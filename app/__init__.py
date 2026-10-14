from __future__ import annotations

import logging
import os
import secrets

from flask import Flask, jsonify, redirect, render_template, request, url_for
from flask_wtf.csrf import CSRFError
from sqlalchemy import text
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import CONFIGS
from .extensions import csrf, db, limiter, login_manager, migrate, talisman
from .utils import register_template_helpers

CSP = {
    "default-src": "'self'",
    "script-src": ["'self'", "https://cdn.jsdelivr.net"],
    "style-src": ["'self'", "https://cdn.jsdelivr.net"],
    "font-src": ["'self'", "https://cdn.jsdelivr.net"],
    "img-src": ["'self'", "data:"],
    "connect-src": "'self'",
    "frame-ancestors": "'none'",
    "form-action": "'self'",
    "base-uri": "'self'",
    "object-src": "'none'",
}


def create_app(config_name: str | None = None, overrides: dict | None = None) -> Flask:
    config_name = config_name or os.environ.get("APP_ENV", "development")
    app = Flask(__name__)
    app.config.from_object(CONFIGS[config_name])
    if overrides:
        app.config.update(overrides)

    if not app.config.get("SECRET_KEY"):
        if config_name == "production":
            raise RuntimeError("SECRET_KEY must be set in production.")
        app.config["SECRET_KEY"] = secrets.token_hex(32)  # dev only: sessions reset on restart
    if config_name == "production" and len(app.config["SECRET_KEY"]) < 32:
        raise RuntimeError("SECRET_KEY must be at least 32 characters in production.")
    if app.config["DUES_POLICY"] not in ("allow", "require_partial", "require_full"):
        raise RuntimeError("DUES_POLICY must be one of: allow, require_partial, require_full.")

    if not app.testing:
        logging.basicConfig(level=app.config["LOG_LEVEL"], format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if app.config["TRUSTED_PROXY_COUNT"]:
        n = app.config["TRUSTED_PROXY_COUNT"]
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=n, x_proto=n, x_host=n)

    db.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)
    limiter.init_app(app)
    talisman.init_app(
        app, content_security_policy=CSP, force_https=app.config["FORCE_HTTPS"],
        strict_transport_security=app.config["FORCE_HTTPS"], session_cookie_secure=app.config["SESSION_COOKIE_SECURE"],
        frame_options="DENY", referrer_policy="same-origin",
    )
    _init_login(app)
    register_template_helpers(app)

    @app.context_processor
    def _globals():
        return {"gym_name": app.config["GYM_NAME"], "default_currency": app.config["DEFAULT_CURRENCY"]}

    from .blueprints import (
        attendance,
        audit,
        auth,
        dashboard,
        devices,
        equipment,
        members,
        memberships,
        payments,
        reports,
        staff,
    )
    for module in (
        auth, dashboard, members, memberships, payments, attendance, devices, equipment, staff, reports, audit
    ):
        app.register_blueprint(module.bp)

    _register_errors(app)

    @app.get("/healthz")
    @limiter.exempt
    def healthz():
        try:
            db.session.execute(text("SELECT 1"))
            return jsonify(status="ok")
        except Exception:  # noqa: BLE001
            app.logger.exception("health check failed")
            return jsonify(status="error"), 503

    from .cli import register_cli
    register_cli(app)
    return app


def _init_login(app: Flask) -> None:
    from .models import User

    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Please sign in to continue."
    login_manager.login_message_category = "warning"
    login_manager.session_protection = "strong"

    @login_manager.user_loader
    def load_user(user_id: str):
        try:
            uid, version = (int(p) for p in user_id.split(":"))
        except ValueError:
            return None
        user = db.session.get(User, uid)
        if user is not None and user.active and user.session_version == version:
            return user
        return None

    @login_manager.unauthorized_handler
    def unauthorized():
        if request.headers.get("X-Requested-With") == "fetch":
            return ("", 401)
        from flask import flash
        flash(login_manager.login_message, login_manager.login_message_category)
        return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))


def _register_errors(app: Flask) -> None:
    def render(code: int, title: str, message: str):
        return render_template("errors/error.html", code=code, title=title, message=message), code

    @app.errorhandler(400)
    def bad_request(_e):
        return render(400, "Bad request", "The request could not be understood.")

    @app.errorhandler(CSRFError)
    def csrf_error(_e):
        return render(400, "Session expired", "Your form expired or was invalid. Go back, reload the page and try again.")

    @app.errorhandler(403)
    def forbidden(_e):
        return render(403, "Not allowed", "You do not have permission to do that.")

    @app.errorhandler(404)
    def not_found(_e):
        return render(404, "Not found", "That page or record does not exist.")

    @app.errorhandler(429)
    def too_many(_e):
        return render(429, "Too many attempts", "Please wait a minute and try again.")

    @app.errorhandler(500)
    def server_error(_e):
        db.session.rollback()
        return render(500, "Something went wrong", "An unexpected error occurred. It has been logged.")
