"""Option structures: building legs from the chain, the suggestion, the paper gate, and the exits."""

from __future__ import annotations

import math
import time

import pytest
from fastapi.testclient import TestClient

from tradebuddy import structures as st
from tradebuddy.app import local_app
from tradebuddy.events import PositionClosed
from tradebuddy.options import OptionQuote, summarize
from tradebuddy.trading import TradeRefused, trading_key

from .test_pipeline import events, make

SPOT = 60_000.0


def chain(spot: float = SPOT, now: float | None = None, hours: float = 48.0, vol: float = 1.0) -> dict:
    """A smooth, plausible BTC chain: strikes 54k-66k, quotes 2% around the mark."""
    now = time.time() if now is None else now
    expiry = now + hours * 3600
    quotes = []
    for strike in range(54_000, 66_001, 1_000):
        for kind in ("call", "put"):
            intrinsic = max(spot - strike, 0) if kind == "call" else max(strike - spot, 0)
            mark = intrinsic + vol * 900 * math.exp(-(((strike - spot) / 2_500) ** 2) / 2)
            call_delta = 1 / (1 + math.exp((strike - spot) / 1_500))
            quotes.append(OptionQuote(
                symbol=f"{'C' if kind == 'call' else 'P'}-BTC-{strike}-X", underlying="BTC", kind=kind, strike=float(strike), expiry=expiry,
                mark=mark, bid=mark * 0.98, ask=mark * 1.02, mark_iv=0.45, delta=call_delta if kind == "call" else call_delta - 1,
                oi=10.0, spot=spot, at=now,
            ))
    return summarize("BTC", quotes, spot, now)


# -- building -----------------------------------------------------------------------------


def test_straddle_buys_the_atm_call_and_put():
    s = st.build("straddle", chain(), qty=2, contract_value=0.001)
    assert [(leg["action"], leg["kind"], leg["strike"]) for leg in s["legs"]] == [("buy", "call", SPOT), ("buy", "put", SPOT)]
    assert s["type"] == "debit" and s["max_profit_usd"] is None
    assert s["max_loss_usd"] == pytest.approx(s["premium_usd"]) and s["premium_usd"] == pytest.approx(900 * 2 * 1.02 * 0.002)
    low, high = s["breakevens"]
    assert low < SPOT < high and high - SPOT == pytest.approx(SPOT - low)


def test_iron_condor_is_a_credit_with_its_loss_capped_by_the_wings():
    s = st.build("iron_condor", chain(), qty=1, contract_value=0.001)
    legs = {(leg["action"], leg["kind"]): leg["strike"] for leg in s["legs"]}
    assert legs[("buy", "put")] < legs[("sell", "put")] < SPOT < legs[("sell", "call")] < legs[("buy", "call")]
    width = max(legs[("sell", "put")] - legs[("buy", "put")], legs[("buy", "call")] - legs[("sell", "call")])
    assert s["type"] == "credit" and s["max_profit_usd"] == pytest.approx(s["premium_usd"])
    assert s["max_loss_usd"] == pytest.approx((width + s["net"]) * 0.001, abs=1e-3)  # net < 0 for a credit


def test_strangle_and_spreads_use_out_of_the_money_wings():
    strangle = st.build("strangle", chain())
    call, put = (leg["strike"] for leg in strangle["legs"])
    assert call > SPOT > put
    bull = st.build("call_spread", chain())
    assert bull["legs"][0]["strike"] < bull["legs"][1]["strike"] and bull["max_profit_usd"] is not None
    bear = st.build("put_spread", chain())
    assert bear["legs"][0]["strike"] > bear["legs"][1]["strike"]


def test_picks_use_those_strikes_or_refuse():
    picks = [{"action": "buy", "kind": "call", "strike": 61_000}, {"action": "buy", "kind": "put", "strike": 59_000}]
    s = st.build("strangle", chain(), picks=picks)
    assert [leg["strike"] for leg in s["legs"]] == [61_000, 59_000]
    with pytest.raises(st.StructureError, match="no longer in the chain"):
        st.build("strangle", chain(), picks=[{"action": "buy", "kind": "call", "strike": 99_000}])


def test_expiry_must_cover_a_day_and_bad_input_is_refused():
    with pytest.raises(st.StructureError, match="at least 20h"):
        st.build("straddle", chain(hours=5))
    for bad in ({"kind": "short_straddle"}, {"kind": "straddle", "qty": 0}):
        with pytest.raises(st.StructureError):
            st.build(bad["kind"], chain(), qty=bad.get("qty", 1))


