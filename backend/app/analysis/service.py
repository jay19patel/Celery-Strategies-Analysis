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
from sqlmodel import col, select

from app.analysis import bias as bias_mod
from app.analysis import (
    fibonacci,
    flow,
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
    zones,
)
from app.analysis.insights import insights, lean, market_context
from app.core.db import session
from app.core.events import EventHub
from app.core.models import Bar, DailyBias, FlowBar, OptionsHistory
from app.errors import NotFound
from app.market.delta import Candle
from app.market.feed import SERIES, MarketFeed
from app.market.indicators import atr
from app.market.options import PERPETUALS, history_row, summarize
from app.market.tape import step_for
from app.options import gex

log = logging.getLogger(__name__)

OUTLOOK_BARS = 40_000  # a year of 15m bars is 35,040
STRUCTURE_BARS = 5_000
# Trendlines pair every two swings and walk the bars after them: 5,000 bars took minutes of CPU a
# bar on the server and starved the event loop. Three weeks find the same lines near the price.
TRENDLINE_BARS = 2_000
PROFILE_BASE_BARS = 3 * 1440  # three days of minutes
CONTEXT_BARS = 700  # a week of 15m bars for the analyst's market context
OPTIONS_HISTORY_DAYS = 30
FLOW_DAYS = 30  # the tape's minutes: a month of footprints
BIAS_DAYS = 730  # one small row a day per underlying: two years of track record
# The outlook's counted odds are for a bracketed trade: this stop and target, as % of price.
VERDICT_STOP_PCT, VERDICT_TARGET_PCT = 1.0, 2.0
MAX_CHART_BARS = 20_000


def flow_rows(symbol: str, since: float) -> list[dict[str, Any]]:
    with session() as s:
        rows = s.exec(select(FlowBar).where(FlowBar.symbol == symbol, col(FlowBar.time) >= since).order_by(col(FlowBar.time))).all()
    return [r.model_dump() for r in rows]


def options_premium(underlying: str, since: float) -> dict[str, float] | None:
    """Option premium that crossed the spread since `since`, from the minute rows."""
    with session() as s:
        rows = s.exec(select(OptionsHistory).where(
            OptionsHistory.underlying == underlying, col(OptionsHistory.ts) >= since, col(OptionsHistory.call_bought).is_not(None),
        )).all()
    if not rows:
        return None
    return {k: round(sum(getattr(r, k) or 0.0 for r in rows), 2) for k in ("call_bought", "call_sold", "put_bought", "put_sold")}


def settle_biases(underlying: str, today: int, symbol: str) -> str:
    """Mark every recorded day that has ended against its own close."""
    marked = 0
    with session() as s:
        open_days = s.exec(select(DailyBias).where(DailyBias.underlying == underlying, col(DailyBias.close).is_(None), DailyBias.day < today)).all()
        for row in open_days:
            close = s.exec(select(Bar.close).where(
                Bar.symbol == symbol, Bar.interval == "15m", col(Bar.time) < row.day + 86_400, col(Bar.time) >= row.day,
            ).order_by(col(Bar.time).desc()).limit(1)).first()
            if close is None:
                continue
            ret, right = bias_mod.settle(row.label, row.open, float(close))
            row.close, row.ret_pct, row.correct = float(close), round(100 * ret, 3), right
            s.add(row)
            marked += 1
        s.commit()
    return f"{underlying}: {marked} day(s) marked" if marked else ""


def store_flow_bars(rows: list[dict[str, Any]]) -> None:
    with session() as s:
        s.exec(sqlite_insert(FlowBar).values(rows).on_conflict_do_nothing())
        s.commit()


def candles(rows: list[dict[str, Any]]) -> list[Candle]:
    return [Candle(int(r["time"]), r["open"], r["high"], r["low"], r["close"], r.get("volume") or 0.0) for r in rows]


