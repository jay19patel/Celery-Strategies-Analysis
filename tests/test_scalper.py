"""The scalper: where it puts its stop, what it aims at, and why it mostly refuses.

Two things here are worth more than the rest. The stop is a statement about the market — it sits
behind the swing the move came from, so "price traded there" and "the reason for the trade is
gone" are the same event; a percentage cannot say that. And the size: a tight stop makes the risk
rule ask for a large position, which is correct and is also the shape of the mistake that empties
an account, so the tests that matter most are the ones proving the risk rule still binds.
"""

from __future__ import annotations

import pytest

from tradebuddy.delta import Candle
from tradebuddy.strategies.base import net_rr
from tradebuddy.strategies.scalper import (
    MIN_NET_RR,
    MIN_STOP_PCT,
    MIN_VOLUME_RATIO,
    PUSH,
    Scalper,
    decide,
    swing,
    target,
)

MIDNIGHT = 1_700_006_400


def bars(n: int = 40, *, close_at: float = 1.0, volume: float = 10.0,
         last_volume: float | None = None, span: float = 0.15) -> list[Candle]:
    """A flat series with a controllable last bar. `close_at` is where it closes inside its own
    range: 1.0 on the high, 0.0 on the low.

    `span` is half a bar's range in price, so the default is a 0.3% candle on a price of 100 —
    about what a five-minute bar actually is. The first version of this fixture used 1.0, a 2%
    five-minute candle, and every trade was refused for having a stop too wide to be a scalp."""
    out = [Candle(MIDNIGHT + i * 300, 100, 100 + span, 100 - span, 100, volume) for i in range(n - 1)]
    low, high = 100 - span, 100 + span
    out.append(Candle(MIDNIGHT + (n - 1) * 300, 100, high, low, low + close_at * (high - low),
                      volume if last_volume is None else last_volume))
    return out


def view(short: int = 1, medium: int = 1) -> dict:
    return {"summary": [{"interval": "15m", "direction": short}, {"interval": "1h", "direction": medium}]}


def structure(above: float | None = 101.0, below: float | None = 99.0, cvd: float | None = None) -> dict:
    """A market-structure read with one level each way, as `nearby` emits them."""
    out: dict = {"nearby": {
        "above": [{"price": above, "label": "3x highs", "kind_label": "resting stops", "confluence": 2}] if above else [],
        "below": [{"price": below, "label": "2x lows", "kind_label": "resting stops", "confluence": 1}] if below else [],
    }}
    if cvd is not None:
        out["flow"] = {"cvd_move_pct": cvd}
    return out


def taken(**kw):
    """The bar that passes every gate: hard close up its range, heavy volume."""
    return bars(close_at=1.0, last_volume=30, **kw)


# =====================================================================================
# the stop, which is the point of the rewrite
# =====================================================================================


def test_the_stop_sits_behind_the_swing_the_move_came_from():
    """Not a percentage. A percentage is a number about the account; the stop is a statement about
    the market, and the two only coincide by accident."""
    candles = taken()
    low = swing(candles, 1)
    assert low == min(c.low for c in candles[-10:])
    out = decide(candles, view(), structure())
    assert out.side == "buy"
    # The stop is below that swing, never above it.
    stop_price = candles[-1].close * (1 - out.stop_loss_pct / 100)
    assert stop_price < low


def test_the_swing_is_mirrored_for_a_short():
    candles = bars(close_at=0.0, last_volume=30)
    assert swing(candles, -1) == max(c.high for c in candles[-10:])


def test_a_wider_swing_buys_a_wider_stop():
    """The market decides the distance, so a violent window gives a wider stop and — through the
    risk rule — a smaller position."""
    tight = decide(taken(span=0.08), view(), structure(above=101.0))
    wide = decide(taken(span=0.30), view(), structure(above=102.0))
    assert tight.side == wide.side == "buy"
    assert tight.stop_loss_pct < wide.stop_loss_pct


def test_a_stop_inside_the_costs_is_refused():
    out = decide(taken(span=0.004), view(), structure())
    assert out.side is None
    assert "the spread and the fees need" in out.reason


def test_the_stop_floor_follows_the_fee_it_has_to_clear():
    """A stop inside three round trips is refused however small MIN_STOP_PCT is.

    The floor used to be a constant, so doubling the fee on the Settings page left this strategy
    taking the same trades at twice the cost."""
    bars, levels = taken(span=0.30), structure(above=102.0)
    cheap = decide(bars, view(), levels, cost_pct=0.0)
    dear = decide(bars, view(), levels, cost_pct=0.5)  # 3 x 0.5% = a 1.5% floor
    assert cheap.side is not None
    assert dear.side is None and "the spread and the fees need" in dear.reason


def test_a_stop_too_wide_to_be_a_scalp_is_refused():
    out = decide(taken(span=3.0), view(), structure(above=130))
    assert out.side is None
    assert "calls a scalp" in out.reason


def test_the_stop_never_goes_under_the_floor():
    out = decide(taken(), view(), structure())
    assert out.stop_loss_pct >= MIN_STOP_PCT


# =====================================================================================
# the target, and the ratio it has to clear
# =====================================================================================


def test_the_target_is_the_next_level_price_has_to_get_through():
    """From `nearby`, which has already merged pools, zones, value edges and previous-period
    levels and ranked them by distance."""
    at, what = target(structure(above=101.0), 1, 100.0)
    assert at == 101.0
    assert "3x highs" in what and "2 sources agree" in what


