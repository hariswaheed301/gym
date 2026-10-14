from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from flask import has_request_context, request

from ..extensions import db
from ..models import AuditLog


def _jsonable(value):
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set):
        return [_jsonable(v) for v in value]
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if value is None or isinstance(value, bool | int | float | str):
        return value
    return str(value)


def log(action: str, entity_type: str, entity_id: int | None = None, details: dict | None = None,
        actor_id: int | None = None) -> None:
    """Add an audit row to the *current transaction* (committed together with the change itself)."""
    ip = request.remote_addr if has_request_context() else None
    db.session.add(
        AuditLog(
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            details=_jsonable(details or {}),
            actor_id=actor_id,
            ip_address=ip,
        )
    )
