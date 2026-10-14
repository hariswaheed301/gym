"""Add LAN attendance devices, user mappings, and imported punch log.

Revision ID: 0002
Revises: 0001
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "attendance_devices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("adapter", sa.String(20), server_default="simulator", nullable=False),
        sa.Column("host", sa.String(45)),
        sa.Column("port", sa.Integer(), server_default="4370", nullable=False),
        sa.Column("transport", sa.String(8), server_default="tcp", nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), server_default="5", nullable=False),
        sa.Column("timezone", sa.String(80), server_default="Asia/Karachi", nullable=False),
        sa.Column("credential_ciphertext", sa.Text()),
        sa.Column("direction_field", sa.String(12), server_default="punch", nullable=False),
        sa.Column("check_in_codes", sa.String(100), server_default="0", nullable=False),
        sa.Column("check_out_codes", sa.String(100), server_default="1", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("connection_state", sa.String(16), server_default="unknown", nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.Column("last_sync_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("name", name="uq_attendance_devices_name"),
        sa.CheckConstraint("adapter IN ('simulator','pyzk')", name="ck_attendance_devices_adapter"),
        sa.CheckConstraint("transport IN ('tcp','udp')", name="ck_attendance_devices_transport"),
        sa.CheckConstraint("direction_field IN ('punch','status')", name="ck_attendance_devices_direction"),
        sa.CheckConstraint("port BETWEEN 1 AND 65535", name="ck_attendance_devices_port"),
        sa.CheckConstraint("timeout_seconds BETWEEN 1 AND 30", name="ck_attendance_devices_timeout"),
        sa.CheckConstraint("connection_state IN ('unknown','online','offline','demo')",
                           name="ck_attendance_devices_state"),
    )
    op.create_table(
        "attendance_device_mappings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "device_id",
            sa.Integer(),
            sa.ForeignKey("attendance_devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("device_user_id", sa.String(64), nullable=False),
        sa.Column(
            "member_id", sa.Integer(), sa.ForeignKey("members.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("device_id", "device_user_id", name="uq_device_mapping_user"),
    )
    op.create_index("ix_device_mappings_member", "attendance_device_mappings", ["member_id"])
    op.create_table(
        "attendance_device_punches",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "device_id",
            sa.Integer(),
            sa.ForeignKey("attendance_devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_key", sa.String(64), nullable=False),
        sa.Column("device_user_id", sa.String(64), nullable=False),
        sa.Column("punched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("direction", sa.String(10), nullable=False),
        sa.Column("event_code", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(16), server_default="pending", nullable=False),
        sa.Column("message", sa.String(255)),
        sa.Column("attendance_id", sa.Integer(), sa.ForeignKey("attendance.id", ondelete="SET NULL")),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("device_id", "event_key", name="uq_device_punch_event"),
        sa.CheckConstraint("direction IN ('in','out','unknown')", name="ck_device_punch_direction"),
        sa.CheckConstraint(
            "outcome IN ('pending','imported','ignored','rejected')", name="ck_device_punch_outcome"
        ),
    )
    op.create_index("ix_device_punches_received", "attendance_device_punches", ["device_id", "received_at"])


def downgrade():
    op.drop_table("attendance_device_punches")
    op.drop_index("ix_device_mappings_member", table_name="attendance_device_mappings")
    op.drop_table("attendance_device_mappings")
    op.drop_table("attendance_devices")