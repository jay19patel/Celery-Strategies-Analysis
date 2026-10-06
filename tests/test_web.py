"""Pages, the settings API and broker routes. The lifespan (and so the stream) stays off."""

import dataclasses
import re

import pytest
from fastapi.testclient import TestClient

from tradebuddy.app import PAGES, local_app
from tradebuddy.events import Tick
from tradebuddy.settings import LIVE_CONFIRM_PHRASE
from tradebuddy.system import System

from .conftest import IdleStream
from .test_pipeline import AlwaysBuy


def make(cfg, exchange, token=""):
    system = System(dataclasses.replace(cfg, api_token=token), strategies=[AlwaysBuy()], stream_factory=IdleStream, client_factory=exchange.client)
    return system, TestClient(local_app(system))


@pytest.mark.parametrize(("page_id", "path", "title"), [(p[0], p[1], p[3]) for p in PAGES])
def test_every_page_renders(cfg, exchange, page_id, path, title):
    _, client = make(cfg, exchange)
    res = client.get(path)
    assert res.status_code == 200
    assert f"<title>{title} · TradeBuddy</title>" in res.text
    assert 'class="nav-link active' in res.text


def test_static_assets_are_served(cfg, exchange):
    _, client = make(cfg, exchange)
    assert client.get("/static/js/app.js").status_code == 200
    assert client.get("/static/css/app.css").status_code == 200


def test_settings_api_masks_secrets(cfg, exchange):
    _, client = make(cfg, exchange)
    res = client.put("/api/settings", json={"changes": {"delta_api_key": "abcdefghijKEY9", "delta_api_secret": "topsecret"}})
    assert res.status_code == 200
    for body in (res.text, client.get("/api/settings").text):
        assert "topsecret" not in body and "abcdefghij" not in body


def test_settings_api_refuses_live_without_phrase(cfg, exchange):
    system, client = make(cfg, exchange)
    bad = client.put("/api/settings", json={"changes": {"delta_active": True, "delta_env": "live"}})
    assert bad.status_code == 400 and system.settings.active_brokers == ["paper"]
    ok = client.put("/api/settings", json={"changes": {"delta_active": True, "delta_env": "live"}, "confirm": LIVE_CONFIRM_PHRASE})
    assert ok.status_code == 200 and ok.json()["is_real_money"] is True


def test_token_protects_every_write(cfg, exchange):
    _, client = make(cfg, exchange, token="sekret")
    writes = [
        ("put", "/api/settings", {"changes": {"stop_loss_pct": 2}}),
        ("post", "/api/settings/clear-credentials", {}),
        ("post", "/api/settings/test-delta", {}),
        ("post", "/api/toggles", {"key": "trading:paper", "enabled": True}),
        ("post", "/api/close-all", {}),
        ("post", "/api/paper/reset", {}),
        ("post", "/api/positions/paper/BTCUSD/close", {}),
    ]
    for method, url, body in writes:
        assert getattr(client, method)(url, json=body).status_code == 401, url
    assert client.put("/api/settings", json={"changes": {"stop_loss_pct": 2}}, headers={"X-API-Token": "sekret"}).status_code == 200
    assert client.get("/api/header").status_code == 200  # reads stay open


def test_broker_routes(cfg, exchange):
    _, client = make(cfg, exchange)
    assert client.get("/api/account?broker=paper").json()["broker"] == "paper"
    assert client.get("/api/account?broker=delta").status_code == 502  # no keys yet
    assert client.get("/api/positions?broker=binance").status_code == 404
    assert client.post("/api/positions/paper/BTCUSD/close").status_code == 502  # nothing open


async def test_close_position_route_closes_paper(cfg, exchange):
    system, client = make(cfg, exchange)
    system.bus.start()
    system.bus.publish(Tick(symbol="BTCUSD", price=100_000))
    await system.bus.drain()
    await system.paper.place_order("BTCUSD", "buy", 1, "c1")
    assert client.post("/api/positions/paper/BTCUSD/close").json()["closed"] == ["BTCUSD"]
    assert client.get("/api/positions?broker=paper").json() == []
    await system.bus.stop()


def test_overview_works_without_delta(cfg, exchange):
    _, client = make(cfg, exchange)
    body = client.get("/api/overview").json()
    assert [a["broker"] for a in body["accounts"]] == ["paper"] and body["accounts"][0]["error"] == ""
    assert body["strategies"][0]["name"] == "always_buy"


async def test_inactive_broker_is_hidden(cfg, exchange):
    system, client = make(cfg, exchange)
    await system.update_settings({"delta_active": True, "paper_active": False})
    page = client.get("/positions").text
    assert 'data-broker="delta"' in page and 'data-broker="paper"' not in page
    assert 'href="/paper"' not in client.get("/").text
    assert [b["name"] for b in client.get("/api/header").json()["brokers"]] == ["delta"]


def test_header_lists_each_active_broker_with_its_switch(cfg, exchange):
    _, client = make(cfg, exchange)
    [paper] = client.get("/api/header").json()["brokers"]
    assert (paper["name"], paper["trading"], paper["not_ready"]) == ("paper", True, "")


def test_close_all_stops_every_broker(cfg, exchange):
    system, client = make(cfg, exchange)
    assert client.post("/api/close-all").json() == {"closed": [], "errors": []}
    assert system.trading_on("paper") is False and system.trading_on("delta") is False


def test_static_urls_carry_a_content_version(cfg, exchange):
    _, client = make(cfg, exchange)
    page = client.get("/market").text
    assert re.search(r'/static/js/app\.js\?v=[0-9a-f]{12}"', page) and re.search(r'/static/css/app\.css\?v=[0-9a-f]{12}"', page)


async def test_saved_ai_reports_open_by_id(cfg, exchange):
    from tradebuddy.events import AIReport

    system, client = make(cfg, exchange)
    await system.on_ai_report(AIReport(ok=True, model="ministral-3b-2512", report={"headline": "open me", "health": "good"}))
    [row] = client.get("/api/ai/history", params={"start": 0, "ok_only": True}).json()
    assert client.get(f"/api/ai/reports/{row['id']}").json()["report"]["headline"] == "open me"
    assert client.get(f"/api/ai/reports/{row['id'] + 1}").status_code == 404
    assert 'id="day"' in client.get("/ai").text