# -- suggestion ---------------------------------------------------------------------------


def history(ivs):
    return [{"atm_iv": iv} for iv in ivs]


def test_suggestion_follows_the_playbook_first():
    pb = {"strategy": "IRON_CONDOR", "reason": "IV rich", "legs": [{"action": "sell", "kind": "put", "strike": 58_000}]}
    sg = st.suggest(chain(), pb, [])
    assert sg["kind"] == "iron_condor" and sg["source"] == "playbook" and sg["picks"][0]["strike"] == 58_000


def test_without_a_forecast_iv_rank_decides():
    summary = chain()  # ATM IV 0.45
    assert st.suggest(summary, None, history([0.45 + i / 1000 for i in range(100)]))["kind"] in ("straddle", "strangle")  # cheapest
    assert st.suggest(summary, None, history([0.30 + i / 1000 for i in range(100)]))["kind"] == "iron_condor"  # dearest
    assert st.suggest(summary, None, history([0.45] * 10))["kind"] is None  # not enough history


# -- the paper gate and exits ------------------------------------------------------------


async def with_chain(cfg, exchange, **kw):
    system = await make(cfg, exchange)
    summary = chain(**kw)
    system.options_latest["BTCUSD"] = {"source": "test", "at": summary["at"], "summary": summary}
    return system


def place(system, rid="req-00000001", **kw):
    args = {"broker": "paper", "symbol": "BTCUSD", "kind": "straddle", "qty": 1, "request_id": rid, "sl_pct": 50.0} | kw
    return system.trader.manual_structure(**args)


async def test_a_structure_is_filled_recorded_and_holds_its_max_loss_as_margin(cfg, exchange):
    system = await with_chain(cfg, exchange)
    before = await system.paper.account()
    result = await place(system)
    assert result["status"] == "filled" and "STRADDLE" in result["label"]
    assert [(o["symbol"], o["side"], o["status"]) for o in result["orders"]] == [("C-BTC-60000-X", "buy", "filled"), ("P-BTC-60000-X", "buy", "filled")]
    row = system.paper.options.rows()[0]
    after = await system.paper.account()
    assert after.margin_used == pytest.approx(before.margin_used + row["capital"])
    assert after.balance == pytest.approx(before.balance - row["entry_fee"])
    assert await place(system) == result  # the same ticket again is the same order
    assert len(system.store.recent_orders()) == 2


def test_option_fees_follow_delta_india():
    # Delta's own example: 300 contracts (0.3 BTC) at a $150 premium, BTC at $90,000:
    # 0.010% of $27,000 = $2.70, capped at 3.5% of the $45 premium = $1.575, plus 18% GST.
    assert st.fee(150, 90_000, 300 * 0.001) == pytest.approx(1.575 * 1.18)
    assert st.fee(5_000, 90_000, 0.3) == pytest.approx(2.70 * 1.18)  # a dear option: the notional rate applies


async def test_a_condor_is_four_orders_with_the_wings_bought_first(cfg, exchange):
    system = await with_chain(cfg, exchange)
    result = await place(system, kind="iron_condor")
    assert [o["side"] for o in result["orders"]] == ["buy", "buy", "sell", "sell"]
    assert len({o["client_order_id"] for o in result["orders"]}) == 4
    legs = [{"action": a} for a in ("sell", "buy", "sell", "buy")]
    assert st.execution_order(legs) == [1, 3, 0, 2] and st.execution_order(legs, closing=True) == [0, 2, 1, 3]


async def test_the_gate_refuses_with_a_reason(cfg, exchange):
    system = await with_chain(cfg, exchange)
    with pytest.raises(TradeRefused, match="paper only"):
        await place(system, broker="delta")
    with pytest.raises(TradeRefused, match="stop loss"):
        await place(system, sl_pct=0)
    with pytest.raises(TradeRefused, match="chain moved"):
        await place(system, legs=["C-BTC-1-X", "P-BTC-1-X"])
    system.set_toggle(trading_key("paper"), False)
    with pytest.raises(TradeRefused, match="switched off"):
        await place(system)
    system.set_toggle(trading_key("paper"), True)
    await place(system)
    with pytest.raises(TradeRefused, match="already open"):
        await place(system, rid="req-00000002", kind="iron_condor")
    await system.drain()
    assert len([e for e in events(system, "TradeSkipped") if e["strategy"] == "manual"]) == 5


