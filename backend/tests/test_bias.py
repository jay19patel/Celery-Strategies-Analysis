"""The day's bias: each part on a day built to show it, the style, and the record that marks it."""

from __future__ import annotations

import time
from typing import Any

from sqlmodel import select

from app.analysis import bias
from app.core.db import session
from app.core.models import Bar, DailyBias
from app.market.delta import Candle

DAY = 86_400
TODAY = 1_800_000_000 // DAY * DAY


def history(today_open: float, today_close: float, days: int = 12) -> list[Candle]:
    """Quiet days between 99 and 101 (value about 99.5-100.5), then today from `today_open`."""
    out = []
    for i in range((days - 1) * 96):
        t = TODAY - (days - 1) * DAY + i * 900
        mid = 100 + (0.5 if i % 4 < 2 else -0.5)
        out.append(Candle(t, mid, mid + 0.6, mid - 0.6, mid, 10.0))
    step = (today_close - today_open) / 8
    for j in range(8):
        o = today_open + j * step
        out.append(Candle(TODAY + j * 900, o, o + abs(step) + 0.1, o - 0.1, o + step, 10.0))
    return out


def test_an_open_above_value_held_above_the_poc_reads_bullish_and_trending() -> None:
    out = bias.read(history(103, 104), TODAY + 8 * 900, gex={"ready": True, "regime": "positive", "flip": 98.0})
    assert out["ready"] and out["label"] == "bullish" and out["score"] >= 0.25
    texts = " ".join(r["text"] for r in out["reasons"])
    assert "above yesterday's value" in texts and "above yesterday's point of control" in texts
    assert out["style"] == "trend"  # opened outside value: follow it, even in positive gamma
    assert out["levels"]["flip"] == 98.0 and out["levels"]["val"] < out["levels"]["poc"] < out["levels"]["vah"]


def test_an_open_inside_value_in_positive_gamma_is_a_range_day() -> None:
    out = bias.read(history(100, 100.2), TODAY + 8 * 900, gex={"ready": True, "regime": "positive"})
    assert out["style"] == "range" and out["label"] == "neutral"


def test_the_tape_and_the_option_book_add_their_lean_only_when_they_have_one() -> None:
    base = bias.read(history(97, 96), TODAY + 8 * 900)
    assert base["label"] == "bearish"
    selling = bias.read(history(97, 96), TODAY + 8 * 900, cvd=(-300.0, 1_000.0), options={"put_bought": 900.0, "call_bought": 100.0})
    assert selling["score"] < base["score"]
    assert any("net selling" in r["text"] for r in selling["reasons"])
    assert any("leaned to puts" in r["text"] for r in selling["reasons"])
    balanced = bias.read(history(97, 96), TODAY + 8 * 900, cvd=(10.0, 1_000.0))
    assert balanced["score"] == base["score"]  # 1% net is not a lean


def test_without_yesterday_it_says_so() -> None:
    assert not bias.read(history(100, 100, days=1), TODAY + 8 * 900)["ready"]


def test_a_day_is_marked_on_its_own_close_and_neutral_is_not_scored() -> None:
    assert bias.settle("bullish", 100, 102) == (0.020000000000000018, True)
    assert bias.settle("bearish", 100, 102)[1] is False
    assert bias.settle("neutral", 100, 102)[1] is None


async def test_the_desk_records_a_day_once_and_marks_it_after(env: Any) -> None:
    from app.analysis.service import MarketDesk

    desk = MarketDesk(env.feed, env.settings, env.hub)
    view = await desk.bias("BTC")
    assert view["ready"], view.get("note")
    now = time.time()
    today = int(now // DAY * DAY)
    if now - today < 1800:  # the record waits half an hour into the day; the test does not wait with it
        return
    quotes = env.feed.book
    env.feed.book = {}
    await desk.record_bias()  # the chain is not live yet: nothing is written that would lack its gamma
    with session() as s:
        assert s.exec(select(DailyBias)).all() == []
    env.feed.book = quotes
    await desk.record_bias()
    await desk.record_bias()
    with session() as s:
        [row] = s.exec(select(DailyBias)).all()
        row.day -= DAY  # pretend it was yesterday's, then let the next pass mark it
        s.add(row)
        s.add(Bar(symbol="BTCUSD", interval="15m", time=row.day + 3_600, open=1, high=1, low=1, close=row.open * 1.01, volume=1))
        s.commit()
    await desk.record_bias()
    record = desk.bias_record("BTC")
    marked = [d for d in record["days"] if d["close"] is not None]
    assert marked and marked[0]["ret_pct"] is not None
