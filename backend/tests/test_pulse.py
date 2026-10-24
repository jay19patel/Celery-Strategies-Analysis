"""Is anything happening — and the discipline about which of the three questions has an answer.

The tests that matter most here are the refusals. A module that detects big moves is easy; one
that detects them and then declines to say which way they are going, because counting says it does
not know, is the part that had to be built carefully.
"""

from __future__ import annotations

import math

import pytest

from app.analysis import outlook as ol
from app.analysis import pulse
from app.market.delta import Candle


def calm(n=400, price=100.0, span=0.22, volume=700.0, close_at=0.5):
    """An ordinary market with an ordinary bar on the end.

    Deliberately not a flat line: with every bar identical the realised volatility is zero, so
    there is nothing to price a horizon with, and every bar's range is exactly one ATR — which is
    the boundary the classifier reads as "wide". A fixture that degenerate tests the fixture.

    `close_at` places the close within the final bar's range: 0.5 is a doji, 1.0 closes on its
    high. `span` and `volume` are that bar's, so one call can produce a quiet bar or a violent one.
    """
    out, p = [], price
    for i in range(n):
        p += math.sin(i / 6.0) * 0.08 + math.cos(i / 11.0) * 0.05
        bar_span = 0.40 * (0.75 + 0.5 * ((i * 7) % 5) / 4)  # ranges vary 0.75x-1.25x
        vol = 1000.0 * (0.7 + 0.6 * ((i * 3) % 7) / 6)  # volume varies 0.7x-1.3x
        out.append(Candle(i * 900, p - bar_span * 0.1, p + bar_span / 2, p - bar_span / 2, p, vol))
    last = out[-1]
    low, high = last.close - span / 2, last.close + span / 2
    out[-1] = Candle(last.time, low, high, low, low + span * close_at, volume)
    return out


def loud(**kw):
    return calm(span=1.6, volume=9000.0, close_at=0.95, **kw)


# -- measuring ---------------------------------------------------------------------------


def test_an_ordinary_bar_is_not_put_in_front_of_anyone():
    p = pulse.read("BTCUSD", calm())
    assert p.ready and not p.loud


def test_a_busy_wide_bar_reads_as_a_big_move():
    p = pulse.read("BTCUSD", loud())
    assert p.state in (pulse.EXPANDING, pulse.BREAKOUT) and p.loud
    assert p.measured.volume_ratio > pulse.BIG_VOLUME
    assert p.measured.range_atr > pulse.BIG_RANGE


def test_every_state_has_a_sentence():
    for state in (pulse.BREAKOUT, pulse.EXPANDING, pulse.COILED, pulse.ACTIVE, pulse.QUIET):
        assert pulse.headline_for(state, pulse.Measured(direction=1), "BTCUSD")


def test_the_quiet_branch_is_reachable():
    """Nothing busy, nothing wide, no coil: the classifier has to have a way of saying so."""
    state, why = pulse.classify(pulse.Measured(volume_ratio=0.6, range_atr=0.5, body_share=0.2))
    assert state == pulse.QUIET and why


def test_the_reasons_carry_the_numbers_the_word_came_from():
    """A label on its own has to be trusted. Nothing on this page asks to be trusted."""
    joined = " ".join(pulse.read("BTCUSD", loud()).reasons)
    assert "volume" in joined and "ATR" in joined and "body" in joined


def test_a_wick_that_gave_it_all_back_is_not_a_push():
    fight = pulse.measure(calm(span=1.6, volume=2600.0, close_at=0.5))
    push = pulse.measure(calm(span=1.6, volume=2600.0, close_at=0.95))
    assert fight.body_share < pulse.DECISIVE_BODY <= push.body_share


def test_too_few_bars_is_a_refusal_not_a_reading():
    p = pulse.read("BTCUSD", calm(n=20))
    assert not p.ready and "backfill" in p.note


# -- how big: arithmetic, and it has to behave like arithmetic ----------------------------


def test_a_nearer_level_is_easier_to_reach():
    chances = [t["either_way"] for t in pulse.how_big(calm(), hours=1.0)["touch"]]
    assert chances == sorted(chances, reverse=True)


