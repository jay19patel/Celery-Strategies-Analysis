"""Everything the dashboard can ask or command, in one place.

The web layer calls these methods directly (single process) or through
ZeroMQ RPC (distributed): `RemoteApi` has the same methods, so routes do not
know which one they hold. Every method takes and returns plain JSON.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from tradebuddy import auth, provenance, resample, verdict
from tradebuddy.brokers import Broker
from tradebuddy.codec import EVENT_TYPES
from tradebuddy.config import DEFAULT_AUTH_PIN
from tradebuddy.delta import round_to_tick
from tradebuddy.errors import BrokerError
from tradebuddy.journal import (
    collect_day,
    collect_month_summary,
    collect_monthly_calendar_data,
    gather_live,
    get_day_bounds,
    parse_day,
)
from tradebuddy.options import UNDERLYINGS
from tradebuddy.settings import SettingsError
from tradebuddy.store import BASE_INTERVAL
from tradebuddy.system import BAR_RESOLUTION, System
from tradebuddy.trading import TradeRefused
from tradebuddy.transport import RpcClient, RpcError

SIGNAL_EVENTS = ["SignalGenerated", "TradeSkipped", "StrategyError"]
ACTIVITY_EVENTS = [*SIGNAL_EVENTS, "OrderPlaced", "OrderFailed", "OrderUnknown", "PositionClosed"]
LEVEL_TYPES = {
    "error": sorted(n for n, cls in EVENT_TYPES.items() if cls.LEVEL == "error"),
    "warning": sorted(n for n, cls in EVENT_TYPES.items() if cls.LEVEL in ("warning", "error")),
}

# Methods the engine answers over RPC. Anything else is refused.
METHODS = (
    "page_context", "header", "strategies", "signals", "positions", "orders", "open_orders", "account",
    "paper_stats", "paper_trades", "metrics", "events", "settings", "update_settings", "clear_credentials",
    "test_delta", "toggle", "close_all", "close_position", "protection", "paper_reset", "place_order", "order_ticket",
    "set_position_control", "risk", "risk_resume", "analysis", "options_history", "database",
    "send_email_report",
    "bars", "snapshots", "outlook", "verdicts", "market_structure", "pulse",
    "journal_summary", "journal_month",
    "paper_history", "ai_report",
    "options_desk", "options_order",
    "verify_pin", "change_pin", "get_auth_status",
)


def _one_claim_per_bar(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One directional reading per bar, for the chart.

    Several cards speak on the same bar and stacking their markers on one candle would be a smear,
    not a record. The Outlook card wins where it spoke — it is the page's direction — and otherwise
    the card that was most sure of itself stands for that bar."""
    best: dict[int, dict[str, Any]] = {}
    for r in rows:
        if r["stance"] not in ("bullish", "bearish"):
            continue
        bar = int(r["bar_time"])
        rank = (r["source"] == "outlook", float(r["confidence"] or 0))
        if bar not in best or rank > best[bar]["_rank"]:
            best[bar] = {
                "_rank": rank, "bar_time": bar, "source": r["source"], "stance": r["stance"],
                "label": r["label"], "price": r["price"], "horizon_hours": r["horizon_hours"],
                "settled": r["settled_at"] is not None,
                "correct": None if r["correct"] is None else bool(r["correct"]),
                "actual_return": r["actual_return"],
            }
    return [{k: v for k, v in row.items() if k != "_rank"} for _, row in sorted(best.items())]


class ApiError(RpcError):
    """An error with an HTTP status, raised the same way locally and over RPC."""


# The timeframes actually stored: the base, and the one the strategies run on. Everything else is
# folded from whichever of these divides it — the coarser one where both do, because reading 96
# bars to build a day beats reading 1,440 to build the same day out of the same market.
STORED_INTERVALS = (BASE_INTERVAL, BAR_RESOLUTION)
MAX_BARS = 20_000  # a chart that cannot be panned through 20,000 candles is not the problem


def _bar_source(interval: str) -> str:
    """Which stored series to build `interval` from. Raises when nothing divides it."""
    if interval in STORED_INTERVALS:
        return interval
    usable = [s for s in STORED_INTERVALS if resample.seconds(interval) % resample.seconds(s) == 0
              and resample.seconds(s) <= resample.seconds(interval)]
    if not usable:
        raise ApiError(400, f"{interval} cannot be folded from any stored series ({', '.join(STORED_INTERVALS)})")
    return max(usable, key=resample.seconds)


