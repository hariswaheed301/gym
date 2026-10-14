from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import PAYMENT_METHODS, Member, Membership, Payment
from ..utils import escape_like, now_utc, quantize_money
from . import audit
from .errors import ServiceError


def paid_subquery():
    return (
        select(Payment.membership_id.label("mid"), func.coalesce(func.sum(Payment.amount), 0).label("paid"))
        .where(Payment.status == "valid")
        .group_by(Payment.membership_id)
        .subquery()
    )


def balance(membership_id: int) -> tuple[Decimal, Decimal, Decimal]:
    """Returns (net_price, paid, outstanding) computed in SQL from valid payments only."""
    m = db.session.get(Membership, membership_id)
    if m is None:
        raise ServiceError("Membership not found.")
    paid = db.session.scalar(
        select(func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.membership_id == membership_id, Payment.status == "valid"
        )
    )
    paid = Decimal(paid)
    net = m.price - m.discount
    return net, paid, net - paid


def dues_stmt(q: str | None = None):
    """Memberships (not cancelled) with outstanding > 0, with paid and outstanding columns."""
    paid = paid_subquery()
    paid_col = func.coalesce(paid.c.paid, 0).label("paid")
    outstanding = (Membership.price - Membership.discount - func.coalesce(paid.c.paid, 0)).label("outstanding")
    stmt = (
        select(Membership, Member, paid_col, outstanding)
        .join(Member, Member.id == Membership.member_id)
        .outerjoin(paid, paid.c.mid == Membership.id)
        .where(Membership.status != "cancelled", (Membership.price - Membership.discount - func.coalesce(paid.c.paid, 0)) > 0)
        .order_by(Membership.expiry_date.asc(), Membership.id.asc())
    )
    if q:
        like = f"%{escape_like(q)}%"
        stmt = stmt.where(
            or_(
                Member.full_name.ilike(like, escape="\\"),
                Member.member_number.ilike(like, escape="\\"),
                Member.phone.ilike(like, escape="\\"),
            )
        )
    return stmt


def dues_totals() -> tuple[int, Decimal]:
    paid = paid_subquery()
    outstanding = Membership.price - Membership.discount - func.coalesce(paid.c.paid, 0)
    row = db.session.execute(
        select(func.count(func.distinct(Membership.member_id)), func.coalesce(func.sum(outstanding), 0))
        .select_from(Membership)
        .outerjoin(paid, paid.c.mid == Membership.id)
        .where(Membership.status != "cancelled", outstanding > 0)
    ).one()
    return int(row[0]), Decimal(row[1])


def record_payment(membership_id: int, amount, method: str, actor_id: int, reference: str | None = None,
                   idempotency_key: str | None = None, notes: str | None = None) -> tuple[Payment, bool]:
    """Record a payment in one transaction. Returns (payment, created).

    The membership row is locked, so concurrent payments serialize and cannot overpay. A repeated
    submission with the same idempotency key returns the original payment (created=False)."""
    amount = quantize_money(amount)
    if method not in PAYMENT_METHODS:
        raise ServiceError("Unknown payment method.")
    if amount <= 0:
        raise ServiceError("Amount must be greater than zero.")
    membership = db.session.scalar(select(Membership).where(Membership.id == membership_id).with_for_update())
    if membership is None or membership.status == "cancelled":
        db.session.rollback()
        raise ServiceError("Payments cannot be recorded against this membership.")
    if idempotency_key:
        existing = db.session.scalar(select(Payment).where(Payment.idempotency_key == idempotency_key))
        if existing is not None:
            db.session.rollback()
            return existing, False
    _, paid, outstanding = balance(membership_id)
    if amount > outstanding:
        db.session.rollback()
        raise ServiceError(f"Amount exceeds the outstanding balance ({outstanding:,.2f}).")
    payment = Payment(
        membership_id=membership_id, amount=amount, method=method, reference_number=reference or None,
        recorded_by_id=actor_id, idempotency_key=idempotency_key or None, notes=notes or None, paid_at=now_utc(),
    )
    db.session.add(payment)
    db.session.flush()
    payment.receipt_number = f"RCP-{payment.id:07d}"
    audit.log("payment.recorded", "payment", payment.id,
              {"membership_id": membership_id, "amount": amount, "method": method, "reference": reference},
              actor_id)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        existing = db.session.scalar(select(Payment).where(Payment.idempotency_key == idempotency_key))
        if existing is not None:
            return existing, False
        raise
    return payment, True


def void_payment(payment_id: int, reason: str, actor_id: int) -> Payment:
    """Void (never delete) a payment. To correct an amount: void, then record a new payment."""
    reason = (reason or "").strip()
    if not reason:
        raise ServiceError("A reason is required to void a payment.")
    payment = db.session.scalar(select(Payment).where(Payment.id == payment_id).with_for_update())
    if payment is None:
        db.session.rollback()
        raise ServiceError("Payment not found.")
    if payment.status == "voided":
        db.session.rollback()
        raise ServiceError("Payment is already voided.")
    payment.status, payment.void_reason = "voided", reason
    payment.voided_by_id, payment.voided_at = actor_id, now_utc()
    audit.log("payment.voided", "payment", payment.id,
              {"amount": payment.amount, "membership_id": payment.membership_id, "reason": reason}, actor_id)
    db.session.commit()
    return payment
