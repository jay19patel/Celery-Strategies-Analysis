"""The tape, the readings built on it, and gamma exposure — each on data whose answer is known."""

from __future__ import annotations

import json
import math
import time
from typing import Any

import pytest
from sqlmodel import select

from app.analysis import flow
from app.core.db import session
from app.core.models import FlowBar, OptionsHistory
from app.market.feed import MarketFeed
from app.market.options import OptionQuote
from app.market.tape import Tape
from app.options import gex

T0 = 1_800_000_000 // 60 * 60


# -- the tape ---------------------------------------------------------------------------------------


def test_trades_fold_into_one_footprint_a_minute_and_bad_prints_are_left_out() -> None:
    tape = Tape()
    tape.trade("BTCUSD", 80_000, 5, True, T0 + 1)
    tape.trade("BTCUSD", 80_012, 3, True, T0 + 2)
    tape.trade("BTCUSD", 79_995, 4, False, T0 + 3)
    tape.trade("BTCUSD", 0.05, 900, False, T0 + 4)  # the empty book's print
    tape.book("BTCUSD", [(79_990, 10), (79_000, 99)], [(80_010, 4), (81_000, 99)])
    tape.trade("BTCUSD", 80_020, 1, True, T0 + 61)  # the next minute closes the first
    [bar] = tape.closed(T0 + 61)
    assert (bar["open"], bar["high"], bar["low"], bar["close"]) == (80_000, 80_012, 79_995, 79_995)
    assert (bar["buy"], bar["sell"], bar["volume"], bar["trades"], bar["max_trade"]) == (8, 4, 12, 3, 5)
    assert bar["levels"] == [[79_990.0, 0.0, 4.0], [80_000.0, 5.0, 0.0], [80_010.0, 3.0, 0.0]]
    assert (bar["bid_depth"], bar["ask_depth"]) == (10, 4)  # only what rests within 0.5% of the mid
    assert tape.rejected == 1
    assert tape.closed(T0 + 61) == []  # each minute once
    assert [b["time"] for b in tape.closed(T0 + 200)] == [T0 + 60]  # a quiet symbol's last minute still closes


def test_option_premium_is_counted_by_kind_and_side_and_taken_once() -> None:
    tape = Tape()
    tape.option_trade("BTC", "call", 400.0, 0.5, True)
    tape.option_trade("BTC", "put", 100.0, 1.0, False)
    flow_now = tape.take_options("BTC")
    assert (flow_now.call_buy, flow_now.put_sell, flow_now.trades) == (200.0, 100.0, 2)
    assert tape.take_options("BTC").trades == 0


def test_the_feed_routes_the_tape_and_reads_the_heavy_book_sparingly() -> None:
    feed = MarketFeed("http://stub", "ws://stub", ["BTC"])
    book = json.dumps({"buy": [{"limit_price": "79990", "size": 10}], "sell": [{"limit_price": "80010", "size": 4}],
                       "symbol": "BTCUSD", "type": "l2_orderbook"}, separators=(",", ":"))
    assert feed.wanted(book) and not feed.wanted(book)  # one look per symbol every few seconds
    assert feed.wanted('{"type":"v2/ticker","symbol":"BTCUSD"}')
    now = time.time()
    feed.handle({"type": "all_trades_snapshot", "symbol": "BTCUSD", "trades": [{"price": "80000", "size": 9, "buyer_role": "taker"}]})
    feed.handle({"type": "all_trades", "symbol": "BTCUSD", "price": "80000", "size": 7, "buyer_role": "taker", "timestamp": now * 1e6})
    feed.handle({"type": "all_trades", "symbol": "C-BTC-81000-051126", "price": "266", "size": 10, "buyer_role": "taker"})
    feed.handle({"type": "all_trades", "symbol": "C-ETH-2400-051126", "price": "20", "size": 10, "buyer_role": "taker"})  # not traded here
    [bar] = feed.tape.closed(now + 120)
    assert bar["buy"] == 7  # the reconnect snapshot was not counted again
    assert feed.tape.take_options("BTC").call_buy == pytest.approx(266 * 10 * 0.001)
    assert feed.tape.take_options("ETH").trades == 0


