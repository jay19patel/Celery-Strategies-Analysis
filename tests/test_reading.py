"""The Market page's tabs: volume, indicators and candle shapes.

Each tab is a reading of the same bars, split by what it is made of. These check the readings are
right and — more important — that none of them invents an edge where there is none.
"""

from __future__ import annotations

import pytest

from tradebuddy import reading, verdict
from tradebuddy import structure as mkt
from tradebuddy.api import Api
from tradebuddy.delta import Candle
from tradebuddy.strategies.indicators import adx, bollinger, macd, obv

from .test_outlook import walk
from .test_pipeline import make

STEP = 900
T = 1_700_000_000


def bar(o, h, lo, c, i=0, v=10.0):
    return Candle(T + i * STEP, o, h, lo, c, v)


# -- the indicator primitives ----------------------------------------------------------


def test_adx_separates_a_trend_from_a_range():
    """The one reading that says whether the others mean anything. A clean ramp is a trend; a flat
    line is not, and anything in between is what the thresholds are for."""
    rise = [100.0 + i for i in range(200)]
    flat = [100.0] * 200
    assert adx([x * 1.001 for x in rise], [x * 0.999 for x in rise], rise)[0][-1] > 50
    assert adx(flat, flat, flat)[0][-1] < 5


def test_macd_histogram_turns_with_momentum():
    """Momentum, not direction. A straight ramp rises forever at a constant rate, and its histogram
    is zero — correctly, because nothing is accelerating. It takes a change of pace to move it."""
    steady = [100.0 + i for i in range(120)]
    line, signal, hist = macd(steady)
    assert len(line) == len(signal) == len(hist)
    assert hist[-1] == pytest.approx(0, abs=1e-6)

    faster = [*steady, *[steady[-1] + i * 4 for i in range(1, 40)]]
    assert macd(faster)[2][-1] > 0  # the climb steepened
    turn = [*steady, *[steady[-1] - i * 3 for i in range(1, 40)]]
    assert macd(turn)[2][-1] < 0


def test_bollinger_bands_bracket_the_price():
    closes = [100.0 + (i % 7) for i in range(80)]
    mid, up, low = bollinger(closes, 20, 2.0)
    assert low[-1] < mid[-1] < up[-1]
    assert min(closes[-20:]) >= low[-1] - 1e-9 and max(closes[-20:]) <= up[-1] + 1e-9


def test_obv_adds_volume_on_up_closes_and_takes_it_off_on_down():
    closes = [100.0, 101.0, 100.0, 100.0]
    assert obv(closes, [0.0, 10.0, 4.0, 99.0]) == [0.0, 10.0, 6.0, 6.0]  # an unchanged close moves nothing


# -- candle shapes ---------------------------------------------------------------------


@pytest.mark.parametrize(("want", "bars"), [
    ("hammer", [bar(100, 100.4, 97, 100.2)]),
    ("shooting_star", [bar(100, 103, 99.8, 100.2)]),
    ("doji", [bar(100, 101, 99, 100.02)]),
    ("marubozu_bull", [bar(100, 102.05, 99.98, 102)]),
    ("bullish_engulfing", [bar(101, 101.2, 100, 100.1, 0), bar(99.9, 102.5, 99.8, 102.2, 1)]),
    ("bearish_engulfing", [bar(100, 101.2, 99.9, 101.1, 0), bar(101.3, 101.5, 99, 99.2, 1)]),
    ("inside", [bar(100, 103, 97, 101, 0), bar(100.5, 101.5, 99.5, 100.8, 1)]),
    ("outside", [bar(100, 101, 99.5, 100.5, 0), bar(100.2, 103, 98, 102.5, 1)]),
])
def test_each_named_shape_is_found(want, bars):
    assert want in [s.name for s in mkt.find_shapes(bars)]


def test_the_specific_shape_wins_over_the_general_one():
    """A hammer has a small body and would also pass the doji test. Labelling one bar both "bull"
    and "neutral" is the contradiction this page exists to remove."""
    names = [s.name for s in mkt.find_shapes([bar(100, 100.4, 97, 100.2)])]
    assert "hammer" in names and "doji" not in names


def test_a_shape_record_finds_no_edge_in_noise():
    """The point of counting rather than naming. On a random walk every shape must land on its
    baseline — a machine that finds an edge here would find one anywhere."""
    record = reading.shape_record(walk(20_000, seed=41), 1.0)
    assert record, "a year of bars should produce shapes to score"
    for row in record:
        if row["enough"]:
            assert abs(row["edge"]) < 0.08, f"{row['label']} claims an edge on noise: {row['edge']}"


def test_a_neutral_shape_is_asked_whether_the_market_moved_at_all():
    """A doji claims indecision, not a direction, so "did it go up" is the wrong question."""
    record = {r["name"]: r for r in reading.shape_record(walk(20_000, seed=42), 1.0)}
    assert record["doji"]["asks"] == "moved more than usual"
    assert record["doji"]["direction"] == "neutral"
    assert record["bullish_engulfing"]["asks"] == "went bull"


