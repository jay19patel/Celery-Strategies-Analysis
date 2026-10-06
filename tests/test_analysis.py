"""Market analysis: the options book, insights, the forecaster, the playbook, the AI review and the jobs
that run them."""

from __future__ import annotations

import json
import math
import random
import time
from datetime import UTC, datetime

import httpx
import pytest

from tradebuddy import tbai
from tradebuddy.analyst import Analyst
from tradebuddy.api import METHODS, Api
from tradebuddy.delta import Candle
from tradebuddy.events import AIReport, EventBus, MarketAnalysis, OptionsSnapshot
from tradebuddy.insights import insights, lean, market_context
from tradebuddy.jobs import Jobs
from tradebuddy.mistral import MistralError, check, complete
from tradebuddy.options import OptionQuote, OptionsBook, OptionsFeed, max_pain, parse_symbol, parse_ticker, summarize
from tradebuddy.playbook import decide
from tradebuddy.settings import DELTA_ROUTING_FIELDS, Settings, apply_changes
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


# -- playbook ---------------------------------------------------------------------------


def forecast(**over):
    base = {"up_probability": 0.5, "down_probability": 0.5, "expected_return": 0.0, "expected_abs_move": 0.02,
            "predicted_realized_vol": 0.5, "breakout_probability": 0.3, "skill": {"direction": True, "realized_vol": True, "breakout": True}}
    return base | over


def test_playbook_without_a_model_or_options_does_nothing():
    assert decide(None, summarize("BTC", book_quotes(), SPOT, NOW))["strategy"] == "NO_TRADE"
    assert decide(forecast(), None)["strategy"] == "NO_TRADE"


def test_playbook_directional_needs_direction_skill():
    opts = summarize("BTC", [q if q.kind == "put" else quote("call", q.strike, mark=3000 - 0.1 * (q.strike - 90_000)) for q in book_quotes()], SPOT, NOW)
    up = forecast(up_probability=0.7, down_probability=0.3, expected_abs_move=0.05)
    d = decide(up, opts)
    assert d["strategy"] == "LONG_CALL_SPREAD"
    assert [(leg["action"], leg["strike"]) for leg in d["legs"]] == [("buy", 100_000), ("sell", 105_000)]
    assert d["max_loss"] == pytest.approx(500) and d["max_profit"] == pytest.approx(4500)
    assert decide(up | {"skill": {"direction": False, "realized_vol": False}}, opts)["strategy"] == "NO_TRADE"


def test_playbook_volatility_trades():
    opts = summarize("BTC", book_quotes(), SPOT, NOW)  # ATM IV 50%, straddle 2,000
    cheap = decide(forecast(predicted_realized_vol=0.8, breakout_probability=0.7, expected_abs_move=0.03), opts)
    assert cheap["strategy"] == "LONG_STRADDLE" and len(cheap["legs"]) == 2
    assert decide(forecast(predicted_realized_vol=0.5), opts)["strategy"] == "NO_TRADE"  # no edge either way

    fine = summarize("BTC", book_quotes(step=1_000, priced=True), SPOT, NOW)  # implied move to expiry: about 2,600
    rich = decide(forecast(predicted_realized_vol=0.3, breakout_probability=0.1), fine)
    assert rich["strategy"] == "IRON_CONDOR"
    assert [(leg["action"], leg["kind"], leg["strike"]) for leg in rich["legs"]] == [
        ("sell", "put", 97_000), ("buy", "put", 96_000), ("sell", "call", 103_000), ("buy", "call", 104_000),
    ]
    assert rich["net"] == pytest.approx(1000) and rich["max_loss"] == pytest.approx(0)  # 1,000 credit on 1,000-wide wings


# -- forecaster -------------------------------------------------------------------------


def regime_bars(n, seed=3):
    """A random walk whose volatility switches between calm and wild for days at a time: learnable."""
    rnd, out, price = random.Random(seed), [], 100.0
    vol = 0.001
    for i in range(n):
        if i % 400 == 0:
            vol = rnd.choice((0.001, 0.006))
        new = price * math.exp(rnd.gauss(0, vol))
        out.append(Candle(1_700_000_000 + 900 * i, price, max(price, new), min(price, new), new, 100.0))
        price = new
    return out