# -- the readings -----------------------------------------------------------------------------------


def minute(t: int, o: float, h: float, lo: float, c: float, buy: float, sell: float, levels: list[list[float]] | None = None) -> dict[str, Any]:
    return {"time": t, "open": o, "high": h, "low": lo, "close": c, "volume": buy + sell, "buy": buy, "sell": sell,
            "trades": 10, "max_trade": 1.0, "levels": levels or [[o, buy, sell]], "bid_depth": 100.0, "ask_depth": 50.0}


def calm(n: int, start: int = T0, price: float = 100.0) -> list[dict[str, Any]]:
    return [minute(start + 60 * i, price, price + 1, price - 1, price + (0.2 if i % 2 else -0.2), 50, 50) for i in range(n)]


def test_minutes_fold_into_bars_with_their_footprint_merged() -> None:
    rows = [minute(T0, 100, 101, 99, 100.5, 5, 2, [[100, 5, 2]]), minute(T0 + 60, 100.5, 102, 100, 101, 1, 4, [[100, 1, 1], [101, 0, 3]])]
    [bar] = flow.fold(rows, 5)
    assert (bar["open"], bar["high"], bar["low"], bar["close"], bar["delta"]) == (100, 102, 99, 101, 0)
    assert bar["levels"] == [[100.0, 6.0, 3.0], [101.0, 0.0, 3.0]]
    assert bar["bid_depth"] == 100


def test_imbalances_are_read_on_the_diagonal_and_stack_into_zones() -> None:
    levels = [[100, 1, 2], [101, 9, 1], [102, 9, 1], [103, 9, 1], [105, 50, 0]]  # 104 never traded
    found = flow.imbalances(levels, step=1.0)
    buys = sorted(i["price"] for i in found if i["side"] == "buy")
    assert buys == [101, 102, 103]  # 105 against nothing at 104 is no diagonal
    assert flow.stacked(found, 1.0) == [{"side": "buy", "low": 101, "high": 103, "levels": 3}]


def test_selling_that_price_refused_is_read_as_absorption() -> None:
    rows = calm(100)
    t = T0 + 60 * 100
    # Five minutes of heavy selling into one bar that still closed up on a small range.
    rows += [minute(t + 60 * i, 100, 100.4, 99.8, 100.1, 40, 260) for i in range(5)]
    out = flow.read(rows, minutes=5, step=1.0)
    [event] = [e for e in out["events"] if e["kind"] == "absorption"]
    assert event["side"] == "bullish" and "absorbed" in event["detail"]


def test_a_climax_that_cannot_hold_is_read_as_exhaustion() -> None:
    rows = calm(100)
    t = T0 + 60 * 100
    rows += [minute(t + 60 * i, 100 + i, 102 + 2 * i, 100 + i, 101 + 2 * i, 300, 40) for i in range(5)]  # the buying climax to a new high
    rows += [minute(t + 300 + 60 * i, 106, 106, 100, 100.5, 30, 200) for i in range(5)]  # then back through its middle, sold
    out = flow.read(rows, minutes=5, step=1.0)
    assert any(e["kind"] == "exhaustion" and e["side"] == "bearish" for e in out["events"])


def test_a_new_high_on_less_buying_is_a_divergence() -> None:
    rows = []
    for i in range(400):  # a strong push up, then a weaker one to a slightly higher high
        up = 100 + i * 0.05 if i < 200 else 110 - (i - 200) * 0.04 if i < 300 else 106 + (i - 300) * 0.05
        rows.append(minute(T0 + 60 * i, up, up + 0.3, up - 0.3, up, 80 if i < 200 else 45, 40 if i < 200 else 60))
    out = flow.read(rows, minutes=5, step=1.0)
    assert out["divergence"] and out["divergence"]["side"] == "bearish"