def test_a_level_behind_price_is_not_a_target():
    at, _ = target(structure(above=99.0), 1, 100.0)
    assert at is None


def test_a_trade_that_cannot_make_its_risk_back_is_refused():
    """Under 1:1 the fees and the spread eat it before the market has to be right."""
    # A level barely above price against a normal-width stop: under 1:1.
    out = decide(taken(), view(), structure(above=100.3))
    assert out.side is None
    assert "cannot pay for its costs" in out.reason or "inside the" in out.reason


def test_the_reward_quoted_is_the_one_actually_taken():
    out = decide(taken(), view(), structure(above=101.5))
    assert out.side == "buy"
    assert net_rr(out.take_profit_pct, out.stop_loss_pct, 0.0) >= MIN_NET_RR


def test_no_level_to_aim_at_is_a_refusal_not_a_guess():
    out = decide(taken(), view(), structure(above=None))
    assert out.side is None and "no level above" in out.reason


# =====================================================================================
# the gates
# =====================================================================================


def test_it_refuses_when_the_two_timeframes_above_disagree():
    out = decide(taken(), view(1, -1), structure())
    assert out.side is None and "do not agree" in out.reason


def test_it_refuses_a_bar_that_drifted_rather_than_pushed():
    out = decide(bars(close_at=0.5, last_volume=30), view(), structure())
    assert out.side is None and f"{PUSH:.0%}" in out.reason


def test_it_refuses_a_push_on_ordinary_volume():
    out = decide(bars(close_at=1.0, last_volume=10), view(), structure())
    assert out.side is None and f"{MIN_VOLUME_RATIO:.2f}x" in out.reason


def test_estimated_flow_can_refuse_a_trade_but_never_justify_one():
    """It is inferred from candles, not read off a tape. An estimate can honestly say "not this
    one"; it cannot carry a decision on its own."""
    against = decide(taken(), view(), structure(cvd=-5.0))
    assert against.side is None and "APPROX" in against.reason
    # And with the flow strongly in favour, every other gate still has to pass.
    assert decide(bars(close_at=0.5, last_volume=30), view(), structure(cvd=+5.0)).side is None


def test_a_missing_structure_read_refuses_rather_than_inventing_levels():
    """No `nearby` means no target. Falling back to a made-up percentage is how a strategy ends up
    aiming at a price nothing in the market cares about."""
    out = decide(taken(), view(), {})
    assert out.side is None and "no level" in out.reason


def test_every_refusal_says_why():
    for args in [(view(1, -1), structure()), (view(), structure(above=None)), (view(0, 0), structure())]:
        assert decide(taken(), *args).reason
    assert decide(bars(5), view(), structure()).reason


def test_it_is_pure():
    candles, v, s = taken(), view(), structure()
    assert decide(candles, v, s) == decide(candles, v, s)


def test_it_is_symmetrical():
    """Neither side may be easier to trigger than the other."""
    long = decide(taken(), view(1, 1), structure())
    short = decide(bars(close_at=0.0, last_volume=30), view(-1, -1), structure())
    assert long.side == "buy" and short.side == "sell"
    assert long.stop_loss_pct == pytest.approx(short.stop_loss_pct, rel=0.05)


# =====================================================================================
# the size, which is the part that can hurt
# =====================================================================================


def test_the_strategy_reads_the_engines_own_readings():
    assert Scalper.wants_outlook and Scalper.wants_structure
    assert Scalper.is_default_sl_tp is False
    assert Scalper.interval == "5m"
    # See `test_it_reads_the_engines_own_readings_and_ships_switched_on` in test_ema_vwap: the
    # race this used to dodge is gone, because the routing ranks instead of racing.
    assert Scalper.default_on is True


async def test_a_looser_cap_can_never_risk_more_than_the_risk_rule_allows(cfg, exchange):
    """THE test. `margin_pct` raises the CAP; sizing stays min(by_risk, by_margin), so a scalper
    with a huge cap still risks exactly `risk_per_trade_pct` behind its stop. If this ever
    inverts, a 0.15% stop with a 60% cap is an account-ending position."""
    from tests.test_pipeline import make

    system = await make(cfg, exchange)
    await system.update_settings({"risk_per_trade_pct": 1.0, "trade_margin_pct": 10.0})
    broker, price = system.paper, 100_000.0
    account = await broker.account()

    tight = await system.trader.size_for_risk(broker, "BTCUSD", price, 0.15, account, system.settings)
    loose = await system.trader.size_for_risk(broker, "BTCUSD", price, 0.15, account, system.settings, margin_pct=60.0)
    by_risk = await broker.size_for_notional(
        "BTCUSD", price, account.equity * system.settings.risk_per_trade_pct / 100.0 / (0.15 / 100.0))

    assert loose >= tight          # the cap was what was binding
    assert loose <= by_risk        # and the risk rule still is
    absurd = await system.trader.size_for_risk(broker, "BTCUSD", price, 0.15, account, system.settings, margin_pct=1000.0)
    assert absurd == by_risk       # a cap of 1000% still cannot beat the risk rule


async def test_only_a_strategy_that_asked_for_a_cap_gets_one(cfg, exchange):
    """Built from the real registry, so a strategy that quietly grows a `margin_pct` shows up."""
    from tradebuddy.strategies import discover
    from tradebuddy.system import System

    from .conftest import IdleStream

    system = System(cfg, strategies=discover(), stream_factory=IdleStream, client_factory=exchange.client)
    assert system.trader.margins == {"scalper": Scalper.margin_pct}
    assert system.trader.margin_for("tb_master_15m") is None
