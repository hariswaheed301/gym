from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from ipaddress import ip_address
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask_wtf import FlaskForm
from sqlalchemy import select
from wtforms import (
    BooleanField,
    DateField,
    DecimalField,
    HiddenField,
    IntegerField,
    PasswordField,
    SelectField,
    SelectMultipleField,
    StringField,
    TextAreaField,
    TimeField,
    ValidationError,
)
from wtforms.validators import DataRequired, Email, EqualTo, InputRequired, Length, NumberRange, Optional

from .extensions import db
from .models import EQUIPMENT_STATUSES, PAYMENT_METHODS, ROLES, AttendanceDevice, Member, User
from .security import password_problem

_PHONE_RE = re.compile(r"^\+?\d{7,15}$")


def normalize_phone(value: str | None) -> str | None:
    if not value:
        return None
    return re.sub(r"[\s\-()]", "", value)


class StrippedStringField(StringField):
    def process_formdata(self, valuelist):
        super().process_formdata(valuelist)
        if isinstance(self.data, str):
            self.data = self.data.strip()


class LoginForm(FlaskForm):
    username = StrippedStringField("Username", validators=[DataRequired(), Length(max=50)])
    password = PasswordField("Password", validators=[DataRequired(), Length(max=256)])


class ActionForm(FlaskForm):
    """CSRF-only form for simple POST buttons."""


class ReasonForm(FlaskForm):
    reason = TextAreaField("Reason", validators=[DataRequired(), Length(max=500)])


