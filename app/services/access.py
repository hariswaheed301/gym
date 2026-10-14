"""Access policy: decides whether a member may enter right now."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from flask import current_app
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from ..extensions import db
from ..models import Member, Membership
from ..utils import local_today, now_utc, to_local
from .payments import balance


@dataclass
class AccessDecision:
    allowed: bool
    code: str
    message: str
    membership: Membership | None = None


def _within_hours(plan, now: datetime) -> bool:
    start, end = plan.access_start_time, plan.access_end_time
    if start is None or end is None:
        return True
    t = to_local(now).time()
    return start <= t <= end if start <= end else (t >= start or t <= end)


def evaluate_access(member: Member, now: datetime | None = None) -> AccessDecision:
    now = now or now_utc()
    today = local_today(now)
    if not member.is_active:
        return AccessDecision(False, "member_inactive", "Member account is inactive.")

    covering = db.session.scalars(
        select(Membership).options(joinedload(Membership.plan))
        .where(Membership.member_id == member.id, Membership.status != "cancelled",
               Membership.start_date <= today, Membership.expiry_date >= today)
    ).unique().first()

    if covering is None:
        last = db.session.scalar(
            select(Membership).where(Membership.member_id == member.id, Membership.status != "cancelled")
            .order_by(Membership.expiry_date.desc()).limit(1)
        )
        if last is None:
            return AccessDecision(False, "no_membership", "No membership on record.")
        if last.start_date > today:
            return AccessDecision(False, "not_started",
                                  f"Membership starts on {last.start_date:%d %b %Y}.", last)
        return AccessDecision(False, "expired", f"Membership expired on {last.expiry_date:%d %b %Y}.", last)

    if covering.status == "suspended":
        return AccessDecision(False, "suspended", "Membership is suspended.", covering)

    _, paid, outstanding = balance(covering.id)
    policy = current_app.config["DUES_POLICY"]
    if policy == "require_full" and outstanding > 0:
        return AccessDecision(False, "dues_outstanding",
                              f"Outstanding dues of {outstanding:,.2f} must be cleared.", covering)
    if policy == "require_partial" and outstanding > 0 and paid <= 0:
        return AccessDecision(False, "unpaid", "Membership has not been paid for yet.", covering)

    if not _within_hours(covering.plan, now):
        s, e = covering.plan.access_start_time, covering.plan.access_end_time
        return AccessDecision(False, "outside_hours",
                              f"Plan allows access only between {s:%H:%M} and {e:%H:%M}.", covering)
    return AccessDecision(True, "ok", "Access permitted.", covering)
