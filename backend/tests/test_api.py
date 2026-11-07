"""The HTTP surface: private by default, the PIN, and no secret ever returned."""

from __future__ import annotations

import time
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
    """Invariant 10, checked against the OpenAPI schema rather than `app.routes`.

    This FastAPI version wraps an included router in an object with no `.path`, so walking
    `app.routes` saw four docs endpoints and twelve opaque wrappers: the loop ran, matched
    nothing, and passed. A test that cannot fail is not a test, and this one guards the rule that
    a route added later is private until somebody lists it.
    """
    schema = client.app.openapi()["paths"]  # type: ignore[attr-defined]
    paths = [p for p in schema if p.startswith("/api")]
    assert len(paths) > 40, "the schema should carry every /api route"
    checked = 0
    for path in paths:
        if path in PUBLIC_PATHS or "{" in path or "get" not in schema[path]:
            continue
        assert client.get(path).status_code == 401, path
        checked += 1
    assert checked > 30
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


def test_a_dashboard_on_another_host_gets_in_with_a_one_time_ticket(client: TestClient) -> None:
    """The session cookie belongs to the dashboard's host, so a socket to the backend's own host
    carries none: the dashboard asks for a ticket through its /api proxy and spends it once."""
    assert client.get("/api/auth/ws").status_code == 401  # a ticket needs the session
    login(client)
    r = client.get("/api/auth/ws", headers={"host": "tbapis.example.com", "x-forwarded-proto": "https"}).json()
    assert r["url"] == "wss://tbapis.example.com/ws"
    client.cookies.clear()  # the browser's socket to the backend's host: no cookie, only the ticket
    with client.websocket_connect(f"/ws?ticket={r['ticket']}") as ws:
        assert ws.receive_json()["type"] == "Hello"
    for bad in (r["ticket"], "1.2.3", security.issue_session()):  # spent, forged, a session posing as one
        with pytest.raises(WebSocketDisconnect), client.websocket_connect(f"/ws?ticket={bad}") as ws:
            ws.receive_json()
    assert not security.redeem_ticket(security.issue_ticket(now=time.time() - 120))  # expired


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


def test_the_strategies_page_can_switch_a_strategy_per_symbol(client: TestClient) -> None:
    """The Strategies page writes both switches through the settings endpoint, and reads the result
    back from /strategies/directional."""
    login(client)
    assert client.patch("/api/settings", json={"changes": {"underlyings": ["BTC", "ETH"]}}).status_code == 200

    before = {s["name"]: s["symbols"] for s in client.get("/api/strategies/directional").json()["strategies"]}
    assert before["ema_vwap"] == ["BTC", "ETH"]  # unmapped: every enabled underlying

    assert client.patch("/api/settings", json={"changes": {"strategy_symbols": {"ema_vwap": ["ETH"]}}}).status_code == 200
    body = client.get("/api/strategies/directional").json()
    assert body["underlyings"] == ["BTC", "ETH"]
    assert {s["name"]: s["symbols"] for s in body["strategies"]}["ema_vwap"] == ["ETH"]

    bad = client.patch("/api/settings", json={"changes": {"strategy_symbols": {"ema_vwap": ["DOGE"]}}})
    assert bad.status_code == 422 and "strategy_symbols" in bad.json()["error"]


def test_a_brief_is_asked_for_by_desk(client: TestClient) -> None:
    login(client)
    assert client.get("/api/ai/brief?underlying=BTC&kind=market").json() == []
    assert client.get("/api/ai/brief?underlying=BTC&kind=nope").status_code == 409
    assert client.post("/api/ai/brief", json={"underlying": "BTC", "kind": "nope"}).status_code == 409


def test_the_zero_hero_surface_is_reachable_and_off_by_default(client: TestClient) -> None:
    login(client)
    status = client.get("/api/zerohero/status").json()
    assert status["enabled"] is False  # fail closed: it does not run until switched on
    assert status["record"]["hit_rate_pct"] is None
    assert client.get("/api/zerohero/decisions").json() == []
    assert client.post("/api/zerohero/run").json()["note"].startswith("zero-hero is off")


