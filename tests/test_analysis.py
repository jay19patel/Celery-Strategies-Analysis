"""Market analysis: the options book, the rule-based insights and the jobs that run them."""

from __future__ import annotations

import math
import random
import time
from datetime import UTC, datetime

import pytest

from tradebuddy.analyst import Analyst
from tradebuddy.api import METHODS, Api
from tradebuddy.delta import Candle
from tradebuddy.events import EventBus, OptionsSnapshot
from tradebuddy.insights import insights, lean, market_context
from tradebuddy.jobs import Jobs
from tradebuddy.options import OptionQuote, OptionsBook, OptionsFeed, max_pain, parse_symbol, parse_ticker, summarize
from tradebuddy.settings import Settings
from tradebuddy.stream import BarCloser, DeltaStream
from tradebuddy.system import System

from .conftest import IdleStream
from .test_pipeline import AlwaysBuy

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC).timestamp()
DAY1 = datetime(2026, 10, 7, 12, 0, tzinfo=UTC).timestamp()  # 24h out
SPOT = 100_000.0


def quote(kind, strike, expiry=DAY1, iv=0.5, oi=1.0, volume=0.5, delta=None, mark=None):
    if delta is None:  # 0.5 at the money, 0.25 out of the money, 0.75 in it
        otm = strike > SPOT if kind == "call" else strike < SPOT
        delta = (0.5 if strike == SPOT else 0.25 if otm else 0.75) * (1 if kind == "call" else -1)
    return OptionQuote(
        symbol=f"{kind[0].upper()}-BTC-{int(strike)}-{datetime.fromtimestamp(expiry, UTC):%d%m%y}", underlying="BTC", kind=kind, strike=strike,
        expiry=expiry, mark=mark if mark is not None else 1000.0, mark_iv=iv, delta=delta, oi=oi, volume=volume, spot=SPOT, at=NOW,
    )


def book_quotes(expiry=DAY1, step=5_000, priced=False):
    """Strikes 90k-110k. Puts heavier below spot, calls heavier above; puts carry more IV (skew).
    `priced`: premiums fall with distance from spot, as real ones do; otherwise every mark is 1,000."""
    out = []
    for strike in range(90_000, 110_001, step):
        mark = max(50.0, 3000 - 0.5 * abs(strike - SPOT)) if priced else None
        out.append(quote("call", strike, expiry, iv=0.50 if strike >= SPOT else 0.55, oi=3.0 if strike == 110_000 else 1.0, mark=mark))
        out.append(quote("put", strike, expiry, iv=0.58 if strike < SPOT else 0.50, oi=4.0 if strike == 90_000 else 1.0, mark=mark))
    return out


# -- options ------------------------------------------------------------------------


def test_option_symbols_and_tickers_parse():
    kind, und, strike, expiry = parse_symbol("C-BTC-110000-091026")
    assert (kind, und, strike) == ("call", "BTC", 110000.0)
    assert datetime.fromtimestamp(expiry, UTC) == datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
    assert parse_symbol("BTCUSD") is None and parse_symbol("C-BTC-x-091026") is None
    q = parse_ticker({"symbol": "P-ETH-2600-071026", "mark_price": "12.5", "oi": "3.2", "oi_value_usd": "8400", "volume": 1.5,
                      "quotes": {"mark_iv": "0.41", "best_bid": "12", "best_ask": "13"}, "greeks": {"delta": "-0.3", "spot": "2650"}})
    assert (q.kind, q.underlying, q.strike, q.mark, q.mark_iv, q.oi, q.delta, q.spot) == ("put", "ETH", 2600.0, 12.5, 0.41, 3.2, -0.3, 2650.0)


def test_summary_numbers():
    s = summarize("BTC", book_quotes(), SPOT, NOW)
    day = s["day"]
    assert s["contracts"] == 10 and day["hours"] == pytest.approx(24)
    assert day["atm_strike"] == SPOT and day["atm_iv"] == pytest.approx(0.5)
    assert day["skew_25d"] == pytest.approx(0.58 - 0.50)  # 25-delta put IV minus 25-delta call IV
    assert s["pcr_oi"] == pytest.approx(8.0 / 7.0, abs=1e-3)
    assert (day["call_wall"], day["put_wall"]) == (110_000, 90_000)
    assert day["implied_move_pct"] == pytest.approx(100 * 0.5 * math.sqrt(1 / 365), abs=1e-3)
    assert [r["strike"] for r in s["chains"][str(int(DAY1))]] == [90_000, 95_000, 100_000, 105_000, 110_000]


def test_max_pain_is_where_holders_are_paid_least():
    quotes = [quote("call", 100_000, oi=10), quote("put", 100_000, oi=10), quote("call", 90_000, oi=1), quote("put", 110_000, oi=1)]
    assert max_pain(quotes) == 100_000


