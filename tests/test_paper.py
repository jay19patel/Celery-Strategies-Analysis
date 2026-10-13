import time

import pytest

from tradebuddy.errors import BrokerError
from tradebuddy.events import Tick
from tradebuddy.system import System

from .conftest import IdleStream


@pytest.fixture
async def system(cfg, exchange):
    s = System(cfg, strategies=[], stream_factory=IdleStream, client_factory=exchange.client)
    await s.update_settings({"paper_starting_balance": 1000, "paper_leverage": 10, "paper_fee_pct": 0.1, "paper_slippage_pct": 0})
    s.paper.reset()
    s.bus.start()
    await tick(s, "BTCUSD", 100_000)
    yield s
    await s.bus.stop()


async def tick(system, symbol, price):
    system.bus.publish(Tick(symbol=symbol, price=price))
    await system.bus.drain()


def events(system, kind):
    return system.store.recent_events(100, types=[kind])


async def test_open_uses_contract_size_margin_and_fee(system):
    # 10 contracts * 0.001 BTC * $100,000 = $1,000 notional; 10x -> $100 margin; 0.1% -> $1 fee
    await system.paper.place_order("BTCUSD", "buy", 10, "c1", stop_loss=99_000, take_profit=102_000, strategy="s")
    account = await system.paper.account()
    assert account.margin_used == pytest.approx(100)
    assert account.balance == pytest.approx(999)
    assert account.available == pytest.approx(899)
    [pos] = await system.paper.positions()
    assert (pos.side, pos.size, pos.entry_price, pos.liquidation_price) == ("long", 10, 100_000, pytest.approx(90_000))


async def test_slippage_worsens_the_fill(system):
    await system.update_settings({"paper_slippage_pct": 0.1})
    await system.paper.place_order("BTCUSD", "sell", 1, "c1")
    [pos] = await system.paper.positions()
    assert pos.entry_price == pytest.approx(99_900)  # a sell fills lower


async def test_insufficient_margin_is_refused(system):
    with pytest.raises(BrokerError, match="insufficient margin"):
        await system.paper.place_order("BTCUSD", "buy", 200, "c1")  # $2,000 margin needed
    assert await system.paper.positions() == []


async def test_same_client_order_id_is_one_position(system):
    first = await system.paper.place_order("BTCUSD", "buy", 1, "c1")
    again = await system.paper.place_order("BTCUSD", "buy", 1, "c1")
    assert first["id"] == again["id"] and len(await system.paper.positions()) == 1


async def test_stop_loss_on_tick_realises_the_loss(system):
    await system.paper.place_order("BTCUSD", "buy", 10, "c1", stop_loss=99_000, take_profit=102_000, strategy="s")
    await tick(system, "BTCUSD", 99_500)
    assert len(await system.paper.positions()) == 1
    await tick(system, "BTCUSD", 98_900)

    assert await system.paper.positions() == []
    [trade] = system.paper.trades()
    # gross -$10 at the stop; fees $1 in + $0.99 out
    assert (trade["exit_price"], trade["reason"]) == (99_000, "stop loss hit")
    assert trade["pnl"] == pytest.approx(-10 - 1 - 0.99)
    assert (await system.paper.account()).balance == pytest.approx(1000 + trade["pnl"])
    assert events(system, "PositionClosed")[0]["pnl"] == pytest.approx(trade["pnl"])


async def test_take_profit_on_short(system):
    await system.paper.place_order("BTCUSD", "sell", 10, "c1", stop_loss=101_000, take_profit=98_000)
    await tick(system, "BTCUSD", 97_900)
    [trade] = system.paper.trades()
    assert trade["reason"] == "take profit hit" and trade["gross_pnl"] == pytest.approx(20)


async def test_liquidation_loses_no_more_than_margin(system):
    await system.paper.place_order("BTCUSD", "buy", 10, "c1")  # no SL
    await tick(system, "BTCUSD", 80_000)
    [trade] = system.paper.trades()
    assert trade["reason"] == "liquidation" and trade["gross_pnl"] == pytest.approx(-100)


async def test_max_hold_closes_at_market(system):
    await system.paper.place_order("BTCUSD", "buy", 1, "c1")
    system.store.db.execute("UPDATE paper_positions SET opened_at = ?", (time.time() - 73 * 3600,))
    await tick(system, "BTCUSD", 100_100)
    assert system.paper.trades()[0]["reason"].startswith("max hold")


