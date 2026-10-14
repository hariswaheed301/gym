from datetime import timedelta

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import select

from ..extensions import db
from ..forms import ActionForm, CorrectionForm
from ..models import Attendance, Member
from ..security import admin_required, staff_required
from ..services import access, attendance as svc, members as members_svc
from ..services.errors import AccessDenied, AlreadyInside, NotInside, ServiceError
from ..utils import format_duration, local_naive_to_utc, local_today, now_utc, paginate, parse_date, to_local
from flask import current_app

bp = Blueprint("attendance", __name__, url_prefix="/attendance")


def _is_fetch() -> bool:
    return request.headers.get("X-Requested-With") == "fetch"


def _inside_context(q: str):
    sessions = list(db.session.scalars(svc.open_sessions_stmt(q)).unique())
    now = now_utc()
    cur = members_svc.current_memberships([s.member_id for s in sessions], local_today(now))
    cutoff = now - timedelta(hours=current_app.config["LONG_SESSION_HOURS"])
    return {"sessions": sessions, "now": now, "current": cur, "cutoff": cutoff, "action_form": ActionForm()}


@bp.get("/")
@staff_required
def reception():
    q = request.args.get("q", "").strip()
    results = []
    if q:
        found = db.session.scalars(select(Member).where(members_svc.search_clause(q)).order_by(Member.full_name).limit(8))
        for m in found:
            results.append({"member": m, "decision": access.evaluate_access(m),
                            "open": svc.get_open_session(m.id)})
    ctx = {"results": results, "q": q, "action_form": ActionForm(), "now": now_utc()}
    if _is_fetch():
        return render_template("attendance/_results.html", **ctx)
    return render_template("attendance/reception.html", **ctx, inside=_inside_context(""))


@bp.get("/inside")
@staff_required
def inside_partial():
    return render_template("attendance/_inside_table.html", **_inside_context(request.args.get("q", "")))


@bp.post("/check-in")
@staff_required
def check_in():
    if not ActionForm().validate_on_submit():
        abort(400)
    member_id = request.form.get("member_id", type=int)
    try:
        att = svc.check_in(member_id, current_user.id)
        member = db.session.get(Member, att.member_id)
        flash(f"Checked in: {member.full_name} ({member.member_number}) at {to_local(att.check_in_at):%I:%M %p}.", "success")
    except AccessDenied as exc:
        flash(f"Entry denied: {exc}", "danger")
    except AlreadyInside as exc:
        flash(str(exc), "warning")
    except ServiceError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("attendance.reception"))


@bp.post("/<int:attendance_id>/check-out")
@staff_required
def check_out(attendance_id):
    if not ActionForm().validate_on_submit():
        abort(400)
    try:
        att = svc.check_out(current_user.id, attendance_id=attendance_id)
        flash(f"Checked out: {att.member.full_name}. Visit duration: {format_duration(att.duration)}.", "success")
    except NotInside as exc:
        flash(str(exc), "warning")
    except ServiceError as exc:
        flash(str(exc), "danger")
    return redirect(request.form.get("next") if (request.form.get("next") or "").startswith("/") and not
                    (request.form.get("next") or "").startswith("//") else url_for("attendance.reception"))


@bp.get("/history")
@staff_required
def history():
    q = request.args.get("q", "").strip()
    member_id = request.args.get("member_id", type=int)
    df, dt = parse_date(request.args.get("from")), parse_date(request.args.get("to"))
    state = request.args.get("state", "")
    page = paginate(svc.history_stmt(q, member_id, df, dt, state))
    member = db.session.get(Member, member_id) if member_id else None
    return render_template("attendance/history.html", page=page, q=q, member=member, member_id=member_id,
                           date_from=request.args.get("from", ""), date_to=request.args.get("to", ""), state=state,
                           now=now_utc(), cutoff=now_utc() - timedelta(hours=current_app.config["LONG_SESSION_HOURS"]))


@bp.route("/<int:attendance_id>/correct", methods=["GET", "POST"])
@admin_required
def correct(attendance_id):
    att = db.get_or_404(Attendance, attendance_id)
    form = CorrectionForm()
    if request.method == "GET":
        form.check_in_local.data = to_local(att.check_in_at).strftime("%Y-%m-%dT%H:%M")
        if att.check_out_at:
            form.check_out_local.data = to_local(att.check_out_at).strftime("%Y-%m-%dT%H:%M")
    if form.validate_on_submit():
        new_in = CorrectionForm.parse(form.check_in_local.data)
        new_out = CorrectionForm.parse(form.check_out_local.data)
        try:
            svc.correct_session(att.id, current_user.id, form.reason.data,
                                new_check_out=local_naive_to_utc(new_out),
                                new_check_in=local_naive_to_utc(new_in) if new_in else None)
        except ServiceError as exc:
            flash(str(exc), "danger")
        else:
            flash("Attendance corrected. The original times are preserved in the audit log.", "success")
            return redirect(url_for("attendance.history", member_id=att.member_id))
    return render_template("attendance/correct.html", form=form, att=att)
