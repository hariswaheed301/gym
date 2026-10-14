"""SQLAlchemy models. All timestamps are timezone-aware and stored in UTC; money is NUMERIC(12,2)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from flask_login import UserMixin
from sqlalchemy import CheckConstraint, Index, UniqueConstraint, func, literal_column, text
from sqlalchemy.dialects.postgresql import JSONB, ExcludeConstraint

from .extensions import db
from .utils import local_today, now_utc

ROLE_ADMIN = "admin"
ROLE_RECEPTIONIST = "receptionist"
ROLES = (ROLE_ADMIN, ROLE_RECEPTIONIST)
PAYMENT_METHODS = ("cash", "bank_transfer", "card", "other")
EQUIPMENT_STATUSES = ("operational", "maintenance", "unavailable")


class TimestampMixin:
    created_at = db.Column(
        db.DateTime(timezone=True), nullable=False, default=now_utc, server_default=func.now()
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=now_utc,
        onupdate=now_utc,
        server_default=func.now(),
    )


class User(UserMixin, TimestampMixin, db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), nullable=False)
    full_name = db.Column(db.String(120), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=True, server_default=db.true())
    # Bumped on password change / deactivation: invalidates every existing session of this user.
    session_version = db.Column(db.Integer, nullable=False, default=1, server_default="1")
    last_login_at = db.Column(db.DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("username", name="uq_users_username"),
        CheckConstraint("role IN ('admin','receptionist')", name="ck_users_role"),
    )

    @property
    def is_active(self) -> bool:  # Flask-Login
        return bool(self.active)

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN

    def get_id(self) -> str:
        return f"{self.id}:{self.session_version}"


class Member(TimestampMixin, db.Model):
    __tablename__ = "members"
    id = db.Column(db.Integer, primary_key=True)
    member_number = db.Column(db.String(20), nullable=False)
    full_name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    email = db.Column(db.String(255))
    date_of_birth = db.Column(db.Date)
    gender = db.Column(db.String(10))
    address = db.Column(db.Text)
    emergency_contact_name = db.Column(db.String(120))
    emergency_contact_phone = db.Column(db.String(20))
    registered_on = db.Column(
        db.Date, nullable=False, default=local_today, server_default=func.current_date()
    )
    notes = db.Column(db.Text)  # internal; admin-only
    is_active = db.Column(db.Boolean, nullable=False, default=True, server_default=db.true())
    # Future multi-branch support: add `branch_id` FK here; intentionally not implemented in the MVP.

    memberships = db.relationship(
        "Membership", back_populates="member", order_by="Membership.start_date.desc()"
    )

    __table_args__ = (
        UniqueConstraint("member_number", name="uq_members_member_number"),
        UniqueConstraint("phone", name="uq_members_phone"),
        Index("uq_members_email", "email", unique=True),  # emails stored lowercase; NULLs allowed
        Index("ix_members_full_name", "full_name"),
    )


class MembershipPlan(TimestampMixin, db.Model):
    __tablename__ = "membership_plans"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    description = db.Column(db.Text)
    duration_days = db.Column(db.Integer, nullable=False)
    price = db.Column(db.Numeric(12, 2), nullable=False)
    currency = db.Column(db.String(3), nullable=False, default="PKR", server_default="PKR")
    access_start_time = db.Column(db.Time)  # both NULL = 24h access; start > end = overnight window
    access_end_time = db.Column(db.Time)
    is_active = db.Column(db.Boolean, nullable=False, default=True, server_default=db.true())

    equipment = db.relationship("PlanEquipmentPermission", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("name", name="uq_membership_plans_name"),
        CheckConstraint("duration_days > 0", name="ck_plans_duration"),
        CheckConstraint("price >= 0", name="ck_plans_price"),
        CheckConstraint(
            "(access_start_time IS NULL) = (access_end_time IS NULL)", name="ck_plans_hours_pair"
        ),
    )


class Membership(TimestampMixin, db.Model):
    """A member's subscription period. `status` is administrative (active/suspended/cancelled);
    whether the period is current/expired is derived from dates; payment state is derived from payments."""

    __tablename__ = "memberships"
    id = db.Column(db.Integer, primary_key=True)
    member_id = db.Column(db.Integer, db.ForeignKey("members.id", ondelete="RESTRICT"), nullable=False)
    plan_id = db.Column(db.Integer, db.ForeignKey("membership_plans.id", ondelete="RESTRICT"), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    expiry_date = db.Column(db.Date, nullable=False)  # inclusive last day
    price = db.Column(db.Numeric(12, 2), nullable=False)  # agreed price snapshot
    discount = db.Column(db.Numeric(12, 2), nullable=False, default=Decimal("0"), server_default="0")
    currency = db.Column(db.String(3), nullable=False, default="PKR", server_default="PKR")
    status = db.Column(db.String(20), nullable=False, default="active", server_default="active")
    renewed_from_id = db.Column(db.Integer, db.ForeignKey("memberships.id", ondelete="RESTRICT"))
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    notes = db.Column(db.Text)

    member = db.relationship("Member", back_populates="memberships")
    plan = db.relationship("MembershipPlan")
    renewed_from = db.relationship("Membership", remote_side=[id])
    payments = db.relationship("Payment", back_populates="membership", order_by="Payment.paid_at.desc()")

    __table_args__ = (
        CheckConstraint("expiry_date >= start_date", name="ck_memberships_dates"),
        CheckConstraint("price >= 0 AND discount >= 0 AND discount <= price", name="ck_memberships_amounts"),
        CheckConstraint("status IN ('active','suspended','cancelled')", name="ck_memberships_status"),
        # No two non-cancelled memberships of one member may overlap (needs btree_gist extension).
        ExcludeConstraint(
            ("member_id", "="),
            (literal_column("daterange(start_date, expiry_date, '[]')"), "&&"),
            using="gist",
            where=text("status <> 'cancelled'"),
            name="ex_memberships_no_overlap",
        ),
        Index("ix_memberships_member_expiry", "member_id", "expiry_date"),
        Index("ix_memberships_expiry", "expiry_date"),
    )

    @property
    def net_price(self) -> Decimal:
        return (self.price or Decimal("0")) - (self.discount or Decimal("0"))

    @property
    def paid_total(self) -> Decimal:
        return sum((p.amount for p in self.payments if p.status == "valid"), Decimal("0"))

    @property
    def outstanding(self) -> Decimal:
        return self.net_price - self.paid_total

    @property
    def payment_status(self) -> str:
        if self.outstanding <= 0:
            return "paid"
        return "partial" if self.paid_total > 0 else "unpaid"

    def state(self, today: date | None = None) -> str:
        today = today or local_today()
        if self.status != "active":
            return self.status
        if today < self.start_date:
            return "upcoming"
        return "current" if today <= self.expiry_date else "expired"


class Payment(db.Model):
    __tablename__ = "payments"
    id = db.Column(db.Integer, primary_key=True)
    membership_id = db.Column(
        db.Integer, db.ForeignKey("memberships.id", ondelete="RESTRICT"), nullable=False
    )
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    method = db.Column(db.String(20), nullable=False)
    paid_at = db.Column(db.DateTime(timezone=True), nullable=False, default=now_utc, server_default=func.now())
    reference_number = db.Column(db.String(80))
    receipt_number = db.Column(db.String(30))
    recorded_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    status = db.Column(db.String(10), nullable=False, default="valid", server_default="valid")
    void_reason = db.Column(db.Text)
    voided_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    voided_at = db.Column(db.DateTime(timezone=True))
    idempotency_key = db.Column(db.String(64))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=now_utc, server_default=func.now())

    membership = db.relationship("Membership", back_populates="payments")
    recorded_by = db.relationship("User", foreign_keys=[recorded_by_id])
    voided_by = db.relationship("User", foreign_keys=[voided_by_id])

    __table_args__ = (
        UniqueConstraint("receipt_number", name="uq_payments_receipt_number"),
        UniqueConstraint("idempotency_key", name="uq_payments_idempotency_key"),
        CheckConstraint("amount > 0", name="ck_payments_amount"),
        CheckConstraint("status IN ('valid','voided')", name="ck_payments_status"),
        CheckConstraint("method IN ('cash','bank_transfer','card','other')", name="ck_payments_method"),
        CheckConstraint(
            "status = 'valid' OR (void_reason IS NOT NULL AND voided_by_id IS NOT NULL)",
            name="ck_payments_void_info",
        ),
        Index("ix_payments_membership", "membership_id"),
        Index("ix_payments_paid_at", "paid_at"),
    )


class Attendance(TimestampMixin, db.Model):
    """One gym visit. Open = check_out_at IS NULL (no redundant status column)."""

    __tablename__ = "attendance"
    id = db.Column(db.Integer, primary_key=True)
    member_id = db.Column(db.Integer, db.ForeignKey("members.id", ondelete="RESTRICT"), nullable=False)
    check_in_at = db.Column(db.DateTime(timezone=True), nullable=False)
    check_out_at = db.Column(db.DateTime(timezone=True))
    check_in_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    check_out_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    check_in_method = db.Column(db.String(20), nullable=False, default="manual", server_default="manual")
    is_correction = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    correction_reason = db.Column(db.Text)
    corrected_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    corrected_at = db.Column(db.DateTime(timezone=True))

    member = db.relationship("Member")
    check_in_by = db.relationship("User", foreign_keys=[check_in_by_id])
    check_out_by = db.relationship("User", foreign_keys=[check_out_by_id])
    corrected_by = db.relationship("User", foreign_keys=[corrected_by_id])

    __table_args__ = (
        CheckConstraint(
            "check_out_at IS NULL OR check_out_at >= check_in_at", name="ck_attendance_order"
        ),
        CheckConstraint(
            "is_correction = false OR (correction_reason IS NOT NULL AND corrected_by_id IS NOT NULL)",
            name="ck_attendance_correction_info",
        ),
        # The core invariant: at most one open session per member.
        Index(
            "uq_attendance_one_open_per_member",
            "member_id",
            unique=True,
            postgresql_where=text("check_out_at IS NULL"),
        ),
        Index("ix_attendance_check_in_at", "check_in_at"),
        Index("ix_attendance_member_check_in", "member_id", "check_in_at"),
    )

    @property
    def is_open(self) -> bool:
        return self.check_out_at is None

    @property
    def duration(self):
        return None if self.check_out_at is None else self.check_out_at - self.check_in_at

    def elapsed(self, now: datetime):
        return now - self.check_in_at


class AttendanceDevice(TimestampMixin, db.Model):
    __tablename__ = "attendance_devices"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    adapter = db.Column(db.String(20), nullable=False, default="simulator", server_default="simulator")
    host = db.Column(db.String(45))
    port = db.Column(db.Integer, nullable=False, default=4370, server_default="4370")
    transport = db.Column(db.String(8), nullable=False, default="tcp", server_default="tcp")
    timeout_seconds = db.Column(db.Integer, nullable=False, default=5, server_default="5")
    timezone = db.Column(db.String(80), nullable=False, default="Asia/Karachi", server_default="Asia/Karachi")
    credential_ciphertext = db.Column(db.Text)
    direction_field = db.Column(db.String(12), nullable=False, default="punch", server_default="punch")
    check_in_codes = db.Column(db.String(100), nullable=False, default="0", server_default="0")
    check_out_codes = db.Column(db.String(100), nullable=False, default="1", server_default="1")
    is_active = db.Column(db.Boolean, nullable=False, default=True, server_default=db.true())
    connection_state = db.Column(db.String(16), nullable=False, default="unknown", server_default="unknown")
    last_seen_at = db.Column(db.DateTime(timezone=True))
    last_sync_at = db.Column(db.DateTime(timezone=True))
    last_error = db.Column(db.Text)

    mappings = db.relationship(
        "AttendanceDeviceMapping", back_populates="device", cascade="all, delete-orphan"
    )
    punches = db.relationship(
        "AttendanceDevicePunch", back_populates="device", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("name", name="uq_attendance_devices_name"),
        CheckConstraint("adapter IN ('simulator','pyzk')", name="ck_attendance_devices_adapter"),
        CheckConstraint("transport IN ('tcp','udp')", name="ck_attendance_devices_transport"),
        CheckConstraint("direction_field IN ('punch','status')", name="ck_attendance_devices_direction"),
        CheckConstraint("port BETWEEN 1 AND 65535", name="ck_attendance_devices_port"),
        CheckConstraint("timeout_seconds BETWEEN 1 AND 30", name="ck_attendance_devices_timeout"),
        CheckConstraint("connection_state IN ('unknown','online','offline','demo')",
                        name="ck_attendance_devices_state"),
    )


class AttendanceDeviceMapping(db.Model):
    __tablename__ = "attendance_device_mappings"
    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(
        db.Integer, db.ForeignKey("attendance_devices.id", ondelete="CASCADE"), nullable=False
    )
    device_user_id = db.Column(db.String(64), nullable=False)
    member_id = db.Column(db.Integer, db.ForeignKey("members.id", ondelete="RESTRICT"), nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True, server_default=db.true())
    created_at = db.Column(
        db.DateTime(timezone=True), nullable=False, default=now_utc, server_default=func.now()
    )

    device = db.relationship("AttendanceDevice", back_populates="mappings")
    member = db.relationship("Member")

    __table_args__ = (
        UniqueConstraint("device_id", "device_user_id", name="uq_device_mapping_user"),
        Index("ix_device_mappings_member", "member_id"),
    )


class AttendanceDevicePunch(db.Model):
    __tablename__ = "attendance_device_punches"
    id = db.Column(db.BigInteger, primary_key=True)
    device_id = db.Column(
        db.Integer, db.ForeignKey("attendance_devices.id", ondelete="CASCADE"), nullable=False
    )
    event_key = db.Column(db.String(64), nullable=False)
    device_user_id = db.Column(db.String(64), nullable=False)
    punched_at = db.Column(db.DateTime(timezone=True), nullable=False)
    direction = db.Column(db.String(10), nullable=False)
    event_code = db.Column(db.Integer, nullable=False)
    outcome = db.Column(db.String(16), nullable=False, default="pending", server_default="pending")
    message = db.Column(db.String(255))
    attendance_id = db.Column(db.Integer, db.ForeignKey("attendance.id", ondelete="SET NULL"))
    received_at = db.Column(
        db.DateTime(timezone=True), nullable=False, default=now_utc, server_default=func.now()
    )

    device = db.relationship("AttendanceDevice", back_populates="punches")
    attendance = db.relationship("Attendance")

    __table_args__ = (
        UniqueConstraint("device_id", "event_key", name="uq_device_punch_event"),
        CheckConstraint("direction IN ('in','out','unknown')", name="ck_device_punch_direction"),
        CheckConstraint(
            "outcome IN ('pending','imported','ignored','rejected')", name="ck_device_punch_outcome"
        ),
        Index("ix_device_punches_received", "device_id", "received_at"),
    )


class Equipment(TimestampMixin, db.Model):
    __tablename__ = "equipment"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    category = db.Column(db.String(60), nullable=False)
    description = db.Column(db.Text)
    location = db.Column(db.String(80))
    status = db.Column(db.String(20), nullable=False, default="operational", server_default="operational")
    is_restricted = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    maintenance_notes = db.Column(db.Text)
    is_active = db.Column(db.Boolean, nullable=False, default=True, server_default=db.true())

    __table_args__ = (
        CheckConstraint(
            "status IN ('operational','maintenance','unavailable')", name="ck_equipment_status"
        ),
        Index("ix_equipment_status", "status"),
    )


class EquipmentPermission(db.Model):
    """Member-specific override. 'deny' always wins; 'allow' grants restricted equipment."""

    __tablename__ = "equipment_permissions"
    id = db.Column(db.Integer, primary_key=True)
    member_id = db.Column(db.Integer, db.ForeignKey("members.id", ondelete="RESTRICT"), nullable=False)
    equipment_id = db.Column(db.Integer, db.ForeignKey("equipment.id", ondelete="RESTRICT"), nullable=False)
    effect = db.Column(db.String(5), nullable=False)
    reason = db.Column(db.Text)
    granted_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=now_utc, server_default=func.now())

    equipment = db.relationship("Equipment")

    __table_args__ = (
        UniqueConstraint("member_id", "equipment_id", name="uq_equipment_permissions_member_equipment"),
        CheckConstraint("effect IN ('allow','deny')", name="ck_equipment_permissions_effect"),
    )


class PlanEquipmentPermission(db.Model):
    """Plan-level grant: members on this plan may use this restricted equipment."""

    __tablename__ = "plan_equipment_permissions"
    plan_id = db.Column(
        db.Integer, db.ForeignKey("membership_plans.id", ondelete="CASCADE"), primary_key=True
    )
    equipment_id = db.Column(
        db.Integer, db.ForeignKey("equipment.id", ondelete="CASCADE"), primary_key=True
    )


class EquipmentUsage(db.Model):
    """Manually recorded by staff. NOT automatically detected (no hardware in the MVP)."""

    __tablename__ = "equipment_usage"
    id = db.Column(db.Integer, primary_key=True)
    member_id = db.Column(db.Integer, db.ForeignKey("members.id", ondelete="RESTRICT"), nullable=False)
    equipment_id = db.Column(db.Integer, db.ForeignKey("equipment.id", ondelete="RESTRICT"), nullable=False)
    recorded_at = db.Column(db.DateTime(timezone=True), nullable=False, default=now_utc, server_default=func.now())
    recorded_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    note = db.Column(db.String(255))

    equipment = db.relationship("Equipment")
    recorded_by = db.relationship("User")

    __table_args__ = (Index("ix_equipment_usage_member", "member_id", "recorded_at"),)


class AuditLog(db.Model):
    __tablename__ = "audit_logs"
    id = db.Column(db.BigInteger().with_variant(db.Integer, "sqlite"), primary_key=True)
    at = db.Column(db.DateTime(timezone=True), nullable=False, default=now_utc, server_default=func.now())
    actor_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    action = db.Column(db.String(60), nullable=False)
    entity_type = db.Column(db.String(40), nullable=False)
    entity_id = db.Column(db.Integer)
    details = db.Column(JSONB)
    ip_address = db.Column(db.String(45))

    actor = db.relationship("User")

    __table_args__ = (
        Index("ix_audit_logs_at", "at"),
        Index("ix_audit_logs_entity", "entity_type", "entity_id"),
        Index("ix_audit_logs_action", "action"),
    )
