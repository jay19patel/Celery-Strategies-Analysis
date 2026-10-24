"""TB Manager: the strategy that trades what the Market page shows — and the demo market.

Most of these are about refusals. A breakout strategy that takes every break is a losing one, so
the gates are the strategy, and each gate has to be shown to actually shut.
"""

from __future__ import annotations

import pytest

from tradebuddy.delta import Candle
from tradebuddy.demo import Refused, seed
from tradebuddy.store import Store
from tradebuddy.strategies import discover
from tradebuddy.strategies.trade_buddy_manager import TradeBuddyManager, decide

from .test_outlook import walk

STEP = 900
BASE = 100.0


def flat_then_break(up: bool = True, size: float = 3.0, volume: float = 40.0, n: int = 60, quiet: float = 0.4) -> list[Candle]:
    """A quiet stretch, then one bar that closes clean through it on a big candle and heavy volume.

    `quiet` is the half-range of the stretch, and so both the level that has to be broken and what
    counts as an ordinary bar — raising it is how a break that clears the level on a *small* candle
    is built."""
    bars = [Candle(1_700_000_000 + i * STEP, BASE, BASE + quiet, BASE - quiet, BASE, 10.0) for i in range(n)]
    t = bars[-1].time + STEP
    close = BASE + size if up else BASE - size
    bars.append(Candle(t, BASE, max(BASE, close) + 0.3, min(BASE, close) - 0.3, close, volume))
    return bars


def view(trend_1h: int = 1, trend_1d: int = 1, conditional: float | None = 0.60, baseline: float = 0.50) -> dict:
    """The engine's reading, as the strategy receives it in ctx.outlook."""
    return {
        "ready": True,
        "alignment": {"verdict": "aligned up", "score": 3, "advice": ""},
        "timeframes": [
            {"interval": "15m", "direction": 1, "trend": "up"},
            {"interval": "1h", "direction": trend_1h, "trend": "up" if trend_1h > 0 else "down" if trend_1h < 0 else "flat"},
            {"interval": "1d", "direction": trend_1d, "trend": "up" if trend_1d > 0 else "down" if trend_1d < 0 else "flat"},
        ],
        "direction": [{
            "horizon_hours": 1.0, "conditional": conditional, "baseline": baseline,
            "conditional_sample": 4000, "sample": 20000,
        }],
    }


# -- the trigger ---------------------------------------------------------------------


def test_it_trades_a_break_that_clears_every_gate():
    d = decide(flat_then_break(up=True), view())
    assert d.side == "buy"
    assert d.stop_loss_pct and d.take_profit_pct and d.take_profit_pct > d.stop_loss_pct
    assert "BOS up" in d.reason and "followed through" in d.reason and "stop under the break" in d.reason


def test_it_is_symmetric():
    """Mirrored rules, so neither side is accidentally easier to trade than the other."""
    long = decide(flat_then_break(up=True), view())
    short = decide(flat_then_break(up=False), view(trend_1h=-1, trend_1d=-1, conditional=0.40))
    assert (long.side, short.side) == ("buy", "sell")
    assert long.stop_loss_pct == pytest.approx(short.stop_loss_pct, rel=0.1)


def test_no_break_no_trade():
    quiet = [Candle(1_700_000_000 + i * STEP, BASE, BASE + 0.4, BASE - 0.4, BASE, 10.0) for i in range(80)]
    assert decide(quiet, view()).reason == "no break of structure on this bar"


def test_only_the_bar_that_broke_counts():
    """The level stays broken for hours. Entering on the third bar after the break is entering late
    into a move the strategy already missed."""
    bars = flat_then_break(up=True)
    later = [*bars, Candle(bars[-1].time + STEP, 103, 103.6, 102.6, 103.2, 12.0)]
    assert decide(bars, view()).side == "buy"
    assert decide(later, view()).side == ""


# -- the gates -----------------------------------------------------------------------


def test_a_break_on_an_ordinary_bar_is_refused():
    # Clears the level, but on a candle no bigger than the ones around it: brushed, not taken.
    d = decide(flat_then_break(quiet=1.5, size=2.0, volume=40.0), view())
    assert d.side == "" and "ordinary bar" in d.reason


def test_a_break_on_thin_volume_is_refused():
    d = decide(flat_then_break(size=3.0, volume=9.0), view())
    assert d.side == "" and "thin volume" in d.reason


def test_a_break_against_a_higher_timeframe_is_a_bounce():
    d = decide(flat_then_break(up=True), view(trend_1d=-1))
    assert d.side == "" and "bounce, not a trend" in d.reason and "1d down" in d.reason
    # The 15m frame itself never vetoes: it is the one that just broke.
    assert decide(flat_then_break(up=True), view(trend_1h=0, trend_1d=0)).side == "buy"


def test_without_counted_history_it_refuses_rather_than_guesses():
    """Fail closed. A strategy that trades on a hunch because the evidence has not arrived is the
    thing this system was built not to be — and the refusal says how to fix it."""
    d = decide(flat_then_break(), view(conditional=None))
    assert d.side == "" and "backfill" in d.reason
    allowed = decide(flat_then_break(), view(conditional=None), require_counted_edge=False)
    assert allowed.side == "buy" and "not counted yet" in allowed.reason


