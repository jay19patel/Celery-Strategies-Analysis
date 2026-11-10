"""The auction strategies, each on a context built to fire it and one built to refuse it."""

from __future__ import annotations

from typing import Any

from app.market.delta import Candle
from app.strategies.base import Context
from app.strategies.exhaustion_fade import ExhaustionFade
from app.strategies.flow_breakout import FlowBreakout
from app.strategies.sweep_absorption import SweepAbsorption
from app.strategies.value_area_return import ValueAreaReturn

T = 1_800_000_000
LEVELS = {"vah": 101.0, "val": 99.0, "poc": 100.0, "flip": None, "call_wall": 106.0, "put_wall": 94.0}


def bars(closes: list[float], step: int = 300) -> list[Candle]:
    return [Candle(T + i * step, c, c + 0.2, c - 0.2, c, 10.0) for i, c in enumerate(closes)]


def ctx(candles: list[Candle], **parts: Any) -> Context:
    return Context(symbol="BTCUSD", underlying="BTC", interval="5m", candles=candles, cost_pct=0.02, **parts)


def bias(**changes: Any) -> dict[str, Any]:
    return {"ready": True, "open": 100.0, "gamma": "positive", "style": "range", "label": "neutral", "levels": dict(LEVELS)} | changes


def test_value_area_return_trades_a_rejected_open_to_the_other_edge() -> None:
    c = bars([100.0] * 20 + [101.6, 100.6, 100.4], step=900)  # outside, then two closes back inside
    d = ValueAreaReturn().decide(ctx(c, bias=bias(open=102.0)))
    assert d.side == "sell" and d.target == 99.0 and d.stop > 100.8
    assert ValueAreaReturn().decide(ctx(c, bias=bias(open=102.0, gamma="negative"))).side is None
    assert ValueAreaReturn().decide(ctx(c, bias=bias(open=100.0))).side is None  # it never left value


def test_sweep_absorption_needs_both_the_sweep_and_the_absorption_at_it() -> None:
    c = bars([100.0] * 30)
    event = {"kind": "absorption", "side": "bullish", "time": c[-1].time, "price": 99.0, "detail": "selling absorbed at 99"}
    flow = {"ready": True, "events": [event]}
    swept = {"liquidity": {"sweeps": [{"direction": "low", "level": 99.1, "extreme": 99.0, "bar_time": c[-3].time}]},
             "nearby": {"above": [{"price": 102.0, "label": "pool"}]}}
    d = SweepAbsorption().decide(ctx(c, flow=flow, structure=swept))
    assert d.side == "buy" and d.stop < 99.0 and d.target == 102.0
    unswept = {"liquidity": {"sweeps": []}, "nearby": {}}
    assert SweepAbsorption().decide(ctx(c, flow=flow, structure=unswept)).side is None
    assert SweepAbsorption().decide(ctx(c, flow={"ready": True, "events": []}, structure=swept)).side is None


def test_exhaustion_is_faded_only_at_one_of_the_days_levels() -> None:
    c = bars([100.0] * 29 + [101.0])
    at_vah = {"kind": "exhaustion", "side": "bearish", "time": c[-1].time, "price": 101.2, "detail": "buying climax at 101.2"}
    d = ExhaustionFade().decide(ctx(c, flow={"ready": True, "events": [at_vah], "profile": {}}, bias=bias()))
    assert d.side == "sell" and d.target == 100.0 and d.stop > 101.2
    nowhere = at_vah | {"price": 103.5}
    assert ExhaustionFade().decide(ctx(c, flow={"ready": True, "events": [nowhere], "profile": {}}, bias=bias())).side is None


def test_a_breakout_is_followed_only_on_a_trend_day_with_the_tape_behind_it() -> None:
    c = bars([100.0] * 28 + [100.9, 101.4])
    flow = {"ready": True, "delta": 500.0, "stacked": [{"side": "buy", "low": 101.0, "high": 101.4, "levels": 3}], "divergence": None}
    d = FlowBreakout().decide(ctx(c, flow=flow, bias=bias(style="trend")))
    assert d.side == "buy" and d.stop < 101.0 and d.target == 106.0  # the call wall pays
    assert FlowBreakout().decide(ctx(c, flow=flow, bias=bias(style="range"))).side is None
    thin = flow | {"delta": -50.0}
    assert FlowBreakout().decide(ctx(c, flow=thin, bias=bias(style="trend"))).side is None


def test_each_says_why_it_waits_without_a_tape_or_a_bias() -> None:
    c = bars([100.0] * 30)
    assert "tape" in (SweepAbsorption().decide(ctx(c, flow={"ready": False, "note": "needs 21 bars of 5m tape"})).reason)
    assert ValueAreaReturn().decide(ctx(c, bias={"ready": False, "note": "needs yesterday"})).reason == "needs yesterday"
