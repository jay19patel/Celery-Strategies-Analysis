"""Authentication and session management for TradeBuddy.

Provides secure 6-digit PIN verification using industry-standard Argon2id,
HMAC-SHA256 signed session tokens with 7-day expiration, and brute-force protection.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import time
from typing import Final

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

logger = logging.getLogger(__name__)

# SECURITY: Session cookie settings and PIN constraints
SESSION_COOKIE_NAME: Final[str] = "tb_session"
SESSION_MAX_AGE_SECONDS: Final[int] = 7 * 24 * 3600  # 7 days (604,800 seconds)
PIN_LENGTH: Final[int] = 6
MAX_FAILED_ATTEMPTS: Final[int] = 5
LOCKOUT_SECONDS: Final[int] = 60

# SECURITY: Argon2id password hasher with tuned parameters for interactive login
_password_hasher: PasswordHasher = PasswordHasher(
    time_cost=2,
    memory_cost=19456,  # 19 MiB memory cost
    parallelism=1,
    hash_len=32,
)


class AuthError(Exception):
    """Base exception for authentication failures."""


class InvalidPinError(AuthError):
    """Raised when an invalid PIN format or value is provided."""


class RateLimitExceededError(AuthError):
    """Raised when consecutive failed authentication attempts exceed the limit."""


def hash_pin(pin: str) -> str:
    """Hash a 6-digit PIN using industry-standard Argon2id."""
    pin_clean = pin.strip()
    if len(pin_clean) != PIN_LENGTH or not pin_clean.isdigit():
        raise InvalidPinError(f"PIN must be exactly {PIN_LENGTH} digits")

    # SECURITY: Argon2id is memory-hard and GPU-resistant
    return _password_hasher.hash(pin_clean)


def verify_pin(pin: str, stored_hash: str) -> bool:
    """Verify a PIN against a stored Argon2id hash using constant-time verification."""
    pin_clean = pin.strip()
    if len(pin_clean) != PIN_LENGTH or not pin_clean.isdigit():
        return False
    if not stored_hash:
        return False

    # SECURITY: Verify with Argon2id
    if stored_hash.startswith("$argon2"):
        try:
            return bool(_password_hasher.verify(stored_hash, pin_clean))
        except (VerifyMismatchError, InvalidHashError):
            return False

    # Fallback compatibility for legacy PBKDF2 hashes
    if ":" in stored_hash:
        salt_hex, expected_hex = stored_hash.split(":", 1)
        try:
            salt_bytes = bytes.fromhex(salt_hex)
            actual = hashlib.pbkdf2_hmac("sha256", pin_clean.encode("utf-8"), salt_bytes, 100_000).hex()
            return hmac.compare_digest(actual, expected_hex)
        except (ValueError, TypeError):
            return False

    return False


def create_session_token(secret_key: str, max_age_seconds: int = SESSION_MAX_AGE_SECONDS) -> str:
    """Create an HMAC-SHA256 signed session token containing expiration timestamp and random nonce.

    Token format: <expires_at>:<nonce>:<signature_hex>
    """
    if not secret_key:
        raise AuthError("secret_key cannot be empty")

    expires_at = int(time.time()) + max_age_seconds
    nonce = secrets.token_hex(16)
    payload = f"{expires_at}:{nonce}"
    # SECURITY: HMAC-SHA256 signature ensures tamper-proof session tokens
    sig = hmac.new(secret_key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}:{sig}"


def validate_session_token(token: str, secret_key: str) -> bool:
    """Validate a session token's cryptographic signature and check expiration.

    # PERF: Fast constant-time check with zero database queries.
    """
    if not token or not secret_key:
        return False

    parts = token.split(":")
    if len(parts) != 3:
        return False

    expires_at_str, nonce, signature = parts
    payload = f"{expires_at_str}:{nonce}"
    expected_sig = hmac.new(secret_key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()

    # SECURITY: Compare signatures in constant time
    if not hmac.compare_digest(signature, expected_sig):
        return False

    try:
        expires_at = int(expires_at_str)
    except ValueError:
        return False

    return expires_at > int(time.time())


class BruteForceGuard:
    """In-memory rate limiter to protect 6-digit PINs against automated brute-force attempts."""

    def __init__(self, max_attempts: int = MAX_FAILED_ATTEMPTS, lockout_seconds: int = LOCKOUT_SECONDS) -> None:
        self.max_attempts = max_attempts
        self.lockout_seconds = lockout_seconds
        self._failures: dict[str, list[float]] = {}
        self._lockouts: dict[str, float] = {}

    def is_locked(self, client_id: str) -> tuple[bool, int]:
        """Check if client_id is currently locked out.

        Returns (is_locked, remaining_lockout_seconds).
        """
        now = time.time()
        locked_until = self._lockouts.get(client_id, 0.0)
        if now < locked_until:
            return True, int(locked_until - now)
        return False, 0

    def record_failure(self, client_id: str) -> tuple[int, int]:
        """Record a failed authentication attempt.

        Returns (remaining_attempts, lockout_seconds).
        """
        now = time.time()
        # Keep failures within the last 5 minutes (300 seconds)
        attempts = [t for t in self._failures.get(client_id, []) if now - t < 300]
        attempts.append(now)
        self._failures[client_id] = attempts

        if len(attempts) >= self.max_attempts:
            # SECURITY: Enforce lockout after maximum failures exceeded
            self._lockouts[client_id] = now + self.lockout_seconds
            self._failures[client_id] = []
            logger.warning("auth_lockout_applied client=%s duration=%ss", client_id, self.lockout_seconds)
            return 0, self.lockout_seconds

        remaining = self.max_attempts - len(attempts)
        return remaining, 0

    def record_success(self, client_id: str) -> None:
        """Clear failed attempts upon successful login."""
        self._failures.pop(client_id, None)
        self._lockouts.pop(client_id, None)