# -- the blocks -------------------------------------------------------------------------


def test_volume_is_reported_as_a_ratio_not_a_raw_figure():
    """400 contracts means nothing on its own, and nothing on this tab is quoted that way."""
    b = reading.volume_block(walk(2_000, seed=43))
    assert b["ready"] and b["normal"] > 0
    assert b["last_ratio"] == pytest.approx(b["last_bar"] / b["normal"], abs=0.005)  # rounded for display
    assert 0 <= b["up_share"] <= 1 and b["up_share"] + b["down_share"] == pytest.approx(1.0, abs=0.001)
    assert len(b["series"]) == reading.VOLUME_WINDOW


def test_volume_says_so_when_there_is_not_enough_of_it():
    b = reading.volume_block(walk(10, seed=44))
    assert b["ready"] is False and "needs" in b["note"]


def test_indicators_carry_the_state_not_just_the_number():
    """"RSI 68" is only useful if you remember where the lines are."""
    b = reading.indicator_block(walk(2_000, seed=45))
    assert b["ready"]
    assert b["rsi"]["state"] in ("oversold", "neutral", "overbought")
    assert b["adx"]["state"] in ("ranging", "forming", "trending")
    assert b["macd"]["state"] in ("rising", "falling", "unknown")
    assert len(b["rsi_series"]) and len(b["adx_series"])


def test_a_ranging_market_is_called_a_range_whatever_else_is_true():
    """Trend readings in a range are the most reliably wrong thing on a chart."""
    flat = [Candle(T + i * STEP, 100, 100.3, 99.7, 100 + (i % 3) * 0.01, 10) for i in range(300)]
    v = verdict.indicators(reading.indicator_block(flat))
    assert v.stance in ("range", "vol_up")  # a range, or a range about to break
    assert "ADX" in v.detail or "range" in v.detail.lower()


# -- one verdict per tab ------------------------------------------------------------------


async def test_every_tab_carries_its_own_verdict(cfg, exchange):
    """A tab with no verdict is a tab the reader has to work out for themselves, which is what the
    page looked like before it had tabs at all."""
    system = await make(cfg, exchange)
    system.store.record_bars("BTCUSD", "15m", walk(20_000, seed=46))
    body = await Api(system).verdicts("BTCUSD")
    keys = {v["key"] for v in body["verdicts"]}
    assert {"shapes", "volume", "indicators", "chain_oi", "outlook", "insights"} <= keys
    for v in body["verdicts"]:
        assert v["source"] in ("math", "counted", "implied", "model", "ai")
        assert v["label"] and v["detail"]


async def test_the_view_carries_a_block_for_every_tab(cfg, exchange):
    system = await make(cfg, exchange)
    system.store.record_bars("BTCUSD", "15m", walk(20_000, seed=47))
    view = await Api(system).outlook("BTCUSD")
    assert view["volume"]["ready"] and view["indicators"]["ready"]
    assert "latest" in view["shapes"] and "record" in view["shapes"]
    assert view["summary"] and view["horizons"]  # the headline above the tabs


def test_the_volume_verdict_never_claims_a_direction_from_size_alone():
    """Volume cannot say which way price goes. Where it leans, it is because the size itself was
    one-sided — and it says that, rather than implying the move is confirmed."""
    quiet = reading.volume_block(walk(2_000, seed=48))
    v = verdict.volume(quiet)
    assert v.stance in ("bullish", "bearish", "neutral", "range")
    assert v.measure in ("direction", "range")
    assert "volume" in v.detail.lower() or "size" in v.detail.lower()


# -- an empty database must not make the page silent ------------------------------------


async def test_the_payload_carries_every_tabs_block_even_with_no_bars(cfg, exchange):
    """A short dict here left four panels with no data and nothing to say, which is how the whole
    page came up blank. An empty database and a quiet market take the same path through the page."""
    system = await make(cfg, exchange)
    view = await Api(system).outlook("BTCUSD")

    assert view["ready"] is False and "backfill" in view["hint"]
    for key in ("volume", "indicators", "shapes", "summary", "horizons", "timeframes", "sources", "alignment"):
        assert key in view, f"{key} missing when there are no bars"
    assert view["volume"]["ready"] is False and view["volume"]["note"]
    assert view["indicators"]["ready"] is False
    assert view["shapes"] == {"latest": [], "record": []}
    assert view["sources"]  # the legend still renders


async def test_the_verdicts_route_survives_an_empty_database(cfg, exchange):
    """Every tab still gets a verdict; they just say there is nothing to say."""
    system = await make(cfg, exchange)
    body = await Api(system).verdicts("BTCUSD")
    assert {v["key"] for v in body["verdicts"]} >= {"term_structure", "chain_oi", "insights"}
    assert all(v["label"] for v in body["verdicts"])
    assert body["record"]["cards"] == [] and body["history"] == []
