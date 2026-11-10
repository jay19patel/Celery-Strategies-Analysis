"""The options desk's read side: the chain, its numbers, the regime, and a structure built and
assessed from them. Shared by the dashboard, the trading service and the auto-trader, so all three
see one market."""

from __future__ import annotations

import time
from typing import Any

from app.errors import NotFound, Refused
from app.market import options as chain
from app.market.feed import MarketFeed
from app.market.indicators import har_forecast, parkinson_vol, realised_vol
from app.options import gex
from app.options import ideas as ideas_mod
from app.options import regime as regime_mod
from app.options.assess import CREDIT_TAKE_PROFIT_PCT, DEBIT_TAKE_PROFIT_PCT, DEFAULT_FEE, FeeRate, assess, leg_fee
from app.options.strategies import Plan, custom
from app.settings import Settings
from app.strategies import registry
from app.strategies.base import OptionContext, OptionStrategy

RV_BARS = {"24h": 97, "7d": 673}
HAR_BARS = 180 * 96  # half a year of 15m bars fits the volatility forecast
DESK_EXPIRY_HOURS = (12.0, 60.0)  # the expiry the dashboard opens on, when none is asked for


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
        quotes = self.feed.quotes(underlying)
        expiry = chain.pick_expiry(quotes, *DESK_EXPIRY_HOURS) or next(iter(chain.by_expiry(quotes)), None)
        if expiry is None:
            raise NotFound(f"no live {underlying} options")
        return expiry

    def realised(self, underlying: str) -> dict[str, float | None]:
        """Realised volatility over 24h and 7d from the stored 15m bars (close-to-close), over 24h from
        their ranges, and the next day's as the HAR model forecasts it; the highest of them is the
        reference a seller's premium is measured against, the lowest (`floor`) the one a buyer's
        is — each side judged on the number least kind to it."""
        bars = self.feed.bars(chain.PERPETUALS[underlying], RV_BARS["7d"])
        last = bars[-1]["time"] if bars else 0
        cached = self._rv.get(underlying)
        if cached and cached[0] == last:
            return cached[1]
        closes = [b["close"] for b in bars]
        out: dict[str, float | None] = {
            span: realised_vol(closes[-n:], 0.25) if len(closes) >= min(n, 200) else None for span, n in RV_BARS.items()
        }
        out["range_24h"] = parkinson_vol(bars[-96:], 0.25) if len(bars) >= 96 else None
        out["forecast_24h"] = har_forecast(self.feed.bars(chain.PERPETUALS[underlying], HAR_BARS), 0.25)
        known = [v for v in out.values() if v]
        out["reference"] = max(known) if known else None
        out["floor"] = min(known) if known else None
        self._rv[underlying] = (last, out)
        return out

    def read_regime(self, underlying: str, expiry: float) -> dict[str, Any]:
        """The market at one expiry (`options.regime.analyze`), without any strategy's score."""
        bars = self.feed.bars(chain.PERPETUALS[underlying], 3000)
        spot = self.spot(underlying)
        exposure = gex.exposure(self.feed.quotes(underlying), spot, time.time())
        forecast = self.realised(underlying)["forecast_24h"]
        return regime_mod.analyze(bars, spot, self.summary(underlying, expiry), exposure.get("regime"), forecast)

    def regime(self, underlying: str, expiry: float | None = None) -> dict[str, Any]:
        """The regime at `expiry` and how every option strategy, as configured, scores it."""
        expiry = expiry or self.default_expiry(underlying)
        result = self.read_regime(underlying, expiry)
        s: Settings = self.settings()
        scores = {}
        for name in registry.names("options"):
            strategy = registry.make(name, s.config(name).params)
            score, reasons = strategy.score(result)
            scores[name] = {"score": score, "reasons": reasons}
        return result | {"scores": scores, "underlying": underlying, "expiry": expiry}

    def option_context(self, underlying: str, rate: FeeRate) -> OptionContext:
        """One underlying's book for the option strategies: read once, the regime read per expiry."""
        spot = self.spot(underlying)
        cache: dict[float, dict[str, Any]] = {}

        def regime(expiry: float) -> dict[str, Any]:
            if expiry not in cache:
                cache[expiry] = self.read_regime(underlying, expiry)
            return cache[expiry]

        return OptionContext(
            underlying=underlying, spot=spot, now=time.time(), quotes=self.feed.quotes(underlying), regime=regime,
            realised=self.realised(underlying), fee=lambda price: leg_fee(price, 1.0, spot, rate),
            max_spread_pct=self.settings().max_spread_pct,
        )

    async def ideas(self, underlying: str, expiry: float) -> dict[str, Any]:
        """Single-option buys and sells with an edge at realised volatility (`options.ideas`)."""
        s: Settings = self.settings()
        quotes = self.quotes(underlying, expiry)
        spot = self.spot(underlying)
        rv = self.realised(underlying)
        rate = await self.fee_rate(quotes[0].symbol)  # one schedule for every option of an underlying
        direction = float(self.regime(underlying, expiry).get("direction") or 0.0)
        out = ideas_mod.ideas(
            quotes, spot, time.time(), rv["floor"], rv["reference"],
            lambda price: leg_fee(price, 1.0, spot, rate), direction, s.max_spread_pct,
            DEBIT_TAKE_PROFIT_PCT, CREDIT_TAKE_PROFIT_PCT,
        )
        return out | {"underlying": underlying, "expiry": expiry}

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

    @staticmethod
    def _fee_key(symbol: str) -> str:
        """Every option of an underlying shares one fee schedule. Keyed per contract, the cache (and the
        REST client's product cache under it) would gain the day's new listings for as long as the
        process runs, which is meant to be years."""
        parsed = chain.parse_symbol(symbol)
        return f"{parsed[1]} options" if parsed else symbol

    async def fee_rate(self, symbol: str) -> FeeRate:
        key = self._fee_key(symbol)
        if key not in self._fees:
            try:
                product = await self.feed.rest.product(symbol)
                specs = product.get("product_specs") or {}
                self._fees[key] = FeeRate.with_gst(
                    float(product.get("taker_commission_rate") or DEFAULT_FEE[0]),
                    float(specs.get("premium_commission_rate") or DEFAULT_FEE[1]),
                )
            except Exception:
                return FeeRate.with_gst(*DEFAULT_FEE)  # not cached: the next call tries the spec again
        return self._fees[key]

    async def contract_value(self, symbol: str) -> float:
        """Underlying per contract, from the product spec (BTCUSD and BTC options: 0.001 BTC)."""
        try:
            return float((await self.feed.rest.product(symbol)).get("contract_value") or 0) or (0.01 if "ETH" in symbol else 0.001)
        except Exception:
            return 0.01 if "ETH" in symbol else 0.001

    def option_fee(self, underlying: str) -> FeeRate:
        """Every option of an underlying shares one schedule: what `fee_rate` read for any of them."""
        return self._fees.get(f"{underlying} options", FeeRate.with_gst(*DEFAULT_FEE))

    def cached_fee(self, symbol: str) -> FeeRate:
        """The fee rate without a network call: what `fee_rate` read before, else the conservative default."""
        return self._fees.get(self._fee_key(symbol), FeeRate.with_gst(*DEFAULT_FEE))

    def strategy(self, name: str, overrides: dict[str, Any] | None = None) -> OptionStrategy | None:
        """An option strategy built with its configured params, `overrides` on top (a ticket's own)."""
        cls = registry.option(name)
        if cls is None:
            return None
        params = self.settings().config(name).params | (overrides or {})
        try:
            return cls(params)
        except ValueError as exc:
            raise Refused(str(exc)) from exc

    def plan(
        self, underlying: str, strategy: str, expiry: float | None = None, legs: list[dict[str, Any]] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Plan:
        if underlying not in chain.PERPETUALS:
            raise NotFound(f"unknown underlying {underlying!r}")
        spot = self.spot(underlying)
        if strategy == "custom":
            try:
                return custom({q.symbol: q for q in self.feed.quotes(underlying)}, legs or [], spot)
            except ValueError as exc:
                raise Refused(str(exc)) from exc
        plugin = self.strategy(strategy, params)
        if plugin is None:
            raise NotFound(f"unknown strategy {strategy!r}")
        expiry = expiry or self.default_expiry(underlying)
        plan = plugin.build(self.option_context(underlying, self.option_fee(underlying)), expiry)
        if plan is None:
            raise Refused(f"the {underlying} chain for that expiry cannot make a {plugin.label}")
        return plan

    async def assess(self, plan: Plan, qty: int) -> dict[str, Any]:
        rates = {leg.symbol: await self.fee_rate(leg.symbol) for leg in plan.legs}
        summary = chain.expiry_summary(self.quotes(plan.underlying, plan.expiry), plan.spot, time.time())
        hours = max(0.0, (plan.expiry - time.time()) / 3600)
        plugin = self.strategy(plan.strategy)
        rv = self.realised(plan.underlying)["floor" if plugin and plugin.premium == "debit" else "reference"]
        s: Settings = self.settings()
        if plugin is None:
            return assess(plan, qty, summary.get("atm_iv"), hours, s.max_spread_pct, rates, rv)
        return assess(
            plan, qty, summary.get("atm_iv"), hours, s.max_spread_pct, rates, rv, label=plugin.label,
            required=plugin.required_checks, min_pop_pct=plugin.min_pop_pct, min_credit_width_pct=plugin.min_credit_width_pct,
        )
