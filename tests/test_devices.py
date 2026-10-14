from datetime import UTC, datetime

from cryptography.fernet import Fernet

from app.extensions import db
from app.models import Attendance, AttendanceDevice, AttendanceDeviceMapping, AttendanceDevicePunch
from app.services import attendance, devices
from app.utils import now_utc

from .helpers import eligible_member, login, make_user


def _simulator(member):
    device = AttendanceDevice(name="Demo terminal", adapter="simulator", timezone="Asia/Karachi")
    db.session.add(device)
    db.session.flush()
    mapping = AttendanceDeviceMapping(device_id=device.id, device_user_id="T-100", member_id=member.id)
    db.session.add(mapping)
    db.session.commit()
    return device, mapping


def test_simulated_in_and_out_punches_create_visit_and_dedupe():
    member = eligible_member()
    device, mapping = _simulator(member)

    assert devices.simulate_punch(device, mapping, "in", actor_id=None) == "imported"
    opened = attendance.get_open_session(member.id)
    assert opened is not None and opened.check_in_method == "zkteco"

    assert devices.simulate_punch(device, mapping, "out", actor_id=None) == "imported"
    assert attendance.get_open_session(member.id) is None
    assert db.session.query(Attendance).filter_by(member_id=member.id).count() == 1
    imported = db.session.query(AttendanceDevicePunch).filter_by(
        device_id=device.id, outcome="imported"
    ).count()
    assert imported == 2


def test_unknown_direction_is_logged_without_creating_attendance():
    member = eligible_member()
    device, _mapping = _simulator(member)
    event = {"uid": 804, "user_id": "T-100", "timestamp": now_utc(), "punch": 3}

    assert devices.import_punch(device, event) == "ignored"
    assert db.session.query(Attendance).filter_by(member_id=member.id).count() == 0
    punch = db.session.query(AttendanceDevicePunch).filter_by(device_id=device.id).one()
    assert punch.direction == "unknown"


def test_hardware_event_maps_member_and_duplicate_event_is_ignored():
    member = eligible_member()
    device, _mapping = _simulator(member)
    device.adapter = "pyzk"
    event_time = datetime.now(UTC)
    event = {"uid": 987, "user_id": "T-100", "timestamp": event_time, "punch": 0}

    assert devices.import_punch(device, event) == "imported"
    assert devices.import_punch(device, event) == "duplicate"
    assert db.session.query(Attendance).filter_by(member_id=member.id).count() == 1


def test_device_credential_is_encrypted_and_round_trips(app):
    app.config["DEVICE_CREDENTIAL_KEY"] = Fernet.generate_key().decode("ascii")
    device = AttendanceDevice(name="Encrypted terminal", adapter="pyzk")

    device.credential_ciphertext = devices.encrypt_credential("12345")

    assert device.credential_ciphertext != "12345"
    assert devices.decrypt_credential(device) == "12345"


def test_device_pages_require_admin_and_render_for_admin(client):
    assert client.get("/devices/").status_code == 302
    member = eligible_member()
    device, _mapping = _simulator(member)
    admin = make_user("admin")
    login(client, admin)

    listing = client.get("/devices/")
    detail = client.get(f"/devices/{device.id}")

    assert listing.status_code == 200 and b"Attendance devices" in listing.data
    assert detail.status_code == 200 and b"Device user mapping" in detail.data