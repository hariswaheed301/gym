from flask import Blueprint, flash, redirect, render_template, url_for
from flask_login import current_user
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..forms import AttendanceDeviceForm, DeviceMappingForm, SimulatedPunchForm
from ..models import AttendanceDevice, AttendanceDeviceMapping, Member
from ..security import admin_required
from ..services import audit
from ..services import devices as svc
from ..services.errors import ServiceError

bp = Blueprint("devices", __name__, url_prefix="/devices")


def _apply_form(device: AttendanceDevice, form: AttendanceDeviceForm) -> None:
    for name in ("name", "adapter", "host", "port", "transport", "timeout_seconds", "timezone",
                 "direction_field", "check_in_codes", "check_out_codes", "is_active"):
        setattr(device, name, form[name].data)
    if form.communication_key.data:
        device.credential_ciphertext = svc.encrypt_credential(form.communication_key.data)
    elif device.adapter == "simulator":
        device.credential_ciphertext = None
    if device.adapter == "simulator":
        device.connection_state = "demo"
        device.last_error = None
    else:
        device.connection_state = "unknown"
        device.last_error = None


@bp.get("/")
@admin_required
def index():
    devices = list(db.session.scalars(select(AttendanceDevice).order_by(AttendanceDevice.name)))
    return render_template("devices/list.html", devices=devices)


@bp.route("/new", methods=["GET", "POST"])
@admin_required
def create():
    form = AttendanceDeviceForm()
    if form.validate_on_submit():
        device = AttendanceDevice()
        try:
            _apply_form(device, form)
        except ServiceError as exc:
            flash(str(exc), "danger")
            return render_template("devices/form.html", form=form, device=None)
        db.session.add(device)
        try:
            db.session.flush()
            audit.log("device.created", "attendance_device", device.id,
                      {"name": device.name, "adapter": device.adapter}, current_user.id)
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("A device with that name already exists.", "danger")
            return render_template("devices/form.html", form=form, device=None)
        flash("Attendance device added.", "success")
        return redirect(url_for("devices.index"))
    return render_template("devices/form.html", form=form, device=None)


@bp.route("/<int:device_id>/edit", methods=["GET", "POST"])
@admin_required
def edit(device_id):
    device = db.get_or_404(AttendanceDevice, device_id)
    form = AttendanceDeviceForm(obj=device, device_id=device.id)
    if form.validate_on_submit():
        before = {"adapter": device.adapter, "host": device.host, "port": device.port,
                  "transport": device.transport, "active": device.is_active}
        try:
            _apply_form(device, form)
        except ServiceError as exc:
            flash(str(exc), "danger")
            return render_template("devices/form.html", form=form, device=device)
        audit.log("device.updated", "attendance_device", device.id,
                  {"before": before, "adapter": device.adapter, "host": device.host,
                   "port": device.port, "active": device.is_active}, current_user.id)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("A device with that name already exists.", "danger")
            return render_template("devices/form.html", form=form, device=device)
        flash("Attendance device updated.", "success")
        return redirect(url_for("devices.index"))
    return render_template("devices/form.html", form=form, device=device)


@bp.get("/<int:device_id>")
@admin_required
def detail(device_id):
    device = db.get_or_404(AttendanceDevice, device_id)
    mappings = list(db.session.scalars(
        select(AttendanceDeviceMapping).where(AttendanceDeviceMapping.device_id == device.id)
        .order_by(AttendanceDeviceMapping.device_user_id)
    ))
    mapping_form = DeviceMappingForm()
    mapping_form.member_id.choices = [
        (member.id, f"{member.member_number} - {member.full_name}")
        for member in db.session.scalars(
            select(Member).where(Member.is_active.is_(True)).order_by(Member.full_name)
        )
    ]
    sim_form = SimulatedPunchForm()
    sim_form.mapping_id.choices = [(mapping.id, f"{mapping.device_user_id} → {mapping.member.full_name}")
                                   for mapping in mappings if mapping.is_active]
    return render_template("devices/detail.html", device=device, mappings=mappings,
                           punches=svc.recent_punches(device.id), mapping_form=mapping_form,
                           sim_form=sim_form)


