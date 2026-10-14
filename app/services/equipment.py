"""Equipment permissions.

Precedence (first match wins):
  1. member-specific DENY            -> denied   (an explicit restriction can never be overridden)
  2. member-specific ALLOW           -> allowed
  3. equipment not restricted        -> allowed
  4. member's current plan grants it -> allowed
  5. otherwise                       -> denied (restricted equipment, no grant)
Permissions are independent of equipment *status*; usage additionally requires status 'operational'."""

from __future__ import annotations

from sqlalchemy import select

from ..extensions import db
from ..models import (Equipment, EquipmentPermission, EquipmentUsage, Member, PlanEquipmentPermission)
from ..utils import local_today
from . import audit
from .errors import ServiceError
from .members import current_memberships

SOURCE_LABELS = {
    "member_deny": "Restricted for this member",
    "member_allow": "Granted to this member",
    "open": "Open to all members",
    "plan": "Included in plan",
    "restricted": "Restricted equipment (no grant)",
}


def _context(member: Member):
    overrides = {p.equipment_id: p.effect for p in db.session.scalars(
        select(EquipmentPermission).where(EquipmentPermission.member_id == member.id))}
    cur = current_memberships([member.id], local_today()).get(member.id)
    plan_grants = set()
    if cur is not None:
        plan_grants = set(db.session.scalars(
            select(PlanEquipmentPermission.equipment_id).where(PlanEquipmentPermission.plan_id == cur.plan_id)))
    return overrides, plan_grants


def _decide(eq: Equipment, overrides: dict, plan_grants: set) -> tuple[bool, str]:
    effect = overrides.get(eq.id)
    if effect == "deny":
        return False, "member_deny"
    if effect == "allow":
        return True, "member_allow"
    if not eq.is_restricted:
        return True, "open"
    if eq.id in plan_grants:
        return True, "plan"
    return False, "restricted"


def evaluate_equipment_access(member: Member, equipment: Equipment) -> tuple[bool, str]:
    overrides, plan_grants = _context(member)
    return _decide(equipment, overrides, plan_grants)


def member_equipment_matrix(member: Member) -> list[dict]:
    overrides, plan_grants = _context(member)
    rows = []
    for eq in db.session.scalars(select(Equipment).where(Equipment.is_active.is_(True)).order_by(Equipment.category, Equipment.name)):
        allowed, source = _decide(eq, overrides, plan_grants)
        rows.append({"equipment": eq, "allowed": allowed, "source": source,
                     "label": SOURCE_LABELS[source], "override": overrides.get(eq.id)})
    return rows


def set_member_permission(member_id: int, equipment_id: int, effect: str, reason: str, actor_id: int) -> None:
    """effect: 'allow' | 'deny' | 'clear' (remove override)."""
    if effect not in ("allow", "deny", "clear"):
        raise ServiceError("Invalid permission.")
    member, eq = db.session.get(Member, member_id), db.session.get(Equipment, equipment_id)
    if member is None or eq is None:
        raise ServiceError("Member or equipment not found.")
    if effect == "deny" and not (reason or "").strip():
        raise ServiceError("A reason is required when restricting equipment.")
    row = db.session.scalar(select(EquipmentPermission).where(
        EquipmentPermission.member_id == member_id, EquipmentPermission.equipment_id == equipment_id))
    old = row.effect if row else None
    if effect == "clear":
        if row is not None:
            db.session.delete(row)
    elif row is None:
        db.session.add(EquipmentPermission(member_id=member_id, equipment_id=equipment_id, effect=effect,
                                           reason=reason or None, granted_by_id=actor_id))
    else:
        row.effect, row.reason, row.granted_by_id = effect, reason or None, actor_id
    audit.log("equipment.permission_changed", "member", member_id,
              {"equipment_id": equipment_id, "equipment": eq.name, "from": old, "to": None if effect == "clear" else effect,
               "reason": reason}, actor_id)
    db.session.commit()


def set_plan_equipment(plan_id: int, equipment_ids: list[int], actor_id: int) -> None:
    existing = set(db.session.scalars(
        select(PlanEquipmentPermission.equipment_id).where(PlanEquipmentPermission.plan_id == plan_id)))
    wanted = set(equipment_ids)
    for eid in existing - wanted:
        db.session.delete(db.session.get(PlanEquipmentPermission, (plan_id, eid)))
    for eid in wanted - existing:
        db.session.add(PlanEquipmentPermission(plan_id=plan_id, equipment_id=eid))
    if existing != wanted:
        audit.log("equipment.plan_permissions_changed", "plan", plan_id,
                  {"added": sorted(wanted - existing), "removed": sorted(existing - wanted)}, actor_id)


def record_usage(member_id: int, equipment_id: int, actor_id: int, note: str | None = None) -> EquipmentUsage:
    """Manually recorded by staff (the system cannot detect physical usage). Enforces permissions."""
    member, eq = db.session.get(Member, member_id), db.session.get(Equipment, equipment_id)
    if member is None or eq is None or not eq.is_active:
        raise ServiceError("Member or equipment not found.")
    allowed, source = evaluate_equipment_access(member, eq)
    if not allowed:
        raise ServiceError(f"{member.full_name} is not permitted to use {eq.name} ({SOURCE_LABELS[source].lower()}).")
    if eq.status != "operational":
        raise ServiceError(f"{eq.name} is currently {eq.status}.")
    usage = EquipmentUsage(member_id=member_id, equipment_id=equipment_id, recorded_by_id=actor_id,
                           note=(note or "")[:255] or None)
    db.session.add(usage)
    db.session.commit()
    return usage
