"""Everything sitting above and below the price, in one ranked list.

The four structure modules each answer a question about a *price*: where the stops rest
(`liquidity`), which zones are live (`zones`), where the size changed hands (`profile`), which
strikes the dealers are short (`options`). Read separately — a tab each — they cannot answer the
only question a person actually has in front of a chart:

    **What is above me, what is below me, and which of it matters?**

A tab per source makes that question unanswerable by construction, because the answer is a *merge*.
This module is that merge. It takes what the other modules already computed and does one thing to
it: sorts by distance from the current price and counts how many independent sources name the same
level.

### Why there is no composite strength score

The tempting move is to score every level 0-100 and sort by that — a pool with four touches beats
a fresh order block beats the POC. Resist it. Those numbers are not commensurable: touches, zone
state and volume share measure different things in different units, and any weighting that turns
them into one number is a guess wearing a decimal point. Fitting the weights would need an outcome
to fit against, and that is `signals.py`'s job, not this one's.

So levels are ranked by **distance**, which is a fact, and the one derived number here is
**confluence**: how many distinct sources put a level within `CONFLUENCE_ATR` of each other. That
is arithmetic over the inputs, it is the thing traders actually look for, and when it says three
sources agree at 82,800 it means exactly that and nothing more.

Pure: dictionaries in, dataclasses out. No clock, no network, no database.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# Levels inside this many ATRs of each other are talking about the same price. Loose enough that a
# pool at 82,790 and a POC at 82,805 count as agreement, tight enough that it is not every level
# on the chart agreeing with every other.
CONFLUENCE_ATR = 0.25
MAX_PER_SIDE = 6  # a list longer than this is a chart with forty lines on it

# What each source is worth knowing for. Not a weight — a label, so the page can say why a level is
# on the list without pretending the reasons are comparable.
KIND_LABEL = {
    "pool": "resting stops",
    "zone": "unfilled zone",
    "poc": "most traded",
    "value": "value edge",
    "naked": "untouched POC",
    "reference": "prev period",
    "wall": "option wall",
}


@dataclass(frozen=True)
class Level:
    """One price worth watching, and what put it there."""

    price: float
    label: str
    kind: str  # a key of KIND_LABEL
    side: str  # "above" | "below"
    distance_pct: float
    distance_atr: float
    evidence: str  # the source's own number, in its own units — never rescaled
    state: str | None = None  # a zone's lifecycle, where it has one
    confluence: int = 1  # how many distinct sources name this price
    agrees: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {"kind_label": KIND_LABEL.get(self.kind, self.kind)}


def _add(out: list[Level], price: Any, price_now: float, atr: float, **kw: Any) -> None:
    """Append one level, skipping anything that is not a usable price."""
    if not isinstance(price, int | float) or isinstance(price, bool) or price <= 0 or atr <= 0:
        return
    out.append(Level(
        price=round(float(price), 2),
        side="above" if price >= price_now else "below",
        distance_pct=round(100 * (price / price_now - 1), 3),
        distance_atr=round(abs(price - price_now) / atr, 2),
        **kw,
    ))


def collect(
    price: float, atr: float,
    liquidity: dict[str, Any] | None = None,
    zones: dict[str, Any] | None = None,
    profile: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
) -> list[Level]:
    """Every level the structure modules found, as one flat list. Unsorted, unmerged."""
    out: list[Level] = []
    if price <= 0 or atr <= 0:
        return out

    # Resting stops only. A pool price has already run through is history, not a target.
    for p in (liquidity or {}).get("pools") or []:
        if p.get("swept"):
            continue
        _add(out, p.get("price"), price, atr, kind="pool",
             label=f"{p.get('touches', 0)}x {'highs' if p.get('kind') == 'high' else 'lows'}",
             evidence=f"{p.get('touches', 0)} touches")

    # Live zones, as their near edge — the price that would be touched first. A zone price is
    # already inside is not above or below anything; it is reported separately by `read`.
    for z in (zones or {}).get("zones") or []:
        low, high = z.get("low"), z.get("high")
        if z.get("state") in ("mitigated", "invalidated") or not isinstance(low, int | float) or not isinstance(high, int | float):
            continue
        if low <= price <= high:
            continue
        # Coming from below, a zone above is met at its LOW edge; coming from above, at its high.
        _add(out, low if low > price else high, price, atr, kind="zone", state=z.get("state"),
             label=f"{str(z.get('kind', '')).upper()} {z.get('direction', '')}".strip(),
             evidence=f"{z.get('state', '?')}, {z.get('touches', 0)} touches")

    prof = profile or {}
    for scope in ("session", "previous"):
        p = prof.get(scope)
        if not p:
            continue
        name = "today" if scope == "session" else "prev day"
        _add(out, p.get("poc"), price, atr, kind="poc", label=f"POC {name}", evidence="most traded price")
        _add(out, p.get("value_high"), price, atr, kind="value", label=f"VAH {name}", evidence="70% value area edge")
        _add(out, p.get("value_low"), price, atr, kind="value", label=f"VAL {name}", evidence="70% value area edge")
    for n in prof.get("naked_pocs") or []:
        _add(out, n.get("poc"), price, atr, kind="naked", label=f"Naked POC {n.get('label', '')}".strip(),
             evidence="price has not been back")

    for level in (liquidity or {}).get("levels") or []:
        _add(out, level.get("price"), price, atr, kind="reference",
             label=level.get("label", level.get("name", "")), evidence="everybody watches it")

    opt = options or {}
    for key, label in (("call_wall", "Call wall"), ("put_wall", "Put wall"), ("max_pain", "Max pain")):
        _add(out, opt.get(key), price, atr, kind="wall", label=label, evidence="dealer positioning")

    return out


def merge(levels: list[Level], atr: float, tolerance_atr: float = CONFLUENCE_ATR) -> list[Level]:
    """Collapse levels that name the same price, counting how many sources agreed.

    The survivor of a cluster is the one from the source with the most specific evidence — but the
    *count* is what matters and what the page shows. Three sources at one price is the single most
    useful thing this module produces."""
    if not levels or atr <= 0:
        return []
    tolerance = tolerance_atr * atr
    out: list[Level] = []
    for side in ("above", "below"):
        same = sorted((level for level in levels if level.side == side), key=lambda level: level.distance_atr)
        used: list[Level] = []
        for level in same:
            near = next((u for u in used if abs(u.price - level.price) <= tolerance), None)
            if near is None:
                used.append(level)
                continue
            # Already have this price from another source: record the agreement, keep the first
            # (which is the nearest, since the list is sorted by distance).
            if level.kind not in near.agrees and level.kind != near.kind:
                near.agrees.append(level.kind)
                object.__setattr__(near, "confluence", near.confluence + 1)
        out += used[:MAX_PER_SIDE]
    return sorted(out, key=lambda level: level.price)


def inside(price: float, zones: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Live zones price is currently sitting in. Not above or below anything — it is here."""
    out = []
    for z in (zones or {}).get("zones") or []:
        low, high = z.get("low"), z.get("high")
        if z.get("state") in ("mitigated", "invalidated"):
            continue
        if isinstance(low, int | float) and isinstance(high, int | float) and low <= price <= high:
            out.append(z)
    return out


def read(
    price: float, atr: float,
    liquidity: dict[str, Any] | None = None,
    zones: dict[str, Any] | None = None,
    profile: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The merged map of price: what is above, what is below, and where sources agree."""
    found = merge(collect(price, atr, liquidity, zones, profile, options), atr)
    by_distance = sorted(found, key=lambda level: level.distance_atr)
    above = [level for level in by_distance if level.side == "above"]
    below = [level for level in by_distance if level.side == "below"]
    agreed = sorted((level for level in found if level.confluence > 1), key=lambda level: -level.confluence)
    return {
        "ready": bool(found),
        "price": round(price, 2),
        "atr": round(atr, 2),
        "above": [level.to_dict() for level in above],
        "below": [level.to_dict() for level in below],
        # The nearest level each way is the one a stop sits behind and a target sits at.
        "next_above": above[0].to_dict() if above else None,
        "next_below": below[0].to_dict() if below else None,
        "confluences": [level.to_dict() for level in agreed][:4],
        "inside": inside(price, zones),
    }
