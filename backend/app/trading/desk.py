"""The options desk's read side: the chain, its numbers, the regime, and a structure built and
assessed from them. Shared by the dashboard, the trading service and the auto-trader, so all three
see one market."""

from __future__ import annotations

import time
from typing import Any

from app.errors import NotFound, Refused
from app.market import options as chain
from app.market.feed import MarketFeed
from app.market.indicators import realised_vol
from app.options import regime as regime_mod
from app.options.assess import DEFAULT_FEE, FeeRate, assess
from app.options.strategies import CATALOG, Plan, build, custom
from app.settings import Settings

RV_BARS = {"24h": 97, "7d": 673}


class Desk:
    def __init__(self, feed: MarketFeed, settings: Any) -> None:
        self.feed = feed
        self.settings: Any = settings  # () -> Settings
        self._fees: dict[str, FeeRate] = {}
        self._rv: dict[str, tuple[int, dict[str, float | None]]] = {}

    # -- market ----------------------------------------------------------------------------

    def spot(self, underlying: str) -> float:
        spot = self.feed.spot(underlying)
        if not spot:
            raise Refused(f"no fresh {underlying} price: the market feed is down or stale")
        return spot

    def expiries(self, underlying: str) -> list[dict[str, Any]]:
        spot = self.spot(underlying)
        now = time.time()
        return [chain.expiry_summary(qs, spot, now) for qs in chain.by_expiry(self.feed.quotes(underlying), now).values()]

    def quotes(self, underlying: str, expiry: float) -> list[chain.OptionQuote]:
        quotes = self.feed.quotes(underlying, expiry)
        if not quotes:
            raise NotFound(f"no fresh {underlying} options for that expiry")
        return quotes

    def summary(self, underlying: str, expiry: float) -> dict[str, Any]:
        return chain.expiry_summary(self.quotes(underlying, expiry), self.spot(underlying), time.time())

    def default_expiry(self, underlying: str) -> float:
        s: Settings = self.settings()
        quotes = self.feed.quotes(underlying)
        expiry = chain.pick_expiry(quotes, s.expiry_min_hours, s.expiry_max_hours) or next(iter(chain.by_expiry(quotes)), None)
        if expiry is None:
            raise NotFound(f"no live {underlying} options")
        return expiry

    def realised(self, underlying: str) -> dict[str, float | None]:
        """Realised volatility over 24h and 7d from the stored 15m bars, and their mean."""
        bars = self.feed.bars(chain.PERPETUALS[underlying], RV_BARS["7d"])
        last = bars[-1]["time"] if bars else 0
        cached = self._rv.get(underlying)
        if cached and cached[0] == last:
            return cached[1]
        closes = [b["close"] for b in bars]
        out: dict[str, float | None] = {
            span: realised_vol(closes[-n:], 0.25) if len(closes) >= min(n, 200) else None for span, n in RV_BARS.items()
        }
        known = [v for v in out.values() if v]
        out["reference"] = sum(known) / len(known) if known else None
        self._rv[underlying] = (last, out)
        return out

    def regime(self, underlying: str, expiry: float | None = None) -> dict[str, Any]:
        expiry = expiry or self.default_expiry(underlying)
        bars = self.feed.bars(chain.PERPETUALS[underlying], 3000)
        result = regime_mod.analyze(bars, self.spot(underlying), self.summary(underlying, expiry))
        return result | {"underlying": underlying, "expiry": expiry}

    def chain(self, underlying: str, expiry: float) -> dict[str, Any]:
        spot = self.spot(underlying)
        quotes = self.quotes(underlying, expiry)
        return {
            "underlying": underlying, "expiry": expiry, "spot": spot,
            "summary": chain.expiry_summary(quotes, spot, time.time()),
            "rows": chain.chain_rows(quotes, spot),
        }

    def overview(self, underlying: str) -> dict[str, Any]:
        perp = self.feed.perp(underlying)
        return {
            "underlying": underlying,
            "spot": self.feed.spot(underlying),
            "perpetual": chain.PERPETUALS[underlying],
            "mark": perp.mark if perp else None,
            "stats": perp.stats if perp else None,
            "expiries": self.expiries(underlying),
            "default_expiry": self.default_expiry(underlying),
            "realised_vol": self.realised(underlying),
        }

    # -- structures ------------------------------------------------------------------------

    async def fee_rate(self, symbol: str) -> FeeRate:
        if symbol not in self._fees:
            try:
                product = await self.feed.rest.product(symbol)
                specs = product.get("product_specs") or {}
                self._fees[symbol] = FeeRate(
                    taker=float(product.get("taker_commission_rate") or DEFAULT_FEE[0]),
                    premium_cap=float(specs.get("premium_commission_rate") or DEFAULT_FEE[1]),
                )
            except Exception:
                return FeeRate(*DEFAULT_FEE)  # not cached: the next call tries the spec again
        return self._fees[symbol]

    async def contract_value(self, symbol: str) -> float:
        """Underlying per contract, from the product spec (BTCUSD and BTC options: 0.001 BTC)."""
        try:
            return float((await self.feed.rest.product(symbol)).get("contract_value") or 0) or (0.01 if "ETH" in symbol else 0.001)
        except Exception:
            return 0.01 if "ETH" in symbol else 0.001

    def cached_fee(self, symbol: str) -> FeeRate:
        """The fee rate without a network call: what `fee_rate` read before, else the conservative default."""
        return self._fees.get(symbol, FeeRate(*DEFAULT_FEE))

    def plan(
        self, underlying: str, strategy: str, expiry: float | None = None, legs: list[dict[str, Any]] | None = None,
        short_delta: float | None = None, wing_width_pct: float | None = None,
    ) -> Plan:
        if underlying not in chain.PERPETUALS:
            raise NotFound(f"unknown underlying {underlying!r}")
        spot = self.spot(underlying)
        if strategy == "custom":
            try:
                return custom({q.symbol: q for q in self.feed.quotes(underlying)}, legs or [], spot)
            except ValueError as exc:
                raise Refused(str(exc)) from exc
        if strategy not in CATALOG:
            raise NotFound(f"unknown strategy {strategy!r}")
        s: Settings = self.settings()
        expiry = expiry or self.default_expiry(underlying)
        plan = build(strategy, self.quotes(underlying, expiry), spot, short_delta or s.short_delta, wing_width_pct or s.wing_width_pct)
        if plan is None:
            raise Refused(f"the {underlying} chain for that expiry cannot make a {CATALOG[strategy].label}")
        return plan

    async def assess(self, plan: Plan, qty: int) -> dict[str, Any]:
        rates = {leg.symbol: await self.fee_rate(leg.symbol) for leg in plan.legs}
        summary = chain.expiry_summary(self.quotes(plan.underlying, plan.expiry), plan.spot, time.time())
        hours = max(0.0, (plan.expiry - time.time()) / 3600)
        return assess(plan, qty, summary.get("atm_iv"), hours, self.settings(), rates, self.realised(plan.underlying)["reference"])