def test_the_breaks_own_record_is_preferred_to_the_markets():
    """The structure-bias rate describes the market the break happened in; the pattern record
    describes the break. When both are there, the one that is about this trade wins."""
    patterns = {"patterns": {"15m": [
        {"kind": "bos", "direction": "up", "count": 300, "followed_through": 0.58, "baseline": 0.50, "edge": 0.08, "enough": True},
    ]}}
    d = decide(flat_then_break(up=True), view(conditional=0.50, baseline=0.52) | patterns)
    assert d.side == "buy" and "bos up on 15m has followed through 58%" in d.reason


def test_too_few_of_a_break_to_count_falls_back_to_the_market():
    thin = {"patterns": {"15m": [
        {"kind": "bos", "direction": "up", "count": 4, "followed_through": None, "baseline": 0.50, "edge": None, "enough": False},
    ]}}
    d = decide(flat_then_break(up=True), view() | thin)
    assert d.side == "buy" and "this structure has followed through" in d.reason


def test_a_setup_the_history_does_not_favour_is_refused():
    d = decide(flat_then_break(up=True), view(conditional=0.50, baseline=0.52))
    assert d.side == "" and "under the 1 it needs" in d.reason


def test_the_edge_is_read_in_the_direction_of_the_trade():
    """"Higher 40% of the time" is a ten-point edge to a short, not a sixty-point deficit."""
    down = {"trend_1h": -1, "trend_1d": -1}
    assert decide(flat_then_break(up=False), view(**down, conditional=0.40, baseline=0.50)).side == "sell"
    assert decide(flat_then_break(up=False), view(**down, conditional=0.60, baseline=0.50)).side == ""


def test_without_a_stored_view_it_does_not_trade():
    d = decide(flat_then_break(), {"ready": False})
    assert d.side == "" and "no stored history" in d.reason


def test_a_stop_too_wide_to_size_is_refused():
    d = decide(flat_then_break(size=3.0), view(), max_stop_atr=0.2)
    assert d.side == "" and "too wide to size" in d.reason


# -- the levels ----------------------------------------------------------------------


def test_the_stop_goes_under_the_bar_that_broke_out():
    bars = flat_then_break(up=True)
    d = decide(bars, view())
    last = bars[-1]
    stop_price = last.close * (1 - d.stop_loss_pct / 100)
    assert stop_price < last.low  # back through the break bar and the idea is gone
    assert stop_price > last.low * 0.97  # but not a mile below it


def test_an_options_wall_caps_the_target():
    bars = flat_then_break(up=True)
    free = decide(bars, view())
    price = bars[-1].close
    # Three-quarters of the way there: close enough to pull the target in, far enough that what is
    # left still clears the stop. A wall at half the distance leaves reward 1:1, and the strategy
    # refuses that outright — which is its own test below.
    wall = price * (1 + 0.75 * free.take_profit_pct / 100)
    capped = decide(bars, view(), {"day": {"call_wall": wall, "put_wall": price * 0.9}})
    assert capped.take_profit_pct < free.take_profit_pct
    assert "capped at the call wall" in capped.reason
    # A wall beyond the target is not in the way, and changes nothing.
    far = decide(bars, view(), {"day": {"call_wall": price * 2, "put_wall": price * 0.5}})
    assert far.take_profit_pct == pytest.approx(free.take_profit_pct)


def test_a_short_is_capped_by_the_put_wall_not_the_call_wall():
    bars = flat_then_break(up=False)
    price = bars[-1].close
    down = view(trend_1h=-1, trend_1d=-1, conditional=0.40)
    free = decide(bars, down)
    wall = price * (1 - 0.75 * free.take_profit_pct / 100)
    d = decide(bars, down, {"day": {"call_wall": price * 1.5, "put_wall": wall}})
    assert d.side == "sell" and "capped at the put wall" in d.reason
    assert d.take_profit_pct < free.take_profit_pct


def test_a_wall_that_leaves_no_room_kills_the_trade_rather_than_shrinking_it():
    """A target pulled inside its own stop is a trade that risks more than it can make. There is no
    version of that worth taking, so it is refused rather than quietly re-sized."""
    bars = flat_then_break(up=True)
    price = bars[-1].close
    free = decide(bars, view())
    just_above = price * (1 + 0.3 * free.take_profit_pct / 100)
    d = decide(bars, view(), {"day": {"call_wall": just_above, "put_wall": price * 0.9}})
    assert d.side == "" and "inside the stop" in d.reason


def test_it_warms_up_quietly():
    assert "warming up" in decide(flat_then_break(n=5), view()).reason


# -- how it is wired -----------------------------------------------------------------


