"""The Market Desk's engine side: every reading of the market, built once and cached per bar.

The modules beside this one are pure — bars in, numbers out — and were carried over unchanged from
the v3 engine with their tests. This class owns what they cannot: reading the stored series, the
caches, the per-minute options history, the analyst that runs every five minutes, and the claims
each card makes (`verdict`), stored when they are made and marked once their horizon has passed.

Two series are read (`market.feed.SERIES`): 15m bars for up to a year — the counted odds in the
outlook and the pulse need the depth — and the 1m base the volume profile prefers. Everything else
is folded from them (`resample`), never fetched separately.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlmodel import col, delete, select

from app.analysis import (
    fibonacci,
    liquidity,
    nearby,
    orderflow,
    outlook,
    profile,
    pulse,
    reading,
    resample,
    structure,
    trendlines,
    verdict,
    zones,
)
from app.analysis.insights import insights, lean, market_context, outcome
from app.core.db import session
from app.core.events import EventHub
from app.core.models import OptionsHistory, Prediction
from app.errors import NotFound
from app.market.delta import Candle
from app.market.feed import SERIES, MarketFeed
from app.market.indicators import atr
from app.market.options import PERPETUALS, history_row, summarize

log = logging.getLogger(__name__)

OUTLOOK_BARS = 40_000  # a year of 15m bars is 35,040
STRUCTURE_BARS = 5_000
PROFILE_BASE_BARS = 3 * 1440  # three days of minutes
CONTEXT_BARS = 700  # a week of 15m bars for the analyst's market context
OPTIONS_HISTORY_DAYS = 30
PREDICTION_DAYS = 180
# A directional claim is marked on what a bracketed trade would have done, net of a round trip.
VERDICT_STOP_PCT, VERDICT_TARGET_PCT, VERDICT_COST_PCT = 1.0, 2.0, 0.14
MAX_CHART_BARS = 20_000


def candles(rows: list[dict[str, Any]]) -> list[Candle]:
    return [Candle(int(r["time"]), r["open"], r["high"], r["low"], r["close"], r.get("volume") or 0.0) for r in rows]


def one_claim_per_bar(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One directional reading per bar, for the chart: the outlook where it spoke, else the card
    most sure of itself. Stacking every card's marker on one candle is a smear, not a record."""
    best: dict[int, dict[str, Any]] = {}
    for r in rows:
        if r["stance"] not in ("bullish", "bearish"):
            continue
        bar = int(r["bar_time"])
        rank = (r["source"] == "outlook", float(r["confidence"] or 0))
        if bar not in best or rank > best[bar]["_rank"]:
            best[bar] = {
                "_rank": rank, "bar_time": bar, "source": r["source"], "stance": r["stance"], "label": r["label"],
                "price": r["price"], "horizon_hours": r["horizon_hours"], "settled": r["settled_at"] is not None,
                "correct": None if r["correct"] is None else bool(r["correct"]), "actual_return": r["actual_return"],
            }
    return [{k: v for k, v in row.items() if k != "_rank"} for _, row in sorted(best.items())]


