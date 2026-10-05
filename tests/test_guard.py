"""Position guard: auto trailing, per-position controls, the daily loss limit; multi-broker tickets; 24h stats."""

from __future__ import annotations

import pytest

from tradebuddy.api import Api, ApiError
from tradebuddy.brokers import Position
from tradebuddy.events import Tick
from tradebuddy.guard import trading_day, trail_levels
from tradebuddy.settings import Settings, SettingsError, apply_changes
from tradebuddy.stream import parse_stats

from .test_pipeline import close_bar, events, make, skipped


def pos(side="long", entry=100.0, sl=99.0, tp=102.0) -> Position:
    return Position(broker="paper", symbol="BTCUSD", side=side, size=1, entry_price=entry, mark_price=entry,
                    unrealized_pnl=0, stop_loss=sl, take_profit=tp)


S = Settings()  # trigger 80%, extend 50%, lock 50%, 3 steps


# -- the trailing rule ------------------------------------------------------------------


def test_no_trail_before_the_trigger():
    assert trail_levels(pos(), 101.5, 2.0, S, 0.01) is None  # 75% of the way to 102


def test_long_trails_target_out_and_stop_up():
    # 85% of the way: target +50% of the first distance (2) -> 103; stop locks 50% of the 1.7 profit -> 100.85
    assert trail_levels(pos(), 101.7, 2.0, S, 0.01) == (100.85, 103.0)


def test_short_is_the_mirror():
    assert trail_levels(pos("short", sl=101.0, tp=98.0), 98.3, 2.0, S, 0.01) == (99.15, 97.0)


def test_stop_never_loosens():
    # the stop is already above where locking 50% would put it
    assert trail_levels(pos(sl=101.0), 101.7, 2.0, S, 0.01) == (101.0, 103.0)


def test_extension_uses_the_first_target_distance():
    # after one trail the target is 103 (3 away); the next step still extends by 50% of the first 2
    assert trail_levels(pos(sl=100.85, tp=103.0), 102.5, 2.0, S, 0.01) == (101.25, 104.0)


def test_nothing_to_trail_without_both_legs():
    assert trail_levels(pos(sl=None), 101.9, 2.0, S, 0.01) is None
    assert trail_levels(pos(tp=None), 101.9, 2.0, S, 0.01) is None


# -- the guard on a live paper position ------------------------------------------------------


@pytest.fixture
async def paper(cfg, exchange):
    exchange.spec = {"id": 27, "contract_value": 0.001, "tick_size": 0.01}
    s = await make(cfg, exchange)
    await s.update_settings({"paper_slippage_pct": 0})
    yield s
    await s.bus.stop()


@pytest.fixture
async def both(cfg, exchange):
    exchange.spec = {"id": 27, "contract_value": 0.001, "tick_size": 0.01}
    s = await make(cfg, exchange, ("paper", "delta"))
    await s.update_settings({"paper_slippage_pct": 0})
    yield s
    await s.bus.stop()


async def tick(system, price, symbol="BTCUSD"):
    system.bus.publish(Tick(symbol=symbol, price=price))
    await system.drain()


async def guard(system):
    await system.guard.check()
    await system.drain()


async def test_guard_trails_and_counts_steps(paper):
    await close_bar(paper)  # long at 100, SL 99, TP 102
    await guard(paper)  # picks the position up
    await tick(paper, 101.7)
    await guard(paper)
    [p] = await paper.paper.positions()
    assert (p.stop_loss, p.take_profit) == (100.85, 103.0)
    [e] = events(paper, "ProtectionTrailed")
    assert (e["step"], e["max_steps"], e["old_take_profit"]) == (1, 3, 102.0)
    assert paper.store.controls("paper")["BTCUSD"]["steps"] == 1


async def test_max_steps_stops_trailing(paper):
    await close_bar(paper)
    await guard(paper)
    await Api(paper).set_position_control("paper", "BTCUSD", max_steps=1)
    await tick(paper, 101.7)
    await guard(paper)
    await tick(paper, 102.8)  # 93% of the way to the new 103 target
    await guard(paper)
    [p] = await paper.paper.positions()
    assert p.take_profit == 103.0 and len(events(paper, "ProtectionTrailed")) == 1


async def test_trailing_can_be_switched_off_per_position(paper):
    await close_bar(paper)
    await guard(paper)
    await Api(paper).set_position_control("paper", "BTCUSD", trailing=False)
    await tick(paper, 101.7)
    await guard(paper)
    [p] = await paper.paper.positions()
    assert (p.stop_loss, p.take_profit) == (99.0, 102.0)
    assert (await Api(paper).positions("paper"))[0]["control"] == {"trailing": False, "max_steps": 3, "steps": 0}


async def test_default_off_in_settings_means_new_positions_do_not_trail(paper):
    await paper.update_settings({"trailing_enabled": False})
    await close_bar(paper)
    await guard(paper)
    await tick(paper, 101.7)
    await guard(paper)
    assert events(paper, "ProtectionTrailed") == []


async def test_control_for_an_unknown_position_is_404(paper):
    with pytest.raises(ApiError) as info:
        await Api(paper).set_position_control("paper", "ETHUSD", trailing=True)
    assert info.value.status == 404


async def test_closed_position_drops_its_control(paper):
    await close_bar(paper)
    await guard(paper)
    await paper.paper.close_position("BTCUSD")
    await guard(paper)
    assert paper.store.controls("paper") == {}


# -- daily loss limit -----------------------------------------------------------------------