def test_zero_hero_settings_round_trip_and_refuse_an_impossible_window(client: TestClient) -> None:
    login(client)
    assert client.patch("/api/settings", json={"changes": {"zh_min_multiple": 8.0}}).status_code == 200
    assert client.get("/api/settings").json()["values"]["zh_min_multiple"] == 8.0
    # A window that opens inside its own time stop is a trade that cannot work, not a preference.
    bad = client.patch("/api/settings", json={"changes": {"zh_window_end_hours": 0.5}})
    assert bad.status_code == 422 and "time stop" in bad.json()["error"]


def test_the_strategy_menu_does_not_offer_zero_hero(client: TestClient) -> None:
    """It is priced, held and exited like any structure, but the options auto-trader must never
    pick it: its gate is its own and the regime score cannot answer for it."""
    login(client)
    keys = {s["key"] for s in client.get("/api/settings").json()["strategies"]}
    assert "zero_hero" not in keys
    assert "iron_condor" in keys


def test_accounts_carry_the_fees_of_closed_structures(client: TestClient) -> None:
    """A closed structure on the book must not break the accounts endpoint the Performance page reads."""
    from app.core.db import session
    from app.core.models import Structure

    with session() as s:
        s.add(Structure(id="s1", broker="paper", underlying="BTC", strategy="iron_condor", source="auto", status="closed",
                        expiry=0.0, qty=1, fees=1.25, pnl=-3.0, take_profit_pct=50.0, stop_loss_pct=100.0, opened_at=0.0))
        s.commit()
    login(client)
    r = client.get("/api/portfolio/accounts")
    assert r.status_code == 200, r.text


def test_orders_come_a_page_at_a_time_newest_first_with_the_total(client: TestClient) -> None:
    from app.core.db import session
    from app.core.models import OrderRecord

    with session() as s:
        for i in range(45):
            s.add(OrderRecord(client_order_id=f"o{i}", structure_id="s1", broker="paper" if i % 3 else "delta", purpose="open",
                              symbol="BTCUSD", side="buy", size=1, limit_price=1.0, status="failed" if i % 5 == 0 else "filled",
                              created_at=float(i), updated_at=float(i)))
        s.commit()
    login(client)
    first = client.get("/api/trading/orders/page?size=20").json()
    assert (first["total"], first["pages"], len(first["rows"])) == (45, 3, 20)
    assert first["rows"][0]["client_order_id"] == "o44"  # newest first
    last = client.get("/api/trading/orders/page?size=20&page=3").json()
    assert len(last["rows"]) == 5 and last["rows"][-1]["client_order_id"] == "o0"
    assert client.get("/api/trading/orders/page?page=99").json()["page"] == 3  # past the end: the last page
    paper_failed = client.get("/api/trading/orders/page?broker=paper&status=failed").json()
    assert paper_failed["total"] == sum(1 for i in range(45) if i % 3 and i % 5 == 0)
    assert all(r["broker"] == "paper" and r["status"] == "failed" for r in paper_failed["rows"])


def test_notifications_are_the_position_and_risk_events_kept_read_or_unread(client: TestClient) -> None:
    from app.core.db import session
    from app.core.models import EventLog

    with session() as s:
        for i, kind in enumerate(["StructureOpened", "SettingsChanged", "StructureClosed", "RiskHalt", "Signal", "StructureOpened"]):
            s.add(EventLog(ts=float(i), type=kind, level="info", message=f"{kind} {i}"))
        s.commit()
    login(client)
    page = client.get("/api/notifications").json()
    assert [r["type"] for r in page["rows"]] == ["StructureOpened", "RiskHalt", "StructureClosed", "StructureOpened"]  # newest first, no noise
    assert page["total"] == page["unread"] == 4

    newest = page["rows"][0]["id"]
    assert client.post("/api/notifications/read", json={"ids": [newest]}).json() == {"marked": 1, "unread": 3}
    unread = client.get("/api/notifications?unread=true").json()
    assert unread["total"] == 3 and newest not in [r["id"] for r in unread["rows"]]
    assert client.get("/api/notifications").json()["rows"][0]["read_at"] is not None  # still listed, as read

    assert client.post("/api/notifications/read", json={}).json() == {"marked": 3, "unread": 0}
    assert client.get("/api/notifications?unread=true").json()["total"] == 0


