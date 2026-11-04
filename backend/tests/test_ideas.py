"""Single-option ideas over the captured chain: the edge is the gap to fair value at realised vol."""

from __future__ import annotations

import time
from typing import Any

from app.options.ideas import ideas
from app.options.pricing import bs_price
from tests.conftest import load_quotes

SPOT = 80_331.1


def run(vol_buyer: float, vol_seller: float, direction: float = 0.0) -> dict[str, Any]:
    now = time.time()
    return ideas(load_quotes(now), SPOT, now, vol_buyer, vol_seller, lambda price: 0.0, direction, 100, 60, 50)


def test_buyers_have_the_edge_only_when_the_market_moves_more_than_it_is_priced_for() -> None:
    """The chain's IV is about 23%. Moving at 60%, its options are cheap; at 5%, dear."""
    wild = run(vol_buyer=0.60, vol_seller=0.60)
    assert wild["ideas"] and {i["side"] for i in wild["ideas"]} == {"buy"}
    calm = run(vol_buyer=0.05, vol_seller=0.05)
    assert calm["ideas"] and {i["side"] for i in calm["ideas"]} == {"sell"}


def test_every_idea_is_priced_at_the_side_of_the_book_it_trades_and_explains_its_exit() -> None:
    out = run(vol_buyer=0.60, vol_seller=0.05)
    for i in out["ideas"]:
        assert i["edge"] > 0 and 0 <= i["pop"] <= 1
        assert i["edge"] == i["fair"] - i["price"] if i["side"] == "buy" else i["edge"] == i["price"] - i["fair"]
        assert i["fair"] == bs_price(i["kind"], SPOT, i["strike"], i["vol_used"], i["hours_left"])
        assert i["exit"]
        assert i["price"] == (i["ask"] if i["side"] == "buy" else i["bid"])  # the quote the popup opens on
        if i["side"] == "sell" and i["kind"] == "call":
            assert i["max_loss"] is None  # a sold call has no ceiling, and it is not given one
        if i["side"] == "buy":
            assert i["max_loss"] == i["price"]  # what was paid, fees here being zero


def test_the_likeliest_to_win_come_first_and_each_says_how_it_sits_with_the_trend() -> None:
    out = run(vol_buyer=0.60, vol_seller=0.05, direction=0.8)
    assert out["lean"] == "bullish"
    chances = [i["pop"] for i in out["ideas"]]
    assert len(chances) > 1 and chances == sorted(chances, reverse=True)
    for i in out["ideas"]:
        assert i["trend"] == ("with" if i["view"] == "bullish" else "against")


async def test_the_desk_reads_its_own_inputs(env: Any) -> None:
    out = await env.desk.ideas("BTC", env.desk.default_expiry("BTC"))
    assert out["underlying"] == "BTC" and out["considered"] > 0
    assert out["vol_buyer"] <= out["vol_seller"]  # each side on the measure least kind to it