class Api:
    def __init__(self, system: System) -> None:
        self.system = system
        self._db_stats: tuple[float, dict[str, Any]] = (0.0, {})

    def _broker(self, name: str | None) -> Broker:
        if name is None:
            return next(iter(self.system.active.values()))
        if name not in self.system.brokers:
            raise ApiError(404, f"unknown broker {name!r}")
        return self.system.brokers[name]

    @staticmethod
    async def _call(coro):
        try:
            return await coro
        except BrokerError as exc:
            raise ApiError(502, str(exc)) from exc

    async def dispatch(self, method: str, params: dict[str, Any]) -> Any:
        if method not in METHODS:
            raise ApiError(404, f"unknown method {method!r}")
        return await getattr(self, method)(**params)

    # -- reads --------------------------------------------------------------

    async def page_context(self) -> dict[str, Any]:
        s = self.system.settings
        return {
            "active_brokers": s.active_brokers, "paper_active": s.paper_active,
            # Set by `python -m tradebuddy demo`. Every page says so while it is set, because a
            # verdict's record read off a market that never happened is worse than no record.
            "demo_data": bool(self.system.store.load_settings().get("demo_data")),
        }

    async def header(self) -> dict[str, Any]:
        return self.system.header()

    async def strategies(self) -> list[dict[str, Any]]:
        return self.system.strategies_view()

    async def signals(self, limit: int = 300, strategy: str = "", symbol: str = "") -> list[dict[str, Any]]:
        rows = self.system.store.recent_events(min(limit, 2000), types=SIGNAL_EVENTS)
        return [r for r in rows if (not strategy or r.get("strategy") == strategy) and (not symbol or r.get("symbol") == symbol)]

    async def positions(self, broker: str | None = None) -> list[dict[str, Any]]:
        b = self._broker(broker)
        controls = self.system.store.controls(b.name)
        out = []
        for p in await self._call(b.positions()):
            row = p.to_dict()
            c = controls.get(p.symbol)
            row["control"] = {k: c[k] for k in ("trailing", "max_steps", "steps")} | {"trailing": bool(c["trailing"])} if c else None
            row["roe_pct"] = p.unrealized_pnl / p.margin * 100 if p.margin else None
            # What holding this is actually worth, and the odds behind it. The guard prices every
            # position on every pass whether or not it is armed to act, because a 1:3 ticket that
            # is really a 75% chance of being stopped is worth showing either way.
            row["exit"] = self.system.guard.exits.get((b.name, p.symbol))
            row |= await self._position_extras(b, p)
            out.append(row)
        return out  # option structures have their own list (structures()), not mixed in here

    async def _position_extras(self, b: Broker, p: Any) -> dict[str, Any]:
        """Size in money, the risk and reward at SL / TP, how far each level is, and R now. Never fails the list."""
        extra: dict[str, Any] = {}
        details = getattr(b, "position_details", None)
        if details is not None:
            extra |= details(p.symbol) or {}
        if "contract_value" not in extra:
            try:
                extra["contract_value"] = float((await self.system.market_client.product(p.symbol)).get("contract_value") or 0) or None
            except Exception:
                extra["contract_value"] = None
        cv, mark, entry = extra["contract_value"], p.mark_price or p.entry_price, p.entry_price
        units = (cv or 0) * p.size
        d = 1 if p.side == "long" else -1
        extra["notional"] = round(units * mark, 4) if units else None
        extra.setdefault("leverage", round(units * entry / p.margin, 2) if units and p.margin else None)
        at = lambda level: round(d * (level - entry) * units, 4) if level and units else None  # noqa: E731
        dist = lambda level: round(100 * (level - mark) / mark, 3) if level and mark else None  # noqa: E731
        extra |= {
            "risk_usd": at(p.stop_loss), "reward_usd": at(p.take_profit),
            "sl_distance_pct": dist(p.stop_loss), "tp_distance_pct": dist(p.take_profit), "liq_distance_pct": dist(p.liquidation_price),
            "move_pct": round(100 * d * (mark - entry) / entry, 3) if entry else None,
        }
        risk = extra["risk_usd"]
        extra["r_now"] = round(p.unrealized_pnl / -risk, 2) if risk and risk < 0 else None
        extra["reward_risk"] = round(extra["reward_usd"] / -risk, 2) if risk and risk < 0 and extra["reward_usd"] else None
        return extra

    async def orders(self, broker: str | None = None, limit: int = 300) -> list[dict[str, Any]]:
        return self.system.store.recent_orders(min(limit, 2000), broker=broker)

    async def open_orders(self, broker: str | None = None) -> list[dict[str, Any]]:
        return await self._call(self._broker(broker).open_orders())

    async def cancel_order(self, client_order_id: str) -> dict[str, Any]:
        """Cancel a pending/open order by its client_order_id."""
        order = self.system.store.order(client_order_id)
        if not order:
            raise ApiError(404, f"Order {client_order_id} not found")
        if order["status"] not in ("pending", "open", "unknown"):
            raise ApiError(400, f"Cannot cancel order in status '{order['status']}'")

        broker_name = order.get("broker") or "paper"
        broker = self.system.brokers.get(broker_name)
        if broker and hasattr(broker, "cancel_order"):
            try:
                await broker.cancel_order(order_id=order.get("order_id") or "", client_order_id=client_order_id)
            except Exception as e:
                log.warning("Broker cancel error for %s: %s", client_order_id, e)

        self.system.store.update_order(client_order_id, "cancelled", error="Cancelled by user")
        if self.system.bus:
            from tradebuddy.events import OrderUpdate

            self.system.bus.publish(
                OrderUpdate(
                    client_order_id=client_order_id,
                    order_id=order.get("order_id") or "",
                    symbol=order.get("symbol") or "",
                    state="cancelled",
                )
            )
        return {"status": "cancelled", "client_order_id": client_order_id}

    async def cancel_all_pending(self, broker: str | None = None) -> dict[str, Any]:
        """Cancel all pending/open orders for a broker or across all brokers."""
        orders = self.system.store.recent_orders(limit=1000, broker=broker)
        pending = [o for o in orders if o.get("status") in ("pending", "open", "unknown")]
        cancelled = []
        for o in pending:
            cid = o["client_order_id"]
            try:
                await self.cancel_order(cid)
                cancelled.append(cid)
            except Exception as e:
                log.warning("Failed to cancel %s: %s", cid, e)
        return {"cancelled": cancelled, "count": len(cancelled)}

    async def account(self, broker: str | None = None) -> dict[str, Any]:
        return (await self._call(self._broker(broker).account())).to_dict()

    async def paper_stats(self) -> dict[str, Any]:
        return self.system.paper.stats()

    async def paper_history(self, page: int = 1, per_page: int = 25, strategy: str = "", q: str = "", outcome: str = "") -> dict[str, Any]:
        """Closed paper trades a page at a time, filtered, each with its SL / TP and option legs."""
        return await asyncio.to_thread(self.system.paper.history, page, per_page, strategy[:64], q[:64], outcome)

    async def paper_trades(self, limit: int = 300) -> list[dict[str, Any]]:
        return self.system.paper.trades(min(limit, 5000))

    async def ai_report(self, symbol: str = "BTCUSD") -> dict[str, Any]:
        """The newest written summary for one symbol, with why there isn't one when there isn't.

        Three ways to have nothing: the feature is off, the key is missing, or the last attempts
        failed. They need different actions from the reader, so they are reported differently
        rather than all collapsing into an empty card."""
        s = self.system.settings
        latest = await asyncio.to_thread(self.system.store.latest_ai_report, symbol)
        recent = await asyncio.to_thread(self.system.store.ai_reports, symbol, 5)
        failures = [r for r in recent if not r["ok"]]
        return {
            "symbol": symbol,
            "enabled": bool(s.ai_enabled),
            "has_key": bool(self.system.cfg.mistral_api_key),
            "model": s.ai_model,
            "every_minutes": s.ai_interval_minutes,
            "report": latest["report"] if latest else None,
            "ts": latest["ts"] if latest else None,
            "bar_time": latest["bar_time"] if latest else None,
            "took_ms": latest["ms"] if latest else None,
            "tokens": latest["tokens"] if latest else None,
            "recent_failures": [{"ts": r["ts"], "error": r["error"]} for r in failures][:3],
            "source": provenance.of("written"),
        }

    async def metrics(self) -> dict[str, Any]:
        return self.system.metrics()

    async def analysis(self) -> dict[str, Any]:
        """Per symbol: the perpetual's 24h stats, the options summary and chains, and the latest analysis."""
        s, prices = self.system, self.system.prices.snapshot()
        symbols = sorted(set(UNDERLYINGS) | set(s.options_latest) | set(s.analysis_latest))
        return {
            "symbols": {
                sym: {
                    "price": (prices.get(sym) or {}).get("price"),
                    "stats": (prices.get(sym) or {}).get("stats"),
                    "options": s.options_latest.get(sym),
                    "analysis": s.analysis_latest.get(sym),
                }
                for sym in symbols
            },
        }

    async def options_history(self, symbol: str = "BTCUSD", hours: float = 24) -> list[dict[str, Any]]:
        if symbol not in UNDERLYINGS:
            raise ApiError(404, f"no options for {symbol!r}")
        hours = min(max(hours, 1), 24 * 30)
        step = 60 if hours <= 24 else 900  # a minute apart for a day, a bar apart beyond
        return self.system.store.options_history(UNDERLYINGS[symbol], time.time() - hours * 3600, step)

    async def options_desk(self, symbol: str = "BTCUSD") -> dict[str, Any]:
        """Comprehensive options data, chain, and multi-leg setups for the Options Desk."""
        from dataclasses import asdict
        from tradebuddy.options import OptionQuote
        from tradebuddy.strategies.iron_condor import (
            analyze_daily_regime,
            build_all_setups,
        )

        s = self.system
        spot = s.prices.price(symbol)
        opt_data = s.options_latest.get(symbol) or {}
        summary = opt_data.get("summary") or {}
        if not spot and summary.get("spot"):
            spot = float(summary["spot"])

        underlying = UNDERLYINGS.get(symbol, "BTC")
        quotes = s.options_book.for_underlying(underlying) if s.options_book else []

        if not quotes and summary.get("chains"):
            for exp_key, rows in summary["chains"].items():
                for row in rows:
                    for side_key in ("call", "put"):
                        if leg := row.get(side_key):
                            quotes.append(
                                OptionQuote(
                                    symbol=leg.get("symbol", ""),
                                    underlying=underlying,
                                    kind=side_key,
                                    strike=float(row.get("strike", 0)),
                                    expiry=float(exp_key),
                                    mark=float(leg["mark"]) if leg.get("mark") else None,
                                    bid=float(leg["bid"]) if leg.get("bid") else None,
                                    ask=float(leg["ask"]) if leg.get("ask") else None,
                                    mark_iv=float(leg["iv"]) if leg.get("iv") else None,
                                    delta=float(leg["delta"]) if leg.get("delta") else None,
                                    oi=float(leg["oi"]) if leg.get("oi") else 0.0,
                                )
                            )

        # Group quotes by expiry
        quotes_by_expiry: dict[str, list[OptionQuote]] = {}
        for q in quotes:
            exp_key = str(int(q.expiry))
            quotes_by_expiry.setdefault(exp_key, []).append(q)

        setups_by_expiry: dict[str, dict[str, Any]] = {}
        for exp_key, exp_quotes in quotes_by_expiry.items():
            setups_by_expiry[exp_key] = build_all_setups(exp_quotes, spot or 0.0)

        first_exp = sorted(quotes_by_expiry.keys())[0] if quotes_by_expiry else None
        setups_dict = setups_by_expiry.get(first_exp) if first_exp else build_all_setups(quotes, spot or 0.0)

        bars_15m = s.store.bars(symbol, "15m", limit=3000) if s.store else []
        regime = analyze_daily_regime(
            spot=spot or 0.0,
            candles_15m=bars_15m,
            stats=(s.prices.snapshot().get(symbol) or {}).get("stats"),
            summary=summary,
            setups=setups_dict,
        )

        return {
            "symbol": symbol,
            "underlying": underlying,
            "spot": spot,
            "stats": (s.prices.snapshot().get(symbol) or {}).get("stats"),
            "summary": summary,
            "setups": setups_dict,
            "setups_by_expiry": setups_by_expiry,
            "regime": asdict(regime),
        }

    def _find_option_mark(self, sym: str) -> float | None:
        """Find mark price for an option symbol from cached snapshots."""
        for snap in self.system.options_latest.values():
            chains = (snap.get("summary") or {}).get("chains") or {}
            for rows in chains.values():
                for row in rows:
                    c = row.get("call") or {}
                    if c.get("symbol") == sym and c.get("mark") is not None:
                        return float(c["mark"])
                    p = row.get("put") or {}
                    if p.get("symbol") == sym and p.get("mark") is not None:
                        return float(p["mark"])
        return None

    async def options_order(
        self,
        broker: str,
        strategy: str,
        legs: list[dict[str, Any]],
        request_id: str,
    ) -> dict[str, Any]:
        """Execute one or more options legs as a coordinated structure."""
        if not legs:
            raise ApiError(400, "no legs provided")
        self._broker(broker)
        results, errors = [], []
        for i, leg in enumerate(legs):
            sym = leg.get("symbol")
            side = leg.get("side", "buy")
            size = int(leg.get("size") or 1)
            leg_price = leg.get("price")
            if sym:
                if leg_price is not None:
                    try:
                        self.system.prices.set_price(sym, float(leg_price))
                    except (ValueError, TypeError):
                        pass
                if self.system.prices.price(sym) is None:
                    mark = self._find_option_mark(sym)
                    if mark is not None:
                        self.system.prices.set_price(sym, mark)
            leg_req = f"{request_id}-{i}"
            try:
                order = await self.system.trader.manual(
                    broker=broker,
                    symbol=sym,
                    side=side,
                    size=size,
                    stop_loss=None,
                    take_profit=None,
                    request_id=leg_req,
                    margin_pct=None,
                    strategy=strategy,
                )
                results.append(order)
            except Exception as exc:
                errors.append(f"{sym} {side}: {exc}")
        if not results:
            raise ApiError(409, "; ".join(errors) or "failed to place option legs")
        return {
            "strategy": strategy,
            "broker": broker,
            "executed_legs": len(results),
            "orders": results,
            "errors": errors,
        }

    async def bars(self, symbol: str = "BTCUSD", interval: str = "15m", days: float = 7, limit: int = 5000) -> dict[str, Any]:
        """Candles with their open interest and funding, oldest first, and where the gaps are.

        Two timeframes are stored — the base (1m) and the one the strategies run on (15m). Every
        other one is **folded from whichever of those divides it**, so the hourly candle is exactly
        the four fifteen-minute candles underneath it rather than a separate download that may
        disagree with them. `derived_from` says which, and `bars[i].coverage` how much of each
        folded bar was actually there.

        Gaps are returned rather than smoothed over: a chart that joins across a missing hour draws
        a move the market never made."""
        if interval not in resample.BAR_SECONDS:
            raise ApiError(400, f"unknown interval {interval!r}; one of: {', '.join(resample.BAR_SECONDS)}")
        days = min(max(days, 0.25), 365)
        limit = min(max(int(limit), 1), MAX_BARS)
        since = time.time() - days * 86_400
        source = _bar_source(interval)
        # Read enough of the source to build `limit` folded bars, plus a little slack for holes.
        per = resample.divides(source, interval)
        rows = await asyncio.to_thread(self.system.store.bars, symbol, source, since, min(limit * per + per, MAX_BARS * 2))
        if source == interval:
            gaps = await asyncio.to_thread(self.system.store.bar_gaps, symbol, interval, since)
            out = rows[-limit:]
        else:
            folded = resample.fold(rows, source, interval)
            out = folded[-limit:]
            gaps = resample.gaps(out, interval)
        return {
            "symbol": symbol, "interval": interval, "days": days, "bars": out,
            "source_interval": source, "derived": source != interval,
            "gaps": [{"after": a, "before": b} for a, b in gaps],
        }

    async def outlook(self, symbol: str = "BTCUSD") -> dict[str, Any]:
        """Which way each timeframe leans, how far price can travel by when, and how often that has
        actually worked out. The engine builds and caches it, so the page and any strategy that
        reads it are looking at one reading rather than two that drifted apart."""
        return await self.system.outlook(symbol)

    async def pulse(self, symbol: str = "BTCUSD") -> dict[str, Any]:
        """Is a big move happening on this symbol, how big it could be, and what usually follows.

        Three questions and only two answers: the measurement and the size are arithmetic, the
        direction is COUNTED against its own baseline and withheld entirely under
        `outlook.MIN_SAMPLE`. The page is expected to render that asymmetry, not flatten it."""
        return await self.system.pulse(symbol)

    async def market_structure(self, symbol: str = "BTCUSD") -> dict[str, Any]:
        """Where the stops are, which zones are still live, where the volume actually traded, and
        who has been leaning on it.

        Four readings that belong together: a zone is worth more with a pool of stops behind it and
        the profile saying price was rejected there. The engine builds and caches it per bar, so
        the page and any strategy that reads it see one answer rather than two that drifted.

        Everything under `flow` is estimated from candles rather than read off a trade tape and
        carries `approx: true` — the UI badges it, because a footprint built from candles is not a
        footprint."""
        return await self.system.market_structure(symbol)

    async def verdicts(self, symbol: str = "BTCUSD", days: float = 30) -> dict[str, Any]:
        """Each card's verdict right now, and how that card has done since it started making them.

        The record is the point: "the term structure says an event is priced" is worth as much as
        the number of times that card has been right, beside the number you would have got by
        saying nothing at all."""
        s = self.system
        summary = (s.options_latest.get(symbol) or {}).get("summary")
        # The same rows the ATM IV and put/call charts draw (a minute apart, last 24h), not the
        # 15-minute series a strategy reads: a badge that disagrees with the line above it is worse
        # than no badge.
        history = await asyncio.to_thread(
            s.store.options_history, UNDERLYINGS.get(symbol, symbol), time.time() - 24 * 3600, 60,
        )
        analysis = s.analysis_latest.get(symbol) or {}
        view = await self.outlook(symbol)
        # With the view, `all_for` also produces the outlook, volume, indicator and shape verdicts —
        # one per tab. Passing None here is what left four tabs with no verdict on them.
        now = verdict.all_for(
            symbol, summary, history, analysis.get("insights") or [],
            (analysis.get("context") or {}).get("lean"), view,
        )
        days = min(max(days, 1), 180)
        since = time.time() - days * 86_400
        rows = await asyncio.to_thread(s.store.verdict_record, symbol, since)
        # The directional claims, settled or not, so the structure chart can draw what was said
        # against what the market then did. Unsettled ones matter most: they are the recent edge.
        history = await asyncio.to_thread(s.store.verdicts_between, symbol, since)
        return {
            "symbol": symbol, "days": days, "verdicts": now, "record": verdict.score(rows),
            "history": _one_claim_per_bar(history),
        }

    async def snapshots(self, symbol: str = "BTCUSD", days: float = 7) -> dict[str, Any]:
        """The analyst's numbers per bar, as stored. What base rates will be counted from."""
        days = min(max(days, 0.25), 365)
        rows = await asyncio.to_thread(self.system.store.snapshots, symbol, time.time() - days * 86_400)
        return {"symbol": symbol, "days": days, "points": rows}

    async def database(self) -> dict[str, Any]:
        """Tables, row counts and sizes. Counting a large table takes a moment, so the answer is reused for 30s."""
        at, stats = self._db_stats
        if time.time() - at > 30:
            stats = await asyncio.to_thread(self.system.store.db_stats)
            self._db_stats = (time.time(), stats)
        return stats | {"measured_at": self._db_stats[0]}

    async def send_email_report(self, date: str = "") -> dict[str, Any]:
        """Email one day's report now (blank = today). It goes in the background: SMTP can take longer than
        an RPC, so the outcome arrives as an EmailReport event (and in the Event Log)."""
        s = self.system.settings
        if not s.email_ready:
            raise ApiError(400, "Turn on Daily Email Report in Settings and set a recipient and SMTP host first.")
        day = self._report_day(date)
        self.system.reporter.send_soon(day)
        recipients = len(s.email_recipients)
        return {"status": "queued", "day": day, "message": f"Sending the {day} report to {recipients} recipient(s)…"}

    def _report_day(self, date: str) -> str:
        tz = self.system.settings.day_timezone
        try:
            day = parse_day(date)
        except ValueError as exc:
            raise ApiError(400, "date must be YYYY-MM-DD") from exc
        today = get_day_bounds(None, tz)[2]
        if day and day.isoformat() > today:
            raise ApiError(400, f"{day} has not happened yet")
        return day.isoformat() if day else today

    async def journal_summary(self, date: str = "") -> dict[str, Any]:
        """Everything that happened on one local day: trades, orders, timeline, skips, risk and system."""
        tz = self.system.settings.day_timezone
        day = self._report_day(date)
        live = await gather_live(self.system) if day == get_day_bounds(None, tz)[2] else None
        return await asyncio.to_thread(collect_day, self.system.store, day, tz, live)

    async def journal_month(self, year: int = 0, month: int = 0) -> dict[str, Any]:
        """PnL, trades and orders per day of one month (default: this month, in day_timezone)."""
        tz = self.system.settings.day_timezone
        if not year or not month:
            now = datetime.now(ZoneInfo(tz))
            year, month = now.year, now.month
        if not 2000 <= int(year) <= 2100 or not 1 <= int(month) <= 12:
            raise ApiError(400, "year or month out of range")
        store = self.system.store
        days = await asyncio.to_thread(collect_monthly_calendar_data, store, int(year), int(month), tz)
        # Live equity for the month in progress; a finished month closed where it closed. The
        # open figure comes from the broker rather than being inferred, so a balance that disagrees
        # with the trade record shows up as itself instead of as a phantom position.
        equity, open_pnl = None, 0.0
        try:
            account = await self._broker("paper").account()
            equity, open_pnl = account.equity, account.unrealized_pnl
        except BrokerError:
            pass
        summary = await asyncio.to_thread(
            collect_month_summary, store, int(year), int(month), days, tz, equity, open_pnl,
        )
        return {"days": days, "month": summary}

    async def events(self, limit: int = 300, type: str = "", level: str = "") -> list[dict[str, Any]]:
        """`level` "warning" -> warnings and errors, "error" -> errors only."""
        types = [type] if type else None
        if level in LEVEL_TYPES:
            types = [t for t in LEVEL_TYPES[level] if not types or t in types] or ["-"]
        return self.system.store.recent_events(min(limit, 2000), types=types)

    async def settings(self) -> dict[str, Any]:
        return self.system.settings.public() | {"token_required": bool(self.system.cfg.api_token)}

    # -- writes -------------------------------------------------------------

    async def update_settings(self, changes: dict[str, Any], confirm: str = "") -> dict[str, Any]:
        try:
            await self.system.update_settings(changes, confirm)
        except SettingsError as exc:
            raise ApiError(400, str(exc)) from exc
        return await self.settings()

    async def clear_credentials(self) -> dict[str, Any]:
        await self.system.clear_credentials()
        return await self.settings()

    async def test_delta(self, env: str | None = None, api_key: str = "", api_secret: str = "") -> dict[str, Any]:
        return await self.system.test_delta(env, api_key.strip(), api_secret.strip())

    async def toggle(self, key: str, enabled: bool) -> dict[str, Any]:
        try:
            self.system.set_toggle(key, enabled)
        except ValueError as exc:
            raise ApiError(400, str(exc)) from exc
        return {"key": key, "enabled": enabled}

    async def close_all(self) -> dict[str, Any]:
        return await self.system.close_all()

    async def close_position(self, broker: str, symbol: str) -> dict[str, Any]:
        """Closes on the named broker only: a click on one account never touches another."""
        return await self._call(self._broker(broker).close_position(symbol))

    async def protection(self, broker: str, symbol: str, stop_loss: float, take_profit: float | None = None) -> dict[str, Any]:
        if not stop_loss or stop_loss <= 0:
            raise ApiError(400, "a stop loss is required: a position is never left without one")
        async with self.system.protection_lock:
            await self._call(self._broker(broker).update_protection(symbol, stop_loss, take_profit or None))
        return {"broker": broker, "symbol": symbol, "stop_loss": stop_loss, "take_profit": take_profit or None}

    async def place_order(
        self, brokers: list[str], symbol: str, side: str, request_id: str, stop_loss: float | None = None,
        take_profit: float | None = None, size: int | None = None, margin_pct: float | None = None,
    ) -> dict[str, Any]:
        """One ticket, one or more brokers. With margin_pct each broker sizes from its own available
        margin, so paper and Delta open the same position as a share of their capital. Each broker is
        gated and recorded on its own; one refusing never stops another. The outcomes arrive as
        OrderPlaced / OrderFailed / OrderUnknown."""
        if not brokers:
            raise ApiError(400, "choose at least one broker")
        if size is not None and len(brokers) > 1:
            raise ApiError(400, "contracts mean different exposure on each broker: size several brokers by margin_pct")
        for name in brokers:
            self._broker(name)
        orders, errors = [], {}
        for name in dict.fromkeys(brokers):
            try:
                orders.append(await self.system.trader.manual(name, symbol, side, size, stop_loss, take_profit, request_id, margin_pct))
            except TradeRefused as exc:
                errors[name] = str(exc)
        if not orders:
            raise ApiError(409, "; ".join(f"{b}: {e}" for b, e in errors.items()))
        return {"orders": orders, "errors": errors}

    # -- option structures (paper) --------------------------------------------------------

    async def order_ticket(self, symbol: str, side: str = "buy", margin_pct: float | None = None) -> dict[str, Any]:
        """Defaults for the order form, and what the ticket would open on each active broker."""
        s = self.system.settings
        price = self.system.prices.price(symbol)
        if price is None:
            raise ApiError(409, f"no fresh live price for {symbol}")
        spec = await self._call(self.system.market_client.product(symbol))
        tick = float(spec.get("tick_size") or 0.5)
        cv = float(spec.get("contract_value") or 1.0)
        pct = s.trade_margin_pct if margin_pct is None else margin_pct
        d = 1 if side == "buy" else -1
        brokers = []
        for name, b in self.system.active.items():
            row: dict[str, Any] = {"broker": name, "real_money": name == "delta" and s.is_real_money,
                                   "trading": self.system.trading_on(name), "blocked": self.system.guard.halt_reason(name) or b.not_ready()}
            try:
                account = await b.account()
                size = await b.size_for_margin(symbol, price, account.available * pct / 100)
                row |= {"available": account.available, "currency": account.currency, "size": size, "notional": size * cv * price,
                        "margin": account.available * pct / 100, "error": ""}
            except BrokerError as exc:
                row |= {"available": None, "size": 0, "notional": 0, "margin": 0, "error": str(exc)}
            brokers.append(row)
        return {
            "symbol": symbol, "side": side, "price": price, "tick_size": tick, "contract_value": cv, "margin_pct": pct,
            "stop_loss": float(round_to_tick(price * (1 - d * s.stop_loss_pct / 100), tick)),
            "take_profit": float(round_to_tick(price * (1 + d * s.take_profit_pct / 100), tick)),
            "stats": (self.system.prices.snapshot().get(symbol) or {}).get("stats"), "brokers": brokers,
        }

    async def set_position_control(self, broker: str, symbol: str, trailing: bool | None = None, max_steps: int | None = None) -> dict[str, Any]:
        """Per-position trailing: switch it on or off, or change how many times it may trail."""
        self._broker(broker)
        changes: dict[str, Any] = {}
        if trailing is not None:
            changes["trailing"] = bool(trailing)
        if max_steps is not None:
            if isinstance(max_steps, bool) or int(max_steps) != max_steps or not 0 <= max_steps <= 50:
                raise ApiError(400, "max trails must be a whole number from 0 to 50")
            changes["max_steps"] = int(max_steps)
        if not changes:
            raise ApiError(400, "nothing to change")
        if not self.system.store.update_control(broker, symbol, **changes):
            raise ApiError(404, f"no tracked {symbol} position on {broker} yet — the guard picks new positions up within seconds")
        return self.system.store.controls(broker)[symbol]

    async def risk(self) -> dict[str, Any]:
        s = self.system.settings
        return {
            "brokers": [self.system.guard.status.get(name, {"broker": name}) for name in s.active_brokers],
            "trailing": {k: getattr(s, k) for k in ("trailing_enabled", "trailing_trigger_pct", "trailing_extend_pct", "trailing_lock_pct", "trailing_max_steps")},
            "daily_loss_limit_pct": s.daily_loss_limit_pct, "strategy_loss_limit_pct": s.strategy_loss_limit_pct, "day_timezone": s.day_timezone,
            "sizing": {k: getattr(s, k) for k in ("risk_per_trade_pct", "trade_margin_pct", "min_edge_multiple")},
            "protection": {k: getattr(s, k) for k in ("atr_protection", "atr_stop_mult", "atr_target_mult", "stop_loss_pct", "take_profit_pct")},
        }

    async def risk_resume(self, broker: str) -> dict[str, Any]:
        """Lift today's daily-loss halt on one broker. Its day restarts from the current equity."""
        from tradebuddy.guard import trading_day

        b = self._broker(broker)
        day = trading_day(self.system.settings.day_timezone)
        if not self.system.store.day_risk(broker, day):
            raise ApiError(404, f"{broker} has no risk record for {day}")
        account = await self._call(b.account())
        self.system.store.resume_day(broker, day, account.equity)
        await self.system.guard.check()
        return await self.risk()

    async def paper_reset(self) -> dict[str, Any]:
        self.system.paper.reset()
        return self.system.paper.stats()

    async def verify_pin(self, pin: str) -> bool:
        """Verify 4-digit PIN against stored hash or configured fallback."""
        pin_clean = str(pin).strip()
        settings = self.system.store.load_settings()
        stored_hash = settings.get("auth_pin_hash")
        if stored_hash:
            return auth.verify_pin(pin_clean, stored_hash)

        # Fallback to configured auth_pin (or empty means auth is off)
        expected = self.system.cfg.auth_pin
        if not expected:
            return True
        if pin_clean == expected:
            # SECURITY: Auto-hash and persist the PIN into the SQLite store
            hashed = auth.hash_pin(pin_clean)
            self.system.store.save_settings({"auth_pin_hash": hashed})
            return True
        return False

    async def change_pin(self, old_pin: str, new_pin: str) -> dict[str, Any]:
        """Verify current PIN and update to a new 6-digit PIN."""
        if not await self.verify_pin(old_pin):
            raise ApiError(400, "Current PIN is incorrect")
        new_clean = str(new_pin).strip()
        if len(new_clean) != 6 or not new_clean.isdigit():
            raise ApiError(400, "New PIN must be exactly 6 digits")
        hashed = auth.hash_pin(new_clean)
        self.system.store.save_settings({"auth_pin_hash": hashed})
        return {"ok": True, "message": "PIN updated successfully"}

    async def get_auth_status(self) -> dict[str, Any]:
        """Whether the PIN is active, custom, and — the one that matters — still the published default.

        `config.require_safe_bind` can only guess at this: when the PIN is changed from the Settings
        page it lives here as an Argon2 hash and never reaches the environment. This is the only
        place that can answer it, so this is where it is answered."""
        settings = self.system.store.load_settings()
        stored_hash = settings.get("auth_pin_hash")
        enabled = bool(stored_hash or self.system.cfg.auth_pin)
        if stored_hash:
            default = await asyncio.to_thread(auth.verify_pin, DEFAULT_AUTH_PIN, stored_hash)
        else:
            default = self.system.cfg.auth_pin == DEFAULT_AUTH_PIN
        return {
            "enabled": enabled,
            "has_custom_pin": bool(stored_hash),
            "using_default_pin": bool(enabled and default),
        }


class RemoteApi:
    """Same methods as Api, answered by the engine process over ZeroMQ."""

    def __init__(self, client: RpcClient, local_metrics=None) -> None:
        self.client = client
        self.local_metrics = local_metrics  # the web process adds its own load to /metrics

    def __getattr__(self, method: str):
        if method not in METHODS:
            raise AttributeError(method)

        async def call(**params: Any) -> Any:
            try:
                result = await self.client.call(method, **params)
            except RpcError as exc:
                raise ApiError(exc.status, exc.detail) from exc
            if method == "metrics" and self.local_metrics:
                result["processes"] = [*result.get("processes", []), self.local_metrics()]
            return result

        return call
