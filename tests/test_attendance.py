from concurrent.futures import ThreadPoolExecutor
from datetime import time, timedelta

import pytest
from sqlalchemy import func, select

from app.extensions import db
from app.models import Attendance, AuditLog
from app.services import attendance as svc
from app.services import reports
from app.services.errors import AccessDenied, AlreadyInside, NotInside, ServiceError
from app.utils import format_duration, local_today, now_utc

from .helpers import eligible_member, local_dt, make_member, make_membership, make_plan, make_user


def _count():
    return db.session.scalar(select(func.count()).select_from(Attendance))


def test_check_in_creates_open_session_with_server_timestamp():
    staff, m = make_user(), eligible_member()
    before = now_utc()
    att = svc.check_in(m.id, staff.id)
    assert att.is_open and att.check_in_by_id == staff.id
    assert before <= att.check_in_at <= now_utc()
    assert svc.get_open_session(m.id).id == att.id


def test_duplicate_check_in_rejected():
    m = eligible_member()
    svc.check_in(m.id, None)
    with pytest.raises(AlreadyInside):
        svc.check_in(m.id, None)
    assert _count() == 1


def test_check_out_without_session_rejected():
    m = eligible_member()
    with pytest.raises(NotInside):
        svc.check_out(None, member_id=m.id)


def test_cannot_check_out_twice():
    m = eligible_member()
    att = svc.check_in(m.id, None)
    svc.check_out(None, attendance_id=att.id)
    with pytest.raises(NotInside):
        svc.check_out(None, attendance_id=att.id)


def test_duration_is_calculated_from_stored_timestamps():
    m, staff = eligible_member(), make_user()
    t0 = now_utc() - timedelta(hours=3)
    att = svc.check_in(m.id, staff.id, now=t0)
    done = svc.check_out(staff.id, attendance_id=att.id, now=t0 + timedelta(minutes=96))
    assert done.duration == timedelta(minutes=96)
    assert format_duration(done.duration) == "1 hour 36 minutes"
    assert done.check_out_by_id == staff.id and not done.is_open


def test_total_visit_duration_includes_completed_and_open_visits():
    m = eligible_member()
    start = now_utc() - timedelta(hours=4)
    first = svc.check_in(m.id, None, now=start)
    svc.check_out(None, attendance_id=first.id, now=start + timedelta(hours=1))
    second = svc.check_in(m.id, None, now=start + timedelta(hours=2))
    svc.check_out(None, attendance_id=second.id, now=start + timedelta(hours=2, minutes=30))
    svc.check_in(m.id, None, now=start + timedelta(hours=3))
    assert svc.total_visit_duration(m.id, now=start + timedelta(hours=4)) == timedelta(hours=2, minutes=30)


def test_daily_report_lists_current_members_without_a_checkin():
    day = local_today()
    no_visit = eligible_member()
    visited = eligible_member()
    visit = svc.check_in(visited.id, None)
    report = reports.daily_report(day)
    assert [m.id for m in report["no_visits"]] == [no_visit.id]
    svc.check_out(None, attendance_id=visit.id)


def test_check_out_before_check_in_rejected():
    m = eligible_member()
    t0 = now_utc() - timedelta(hours=1)
    att = svc.check_in(m.id, None, now=t0)
    with pytest.raises(ServiceError):
        svc.check_out(None, attendance_id=att.id, now=t0 - timedelta(minutes=1))
    assert svc.get_open_session(m.id) is not None


def test_expired_membership_denied_and_no_session_created():
    m = make_member()
    make_membership(m, start=local_today() - timedelta(days=60), paid="5000")  # 30-day plan: expired
    with pytest.raises(AccessDenied) as exc:
        svc.check_in(m.id, None)
    assert exc.value.decision.code == "expired"
    assert _count() == 0


def test_no_membership_inactive_and_suspended_denied():
    from app.services import memberships as ms
    none_m = make_member()
    with pytest.raises(AccessDenied) as e1:
        svc.check_in(none_m.id, None)
    assert e1.value.decision.code == "no_membership"
    inactive = make_member(active=False)
    with pytest.raises(AccessDenied) as e2:
        svc.check_in(inactive.id, None)
    assert e2.value.decision.code == "member_inactive"
    m = make_member()
    membership = make_membership(m, paid="5000")
    ms.set_status(membership.id, "suspend", "chargeback", make_user("admin").id)
    with pytest.raises(AccessDenied) as e3:
        svc.check_in(m.id, None)
    assert e3.value.decision.code == "suspended"
    assert _count() == 0


def test_dues_policy_require_partial(app):
    m = make_member()
    make_membership(m)  # unpaid
    with pytest.raises(AccessDenied) as exc:
        svc.check_in(m.id, None)
    assert exc.value.decision.code == "unpaid"
    m2 = make_member()
    make_membership(m2, paid="1000")  # partial payment is enough under require_partial
    assert svc.check_in(m2.id, None).is_open


