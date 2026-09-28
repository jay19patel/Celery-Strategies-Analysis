"""Process bootstrap: only what is needed before the database opens.

Everything else — broker, Delta keys, risk, paper account — lives in the
database and is managed from the Settings page (see settings.py).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

LOOPBACK = {"127.0.0.1", "localhost", "::1"}


@dataclass(frozen=True)
class Config:
    db_path: str
    host: str
    port: int
    api_token: str


def load_config(environ: dict[str, str] | None = None) -> Config:
    if environ is None:
        load_dotenv()
        environ = dict(os.environ)
    e = environ

    host = e.get("HOST", "127.0.0.1")
    api_token = e.get("API_TOKEN", "")
    if host not in LOOPBACK and not api_token:
        raise RuntimeError(f"HOST={host} is reachable from the network: set API_TOKEN")

    return Config(
        db_path=e.get("DB_PATH", "data/tradebuddy.db"),
        host=host,
        port=int(e.get("PORT", "8080")),
        api_token=api_token,
    )