def test_a_longer_horizon_reaches_further():
    assert pulse.how_big(calm(), 24.0)["typical_move_pct"] > pulse.how_big(calm(), 1.0)["typical_move_pct"]


def test_the_band_brackets_the_price():
    size = pulse.how_big(calm(), hours=4.0)
    low, high = size["band_68"]
    assert low < calm()[-1].close < high


def test_without_volatility_there_is_nothing_to_price():
    flat = [Candle(i * 900, 100.0, 100.0, 100.0, 100.0, 1000.0) for i in range(300)]
    assert pulse.how_big(flat, 1.0)["ready"] is False


# -- which way: the refusals are the feature ----------------------------------------------


def test_an_ordinary_bar_gets_no_percentage():
    """`looks_like` on an ordinary bar matches most of the history and hands back the baseline
    wearing a conditional's clothes. A number that always equals the baseline reads as a
    measurement when it is a tautology, so there is no number."""
    for w in pulse.read("BTCUSD", calm()).way:
        assert w["conditional"] is None and "not unusual" in w["note"]


def test_a_rare_bar_with_too_few_matches_gets_no_percentage_either():
    """The rarer and more interesting the bar, the less there is to count — and the answer is
    silence, not a percentage off thirty samples (invariant 14b)."""
    p = pulse.read("BTCUSD", loud())
    assert p.loud
    for w in p.way:
        assert w["conditional"] is None and str(ol.MIN_SAMPLE) in w["note"]


def test_a_percentage_never_arrives_without_its_baseline_and_sample():
    """Whenever a rate IS returned, the numbers that make it readable come with it. A conditional
    rate alone cannot tell an edge from a market that simply went up all year."""
    series = [
        Candle(i * 900, 100.0, 101.0, 99.0, 100.5 if i % 2 else 99.5, 1000.0 + 900.0 * (i % 3))
        for i in range(1200)
    ]
    m = pulse.measure(series)
    w = pulse.which_way(series, m, 1.0)
    if w["conditional"] is not None:  # the fixture is synthetic; the contract is what is asserted
        assert w["baseline"] is not None
        assert w["sample"] >= ol.MIN_SAMPLE and w["matched"] >= ol.MIN_SAMPLE
        assert w["edge"] == pytest.approx(w["conditional"] - w["baseline"], abs=1e-9)


def test_worth_counting_is_what_gates_it():
    assert not pulse.worth_counting(pulse.Measured(volume_ratio=0.5, range_atr=0.5))
    assert pulse.worth_counting(pulse.Measured(volume_ratio=1.5, range_atr=0.5))
    assert pulse.worth_counting(pulse.Measured(volume_ratio=0.5, range_atr=1.2))


def test_up_bars_and_down_bars_are_not_one_statistic():
    """"What follows a big up bar" and "what follows a big down bar", averaged together, is how an
    edge disappears into its own mirror image."""
    up = pulse.Measured(volume_ratio=3.0, range_atr=2.0, direction=1)
    down = pulse.Measured(volume_ratio=3.0, range_atr=2.0, direction=-1)
    assert pulse.looks_like(up, up) and not pulse.looks_like(up, down)


def test_missing_measurements_match_nothing():
    assert not pulse.looks_like(pulse.Measured(), pulse.Measured(volume_ratio=3.0, range_atr=2.0))


# -- the shape the page and the model are handed ------------------------------------------


def test_the_reading_is_json_and_says_where_each_part_came_from():
    d = pulse.read("BTCUSD", calm()).to_dict()
    assert d["sources"] == {"measured": "math", "size": "math", "way": "counted"}
    assert set(d) >= {"ready", "state", "headline", "measured", "reasons", "size", "way", "loud"}


def test_only_a_real_move_is_loud():
    assert pulse.Pulse(True, pulse.BREAKOUT, "x").loud
    assert pulse.Pulse(True, pulse.EXPANDING, "x").loud
    for quiet in (pulse.COILED, pulse.ACTIVE, pulse.QUIET):
        assert not pulse.Pulse(True, quiet, "x").loud
