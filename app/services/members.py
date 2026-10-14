from __future__ import annotations

from datetime import date
from uuid import uuid4

from sqlalchemy import exists, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from ..extensions import db
from ..models import Attendance, Member, Membership
from ..utils import escape_like
from . import audit
from .errors import ServiceError

FIELDS = ("full_name", "phone", "email", "date_of_birth", "gender", "address",
          "emergency_contact_name", "emergency_contact_phone")


def search_clause(q: str):
    like = f"%{escape_like(q.strip())}%"
    return or_(
        Member.member_number.ilike(like, escape="\\"),
        Member.full_name.ilike(like, escape="\\"),
        Member.phone.ilike(like, escape="\\"),
    )


def has_current_membership(today: date):
    return exists().where(
        Membership.member_id == Member.id,
        Membership.status == "active",
        Membership.start_date <= today,
        Membership.expiry_date >= today,
    )


def has_any_membership():
    return exists().where(Membership.member_id == Member.id, Membership.status != "cancelled")


def current_memberships(member_ids: list[int], today: date) -> dict[int, Membership]:
    if not member_ids:
        return {}
    rows = db.session.scalars(
        select(Membership).options(joinedload(Membership.plan))
        .where(Membership.member_id.in_(member_ids), Membership.status != "cancelled",
               Membership.start_date <= today, Membership.expiry_date >= today)
    ).unique()
    return {m.member_id: m for m in rows}


def create_member(data: dict, actor_id: int, notes: str | None = None) -> Member:
    member = Member(member_number=f"TMP-{uuid4().hex[:12]}", notes=notes or None,
                    **{k: data.get(k) for k in FIELDS})
    db.session.add(member)
    try:
        db.session.flush()
        member.member_number = f"M{member.id:05d}"
        audit.log("member.created", "member", member.id, {"member_number": member.member_number}, actor_id)
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        raise ServiceError("A member with this phone number or email already exists.") from exc
    return member


def update_member(member: Member, data: dict, actor_id: int, notes=...) -> Member:
    for k in FIELDS:
        setattr(member, k, data.get(k))
    if notes is not ...:
        member.notes = notes or None
    audit.log("member.updated", "member", member.id, {k: data.get(k) for k in FIELDS}, actor_id)
    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        raise ServiceError("A member with this phone number or email already exists.") from exc
    return member


def set_active(member_id: int, active: bool, actor_id: int) -> Member:
    member = db.session.scalar(select(Member).where(Member.id == member_id).with_for_update())
    if member is None:
        db.session.rollback()
        raise ServiceError("Member not found.")
    if not active and db.session.scalar(
        select(Attendance.id).where(Attendance.member_id == member_id, Attendance.check_out_at.is_(None))
    ):
        db.session.rollback()
        raise ServiceError("Member is currently inside the gym. Check them out before deactivating.")
    member.is_active = active
    audit.log("member.activated" if active else "member.deactivated", "member", member.id, {}, actor_id)
    db.session.commit()
    return member
