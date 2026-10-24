"""The merged level map.

This module exists to answer one question — what is above me and what is below me — by combining
four sources that a tabbed page kept apart. The tests are mostly about what it must NOT do:
invent a composite strength, double-count one price as two levels, or let a dead level onto a list
of live ones.
"""

from __future__ import annotations

from app.analysis import nearby

PRICE, ATR = 100.0, 2.0


def liquidity(**kw):
    return {"pools": [], "levels": [], **kw}


def pool(price, kind="high", touches=3, swept=False):
    return {"price": price, "kind": kind, "touches": touches, "swept": swept}


def zone(low, high, kind="ob", direction="bull", state="fresh", touches=0):
    return {"low": low, "high": high, "kind": kind, "direction": direction, "state": state, "touches": touches}


def test_levels_land_on_the_right_side_of_price():
    view = nearby.read(PRICE, ATR, liquidity=liquidity(pools=[pool(110), pool(90, "low")]))
    assert [level["price"] for level in view["above"]] == [110]
    assert [level["price"] for level in view["below"]] == [90]


def test_both_sides_are_sorted_nearest_first():
    """The nearest level is the one a stop sits behind. It has to be first or the list is a
    reference table, not an answer."""
    pools = [pool(130), pool(105), pool(120), pool(70, "low"), pool(95, "low"), pool(80, "low")]
    view = nearby.read(PRICE, ATR, liquidity=liquidity(pools=pools))
    assert [level["price"] for level in view["above"]] == [105, 120, 130]
    assert [level["price"] for level in view["below"]] == [95, 80, 70]
    assert view["next_above"]["price"] == 105
    assert view["next_below"]["price"] == 95


def test_a_pool_price_already_ran_through_is_not_offered():
    """Swept liquidity is history. Listing it is how a chart ends up with a support that has
    never held."""
    view = nearby.read(PRICE, ATR, liquidity=liquidity(pools=[pool(110, swept=True), pool(120)]))
    assert [level["price"] for level in view["above"]] == [120]


def test_a_mitigated_zone_is_not_offered():
    zones = {"zones": [zone(108, 112, state="mitigated"), zone(118, 122, state="fresh")]}
    view = nearby.read(PRICE, ATR, zones=zones)
    assert [level["price"] for level in view["above"]] == [118]


def test_a_zone_is_listed_at_the_edge_price_would_touch_first():
    view = nearby.read(PRICE, ATR, zones={"zones": [zone(110, 120), zone(80, 90)]})
    assert [level["price"] for level in view["above"]] == [110]  # the low edge, coming from below
    assert [level["price"] for level in view["below"]] == [90]  # the high edge, coming from above


def test_a_zone_price_is_inside_is_reported_as_here_not_as_a_level():
    """It is not above or below anything. Forcing it onto one side would put a level at the
    current price and make the nearest-level answer meaningless."""
    view = nearby.read(PRICE, ATR, zones={"zones": [zone(98, 102)]})
    assert view["above"] == [] and view["below"] == []
    assert len(view["inside"]) == 1
    assert view["inside"][0]["low"] == 98


def test_sources_naming_the_same_price_merge_and_count():
    """The one derived number in this module. Three sources at one price is the most useful thing
    it produces, and it has to be a count of DISTINCT sources."""
    view = nearby.read(
        PRICE, ATR,
        liquidity=liquidity(pools=[pool(110)]),
        zones={"zones": [zone(110.2, 115)]},
        profile={"session": {"poc": 109.9, "value_high": None, "value_low": None}},
    )
    assert len(view["above"]) == 1
    merged = view["above"][0]
    assert merged["confluence"] == 3
    # The survivor is the nearest of the cluster — here the POC at 109.9 — and it carries the
    # other two as agreements.
    assert merged["kind"] == "poc"
    assert set(merged["agrees"]) == {"pool", "zone"}
    assert view["confluences"][0]["confluence"] == 3


def test_two_sources_of_the_same_kind_do_not_inflate_confluence():
    """Two pools at one price is one source agreeing with itself, not two sources agreeing."""
    view = nearby.read(PRICE, ATR, liquidity=liquidity(pools=[pool(110), pool(110.1, touches=2)]))
    assert len(view["above"]) == 1
    assert view["above"][0]["confluence"] == 1
    assert view["above"][0]["agrees"] == []


def test_prices_further_apart_than_the_tolerance_stay_separate():
    far = PRICE + 10 * nearby.CONFLUENCE_ATR * ATR
    view = nearby.read(PRICE, ATR, liquidity=liquidity(pools=[pool(110)]), zones={"zones": [zone(far, far + 5)]})
    assert len({level["price"] for level in view["above"]}) == 2
    assert all(level["confluence"] == 1 for level in view["above"])


