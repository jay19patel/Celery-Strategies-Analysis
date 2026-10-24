"""Authentication service — PIN-based auth with HMAC session tokens."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from app.application.base_service import BaseService
from app.domain.exceptions import AuthenticationError
from app.infrastructure.redis.cache import RedisCache

_hasher = PasswordHasher()


class AuthService(BaseService):
    """PIN verification, session tokens, and PIN change flow."""

    def __init__(self, auth_pin: str, auth_secret: str, cache: RedisCache) -> None:
        super().__init__()
        self._default_pin = auth_pin
        self._secret = auth_secret
        self._cache = cache

    async def verify_pin(self, pin: str, stored_hash: str | None = None) -> str:
        """Verify a PIN and return a session token.

        Raises:
            AuthenticationError: If the PIN is wrong.
        """
        is_valid = False

        if stored_hash:
            try:
                _hasher.verify(stored_hash, pin)
                is_valid = True
            except VerifyMismatchError:
                pass
        else:
            is_valid = hmac.compare_digest(pin, self._default_pin)

        if not is_valid:
            self._warn("auth_failed")
            raise AuthenticationError("Invalid PIN")

        token = self._create_token()
        await self._cache.set_session(token, {
            "authenticated": True,
            "created_at": time.time(),
        })
        self._info("auth_success")
        return token

    async def validate_token(self, token: str) -> bool:
        """Check if a session token is still valid."""
        if not token:
            return False
        session = await self._cache.get_session(token)
        return session is not None and session.get("authenticated", False)

    async def logout(self, token: str) -> None:
        """Invalidate a session token."""
        await self._cache.delete_session(token)
        self._info("auth_logout")

    async def change_pin(self, current_pin: str, new_pin: str, stored_hash: str | None = None) -> str:
        """Change the PIN. Returns the new Argon2 hash to store.

        Raises:
            AuthenticationError: If the current PIN is wrong.
        """
        await self.verify_pin(current_pin, stored_hash)
        new_hash = _hasher.hash(new_pin)
        self._info("pin_changed")
        return new_hash

    def _create_token(self) -> str:
        """Generate a cryptographically secure, HMAC-signed session token."""
        raw = secrets.token_hex(32)
        # SECURITY: HMAC-sign so tokens cannot be forged
        signature = hmac.new(
            self._secret.encode(),
            raw.encode(),
            hashlib.sha256,
        ).hexdigest()
        return f"{raw}.{signature}"
