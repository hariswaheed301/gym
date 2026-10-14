from __future__ import annotations

from functools import wraps
from urllib.parse import urlparse

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from flask import abort
from flask_login import current_user, login_required

_ph = PasswordHasher()  # argon2-cffi default type is Argon2id


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _ph.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _ph.check_needs_rehash(password_hash)


# Used to spend equal time when a username does not exist (limits user enumeration by timing).
DUMMY_HASH = _ph.hash("not-a-real-password")


def password_problem(password: str) -> str | None:
    if len(password) < 10:
        return "Password must be at least 10 characters."
    if password.lower() == password or password.upper() == password or not any(c.isdigit() for c in password):
        return "Password must mix upper and lower case letters and include a digit."
    return None


def roles_required(*roles: str):
    """Server-side authorization: authenticated AND role in `roles`, else 403."""

    def decorator(view):
        @wraps(view)
        @login_required
        def wrapper(*args, **kwargs):
            if current_user.role not in roles:
                abort(403)
            return view(*args, **kwargs)

        return wrapper

    return decorator


admin_required = roles_required("admin")
staff_required = roles_required("admin", "receptionist")


def safe_next_url(target: str | None) -> str | None:
    """Only allow same-site relative redirects (prevents open redirects)."""
    if not target:
        return None
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc or not target.startswith("/") or target.startswith("//"):
        return None
    if "\\" in target:
        return None
    return target