def test_features_never_look_ahead():
    pytest.importorskip("sklearn")
    import numpy as np

    from tradebuddy.forecast import features

    candles = regime_bars(900)
    changed = candles[:800] + [Candle(c.time, c.open, c.high * 2, c.low, c.close * 2, c.volume * 9) for c in candles[800:]]
    np.testing.assert_array_equal(features(candles)[:800], features(changed)[:800])  # the future changed; the past did not


def test_forecaster_trains_validates_saves_and_predicts(tmp_path):
    pytest.importorskip("sklearn")
    from tradebuddy.forecast import ForecastConfig, Forecaster

    candles = regime_bars(4000)
    model = Forecaster.train("BTCUSD", candles, cfg=ForecastConfig(min_rows=1000))
    card = model.card
    assert card.rows_test > 0 and card.train_to > card.train_from
    for name in ("realized_vol", "abs_move", "return"):  # skill means beating the naive baseline by 5% on unseen data
        assert card.skill[name] == (card.metrics[f"{name}_mae"] <= 0.95 * card.baselines[f"{name}_mae"])
    assert card.metrics["direction_auc"] < 0.62  # a random walk has no direction to find
    assert "oi_chg_4" not in card.features and "rv_96" in card.features  # no OI series given: fitted without it
    path = model.save(tmp_path)
    again = Forecaster.load(path)
    f = again.predict(candles[-500:])
    assert set(f) >= {"up_probability", "predicted_realized_vol", "breakout_probability", "skill"}
    assert f["up_probability"] + f["down_probability"] == pytest.approx(1)
    with pytest.raises(ValueError):
        again.predict(candles[-100:])  # not enough history for the 4-day vol


# -- TradeBuddy AI ----------------------------------------------------------------------------


def mistral(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def answer(content, status=200, headers=None):
    return httpx.Response(status, headers=headers or {}, json={"model": "ministral-3b-2512", "choices": [{"message": {"content": json.dumps(content)}}]})


async def test_complete_parses_json_and_reports_limits():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return answer({"headline": "Calm."}, headers={"x-ratelimit-remaining-tokens-month": "999"})

    out, meta = await complete("k-123", "ministral-3b-2512", "sys", {"x": 1}, http=mistral(handler))
    assert out == {"headline": "Calm."} and meta["limits"] == {"x-ratelimit-remaining-tokens-month": "999"}
    assert seen["auth"] == "Bearer k-123" and seen["body"]["response_format"] == {"type": "json_object"}


async def test_rate_limit_errors_carry_retry_after_and_limits_but_never_the_key():
    def limited(request):
        return httpx.Response(429, headers={"retry-after": "120", "x-ratelimit-limit-req-minute": "2"}, json={"message": "Rate limit exceeded"})

    with pytest.raises(MistralError) as info:
        await complete("secret-key", "m", "sys", {"x": 1}, http=mistral(limited))
    err = info.value
    assert err.rate_limited and err.retry_after == 120 and err.limits["x-ratelimit-limit-req-minute"] == "2"
    assert "secret-key" not in str(err) and "429" in str(err)
    with pytest.raises(MistralError, match="JSON"):
        await complete("k", "m", "sys", {}, http=mistral(lambda r: httpx.Response(200, json={"choices": [{"message": {"content": "no json"}}]})))


async def test_check_says_whether_the_model_is_available():
    def handler(request):
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "ministral-3b-2512"}, {"id": "ministral-8b-2512"}, {"id": "mistral-small-latest"}]})
        return httpx.Response(429, headers={"x-ratelimit-remaining-req-minute": "0"}, json={"message": "Rate limit exceeded"})

    out = await check("k", "ministral-3b-2512", http=mistral(handler))
    assert out["model_listed"] and out["similar"] == ["ministral-3b-2512", "ministral-8b-2512"]
    assert (out["ok"], out["status"], out["step"]) == (False, 429, "completion") and out["limits"]["x-ratelimit-remaining-req-minute"] == "0"


