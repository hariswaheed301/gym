import os

import click
from sqlalchemy import select, text

from .extensions import db
from .models import Equipment, Member, MembershipPlan, User
from .security import hash_password, password_problem


def register_cli(app):
    @app.cli.command("create-admin")
    @click.option("--username", prompt=True)
    @click.option("--full-name", prompt=True)
    @click.password_option(confirmation_prompt=True)
    def create_admin(username, full_name, password):
        """Create an administrator (password is prompted, never stored in shell history)."""
        username = username.strip().lower()
        problem = password_problem(password)
        if problem:
            raise click.ClickException(problem)
        if db.session.scalar(select(User.id).where(User.username == username)):
            raise click.ClickException("That username already exists.")
        db.session.add(User(username=username, full_name=full_name.strip(), role="admin",
                            password_hash=hash_password(password)))
        db.session.commit()
        click.echo(f"Administrator '{username}' created.")

    @app.cli.command("seed-demo")
    def seed_demo():
        """Development-only sample plans, equipment and two members (no staff, no payments)."""
        if os.environ.get("APP_ENV", "development") == "production":
            raise click.ClickException("Refusing to seed demo data in production.")
        from datetime import time
        from decimal import Decimal
        if not db.session.scalar(select(MembershipPlan.id).limit(1)):
            db.session.add_all([
                MembershipPlan(name="Monthly", duration_days=30, price=Decimal("5000"), currency="PKR"),
                MembershipPlan(name="Quarterly", duration_days=90, price=Decimal("13500"), currency="PKR"),
                MembershipPlan(name="Morning only (monthly)", duration_days=30, price=Decimal("4000"),
                               currency="PKR", access_start_time=time(5, 0), access_end_time=time(12, 0)),
            ])
        if not db.session.scalar(select(Equipment.id).limit(1)):
            db.session.add_all([
                Equipment(name="Treadmill 1", category="Cardio", location="Floor 1"),
                Equipment(name="Power Rack", category="Strength", location="Floor 1", is_restricted=True),
                Equipment(name="Leg Press", category="Strength", location="Floor 2", status="maintenance",
                          maintenance_notes="Cable replacement pending"),
            ])
        if not db.session.scalar(select(Member.id).limit(1)):
            db.session.add_all([
                Member(member_number="M90001", full_name="Demo Member One", phone="03000000001"),
                Member(member_number="M90002", full_name="Demo Member Two", phone="03000000002"),
            ])
        db.session.commit()
        click.echo("Demo data created.")

    @app.cli.command("db-health")
    def db_health():
        """Check database connectivity and show the applied migration."""
        db.session.execute(text("SELECT 1"))
        try:
            rev = db.session.scalar(text("SELECT version_num FROM alembic_version"))
        except Exception:  # noqa: BLE001
            rev = "none (run `flask db upgrade`)"
            db.session.rollback()
        click.echo(f"Database OK. Migration revision: {rev}")
