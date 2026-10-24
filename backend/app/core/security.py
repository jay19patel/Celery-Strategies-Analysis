"""The dashboard PIN and the session cookie it buys.

The PIN starts as AUTH_PIN from the environment. The first successful login stores it as an Argon2
hash, and a PIN changed on the Settings page only ever exists as that hash. Sessions are
`<expiry>.<hmac>` signed with a random key the process generates once and keeps in the database,
so a restart does not log anyone out and nothing about the key is guessable from the code.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.config import DEFAULT_AUTH_PIN, get_config
from app.core.db import kv_get, kv_set

COOKIE = "tb_session"
_hasher = PasswordHasher()


def _secret() -> bytes:
    key = kv_get("auth:secret")
    if not key:
        key = secrets.token_hex(32)
        kv_set("auth:secret", key)
    return bytes.fromhex(key)


def verify_pin(pin: str) -> bool:
    pin = str(pin).strip()
    stored = kv_get("auth:pin_hash")
    if stored:
        try:
            return _hasher.verify(stored, pin)
        except (VerificationError, InvalidHashError):
            return False
    if hmac.compare_digest(pin, get_config().auth_pin):
        kv_set("auth:pin_hash", _hasher.hash(pin))
        return True
    return False


def change_pin(current: str, new: str) -> None:
    from app.errors import Refused

    if not verify_pin(current):
        raise Refused("the current PIN is incorrect")
    new = str(new).strip()
    if len(new) != 6 or not new.isdigit():
        raise Refused("the new PIN must be exactly 6 digits")
    kv_set("auth:pin_hash", _hasher.hash(new))


def using_default_pin() -> bool:
    stored = kv_get("auth:pin_hash")
    if stored:
        try:
            return _hasher.verify(stored, DEFAULT_AUTH_PIN)
        except (VerificationError, InvalidHashError):
            return False
    return get_config().auth_pin == DEFAULT_AUTH_PIN


def issue_session(now: float | None = None) -> str:
    expires = int((now or time.time()) + get_config().session_days * 86_400)
    sig = hmac.new(_secret(), str(expires).encode(), hashlib.sha256).hexdigest()
    return f"{expires}.{sig}"


def valid_session(token: str | None, now: float | None = None) -> bool:
    if not token or "." not in token:
        return False
    expires, sig = token.split(".", 1)
    if not expires.isdigit() or int(expires) < (now or time.time()):
        return False
    expected = hmac.new(_secret(), expires.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expected)
