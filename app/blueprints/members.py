from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import func, select

from ..extensions import db
from ..forms import ActionForm, MemberForm, UsageForm
from ..models import Attendance, Member, Membership, Payment
from ..security import admin_required, staff_required
from ..services import access, attendance as att_svc, equipment as eq_svc, members as svc
from ..services.errors import ServiceError
from ..utils import local_today, now_utc, paginate

bp = Blueprint("members", __name__, url_prefix="/members")


@bp.get("/")
@staff_required
def index():
    q = request.args.get("q", "").strip()
    status = request.args.get("status", "active")
    mstate = request.args.get("membership", "")
    today = local_today()
    stmt = select(Member).order_by(Member.full_name)
    if q:
        stmt = stmt.where(svc.search_clause(q))
    if status == "active":
        stmt = stmt.where(Member.is_active.is_(True))
    elif status == "inactive":
        stmt = stmt.where(Member.is_active.is_(False))
    if mstate == "current":
        stmt = stmt.where(svc.has_current_membership(today))
    elif mstate == "expired":
        stmt = stmt.where(~svc.has_current_membership(today), svc.has_any_membership())
    elif mstate == "none":
        stmt = stmt.where(~svc.has_any_membership())
    page = paginate(stmt)
    current = svc.current_memberships([m.id for m in page.items], today)
    return render_template("members/list.html", page=page, current=current, q=q, status=status, mstate=mstate)


@bp.route("/new", methods=["GET", "POST"])
@staff_required
def create():
    form = MemberForm()
    if current_user.role != "admin":
        del form.notes
    if form.validate_on_submit():
        try:
            member = svc.create_member(form.cleaned(), current_user.id,
                                       notes=form.notes.data if "notes" in form else None)
        except ServiceError as exc:
            flash(str(exc), "danger")
        else:
            flash(f"Member {member.member_number} registered. Now add a membership.", "success")
            return redirect(url_for("memberships.create", member_id=member.id))
    return render_template("members/form.html", form=form, member=None)


@bp.get("/<int:member_id>")
@staff_required
def detail(member_id):
    member = db.get_or_404(Member, member_id)
    today = local_today()
    memberships = list(db.session.scalars(select(Membership).where(Membership.member_id == member_id)
                                          .order_by(Membership.start_date.desc())))
    payments = list(db.session.scalars(
        select(Payment).join(Membership).where(Membership.member_id == member_id)
        .order_by(Payment.paid_at.desc()).limit(50)))
    visits = list(db.session.scalars(att_svc.history_stmt(member_id=member_id).limit(15)).unique())
    visit_count = db.session.scalar(select(func.count()).select_from(Attendance).where(Attendance.member_id == member_id))
    now = now_utc()
    open_session = att_svc.get_open_session(member_id)
    matrix = eq_svc.member_equipment_matrix(member)
    usage_form = UsageForm()
    usage_form.equipment_id.choices = [(r["equipment"].id, r["equipment"].name) for r in matrix
                                       if r["allowed"] and r["equipment"].status == "operational"]
    return render_template(
        "members/detail.html", member=member, memberships=memberships, payments=payments, visits=visits,
        visit_count=visit_count, total_visit_duration=att_svc.total_visit_duration(member_id, now), today=today,
        decision=access.evaluate_access(member), open_session=open_session,
        outstanding=sum((m.outstanding for m in memberships if m.status != "cancelled"), 0),
        matrix=matrix, usage_form=usage_form, action_form=ActionForm(),
    )


@bp.route("/<int:member_id>/edit", methods=["GET", "POST"])
@staff_required
def edit(member_id):
    member = db.get_or_404(Member, member_id)
    form = MemberForm(obj=member, member_id=member.id)
    if current_user.role != "admin":
        del form.notes
    if form.validate_on_submit():
        try:
            svc.update_member(member, form.cleaned(), current_user.id,
                              notes=form.notes.data if "notes" in form else ...)
        except ServiceError as exc:
            flash(str(exc), "danger")
        else:
            flash("Member updated.", "success")
            return redirect(url_for("members.detail", member_id=member.id))
    return render_template("members/form.html", form=form, member=member)


@bp.post("/<int:member_id>/<action>")
@admin_required
def set_active(member_id, action):
    if action not in ("deactivate", "activate"):
        abort(404)
    if ActionForm().validate_on_submit():
        try:
            svc.set_active(member_id, action == "activate", current_user.id)
            flash(f"Member {action}d.", "success")
        except ServiceError as exc:
            flash(str(exc), "danger")
    return redirect(url_for("members.detail", member_id=member_id))
