"""Paper option structures: opened from the order ticket, valued from the live chain, closed on a combined SL/TP.

A structure's max loss is reserved as margin, so the paper account's available margin, equity and
daily loss limit all see it. Exits, checked by the engine every few seconds:

    stop loss     the loss reaches sl_pct of the max loss
    take profit   the profit reaches tp_pct of the premium paid (debit) or received (credit)
    expiry        one hour before settlement it is closed at the chain's prices, or, with no
                  prices, settled at intrinsic value on the spot

A snapshot older than a minute is no price: nothing is valued or closed on it, except that a
structure past its expiry is always settled. A close becomes a row in paper_trades, like any paper trade.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

from tradebuddy import structures
from tradebuddy.errors import BrokerError
from tradebuddy.events import EventBus, PositionClosed
from tradebuddy.settings import Settings
from tradebuddy.store import Store

EXPIRY_CLOSE_SECONDS = 3600


class PaperOptions:
    def __init__(
        self, store: Store, bus: EventBus, settings: Callable[[], Settings],
        snapshot: Callable[[str], dict[str, Any] | None], spot: Callable[[str], float | None],
    ) -> None:
        self.db = store.db
        self.bus = bus
        self.settings = settings
        self.snapshot = snapshot  # perpetual symbol -> latest options summary, or None
        self.spot = spot  # perpetual symbol -> fresh live price, or None

    # -- state ----------------------------------------------------------------

    def rows(self) -> list[dict[str, Any]]:
        return [self._decode(r) for r in self.db.execute("SELECT * FROM paper_structures ORDER BY opened_at")]

    def row(self, structure_id: int) -> dict[str, Any] | None:
        r = self.db.execute("SELECT * FROM paper_structures WHERE id = ?", (structure_id,)).fetchone()
        return self._decode(r) if r else None

    def open_on(self, symbol: str) -> dict[str, Any] | None:
        r = self.db.execute("SELECT * FROM paper_structures WHERE symbol = ? LIMIT 1", (symbol,)).fetchone()
        return self._decode(r) if r else None

    def count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM paper_structures").fetchone()[0]

    def by_client_id(self, client_order_id: str) -> dict[str, Any] | None:
        r = self.db.execute("SELECT * FROM paper_structures WHERE client_order_id = ?", (client_order_id,)).fetchone()
        return self._decode(r) if r else None

    @staticmethod
    def _decode(r: Any) -> dict[str, Any]:
        row = dict(r)
        row["legs"] = json.loads(row["legs"])
        return row

    def entries(self, symbol: str, underlying: str, strategy: str, since: float) -> tuple[int, float | None]:
        """Structures `strategy` opened on `symbol` since `since` (open and closed), and its last close time."""
        open_n = self.db.execute(
            "SELECT COUNT(*) FROM paper_structures WHERE symbol = ? AND strategy = ? AND opened_at >= ?", (symbol, strategy, since),
        ).fetchone()[0]
        closed = self.db.execute(
            "SELECT COUNT(*) FILTER (WHERE opened_at >= ?), MAX(closed_at) FROM paper_trades WHERE strategy = ? AND symbol LIKE ?",
            (since, strategy, f"{underlying} %"),
        ).fetchone()
        return open_n + closed[0], closed[1]

    def margin(self) -> float:
        return float(self.db.execute("SELECT COALESCE(SUM(capital), 0) FROM paper_structures").fetchone()[0])

    def unrealized(self, now: float | None = None) -> float:
        return sum(v["unrealized_pnl"] or 0.0 for v in (self.view(r, now) for r in self.rows()))

    # -- valuing ----------------------------------------------------------------

    def _units(self, row: dict[str, Any]) -> float:
        return row["contract_value"] * row["qty"]

    def _value(self, row: dict[str, Any], now: float, at: str = "mark") -> tuple[list[float] | None, float | None]:
        """Prices per leg and gross PnL in USD from a fresh snapshot, (None, None) without one. `at="mark"`
        values as Delta does (unrealised PnL, SL / TP triggers); `at="fill"` is what a close gets (bid / ask)."""
        summary = self.snapshot(row["symbol"])
        if not structures.fresh(summary, now):
            return None, None
        if at == "mark":
            prices = structures.mark_prices(row["legs"], summary)
        else:
            prices = structures.exit_prices(row["legs"], summary, self.settings().paper_slippage_pct)
        if prices is None:
            return None, None
        gross = (structures.held_value(row["legs"], prices) - row["net"]) * self._units(row)
        return prices, max(gross, -row["capital"])

    def view(self, row: dict[str, Any], now: float | None = None) -> dict[str, Any]:
        """Everything the Positions page shows for one structure: risk, where it stands now, each leg, greeks."""
        now = time.time() if now is None else now
        summary = self.snapshot(row["symbol"])
        fresh = structures.fresh(summary, now)
        slip = self.settings().paper_slippage_pct
        units = self._units(row)
        prices, gross = self._value(row, now)  # at the mark, as Delta shows it
        fills, close_gross = self._value(row, now, at="fill")
        sides = structures.quotes(row["legs"], summary) if fresh else [None] * len(row["legs"])
        legs = []
        for leg, side in zip(row["legs"], sides, strict=True):
            sign = 1 if leg["action"] == "buy" else -1
            exit_px = structures.fill_price(side, "sell" if sign > 0 else "buy", slip)  # each leg on its own: one gap hides only itself
            mark = structures.mark_of(side)
            legs.append(leg | {
                "exit_price": None if exit_px is None else round(exit_px, 4),
                "mark": None if mark is None else round(mark, 4), "iv": (side or {}).get("iv"), "delta": (side or {}).get("delta"),
                "pnl": None if mark is None else round(sign * (mark - leg["price"]) * units, 4),
            })
        greeks = None
        if fresh and all(sides):
            greeks = {
                g: round(sum((1 if leg["action"] == "buy" else -1) * (side.get(g) or 0.0) for leg, side in zip(row["legs"], sides, strict=True)) * units, 6)
                for g in ("delta", "gamma", "theta", "vega")
            }
        spot = self.spot(row["symbol"]) or (summary or {}).get("spot")
        _, _, breakevens = structures.profile(row["legs"], row["net"], row["kind"])
        ref = row["premium"]
        stop_at, target_at = -row["capital"] * row["sl_pct"] / 100, ref * row["tp_pct"] / 100
        progress = None
        if gross is not None:
            progress = round(100 * gross / target_at, 1) if gross >= 0 else round(-100 * gross / stop_at, 1)
        return {
            **{k: row[k] for k in ("id", "client_order_id", "symbol", "underlying", "kind", "label", "expiry", "qty", "contract_value",
                                   "net", "premium", "capital", "max_profit", "entry_fee", "sl_pct", "tp_pct", "strategy", "opened_at")},
            "name": structures.KINDS.get(row["kind"], row["kind"]),
            "type": "debit" if row["net"] > 0 else "credit",
            "expiry_label": structures.expiry_label(row["expiry"]),
            "hours_left": round((row["expiry"] - now) / 3600, 2),
            "close_at": row["expiry"] - EXPIRY_CLOSE_SECONDS,
            "held_seconds": round(now - row["opened_at"]),
            "legs": legs,
            "spot": spot,
            "breakevens": [round(b, 2) for b in breakevens],
            "breakeven_distance_pct": [round(100 * (b - spot) / spot, 2) for b in breakevens] if spot else [],
            "value_now": None if prices is None else round(structures.held_value(row["legs"], prices) * units, 4),
            "close_pnl": None if close_gross is None else round(close_gross - sum(
                structures.fee(px, (summary or {}).get("spot") or spot or 0.0, units) for px in fills or []), 4),  # closing now, at bid / ask, after fees
            "unrealized_pnl": None if gross is None else round(gross, 4),
            "pnl_pct": None if gross is None or not ref else round(100 * gross / ref, 2),
            "pnl_of_max_loss_pct": None if gross is None else round(100 * gross / row["capital"], 2),
            "stop_at": round(stop_at, 4),
            "target_at": round(target_at, 4),
            "progress_pct": progress,  # + share of the way to the target, - to the stop
            "greeks": greeks,
            "chain_age_seconds": round(now - summary["at"], 1) if summary else None,
            "priced": gross is not None,
        }

    # -- open / close -------------------------------------------------------------

    def open(self, client_order_id: str, symbol: str, built: dict[str, Any], sl_pct: float, tp_pct: float, strategy: str, available: float) -> dict[str, Any]:
        """Fill a built structure (structures.build). Synchronous, so nothing interleaves on the event loop."""
        if existing := self.by_client_id(client_order_id):
            return existing
        if self.open_on(symbol):
            raise BrokerError(f"paper: an option structure on {symbol} is already open")
        units = built["contract_value"] * built["qty"]
        entry_fee = sum(structures.fee(leg["price"], built["spot"], units) for leg in built["legs"])
        capital = built["max_loss_usd"]
        if capital + entry_fee > available:
            raise BrokerError(f"paper: insufficient margin (max loss {capital:.2f} + fees {entry_fee:.2f}, available {available:.2f})")
        now = time.time()
        with self.db:
            cur = self.db.execute(
                "INSERT INTO paper_structures (client_order_id, symbol, underlying, kind, label, expiry, qty, contract_value, legs, net,"
                " premium, capital, max_profit, entry_fee, sl_pct, tp_pct, strategy, opened_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (client_order_id, symbol, built["underlying"], built["kind"], built["label"], built["expiry"], built["qty"], built["contract_value"],
                 json.dumps([{k: leg.get(k) for k in ("action", "kind", "strike", "symbol", "price", "client_order_id")} for leg in built["legs"]]),
                 built["net"], built["premium_usd"], capital, built["max_profit_usd"], entry_fee, sl_pct, tp_pct, strategy, now),
            )
            self.db.execute("UPDATE paper_account SET balance = balance - ?, fees_paid = fees_paid + ? WHERE id = 1", (entry_fee, entry_fee))
        return self.row(cur.lastrowid)

    def close(self, structure_id: int, reason: str = "manual close", now: float | None = None) -> dict[str, Any]:
        now = time.time() if now is None else now
        row = self.row(structure_id)
        if row is None:
            raise BrokerError(f"no open paper option structure {structure_id}")
        prices, gross = self._value(row, now, at="fill")
        if prices is None:
            if now < row["expiry"]:
                raise BrokerError(f"paper: no fresh option prices to close {row['label']}")
            # Settled: intrinsic value on the spot, no exit fee (Delta settles in cash).
            spot = self.spot(row["symbol"]) or (self.snapshot(row["symbol"]) or {}).get("spot")
            if not spot:
                raise BrokerError(f"paper: no spot price to settle {row['label']}")
            value = structures.payoff(row["legs"], float(spot))
            gross = max((value - row["net"]) * self._units(row), -row["capital"])
            exit_fee, exit_net = 0.0, value
            reason = f"settled at expiry on spot {float(spot):,.2f}"
        else:
            spot = (self.snapshot(row["symbol"]) or {}).get("spot") or 0.0
            exit_fee = sum(structures.fee(px, spot, self._units(row)) for px in prices)
            exit_net = structures.held_value(row["legs"], prices)
        self._record(row, gross, exit_fee, exit_net, reason, now)
        return {"closed": [row["label"]], "errors": []}

    def _record(self, row: dict[str, Any], gross: float, exit_fee: float, exit_net: float, reason: str, now: float) -> None:
        fees = row["entry_fee"] + exit_fee
        pnl = gross - fees
        side = "long" if row["net"] > 0 else "short"
        with self.db:
            self.db.execute("DELETE FROM paper_structures WHERE id = ?", (row["id"],))
            self.db.execute(
                "UPDATE paper_account SET balance = balance + ?, realized_pnl = realized_pnl + ?, fees_paid = fees_paid + ? WHERE id = 1",
                (gross - exit_fee, pnl, exit_fee),
            )
            # entry/exit as premiums per unit: paid then received (debit), or received then paid back (credit).
            self.db.execute(
                "INSERT INTO paper_trades (client_order_id, strategy, symbol, side, size, contract_value, entry_price, exit_price,"
                " leverage, margin, gross_pnl, fees, pnl, reason, opened_at, closed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (row["client_order_id"], row["strategy"], row["label"], side, row["qty"], row["contract_value"], abs(row["net"]), abs(exit_net),
                 1.0, row["capital"], gross, fees, pnl, reason, row["opened_at"], now),
            )
        self.bus.publish(PositionClosed(
            broker="paper", strategy=row["strategy"], symbol=row["label"], side=side,
            entry_price=round(abs(row["net"]), 4), exit_price=round(abs(exit_net), 4), pnl=round(pnl, 6), reason=reason,
        ))

    def close_all(self, reason: str = "manual close") -> dict[str, Any]:
        closed, errors = [], []
        for row in self.rows():
            try:
                closed += self.close(row["id"], reason)["closed"]
            except BrokerError as exc:
                errors.append(f"{row['label']}: {exc}")
        return {"closed": closed, "errors": errors}

    # -- protective exits ---------------------------------------------------------

    def check(self, now: float | None = None) -> list[str]:
        """Stop loss, take profit and expiry for every open structure. Returns the labels closed."""
        now = time.time() if now is None else now
        closed = []
        for row in self.rows():
            reason = ""
            _, gross = self._value(row, now)
            if now >= row["expiry"] - EXPIRY_CLOSE_SECONDS:
                reason = "closed an hour before expiry"
            elif gross is None:
                continue  # no fresh prices: absence over staleness
            elif gross <= -row["capital"] * row["sl_pct"] / 100:
                reason = f"stop loss: down {-gross:.2f} ({row['sl_pct']:g}% of max loss)"
            elif row["premium"] and gross >= row["premium"] * row["tp_pct"] / 100:
                reason = f"take profit: up {gross:.2f} ({row['tp_pct']:g}% of premium)"
            if not reason:
                continue
            try:
                closed += self.close(row["id"], reason, now)["closed"]
            except BrokerError:
                continue  # before expiry without prices: tried again on the next pass
        return closed