def test_tbai_answers_are_cut_to_shape():
    raw = {"headline": "h" * 999, "health": "panic", "priorities": ["a", "b", "c", "d"],
           "sections": {"market": {"status": "act", "summary": "s", "points": list("abcdef"), "actions": ["x"]}, "extra": {}},
           "symbols": {"BTCUSD": {"summary": "btc"}, "DOGE": {"summary": "?"}}}
    out = tbai.clean(raw, ["BTCUSD", "ETHUSD"])
    assert len(out["headline"]) == 300 and out["health"] == "watch" and len(out["priorities"]) == 3
    assert set(out["sections"]) == set(tbai.SECTIONS) and out["sections"]["market"]["status"] == "act" and len(out["sections"]["market"]["points"]) == 4
    assert out["sections"]["system"] == {"status": "watch", "summary": "", "points": [], "actions": []}
    assert list(out["symbols"]) == ["BTCUSD"]


# -- the analyst ------------------------------------------------------------------------------


class CandleClient:
    def __init__(self, candles):
        self.candles_by = candles
        self.asked = []

    async def candles(self, symbol, resolution, count):
        self.asked.append(symbol)
        return self.candles_by[-count:]


def make_analyst(tmp_path, settings, published, digest=None):
    client = CandleClient(bars(700))
    live = summarize("BTC", book_quotes(expiry=time.time() + 86_400), SPOT)
    a = Analyst(lambda: client, lambda: settings[0], published.append, lambda s: live if s == "BTCUSD" else None, lambda s: None, model_dir=tmp_path, digest=digest,
                 bundled_models=tmp_path / "none")
    return a, client


async def test_analyst_publishes_one_analysis_per_symbol(tmp_path):
    published = []
    a, client = make_analyst(tmp_path, [Settings()], published)
    await a.cycle()
    assert [e.symbol for e in published] == ["BTCUSD", "ETHUSD"]
    btc = published[0]
    assert btc.context["model"]["status"] == "not_trained" and btc.forecast is None and btc.ai is None
    assert btc.playbook["strategy"] == "NO_TRADE" and btc.insights
    assert set(client.asked) == {"BTCUSD", "OI:BTCUSD", "FUNDING:BTCUSD", "ETHUSD", "OI:ETHUSD", "FUNDING:ETHUSD"}


async def test_tbai_one_request_per_interval_with_the_engine_digest(tmp_path):
    published, sent, digests = [], [], []

    async def digest(include_account):
        digests.append(include_account)
        return {"as_of": 1, "trading": {"strategies": []}, "portfolio": {"shared": include_account}, "system": {"feed": {}}}

    def handler(request):
        sent.append(json.loads(request.content))
        return answer({"headline": "All quiet", "health": "good", "sections": {"system": {"status": "good", "summary": "ok"}}, "symbols": {"BTCUSD": {"summary": "s"}}})

    settings = [apply_changes(Settings(), {"ai_enabled": True, "mistral_api_key": "mk-0123456789", "ai_interval_minutes": 15, "ai_share_account": False})]
    a, _ = make_analyst(tmp_path, settings, published, digest)
    a.http = mistral(handler)
    await a.cycle()
    report = next(e for e in published if isinstance(e, AIReport))
    assert report.ok and report.report["headline"] == "All quiet" and report.shared_account is False and digests == [False]
    btc = next(e for e in published if isinstance(e, MarketAnalysis) and e.symbol == "BTCUSD")
    assert btc.ai["summary"] == "s"
    assert "mk-0123456789" not in json.dumps(sent[0])  # the key goes in a header, never in the body
    user = json.loads(sent[0]["messages"][1]["content"])
    assert user["portfolio"] == {"shared": False} and set(user["market"]) == {"BTCUSD", "ETHUSD"}

    published.clear()
    await a.cycle()  # 5 minutes later in real life: inside the 15-minute interval, no request
    assert len(sent) == 1 and not any(isinstance(e, AIReport) for e in published)


