"""Process bootstrap: only what is needed before the database opens.

Everything about trading — brokers, Delta keys, risk, the auto-trader, the LLM models,
notifications — is a runtime setting stored in the database and edited on the Settings page
(`app.settings`). LLM provider keys are the exception: they stay in the environment
(MISTRAL_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY, GEMINI_API_KEY, ...) where LiteLLM reads
them, so nothing can read them back out of the database or an endpoint.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
ENV_FILES = (BACKEND_DIR.parent / ".env", BACKEND_DIR / ".env")  # later files win
DEFAULT_AUTH_PIN = "242425"


class Config(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILES, extra="ignore")

    db_path: str = str(BACKEND_DIR / "data" / "tradebuddy.db")
    host: str = "127.0.0.1"
    port: int = 8000
    log_level: str = "INFO"
    # The dashboard PIN until it is changed from the Settings page (then it is an Argon2 hash in the
    # database and this value stops mattering).
    auth_pin: str = DEFAULT_AUTH_PIN
    session_days: int = 7
    cors_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]
    # Start the market feed and the scheduler. Tests run the API without them.
    engine_enabled: bool = True


@lru_cache
def get_config() -> Config:
    # LiteLLM reads provider keys from os.environ; pydantic-settings does not export .env there.
    for path in ENV_FILES:
        load_dotenv(path, override=False)
    return Config()
