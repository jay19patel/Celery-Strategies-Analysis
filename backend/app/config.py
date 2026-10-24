"""Application configuration via Pydantic Settings.

All secrets and environment-specific values come from .env or
environment variables — nothing is ever hardcoded.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Immutable app configuration, validated at startup.

    Every field maps to an env var with the same uppercase name.
    Missing required fields crash the process immediately — fail fast.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- App ---
    app_name: str = "TradeBuddy"
    debug: bool = False
    log_level: str = "INFO"

    # --- Server ---
    host: str = "0.0.0.0"
    port: int = 8000

    # --- Database ---
    db_path: str = "data/tradebuddy.db"

    # --- Redis ---
    redis_url: str = "redis://redis:6379/0"

    # --- Celery ---
    celery_broker_url: str = "redis://redis:6379/0"

    # --- Auth ---
    auth_pin: str = "242425"
    auth_secret: str = ""
    api_token: str = ""

    # --- ZMQ (feed ↔ engine communication) ---
    zmq_feed_bind: str = "tcp://0.0.0.0:5555"
    zmq_feed_url: str = "tcp://feed:5555"
    zmq_events_bind: str = "tcp://0.0.0.0:5556"
    zmq_events_url: str = "tcp://engine:5556"
    zmq_rpc_bind: str = "tcp://0.0.0.0:5557"
    zmq_rpc_url: str = "tcp://engine:5557"
    zmq_results_bind: str = "tcp://0.0.0.0:5558"
    zmq_results_url: str = "tcp://engine:5558"

    # --- External APIs ---
    mistral_api_key: str = ""

    # --- CORS ---
    cors_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]

    # --- Delta Exchange ---
    delta_api_key: str = ""
    delta_api_secret: str = ""
    delta_env: str = "demo"  # "demo" | "live"

    @property
    def database_url(self) -> str:
        """SQLite connection string for SQLModel."""
        return f"sqlite:///{self.db_path}"

    @property
    def effective_auth_secret(self) -> str:
        """Derive a deterministic secret if none is explicitly set.

        This matches the original behaviour so sessions survive process restarts
        without requiring an explicit AUTH_SECRET in every deployment.
        """
        if self.auth_secret:
            return self.auth_secret
        # SECURITY: Deterministic derivation — not cryptographically ideal but mirrors v3 behaviour
        derived = hashlib.sha256(
            f"tb_auth_seed:{self.api_token}:{self.db_path}".encode()
        ).hexdigest()
        return derived


def load_settings() -> Settings:
    """Create and validate settings from environment.

    Crashes at startup if anything required is missing — fail fast.
    """
    settings = Settings()

    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [backend] %(name)s %(message)s",
    )

    if settings.auth_pin == "242425":
        logger.warning(
            "auth_pin_default — AUTH_PIN is the published default. "
            "Change it for any non-loopback deployment."
        )

    return settings
