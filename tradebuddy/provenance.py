"""Where a number came from, and therefore how much weight it can carry.

The Market page draws arithmetic, counted history, what other traders have paid, a trained model
and a language model's prose, and until now they all looked the same: same type, same card, same
confidence. They are not the same. An expected move from volatility is true given the inputs; a
hit rate counted over 3,000 bars is true with an error bar; an implied volatility is a fact about
what someone paid, not a forecast; a model's direction call is worth exactly what it beat its
baseline by, which here is nothing.

So every figure carries a source, the UI badges it, and the legend says in one line what each one
depends on. Being able to see that the direction is counted and the distance is arithmetic — and
that nothing on the page is the model guessing — is most of what makes the page trustworthy.

Ordered most dependable first. "Dependable" means how little has to be true for the number to
hold, not how useful it is: arithmetic needs only its inputs, a counted rate needs the future to
resemble the past, a model needs that and its own fit to generalise.
"""

from __future__ import annotations

from typing import Any

MATH = "math"
COUNTED = "counted"
IMPLIED = "implied"
MODEL = "model"
AI = "ai"

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
    MODEL: {
        "key": MODEL,
        "label": "Trained model",
        "badge": "MODEL",
        "colour": "#d97706",
        "depends_on": "A fit that held up on bars it never saw.",
        "detail": (
            "Gradient-boosted models over 15-minute features. Each is scored against a naive baseline on a "
            "purged holdout and used only for what it beat that baseline at. It currently has no skill at "
            "24-hour direction, so it is not shown a side here — direction on this page is counted and "
            "structural, never the model's guess."
        ),
    },
    AI: {
        "key": AI,
        "label": "TradeBuddy AI",
        "badge": "TB-AI",
        "colour": "#db2777",
        "depends_on": "A language model reading the numbers above.",
        "detail": (
            "Prose written by Mistral from the computed figures and findings. It can be wrong in ways the "
            "rest of the page cannot: it is the only thing here that is not derived from a formula or a count. "
            "Useful as a second reading, never as the reason for a trade."
        ),
    },
}

ORDER = (MATH, COUNTED, IMPLIED, MODEL, AI)

# Which card on the Market page rests on what. A card can only be as dependable as its source.
CARD_SOURCE = {
    "term_structure": IMPLIED,
    "atm_iv": IMPLIED,
    "pcr": IMPLIED,
    "chain_oi": IMPLIED,
    "insights": COUNTED,  # rules over measured numbers; no fit, no forecast
    "volume": COUNTED,
    "indicators": MATH,  # transforms of price; nothing fitted and nothing counted
    "shapes": COUNTED,
    "where": COUNTED,  # levels the traded size itself made
    "levels": MATH,  # the highs and lows themselves; nothing fitted
    "outlook": COUNTED,
    "forecast": MODEL,
    "playbook": MODEL,
    "review": AI,
}


def legend() -> list[dict[str, Any]]:
    """The sources in order, for the page to render once at the top."""
    return [SOURCES[k] for k in ORDER]


def of(card: str) -> dict[str, Any]:
    """The source behind one card, falling back to counted history rather than claiming more."""
    return SOURCES[CARD_SOURCE.get(card, COUNTED)]