async def test_tbai_backs_off_after_a_rate_limit_and_keeps_the_last_review(tmp_path):
    published, calls = [], []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            return answer({"headline": "first", "symbols": {"BTCUSD": {"summary": "kept"}}})
        return httpx.Response(429, headers={"retry-after": "900"}, json={"message": "Rate limit exceeded"})

    settings = [apply_changes(Settings(), {"ai_enabled": True, "mistral_api_key": "mk-0123456789"})]
    a, _ = make_analyst(tmp_path, settings, published)
    a.http = mistral(handler)
    now, results = time.time(), {"BTCUSD": {"context": {}, "insights": [], "forecast": None, "playbook": None}}
    await a._tbai(settings[0], results, now=now)
    reviews = await a._tbai(settings[0], results, now=now + 300)  # next slot: 429
    failure = [e for e in published if isinstance(e, AIReport)][-1]
    assert not failure.ok and failure.status == 429 and failure.paused_until == pytest.approx(now + 300 + 900)
    assert reviews["BTCUSD"]["summary"] == "kept" and reviews["BTCUSD"]["stale"] is True and "429" in reviews["BTCUSD"]["error"]
    await a._tbai(settings[0], results, now=now + 900)  # still paused: no request
    assert len(calls) == 2
    assert a.status()["ai_paused_until"] is not None


# -- engine, API, settings ----------------------------------------------------------------------


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


def test_the_mistral_key_is_a_secret_but_not_a_delta_route():
    s = apply_changes(Settings(), {"ai_enabled": True, "mistral_api_key": "mk-abcdefghijkl"})
    assert s.public()["mistral_api_key"] == "••••ijkl" and "mk-abcdefghijkl" not in json.dumps(s.public())
    assert "mistral_api_key" not in DELTA_ROUTING_FIELDS  # changing it never stops Delta trading
    assert apply_changes(s, {"mistral_api_key": ""}).mistral_api_key == "mk-abcdefghijkl"  # blank keeps the key


async def test_changing_the_ai_key_leaves_delta_trading_alone(cfg, exchange):
    s = System(cfg, strategies=[AlwaysBuy()], stream_factory=IdleStream, client_factory=exchange.client)
    s.set_toggle("trading:delta", True)
    await s.update_settings({"ai_enabled": True, "mistral_api_key": "mk-abcdefghijkl"})
    assert s.trading_on("delta") is True


# -- jobs ----------------------------------------------------------------------------------------


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



async def test_the_ai_digest_has_numbers_not_secrets(cfg, exchange):
    s = System(cfg, strategies=[AlwaysBuy()], stream_factory=IdleStream, client_factory=exchange.client)
    await s.update_settings({"delta_api_key": "dk-SECRET-123", "delta_api_secret": "ds-SECRET-456", "mistral_api_key": "mk-SECRET-789", "delta_active": True})
    api = Api(s)
    full = await api.ai_digest(include_account=True)
    text = json.dumps(full, default=str)
    assert "SECRET" not in text and "client_order_id" not in text
    assert set(full) == {"as_of", "system", "trading", "portfolio"} and full["portfolio"]["shared"] is True
    assert {a["broker"] for a in full["portfolio"]["accounts"]} == {"paper", "delta"}
    private = await api.ai_digest(include_account=False)
    assert private["portfolio"] == {"shared": False} and "open_positions" not in private["trading"]


async def test_ai_report_keeps_the_last_good_one(cfg, exchange):
    s = System(cfg, strategies=[AlwaysBuy()], stream_factory=IdleStream, client_factory=exchange.client)
    good = AIReport(ok=True, model="ministral-3b-2512", report={"headline": "fine", "health": "good"})
    await s.on_ai_report(good)
    await s.record(good)
    bad = AIReport(ok=False, model="ministral-3b-2512", error="Mistral answered HTTP 429", status=429)
    await s.on_ai_report(bad)
    await s.record(bad)
    r = await Api(s).ai_report()
    assert r["report"]["report"]["headline"] == "fine" and r["latest"]["status"] == 429
    assert [h["ok"] for h in r["history"]] == [False, True] and bad.level == "warning"
    with pytest.raises(Exception, match="no Mistral API key"):
        await Api(s).test_ai()


def test_the_tbai_page_and_model_ids():
    from tradebuddy.app import PAGES

    assert ("ai", "/ai") in [(p[0], p[1]) for p in PAGES]
    assert Settings().mistral_model == "ministral-3b-2512"
    assert apply_changes(Settings(), {"mistral_model": "mistral-large-2411"}).mistral_model == "mistral-large-2411"
    with pytest.raises(ValueError):
        apply_changes(Settings(), {"mistral_model": "rm -rf /"})


# -- saved TB-AI reports ---------------------------------------------------------------------------


