"""Where a number came from, and therefore how much weight it can carry.

The Market page draws arithmetic, counted history and what other traders have paid, and until now
they all looked the same: same type, same card, same confidence. They are not the same. An
expected move from volatility is true given the inputs; a hit rate counted over 3,000 bars is true
with an error bar; an implied volatility is a fact about what someone paid, not a forecast. And a
paragraph a language model wrote about all three is weaker again — it is the only thing here that
cannot be rechecked against the bars it came from, which is exactly why it gets its own badge
rather than sitting unmarked next to arithmetic.

So every figure carries a source, the UI badges it, and the legend says in one line what each one
depends on. Being able to see that the direction is counted and the distance is arithmetic — and
that nothing on the page is a guess — is most of what makes the page trustworthy.

Ordered most dependable first. "Dependable" means how little has to be true for the number to
hold, not how useful it is: arithmetic needs only its inputs, a counted rate needs the future to
resemble the past.
"""

from __future__ import annotations

from typing import Any

MATH = "math"
COUNTED = "counted"
IMPLIED = "implied"
WRITTEN = "written"

SOURCES: dict[str, dict[str, Any]] = {
    MATH: {
        "key": MATH,
        "label": "Arithmetic",
        "badge": "MATH",
        "colour": "#0f766e",
        "depends_on": "Price history only.",
        "detail": (
            "Volatility scaled by the square root of time, and the standard results that follow from it: "
            "the typical move over a horizon, the chance of travelling a given distance, and the odds of "
            "reaching a stop before a target. Nothing is fitted and nothing is assumed about direction — "
            "given the volatility, these hold."
        ),
    },
    COUNTED: {
        "key": COUNTED,
        "label": "Counted history",
        "badge": "COUNTED",
        "colour": "#1e88e5",
        "depends_on": "Stored bars, and the future resembling them.",
        "detail": (
            "Rates measured over every stored bar: how often price rose from this structure, how often a "
            "break of this kind followed through. Always shown beside the base rate it has to beat and the "
            "number of outcomes behind it — a 56% on a 56% base rate is the market drifting, not a reading. "
            "Below the sample threshold no percentage is shown at all."
        ),
    },
    IMPLIED: {
        "key": IMPLIED,
        "label": "What traders paid",
        "badge": "MARKET",
        "colour": "#7c3aed",
        "depends_on": "The live options book and open interest.",
        "detail": (
            "Implied volatility, the term curve, 25-delta skew, put/call open interest, the strikes with the "
            "most open interest and max pain. Not forecasts — positions other people have money on. They say "
            "what the market is paying for, which is why they are worth reading next to what it has been doing."
        ),
    },
    WRITTEN: {
        "key": WRITTEN,
        "label": "Written by a model",
        "badge": "WRITTEN",
        "colour": "#b45309",
        "depends_on": "A language model reading the numbers above. Sent to Mistral.",
        "detail": (
            "The only thing on this page that is not a number and cannot be rechecked against a bar. A "
            "language model is given the measured figures — price, volatility, the counted direction, the "
            "levels, the profile, the estimated flow — and writes them up as sentences. It computes nothing "
            "and decides nothing, and it is told not to predict, not to recommend a trade and not to use any "
            "number it was not given. It can still be wrong in ways the rest of this page cannot: it may "
            "misread a figure or write a confident sentence about a quiet market. Read it as a summary of "
            "the numbers, never as evidence for them. Off unless switched on in Settings, and it is the one "
            "feature that sends market data off this machine."
        ),
    },
}

ORDER = (MATH, COUNTED, IMPLIED, WRITTEN)

# Which card on the Market page rests on what. A card can only be as dependable as its source.
CARD_SOURCE = {
    "term_structure": IMPLIED,
    "atm_iv": IMPLIED,
    "pcr": IMPLIED,
    "chain_oi": IMPLIED,
    "insights": COUNTED,  # rules over measured numbers; nothing fitted
    "volume": COUNTED,
    "indicators": MATH,  # transforms of price; nothing fitted and nothing counted
    "shapes": COUNTED,
    "where": COUNTED,  # levels the traded size itself made
    "levels": MATH,  # the highs and lows themselves; nothing fitted
    "outlook": COUNTED,
    "written": WRITTEN,
}


def legend() -> list[dict[str, Any]]:
    """The sources in order, for the page to render once at the top."""
    return [SOURCES[k] for k in ORDER]


def of(card: str) -> dict[str, Any]:
    """The source behind one card, falling back to counted history rather than claiming more."""
    return SOURCES[CARD_SOURCE.get(card, COUNTED)]