def test_dues_policy_require_full_and_allow(app, monkeypatch):
    m = make_member()
    make_membership(m, paid="1000")
    monkeypatch.setitem(app.config, "DUES_POLICY", "require_full")
    with pytest.raises(AccessDenied) as exc:
        svc.check_in(m.id, None)
    assert exc.value.decision.code == "dues_outstanding"
    monkeypatch.setitem(app.config, "DUES_POLICY", "allow")
    unpaid = make_member()
    make_membership(unpaid)
    assert svc.check_in(unpaid.id, None).is_open


def test_plan_access_hours_enforced():
    m = make_member()
    make_membership(m, plan=make_plan(hours=(time(5, 0), time(8, 0))), paid="5000")
    today = local_today()
    with pytest.raises(AccessDenied) as exc:
        svc.check_in(m.id, None, now=local_dt(today, 12))
    assert exc.value.decision.code == "outside_hours"
    assert svc.check_in(m.id, None, now=local_dt(today, 6, 30)).is_open


def test_concurrent_check_ins_create_exactly_one_session(app):
    mid = eligible_member().id

    def worker(_):
        with app.app_context():
            try:
                svc.check_in(mid, None)
                return "ok"
            except AlreadyInside:
                return "dup"

    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(worker, range(8)))
    assert results.count("ok") == 1 and results.count("dup") == 7
    assert _count() == 1


def test_database_blocks_second_open_session_even_bypassing_service():
    from sqlalchemy.exc import IntegrityError
    m = eligible_member()
    svc.check_in(m.id, None)
    db.session.add(Attendance(member_id=m.id, check_in_at=now_utc()))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_missed_checkout_correction_preserves_audit_history():
    m, admin = eligible_member(), make_user("admin")
    t_in = now_utc() - timedelta(hours=30)
    att = svc.check_in(m.id, None, now=t_in)
    assert [a.id for a in svc.long_running()] == [att.id]
    with pytest.raises(ServiceError):
        svc.correct_session(att.id, admin.id, "  ", new_check_out=t_in + timedelta(hours=2))
    with pytest.raises(ServiceError):  # future time rejected
        svc.correct_session(att.id, admin.id, "forgot", new_check_out=now_utc() + timedelta(hours=1))
    fixed = svc.correct_session(att.id, admin.id, "Forgot to tap out", new_check_out=t_in + timedelta(hours=2))
    assert not fixed.is_open and fixed.is_correction and fixed.corrected_by_id == admin.id
    assert fixed.duration == timedelta(hours=2)
    log = db.session.scalar(select(AuditLog).where(AuditLog.action == "attendance.corrected"))
    assert log.details["original"]["check_out_at"] is None and log.details["reason"] == "Forgot to tap out"
    assert svc.get_open_session(m.id) is None


def test_history_filters_by_date_state_and_member():
    m, other = eligible_member(), eligible_member()
    day = local_today() - timedelta(days=2)
    a1 = svc.check_in(m.id, None, now=local_dt(day, 10))
    svc.check_out(None, attendance_id=a1.id, now=local_dt(day, 11))
    a2 = svc.check_in(m.id, None)
    svc.check_in(other.id, None)
    ids = lambda **kw: {a.id for a in db.session.scalars(svc.history_stmt(**kw)).unique()}  # noqa: E731
    assert ids(date_from=day, date_to=day) == {a1.id}
    assert ids(state="open", member_id=m.id) == {a2.id}
    assert ids(state="completed") == {a1.id}
    assert len(ids(q=m.member_number)) == 2


def test_midnight_crossing_visit_counts_on_check_in_day():
    m = eligible_member()
    day = local_today() - timedelta(days=3)
    att = svc.check_in(m.id, None, now=local_dt(day, 23, 30))
    svc.check_out(None, attendance_id=att.id, now=local_dt(day + timedelta(days=1), 0, 30))
    assert reports.daily_report(day)["total_checkins"] == 1
    assert reports.daily_report(day + timedelta(days=1))["total_checkins"] == 0
    assert reports.daily_report(day)["avg_duration"] == timedelta(hours=1)


def test_daily_report_average_excludes_open_sessions():
    day = local_today() - timedelta(days=1)
    for minutes in (60, 120):
        m = eligible_member()
        a = svc.check_in(m.id, None, now=local_dt(day, 8))
        svc.check_out(None, attendance_id=a.id, now=local_dt(day, 8) + timedelta(minutes=minutes))
    still_in = eligible_member()
    svc.check_in(still_in.id, None, now=local_dt(day, 9))
    r = reports.daily_report(day)
    assert (r["total_checkins"], r["unique_members"], r["completed"]) == (3, 3, 2)
    assert r["avg_duration"] == timedelta(minutes=90)
    assert r["inside_now"] == 1
