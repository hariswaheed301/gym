from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from cryptography.fernet import Fernet, InvalidToken
from flask import current_app
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import AttendanceDevice, AttendanceDeviceMapping, AttendanceDevicePunch
from . import attendance as attendance_service
from . import audit
from .errors import ServiceError


def _cipher() -> Fernet:
    key = current_app.config.get("DEVICE_CREDENTIAL_KEY", "").strip()
    if not key:
        raise ServiceError("Set DEVICE_CREDENTIAL_KEY in .env before saving a device key.")
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        message = "DEVICE_CREDENTIAL_KEY is invalid; generate a Fernet key and restart Flask."
        raise ServiceError(message) from exc


def encrypt_credential(value: str) -> str:
    if not value:
        return ""
    return _cipher().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_credential(device: AttendanceDevice) -> str:
    if not device.credential_ciphertext:
        return "0"
    try:
        return _cipher().decrypt(device.credential_ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeEncodeError) as exc:
        raise ServiceError("Cannot decrypt this device key. Check DEVICE_CREDENTIAL_KEY.") from exc


def _event_value(event, name: str, default=None):
    return event.get(name, default) if isinstance(event, dict) else getattr(event, name, default)


def _event_time(value, timezone_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise ServiceError("Device punch has no valid timestamp.")
    if value.tzinfo is None:
        try:
            value = value.replace(tzinfo=ZoneInfo(timezone_name))
        except ZoneInfoNotFoundError as exc:
            raise ServiceError("Device time zone is not recognized.") from exc
    return value.astimezone(UTC)


def _event_key(event, user_id: str, punched_at: datetime, code: int) -> str:
    uid = _event_value(event, "uid")
    if uid not in (None, 0, "0", ""):
        raw = f"uid:{uid}"
    else:
        raw = f"{user_id}|{punched_at.isoformat()}|{code}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _event_sort_key(event, timezone_name: str) -> float:
    try:
        return _event_time(_event_value(event, "timestamp"), timezone_name).timestamp()
    except ServiceError:
        return float("inf")


def _direction(device: AttendanceDevice, code: int) -> str:
    in_codes = {part.strip() for part in device.check_in_codes.split(",") if part.strip()}
    out_codes = {part.strip() for part in device.check_out_codes.split(",") if part.strip()}
    value = str(code)
    if value in in_codes and value not in out_codes:
        return "in"
    if value in out_codes and value not in in_codes:
        return "out"
    return "unknown"


def import_punch(device: AttendanceDevice, event, actor_id: int | None = None) -> str:
    user_id = str(_event_value(event, "user_id", "")).strip()
    punched_at = _event_time(_event_value(event, "timestamp"), device.timezone)
    try:
        code = int(_event_value(event, device.direction_field))
    except (TypeError, ValueError):
        code = -1
    key = _event_key(event, user_id, punched_at, code)

    prior = db.session.scalar(select(AttendanceDevicePunch).where(
        AttendanceDevicePunch.device_id == device.id,
        AttendanceDevicePunch.event_key == key,
    ))
    if prior is not None:
        return "duplicate"

    punch = AttendanceDevicePunch(
        device_id=device.id,
        event_key=key,
        device_user_id=user_id[:64] or "unknown",
        punched_at=punched_at,
        direction=_direction(device, code),
        event_code=code,
        outcome="pending",
    )
    db.session.add(punch)
    try:
        db.session.flush()
    except IntegrityError:
        db.session.rollback()
        return "duplicate"

    mapping = db.session.scalar(select(AttendanceDeviceMapping).where(
        AttendanceDeviceMapping.device_id == device.id,
        AttendanceDeviceMapping.device_user_id == user_id,
        AttendanceDeviceMapping.is_active.is_(True),
    ))
    if mapping is None:
        punch.outcome, punch.message = "ignored", "No active member mapping for this device user ID."
    elif punch.direction == "unknown":
        punch.outcome, punch.message = "ignored", "Punch direction code is not configured unambiguously."
    else:
        try:
            if punch.direction == "in":
                attendance = attendance_service.check_in(
                    mapping.member_id, None, method="zkteco", now=punched_at
                )
            else:
                opened = attendance_service.get_open_session(mapping.member_id)
                if opened is None:
                    raise ServiceError("No open gym visit exists for this check-out punch.")
                attendance = attendance_service.check_out(None, attendance_id=opened.id, now=punched_at)
            punch.outcome = "imported"
            punch.attendance_id = attendance.id
            punch.message = "Punch imported."
            audit.log("device.punch_imported", "device_punch", punch.id,
                      {"device_id": device.id, "member_id": mapping.member_id,
                       "direction": punch.direction, "event_code": code}, actor_id)
        except ServiceError as exc:
            db.session.rollback()
            punch = db.session.scalar(select(AttendanceDevicePunch).where(
                AttendanceDevicePunch.device_id == device.id,
                AttendanceDevicePunch.event_key == key,
            ))
            if punch is None:
                punch = AttendanceDevicePunch(
                    device_id=device.id,
                    event_key=key,
                    device_user_id=user_id[:64] or "unknown",
                    punched_at=punched_at,
                    direction=_direction(device, code),
                    event_code=code,
                )
                db.session.add(punch)
            punch.outcome, punch.message = "rejected", str(exc)[:255]
    db.session.commit()
    return punch.outcome


def simulate_punch(device: AttendanceDevice, mapping: AttendanceDeviceMapping, direction: str,
                   actor_id: int) -> str:
    if device.adapter != "simulator":
        raise ServiceError("Simulated punches are available only for simulator devices.")
    code_text = device.check_in_codes if direction == "in" else device.check_out_codes
    codes = [part.strip() for part in code_text.split(",") if part.strip()]
    if len(codes) != 1:
        raise ServiceError("Simulator requires exactly one configured code for each direction.")
    event = {"uid": f"demo-{datetime.now(UTC).timestamp()}", "user_id": mapping.device_user_id,
             "timestamp": datetime.now(UTC), device.direction_field: int(codes[0])}
    return import_punch(device, event, actor_id)


def _pyzk_connection(device: AttendanceDevice):
    try:
        from zk import ZK
    except ImportError as exc:
        message = "Install the optional adapter with: pip install -r requirements-device.txt"
        raise ServiceError(message) from exc
    password = decrypt_credential(device)
    try:
        password_value = int(password or "0")
    except ValueError as exc:
        message = "ZKTeco communication key must be a numeric device code for this adapter."
        raise ServiceError(message) from exc
    return ZK(device.host, port=device.port, timeout=device.timeout_seconds, password=password_value,
              force_udp=device.transport == "udp", ommit_ping=True)


def test_connection(device: AttendanceDevice) -> str:
    if device.adapter == "simulator":
        return "Simulator ready"
    connection = None
    try:
        connection = _pyzk_connection(device).connect()
        try:
            label = connection.get_device_name() or "ZKTeco device"
        except Exception:  # noqa: BLE001
            label = "ZKTeco device"
        device.connection_state = "online"
        device.last_seen_at = datetime.now(UTC)
        device.last_error = None
        db.session.commit()
        return str(label)
    except Exception as exc:  # noqa: BLE001
        device.connection_state = "offline"
        device.last_error = str(exc)[:1000]
        db.session.commit()
        raise ServiceError(f"Device is unreachable or incompatible: {exc}") from exc
    finally:
        if connection is not None:
            try:
                connection.disconnect()
            except Exception:  # noqa: BLE001
                current_app.logger.debug("Error while disconnecting from attendance device", exc_info=True)


def sync_device(device: AttendanceDevice, actor_id: int) -> dict[str, int]:
    if not device.is_active:
        raise ServiceError("This device is inactive.")
    if device.adapter == "simulator":
        device.connection_state = "demo"
        device.last_sync_at = datetime.now(UTC)
        device.last_error = None
        db.session.commit()
        return {"seen": 0, "imported": 0, "ignored": 0, "rejected": 0, "duplicates": 0}

    connection = None
    counts = {"seen": 0, "imported": 0, "ignored": 0, "rejected": 0, "duplicates": 0}
    try:
        connection = _pyzk_connection(device).connect()
        events = connection.get_attendance() or []
        limit = current_app.config["DEVICE_SYNC_MAX_RECORDS"]
        if len(events) > limit:
            raise ServiceError(f"Device returned {len(events)} punches, above the safe limit of {limit}; "
                               "no punches were imported. Increase DEVICE_SYNC_MAX_RECORDS after review.")
        for event in sorted(events, key=lambda item: _event_sort_key(item, device.timezone)):
            counts["seen"] += 1
            try:
                result = import_punch(device, event, actor_id)
            except ServiceError:
                counts["rejected"] += 1
                continue
            key = "duplicates" if result == "duplicate" else result
            counts[key] = counts.get(key, 0) + 1
        device.connection_state = "online"
        device.last_seen_at = datetime.now(UTC)
        device.last_sync_at = datetime.now(UTC)
        device.last_error = None
        db.session.commit()
        return counts
    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        device.connection_state = "offline"
        device.last_error = str(exc)[:1000]
        device.last_sync_at = datetime.now(UTC)
        db.session.commit()
        raise ServiceError(f"Sync failed; existing attendance was kept: {exc}") from exc
    finally:
        if connection is not None:
            try:
                connection.disconnect()
            except Exception:  # noqa: BLE001
                current_app.logger.debug("Error while disconnecting from attendance device", exc_info=True)


def recent_punches(device_id: int, limit: int = 100) -> list[AttendanceDevicePunch]:
    return list(db.session.scalars(
        select(AttendanceDevicePunch).where(AttendanceDevicePunch.device_id == device_id)
        .order_by(AttendanceDevicePunch.received_at.desc()).limit(limit)
    ))