async def test_a_stale_chain_is_no_price(cfg, exchange):
    system = await with_chain(cfg, exchange, now=time.time() - 120)
    with pytest.raises(TradeRefused, match="no fresh options chain"):
        await place(system)


async def test_stop_loss_closes_the_whole_structure_into_paper_trades(cfg, exchange):
    system = await with_chain(cfg, exchange)
    await place(system)
    assert system.paper.options.check() == []  # nothing has moved
    crushed = chain(vol=0.2)  # IV collapses, spot unchanged: the straddle loses most of its value
    system.options_latest["BTCUSD"]["summary"] = crushed
    closed = system.paper.options.check()
    assert len(closed) == 1 and system.paper.options.count() == 0
    trade = system.paper.trades()[0]
    assert trade["reason"].startswith("stop loss") and trade["pnl"] < 0 and trade["margin"] > 0
    assert (await system.paper.account()).margin_used == 0
    await system.drain()
    assert any(e["symbol"] == trade["symbol"] for e in events(system, "PositionClosed"))


async def test_take_profit_on_a_big_move(cfg, exchange):
    system = await with_chain(cfg, exchange)
    await place(system)
    system.options_latest["BTCUSD"]["summary"] = chain(spot=64_000)
    system.paper.options.check()
    assert system.paper.trades()[0]["reason"].startswith("take profit") and system.paper.trades()[0]["pnl"] > 0


async def test_closed_before_expiry_and_settled_after_it_without_prices(cfg, exchange):
    system = await with_chain(cfg, exchange)
    await place(system)
    row = system.paper.options.rows()[0]
    system.paper.options.check(now=row["expiry"] - 1800)  # stale chain at that time: settled only after expiry
    assert system.paper.options.count() == 1
    system.paper.options.check(now=row["expiry"] + 1)
    trade = system.paper.trades()[0]
    assert system.paper.options.count() == 0 and trade["reason"].startswith("settled at expiry")


async def test_kill_switch_closes_structures_too(cfg, exchange):
    system = await with_chain(cfg, exchange)
    await place(system)
    result = await system.close_all()
    assert system.paper.options.count() == 0 and any("STRADDLE" in c for c in result["closed"])


def test_the_api_previews_and_requires_a_stop(cfg, exchange):
    import asyncio

    system = asyncio.run(with_chain(cfg, exchange))
    client = TestClient(local_app(system))
    ticket = client.get("/api/option-ticket?symbol=BTCUSD&kind=iron_condor").json()
    assert ticket["structure"]["kind"] == "iron_condor" and len(ticket["structure"]["legs"]) == 4 and ticket["fresh"]
    body = {"symbol": "BTCUSD", "kind": "iron_condor", "qty": 1, "request_id": "req-00000009", "legs": [x["symbol"] for x in ticket["structure"]["legs"]]}
    assert client.post("/api/structures", json=body).status_code == 422  # no sl_pct
    assert client.post("/api/structures", json=body | {"sl_pct": 50}).status_code == 200
    assert client.get("/api/structures").json()[0]["kind"] == "iron_condor"


def test_position_closed_is_the_event_a_structure_close_publishes():
    assert "symbol" in PositionClosed.__dataclass_fields__


# -- auto-trade ---------------------------------------------------------------------------

RICH = history([0.30 + i / 1000 for i in range(100)])  # today's 45% ATM IV is the top of the range
CHEAP = history([0.45 + i / 1000 for i in range(100)])


async def auto(cfg, exchange, ivs=RICH, **settings):
    system = await with_chain(cfg, exchange)
    await system.update_settings({"options_auto_enabled": True} | settings)
    system.auto_options.history = lambda underlying, since: ivs
    return system


def test_auto_trade_is_off_by_default_with_a_small_stop_and_target():
    from tradebuddy.settings import Settings, SettingsError, apply_changes

    s = Settings()
    assert s.options_auto_enabled is False and s.options_auto_sl_pct == 30 and s.options_auto_tp_pct == 40 and s.options_auto_qty == 1
    with pytest.raises(SettingsError):
        apply_changes(s, {"options_auto_sl_pct": 1})