def test_reset_data_needs_the_pin_keeps_settings_and_spares_open_positions(client: TestClient) -> None:
    from sqlmodel import select

    from app.core.db import session
    from app.core.models import Decision, Structure

    def structure(sid: str, status: str) -> Structure:
        return Structure(id=sid, broker="paper", underlying="BTC", strategy="iron_condor", source="auto", status=status,
                         expiry=0.0, qty=1, pnl=-1.0, take_profit_pct=50.0, stop_loss_pct=100.0, opened_at=0.0)

    login(client)
    assert client.patch("/api/settings", json={"changes": {"min_score": 70}}).status_code == 200
    with session() as s:
        s.add(structure("done", "closed"))
        s.add(Decision(ts=0.0, underlying="BTC", action="skip"))
        s.add(structure("held", "open"))
        s.commit()

    assert client.post("/api/system/reset-data", json={"pin": "000000"}).status_code == 409
    r = client.post("/api/system/reset-data", json={"pin": PIN})
    assert r.status_code == 409 and "close every position" in r.json()["error"]
    with session() as s:
        assert len(s.exec(select(Structure)).all()) == 2  # refused: nothing deleted

        s.delete(s.get(Structure, "held"))
        s.commit()
    r = client.post("/api/system/reset-data", json={"pin": PIN})
    assert r.status_code == 200 and r.json()["deleted"]["structure"] == 1
    with session() as s:
        assert not s.exec(select(Structure)).all() and not s.exec(select(Decision)).all()
    assert client.get("/api/settings").json()["values"]["min_score"] == 70
    assert client.get("/api/portfolio/stats").json()["overall"]["trades"] == 0


def test_the_overview_reads_equity_funnel_and_a_per_broker_journal(client: TestClient) -> None:
    from datetime import datetime

    from app.core.db import session
    from app.core.models import DayRisk, Decision, SignalLog, Structure

    today = datetime.now().strftime("%Y-%m-%d")  # noqa: DTZ005
    now = time.time()
    with session() as s:
        s.add(DayRisk(broker="paper", day=today, start_equity=1000.0))
        s.add(DayRisk(broker="delta", day=today, start_equity=50.0))
        s.add(SignalLog(ts=now, strategy="x", symbol="BTCUSD", interval="15m", bar_time=0, action="open"))
        s.add(SignalLog(ts=now, strategy="x", symbol="BTCUSD", interval="15m", bar_time=1, action="skip"))
        s.add(Decision(ts=now, underlying="BTC", action="error"))  # an error is not a signal
        for i, (broker, status, pnl) in enumerate([("paper", "closed", 5.0), ("paper", "closed", -2.0), ("paper", "failed", None), ("paper", "error", None), ("delta", "closed", 1.0)]):
            s.add(Structure(id=f"f{i}", broker=broker, underlying="BTC", strategy="iron_condor", source="auto", status=status, expiry=0.0,
                            qty=1, pnl=pnl, take_profit_pct=50.0, stop_loss_pct=100.0, opened_at=now, closed_at=now if pnl is not None else None))
        s.commit()
    login(client)
    assert client.get("/api/portfolio/equity?broker=paper").json() == [{"broker": "paper", "day": today, "equity": 1000.0}]
    f = client.get(f"/api/portfolio/funnel?since={now - 60}&broker=paper").json()
    assert (f["signals"], f["passed"], f["orders"], f["filled"], f["profitable"]) == (2, 1, 4, 2, 1)  # one in error is not filled
    assert client.get(f"/api/portfolio/funnel?since={now + 60}").json()["orders"] == 0
    j = client.get("/api/portfolio/journal?broker=delta").json()
    assert (j["trades"], j["pnl"]) == (1, 1.0)
