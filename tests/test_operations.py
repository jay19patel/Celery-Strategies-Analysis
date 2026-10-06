"""Running for a long time: the event log stays bounded, outages are reported once, and the
dashboard can tell when there is no market data."""

from __future__ import annotations

import time
from types import SimpleNamespace

from tradebuddy.api import Api
from tradebuddy.events import EventBus, FeedStatus, OrderFailed, SignalGenerated, StrategyEvaluated, Tick, TradeSkipped
from tradebuddy.store import DEFAULT_RETENTION_DAYS, RETENTION_DAYS, Store
from tradebuddy.stream import BarCloser, DeltaStream, describe
from tradebuddy.system import System

from .conftest import IdleStream
from .test_pipeline import AlwaysBuy

DAY = 86_400


def test_events_carry_their_level():
    assert OrderFailed(client_order_id="x", error="e").to_dict()["level"] == "error"
    assert TradeSkipped(strategy="s", symbol="BTCUSD", side="buy", broker="paper", reason="r").to_dict()["level"] == "warning"
    assert Tick(symbol="BTCUSD", price=1.0).to_dict()["level"] == "info"
    assert FeedStatus(connected=False).level == "warning" and FeedStatus(connected=True).level == "info"


def test_prune_keeps_each_type_for_its_retention(tmp_path):
    store, now = Store(str(tmp_path / "t.db")), time.time()
    for event_type, age_days in [
        ("CandleClosed", RETENTION_DAYS["CandleClosed"] + 1), ("CandleClosed", 0.5),
        ("SignalGenerated", DEFAULT_RETENTION_DAYS + 1), ("SignalGenerated", RETENTION_DAYS["CandleClosed"] + 1),
        ("OrderUpdate", RETENTION_DAYS["OrderUpdate"] + 1),
    ]:
        store.record_event({"ts": now - age_days * DAY, "type": event_type})
    assert store.prune_events(now=now, batch=1) == 3  # batches loop until nothing is left
    left = sorted((e["type"], round((now - e["ts"]) / DAY, 1)) for e in store.recent_events(10))
    assert left == [("CandleClosed", 0.5), ("SignalGenerated", RETENTION_DAYS["CandleClosed"] + 1)]
    assert store.events_size()["rows"] == 2


async def test_per_bar_plumbing_is_not_written_to_the_event_log(cfg, exchange):
    s = System(cfg, strategies=[AlwaysBuy()], stream_factory=IdleStream, client_factory=exchange.client)
    s.bus.start()
    for event in (
        Tick(symbol="BTCUSD", price=1.0),
        StrategyEvaluated(strategy="always_buy", version=1, symbol="BTCUSD", resolution="1m", bar_time=0),
        SignalGenerated(strategy="always_buy", version=1, symbol="BTCUSD", side="buy", reason="", bar_time=0, size=1),
    ):
        await s.record(event)
    assert [e["type"] for e in s.store.recent_events(10)] == ["SignalGenerated"]
    await s.bus.stop()


async def test_event_log_can_show_only_warnings_and_errors(cfg, exchange):
    s = System(cfg, strategies=[AlwaysBuy()], stream_factory=IdleStream, client_factory=exchange.client)
    for event in (
        SignalGenerated(strategy="a", version=1, symbol="BTCUSD", side="buy", reason="", bar_time=0, size=1),
        TradeSkipped(strategy="a", symbol="BTCUSD", side="buy", broker="paper", reason="r"),
        OrderFailed(client_order_id="x", error="e"),
    ):
        await s.record(event)
    api = Api(s)
    assert [e["type"] for e in await api.events(level="warning")] == ["OrderFailed", "TradeSkipped"]
    assert [e["type"] for e in await api.events(level="error")] == ["OrderFailed"]
    assert [e["type"] for e in await api.events(level="error", type="TradeSkipped")] == []
    assert len(await api.events()) == 3


def test_a_refused_handshake_is_one_short_line():
    response = SimpleNamespace(status_code=403, reason_phrase="Forbidden", headers={"Via": "cloudfront"}, body=b"<HTML>" * 100)
    assert describe(TimeoutError("timed out during opening handshake")) == "TimeoutError: timed out during opening handshake"
    exc = Exception("server rejected WebSocket connection: HTTP 403")
    exc.response = response
    assert describe(exc) == "handshake refused: HTTP 403 Forbidden (the exchange's CDN refuses this server's IP or region)"


async def test_feed_status_is_published_once_per_change_not_per_retry():
    bus, seen = EventBus(), []

    async def collect(e):
        seen.append(e)

    bus.subscribe(collect, FeedStatus)
    bus.start()
    stream = DeltaStream("wss://x", bus, BarCloser(bus, set()))
    for _ in range(5):
        stream._set_status(connected=False, authenticated=False)  # five failed retries
    stream._set_status(connected=True)
    stream._set_status(connected=True)
    stream._set_status(connected=False)
    await bus.drain()
    assert [e.connected for e in seen] == [False, True, False]
    await bus.stop()


async def test_header_says_when_there_is_no_market_data(cfg, exchange):
    class Connected(IdleStream):
        def status(self):
            return {"connected": True, "url": self.url, "last_error": "", "down_since": None}

    down = System(cfg, strategies=[AlwaysBuy()], stream_factory=IdleStream, client_factory=exchange.client)
    market = down.header()["market"]
    assert (market["live"], market["reason"], market["missing"]) == (False, "market data stream is down", ["BTCUSD", "ETHUSD"])

    up = System(cfg, strategies=[AlwaysBuy()], stream_factory=Connected, client_factory=exchange.client)
    await up.prices.on_tick(Tick(symbol="BTCUSD", price=1.0))
    market = up.header()["market"]
    assert (market["live"], market["reason"]) == (False, "no live price for ETHUSD")
    await up.prices.on_tick(Tick(symbol="ETHUSD", price=1.0))
    assert up.header()["market"]["live"] is True
