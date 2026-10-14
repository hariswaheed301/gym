"""Attendance: check-in, check-out, corrections and queries. Timestamps always come from the server."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from flask import current_app
from sqlalchemy import extract, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from ..extensions import db
from ..models import Attendance, Member
from ..utils import local_day_bounds, now_utc
from . import audit
from .access import evaluate_access
from .errors import AccessDenied, AlreadyInside, NotInside, ServiceError
from .members import search_clause


def get_open_session(member_id: int) -> Attendance | None:
    return db.session.scalar(
        select(Attendance).where(Attendance.member_id == member_id, Attendance.check_out_at.is_(None))
    )


def total_visit_duration(member_id: int, now: datetime | None = None) -> timedelta:
    """Sum completed visit durations and include elapsed time for the current open visit."""
    now = now or now_utc()
    seconds = float(db.session.scalar(
        select(func.coalesce(func.sum(extract("epoch", Attendance.check_out_at - Attendance.check_in_at)), 0))
        .where(Attendance.member_id == member_id, Attendance.check_out_at.is_not(None))
    ) or 0)
    open_session = get_open_session(member_id)
    if open_session is not None:
        seconds += max(0, (now - open_session.check_in_at).total_seconds())
    return timedelta(seconds=seconds)


def check_in(member_id: int, staff_id: int | None, method: str = "manual", now: datetime | None = None) -> Attendance:
    """Create an open session if (and only if) the member is eligible and not already inside.

    Concurrency: the member row is locked (SELECT ... FOR UPDATE) so simultaneous requests serialize, and the
    partial unique index uq_attendance_one_open_per_member is the final guard. `now` exists for tests only;
    routes never pass it (the timestamp is always the server's)."""
    now = now or now_utc()
    member = db.session.scalar(select(Member).where(Member.id == member_id).with_for_update())
    if member is None:
        db.session.rollback()
        raise ServiceError("Member not found.")
    if get_open_session(member_id) is not None:
        db.session.rollback()
        raise AlreadyInside(f"{member.full_name} is already checked in.")
    decision = evaluate_access(member, now)
    if not decision.allowed:
        db.session.rollback()
        raise AccessDenied(decision)
    att = Attendance(member_id=member_id, check_in_at=now, check_in_by_id=staff_id, check_in_method=method)
    db.session.add(att)
    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        raise AlreadyInside(f"{member.full_name} is already checked in.") from exc
    return att


def check_out(staff_id: int | None, attendance_id: int | None = None, member_id: int | None = None,
              now: datetime | None = None) -> Attendance:
    now = now or now_utc()
    stmt = select(Attendance).where(Attendance.check_out_at.is_(None))
    if attendance_id is not None:
        stmt = stmt.where(Attendance.id == attendance_id)
    elif member_id is not None:
        stmt = stmt.where(Attendance.member_id == member_id)
    else:
        raise ServiceError("Specify a session or member.")
    att = db.session.scalar(stmt.with_for_update())
    if att is None:
        db.session.rollback()
        raise NotInside("There is no open session to check out (already checked out, or never checked in).")
    if now < att.check_in_at:
        db.session.rollback()
        raise ServiceError("Check-out cannot be earlier than check-in.")
    att.check_out_at = now
    att.check_out_by_id = staff_id
    db.session.commit()
    return att


def correct_session(attendance_id: int, admin_id: int, reason: str, new_check_out: datetime | None = None,
                    new_check_in: datetime | None = None, now: datetime | None = None) -> Attendance:
    """Authorised correction (admin only, enforced by the route). Original values are preserved in the audit log.

    * Open session  -> supply new_check_out to record a missed checkout (marked as a correction).
    * Closed session -> supply new_check_in and/or new_check_out to amend the recorded times."""
    now = now or now_utc()
    reason = (reason or "").strip()
    if not reason:
        raise ServiceError("A correction reason is required.")
    att = db.session.scalar(select(Attendance).where(Attendance.id == attendance_id).with_for_update())
    if att is None:
        db.session.rollback()
        raise ServiceError("Attendance record not found.")
    original = {"check_in_at": att.check_in_at, "check_out_at": att.check_out_at}
    check_in_at = new_check_in or att.check_in_at
    if att.check_out_at is None:
        if new_check_out is None:
            db.session.rollback()
            raise ServiceError("Provide the actual check-out time.")
        check_out_at = new_check_out
    else:
        check_out_at = new_check_out or att.check_out_at
    if check_out_at < check_in_at:
        db.session.rollback()
        raise ServiceError("Check-out cannot be earlier than check-in.")
    if check_out_at > now or check_in_at > now:
        db.session.rollback()
        raise ServiceError("Corrected times cannot be in the future.")
    att.check_in_at, att.check_out_at = check_in_at, check_out_at
    att.is_correction, att.correction_reason = True, reason
    att.corrected_by_id, att.corrected_at = admin_id, now
    audit.log("attendance.corrected", "attendance", att.id,
              {"member_id": att.member_id, "original": original,
               "corrected": {"check_in_at": check_in_at, "check_out_at": check_out_at}, "reason": reason},
              admin_id)
    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        raise ServiceError("Correction conflicts with another attendance record.") from exc
    return att


def _with_people(stmt):
    return stmt.options(joinedload(Attendance.member), joinedload(Attendance.check_in_by),
                        joinedload(Attendance.check_out_by), joinedload(Attendance.corrected_by))


def open_sessions_stmt(q: str | None = None):
    stmt = _with_people(
        select(Attendance).join(Member, Member.id == Attendance.member_id)
        .where(Attendance.check_out_at.is_(None)).order_by(Attendance.check_in_at.asc())
    )
    if q and q.strip():
        stmt = stmt.where(search_clause(q))
    return stmt


def history_stmt(q: str | None = None, member_id: int | None = None, date_from: date | None = None,
                 date_to: date | None = None, state: str = ""):
    """Visits are filtered by the gym-local DATE OF CHECK-IN, so a visit crossing midnight belongs to the
    day it started."""
    stmt = _with_people(
        select(Attendance).join(Member, Member.id == Attendance.member_id).order_by(Attendance.check_in_at.desc())
    )
    if q and q.strip():
        stmt = stmt.where(search_clause(q))
    if member_id:
        stmt = stmt.where(Attendance.member_id == member_id)
    if date_from:
        stmt = stmt.where(Attendance.check_in_at >= local_day_bounds(date_from)[0])
    if date_to:
        stmt = stmt.where(Attendance.check_in_at < local_day_bounds(date_to)[1])
    if state == "open":
        stmt = stmt.where(Attendance.check_out_at.is_(None))
    elif state == "completed":
        stmt = stmt.where(Attendance.check_out_at.is_not(None))
    return stmt


def long_running(now: datetime | None = None) -> list[Attendance]:
    now = now or now_utc()
    cutoff = now - timedelta(hours=current_app.config["LONG_SESSION_HOURS"])
    return list(db.session.scalars(
        _with_people(select(Attendance).where(Attendance.check_out_at.is_(None), Attendance.check_in_at < cutoff)
                     .order_by(Attendance.check_in_at.asc()))
    ).unique())
