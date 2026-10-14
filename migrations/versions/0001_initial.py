"""Initial schema: users, members, plans, memberships, payments, attendance, equipment, audit.

Revision ID: 0001
Revises:
Create Date: 2026-10-10
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

NOW = sa.text("now()")


def _ts():
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
    ]


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")  # needed by the membership-overlap exclusion constraint

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(50), nullable=False),
        sa.Column("full_name", sa.String(120), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("session_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        *_ts(),
        sa.UniqueConstraint("username", name="uq_users_username"),
        sa.CheckConstraint("role IN ('admin','receptionist')", name="ck_users_role"),
    )

    op.create_table(
        "members",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("member_number", sa.String(20), nullable=False),
        sa.Column("full_name", sa.String(120), nullable=False),
        sa.Column("phone", sa.String(20), nullable=False),
        sa.Column("email", sa.String(255)),
        sa.Column("date_of_birth", sa.Date()),
        sa.Column("gender", sa.String(10)),
        sa.Column("address", sa.Text()),
        sa.Column("emergency_contact_name", sa.String(120)),
        sa.Column("emergency_contact_phone", sa.String(20)),
        sa.Column("registered_on", sa.Date(), server_default=sa.text("CURRENT_DATE"), nullable=False),
        sa.Column("notes", sa.Text()),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        *_ts(),
        sa.UniqueConstraint("member_number", name="uq_members_member_number"),
        sa.UniqueConstraint("phone", name="uq_members_phone"),
    )
    op.create_index("uq_members_email", "members", ["email"], unique=True)
    op.create_index("ix_members_full_name", "members", ["full_name"])

    op.create_table(
        "membership_plans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("duration_days", sa.Integer(), nullable=False),
        sa.Column("price", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(3), server_default="PKR", nullable=False),
        sa.Column("access_start_time", sa.Time()),
        sa.Column("access_end_time", sa.Time()),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        *_ts(),
        sa.UniqueConstraint("name", name="uq_membership_plans_name"),
        sa.CheckConstraint("duration_days > 0", name="ck_plans_duration"),
        sa.CheckConstraint("price >= 0", name="ck_plans_price"),
        sa.CheckConstraint("(access_start_time IS NULL) = (access_end_time IS NULL)", name="ck_plans_hours_pair"),
    )

    op.create_table(
        "memberships",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("member_id", sa.Integer(), sa.ForeignKey("members.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("plan_id", sa.Integer(), sa.ForeignKey("membership_plans.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("expiry_date", sa.Date(), nullable=False),
        sa.Column("price", sa.Numeric(12, 2), nullable=False),
        sa.Column("discount", sa.Numeric(12, 2), server_default="0", nullable=False),
        sa.Column("currency", sa.String(3), server_default="PKR", nullable=False),
        sa.Column("status", sa.String(20), server_default="active", nullable=False),
        sa.Column("renewed_from_id", sa.Integer(), sa.ForeignKey("memberships.id", ondelete="RESTRICT")),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("notes", sa.Text()),
        *_ts(),
        sa.CheckConstraint("expiry_date >= start_date", name="ck_memberships_dates"),
        sa.CheckConstraint("price >= 0 AND discount >= 0 AND discount <= price", name="ck_memberships_amounts"),
        sa.CheckConstraint("status IN ('active','suspended','cancelled')", name="ck_memberships_status"),
    )
    op.execute(
        "ALTER TABLE memberships ADD CONSTRAINT ex_memberships_no_overlap EXCLUDE USING gist "
        "(member_id WITH =, daterange(start_date, expiry_date, '[]') WITH &&) WHERE (status <> 'cancelled')"
    )
    op.create_index("ix_memberships_member_expiry", "memberships", ["member_id", "expiry_date"])
    op.create_index("ix_memberships_expiry", "memberships", ["expiry_date"])

    op.create_table(
        "payments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("membership_id", sa.Integer(), sa.ForeignKey("memberships.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("method", sa.String(20), nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("reference_number", sa.String(80)),
        sa.Column("receipt_number", sa.String(30)),
        sa.Column("recorded_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("status", sa.String(10), server_default="valid", nullable=False),
        sa.Column("void_reason", sa.Text()),
        sa.Column("voided_by_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("voided_at", sa.DateTime(timezone=True)),
        sa.Column("idempotency_key", sa.String(64)),
        sa.Column("notes", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.UniqueConstraint("receipt_number", name="uq_payments_receipt_number"),
        sa.UniqueConstraint("idempotency_key", name="uq_payments_idempotency_key"),
        sa.CheckConstraint("amount > 0", name="ck_payments_amount"),
        sa.CheckConstraint("status IN ('valid','voided')", name="ck_payments_status"),
        sa.CheckConstraint("method IN ('cash','bank_transfer','card','other')", name="ck_payments_method"),
        sa.CheckConstraint("status = 'valid' OR (void_reason IS NOT NULL AND voided_by_id IS NOT NULL)",
                           name="ck_payments_void_info"),
    )
    op.create_index("ix_payments_membership", "payments", ["membership_id"])
    op.create_index("ix_payments_paid_at", "payments", ["paid_at"])

    op.create_table(
        "attendance",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("member_id", sa.Integer(), sa.ForeignKey("members.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("check_in_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("check_out_at", sa.DateTime(timezone=True)),
        sa.Column("check_in_by_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("check_out_by_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("check_in_method", sa.String(20), server_default="manual", nullable=False),
        sa.Column("is_correction", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("correction_reason", sa.Text()),
        sa.Column("corrected_by_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("corrected_at", sa.DateTime(timezone=True)),
        *_ts(),
        sa.CheckConstraint("check_out_at IS NULL OR check_out_at >= check_in_at", name="ck_attendance_order"),
        sa.CheckConstraint("is_correction = false OR (correction_reason IS NOT NULL AND corrected_by_id IS NOT NULL)",
                           name="ck_attendance_correction_info"),
    )
    op.create_index("uq_attendance_one_open_per_member", "attendance", ["member_id"], unique=True,
                    postgresql_where=sa.text("check_out_at IS NULL"))
    op.create_index("ix_attendance_check_in_at", "attendance", ["check_in_at"])
    op.create_index("ix_attendance_member_check_in", "attendance", ["member_id", "check_in_at"])

    op.create_table(
        "equipment",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("category", sa.String(60), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("location", sa.String(80)),
        sa.Column("status", sa.String(20), server_default="operational", nullable=False),
        sa.Column("is_restricted", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("maintenance_notes", sa.Text()),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        *_ts(),
        sa.CheckConstraint("status IN ('operational','maintenance','unavailable')", name="ck_equipment_status"),
    )
    op.create_index("ix_equipment_status", "equipment", ["status"])

    op.create_table(
        "equipment_permissions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("member_id", sa.Integer(), sa.ForeignKey("members.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("equipment_id", sa.Integer(), sa.ForeignKey("equipment.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("effect", sa.String(5), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("granted_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.UniqueConstraint("member_id", "equipment_id", name="uq_equipment_permissions_member_equipment"),
        sa.CheckConstraint("effect IN ('allow','deny')", name="ck_equipment_permissions_effect"),
    )

    op.create_table(
        "plan_equipment_permissions",
        sa.Column("plan_id", sa.Integer(), sa.ForeignKey("membership_plans.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("equipment_id", sa.Integer(), sa.ForeignKey("equipment.id", ondelete="CASCADE"), primary_key=True),
    )

    op.create_table(
        "equipment_usage",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("member_id", sa.Integer(), sa.ForeignKey("members.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("equipment_id", sa.Integer(), sa.ForeignKey("equipment.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("recorded_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("note", sa.String(255)),
    )
    op.create_index("ix_equipment_usage_member", "equipment_usage", ["member_id", "recorded_at"])

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("action", sa.String(60), nullable=False),
        sa.Column("entity_type", sa.String(40), nullable=False),
        sa.Column("entity_id", sa.Integer()),
        sa.Column("details", postgresql.JSONB()),
        sa.Column("ip_address", sa.String(45)),
    )
    op.create_index("ix_audit_logs_at", "audit_logs", ["at"])
    op.create_index("ix_audit_logs_entity", "audit_logs", ["entity_type", "entity_id"])
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])


def downgrade():
    for table in ("audit_logs", "equipment_usage", "plan_equipment_permissions", "equipment_permissions", "equipment",
                  "attendance", "payments", "memberships", "membership_plans", "members", "users"):
        op.drop_table(table)