class MarketDesk:
    def __init__(self, feed: MarketFeed, settings: Any, hub: EventHub) -> None:
        self.feed = feed
        self.settings: Any = settings
        self.hub = hub
        self.latest: dict[str, dict[str, Any]] = {}  # perpetual -> the analyst's last context and findings
        self._cache: dict[tuple[str, str], tuple[int, dict[str, Any]]] = {}

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
        latest_time = self.feed.latest_bar_time(symbol, "15m")
        hit = self._cache.get(("outlook", symbol))
        if hit and hit[0] == latest_time and latest_time > 0:
            return hit[1]
        rows = await self._rows(symbol, OUTLOOK_BARS)
        summary = self.summary(underlying)
        return await self._cached("outlook", symbol, rows, lambda: outlook.build(symbol, candles(rows), summary, VERDICT_STOP_PCT, VERDICT_TARGET_PCT))

    async def flow(self, underlying: str, minutes: int = 5) -> dict[str, Any]:
        """Order flow from the recorded tape (`analysis.flow`), over the last day of its minutes.

        Only finished bars: the one still forming is left out, so a strategy deciding on a closed
        candle reads the tape of that same candle and no later. A tape whose newest minute is two
        bars old is no tape — the socket has gone quiet — and is not ready."""
        symbol = self.perpetual(underlying)
        now = time.time()
        span = minutes * 60
        forming = int(now // span * span)
        rows = [r for r in await asyncio.to_thread(flow_rows, symbol, now - 86_400) if r["time"] < forming]
        if rows and now - (rows[-1]["time"] + 60) > 2 * span:
            return {"ready": False, "note": f"the tape is stale: its newest minute is {int((now - rows[-1]['time']) // 60)} min old",
                    "symbol": symbol, "underlying": underlying}
        last = int(rows[-1]["time"]) if rows else 0
        hit = self._cache.get((f"flow{minutes}", symbol))
        if hit and hit[0] == last and last:
            return hit[1]
        step = step_for(symbol, rows[-1]["close"]) if rows else 1.0
        view = await asyncio.to_thread(flow.read, rows, minutes, step) | {"symbol": symbol, "underlying": underlying}
        self._cache[(f"flow{minutes}", symbol)] = (last, view)
        return view

    async def bias(self, underlying: str) -> dict[str, Any]:
        """The day's bias (`analysis.bias`) from the stored bars, gamma, the tape and funding."""
        symbol = self.perpetual(underlying)
        now = time.time()
        hit = self._cache.get(("bias", symbol))
        if hit and now - hit[0] < 60:
            return hit[1]
        today = int(now // 86_400 * 86_400)
        rows = await self._rows(symbol, 96 * 40)  # 40 days of 15m bars: enough 4h bars for structure
        spot = self.feed.spot(underlying) or (rows[-1]["close"] if rows else 0.0)
        g = gex.exposure(self.feed.quotes(underlying), spot, now) if spot else {"ready": False}
        flow_today = await asyncio.to_thread(flow_rows, symbol, today)
        cvd = (sum(r["buy"] - r["sell"] for r in flow_today), sum(r["volume"] for r in flow_today)) if flow_today else None
        premium = await asyncio.to_thread(options_premium, underlying, today)
        perp = self.feed.perp(underlying)
        view = bias_mod.read(
            candles(rows), now, gex=g if g.get("ready") else None, cvd=cvd, options=premium,
            funding_pct=(perp.stats.get("funding_rate_pct") if perp else None),
        ) | {"underlying": underlying, "symbol": symbol, "gex": {k: v for k, v in g.items() if k != "by_strike"}, "options_today": premium}
        self._cache[("bias", symbol)] = (int(now), view)
        return view

    async def gamma(self, underlying: str) -> dict[str, Any]:
        """Gamma exposure by strike now (`options.gex`), its last day recorded each minute, and the
        option premium that crossed the spread today."""
        now = time.time()
        spot = self.feed.spot(underlying)
        view = gex.exposure(self.feed.quotes(underlying), spot, now) if spot else {"ready": False, "note": f"no fresh {underlying} price"}
        since = now - 86_400
        with session() as s:
            rows = s.exec(select(OptionsHistory).where(
                OptionsHistory.underlying == underlying, col(OptionsHistory.ts) >= since, col(OptionsHistory.net_gex).is_not(None),
            ).order_by(col(OptionsHistory.ts))).all()
        history = [{"ts": r.ts, "spot": r.spot, "net": r.net_gex, "flip": r.gamma_flip} for r in rows[::5]]  # one point per 5 minutes
        premium = await asyncio.to_thread(options_premium, underlying, int(now // 86_400 * 86_400))
        return view | {"underlying": underlying, "history": history, "options_today": premium}

    async def record_bias(self) -> str:
        """Once a UTC day is half an hour old, its bias is written down; once it has ended, marked."""
        now = time.time()
        today = int(now // 86_400 * 86_400)
        out = []
        for underlying in self.settings().underlyings:
            with session() as s:
                have = s.get(DailyBias, (underlying, today))
            # Not before the chain is live: a bias written without its gamma is wrong all day, and the
            # day's record is written once.
            if have is None and now - today >= 1800 and self.feed.quotes(underlying):
                view = await self.bias(underlying)
                if view.get("ready"):
                    with session() as s:
                        s.add(DailyBias(underlying=underlying, day=today, ts=now, score=view["score"], label=view["label"],
                                        style=view["style"], gamma=view["gamma"], open=view["open"], reasons=view["reasons"]))
                        s.commit()
                    out.append(f"{underlying}: {view['label']} ({view['score']:+.2f}, {view['style']})")
            out.append(await asyncio.to_thread(settle_biases, underlying, today, self.perpetual(underlying)))
        return "; ".join(x for x in out if x)

    @staticmethod
    def bias_record(underlying: str, days: int = 90) -> dict[str, Any]:
        """The recorded days and how often a directional bias got the day's sign right."""
        since = time.time() - days * 86_400
        with session() as s:
            rows = [r.model_dump() for r in s.exec(
                select(DailyBias).where(DailyBias.underlying == underlying, col(DailyBias.day) >= since).order_by(col(DailyBias.day).desc())
            ).all()]
        scored = [r for r in rows if r["correct"] is not None]
        return {
            "days": rows, "scored": len(scored), "right": sum(1 for r in scored if r["correct"]),
            "hit_rate_pct": round(100 * sum(1 for r in scored if r["correct"]) / len(scored), 1) if scored else None,
            "note": "neutral days are not scored; a hit rate means little before about 30 scored days",
        }

    async def pulse(self, underlying: str) -> dict[str, Any]:
        symbol = self.perpetual(underlying)
        latest_time = self.feed.latest_bar_time(symbol, "15m")
        hit = self._cache.get(("pulse", symbol))
        if hit and hit[0] == latest_time and latest_time > 0:
            return hit[1]
        rows = await self._rows(symbol, OUTLOOK_BARS)
        return await self._cached("pulse", symbol, rows, lambda: pulse.read(symbol, candles(rows)).to_dict())

    async def structure(self, underlying: str) -> dict[str, Any]:
        """Liquidity, zones, the volume profile and estimated flow, and `nearby` — every level above
        and below merged from all four plus the option walls. The merge is redone on every read:
        the walls move every few seconds, the rest once a bar."""
        symbol = self.perpetual(underlying)
        latest_time = self.feed.latest_bar_time(symbol, "15m")
        hit = self._cache.get(("structure", symbol))
        if hit and hit[0] == latest_time and latest_time > 0:
            view = hit[1]
            price = self.feed.spot(underlying) or view["price"]
            return view | {"nearby": nearby.read(
                price, view["atr"], view["liquidity"], view["zones"], view["profile"],
                self.summary(underlying), view.get("fibonacci"),
            )}
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
                "trendlines": trendlines.analyze_trendlines(bars[-TRENDLINE_BARS:]),
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
        # O(swings^2 x bars): off the event loop the guard runs on.
        found = await asyncio.to_thread(trendlines.analyze_trendlines, candles(rows))
        return found | {"symbol": symbol, "interval": interval}

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
        return (await asyncio.to_thread(fibonacci.read, candles(rows))).to_dict() | {"symbol": symbol, "interval": interval}

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
        if not self.feed.record:
            return "not recorded: the feed is on the demo exchange"
        rows = []
        now = time.time()
        for underlying in self.settings().underlyings:
            flow = self.feed.tape.take_options(underlying)
            if summary := self.summary(underlying):
                g = gex.exposure(self.feed.quotes(underlying), summary["spot"], now) if summary.get("spot") else {}
                rows.append(history_row(summary) | {
                    "ts": float(int(now // 60 * 60)),
                    "net_gex": g.get("net"), "gamma_flip": g.get("flip"),
                    "gex_call_wall": g.get("call_wall"), "gex_put_wall": g.get("put_wall"),
                    "call_bought": round(flow.call_buy, 2), "call_sold": round(flow.call_sell, 2),
                    "put_bought": round(flow.put_buy, 2), "put_sold": round(flow.put_sell, 2),
                })
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
