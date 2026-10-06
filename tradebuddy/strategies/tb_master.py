"""TB Master: one strategy that reads the market like a trader, picks swing or scalp, and sets its own levels.

On every closed 15-minute bar, for BTCUSD and ETHUSD, it reads:

    1d candles    the higher-timeframe bias      close vs EMA21 vs EMA50 (up, down or neither)
    1h candles    the swing breakout             the 20-hour range, ATR(14), ADX(14), EMA21/EMA50
    15m candles   the regime and the scalp       Bollinger width rank (squeeze), volume z-score, ATR, ADX
    15m OI        who is behind the move         open interest rising with the breakout = new positions
    15m funding   how crowded each side is       shown in the reason
    options       where the walls are            a call wall above (put wall below) inside the target caps it;
                                                 ATM IV, 25-delta skew and put/call are shown in the reason

Then, in this order:

    SWING   on a bar that closes an hour: the 1h close breaks the last 20 hours' high (low), the daily bias
            is not against it, and open interest rose at least 0.5% in that hour.
            Stop 1.5 x ATR(1h), target 3 x ATR(1h).
    SCALP   otherwise: the 15m Bollinger width was in its bottom 25% of the last 100 hours (a squeeze), the
            bar closes beyond the last 6 hours' range on volume at least 1.25 sigma above normal, open interest
            rose in the last hour, and neither the 1h trend nor the daily bias is against it.
            Stop at the Bollinger middle (0.8 - 2.5 x ATR(15m)), target 3R. Typically closes within hours.
    LEVELS  are the strategy's own (is_default_sl_tp = False); an options wall in the way caps the target.
    COSTS   0.05% taker fee + 0.02% slippage a side. A trade whose reward/risk after costs is under 1.4,
            or whose stop is under 0.2% or over 4%, is skipped.
    NO TRADE on every other bar, which is most of them. The regime (trend, range, squeeze) is in the log.

Why these and not others: tested on a year of Delta India data (Aug 2025 - Oct 2026), costs included,
the last 30% held out. A breakout without rising open interest loses (stops being run, not a new move);
with it, it pays on both coins. EMA pullbacks (15m and 1h), Bollinger mean-reversion scalps, 15m range
breakouts, fading OI flushes and trading against negative funding all lost after costs. See BACKTEST.

Perpetuals only: the brokers trade the BTCUSD and ETHUSD perps. Option structures (straddles, condors)
are the analyst's playbook on the Market page, not traded here.

`decide()` is a pure function over precomputed indicator series, so the backtest runs the code that trades.
"""

from __future__ import annotations

import asyncio
import bisect
import math
from dataclasses import dataclass, field
from typing import Any, ClassVar

from tradebuddy.delta import RESOLUTION_SECONDS, Candle
from tradebuddy.strategies.base import Context, Signal, Strategy

COST_PCT = 0.07  # per side, % of price: 0.05% taker fee + 0.02% slippage

# Backtest, Aug 2025 - Oct 2026, Delta India, costs included, one position per symbol, 72h max hold.
# Filled from the research run; "out" is the last 30%, never used to choose a parameter.
BACKTEST = {
    "BTCUSD": {"trades": 121, "win_pct": 43.8, "profit_factor": 1.45, "net_pct": 32.8, "max_dd_pct": -16.6, "out_net_pct": 11.2,
               "swing": {"trades": 103, "profit_factor": 1.47}, "scalp": {"trades": 18, "profit_factor": 1.26}},
    "ETHUSD": {"trades": 86, "win_pct": 46.5, "profit_factor": 1.75, "net_pct": 49.0, "max_dd_pct": -16.5, "out_net_pct": 17.3,
               "swing": {"trades": 71, "profit_factor": 1.84}, "scalp": {"trades": 15, "profit_factor": 1.07}},
}  # net = sum of per-trade % moves, unlevered. Scalps are few (one or two a month): treat their numbers as thin.


# -- indicator series, aligned to the bars (None while warming up) ------------------------------------


