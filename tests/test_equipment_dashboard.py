from datetime import timedelta
from decimal import Decimal

import pytest

from app.extensions import db
from app.models import PlanEquipmentPermission
from app.services import attendance as att
from app.services import equipment as eq
from app.services import reports
from app.services.errors import ServiceError
from app.utils import local_today

from .helpers import eligible_member, make_equipment, make_member, make_membership, make_plan, make_user


def test_restricted_equipment_requires_grant_and_deny_overrides_everything():
    admin, m = make_user("admin"), make_member()
    plan = make_plan()
    make_membership(m, plan=plan, paid="5000", actor=admin)
    open_eq, rack = make_equipment(), make_equipment(restricted=True)
    assert eq.evaluate_equipment_access(m, open_eq) == (True, "open")
    assert eq.evaluate_equipment_access(m, rack) == (False, "restricted")
    db.session.add(PlanEquipmentPermission(plan_id=plan.id, equipment_id=rack.id))
    db.session.commit()
    assert eq.evaluate_equipment_access(m, rack) == (True, "plan")
    eq.set_member_permission(m.id, rack.id, "deny", "unsafe lifting form", admin.id)
    assert eq.evaluate_equipment_access(m, rack) == (False, "member_deny")  # explicit restriction beats plan grant
    eq.set_member_permission(m.id, rack.id, "clear", "", admin.id)
    assert eq.evaluate_equipment_access(m, rack) == (True, "plan")


def test_member_allow_and_deny_on_open_equipment_and_reason_required():
    admin, m = make_user("admin"), make_member()
    rack, treadmill = make_equipment(restricted=True), make_equipment()
    eq.set_member_permission(m.id, rack.id, "allow", "", admin.id)
    assert eq.evaluate_equipment_access(m, rack) == (True, "member_allow")
    with pytest.raises(ServiceError):
        eq.set_member_permission(m.id, treadmill.id, "deny", "", admin.id)
    eq.set_member_permission(m.id, treadmill.id, "deny", "injury", admin.id)
    assert eq.evaluate_equipment_access(m, treadmill) == (False, "member_deny")


def test_usage_recording_enforces_permission_and_status():
    staff, m = make_user(), make_member()
    rack, broken = make_equipment(restricted=True), make_equipment(status="maintenance")
    with pytest.raises(ServiceError):
        eq.record_usage(m.id, rack.id, staff.id)
    with pytest.raises(ServiceError):
        eq.record_usage(m.id, broken.id, staff.id)
    assert eq.record_usage(m.id, make_equipment().id, staff.id, "bench 1").id


def test_permission_changes_are_audited():
    from sqlalchemy import select

    from app.models import AuditLog
    admin, m, rack = make_user("admin"), make_member(), make_equipment(restricted=True)
    eq.set_member_permission(m.id, rack.id, "allow", "", admin.id)
    row = db.session.scalar(select(AuditLog).where(AuditLog.action == "equipment.permission_changed"))
    assert row.actor_id == admin.id and row.details["to"] == "allow"


def test_dashboard_metrics_reflect_real_data():
    staff = make_user()
    inside = eligible_member()
    att.check_in(inside.id, staff.id)
    done = eligible_member()
    a = att.check_in(done.id, staff.id)
    att.check_out(staff.id, attendance_id=a.id)
    expired = make_member()
    make_membership(expired, start=local_today() - timedelta(days=90), paid="5000")
    owing = make_member()
    make_membership(owing, plan=make_plan(price="800"), actor=staff)
    make_member(active=False)
    soon = make_member()
    make_membership(soon, start=local_today() - timedelta(days=27), paid="5000")  # expires in 2 days
    make_equipment(status="maintenance")
    m = reports.dashboard_metrics()
    assert m["total_members"] == 6 and m["active_members"] == 5
    assert m["inside_now"] == 1 and m["checkins_day"] == 2 and m["completed_day"] == 1
    assert m["expired"] == 1 and m["expiring_soon"] == 1
    assert m["dues_members"] == 1 and m["dues_total"] == Decimal("800.00")
    assert m["equipment"] == {"maintenance": 1}
    assert len(m["recent_payments"]) >= 1 and len(m["recent_attendance"]) == 2