async def test_daily_loss_closes_everything_and_blocks_entries(paper):
    await paper.update_settings({"daily_loss_limit_pct": 2})
    await guard(paper)  # the day starts at equity 1000
    await close_bar(paper)
    paper.store.db.execute("UPDATE paper_account SET balance = balance - 30")  # a $30 loss: 3%
    await guard(paper)
    assert await paper.paper.positions() == []
    [halt] = events(paper, "DailyLossHalt")
    assert halt["closed"] == ["BTCUSD"] and halt["loss_pct"] >= 2
    await close_bar(paper, bar=paper_bar(paper) + 60)
    assert "daily loss limit hit" in skipped(paper)[0]
    with pytest.raises(ApiError, match="daily loss limit"):
        await Api(paper).place_order(["paper"], "BTCUSD", "buy", "ticket-0001", stop_loss=99.0, size=1)
    risk = await Api(paper).risk()
    assert risk["brokers"][0]["halted"] is True


def paper_bar(system) -> int:
    return max(e["bar_time"] for e in events(system, "SignalGenerated"))


async def test_resume_lifts_the_halt_from_current_equity(paper):
    await paper.update_settings({"daily_loss_limit_pct": 2})
    await guard(paper)
    paper.store.db.execute("UPDATE paper_account SET balance = balance - 30")
    await guard(paper)
    risk = await Api(paper).risk_resume("paper")
    assert risk["brokers"][0]["halted"] is False and risk["brokers"][0]["start_equity"] == pytest.approx(970)
    await close_bar(paper)
    assert len(await paper.paper.positions()) == 1


async def test_a_paper_halt_does_not_stop_delta(both, exchange):
    await both.update_settings({"daily_loss_limit_pct": 2})
    both.set_toggle("trading:delta", True)
    await guard(both)
    both.store.db.execute("UPDATE paper_account SET balance = balance - 30")
    await guard(both)
    await close_bar(both)
    assert len(exchange.placed) == 1  # Delta traded
    assert await both.paper.positions() == []


async def test_limit_zero_is_off(paper):
    await paper.update_settings({"daily_loss_limit_pct": 0})
    await guard(paper)
    paper.store.db.execute("UPDATE paper_account SET balance = balance - 500")
    await guard(paper)
    assert events(paper, "DailyLossHalt") == []


def test_trading_day_follows_the_timezone():
    t = 1_791_225_000  # 2026-10-05 21:50 UTC = 2026-10-06 03:20 IST
    assert trading_day("UTC", t) == "2026-10-05" and trading_day("Asia/Kolkata", t) == "2026-10-06"


# -- one ticket, both brokers, same share of capital --------------------------------------------


async def test_margin_pct_opens_the_same_share_of_capital_on_each_broker(both, exchange):
    both.set_toggle("trading:delta", True)
    result = await Api(both).place_order(["paper", "delta"], "BTCUSD", "buy", "ticket-0001", stop_loss=99.0, take_profit=102.0, margin_pct=10)
    await both.drain()
    assert result["errors"] == {}
    # paper: 10% of $1,000 * 10x / ($100 * 0.001) = 10,000; Delta: 10% of $90 * 10x / 0.1 = 900
    assert [o["size"] for o in result["orders"]] == [10_000, 900]
    assert exchange.placed[0]["size"] == 900 and len(await both.paper.positions()) == 1


async def test_one_broker_refusing_does_not_stop_the_other(both, exchange):
    result = await Api(both).place_order(["paper", "delta"], "BTCUSD", "buy", "ticket-0001", stop_loss=99.0, margin_pct=10)
    assert [o["broker"] for o in result["orders"]] == ["paper"]
    assert result["errors"] == {"delta": "trading is switched off"}


async def test_contracts_cannot_be_mirrored(both):
    with pytest.raises(ApiError, match="margin_pct"):
        await Api(both).place_order(["paper", "delta"], "BTCUSD", "buy", "ticket-0001", stop_loss=99.0, size=5)


async def test_ticket_previews_every_active_broker(both):
    ticket = await Api(both).order_ticket("BTCUSD", "buy", margin_pct=10)
    assert [(b["broker"], b["size"], b["trading"]) for b in ticket["brokers"]] == [("paper", 10_000, True), ("delta", 900, False)]


# -- settings and market stats -----------------------------------------------------------------


@pytest.mark.parametrize(
    "change",
    [{"trailing_max_steps": 2.5}, {"trailing_trigger_pct": 40}, {"trailing_lock_pct": 99}, {"day_timezone": "Mars"}, {"trailing_enabled": "yes"}],
)
def test_guard_settings_are_validated(change):
    with pytest.raises(SettingsError):
        apply_changes(Settings(), change)


def test_ticker_gives_the_real_24h_change():
    msg = {"type": "v2/ticker", "symbol": "BTCUSD", "close": 85622.0, "mark_price": "85617.3", "spot_price": "85637.1",
           "open": 85900.0, "high": 92295.7, "low": 84950.0, "ltp_change_24h": "-0.3236", "mark_change_24h": "0.0728",
           "volume": 1641.88, "turnover_usd": 141098155.1, "oi_value_usd": "73620675.05", "funding_rate": "0.01",
           "quotes": {"best_bid": "85608", "best_ask": "85612.5"}}
    stats = parse_stats(msg)
    assert (stats.last, stats.change_24h_pct, stats.high_24h, stats.index) == (85622.0, -0.3236, 92295.7, 85637.1)
    assert (stats.bid, stats.ask, stats.funding_rate_pct) == (85608.0, 85612.5, 0.01)
    assert parse_stats({"symbol": "ETHUSD"}).last is None  # missing fields are absent, never zero
