"""The HTTP surface: private by default, the PIN, and no secret ever returned."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.api.routes import auth as auth_routes
from app.config import Config
from app.core import security
from app.main import PUBLIC_PATHS, create_app

PIN = "135790"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    cfg = Config(db_path=str(tmp_path / "api.db"), engine_enabled=False, auth_pin=PIN)
    monkeypatch.setattr(security, "get_config", lambda: cfg)
    monkeypatch.setattr(auth_routes, "get_config", lambda: cfg)
    auth_routes._failures.clear()
    with TestClient(create_app(cfg)) as c:
        yield c


def login(client: TestClient) -> None:
    assert client.post("/api/auth/login", json={"pin": PIN}).status_code == 200


def test_every_route_is_private_unless_listed(client: TestClient) -> None:
    for route in client.app.routes:  # type: ignore[attr-defined]
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", None) or set()
        if not path.startswith("/api") or path in PUBLIC_PATHS or "{" in path or "GET" not in methods:
            continue
        assert client.get(path).status_code == 401, path
    assert client.get("/api/health").status_code == 200


def test_the_pin_buys_a_session_and_wrong_pins_are_limited(client: TestClient) -> None:
    for _ in range(5):
        assert client.post("/api/auth/login", json={"pin": "000000"}).status_code == 409
    assert client.post("/api/auth/login", json={"pin": PIN}).status_code == 429  # locked out, even with the right PIN
    auth_routes._failures.clear()
    login(client)
    assert client.get("/api/system/status").status_code == 200
    client.post("/api/auth/logout")
    client.cookies.clear()
    assert client.get("/api/system/status").status_code == 401


def test_the_live_stream_needs_the_session(client: TestClient) -> None:
    with pytest.raises(WebSocketDisconnect), client.websocket_connect("/ws") as ws:
        ws.receive_json()
    login(client)
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "Hello"


def test_settings_never_return_a_secret(client: TestClient) -> None:
    login(client)
    r = client.patch("/api/settings", json={"changes": {"delta_api_key": "key-abcdefgh1234", "delta_api_secret": "very-secret", "telegram_bot_token": "tg-secret"}})
    assert r.status_code == 200
    body = r.text + client.get("/api/settings").text
    assert "very-secret" not in body and "tg-secret" not in body and "key-abcdefgh1234" not in body
    bad = client.patch("/api/settings", json={"changes": {"risk_per_trade_pct": 99}})
    assert bad.status_code == 422 and "risk_per_trade_pct" in bad.json()["error"]


def test_going_live_needs_the_phrase_over_http(client: TestClient) -> None:
    login(client)
    r = client.patch("/api/settings", json={"changes": {"delta_active": True, "delta_env": "live"}})
    assert r.status_code == 422 and "confirmation phrase" in r.json()["error"]
