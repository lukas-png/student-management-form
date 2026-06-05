from __future__ import annotations

import base64
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from itsdangerous import BadData, URLSafeTimedSerializer

_SALT_AVAIL = "availability-v1"
_SALT_TUTOR = "tutor-v1"


class BadTokenError(Exception):
    pass


def sign_availability_token(student_id: str, round_id: int, *, secret_key: str) -> str:
    """Return a signed URL-safe token embedding student_id and round_id (no PII)."""
    s = URLSafeTimedSerializer(secret_key, salt=_SALT_AVAIL)
    return s.dumps({"student_id": student_id, "round_id": round_id})


def verify_availability_token(
    token: str,
    *,
    secret_key: str,
    max_age: int,
) -> tuple[str, int]:
    """Verify and decode an availability token.

    Returns (student_id, round_id).
    Raises BadToken on any tamper, expiry, or malformed payload — no details
    leaked to the caller, so the endpoint can return a generic 400.
    """
    s = URLSafeTimedSerializer(secret_key, salt=_SALT_AVAIL)
    try:
        data = s.loads(token, max_age=max_age)
        return str(data["student_id"]), int(data["round_id"])
    except (BadData, KeyError, TypeError, ValueError) as exc:
        raise BadTokenError from exc


def sign_tutor_token(slot_id: int, round_id: int, *, secret_key: str) -> str:
    """Return a signed token granting a tutor attendance-marking rights for a slot."""
    s = URLSafeTimedSerializer(secret_key, salt=_SALT_TUTOR)
    return s.dumps({"slot_id": slot_id, "round_id": round_id})


def verify_tutor_token(token: str, *, secret_key: str, max_age: int) -> tuple[int, int]:
    """Verify a tutor token. Returns (slot_id, round_id). Raises BadTokenError."""
    s = URLSafeTimedSerializer(secret_key, salt=_SALT_TUTOR)
    try:
        data = s.loads(token, max_age=max_age)
        return int(data["slot_id"]), int(data["round_id"])
    except (BadData, KeyError, TypeError, ValueError) as exc:
        raise BadTokenError from exc


_ph = PasswordHasher()


def verify_admin_password(password: str, password_hash: str) -> bool:
    """Return True if password matches the Argon2 hash. False on mismatch."""
    try:
        return _ph.verify(password_hash, password)
    except VerifyMismatchError:
        return False


def parse_basic_auth(authorization: str) -> tuple[str, str] | None:
    """Parse 'Basic <b64>' header. Returns (username, password) or None."""
    if not authorization.startswith("Basic "):
        return None
    try:
        decoded = base64.b64decode(authorization[6:]).decode()
        user, _, pw = decoded.partition(":")
        return user, pw
    except Exception:
        return None


# ---------------------------------------------------------------------------
# CSRF (double-submit cookie)
# ---------------------------------------------------------------------------


def generate_csrf_token() -> str:
    """Return a fresh random CSRF token for the double-submit cookie scheme."""
    return secrets.token_urlsafe(32)


def csrf_tokens_match(cookie_value: str | None, form_value: str | None) -> bool:
    """Constant-time compare of the cookie token and the form-submitted token."""
    if not cookie_value or not form_value:
        return False
    return secrets.compare_digest(cookie_value, form_value)
