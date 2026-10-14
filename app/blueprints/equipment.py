from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import select

from ..extensions import db
from ..forms import ActionForm, EquipmentForm, PermissionForm, UsageForm
from ..models import Equipment, EquipmentUsage, Member
from ..security import admin_required, staff_required
from ..services import audit, equipment as svc
from ..services.errors import ServiceError

bp = Blueprint("equipment", __name__, url_prefix="/equipment")


@bp.get("/")
@staff_required
def index():
    status = request.args.get("status", "")
    stmt = select(Equipment).order_by(Equipment.is_active.desc(), Equipment.category, Equipment.name)
    if status in ("operational", "maintenance", "unavailable"):
        stmt = stmt.where(Equipment.status == status, Equipment.is_active.is_(True))
    return render_template("equipment/list.html", items=list(db.session.scalars(stmt)), status=status,
                           action_form=ActionForm())


@bp.route("/new", methods=["GET", "POST"])
@admin_required
def create():
    form = EquipmentForm()
    if form.validate_on_submit():
        eq = Equipment()
        form.populate_obj(eq)
        db.session.add(eq)
        db.session.flush()
        audit.log("equipment.created", "equipment", eq.id, {"name": eq.name, "restricted": eq.is_restricted}, current_user.id)
        db.session.commit()
        flash("Equipment added.", "success")
        return redirect(url_for("equipment.index"))
    return render_template("equipment/form.html", form=form, eq=None)


@bp.route("/<int:equipment_id>/edit", methods=["GET", "POST"])
@admin_required
def edit(equipment_id):
    eq = db.get_or_404(Equipment, equipment_id)
    form = EquipmentForm(obj=eq)
    if form.validate_on_submit():
        before = {"status": eq.status, "restricted": eq.is_restricted, "active": eq.is_active}
        form.populate_obj(eq)
        audit.log("equipment.updated", "equipment", eq.id, {"before": before, "name": eq.name, "status": eq.status,
                                                            "restricted": eq.is_restricted, "active": eq.is_active},
                  current_user.id)
        db.session.commit()
        flash("Equipment updated.", "success")
        return redirect(url_for("equipment.index"))
    return render_template("equipment/form.html", form=form, eq=eq)


@bp.post("/<int:equipment_id>/deactivate")
@admin_required
def deactivate(equipment_id):
    eq = db.get_or_404(Equipment, equipment_id)
    if ActionForm().validate_on_submit():
        eq.is_active = False
        audit.log("equipment.deactivated", "equipment", eq.id, {"name": eq.name}, current_user.id)
        db.session.commit()
        flash("Equipment deactivated (history is kept).", "success")
    return redirect(url_for("equipment.index"))


@bp.route("/member/<int:member_id>", methods=["GET", "POST"])
@staff_required
def member_permissions(member_id):
    member = db.get_or_404(Member, member_id)
    matrix = svc.member_equipment_matrix(member)
    form = PermissionForm()
    form.equipment_id.choices = [(r["equipment"].id, r["equipment"].name) for r in matrix]
    if request.method == "POST":
        if current_user.role != "admin":
            abort(403)  # only administrators change permissions
        if form.validate_on_submit():
            try:
                svc.set_member_permission(member_id, form.equipment_id.data, form.effect.data,
                                          form.reason.data or "", current_user.id)
                flash("Permission updated.", "success")
                return redirect(url_for("equipment.member_permissions", member_id=member_id))
            except ServiceError as exc:
                flash(str(exc), "danger")
    usage = list(db.session.scalars(select(EquipmentUsage).where(EquipmentUsage.member_id == member_id)
                                    .order_by(EquipmentUsage.recorded_at.desc()).limit(20)))
    return render_template("equipment/member_permissions.html", member=member, matrix=matrix, form=form, usage=usage)


@bp.post("/member/<int:member_id>/usage")
@staff_required
def record_usage(member_id):
    form = UsageForm()
    form.equipment_id.choices = [(e.id, e.name) for e in db.session.scalars(select(Equipment))]
    if form.validate_on_submit():
        try:
            svc.record_usage(member_id, form.equipment_id.data, current_user.id, form.note.data)
            flash("Usage recorded (manually reported by staff).", "success")
        except ServiceError as exc:
            flash(str(exc), "danger")
    else:
        flash("Select the equipment to record.", "danger")
    return redirect(url_for("members.detail", member_id=member_id))