async def test_protection_must_bracket_the_price(system):
    await system.paper.place_order("BTCUSD", "buy", 1, "c1")
    with pytest.raises(BrokerError):
        await system.paper.update_protection("BTCUSD", 101_000, 102_000)
    await system.paper.update_protection("BTCUSD", 95_000, 105_000)
    [pos] = await system.paper.positions()
    assert (pos.stop_loss, pos.take_profit) == (95_000, 105_000)


async def test_manual_close_and_stats(system):
    await system.paper.place_order("BTCUSD", "buy", 10, "c1", strategy="alpha")
    await tick(system, "BTCUSD", 101_000)
    await system.paper.close_position("BTCUSD")
    stats = system.paper.stats()
    assert stats["overall"]["trades"] == 1 and stats["overall"]["wins"] == 1
    assert stats["by_strategy"][0]["strategy"] == "alpha"
    # The curve opens at the starting balance and then carries the trade behind every step, so the
    # chart can colour each point and name what moved it.
    opened, after = stats["equity_curve"]
    assert len(stats["equity_curve"]) == 2 and opened[2] is None
    assert after[2]["strategy"] == "alpha" and after[2]["symbol"] == "BTCUSD" and after[2]["pnl"] > 0
    assert after[1] == round(opened[1] + after[2]["pnl"], 4)


async def test_reset_restores_starting_balance(system):
    await system.paper.place_order("BTCUSD", "buy", 1, "c1")
    system.paper.reset()
    account = await system.paper.account()
    assert (account.balance, account.margin_used) == (1000, 0)
    assert system.paper.trades() == []


async def test_no_price_no_fill(system):
    with pytest.raises(BrokerError, match="no fresh price"):
        await system.paper.place_order("ETHUSD", "buy", 1, "c1")


async def test_a_closed_trade_records_how_far_it_went_each_way(system):
    """MAE and MFE: without them a losing trade cannot say whether the signal was wrong or the stop
    was simply too tight to survive the noise."""
    await system.paper.place_order("BTCUSD", "buy", 1, "c1", stop_loss=98_000, take_profit=120_000, strategy="s")
    await tick(system, "BTCUSD", 103_000)  # +3% in favour
    await tick(system, "BTCUSD", 99_000)  # -1% against
    await tick(system, "BTCUSD", 101_000)
    [pos] = system.paper._rows()
    assert (pos["mfe_pct"], pos["mae_pct"]) == (pytest.approx(3.0), pytest.approx(-1.0))

    await system.paper.close_position("BTCUSD", "manual close")
    trade = system.paper.trades()[0]
    assert trade["mfe_pct"] == pytest.approx(3.0) and trade["mae_pct"] == pytest.approx(-1.0)


async def test_the_excursion_of_a_short_counts_the_other_way(system):
    await system.paper.place_order("BTCUSD", "sell", 1, "c1", stop_loss=102_000, take_profit=90_000, strategy="s")
    await tick(system, "BTCUSD", 98_000)  # a short gains when price falls
    await tick(system, "BTCUSD", 101_000)
    [pos] = system.paper._rows()
    assert (pos["mfe_pct"], pos["mae_pct"]) == (pytest.approx(2.0), pytest.approx(-1.0))


async def test_the_exit_price_itself_counts_towards_the_excursions(system):
    """The row is deleted on the close, so the exit has to be folded in before it goes."""
    await system.paper.place_order("BTCUSD", "buy", 1, "c1", stop_loss=90_000, take_profit=120_000, strategy="s")
    await system.paper.close_position("BTCUSD", "manual close", price=97_000)  # no tick in between
    trade = system.paper.trades()[0]
    assert trade["mae_pct"] == pytest.approx(-3.0) and trade["mfe_pct"] == 0.0


async def test_the_excursion_follows_the_market_not_the_fill(system):
    """A stop fills at its level, but the market went further: the excursion records where it went."""
    await system.paper.place_order("BTCUSD", "buy", 1, "c1", stop_loss=99_000, take_profit=120_000, strategy="s")
    await tick(system, "BTCUSD", 98_000)  # gaps straight through the stop
    trade = system.paper.trades()[0]
    assert trade["reason"] == "stop loss hit" and trade["exit_price"] == 99_000
    assert trade["mae_pct"] == pytest.approx(-2.0)