class MemberForm(FlaskForm):
    full_name = StrippedStringField("Full name", validators=[DataRequired(), Length(max=120)])
    phone = StrippedStringField("Phone", validators=[DataRequired(), Length(max=25)])
    email = StrippedStringField("Email", validators=[Optional(), Email(), Length(max=255)])
    date_of_birth = DateField("Date of birth", validators=[Optional()])
    gender = SelectField("Gender", choices=[("", "—"), ("female", "Female"), ("male", "Male"), ("other", "Other")],
                         validators=[Optional()])
    address = TextAreaField("Address", validators=[Optional(), Length(max=500)])
    emergency_contact_name = StrippedStringField("Emergency contact name", validators=[Optional(), Length(max=120)])
    emergency_contact_phone = StrippedStringField("Emergency contact phone", validators=[Optional(), Length(max=25)])
    notes = TextAreaField("Internal notes (admin only)", validators=[Optional(), Length(max=2000)])

    def __init__(self, *args, member_id: int | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.member_id = member_id

    def validate_phone(self, field):
        phone = normalize_phone(field.data)
        if not phone or not _PHONE_RE.match(phone):
            raise ValidationError("Enter a valid phone number (7–15 digits, optional leading +).")
        q = select(Member.id).where(Member.phone == phone)
        if self.member_id:
            q = q.where(Member.id != self.member_id)
        if db.session.scalar(q):
            raise ValidationError("Another member already uses this phone number.")

    def validate_email(self, field):
        if not field.data:
            return
        q = select(Member.id).where(Member.email == field.data.lower())
        if self.member_id:
            q = q.where(Member.id != self.member_id)
        if db.session.scalar(q):
            raise ValidationError("Another member already uses this email.")

    def validate_emergency_contact_phone(self, field):
        if field.data and not _PHONE_RE.match(normalize_phone(field.data)):
            raise ValidationError("Enter a valid phone number.")

    def validate_date_of_birth(self, field):
        if field.data and field.data > datetime.now().date():
            raise ValidationError("Date of birth cannot be in the future.")

    def cleaned(self) -> dict:
        d = self
        return {
            "full_name": d.full_name.data, "phone": normalize_phone(d.phone.data),
            "email": d.email.data.lower() if d.email.data else None,
            "date_of_birth": d.date_of_birth.data, "gender": d.gender.data or None,
            "address": d.address.data or None, "emergency_contact_name": d.emergency_contact_name.data or None,
            "emergency_contact_phone": normalize_phone(d.emergency_contact_phone.data),
        }


class PlanForm(FlaskForm):
    name = StrippedStringField("Plan name", validators=[DataRequired(), Length(max=80)])
    description = TextAreaField("Description", validators=[Optional(), Length(max=1000)])
    duration_days = IntegerField("Duration (days)", validators=[DataRequired(), NumberRange(1, 3650)],
                                 default=30)
    price = DecimalField("Price", places=2, validators=[InputRequired(), NumberRange(min=Decimal("0"))],
                         render_kw={"step": "0.01", "min": "0"})
    currency = StrippedStringField("Currency", validators=[DataRequired(), Length(min=3, max=3)], default="PKR")
    access_start_time = TimeField("Access from (optional)", validators=[Optional()])
    access_end_time = TimeField("Access until (optional)", validators=[Optional()])
    equipment_ids = SelectMultipleField("Restricted equipment included in this plan", coerce=int,
                                        validators=[Optional()])
    is_active = BooleanField("Active (available for new memberships)", default=True)

    def validate_access_end_time(self, field):
        if bool(self.access_start_time.data) != bool(field.data):
            raise ValidationError("Set both access times, or leave both empty for 24-hour access.")


class MembershipForm(FlaskForm):
    plan_id = SelectField("Plan", coerce=int, validators=[DataRequired()])
    start_date = DateField("Start date", validators=[DataRequired()])
    discount = DecimalField("Discount", places=2, default=Decimal("0"), validators=[Optional(), NumberRange(min=0)],
                            render_kw={"step": "0.01", "min": "0"})
    notes = TextAreaField("Notes", validators=[Optional(), Length(max=500)])


class PaymentForm(FlaskForm):
    amount = DecimalField("Amount", places=2, validators=[DataRequired(), NumberRange(min=Decimal("0.01"))],
                          render_kw={"step": "0.01", "min": "0.01"})
    method = SelectField("Method", choices=[(m, m.replace("_", " ").title()) for m in PAYMENT_METHODS])
    reference_number = StrippedStringField("Reference no. (bank/card)", validators=[Optional(), Length(max=80)])
    notes = TextAreaField("Notes", validators=[Optional(), Length(max=500)])
    idempotency_key = HiddenField()


class CorrectionForm(FlaskForm):
    check_in_local = StringField("Check-in (gym local time)", validators=[Optional()],
                                 render_kw={"type": "datetime-local"})
    check_out_local = StringField("Check-out (gym local time)", validators=[DataRequired()],
                                  render_kw={"type": "datetime-local"})
    reason = TextAreaField("Reason for correction", validators=[DataRequired(), Length(max=500)])

    @staticmethod
    def parse(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.strptime(value, "%Y-%m-%dT%H:%M")
        except ValueError:
            return None

    def validate_check_in_local(self, field):
        if field.data and self.parse(field.data) is None:
            raise ValidationError("Invalid date/time.")

    def validate_check_out_local(self, field):
        if self.parse(field.data) is None:
            raise ValidationError("Invalid date/time.")


class EquipmentForm(FlaskForm):
    name = StrippedStringField("Name", validators=[DataRequired(), Length(max=120)])
    category = StrippedStringField("Category", validators=[DataRequired(), Length(max=60)])
    description = TextAreaField("Description", validators=[Optional(), Length(max=1000)])
    location = StrippedStringField("Location in gym", validators=[Optional(), Length(max=80)])
    status = SelectField("Status", choices=[(s, s.title()) for s in EQUIPMENT_STATUSES])
    is_restricted = BooleanField("Restricted (members need explicit permission or plan grant)")
    maintenance_notes = TextAreaField("Maintenance notes", validators=[Optional(), Length(max=1000)])
    is_active = BooleanField("Active", default=True)


class PermissionForm(FlaskForm):
    equipment_id = SelectField("Equipment", coerce=int, validators=[DataRequired()])
    effect = SelectField("Action", choices=[("allow", "Allow"), ("deny", "Restrict (deny)"),
                                            ("clear", "Remove override")])
    reason = StrippedStringField("Reason (required for restrict)", validators=[Optional(), Length(max=255)])


class UsageForm(FlaskForm):
    equipment_id = SelectField("Equipment", coerce=int, validators=[DataRequired()])
    note = StrippedStringField("Note (optional)", validators=[Optional(), Length(max=255)])


class StaffForm(FlaskForm):
    username = StrippedStringField("Username", validators=[DataRequired(), Length(min=3, max=50)])
    full_name = StrippedStringField("Full name", validators=[DataRequired(), Length(max=120)])
    role = SelectField("Role", choices=[(r, r.title()) for r in ROLES])
    password = PasswordField("Temporary password", validators=[DataRequired(), Length(max=256)])
    confirm = PasswordField("Confirm password", validators=[DataRequired(), EqualTo("password", "Passwords differ.")])

    def validate_username(self, field):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", field.data or ""):
            raise ValidationError("Use letters, digits, dot, dash or underscore only.")
        if db.session.scalar(select(User.id).where(User.username == field.data.lower())):
            raise ValidationError("Username already taken.")

    def validate_password(self, field):
        problem = password_problem(field.data)
        if problem:
            raise ValidationError(problem)


class ResetPasswordForm(FlaskForm):
    password = PasswordField("New password", validators=[DataRequired(), Length(max=256)])
    confirm = PasswordField("Confirm password", validators=[DataRequired(), EqualTo("password", "Passwords differ.")])

    def validate_password(self, field):
        problem = password_problem(field.data)
        if problem:
            raise ValidationError(problem)


class AttendanceDeviceForm(FlaskForm):
    name = StrippedStringField("Device name", validators=[DataRequired(), Length(max=100)])
    adapter = SelectField("Device mode", choices=[("simulator", "Simulator"), ("pyzk", "ZKTeco TCP/UDP")])
    host = StrippedStringField("Private LAN IP address", validators=[Optional(), Length(max=45)])
    port = IntegerField("Port", validators=[DataRequired(), NumberRange(min=1, max=65535)], default=4370)
    transport = SelectField("Transport", choices=[("tcp", "TCP"), ("udp", "UDP")])
    timeout_seconds = IntegerField(
        "Connection timeout (seconds)", validators=[DataRequired(), NumberRange(1, 30)], default=5
    )
    timezone = StrippedStringField("Device time zone", validators=[DataRequired(), Length(max=80)],
                                   default="Asia/Karachi")
    communication_key = PasswordField(
        "Device communication code (optional)", validators=[Optional(), Length(max=64)]
    )
    direction_field = SelectField("Punch direction field", choices=[("punch", "Punch"), ("status", "Status")])
    check_in_codes = StrippedStringField(
        "Check-in code(s), comma separated", validators=[DataRequired(), Length(max=100)], default="0"
    )
    check_out_codes = StrippedStringField(
        "Check-out code(s), comma separated", validators=[DataRequired(), Length(max=100)], default="1"
    )
    is_active = BooleanField("Active", default=True)

    def __init__(self, *args, device_id: int | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.device_id = device_id

    def validate_name(self, field):
        query = select(AttendanceDevice.id).where(AttendanceDevice.name == field.data)
        if self.device_id:
            query = query.where(AttendanceDevice.id != self.device_id)
        if db.session.scalar(query):
            raise ValidationError("A device already uses this name.")

    def validate_host(self, field):
        if self.adapter.data == "simulator":
            return
        if not field.data:
            raise ValidationError("Enter the device's private LAN IP address.")
        try:
            address = ip_address(field.data)
        except ValueError as exc:
            raise ValidationError("Enter an IP address, not a URL or hostname.") from exc
        if not (address.is_private or address.is_loopback) or address.is_multicast or address.is_unspecified:
            raise ValidationError("For safety, configure only a private LAN or loopback IP address.")

    def validate_timezone(self, field):
        try:
            ZoneInfo(field.data)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValidationError("Enter a valid IANA time zone, for example Asia/Karachi.") from exc

    def validate_communication_key(self, field):
        if field.data and not field.data.isdigit():
            raise ValidationError("This ZKTeco adapter expects a numeric communication code.")

    def validate_check_out_codes(self, field):
        in_codes = {part.strip() for part in (self.check_in_codes.data or "").split(",") if part.strip()}
        out_codes = {part.strip() for part in (field.data or "").split(",") if part.strip()}
        if not out_codes:
            raise ValidationError("Enter at least one check-out event code.")
        if in_codes & out_codes:
            raise ValidationError("Check-in and check-out codes must not overlap.")
        if any(not code.isdigit() for code in in_codes | out_codes):
            raise ValidationError("Event codes must be integers separated by commas.")


class DeviceMappingForm(FlaskForm):
    device_user_id = StrippedStringField("Device user ID", validators=[DataRequired(), Length(max=64)])
    member_id = SelectField("Gym member", coerce=int, validators=[DataRequired()])


class SimulatedPunchForm(FlaskForm):
    mapping_id = SelectField("Mapped device user", coerce=int, validators=[DataRequired()])
    direction = SelectField("Punch", choices=[("in", "Check in"), ("out", "Check out")])