def _report(ts: float, ok: bool = True, headline: str = "fine") -> dict:
    e = AIReport(ok=ok, model="ministral-3b-2512", report={"headline": headline, "health": "watch", "sections": {}} if ok else None,
                 error="" if ok else "Mistral answered HTTP 429", status=None if ok else 429)
    return e.to_dict() | {"ts": ts}


def test_every_ai_report_is_saved_in_full_and_found_by_day():
    from tradebuddy.store import Store

    store, day = Store(":memory:"), 1_780_000_000.0
    for n, ok in enumerate((True, False, True)):
        store.record_ai_report(_report(day + 300 * n, ok, f"report {n}"))
    store.record_ai_report(_report(day + 86_400, True, "next day"))
    store.record_ai_report(_report(day, True, "duplicate"))  # same moment: recorded once

    rows = store.ai_reports(day, day + 86_400)
    assert [r["headline"] for r in rows] == ["report 2", None, "report 0"] and [r["ok"] for r in rows] == [True, False, True]
    assert "data" not in rows[0]  # the list is summaries; the body comes one at a time
    assert [r["headline"] for r in store.ai_reports(day, day + 86_400, ok_only=True)] == ["report 2", "report 0"]

    first = store.ai_report(rows[-1]["id"])
    assert first["report"]["headline"] == "report 0" and first["older_id"] is None
    assert store.ai_report(first["newer_id"])["report"]["headline"] == "report 2"  # skips the failed attempt
    assert store.ai_report(9999) is None


def test_old_ai_reports_are_pruned_failed_ones_sooner():
    from tradebuddy.store import AI_FAILED_DAYS, AI_REPORT_DAYS, Store

    store, now = Store(":memory:"), 1_780_000_000.0
    store.record_ai_report(_report(now - (AI_REPORT_DAYS + 1) * 86_400))
    store.record_ai_report(_report(now - (AI_FAILED_DAYS + 1) * 86_400, ok=False))
    store.record_ai_report(_report(now - (AI_FAILED_DAYS + 1) * 86_400 + 1))
    assert store.prune_ai_reports(now) == 2
    assert [r["ok"] for r in store.ai_reports()] == [True]


def test_reports_from_the_event_log_are_copied_once(tmp_path):
    from tradebuddy.store import Store

    path = str(tmp_path / "tb.db")
    old = Store(path)
    old.db.execute("DELETE FROM ai_reports")
    old.record_event(_report(1_780_000_000.0, headline="from before the table"))
    assert old.ai_reports() == []
    again = Store(path)
    assert [r["headline"] for r in again.ai_reports()] == ["from before the table"]


async def test_the_engine_saves_each_report_and_serves_it(cfg, exchange):
    s = System(cfg, strategies=[AlwaysBuy()], stream_factory=IdleStream, client_factory=exchange.client)
    await s.on_ai_report(AIReport(ok=True, model="ministral-3b-2512", report={"headline": "saved", "health": "good"}))
    api = Api(s)
    [row] = await api.ai_history()
    full = await api.ai_report_at(row["id"])
    assert full["report"]["headline"] == "saved" and full["id"] == row["id"]
    assert (await api.ai_report())["saved"]["count"] == 1
    with pytest.raises(Exception, match="no saved AI report"):
        await api.ai_report_at(row["id"] + 1)
    assert {"ai_history", "ai_report_at"} <= set(METHODS)


def test_a_model_trained_here_wins_over_the_bundled_one(tmp_path):
    from tradebuddy.analyst import BUNDLED_MODELS, model_path

    here, bundled = tmp_path / "data_models", tmp_path / "bundled"
    here.mkdir(), bundled.mkdir()
    assert model_path(here, "BTCUSD", bundled) == (None, "")
    (bundled / "forecast_BTCUSD.joblib").write_bytes(b"x")
    assert model_path(here, "BTCUSD", bundled) == (bundled / "forecast_BTCUSD.joblib", "bundled")
    (here / "forecast_BTCUSD.joblib").write_bytes(b"x")
    assert model_path(here, "BTCUSD", bundled) == (here / "forecast_BTCUSD.joblib", "trained here")
    # the repo ships models for both symbols, with their cards
    assert {p.name for p in BUNDLED_MODELS.glob("forecast_*")} >= {f"forecast_{s}.{x}" for s in ("BTCUSD", "ETHUSD") for x in ("joblib", "json")}
