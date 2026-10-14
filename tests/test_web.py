import re
from sqlalchemy import func, select

from app.extensions import db
from app.models import Attendance, Payment, User
from app.services import attendance as att
from app.services import payments as pay

from .helpers import PASSWORD, eligible_member, login, make_member, make_membership, make_user


def test_passwords_are_hashed_with_argon2id():
    user = make_user()
    assert user.password_hash.startswith("$argon2id$") and PASSWORD not in user.password_hash


def test_login_logout_and_generic_failure_message(client):
    user = make_user()
    bad = client.post("/login", data={"username": user.username, "password": "wrong-password"})
    assert b"Invalid username or password." in bad.data
    ghost = client.post("/login", data={"username": "nobody", "password": "wrong-password"})
    assert b"Invalid username or password." in ghost.data  # same message: no user enumeration
    assert login(client, user).status_code == 302
    assert client.get("/").status_code == 200
    assert client.post("/logout").status_code == 302
    assert client.get("/").status_code == 302  # signed out


def test_protected_pages_redirect_anonymous_users(client):
    for url in ("/", "/members/", "/attendance/", "/payments/dues", "/staff/", "/audit/"):
        r = client.get(url)
        assert r.status_code == 302 and "/login" in r.headers["Location"]


def test_open_redirect_is_blocked(client):
    user = make_user()
    r = client.post("/login?next=https://evil.example/", data={"username": user.username, "password": PASSWORD})
    assert r.headers["Location"].endswith("/")


def test_deactivation_invalidates_existing_sessions(client):
    admin, staff = make_user("admin"), make_user()
    login(client, staff)
    assert client.get("/").status_code == 200
    staff.active = False
    staff.session_version += 1
    db.session.commit()
    assert client.get("/").status_code == 302


def test_receptionist_cannot_reach_admin_functions(client):
    rec, member = make_user(), make_member()
    membership = make_membership(member, paid="5000")
    payment = db.session.scalar(select(Payment))
    visit = att.check_in(member.id, None)
    login(client, rec)
    assert client.get("/staff/").status_code == 403
    assert client.get("/audit/").status_code == 403
    assert client.get("/plans/new").status_code == 403
    assert client.get(f"/attendance/{visit.id}/correct").status_code == 403
    assert client.post(f"/attendance/{visit.id}/correct", data={"check_out_local": "2026-01-01T10:00", "reason": "x"}).status_code == 403
    assert client.post(f"/payments/{payment.id}/void", data={"reason": "nope"}).status_code == 403
    assert client.post(f"/equipment/member/{member.id}", data={"equipment_id": 1, "effect": "deny"}).status_code == 403
    assert client.post(f"/members/{member.id}/deactivate").status_code == 403
    assert client.post(f"/memberships/{membership.id}/status/cancel", data={"reason": "x"}).status_code == 403
    db.session.refresh(payment)
    assert payment.status == "valid"


def test_receptionist_does_not_see_internal_notes_or_discount(client):
    member = make_member()
    member.notes = "SECRET-NOTE"
    db.session.commit()
    login(client, make_user())
    assert b"SECRET-NOTE" not in client.get(f"/members/{member.id}").data
    assert b'name="discount"' not in client.get(f"/members/{member.id}/memberships/new").data


def test_reception_check_in_and_check_out_flow(client):
    member = eligible_member()
    login(client, make_user())
    page = client.get(f"/attendance/?q={member.member_number}")
    assert member.full_name.encode() in page.data
    r = client.post("/attendance/check-in", data={"member_id": member.id}, follow_redirects=True)
    assert b"Checked in" in r.data and member.full_name.encode() in r.data  # appears in the inside list
    again = client.post("/attendance/check-in", data={"member_id": member.id}, follow_redirects=True)
    assert b"already checked in" in again.data
    assert db.session.scalar(select(func.count()).select_from(Attendance)) == 1
    visit = att.get_open_session(member.id)
    out = client.post(f"/attendance/{visit.id}/check-out", follow_redirects=True)
    assert b"Checked out" in out.data and b"Visit duration" in out.data
    assert att.get_open_session(member.id) is None
    second = client.post(f"/attendance/{visit.id}/check-out", follow_redirects=True)
    assert b"no open session" in second.data


def test_denied_check_in_shows_reason_and_creates_nothing(client):
    member = make_member()  # no membership
    login(client, make_user())
    r = client.post("/attendance/check-in", data={"member_id": member.id}, follow_redirects=True)
    assert b"Entry denied" in r.data and b"No membership" in r.data
    assert db.session.scalar(select(func.count()).select_from(Attendance)) == 0


def test_partial_html_and_unauthenticated_fetch_returns_401(client):
    assert client.get("/attendance/inside", headers={"X-Requested-With": "fetch"}).status_code == 401
    login(client, make_user())
    r = client.get("/attendance/inside", headers={"X-Requested-With": "fetch"})
    assert r.status_code == 200 and b"<html" not in r.data


def test_duplicate_payment_form_submission_records_once(client):
    member = make_member()
    membership = make_membership(member)
    login(client, make_user())
    data = {"amount": "1000.00", "method": "cash", "idempotency_key": "form-key-1"}
    url = f"/payments/new?membership_id={membership.id}"
    client.post(url, data=data)
    client.post(url, data=data)
    assert db.session.scalar(select(func.count()).select_from(Payment)) == 1


def test_csrf_is_enforced_when_enabled(app, client, monkeypatch):
    monkeypatch.setitem(app.config, "WTF_CSRF_ENABLED", True)
    user = make_user()
    assert client.post("/login", data={"username": user.username, "password": PASSWORD}).status_code == 400
    html = client.get("/login").get_data(as_text=True)
    token = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html).group(1)
    ok = client.post("/login", data={"username": user.username, "password": PASSWORD, "csrf_token": token})
    assert ok.status_code == 302


def test_security_headers_present(client):
    r = client.get("/login")
    assert "default-src 'self'" in r.headers["Content-Security-Policy"]
    assert r.headers["X-Frame-Options"] == "DENY"


def test_health_endpoint(client):
    assert client.get("/healthz").get_json() == {"status": "ok"}


def test_staff_management_rules(client):
    admin = make_user("admin")
    login(client, admin)
    r = client.post("/staff/new", data={"username": "newrec", "full_name": "New Rec", "role": "receptionist",
                                         "password": "Weakpass", "confirm": "Weakpass"})
    assert r.status_code == 200 and db.session.scalar(select(User).where(User.username == "newrec")) is None
    client.post("/staff/new", data={"username": "newrec", "full_name": "New Rec", "role": "receptionist",
                                     "password": "Better-Pass-123", "confirm": "Better-Pass-123"})
    assert db.session.scalar(select(User).where(User.username == "newrec")).role == "receptionist"
    client.post(f"/staff/{admin.id}/toggle")  # cannot deactivate yourself / last admin
    db.session.refresh(admin)
    assert admin.active
