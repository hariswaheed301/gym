from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import select

from ..extensions import db, limiter
from ..forms import ActionForm, LoginForm
from ..models import User
from ..security import DUMMY_HASH, hash_password, needs_rehash, safe_next_url, verify_password
from ..services import audit
from ..utils import now_utc

bp = Blueprint("auth", __name__)


@bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute; 60 per hour", methods=["POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))
    form = LoginForm()
    if form.validate_on_submit():
        username = form.username.data.strip().lower()
        user = db.session.scalar(select(User).where(User.username == username))
        password_ok = verify_password(user.password_hash if user else DUMMY_HASH, form.password.data)
        if user and password_ok and user.active:
            session.clear()  # prevent session fixation
            login_user(user)
            session.permanent = True
            user.last_login_at = now_utc()
            if needs_rehash(user.password_hash):
                user.password_hash = hash_password(form.password.data)
            audit.log("auth.login", "user", user.id, actor_id=user.id)
            db.session.commit()
            return redirect(safe_next_url(request.args.get("next")) or url_for("dashboard.index"))
        audit.log("auth.login_failed", "user", user.id if user else None, {"username": username[:50]})
        db.session.commit()
        flash("Invalid username or password.", "danger")  # deliberately generic
    return render_template("auth/login.html", form=form)


@bp.post("/logout")
@login_required
def logout():
    if ActionForm().validate_on_submit():
        audit.log("auth.logout", "user", current_user.id, actor_id=current_user.id)
        db.session.commit()
        logout_user()
        session.clear()
        flash("You have been signed out.", "info")
    return redirect(url_for("auth.login"))