async def test_auto_opens_the_suggested_structure_with_the_settings_stop(cfg, exchange):
    system = await auto(cfg, exchange)
    note = await system.auto_options.check()
    row = system.paper.options.rows()[0]
    assert "opened BTC IRON CONDOR" in note and row["strategy"] == "auto_options"
    assert (row["sl_pct"], row["tp_pct"], row["qty"]) == (30.0, 40.0, 1)
    assert {o["strategy"] for o in system.store.recent_orders()} == {"auto_options"}
    assert "holding" in await system.auto_options.check()  # one per underlying
    assert system.paper.options.count() == 1


async def test_cheap_volatility_buys_it(cfg, exchange):
    system = await auto(cfg, exchange, ivs=CHEAP, options_auto_qty=2, options_auto_tp_pct=25)
    await system.auto_options.check()
    row = system.paper.options.rows()[0]
    assert row["kind"] in ("straddle", "strangle") and row["qty"] == 2 and row["tp_pct"] == 25


async def test_nothing_without_an_edge_or_with_trading_off(cfg, exchange):
    system = await auto(cfg, exchange, ivs=history([0.40 + i / 1000 for i in range(100)]))  # 45% is mid-range
    assert "no edge" in await system.auto_options.check() and system.paper.options.count() == 0
    system.auto_options.history = lambda underlying, since: RICH
    system.set_toggle(trading_key("paper"), False)
    assert await system.auto_options.check() == "paper trading is switched off"
    await system.update_settings({"options_auto_enabled": False})
    assert await system.auto_options.check() == "off"
    await system.drain()
    assert system.paper.options.count() == 0 and not [e for e in events(system, "TradeSkipped") if e["strategy"] == "auto_options"]


async def test_cooldown_after_a_close_and_a_daily_maximum(cfg, exchange):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from tradebuddy.trading import AUTO_COOLDOWN, AUTO_MAX_PER_DAY

    # IST and UTC midnights are 5.5h apart, so one of them leaves the 2h this test spans inside one day.
    tz = next(z for z in ("Asia/Kolkata", "UTC") if datetime.now(ZoneInfo(z)).hour < 21)
    system = await auto(cfg, exchange, day_timezone=tz)
    r = system.auto_options
    base = time.time()
    notes = []
    for n in range(AUTO_MAX_PER_DAY + 1):
        t = base + n * (AUTO_COOLDOWN + 60)
        system.options_latest["BTCUSD"]["summary"] = chain(now=t)
        r._quiet_until.clear()  # only the close's cooldown below
        notes.append(await r.check(t))
        if system.paper.options.count():
            system.paper.options.close(system.paper.options.rows()[0]["id"], "test", now=t)
            assert "cooling down" in await r.check(t + 60)
    assert sum("opened" in x for x in notes) == AUTO_MAX_PER_DAY and "daily maximum" in notes[-1]


async def test_no_condor_when_the_front_expiry_prices_an_event():
    summary = chain()
    summary["nearest"] = dict(summary["nearest"], atm_iv=summary["day"]["atm_iv"] * 1.3)
    sg = st.suggest(summary, None, RICH)
    assert sg["kind"] is None and "event" in sg["reason"]


# -- shown as Delta shows it --------------------------------------------------------------


async def test_legs_live_on_the_structure_not_in_open_positions(cfg, exchange):
    from tradebuddy.api import Api

    system = await with_chain(cfg, exchange)
    await place(system)
    api = Api(system)
    assert await api.positions("paper") == []  # perp positions only
    s = (await api.structures("paper"))[0]
    call = s["legs"][0]
    assert call["symbol"] == "C-BTC-60000-X" and call["mark"] == pytest.approx(900) and call["price"] == pytest.approx(918)
    assert call["pnl"] == pytest.approx((900 - 918) * 0.001)  # valued at the mark, as Delta does
    assert {o["symbol"] for o in await api.orders("paper")} == {"C-BTC-60000-X", "P-BTC-60000-X"}  # the order history keeps every leg


async def test_unrealised_is_at_the_mark_and_a_close_at_bid_after_fees(cfg, exchange):
    system = await with_chain(cfg, exchange)
    await place(system)
    v = system.paper.options.view(system.paper.options.rows()[0])
    assert v["unrealized_pnl"] == pytest.approx((1800 - 1836) * 0.001)  # marks 900+900, paid 918+918
    assert v["close_pnl"] < (1764 - 1836) * 0.001  # sold at the bid (882 each), and fees on top
    assert len(v["breakevens"]) == 2 and v["greeks"]["delta"] == pytest.approx(0, abs=1e-4)
    assert v["max_profit"] is None and v["progress_pct"] < 0
