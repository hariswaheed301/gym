from decimal import Decimal

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import select

from ..extensions import db
from ..forms import ActionForm, MembershipForm, PlanForm, ReasonForm
from ..models import Equipment, Member, Membership, MembershipPlan, PlanEquipmentPermission
from ..security import admin_required, staff_required
from ..services import audit, equipment as eq_svc, memberships as svc
from ..services.errors import ServiceError
from ..utils import local_today

bp = Blueprint("memberships", __name__)


@bp.get("/plans")
@staff_required
def plans():
    rows = db.session.scalars(select(MembershipPlan).order_by(MembershipPlan.is_active.desc(), MembershipPlan.name))
    return render_template("memberships/plans.html", plans=list(rows))


def _plan_form(plan=None):
    form = PlanForm(obj=plan)
    restricted = db.session.scalars(select(Equipment).where(Equipment.is_restricted.is_(True),
                                                            Equipment.is_active.is_(True)).order_by(Equipment.name))
    form.equipment_ids.choices = [(e.id, e.name) for e in restricted]
    if plan is not None and request.method == "GET":
        form.equipment_ids.data = [p.equipment_id for p in plan.equipment]
    return form


@bp.route("/plans/new", methods=["GET", "POST"])
@admin_required
def plan_create():
    form = _plan_form()
    if request.method == "GET":
        form.currency.data = form.currency.data or "PKR"
    if form.validate_on_submit():
        if db.session.scalar(select(MembershipPlan.id).where(MembershipPlan.name == form.name.data)):
            form.name.errors.append("A plan with this name already exists.")
        else:
            plan = MembershipPlan()
            _apply_plan(plan, form)
            db.session.add(plan)
            db.session.flush()
            eq_svc.set_plan_equipment(plan.id, form.equipment_ids.data or [], current_user.id)
            audit.log("plan.created", "plan", plan.id, {"name": plan.name, "price": plan.price}, current_user.id)
            db.session.commit()
            flash("Plan created.", "success")
            return redirect(url_for("memberships.plans"))
    return render_template("memberships/plan_form.html", form=form, plan=None)


@bp.route("/plans/<int:plan_id>/edit", methods=["GET", "POST"])
@admin_required
def plan_edit(plan_id):
    plan = db.get_or_404(MembershipPlan, plan_id)
    form = _plan_form(plan)
    if form.validate_on_submit():
        clash = db.session.scalar(select(MembershipPlan.id).where(MembershipPlan.name == form.name.data,
                                                                  MembershipPlan.id != plan.id))
        if clash:
            form.name.errors.append("A plan with this name already exists.")
        else:
            old_price = plan.price
            _apply_plan(plan, form)
            eq_svc.set_plan_equipment(plan.id, form.equipment_ids.data or [], current_user.id)
            audit.log("plan.updated", "plan", plan.id,
                      {"name": plan.name, "old_price": old_price, "price": plan.price, "active": plan.is_active},
                      current_user.id)
            db.session.commit()
            flash("Plan updated. Existing memberships keep the price they were sold at.", "success")
            return redirect(url_for("memberships.plans"))
    return render_template("memberships/plan_form.html", form=form, plan=plan)


def _apply_plan(plan, form):
    plan.name, plan.description = form.name.data, form.description.data or None
    plan.duration_days, plan.price = form.duration_days.data, form.price.data
    plan.currency = form.currency.data.upper()
    plan.access_start_time, plan.access_end_time = form.access_start_time.data, form.access_end_time.data
    plan.is_active = form.is_active.data


@bp.route("/members/<int:member_id>/memberships/new", methods=["GET", "POST"])
@staff_required
def create(member_id):
    member = db.get_or_404(Member, member_id)
    form = MembershipForm()
    plans = list(db.session.scalars(select(MembershipPlan).where(MembershipPlan.is_active.is_(True))
                                    .order_by(MembershipPlan.name)))
    form.plan_id.choices = [(p.id, f"{p.name} — {p.currency} {p.price:,.2f} / {p.duration_days} days") for p in plans]
    if request.method == "GET":
        form.start_date.data = svc.suggest_start(member_id)
        form.plan_id.data = request.args.get("plan_id", type=int)
    is_admin = current_user.role == "admin"
    if not is_admin:
        del form.discount  # only administrators may grant discounts
    if form.validate_on_submit():
        try:
            m = svc.create_membership(member_id, form.plan_id.data, form.start_date.data,
                                      form.discount.data if is_admin else Decimal("0"), current_user.id,
                                      form.notes.data)
        except ServiceError as exc:
            flash(str(exc), "danger")
        else:
            flash("Membership created. Record a payment next.", "success")
            return redirect(url_for("memberships.detail", membership_id=m.id))
    return render_template("memberships/form.html", form=form, member=member,
                           latest=svc.latest_membership(member_id))


@bp.get("/memberships/<int:membership_id>")
@staff_required
def detail(membership_id):
    m = db.get_or_404(Membership, membership_id)
    return render_template("memberships/detail.html", m=m, today=local_today(), action_form=ReasonForm(),
                           plain_form=ActionForm())


@bp.post("/memberships/<int:membership_id>/status/<action>")
@admin_required
def status(membership_id, action):
    if action not in ("suspend", "reactivate", "cancel"):
        abort(404)
    form = ReasonForm()
    if action == "reactivate":
        form.reason.validators = []
    if form.validate_on_submit():
        try:
            svc.set_status(membership_id, action, form.reason.data, current_user.id)
            flash({"suspend": "Membership suspended.", "reactivate": "Membership reactivated.",
                   "cancel": "Membership cancelled. Recorded payments are kept; refunds are handled manually."}[action],
                  "success")
        except ServiceError as exc:
            flash(str(exc), "danger")
    else:
        flash("A reason is required.", "danger")
    return redirect(url_for("memberships.detail", membership_id=membership_id))
