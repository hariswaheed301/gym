from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.extensions import db
from app.models import AuditLog, Payment
from app.services import memberships as ms
from app.services import payments as pay
from app.services.errors import ServiceError
from app.utils import local_today

from .helpers import make_member, make_membership, make_plan, make_user


def test_partial_payments_and_outstanding_balance_use_exact_decimals():
    staff, m = make_user(), make_member()
    membership = make_membership(m, plan=make_plan(price="1000.10"), actor=staff)
    pay.record_payment(membership.id, "400.05", "cash", staff.id)
    _, paid, outstanding = pay.balance(membership.id)
    assert paid == Decimal("400.05") and outstanding == Decimal("600.05")
    assert membership.payment_status == "partial"
    pay.record_payment(membership.id, "600.05", "bank_transfer", staff.id, reference="TX1")
    db.session.refresh(membership)
    assert pay.balance(membership.id)[2] == Decimal("0.00") and membership.payment_status == "paid"


def test_discount_reduces_balance_and_overpayment_is_rejected():
    staff, m = make_user(), make_member()
    membership = make_membership(m, plan=make_plan(price="5000"), discount="500", actor=staff)
    assert pay.balance(membership.id)[0] == Decimal("4500.00")
    with pytest.raises(ServiceError):
        pay.record_payment(membership.id, "4500.01", "cash", staff.id)
    with pytest.raises(ServiceError):
        pay.record_payment(membership.id, "0", "cash", staff.id)
    with pytest.raises(ServiceError):
        pay.record_payment(membership.id, "10", "bitcoin", staff.id)


def test_idempotency_key_prevents_duplicate_payment():
    staff, m = make_user(), make_member()
    membership = make_membership(m, actor=staff)
    p1, created1 = pay.record_payment(membership.id, "1000", "cash", staff.id, idempotency_key="abc123")
    p2, created2 = pay.record_payment(membership.id, "1000", "cash", staff.id, idempotency_key="abc123")
    assert created1 and not created2 and p1.id == p2.id
    assert db.session.scalar(select(func.count()).select_from(Payment)) == 1
    assert p1.receipt_number == f"RCP-{p1.id:07d}"


def test_void_keeps_record_reopens_balance_and_is_audited():
    staff, m = make_user(), make_member()
    membership = make_membership(m, actor=staff, paid="5000")
    payment = db.session.scalar(select(Payment))
    with pytest.raises(ServiceError):
        pay.void_payment(payment.id, "", staff.id)
    pay.void_payment(payment.id, "entered twice", staff.id)
    assert pay.balance(membership.id)[2] == Decimal("5000.00")
    assert db.session.scalar(select(func.count()).select_from(Payment)) == 1  # never deleted
    assert db.session.scalar(select(AuditLog).where(AuditLog.action == "payment.voided")) is not None
    with pytest.raises(ServiceError):
        pay.void_payment(payment.id, "again", staff.id)


def test_dues_totals_and_listing():
    staff = make_user()
    a, b = make_member(), make_member()
    make_membership(a, actor=staff)  # 5000 due
    make_membership(b, actor=staff, paid="3000")  # 2000 due
    make_membership(make_member(), actor=staff, paid="5000")  # settled
    members_n, total = pay.dues_totals()
    assert (members_n, total) == (2, Decimal("7000.00"))
    rows = db.session.execute(pay.dues_stmt()).all()
    assert sorted(r.outstanding for r in rows) == [Decimal("2000.00"), Decimal("5000.00")]


def test_expiry_date_is_inclusive_end_of_duration():
    start = local_today()
    membership = make_membership(make_member(), start=start)
    assert membership.expiry_date == start + timedelta(days=29)
    assert membership.state() == "current"
    old = make_membership(make_member(), start=start - timedelta(days=60))
    assert old.state() == "expired"
    future = make_membership(make_member(), start=start + timedelta(days=3))
    assert future.state() == "upcoming"


def test_overlapping_membership_rejected_and_renewal_starts_after_expiry():
    admin, m = make_user("admin"), make_member()
    first = make_membership(m, start=local_today(), actor=admin)
    assert ms.suggest_start(m.id) == first.expiry_date + timedelta(days=1)
    with pytest.raises(ServiceError):
        ms.create_membership(m.id, make_plan().id, local_today() + timedelta(days=5), 0, admin.id)
    renewal = ms.create_membership(m.id, make_plan().id, ms.suggest_start(m.id), 0, admin.id)
    assert renewal.renewed_from_id == first.id and renewal.start_date == first.expiry_date + timedelta(days=1)


def test_cancelled_membership_does_not_block_new_one_and_discount_is_validated():
    admin, m = make_user("admin"), make_member()
    first = make_membership(m, start=local_today(), actor=admin)
    ms.set_status(first.id, "cancel", "mistake", admin.id)
    plan = make_plan(price="1000")
    with pytest.raises(ServiceError):
        ms.create_membership(m.id, plan.id, local_today(), Decimal("1001"), admin.id)
    assert ms.create_membership(m.id, plan.id, local_today(), Decimal("100"), admin.id).discount == Decimal("100.00")


def test_database_exclusion_constraint_blocks_overlap_even_without_service():
    from sqlalchemy.exc import IntegrityError

    from app.models import Membership
    m, plan = make_member(), make_plan()
    make_membership(m, plan=plan, start=local_today())
    db.session.add(Membership(member_id=m.id, plan_id=plan.id, start_date=local_today() + timedelta(days=2),
                              expiry_date=local_today() + timedelta(days=9), price=Decimal("10"), discount=0))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()