@bp.post("/<int:device_id>/mappings")
@admin_required
def add_mapping(device_id):
    device = db.get_or_404(AttendanceDevice, device_id)
    form = DeviceMappingForm()
    form.member_id.choices = [(member.id, member.full_name) for member in db.session.scalars(
        select(Member).where(Member.is_active.is_(True)).order_by(Member.full_name))]
    if form.validate_on_submit():
        mapping = AttendanceDeviceMapping(device_id=device.id, device_user_id=form.device_user_id.data,
                                          member_id=form.member_id.data)
        db.session.add(mapping)
        try:
            db.session.flush()
            audit.log("device.mapping_created", "device_mapping", mapping.id,
                      {"device_id": device.id, "device_user_id": mapping.device_user_id,
                       "member_id": mapping.member_id}, current_user.id)
            db.session.commit()
            flash("Member mapping added.", "success")
        except IntegrityError:
            db.session.rollback()
            flash("That device user ID is already mapped on this device.", "danger")
    else:
        flash("Check the device user ID and member fields.", "danger")
    return redirect(url_for("devices.detail", device_id=device.id))


@bp.post("/<int:device_id>/mappings/<int:mapping_id>/deactivate")
@admin_required
def deactivate_mapping(device_id, mapping_id):
    mapping = db.get_or_404(AttendanceDeviceMapping, mapping_id)
    if mapping.device_id != device_id:
        return redirect(url_for("devices.detail", device_id=device_id))
    mapping.is_active = False
    audit.log("device.mapping_deactivated", "device_mapping", mapping.id,
              {"device_id": device_id, "device_user_id": mapping.device_user_id}, current_user.id)
    db.session.commit()
    flash("Mapping deactivated; punch history was retained.", "success")
    return redirect(url_for("devices.detail", device_id=device_id))


@bp.post("/<int:device_id>/test")
@admin_required
def test(device_id):
    device = db.get_or_404(AttendanceDevice, device_id)
    try:
        label = svc.test_connection(device)
        flash(f"Connection successful: {label}.", "success")
    except ServiceError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("devices.detail", device_id=device.id))


@bp.post("/<int:device_id>/sync")
@admin_required
def sync(device_id):
    device = db.get_or_404(AttendanceDevice, device_id)
    try:
        counts = svc.sync_device(device, current_user.id)
        flash(f"Sync complete: {counts['seen']} read, {counts['imported']} imported, "
              f"{counts['ignored']} ignored, {counts['rejected']} rejected, "
              f"{counts['duplicates']} duplicates.", "success")
    except ServiceError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("devices.detail", device_id=device.id))


@bp.post("/<int:device_id>/simulate")
@admin_required
def simulate(device_id):
    device = db.get_or_404(AttendanceDevice, device_id)
    form = SimulatedPunchForm()
    mappings = list(db.session.scalars(select(AttendanceDeviceMapping).where(
        AttendanceDeviceMapping.device_id == device.id, AttendanceDeviceMapping.is_active.is_(True))))
    form.mapping_id.choices = [(mapping.id, mapping.device_user_id) for mapping in mappings]
    if form.validate_on_submit():
        mapping = db.session.get(AttendanceDeviceMapping, form.mapping_id.data)
        if mapping is not None and mapping.device_id == device.id:
            try:
                outcome = svc.simulate_punch(device, mapping, form.direction.data, current_user.id)
                category = "success" if outcome == "imported" else "warning"
                flash(f"Simulated punch result: {outcome}.", category)
            except ServiceError as exc:
                flash(str(exc), "danger")
        else:
            flash("Choose a mapping belonging to this device.", "danger")
    else:
        flash("Choose a mapped device user and punch direction.", "danger")
    return redirect(url_for("devices.detail", device_id=device.id))