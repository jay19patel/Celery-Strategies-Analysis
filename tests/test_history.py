"""Account page data: closed trades a page at a time, per-strategy numbers, and what an open position shows."""

from __future__ import annotations

import time

import pytest

from tradebuddy.api import Api

from .test_pipeline import close_bar, make


def add_trades(system, n, strategy="s1", pnl=1.0):
    now = time.time()
    with system.store.db:
        for i in range(n):
            system.store.db.execute(
                "INSERT INTO paper_trades (client_order_id, strategy, symbol, side, size, contract_value, entry_price, exit_price,"
                " leverage, margin, gross_pnl, fees, pnl, reason, opened_at, closed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (f"{strategy}-{i}", strategy, "BTCUSD", "long", 1, 0.001, 100, 101, 10, 10, pnl, 0.1, pnl * (1 if i % 2 else -1),
                 "take profit hit" if i % 2 else "stop loss hit", now - 600 - i, now - i),
            )


async def test_history_pages_and_filters(cfg, exchange):
    system = await make(cfg, exchange)
    add_trades(system, 30, "s1")
    add_trades(system, 5, "s2")
    h = system.paper.history(page=2, per_page=10)
    assert h["total"] == 35 and h["pages"] == 4 and h["page"] == 2 and len(h["rows"]) == 10
    assert h["rows"][0]["closed_at"] >= h["rows"][-1]["closed_at"]  # newest first
    assert set(h["strategies"]) == {"s1", "s2"}
    only = system.paper.history(strategy="s2")
    assert only["total"] == 5 and {r["strategy"] for r in only["rows"]} == {"s2"}
    wins = system.paper.history(outcome="win")
    assert wins["summary"]["win_rate"] == 100.0 and all(r["pnl"] > 0 for r in wins["rows"])
    assert system.paper.history(q="stop loss")["total"] == 18
    assert system.paper.history(page=99, per_page=10)["page"] == 4  # past the end: the last page
    assert system.paper.history(outcome="1; DROP TABLE paper_trades")["total"] == 35  # unknown outcome: no filter


async def test_strategy_stats_have_expectancy_and_hold(cfg, exchange):
    system = await make(cfg, exchange)
    add_trades(system, 4, "s1", pnl=2.0)
    s1 = system.paper.stats()["by_strategy"][0]
    assert s1["strategy"] == "s1" and s1["wins"] == 2 and s1["losses"] == 2
    assert s1["avg_win"] == 2.0 and s1["avg_loss"] == -2.0 and s1["expectancy"] == 0.0
    assert s1["avg_hold_seconds"] == 600 and s1["share_pct"] == 100.0
    assert system.paper.stats()["exit_reasons"] == {"take profit hit": 2, "stop loss hit": 2}


async def test_a_closed_trade_carries_its_stop_and_r(cfg, exchange):
    system = await make(cfg, exchange)
    await close_bar(system)
    await system.paper.close_position("BTCUSD", reason="manual close")
    t = system.paper.history()["rows"][0]
    assert t["stop_loss"] and t["take_profit"] and t["risk_usd"] > 0 and t["r_multiple"] is not None and t["legs"] == []


async def test_open_positions_show_risk_reward_and_distances(cfg, exchange):
    system = await make(cfg, exchange)
    await close_bar(system)
    p = (await Api(system).positions("paper"))[0]
    assert p["notional"] > 0 and p["leverage"] == pytest.approx(system.settings.paper_leverage)
    assert p["risk_usd"] < 0 < p["reward_usd"] and p["reward_risk"] > 0
    assert p["sl_distance_pct"] < 0 < p["tp_distance_pct"] and p["entry_fee"] > 0 and p["client_order_id"]


# -- option structures as their own lines -------------------------------------------------


def add_structure_trades(system, labels, strategy="auto_options", pnl=1.0):
    """Closed structures the way the paper broker writes them: the label is the symbol."""
    now = time.time()
    with system.store.db:
        for i, label in enumerate(labels):
            system.store.db.execute(
                "INSERT INTO paper_trades (client_order_id, strategy, symbol, side, size, contract_value, entry_price, exit_price,"
                " leverage, margin, gross_pnl, fees, pnl, reason, opened_at, closed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (f"st-{i}-{label}", strategy, label, "short", 1, 0.001, 10, 8, 1, 1, pnl, 0.01, pnl,
                 "take profit", now - 600 - i, now - i),
            )


async def test_structures_are_counted_by_kind_not_only_by_strategy(cfg, exchange):
    """auto_options -0.69 says nothing about whether the condors or the straddles did it."""
    system = await make(cfg, exchange)
    add_structure_trades(system, [
        "ETH IRON CONDOR 14 Oct 2420/2460/2560/2600",
        "BTC IRON CONDOR 14 Oct 81000/82200/84400/85600",
    ], pnl=3.0)
    add_structure_trades(system, ["BTC LONG STRADDLE 11 Oct 83000"], pnl=-5.0)
    add_trades(system, 2, "ema_cross_15m", pnl=1.0)  # directional: never an option structure

    by_kind = {s["kind"]: s for s in system.paper.stats()["by_structure"]}
    assert set(by_kind) == {"iron_condor", "straddle"}  # the directional trades are not in here
    assert by_kind["iron_condor"]["trades"] == 2 and by_kind["iron_condor"]["pnl"] == 6.0
    assert by_kind["iron_condor"]["underlyings"] == ["BTC", "ETH"]
    assert by_kind["iron_condor"]["credit"] is True  # a condor is sold, so the premium comes in
    assert by_kind["straddle"]["trades"] == 1 and by_kind["straddle"]["pnl"] == -5.0
    assert by_kind["straddle"]["credit"] is False
    assert by_kind["iron_condor"]["strategies"] == ["auto_options"]


async def test_a_strategy_is_flagged_as_options_only_when_every_trade_is_a_structure(cfg, exchange):
    system = await make(cfg, exchange)
    add_structure_trades(system, ["ETH IRON CONDOR 14 Oct 2420/2460/2560/2600"])
    add_trades(system, 1, "ema_cross_15m")
    by_name = {s["strategy"]: s for s in system.paper.stats()["by_strategy"]}
    assert by_name["auto_options"]["options"] is True
    assert by_name["ema_cross_15m"]["options"] is False


async def test_an_account_with_no_structures_has_an_empty_breakdown(cfg, exchange):
    system = await make(cfg, exchange)
    add_trades(system, 3, "s1")
    assert system.paper.stats()["by_structure"] == []