def ema_series(values: list[float], n: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) < n:
        return out
    k = 2 / (n + 1)
    prev = sum(values[:n]) / n
    out[n - 1] = prev
    for i in range(n, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def wilder(values: list[float], n: int, start: int) -> list[float | None]:
    """Wilder's smoothing of values[start:], seeded with their first n mean."""
    out: list[float | None] = [None] * len(values)
    if len(values) < start + n:
        return out
    prev = sum(values[start : start + n]) / n
    out[start + n - 1] = prev
    for i in range(start + n, len(values)):
        prev = (prev * (n - 1) + values[i]) / n
        out[i] = prev
    return out


def true_range(h: list[float], lo: list[float], c: list[float]) -> list[float]:
    return [h[0] - lo[0]] + [max(h[i] - lo[i], abs(h[i] - c[i - 1]), abs(lo[i] - c[i - 1])) for i in range(1, len(c))]


def rsi_series(c: list[float], n: int = 14) -> list[float | None]:
    gains = [0.0] + [max(c[i] - c[i - 1], 0.0) for i in range(1, len(c))]
    losses = [0.0] + [max(c[i - 1] - c[i], 0.0) for i in range(1, len(c))]
    g, lo = wilder(gains, n, 1), wilder(losses, n, 1)
    return [None if a is None or b is None else (100.0 if b == 0 else 100 - 100 / (1 + a / b)) for a, b in zip(g, lo, strict=True)]


def adx_series(h: list[float], lo: list[float], c: list[float], n: int = 14) -> list[float | None]:
    size = len(c)
    plus = [0.0] * size
    minus = [0.0] * size
    for i in range(1, size):
        up, down = h[i] - h[i - 1], lo[i - 1] - lo[i]
        plus[i] = up if up > down and up > 0 else 0.0
        minus[i] = down if down > up and down > 0 else 0.0
    tr = wilder(true_range(h, lo, c), n, 1)
    p, m = wilder(plus, n, 1), wilder(minus, n, 1)
    dx = [0.0] * size
    first = None
    for i in range(size):
        if tr[i] and p[i] is not None and m[i] is not None:
            di_p, di_m = 100 * p[i] / tr[i], 100 * m[i] / tr[i]
            dx[i] = 0.0 if di_p + di_m == 0 else 100 * abs(di_p - di_m) / (di_p + di_m)
            first = i if first is None else first
    return [None] * size if first is None else wilder(dx, n, first)


def rolling_mean_std(values: list[float], n: int) -> tuple[list[float | None], list[float | None]]:
    mean: list[float | None] = [None] * len(values)
    std: list[float | None] = [None] * len(values)
    s = sq = 0.0
    for i, v in enumerate(values):
        s += v
        sq += v * v
        if i >= n:
            old = values[i - n]
            s -= old
            sq -= old * old
        if i >= n - 1:
            mu = s / n
            mean[i] = mu
            std[i] = math.sqrt(max(sq / n - mu * mu, 0.0))
    return mean, std


@dataclass
class Frame:
    """One timeframe's bars and indicators, index-aligned."""

    t: list[int]
    o: list[float]
    h: list[float]
    lo: list[float]
    c: list[float]
    v: list[float]
    ema9: list[float | None]
    ema21: list[float | None]
    ema50: list[float | None]
    atr: list[float | None]
    rsi: list[float | None]
    adx: list[float | None]
    bb_mid: list[float | None]
    bb_up: list[float | None]
    bb_lo: list[float | None]
    bbw: list[float | None]  # (upper - lower) / middle
    vol_z: list[float | None]  # log volume vs its last 96 bars

    def __len__(self) -> int:
        return len(self.c)


def frame(bars: list[Candle]) -> Frame:
    t = [b.time for b in bars]
    o, h, lo, c, v = ([getattr(b, k) for b in bars] for k in ("open", "high", "low", "close", "volume"))
    mid, sd = rolling_mean_std(c, 20)
    up = [None if m is None else m + 2 * s for m, s in zip(mid, sd, strict=True)]
    dn = [None if m is None else m - 2 * s for m, s in zip(mid, sd, strict=True)]
    bbw = [None if m is None or not m else (u - d) / m for m, u, d in zip(mid, up, dn, strict=True)]
    lv = [math.log1p(max(x, 0.0)) for x in v]
    vm, vs = rolling_mean_std(lv, 96)
    vz = [None if m is None else ((x - m) / s if s else 0.0) for x, m, s in zip(lv, vm, vs, strict=True)]
    return Frame(
        t, o, h, lo, c, v, ema_series(c, 9), ema_series(c, 21), ema_series(c, 50), wilder(true_range(h, lo, c), 14, 1),
        rsi_series(c), adx_series(h, lo, c), mid, up, dn, bbw, vz,
    )


def pct_rank(values: list[float | None], i: int, window: int) -> float | None:
    """Share of the last `window` values below values[i]."""
    x = values[i]
    past = [v for v in values[max(0, i - window) : i] if v is not None]
    if x is None or len(past) < window // 2:
        return None
    return sum(v < x for v in past) / len(past)


# -- the decision -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Params:
    swing_bars: int = 20  # 1h bars: the range a swing breakout must clear
    swing_stop_atr: float = 1.5
    swing_target_atr: float = 3.0
    swing_oi_min: float = 0.005  # open interest up at least 0.5% over the breakout hour
    squeeze_rank: float = 0.25  # 15m Bollinger width below this share of the last 400 bars
    scalp_bars: int = 24  # 15m bars: the range a scalp breakout must clear
    scalp_vol_z: float = 1.25
    scalp_rr: float = 3.0
    scalp_oi_min: float = 0.0
    min_stop_pct: float = 0.20
    max_stop_pct: float = 4.0
    min_net_rr: float = 1.4  # reward/risk after costs
    wall_buffer_atr: float = 0.25  # stop short of an options wall by this much 15m ATR
    trend_adx: float = 20.0  # for the regime label only
    range_adx: float = 18.0


@dataclass
class Market:
    """Everything decide() reads at one closed 15m bar."""

    m15: Frame
    i: int  # index of the bar that just closed
    h1: Frame
    j: int  # last 1h bar closed at or before it
    d1: Frame
    k: int  # last daily bar closed at or before it
    oi: list[float] = field(default_factory=list)  # 15m open interest, oldest first, ending at bar i (may be empty)
    funding: float | None = None  # latest funding rate, % per 8h
    options: dict[str, Any] | None = None  # options.summarize() for the underlying, live only

    @property
    def hour_close(self) -> bool:
        """The 15m bar that just closed also closed an hour, and that hour is h1[j]."""
        return (self.m15.t[self.i] + 900) % 3600 == 0 and self.h1.t[self.j] + 3600 == self.m15.t[self.i] + 900


@dataclass(frozen=True)
class Plan:
    side: str
    mode: str  # "swing" or "scalp"
    entry: float
    stop: float
    target: float
    reasons: tuple[str, ...]

    @property
    def stop_pct(self) -> float:
        return abs(self.entry - self.stop) / self.entry * 100

    @property
    def target_pct(self) -> float:
        return abs(self.target - self.entry) / self.entry * 100

    @property
    def net_rr(self) -> float:
        cost = 2 * COST_PCT
        return (self.target_pct - cost) / (self.stop_pct + cost)


def bias(f: Frame, i: int) -> int:
    """+1 above a rising EMA stack, -1 below a falling one, 0 otherwise."""
    c, e21, e50 = f.c[i], f.ema21[i], f.ema50[i]
    if e21 is None or e50 is None:
        return 0
    if c > e21 > e50:
        return 1
    if c < e21 < e50:
        return -1
    return 0


def oi_change(oi: list[float], n: int) -> float | None:
    if len(oi) <= n or not oi[-1 - n] or not oi[-1]:
        return None
    return oi[-1] / oi[-1 - n] - 1


def _arrow(d: int) -> str:
    return {1: "up", -1: "down"}.get(d, "flat")


def regime(m: Market, p: Params) -> str:
    """A label for the log: what kind of market this is."""
    sq = pct_rank(m.m15.bbw, m.i - 1, 400)
    adx_1h = m.h1.adx[m.j]
    if sq is not None and sq < p.squeeze_rank:
        return "squeeze"
    if adx_1h is not None and bias(m.h1, m.j) and adx_1h >= p.trend_adx:
        return f"{_arrow(bias(m.h1, m.j))}trend"
    if adx_1h is not None and adx_1h < p.range_adx:
        return "range"
    return "mixed"


def swing(m: Market, p: Params) -> Plan | None:
    """A 1h close beyond the last swing_bars hours, with the daily bias and new open interest behind it."""
    f, j, n = m.h1, m.j, p.swing_bars
    if not m.hour_close or j < n or f.atr[j] is None:
        return None
    c, a = f.c[j], f.atr[j]
    hi, lo = max(f.h[j - n : j]), min(f.lo[j - n : j])
    daily = bias(m.d1, m.k)
    if c > hi and daily >= 0:
        d, edge = 1, hi
    elif c < lo and daily <= 0:
        d, edge = -1, lo
    else:
        return None
    oi1h = oi_change(m.oi, 4)
    if oi1h is None or oi1h < p.swing_oi_min:
        return None
    return Plan("buy" if d == 1 else "sell", "swing", c, c - d * p.swing_stop_atr * a, c + d * p.swing_target_atr * a, (
        f"1h closed {c:.2f} {'above' if d == 1 else 'below'} the {n}h {'high' if d == 1 else 'low'} {edge:.2f}",
        f"daily bias {_arrow(daily)}, open interest {100 * oi1h:+.2f}% in the hour",
        f"stop {p.swing_stop_atr:g} x 1h ATR {a:.2f}, target {p.swing_target_atr:g} x ATR",
    ))


def scalp(m: Market, p: Params) -> Plan | None:
    """A 15m squeeze that breaks out on volume, with new open interest and no higher timeframe against it."""
    f, i, n = m.m15, m.i, p.scalp_bars
    if i < n + 1:
        return None
    sq = pct_rank(f.bbw, i - 1, 400)  # the bar before: a breakout bar itself widens the bands
    a, mid, vz = f.atr[i], f.bb_mid[i], f.vol_z[i]
    if sq is None or sq >= p.squeeze_rank or None in (a, mid, vz) or vz < p.scalp_vol_z:
        return None
    c = f.c[i]
    hi, lo = max(f.h[i - n : i]), min(f.lo[i - n : i])
    trend, daily = bias(m.h1, m.j), bias(m.d1, m.k)
    if c > hi and trend >= 0 and daily >= 0:
        d, edge = 1, hi
    elif c < lo and trend <= 0 and daily <= 0:
        d, edge = -1, lo
    else:
        return None
    oi1h = oi_change(m.oi, 4)
    if oi1h is None or oi1h <= p.scalp_oi_min:
        return None
    risk = min(max(abs(c - mid), 0.8 * a), 2.5 * a)
    return Plan("buy" if d == 1 else "sell", "scalp", c, c - d * risk, c + d * p.scalp_rr * risk, (
        f"15m squeeze (Bollinger width in its bottom {100 * sq:.0f}%) broke {'above' if d == 1 else 'below'} the {n * 15 // 60}h {'high' if d == 1 else 'low'} {edge:.2f} at {c:.2f}",
        f"volume {vz:+.1f} sigma, open interest {100 * oi1h:+.2f}% in the hour, 1h {_arrow(trend)}, daily {_arrow(daily)}",
        f"stop at the Bollinger middle {mid:.2f} (0.8-2.5 x ATR), target {p.scalp_rr:g}R",
    ))


def read_options(plan: Plan, opts: dict[str, Any] | None, atr: float, p: Params) -> tuple[Plan | None, str]:
    """What the options book says, and the plan with its target capped short of a wall in the way."""
    if not opts:
        return plan, ""
    day = opts.get("day") or {}
    bits = []
    if day.get("atm_iv"):
        bits.append(f"ATM IV {100 * day['atm_iv']:.0f}%")
    if day.get("skew_25d") is not None:
        bits.append(f"25d skew {100 * day['skew_25d']:+.1f}")
    if opts.get("pcr_oi") is not None:
        bits.append(f"put/call {opts['pcr_oi']:.2f}")
    d = 1 if plan.side == "buy" else -1
    wall = day.get("call_wall") if d == 1 else day.get("put_wall")
    if not wall or (wall - plan.entry) * d <= 0 or (plan.target - wall) * d <= 0:
        return plan, ", ".join(bits)
    target = wall - d * p.wall_buffer_atr * atr
    bits.append(f"target capped short of the {'call' if d == 1 else 'put'} wall {wall:g}")
    if (target - plan.entry) * d <= 0:
        return None, ", ".join(bits)
    return Plan(plan.side, plan.mode, plan.entry, plan.stop, target, plan.reasons), ", ".join(bits)


def decide(m: Market, p: Params = Params()) -> tuple[Plan | None, str]:
    """The trade to take on this bar, or None, with a one-line reason either way."""
    if m.m15.atr[m.i] is None or m.h1.atr[m.j] is None:
        return None, "warming up"
    label = regime(m, p)
    plan = swing(m, p) or scalp(m, p)
    if plan is None:
        return None, f"{label}: no setup"
    plan, opts = read_options(plan, m.options, m.m15.atr[m.i] or 0.0, p)
    if plan is None:
        return None, f"{label}: an options wall leaves no room to the target ({opts})"
    if not p.min_stop_pct <= plan.stop_pct <= p.max_stop_pct:
        return None, f"{plan.mode}: stop {plan.stop_pct:.2f}% outside {p.min_stop_pct}-{p.max_stop_pct}%"
    if plan.net_rr < p.min_net_rr:
        return None, f"{plan.mode}: reward/risk after costs {plan.net_rr:.2f} < {p.min_net_rr}"
    tail = [f"SL {plan.stop_pct:.2f}%, TP {plan.target_pct:.2f}%, R {plan.net_rr:.2f} after costs"]
    if m.funding is not None:
        tail.append(f"funding {m.funding:.4f}%")
    if opts:
        tail.append(opts)
    return plan, f"{plan.mode.upper()} [{label}] " + "; ".join([*plan.reasons, *tail])


# -- live --------------------------------------------------------------------------------------------


def last_closed(f: Frame, step: int, at: int) -> int | None:
    """Index of the last bar of `f` (bars of `step` seconds) closed by time `at`."""
    idx = bisect.bisect_right(f.t, at - step) - 1
    return idx if idx >= 0 else None


def with_hour(h1: list[Candle], m15: list[Candle], close_at: int) -> list[Candle]:
    """At an hour's close, the hour that just closed, built from its four 15m bars when REST has not
    published it yet: a swing is decided on that bar and must not wait an hour for it."""
    start = close_at - 3600
    if close_at % 3600 or (h1 and h1[-1].time >= start):
        return h1
    quarter = [c for c in m15 if start <= c.time < close_at]
    if len(quarter) != 4:
        return h1
    return [*h1, Candle(start, quarter[0].open, max(c.high for c in quarter), min(c.low for c in quarter), quarter[-1].close, sum(c.volume for c in quarter))]


class TbMaster(Strategy):
    name = "tb_master_15m"
    interval = "15m"
    symbols = ("BTCUSD", "ETHUSD")
    lookback = 500  # 15m bars: ATR and Bollinger-width ranks look back 400
    is_default_sl_tp = False  # every plan carries its own stop and target

    h1_bars: ClassVar[int] = 300
    d1_bars: ClassVar[int] = 120
    params: ClassVar[Params] = Params()

    async def on_candle(self, ctx: Context) -> Signal | None:
        market, sym = ctx.market, ctx.symbol
        close_at = ctx.bar_time + RESOLUTION_SECONDS["15m"]

        async def optional(coro: Any) -> Any:
            try:
                return await coro
            except Exception:
                return None  # no OI means no trade (decide() needs it); funding and options only add to the reason

        h1, d1, oi, funding, opts = await asyncio.gather(
            market.candles(sym, "1h", self.h1_bars),
            market.candles(sym, "1d", self.d1_bars),
            optional(market.candles(f"OI:{sym}", "15m", 40)),
            optional(market.candles(f"FUNDING:{sym}", "15m", 8)),
            optional(market.option_summary(sym.removesuffix("USD"))) if hasattr(market, "option_summary") else asyncio.sleep(0),
        )
        h1 = with_hour(list(h1), ctx.candles, close_at)
        f15, fh, fd = frame(ctx.candles), frame(h1), frame(d1)
        j, k = last_closed(fh, 3600, close_at), last_closed(fd, 86400, close_at)
        if j is None or k is None:
            return None
        oi_vals = [c.close for c in (oi or []) if c.time <= ctx.bar_time]
        fund = [c.close for c in (funding or []) if c.time <= ctx.bar_time]
        plan, why = decide(Market(f15, len(f15) - 1, fh, j, fd, k, oi_vals, fund[-1] if fund else None, opts), self.params)
        if plan is None:
            return None
        return Signal(plan.side, why, stop_loss_pct=round(plan.stop_pct, 3), take_profit_pct=round(plan.target_pct, 3))