def test_expired_contracts_are_left_out():
    old = quote("call", SPOT, expiry=NOW - 1)
    assert summarize("BTC", [old], SPOT, NOW) is None
    assert summarize("BTC", [*book_quotes(), old], SPOT, NOW)["contracts"] == 10


def test_the_stream_puts_option_tickers_in_the_book_not_on_the_bus():
    bus, book = EventBus(), OptionsBook({"BTC"})
    stream = DeltaStream("wss://x", bus, BarCloser(bus, {("BTCUSD", "1m")}), options=book)
    assert {"call_options", "put_options"} <= set(stream.subscribe_payload()["payload"]["channels"][0]["symbols"])

    published = []
    bus.publish = published.append
    import asyncio

    asyncio.run(stream.handle({"type": "v2/ticker", "symbol": "C-BTC-110000-091026", "mark_price": "5", "quotes": {"mark_iv": "0.4"}}))
    asyncio.run(stream.handle({"type": "v2/ticker", "symbol": "C-SOL-150-091026", "mark_price": "5"}))  # not an underlying we track
    asyncio.run(stream.handle({"type": "v2/ticker", "symbol": "BTCUSD", "mark_price": "100"}))
    assert list(book.quotes) == ["C-BTC-110000-091026"]
    assert [type(e).__name__ for e in published] == ["Tick", "MarketStats"]


async def test_options_feed_uses_the_socket_and_falls_back_to_rest():
    book, published, fetched = OptionsBook({"BTC"}), [], []

    async def fetch(underlying):
        fetched.append(underlying)
        return book_quotes()

    feed = OptionsFeed(book, published.append, fetch, lambda _sym: SPOT, ws_stale=60, rest_every=60)
    first = book_quotes()[0]
    book.quotes[first.symbol], book.updated["BTC"] = first, NOW  # the socket has sent one contract so far
    await feed.tick(now=NOW)  # not trusted until a full chain has seeded the book
    assert fetched == ["BTC"] and published[-1].source == "rest" and published[-1].summary["contracts"] == 10
    book.updated.clear()
    await feed.tick(now=NOW + 15)  # socket quiet, REST used 15s ago: no new call
    assert fetched == ["BTC"] and len(published) == 1

    for q in book_quotes():
        book.quotes[q.symbol] = q
    book.updated["BTC"] = NOW + 20  # the socket delivers
    await feed.tick(now=NOW + 30)
    assert fetched == ["BTC"] and published[-1].source == "websocket"
    assert isinstance(published[-1], OptionsSnapshot) and published[-1].symbol == "BTCUSD"


async def test_options_feed_records_a_rest_failure_and_carries_on():
    async def fetch(_u):
        raise RuntimeError("HTTP 503")

    feed = OptionsFeed(OptionsBook({"BTC"}), [].append, fetch, lambda _s: SPOT)
    await feed.tick(now=NOW)
    assert "503" in feed.stats()["last_error"]


# -- insights -------------------------------------------------------------------------


def bars(n, start=100.0, step=0.0, vol=0.001, seed=1):
    rnd, out, price = random.Random(seed), [], start
    for i in range(n):
        new = price * (1 + step + rnd.gauss(0, vol))
        out.append(Candle(1_700_000_000 + 900 * i, price, max(price, new) * 1.0005, min(price, new) * 0.9995, new, 100 + rnd.random()))
        price = new
    return out


def test_context_and_rules():
    candles = bars(700, step=0.0005)
    oi = [Candle(c.time, 0, 0, 0, 100 * 1.0015**i) for i, c in enumerate(candles)]  # OI up ~15% a day
    funding = [Candle(c.time, 0, 0, 0, 0.07) for c in candles]
    options = summarize("BTC", book_quotes(), SPOT, NOW)
    ctx = market_context("BTCUSD", candles, oi, funding, {"last": candles[-1].close}, options)
    assert ctx["rv_7d"] > 0 and ctx["oi_change_24h_pct"] > 5 and ctx["iv_rv_ratio"] > 1.3
    keys = {i["key"]: i for i in insights(ctx)}
    assert keys["funding_high"]["severity"] == "alert" and keys["funding_high"]["bias"] == "bearish"
    assert keys["iv_rich"]["bias"] == "volatility"
    assert keys["put_skew"]["bias"] == "bearish"
    assert keys["oi_flow"]["title"].startswith("New longs")
    assert keys["trend"]["bias"] == "bullish"
    assert [i["severity"] for i in insights(ctx)] == sorted((i["severity"] for i in insights(ctx)), key=["alert", "watch", "info"].index)
    assert lean([{"severity": "alert", "bias": "bearish"}, {"severity": "info", "bias": "bullish"}])["lean"] == "bearish"


def test_rules_stay_quiet_without_data():
    assert insights(market_context("BTCUSD", [])) == []


# -- the analyst ------------------------------------------------------------------------


class CandleClient:
    def __init__(self, candles):
        self.candles_by = candles
        self.asked = []

    async def candles(self, symbol, resolution, count):
        self.asked.append(symbol)
        return self.candles_by[-count:]