def test_too_little_tape_says_so_rather_than_reading_noise() -> None:
    out = flow.read(calm(30), minutes=5, step=1.0)
    assert not out["ready"] and "tape is recorded from the day" in out["note"]


async def test_the_engine_stores_finished_minutes_and_the_desk_reads_them(env: Any) -> None:
    from app.analysis.service import MarketDesk

    now = time.time()
    rows = [r | {"symbol": "BTCUSD"} for r in calm(150, start=int(now // 60 * 60) - 150 * 60, price=80_000)]
    from app.analysis.service import store_flow_bars

    store_flow_bars(rows)
    with session() as s:
        assert len(s.exec(select(FlowBar)).all()) == 150
    out = await MarketDesk(env.feed, env.settings, env.hub).flow("BTC")
    assert out["ready"] and out["bars"] >= 30 and out["profile"]["ready"]


# -- gamma exposure ---------------------------------------------------------------------------------


def quote(kind: str, strike: float, oi: float, hours: float = 24.0, iv: float = 0.5) -> OptionQuote:
    now = time.time()
    return OptionQuote(symbol=f"{kind[0].upper()}-BTC-{strike:.0f}-X", underlying="BTC", kind=kind, strike=strike,
                       expiry=now + hours * 3600, mark=1.0, bid=0.9, ask=1.1, iv=iv, oi=oi, at=now)


def test_gex_signs_walls_and_the_flip_follow_the_book() -> None:
    now = time.time()
    calls_heavy = gex.exposure([quote("call", 82_000, 50), quote("put", 78_000, 5)], 80_000, now)
    assert calls_heavy["regime"] == "positive" and calls_heavy["call_wall"] == 82_000 and calls_heavy["put_wall"] == 78_000
    puts_heavy = gex.exposure([quote("call", 84_000, 5), quote("put", 79_000, 80)], 80_000, now)
    assert puts_heavy["regime"] == "negative"
    assert puts_heavy["flip"] is not None and puts_heavy["flip"] > 80_000  # rally far enough and the calls take over
    # The number itself: gamma x OI x spot^2 x 1%.
    g = gex.bs_gamma(80_000, 80_000, 0.5, 24.0)
    one = gex.exposure([quote("call", 80_000, 2)], 80_000, now)
    assert one["net"] == pytest.approx(g * 2 * 80_000**2 * 0.01, rel=1e-6)
    assert math.isclose(g, math.exp(0) / math.sqrt(2 * math.pi) / (80_000 * 0.5 * math.sqrt(24 / 8760)), rel_tol=1e-2)


def test_nothing_with_gamma_is_said_plainly() -> None:
    assert not gex.exposure([quote("call", 82_000, 0)], 80_000, time.time())["ready"]


async def test_each_minutes_options_row_carries_gex_and_the_premium_that_crossed(env: Any) -> None:
    from app.analysis.service import MarketDesk

    env.feed.tape.option_trade("BTC", "call", 300.0, 1.0, True)
    desk = MarketDesk(env.feed, env.settings, env.hub)
    await desk.record_options()
    with session() as s:
        [row] = s.exec(select(OptionsHistory)).all()
    assert row.call_bought == 300.0 and row.put_sold == 0.0
    assert row.net_gex is not None and row.gex_call_wall is not None


async def test_the_desk_serves_gex_by_strike_with_its_recorded_day(env: Any) -> None:
    from app.analysis.service import MarketDesk

    desk = MarketDesk(env.feed, env.settings, env.hub)
    await desk.record_options()
    out = await desk.gamma("BTC")
    assert out["ready"] and out["by_strike"] and out["regime"] in ("positive", "negative")
    assert len(out["history"]) == 1 and out["history"][0]["net"] == pytest.approx(out["net"], rel=1e-3)  # a moment apart