def test_the_merge_keeps_the_nearest_of_a_cluster():
    view = nearby.read(PRICE, ATR, liquidity=liquidity(pools=[pool(110.4)]), zones={"zones": [zone(110.0, 115)]})
    assert view["above"][0]["price"] == 110.0


def test_every_level_carries_its_own_evidence_in_its_own_units():
    """There is deliberately no composite strength score. Touches, zone state and volume share are
    not commensurable, and any single number blending them is a guess wearing a decimal point."""
    view = nearby.read(
        PRICE, ATR,
        liquidity=liquidity(pools=[pool(110, touches=4)]),
        zones={"zones": [zone(120, 125, state="tested", touches=2)]},
    )
    assert "4 touches" in view["above"][0]["evidence"]
    assert "tested" in view["above"][1]["evidence"]
    assert all("strength" not in level for level in view["above"])
    assert all("score" not in level for level in view["above"])


def test_each_side_is_capped_so_the_chart_stays_readable():
    pools = [pool(100 + i) for i in range(1, 40)] + [pool(100 - i, "low") for i in range(1, 40)]
    view = nearby.read(PRICE, ATR, liquidity=liquidity(pools=pools))
    assert len(view["above"]) <= nearby.MAX_PER_SIDE
    assert len(view["below"]) <= nearby.MAX_PER_SIDE


def test_a_level_at_exactly_the_price_counts_as_above():
    """An arbitrary but fixed choice. Left unstated it becomes a level that appears on neither
    side and silently vanishes from the map."""
    view = nearby.read(PRICE, ATR, liquidity=liquidity(pools=[pool(PRICE)]))
    assert len(view["above"]) == 1 and view["below"] == []


def test_junk_prices_are_dropped_not_rendered():
    view = nearby.read(PRICE, ATR, liquidity=liquidity(pools=[
        pool(None), pool(0), pool(-5), pool(True), pool("110"), pool(110),
    ]))
    assert [level["price"] for level in view["above"]] == [110]


def test_nothing_to_show_is_said_rather_than_drawn_empty():
    view = nearby.read(PRICE, ATR)
    assert view["ready"] is False
    assert view["above"] == [] and view["below"] == []
    assert view["next_above"] is None and view["next_below"] is None


def test_no_price_or_no_atr_means_no_map():
    assert nearby.read(0.0, ATR, liquidity=liquidity(pools=[pool(110)]))["ready"] is False
    assert nearby.read(PRICE, 0.0, liquidity=liquidity(pools=[pool(110)]))["ready"] is False


def test_every_source_reaches_the_map():
    """A source wired in but never read is the failure mode this whole module exists to prevent."""
    view = nearby.read(
        PRICE, ATR,
        liquidity=liquidity(pools=[pool(104, "high")], levels=[{"price": 106, "label": "Prev day high", "name": "PDH"}]),
        zones={"zones": [zone(108, 112)]},
        profile={"session": {"poc": 114, "value_high": 116, "value_low": 94},
                 "naked_pocs": [{"price": 118, "label": "Mon"}]},
        options={"call_wall": 122, "put_wall": 90, "max_pain": 124},
    )
    kinds = {level["kind"] for level in view["above"] + view["below"]}
    assert kinds == {"pool", "reference", "zone", "poc", "value", "naked", "wall"}
    assert all(level["kind_label"] for level in view["above"])


def test_the_map_reads_the_keys_the_profile_actually_emits():
    """Built from the real producer, not from a hand-written dict.

    The hand-written version of the test above said `{"poc": 118}` for a naked point of control and
    the module read `.get("poc")` to match — but `profile.py` emits `price`. Test and code shared
    one wrong assumption, so naked POCs never once reached the page and nothing failed. A fixture
    invented by the same person who wrote the reader cannot catch that; the producer's own output
    can."""
    from app.analysis import liquidity as lq
    from app.analysis import profile as pf
    from app.analysis import zones as zn
    from tests.test_market_structure import atr_of, walk

    candles = walk(4 * 86_400 // 900, seed=5)
    atr = atr_of(candles)
    price = candles[-1].close
    prof = pf.read(candles).to_dict()
    liq = lq.read(candles, atr).to_dict()
    zns = zn.read(candles, atr).to_dict()

    # `collect`, not `read`: collection is where keys are read, and it is the only stage that must
    # see everything. What survives `read` is then subject to the merge and the per-side cap, both
    # of which are allowed to drop a distant level and are tested on their own above.
    got = {level.kind for level in nearby.collect(price, atr, liq, zns, prof)}
    assert prof["naked_pocs"], "fixture produced no naked POCs — the check below would prove nothing"
    assert "naked" in got, "naked points of control did not reach the map"
    assert {"poc", "value"} <= got, "the session profile did not reach the map"
    assert {"pool", "reference"} <= got, "liquidity did not reach the map"
    assert nearby.read(price, atr, liq, zns, prof)["ready"] is True
