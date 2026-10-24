
from tradebuddy.delta import Candle
from tradebuddy.settings import RANGES, Settings
from tradebuddy.strategies import Context, discover
from tradebuddy.strategies.indicators import atr, ema, rsi, supertrend
from tradebuddy.strategies.levels import from_stop

from .conftest import FakeExchange


def ctx(candles, market=None, symbol="BTCUSD"):
    return Context(symbol, "15m", candles[-1].time if candles else 0, candles, market or FakeExchange().client(""))


def test_discover_finds_every_strategy_once():
    names = [s.name for s in discover()]
    assert names == sorted(names) == [
        "ema_vwap_5m", "scalper", "tb_master_15m", "trade_buddy_manager_15m",
    ]


def test_ema_matches_hand_calculation():
    assert ema([1, 2, 3, 4], 3) == [2.0, 3.0]
    assert ema([1, 2], 3) == []


def test_rsi_extremes():
    assert rsi(list(range(20)), 14)[-1] == 100.0
    assert rsi(list(range(20, 0, -1)), 14)[-1] == 0.0


def candle(i, o, h, lo, c):
    return Candle(1_700_000_000 + i * 900, o, h, lo, c)


def flat(closes, start=0, wick=0.2):
    return [candle(start + i, x, x + wick, x - wick, x) for i, x in enumerate(closes)]


def test_atr_of_constant_ranges():
    assert atr([2, 2, 2, 2], [0, 0, 0, 0], [1, 1, 1, 1], period=2) == [2.0, 2.0]
    assert atr([1], [0], [1], period=2) == []


def test_supertrend_follows_a_trend_and_flips_with_it():
    up = flat([100 + i for i in range(40)])
    line = supertrend([c.high for c in up], [c.low for c in up], [c.close for c in up], period=10, multiplier=3.0)
    level, direction = line[-1]
    assert direction == 1 and level < up[-1].close  # a long stop sits below price

    down = flat([200 - i for i in range(40)])
    level, direction = supertrend([c.high for c in down], [c.low for c in down], [c.close for c in down], 10, 3.0)[-1]
    assert direction == -1 and level > down[-1].close


def test_supertrend_needs_more_bars_than_its_period():
    assert supertrend([2, 2], [1, 1], [1.5, 1.5], period=10) == []


def test_from_stop_refuses_a_level_on_the_wrong_side():
    assert from_stop("buy", 100.0, 101.0) is None  # a long stop above the price
    assert from_stop("sell", 100.0, 99.0) is None
    assert from_stop("buy", 100.0, 0.0) is None


def test_from_stop_keeps_the_stop_outside_the_noise_and_inside_the_ranges():
    assert from_stop("buy", 100.0, 98.0, reward=2.0) == (2.0, 4.0)
    assert from_stop("buy", 100.0, 99.99, reward=2.0, floor_pct=0.8) == (0.8, 1.6)  # the floor wins
    sl, tp = from_stop("sell", 100.0, 400.0, reward=3.0)  # a level far past what Settings allows
    assert sl == RANGES["stop_loss_pct"][1] and tp == RANGES["take_profit_pct"][1]


def test_every_strategy_arrives_switched_on():
    """The 5-minute strategies used to ship off, because one position per symbol plus first-come
    routing meant the fastest strategy took the slot whatever the trade looked like — a 5m
    strategy gets three times the chances of a 15m one.

    That was a routing defect, not a verdict on the strategies, and it is fixed where it was:
    `Trader._settle` ranks the competing signals on reward-to-risk after costs. Being fast no
    longer wins, so there is nothing left for `default_on = False` to protect."""
    assert [s.name for s in discover() if not s.default_on] == []


def test_being_fast_does_not_win_the_slot():
    """The guarantee the old default protected, asserted where it now lives."""
    assert Settings().signal_settle_seconds > 0, "signals are routed first-come again"


async def test_the_system_builds_the_way_the_containers_build_it(cfg, exchange):
    """`roles.engine` constructs `System(cfg, ...)` with no `strategies`, so the list comes from
    `discover()` inside __init__.

    Every other test passes one explicitly. That is why reading the raw parameter instead of
    `self.strategies` — None in every deployed process — passed the whole suite and then crashed
    the engine on boot, in a loop, with the web container serving 503s.
    """
    from tradebuddy.system import System

    from .conftest import IdleStream

    system = System(cfg, strategies=None, stream_factory=IdleStream, client_factory=exchange.client)
    assert {s.name for s in system.strategies} == {s.name for s in discover()}
    assert system.trader.margins == {"scalper": 60.0}


async def test_the_page_shows_a_strategy_the_way_the_runner_treats_it(cfg, exchange):
    """A toggle row that has never been written means "nobody has been asked", and `enabled()`
    answers with whatever default it is handed. The runner hands it `default_on`; this view used
    to hand it nothing, so the scalper appeared switched ON on the Strategies page while the
    engine was quietly not running it."""
    from tradebuddy.system import System

    from .conftest import IdleStream

    system = System(cfg, strategies=discover(), stream_factory=IdleStream, client_factory=exchange.client)
    shown = {s["name"]: s["enabled"] for s in system.strategies_view()}
    for s in discover():
        runs = system.runner.is_on(s, s.symbols[0])
        assert shown[s.name] == runs, f"{s.name}: page says {shown[s.name]}, runner says {runs}"
