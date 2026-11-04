"""The Market page's tabs: volume, indicators and candle shapes.

Each tab is a reading of the same bars, split by what it is made of. These check the readings are
right and — more important — that none of them invents an edge where there is none.
"""

from __future__ import annotations

import pytest

from app.analysis import reading
from app.analysis import structure as mkt
from app.market.delta import Candle
from app.market.indicators import macd, obv

from .test_outlook import walk

STEP = 900
T = 1_700_000_000


def bar(o, h, lo, c, i=0, v=10.0):
    return Candle(T + i * STEP, o, h, lo, c, v)


# -- the indicator primitives ----------------------------------------------------------


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


# -- the levels everyone is watching, and whether anyone turned up ----------------------


def level_bars(n: int = 120, *, last_volume: float = 10.0) -> list[Candle]:
    """A flat series with a controllable last bar's volume."""
    out = [Candle(1_700_006_400 + i * 900, 100, 101, 99, 100, 10.0) for i in range(n - 1)]
    out.append(Candle(1_700_006_400 + (n - 1) * 900, 100, 101, 99, 100.9, last_volume))
    return out


def test_a_level_is_in_play_only_when_price_is_near_it():
    """"In play" is a statement about where price is, not about what it will do."""
    block = reading.levels_block(level_bars())
    today = next(w for w in block["windows"] if w["key"] == "1d")
    assert today["high"] == 101 and today["low"] == 99
    assert "high" in today["in_play"]  # 100.9 is 0.1% under 101
    assert block["price"] == 100.9


def test_the_volume_ratio_is_reported_beside_the_level_not_mixed_into_it():
    """A level reached on ordinary volume is price drifting into it; the same level on twice the
    average is somebody taking it. Both numbers have to be visible for the difference to be."""
    quiet = reading.levels_block(level_bars(last_volume=3.0))
    busy = reading.levels_block(level_bars(last_volume=30.0))
    assert quiet["volume_ratio"] == pytest.approx(0.3, abs=0.01)
    assert busy["volume_ratio"] == pytest.approx(3.0, abs=0.01)
    # The level itself is identical in both — the volume never moves it.
    assert quiet["windows"] == busy["windows"]


def test_where_price_sits_between_the_extremes_is_a_share_of_the_range():
    mid = [Candle(1_700_006_400 + i * 900, 100, 102, 98, 100, 10.0) for i in range(60)]
    block = reading.levels_block(mid)
    today = next(w for w in block["windows"] if w["key"] == "1d")
    assert today["position"] == pytest.approx(0.5, abs=0.01)  # 100 between 98 and 102


def test_it_refuses_rather_than_averaging_over_nothing():
    assert reading.levels_block([])["ready"] is False
    assert reading.levels_block(level_bars(5))["ready"] is False


def test_every_window_names_the_bars_it_counted():
    """A 52-week high measured over nine days is not a 52-week high, and the only way to tell is
    for the window to say how many bars it actually had."""
    block = reading.levels_block(level_bars(120))
    for w in block["windows"]:
        assert w["bars"] <= 120
        assert w["bars"] > 0
