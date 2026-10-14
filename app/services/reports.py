from __future__ import annotations

from datetime import date, timedelta

from flask import current_app
from sqlalchemy import extract, func, select
from sqlalchemy.orm import aliased, joinedload

from ..extensions import db
from ..models import Attendance, Equipment, Member, Membership, Payment
from ..utils import local_day_bounds, local_today, now_utc
from . import attendance as att_svc
from .payments import dues_totals


def members_without_visits(day: date) -> list[Member]:
    """Active members with a current membership on this date and no recorded check-in that day."""
    start, end = local_day_bounds(day)
    membership_exists = select(Membership.id).where(
        Membership.member_id == Member.id,
        Membership.status == "active",
        Membership.start_date <= day,
        Membership.expiry_date >= day,
    ).exists()
    visit_exists = select(Attendance.id).where(
        Attendance.member_id == Member.id,
        Attendance.check_in_at >= start,
        Attendance.check_in_at < end,
    ).exists()
    return list(db.session.scalars(
        select(Member).where(Member.is_active.is_(True), membership_exists, ~visit_exists)
        .order_by(Member.full_name)
    ))


def daily_report(day: date) -> dict:
    """Day = gym-local calendar day of *check-in*. A visit that crosses midnight is counted on the day it
    began, and appears (open or closed) in that day's list only."""
    start, end = local_day_bounds(day)
    in_day = (Attendance.check_in_at >= start, Attendance.check_in_at < end)
    count = lambda *conds: db.session.scalar(select(func.count()).select_from(Attendance).where(*conds)) or 0  # noqa: E731
    avg_secs = db.session.scalar(
        select(func.avg(extract("epoch", Attendance.check_out_at - Attendance.check_in_at)))
        .where(*in_day, Attendance.check_out_at.is_not(None))
    )
    return {
        "day": day,
        "total_checkins": count(*in_day),
        "unique_members": db.session.scalar(
            select(func.count(func.distinct(Attendance.member_id))).where(*in_day)) or 0,
        "completed": count(*in_day, Attendance.check_out_at.is_not(None)),
        "inside_now": count(Attendance.check_out_at.is_(None)),
        "avg_duration": timedelta(seconds=float(avg_secs)) if avg_secs is not None else None,
        "long_running": att_svc.long_running(),
        "no_visits": members_without_visits(day),
    }


def dashboard_metrics(day: date | None = None) -> dict:
    today = local_today()
    day = day or today
    cfg = current_app.config
    start, end = local_day_bounds(day)
    scalar = lambda stmt: db.session.scalar(stmt) or 0  # noqa: E731
    m2 = aliased(Membership)
    dues_members, dues_total = dues_totals()
    eq_rows = db.session.execute(
        select(Equipment.status, func.count()).where(Equipment.is_active.is_(True)).group_by(Equipment.status)).all()
    return {
        "day": day,
        "total_members": scalar(select(func.count()).select_from(Member)),
        "active_members": scalar(select(func.count()).select_from(Member).where(Member.is_active.is_(True))),
        "inside_now": scalar(select(func.count()).select_from(Attendance).where(Attendance.check_out_at.is_(None))),
        "checkins_day": scalar(select(func.count()).select_from(Attendance).where(
            Attendance.check_in_at >= start, Attendance.check_in_at < end)),
        "completed_day": scalar(select(func.count()).select_from(Attendance).where(
            Attendance.check_in_at >= start, Attendance.check_in_at < end, Attendance.check_out_at.is_not(None))),
        "expiring_soon": scalar(select(func.count(func.distinct(Membership.member_id))).where(
            Membership.status == "active", Membership.expiry_date >= today,
            Membership.expiry_date <= today + timedelta(days=cfg["EXPIRY_WARNING_DAYS"]))),
        "expired": scalar(select(func.count(func.distinct(Membership.member_id))).where(
            Membership.status == "active", Membership.expiry_date < today,
            ~(select(m2.id).where(m2.member_id == Membership.member_id, m2.status != "cancelled",
                                  m2.expiry_date >= today).exists()))),
        "dues_members": dues_members,
        "dues_total": dues_total,
        "long_open": len(att_svc.long_running(now_utc())),
        "recent_payments": list(db.session.scalars(
            select(Payment).options(joinedload(Payment.membership).joinedload(Membership.member),
                                    joinedload(Payment.recorded_by))
            .order_by(Payment.paid_at.desc()).limit(8)).unique()),
        "recent_attendance": list(db.session.scalars(
            select(Attendance).options(joinedload(Attendance.member))
            .order_by(Attendance.id.desc()).limit(8)).unique()),
        "equipment": {s: n for s, n in eq_rows},
    }