def make_analyst(settings, published):
    client = CandleClient(bars(700))
    live = summarize("BTC", book_quotes(expiry=time.time() + 86_400), SPOT)
    a = Analyst(lambda: client, lambda: settings[0], published.append, lambda s: live if s == "BTCUSD" else None, lambda s: None)
    return a, client


async def test_analyst_publishes_one_analysis_per_symbol():
    published = []
    a, client = make_analyst([Settings()], published)
    await a.cycle()
    assert [e.symbol for e in published] == ["BTCUSD", "ETHUSD"]
    btc = published[0]
    assert btc.insights and btc.context["lean"]
    assert set(client.asked) == {"BTCUSD", "OI:BTCUSD", "FUNDING:BTCUSD", "ETHUSD", "OI:ETHUSD", "FUNDING:ETHUSD"}


async def test_engine_keeps_the_latest_and_a_row_a_minute(cfg, exchange):
    s = System(cfg, strategies=[AlwaysBuy()], stream_factory=IdleStream, client_factory=exchange.client)
    for ts in (NOW, NOW + 15, NOW + 30, NOW + 61):
        summary = summarize("BTC", book_quotes(), SPOT, ts)
        await s.on_options(OptionsSnapshot(ts=ts, symbol="BTCUSD", underlying="BTC", source="websocket", summary=summary))
    rows = s.store.options_history("BTC", 0)
    assert [r["ts"] for r in rows] == [NOW, NOW + 60] and rows[0]["keep"] == 1  # 12:00 starts a 15-minute bar
    await s.record(OptionsSnapshot(symbol="BTCUSD", underlying="BTC", source="websocket", summary=summary))
    assert s.store.recent_events(5) == []  # summarised per minute, not logged
    api = Api(s)
    assert {"analysis", "options_history", "database"} <= set(METHODS)
    assert (await api.analysis())["symbols"]["BTCUSD"]["options"]["source"] == "websocket"
    db = await api.database()
    assert {t["name"] for t in db["tables"]} >= {"events", "orders", "options_history"} and db["file_bytes"] > 0


def test_jobs_record_runs_errors_and_the_next_run():
    job = Jobs("engine").add("housekeeping", "prunes", every=60)
    with job.tick():
        pass
    with pytest.raises(RuntimeError), job.tick():
        raise RuntimeError("disk full")
    snap = job.snapshot()
    assert (snap["runs"], snap["errors"], snap["state"]) == (2, 1, "failed")
    assert "disk full" in snap["last_error"] and snap["next_at"] > time.time() + 55


async def test_every_process_reports_its_jobs(cfg, exchange):
    s = System(cfg, strategies=[AlwaysBuy()], stream_factory=IdleStream, client_factory=exchange.client)
    names = {j["name"] for j in s.all_jobs()}
    assert {"housekeeping", "position guard", "reconcile", "bar clock"} <= names
    from tradebuddy.events import ProcessHeartbeat

    await s.on_process(ProcessHeartbeat(role="analyst", process={"role": "analyst"}, jobs=[{"name": "market analysis", "process": "analyst", "state": "idle"}]))
    assert any(j["name"] == "market analysis" for j in s.all_jobs())
    s.remote_processes["analyst"]["at"] -= 120
    assert next(j for j in s.all_jobs() if j["name"] == "market analysis")["state"] == "silent"
    assert any(p["role"] == "analyst" for p in s.metrics()["processes"])



def bars_from(start_ts, closes):
    """15m candles whose high and low straddle the close, so an excursion is measurable."""
    return [
        Candle(start_ts + 900 * i, c, c * 1.001, c * 0.999, c, 10.0)
        for i, c in enumerate(closes)
    ]


def test_outcome_measures_the_window_a_claim_covered():
    from tradebuddy.insights import outcome

    t0 = 1_700_000_000
    closes = [100.0] * 5 + [104.0] + [102.0] * 3  # up 4% at the peak, settling at +2%
    out = outcome(bars_from(t0, closes), t0, 8 * 900 / 3600, breakout_pct=3.0)
    assert out is not None
    assert out["price"] == pytest.approx(102.0)
    assert out["ret"] == pytest.approx(0.02)
    assert out["excursion"] == pytest.approx(104 * 1.001 / 100 - 1, rel=1e-3)  # the high, not the close
    assert out["breakout"] is True  # it travelled past 3% even though it closed at 2%
    assert outcome(bars_from(t0, closes), t0, 8 * 900 / 3600, breakout_pct=6.0)["breakout"] is False


def test_outcome_waits_rather_than_guessing_when_the_window_is_short():
    from tradebuddy.insights import outcome

    t0 = 1_700_000_000
    bars = bars_from(t0, [100.0] * 4)  # only an hour of candles
    assert outcome(bars, t0, 24, breakout_pct=3.0) is None  # a 24h horizon is not covered
    assert outcome(bars, t0 - 86_400, 24, breakout_pct=3.0) is None  # the claim bar is not in them

