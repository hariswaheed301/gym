from flask import Blueprint, flash, redirect, render_template, url_for
from flask_login import current_user
from sqlalchemy import func, select

from ..extensions import db
from ..forms import ActionForm, ResetPasswordForm, StaffForm
from ..models import User
from ..security import admin_required, hash_password
from ..services import audit

bp = Blueprint("staff", __name__, url_prefix="/staff")


@bp.get("/")
@admin_required
def index():
    users = list(db.session.scalars(select(User).order_by(User.active.desc(), User.username)))
    return render_template("staff/list.html", users=users, action_form=ActionForm())


@bp.route("/new", methods=["GET", "POST"])
@admin_required
def create():
    form = StaffForm()
    if form.validate_on_submit():
        user = User(username=form.username.data.lower(), full_name=form.full_name.data, role=form.role.data,
                    password_hash=hash_password(form.password.data))
        db.session.add(user)
        db.session.flush()
        audit.log("staff.created", "user", user.id, {"username": user.username, "role": user.role}, current_user.id)
        db.session.commit()
        flash("Staff account created.", "success")
        return redirect(url_for("staff.index"))
    return render_template("staff/form.html", form=form, title="New staff account")


@bp.post("/<int:user_id>/toggle")
@admin_required
def toggle(user_id):
    user = db.get_or_404(User, user_id)
    if not ActionForm().validate_on_submit():
        return redirect(url_for("staff.index"))
    if user.id == current_user.id:
        flash("You cannot deactivate your own account.", "danger")
    elif user.active and user.role == "admin" and db.session.scalar(
            select(func.count()).select_from(User).where(User.role == "admin", User.active.is_(True))) <= 1:
        flash("At least one active administrator is required.", "danger")
    else:
        user.active = not user.active
        user.session_version += 1  # kills any live sessions
        audit.log("staff.activated" if user.active else "staff.deactivated", "user", user.id, {}, current_user.id)
        db.session.commit()
        flash(f"Account {'activated' if user.active else 'deactivated'}.", "success")
    return redirect(url_for("staff.index"))


@bp.route("/<int:user_id>/reset-password", methods=["GET", "POST"])
@admin_required
def reset_password(user_id):
    user = db.get_or_404(User, user_id)
    form = ResetPasswordForm()
    if form.validate_on_submit():
        user.password_hash = hash_password(form.password.data)
        user.session_version += 1
        audit.log("staff.password_reset", "user", user.id, {}, current_user.id)
        db.session.commit()
        flash("Password reset. The user's existing sessions were signed out.", "success")
        return redirect(url_for("staff.index"))
    return render_template("staff/form.html", form=form, title=f"Reset password: {user.username}")