def test_the_strategy_is_discovered_and_declares_what_it_needs():
    [s] = [s for s in discover() if s.name == "trade_buddy_manager_15m"]
    assert isinstance(s, TradeBuddyManager)
    assert s.wants_outlook is True  # or ctx.outlook arrives empty and it never trades
    assert s.is_default_sl_tp is False  # it sets its own levels
    assert s.lookback >= 100 and s.interval == "15m"


async def test_it_keeps_no_state_between_bars():
    """It runs on whichever Celery worker is free, so two instances must answer identically."""
    from tradebuddy.strategies.base import Context

    bars = flat_then_break(up=True)

    class NoOptions:
        def price(self, symbol): return None
        async def candles(self, *a): return []
        async def option_chain(self, u): return []
        async def option_summary(self, u): return None
        async def option_history(self, u, h): return []

    ctx = Context("BTCUSD", "15m", bars[-1].time, bars, NoOptions(), view())
    first = await TradeBuddyManager().on_candle(ctx)
    second = await TradeBuddyManager().on_candle(ctx)
    assert first is not None
    assert (first.side, first.stop_loss_pct, first.take_profit_pct) == (second.side, second.stop_loss_pct, second.take_profit_pct)


async def test_the_engine_hands_the_strategy_the_same_view_the_page_shows(cfg, exchange):
    """Two readings of "which way is this market going" is how a system stops being trusted."""
    from tradebuddy.api import Api
    from tradebuddy.runner import make_job

    from .test_pipeline import make

    system = await make(cfg, exchange)
    system.store.record_bars("BTCUSD", "15m", walk(3_000, seed=4))
    page = await Api(system).outlook("BTCUSD")
    engine = await system.outlook("BTCUSD")
    assert page is engine  # literally the same object, not two builds that agree today

    from tradebuddy.events import CandleClosed

    event = CandleClosed(symbol="BTCUSD", resolution="15m", bar_time=1_700_000_000, source="clock")
    job = make_job(TradeBuddyManager(), event, {"BTCUSD": 100.0}, "live", None, engine)
    assert job["outlook"]["bar_time"] == engine["bar_time"]
    # A strategy that did not ask for it does not carry it: the payload goes over ZeroMQ every bar.
    plain = make_job(next(s for s in discover() if not s.wants_outlook), event, {}, "live", None, engine)
    assert "outlook" not in plain


# -- the demo market -----------------------------------------------------------------


def test_the_demo_market_fills_every_table(tmp_path):
    out = seed(str(tmp_path / "demo.db"), days=12)
    w = out["written"]
    assert w["bars"] == 12 * 96 * 2  # twelve days of 15-minute bars, two symbols
    assert w["verdicts"] > 0 and w["verdicts marked"] > 0

    store = Store(str(tmp_path / "demo.db"))
    assert store.last_bar_time("BTCUSD", "15m") > 0
    assert store.bar_gaps("BTCUSD", "15m") == []  # a demo market with holes in it teaches the wrong lesson
    assert store.verdict_record("BTCUSD")
    assert store.load_settings().get("demo_data") == "1"  # every page says so while this is set


def test_the_demo_options_history_is_dense_only_where_it_is_read(tmp_path):
    """A row a minute for three months is two million rows nobody opens. The 24-hour IV and
    put/call charts need minutes; everything older is read a bar at a time, so that is what it
    keeps — which is also what the real store keeps."""
    seed(str(tmp_path / "d.db"), days=12)
    store = Store(str(tmp_path / "d.db"))
    rows = store.options_history("BTC", 0, step=1)
    newest = rows[-1]["ts"]
    recent = [r for r in rows if r["ts"] > newest - 2 * 86_400]
    older = [r for r in rows if r["ts"] <= newest - 3 * 86_400]
    assert len(recent) > 2_000  # a minute apart over the last two days
    assert len(older) < len(recent)  # a bar apart before that
    assert all(r["ts"] % 900 == 0 for r in older)  # and on the boundary, so a strategy can read them back


def test_the_demo_market_is_the_same_market_twice(tmp_path):
    """Fixed seeds: looking at it again shows what you were looking at before."""
    a = seed(str(tmp_path / "a.db"), days=6)["written"]
    b = seed(str(tmp_path / "b.db"), days=6)["written"]
    assert a == b
    rows_a = Store(str(tmp_path / "a.db")).bars("BTCUSD", "15m")
    rows_b = Store(str(tmp_path / "b.db")).bars("BTCUSD", "15m")
    assert [r["close"] for r in rows_a] == [r["close"] for r in rows_b]


def test_the_demo_refuses_a_database_that_has_traded(tmp_path):
    """Fake market data under real decisions is the one way this command could do harm."""
    path = str(tmp_path / "real.db")
    store = Store(path)
    store.db.execute(
        "INSERT INTO orders (client_order_id, ts, broker, strategy, symbol, side, size, price, status)"
        " VALUES ('c1', 1, 'paper', 's', 'BTCUSD', 'buy', 1, 100, 'filled')",
    )
    store.db.commit()
    store.db.close()

    with pytest.raises(Refused, match="real database"):
        seed(path, days=2)
    assert seed(path, days=2, force=True)["written"]["bars"] > 0  # said out loud, it will still do it
