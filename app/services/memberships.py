from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import Member, Membership, MembershipPlan
from ..utils import local_today, quantize_money
from . import audit
from .errors import ServiceError


def latest_membership(member_id: int) -> Membership | None:
    return db.session.scalar(
        select(Membership).where(Membership.member_id == member_id, Membership.status != "cancelled")
        .order_by(Membership.expiry_date.desc()).limit(1)
    )


def suggest_start(member_id: int, today: date | None = None) -> date:
    """Renewal rule: if the latest membership is still running (or upcoming), the renewal starts the day
    after it expires (no gap, no overlap); otherwise it starts today."""
    today = today or local_today()
    last = latest_membership(member_id)
    if last and last.expiry_date >= today:
        return last.expiry_date + timedelta(days=1)
    return today


def create_membership(member_id: int, plan_id: int, start_date: date, discount, actor_id: int,
                      notes: str | None = None) -> Membership:
    member = db.session.scalar(select(Member).where(Member.id == member_id).with_for_update())
    plan = db.session.get(MembershipPlan, plan_id)
    if member is None or plan is None:
        db.session.rollback()
        raise ServiceError("Member or plan not found.")
    if not member.is_active:
        db.session.rollback()
        raise ServiceError("Cannot add a membership to an inactive member.")
    if not plan.is_active:
        db.session.rollback()
        raise ServiceError("This plan is no longer available.")
    discount = quantize_money(discount or Decimal("0"))
    if discount < 0 or discount > plan.price:
        db.session.rollback()
        raise ServiceError("Discount must be between 0 and the plan price.")
    expiry = start_date + timedelta(days=plan.duration_days - 1)
    clash = db.session.scalar(
        select(Membership).where(
            Membership.member_id == member_id, Membership.status != "cancelled",
            Membership.start_date <= expiry, Membership.expiry_date >= start_date,
        ).limit(1)
    )
    if clash:
        db.session.rollback()
        raise ServiceError(
            f"Overlaps an existing membership ({clash.start_date:%d %b %Y} – {clash.expiry_date:%d %b %Y}). "
            "Choose a start date after it expires."
        )
    prev = latest_membership(member_id)
    membership = Membership(
        member_id=member_id, plan_id=plan.id, start_date=start_date, expiry_date=expiry, price=plan.price,
        discount=discount, currency=plan.currency, status="active", created_by_id=actor_id,
        notes=notes or None, renewed_from_id=prev.id if prev else None,
    )
    db.session.add(membership)
    try:
        db.session.flush()
        audit.log("membership.created", "membership", membership.id,
                  {"member_id": member_id, "plan": plan.name, "start": start_date, "expiry": expiry,
                   "price": plan.price, "discount": discount, "renewal_of": membership.renewed_from_id}, actor_id)
        db.session.commit()
    except IntegrityError as exc:  # exclusion constraint = last line of defence against races
        db.session.rollback()
        raise ServiceError("This membership overlaps an existing one.") from exc
    return membership


_TRANSITIONS = {"suspend": ("active", "suspended"), "reactivate": ("suspended", "active"),
                "cancel": ("active", "cancelled")}


def set_status(membership_id: int, action: str, reason: str, actor_id: int) -> Membership:
    if action not in _TRANSITIONS:
        raise ServiceError("Unknown action.")
    reason = (reason or "").strip()
    if action in ("suspend", "cancel") and not reason:
        raise ServiceError("A reason is required.")
    membership = db.session.scalar(select(Membership).where(Membership.id == membership_id).with_for_update())
    if membership is None:
        db.session.rollback()
        raise ServiceError("Membership not found.")
    expected, new = _TRANSITIONS[action]
    if action == "cancel" and membership.status == "suspended":
        expected = "suspended"
    if membership.status != expected:
        db.session.rollback()
        raise ServiceError(f"Cannot {action} a membership that is {membership.status}.")
    old = membership.status
    membership.status = new
    audit.log(f"membership.{new}", "membership", membership.id, {"from": old, "reason": reason}, actor_id)
    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        raise ServiceError("Reactivating would overlap another membership.") from exc
    return membership