class MarketDesk:
    def __init__(self, feed: MarketFeed, settings: Any, hub: EventHub) -> None:
        self.feed = feed
        self.settings: Any = settings
        self.hub = hub
        self.latest: dict[str, dict[str, Any]] = {}  # perpetual -> the analyst's last context and findings
        self._cache: dict[tuple[str, str], tuple[int, dict[str, Any]]] = {}
        self._recorded_bar: dict[str, int] = {}

    @staticmethod
    def perpetual(underlying: str) -> str:
        if underlying not in PERPETUALS:
            raise NotFound(f"unknown underlying {underlying!r}")
        return PERPETUALS[underlying]

    def summary(self, underlying: str) -> dict[str, Any] | None:
        return summarize(underlying, self.feed.quotes(underlying), self.feed.spot(underlying))

    async def _rows(self, symbol: str, limit: int, interval: str = "15m") -> list[dict[str, Any]]:
        return await asyncio.to_thread(self.feed.bars, symbol, limit, interval)

    async def _cached(self, kind: str, symbol: str, rows: list[dict[str, Any]], build: Any) -> dict[str, Any]:
        """Once a bar: nothing in these readings changes until the next 15m bar closes, and each
        is a scan over thousands of bars, so it runs on a thread and is kept until then."""
        last = int(rows[-1]["time"]) if rows else 0
        hit = self._cache.get((kind, symbol))
        if hit and hit[0] == last:
            return hit[1]
        view = await asyncio.to_thread(build)
        self._cache[(kind, symbol)] = (last, view)
        return view

    # -- readings ------------------------------------------------------------------------------

    async def outlook(self, underlying: str) -> dict[str, Any]:
        symbol = self.perpetual(underlying)
        rows = await self._rows(symbol, OUTLOOK_BARS)
        summary = self.summary(underlying)
        return await self._cached("outlook", symbol, rows, lambda: outlook.build(symbol, candles(rows), summary, VERDICT_STOP_PCT, VERDICT_TARGET_PCT))

    async def pulse(self, underlying: str) -> dict[str, Any]:
        symbol = self.perpetual(underlying)
        rows = await self._rows(symbol, OUTLOOK_BARS)
        return await self._cached("pulse", symbol, rows, lambda: pulse.read(symbol, candles(rows)).to_dict())

    async def structure(self, underlying: str) -> dict[str, Any]:
        """Liquidity, zones, the volume profile and estimated flow, and `nearby` — every level above
        and below merged from all four plus the option walls. The merge is redone on every read:
        the walls move every few seconds, the rest once a bar."""
        symbol = self.perpetual(underlying)
        rows = await self._rows(symbol, STRUCTURE_BARS)
        if not rows:
            return {"ready": False, "symbol": symbol, "note": "no stored bars yet — the bar collector fills them within minutes"}
        base = await self._rows(symbol, PROFILE_BASE_BARS, "1m")

        def build() -> dict[str, Any]:
            bars = candles(rows)
            highs, lows, closes = [c.high for c in bars], [c.low for c in bars], [c.close for c in bars]
            atr_now = (atr(highs, lows, closes, 14) or [0.0])[-1]
            # A minute profile is a real profile; a 15m one estimates where inside each bar the volume went.
            rich = len(base) >= 240
            return {
                "symbol": symbol, "ready": True, "bar_time": bars[-1].time, "bars": len(bars),
                "atr": round(atr_now, 2), "price": round(closes[-1], 2),
                "liquidity": liquidity.read(bars, atr_now).to_dict(),
                "zones": zones.read(bars, atr_now, "15m").to_dict(),
                "profile": profile.read(base if rich else rows, source="1m" if rich else "15m").to_dict(),
                "flow": orderflow.read(bars).to_dict(),
                "levels": reading.levels_block(bars),
                "smc": structure.read(bars).to_dict(),
                "trendlines": trendlines.analyze_trendlines(bars),
                "fibonacci": fibonacci.read(bars).to_dict(),
            }

        view = await self._cached("structure", symbol, rows, build)
        price = self.feed.spot(underlying) or view["price"]
        return view | {"nearby": nearby.read(
            price, view["atr"], view["liquidity"], view["zones"], view["profile"],
            self.summary(underlying), view.get("fibonacci"),
        )}

    async def trendlines(self, underlying: str, interval: str = "15m", limit: int = 500) -> dict[str, Any]:
        """Trendlines and multi-swing horizontal levels for the given timeframe (15m, 1h, 1d)."""
        symbol = self.perpetual(underlying)
        if interval == "15m":
            rows = await self._rows(symbol, limit, interval)
        else:
            days = 365.0 if interval == "1d" else 30.0
            bars_res = await self.bars(underlying, interval, days, limit)
            rows = bars_res.get("bars", [])
        if not rows:
            return {"ready": False, "symbol": symbol, "trendlines": [], "horizontal_clusters": []}
        return trendlines.analyze_trendlines(candles(rows)) | {"symbol": symbol, "interval": interval}

    async def fibonacci(self, underlying: str, interval: str = "15m", limit: int = 500) -> dict[str, Any]:
        """The last impulse and its levels, on one timeframe. Same source as the Market page's
        structure reading; separate so the chart can ask for 1h or 1d without rebuilding the rest."""
        symbol = self.perpetual(underlying)
        if interval == "15m":
            rows = await self._rows(symbol, limit, interval)
        else:
            days = 365.0 if interval == "1d" else 30.0
            rows = (await self.bars(underlying, interval, days, limit)).get("bars", [])
        if not rows:
            return {"ready": False, "note": "no stored bars yet", "symbol": symbol, "interval": interval}
        return fibonacci.read(candles(rows)).to_dict() | {"symbol": symbol, "interval": interval}

    def analysis(self, underlying: str) -> dict[str, Any]:
        """The price, the options book and the analyst's latest findings, for one underlying."""
        symbol = self.perpetual(underlying)
        perp = self.feed.perp(underlying)
        return {
            "underlying": underlying, "symbol": symbol,
            "price": perp.mark if perp else None, "index": perp.index if perp else None,
            "stats": perp.stats if perp else None,
            "options": self.summary(underlying),
            "analysis": self.latest.get(symbol),
        }

    async def bars(self, underlying: str, interval: str, days: float, limit: int) -> dict[str, Any]:
        """Candles for the chart, folded from whichever stored series divides `interval` — the
        hourly candle is exactly its four quarter-hours. Gaps are returned, never smoothed over."""
        symbol = self.perpetual(underlying)
        if interval not in resample.BAR_SECONDS:
            raise NotFound(f"unknown interval {interval!r}")
        usable = [s for s in SERIES if resample.seconds(interval) % resample.seconds(s) == 0]
        if not usable:
            raise NotFound(f"{interval} cannot be folded from the stored series")
        source = max(usable, key=resample.seconds)  # the coarsest that divides it reads the fewest rows
        limit = min(max(int(limit), 1), MAX_CHART_BARS)
        per = resample.divides(source, interval)
        since = time.time() - min(max(days, 0.25), 365) * 86_400
        rows = await asyncio.to_thread(self.feed.bars, symbol, min(limit * per + per, MAX_CHART_BARS * 2), source, since)
        out = rows[-limit:] if source == interval else resample.fold(rows, source, interval)[-limit:]
        return {
            "symbol": symbol, "interval": interval, "source_interval": source, "derived": source != interval, "bars": out,
            "gaps": [{"after": a, "before": b} for a, b in resample.gaps(out, interval)],
        }

    # -- options history -----------------------------------------------------------------------

    async def record_options(self) -> str:
        """One row per underlying per minute: the ATM IV and put/call history the exchange does not keep."""
        rows = []
        for underlying in self.settings().underlyings:
            if summary := self.summary(underlying):
                rows.append(history_row(summary) | {"ts": float(int(time.time() // 60 * 60))})
        if rows:
            with session() as s:
                s.exec(sqlite_insert(OptionsHistory).values(rows).on_conflict_do_nothing())
                s.commit()
        return f"{len(rows)} rows"

    @staticmethod
    def options_history(underlying: str, hours: float, step: int = 60) -> list[dict[str, Any]]:
        since = time.time() - min(max(hours, 1), 24 * OPTIONS_HISTORY_DAYS) * 3600
        with session() as s:
            rows = s.exec(select(OptionsHistory).where(OptionsHistory.underlying == underlying, col(OptionsHistory.ts) >= since).order_by(OptionsHistory.ts)).all()
        out, last = [], -1
        for r in rows:  # one row per `step` seconds
            bucket = int(r.ts // step)
            if bucket != last:
                out.append(r.model_dump())
                last = bucket
        return out

    # -- the analyst ---------------------------------------------------------------------------

    async def analyse(self) -> str:
        """Every five minutes: the market context and the rules over it (`insights`) per underlying,
        and — once per bar — every card's claim, written down so it can be marked later."""
        out = []
        for underlying in self.settings().underlyings:
            symbol = self.perpetual(underlying)
            rows = await self._rows(symbol, CONTEXT_BARS)
            if not rows:
                out.append(f"{underlying}: no bars yet")
                continue
            errors: list[str] = []
            oi, funding = await asyncio.gather(self._series("OI", symbol, errors), self._series("FUNDING", symbol, errors))
            perp = self.feed.perp(underlying)
            stats = (perp.stats | {"last": perp.mark, "mark": perp.mark, "index": perp.index}) if perp else None
            summary = self.summary(underlying)
            ctx = market_context(symbol, candles(rows), oi, funding, stats, summary)
            found = insights(ctx)
            ctx["lean"] = lean(found)
            ctx["errors"] = errors
            self.latest[symbol] = {"ts": time.time(), "context": ctx, "insights": found}
            await self._record_claims(underlying, symbol, ctx, found, summary)
            out.append(f"{underlying}: {len(found)} findings")
        self.hub.publish("MarketAnalysis", "; ".join(out), persist=False)
        return "; ".join(out)

    async def _series(self, name: str, symbol: str, errors: list[str]) -> list[Candle]:
        """Open interest or funding as 15m candles from Delta (OI:BTCUSD, FUNDING:BTCUSD), three days of them."""
        now = int(time.time())
        try:
            return await self.feed.rest.candles(f"{name}:{symbol}", "15m", now - 3 * 86_400, now)
        except Exception as exc:
            errors.append(f"{name}: {exc}")
            return []

    async def _record_claims(self, underlying: str, symbol: str, ctx: dict[str, Any], found: list[dict[str, Any]], summary: dict[str, Any] | None) -> None:
        bar_time, price = ctx.get("bar_time"), ctx.get("price")
        if not bar_time or not isinstance(price, int | float) or price <= 0 or self._recorded_bar.get(symbol) == bar_time:
            return
        self._recorded_bar[symbol] = bar_time
        view = await self.outlook(underlying)
        claims = verdict.all_for(symbol, summary, self.options_history(underlying, 24), found, ctx.get("lean"), view if view.get("ready") else None)
        rows = [
            {
                "symbol": symbol, "source": v["source"], "bar_time": int(bar_time), "ts": time.time(),
                "horizon_hours": v["horizon_hours"], "stance": v["stance"], "measure": v["measure"], "label": v["label"],
                "confidence": v["confidence"], "price": float(price), "numbers": v.get("numbers") or {},
            }
            for v in claims
        ]
        if rows:
            with session() as s:
                s.exec(sqlite_insert(Prediction).values(rows).on_conflict_do_nothing())
                s.commit()

    async def verdicts(self, underlying: str, days: float = 30) -> dict[str, Any]:
        """Each card's verdict now, and how that card has done beside saying nothing at all."""
        symbol = self.perpetual(underlying)
        analysis = self.latest.get(symbol) or {}
        view = await self.outlook(underlying)
        now = verdict.all_for(
            symbol, self.summary(underlying), self.options_history(underlying, 24), analysis.get("insights") or [],
            (analysis.get("context") or {}).get("lean"), view if view.get("ready") else None,
        )
        days = min(max(days, 1), PREDICTION_DAYS)
        with session() as s:
            rows = [r.model_dump() for r in s.exec(select(Prediction).where(Prediction.symbol == symbol, col(Prediction.ts) >= time.time() - days * 86_400)).all()]
        settled = [r for r in rows if r["settled_at"] is not None]
        return {"symbol": symbol, "days": days, "verdicts": now, "record": verdict.score(settled), "history": one_claim_per_bar(rows)}

    async def settle(self) -> str:
        """Mark every claim whose horizon has passed against what the stored bars did."""
        now = time.time()
        with session() as s:
            due = [r.model_dump() for r in s.exec(select(Prediction).where(col(Prediction.settled_at).is_(None))).all()
                   if r.bar_time + r.horizon_hours * 3600 + 900 <= now]
        marked = 0
        for symbol in sorted({r["symbol"] for r in due}):
            mine = [r for r in due if r["symbol"] == symbol]
            span_bars = int((now - min(r["bar_time"] for r in mine)) / 900) * 2 + 16
            bars = candles(await self._rows(symbol, min(span_bars, OUTLOOK_BARS)))
            marked += self._mark(mine, bars, now)
        with session() as s:
            s.exec(delete(Prediction).where(col(Prediction.ts) < now - PREDICTION_DAYS * 86_400))
            s.exec(delete(OptionsHistory).where(col(OptionsHistory.ts) < now - OPTIONS_HISTORY_DAYS * 86_400))
            s.commit()
        return f"marked {marked} of {len(due)} due"

    @staticmethod
    def _mark(rows: list[dict[str, Any]], bars: list[Candle], now: float) -> int:
        """A directional claim is marked on what a bracketed trade would have done — target before
        stop, net of costs; a volatility claim against the span before it."""
        by_time = {int(c.time): i for i, c in enumerate(bars)}
        marked = 0
        for row in rows:
            hours, bar_time = float(row["horizon_hours"]), int(row["bar_time"])
            actual = outcome(bars, bar_time, hours, 0.0)
            if actual is None:
                continue
            before = outcome(bars, bar_time - int(hours * 3600), hours, 0.0)
            prior_vol = float(before["realized_vol"]) if before else float(actual["realized_vol"])
            sigma = outlook.sigma_over(prior_vol, hours)
            claimed = naive = None
            index = by_time.get(bar_time)
            if index is not None and row["stance"] in ("bullish", "bearish"):
                ahead = max(1, round(hours * 3600 / 900))
                side = "buy" if row["stance"] == "bullish" else "sell"
                claimed = reading.barrier_outcome(bars, index, side, VERDICT_STOP_PCT, VERDICT_TARGET_PCT, ahead, VERDICT_COST_PCT)
                naive = reading.barrier_outcome(bars, index, "buy", VERDICT_STOP_PCT, VERDICT_TARGET_PCT, ahead, VERDICT_COST_PCT)
                if claimed is None or naive is None:
                    claimed = naive = None
            try:
                judged = verdict.settle(row, float(actual["ret"]), float(actual["realized_vol"]), prior_vol, sigma, claimed, naive)
            except (TypeError, ValueError) as exc:
                log.warning("verdict_unreadable source=%s bar=%s error=%s", row["source"], bar_time, exc)
                continue
            if judged is None:
                continue
            correct, baseline = judged
            with session() as s:
                p = s.get(Prediction, (row["symbol"], row["source"], bar_time))
                if p is None:
                    continue
                p.settled_at, p.actual_return, p.actual_vol, p.prior_vol = now, float(actual["ret"]), float(actual["realized_vol"]), prior_vol
                p.actual_excursion, p.correct, p.baseline_correct = float(actual["excursion"]), int(correct), int(baseline)
                p.regime = reading.regime_at(bars, index) if index is not None else "unknown"
                p.exit_reason = claimed.exit if claimed else ""
                p.r_multiple = claimed.r_multiple if claimed else None
                s.add(p)
                s.commit()
            marked += 1
        return marked
