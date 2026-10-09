"""Process bootstrap: only what is needed before the database opens.

Everything else — brokers, Delta keys, risk, paper account — lives in the
database and is managed from the Settings page (see settings.py).

The system runs as four processes (feed, engine, web, worker) under
docker-compose, joined by ZeroMQ, with Redis as the Celery broker. Each endpoint has a bind address (the process that owns the socket)
and a connect address (everyone else). Both default to loopback: these
sockets carry commands and API keys and must never face a network.
"""

from __future__ import annotations

import hashlib
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
    # 6-digit PIN for dashboard security. Set AUTH_PIN or APP_PIN in .env, defaults to 242425.
    auth_pin: str = "242425"
    # Secret key for HMAC session token signing. Derived if empty.
    auth_secret: str = ""
    # Set by docker-compose for the web container: its port is published on the
    # host's 127.0.0.1 only, so binding 0.0.0.0 inside the container is not exposure.
    dashboard_loopback_only: bool = False
    # feed -> engine: market events
    zmq_feed_bind: str = "tcp://127.0.0.1:5555"
    zmq_feed_url: str = "tcp://127.0.0.1:5555"
    # engine -> web, feed: every event
    zmq_events_bind: str = "tcp://127.0.0.1:5556"
    zmq_events_url: str = "tcp://127.0.0.1:5556"
    # web -> engine: commands and queries
    zmq_rpc_bind: str = "tcp://127.0.0.1:5557"
    zmq_rpc_url: str = "tcp://127.0.0.1:5557"
    # workers -> engine: strategy results
    zmq_results_bind: str = "tcp://127.0.0.1:5558"
    zmq_results_url: str = "tcp://127.0.0.1:5558"
    celery_broker_url: str = "redis://127.0.0.1:6379/0"


ENV_FIELDS = {
    "zmq_feed_bind": "ZMQ_FEED_BIND",
    "zmq_feed_url": "ZMQ_FEED_URL",
    "zmq_events_bind": "ZMQ_EVENTS_BIND",
    "zmq_events_url": "ZMQ_EVENTS_URL",
    "zmq_rpc_bind": "ZMQ_RPC_BIND",
    "zmq_rpc_url": "ZMQ_RPC_URL",
    "zmq_results_bind": "ZMQ_RESULTS_BIND",
    "zmq_results_url": "ZMQ_RESULTS_URL",
    "celery_broker_url": "CELERY_BROKER_URL",
}


def load_config(environ: dict[str, str] | None = None) -> Config:
    if environ is None:
        load_dotenv()
        environ = dict(os.environ)
    e = environ

    api_token = e.get("API_TOKEN", "")
    db_path = e.get("DB_PATH", "data/tradebuddy.db")
    # SECURITY: Read PIN from AUTH_PIN or APP_PIN or default to "242425"
    auth_pin = e.get("AUTH_PIN", e.get("APP_PIN", "242425"))
    auth_secret = e.get("AUTH_SECRET", "")
    if not auth_secret:
        # PERF: Derive a deterministic secret across processes if not explicitly provided
        derived = hashlib.sha256(f"tb_auth_seed:{api_token}:{db_path}".encode()).hexdigest()
        auth_secret = derived

    return Config(
        db_path=db_path,
        host=e.get("HOST", "127.0.0.1"),
        port=int(e.get("PORT", "8080")),
        api_token=api_token,
        auth_pin=auth_pin,
        auth_secret=auth_secret,
        dashboard_loopback_only=e.get("DASHBOARD_LOOPBACK_ONLY", "").strip().lower() in ("1", "true", "yes"),
        **{field: e[name] for field, name in ENV_FIELDS.items() if e.get(name)},
    )


def require_safe_bind(cfg: Config) -> None:
    """For processes that serve the dashboard: off loopback, every change needs a token."""
    if cfg.host not in LOOPBACK and not cfg.api_token and not cfg.dashboard_loopback_only:
        raise RuntimeError(f"HOST={cfg.host} is reachable from the network: set API_TOKEN")
