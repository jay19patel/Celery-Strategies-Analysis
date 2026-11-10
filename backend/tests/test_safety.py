"""The safety rules added by the review: crash recovery, reconciliation with the exchange, the
drawdown stop, a tape that cannot lock itself out, no stale tape, no secret in the logs."""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx
import pytest
from sqlmodel import select

from app.core.db import session
from app.core.models import Leg, OrderRecord, Structure
from app.errors import Refused, SettingsError
from app.market.tape import Tape
from app.trading.guard import Guard
from app.trading.reconcile import Reconciler, differences, expected_positions
from app.trading.risk import kill_state, reconcile_reason, set_kill, set_reconcile


def _row(sid: str, status: str, broker: str = "paper", **kw: Any) -> Structure:
    return Structure(id=sid, broker=broker, underlying="BTC", strategy="iron_condor", source="auto", status=status,
                     expiry=time.time() + 86_400, qty=1, take_profit_pct=50.0, stop_loss_pct=100.0, opened_at=time.time(), **kw)


# -- crash recovery --------------------------------------------------------------------------------


async def test_a_structure_left_half_opened_by_a_crash_is_recovered_and_closed(env: Any) -> None:
    """The process died after the bought wing filled and before the sold leg was sent. On start the
    structure is `error`, its recorded fill is on its leg, and the guard gets it flat."""
    env.configure(max_spread_pct=100)
    plan = env.desk.plan("BTC", "iron_condor")
    wing = plan.legs[0]
    fill = await env.paper.execute(__import__("app.brokers.base", fromlist=["LegOrder"]).LegOrder(
        "scrash0o0", wing.symbol, "buy", 1, (wing.ask or wing.price) * 1.05, wing.contract_value))
    with session() as s:
        s.add(_row("scrash", "opening"))
        s.commit()
        for i, leg in enumerate(plan.legs):
            s.add(Leg(structure_id="scrash", idx=i, symbol=leg.symbol, kind=leg.kind, strike=leg.strike, side=leg.side,
                      size=1, contract_value=leg.contract_value))
        s.add(OrderRecord(client_order_id="scrash0o0", structure_id="scrash", broker="paper", purpose="open", symbol=wing.symbol,
                          side="buy", size=1, limit_price=wing.price, status="filled", fill_price=fill.price, filled_size=1,
                          fee=fill.fee, created_at=time.time(), updated_at=time.time()))
        s.add(OrderRecord(client_order_id="scrash1o0", structure_id="scrash", broker="paper", purpose="open", symbol=plan.legs[1].symbol,
                          side="sell", size=1, limit_price=1.0, status="pending", created_at=time.time(), updated_at=time.time()))
        s.commit()

    assert env.trading.recover() == ["scrash"]
    st, legs = env.trading._load("scrash")
    assert st.status == "error" and "interrupted by a restart" in st.error
    assert legs[0].entry_price == fill.price and legs[1].entry_price is None
    with session() as s:
        assert s.get(OrderRecord, "scrash1o0").status == "unknown"  # sent, never heard back: looked up, not resent
    with pytest.raises(Refused, match="unresolved"):
        await env.trading.open("paper", plan, 1, "manual", "req-after-crash")  # the broker waits for it

    await Guard(env.trading, env.settings, env.hub).check_structures()
    done = env.trading.structure("scrash")
    assert done["status"] == "closed" and done["legs"][0]["exit_price"] is not None  # the wing was sold back
    assert env.trading.recover() == []


# -- reconciliation --------------------------------------------------------------------------------


class Positions:
    name = "delta"

    def __init__(self, held: dict[str, int]) -> None:
        self.held = held

    def not_ready(self) -> str:
        return ""

    async def positions(self) -> dict[str, int]:
        return self.held


def test_expected_positions_net_every_open_leg() -> None:
    with session() as s:
        s.add(_row("r1", "open", broker="delta"))
        s.add(_row("r2", "closed", broker="delta"))
        s.commit()
        s.add(Leg(structure_id="r1", idx=0, symbol="BTCUSD", kind="perp", strike=0, side="sell", size=5, contract_value=0.001, entry_price=1.0))
        s.add(Leg(structure_id="r1", idx=1, symbol="C-BTC-1", kind="call", strike=1, side="buy", size=2, contract_value=0.001,
                  entry_price=1.0, exit_price=1.0))  # already out
        s.add(Leg(structure_id="r2", idx=0, symbol="ETHUSD", kind="perp", strike=0, side="buy", size=1, contract_value=0.01, entry_price=1.0))
        s.commit()
    assert expected_positions("delta") == {"BTCUSD": -5}
    assert differences({"BTCUSD": -5}, {"BTCUSD": -5}) == []
    assert differences({"BTCUSD": -5}, {"BTCUSD": -3, "ETHUSD": 1}) == ["BTCUSD: exchange -3, desk -5", "ETHUSD: exchange +1, desk +0"]


async def test_a_mismatch_with_the_exchange_blocks_delta_until_they_agree(env: Any) -> None:
    env.configure(delta_active=True)
    exchange = Positions({"BTCUSD": 3})  # a position the desk knows nothing about
    rec = Reconciler({"delta": exchange}, env.settings, env.hub)
    assert (await rec.run()).startswith("mismatch")
    assert "BTCUSD" in reconcile_reason("delta")
    env.brokers["delta"] = exchange
    with pytest.raises(Refused, match="does not match the exchange"):
        env.trading._ready("delta", env.settings())
    exchange.held = {}  # closed by hand on the exchange
    assert "match" in await rec.run()
    assert reconcile_reason("delta") == ""


async def test_an_inactive_delta_blocks_nothing(env: Any) -> None:
    set_reconcile("delta", "stale")
    assert await Reconciler({"delta": Positions({"BTCUSD": 1})}, env.settings, env.hub).run() == "delta inactive"
    assert reconcile_reason("delta") == ""


# -- drawdown stop ---------------------------------------------------------------------------------


async def test_a_drawdown_past_the_limit_stops_every_entry_and_closes_everything(env: Any) -> None:
    env.configure(max_spread_pct=100, max_drawdown_pct=10, daily_loss_limit_pct=0)
    held = await env.trading.open("paper", env.desk.plan("BTC", "iron_condor"), 1, "manual", "req-dd-held")
    assert held["status"] == "open"
    with session() as s:
        s.add(_row("won", "closed", pnl=500.0, closed_at=time.time() - 7200))  # up 500 to a peak ...
        s.add(_row("lost", "closed", pnl=-400.0, closed_at=time.time() - 3600))  # ... then 400 of it back: 29% down
        s.commit()
    await Guard(env.trading, env.settings, env.hub).check_daily_loss()
    stopped = kill_state()
    assert stopped is not None and "below its peak" in stopped["reason"]
    assert env.trading.structure(held["id"])["status"] == "closed"
    with pytest.raises(Refused, match="every entry is stopped"):
        await env.trading.open("paper", env.desk.plan("BTC", "iron_condor"), 1, "manual", "req-dd-after")
    set_kill("")


# -- tape and flow ---------------------------------------------------------------------------------


def test_a_junk_first_print_cannot_lock_the_tape_out() -> None:
    """Judged against the last print, one 0.05 print on the ETH perpetual rejected every real one
    after it until a restart. Judged against the mark, the junk is the one left out."""
    tape = Tape()
    now = time.time()
    tape.trade("ETHUSD", 0.05, 1.0, True, now, mark=2_240.0)
    for i in range(5):
        tape.trade("ETHUSD", 2_240.0 + i, 1.0, True, now + i, mark=2_240.0)
    assert tape.rejected == 1
    assert tape._minutes["ETHUSD"].close == 2_244.0


async def test_flow_reads_only_finished_bars_and_stale_tape_is_not_ready(env: Any) -> None:
    from app.analysis.service import MarketDesk, store_flow_bars

    def minute(t: int) -> dict[str, Any]:
        return {"symbol": "BTCUSD", "time": t, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0, "buy": 1.0,
                "sell": 0.0, "trades": 1, "max_trade": 1.0, "levels": [], "bid_depth": None, "ask_depth": None}

    desk = MarketDesk(env.feed, env.settings, env.hub)
    now = int(time.time())
    stale = now // 60 * 60 - 3 * 3600
    store_flow_bars([minute(stale - 60 * i) for i in range(200)])
    old = await desk.flow("BTC", 5)
    assert not old["ready"] and "stale" in old["note"]

    forming = now // 300 * 300
    store_flow_bars([minute(forming - 60 * i) for i in range(1, 200)] + [minute(forming)])
    fresh = await desk.flow("BTC", 5)
    assert fresh.get("ready"), fresh
    assert all(p["time"] < forming for p in fresh["series"])  # the bar still forming is left out


# -- secrets and the PIN ---------------------------------------------------------------------------


async def test_the_telegram_token_never_reaches_the_logs(caplog: pytest.LogCaptureFixture) -> None:
    from app.main import create_app
    from app.notify.notifier import Notifier
    from app.settings import Settings

    create_app()  # sets the log levels
    s = Settings(notify_telegram=True, telegram_bot_token="123:SECRET-TOKEN", telegram_chat_id="1")
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"ok": True})))
    with caplog.at_level(logging.DEBUG):
        logging.getLogger("httpx").setLevel(logging.WARNING)  # what create_app sets, re-applied over caplog
        assert await Notifier(lambda: s, http).send("t", "x") == {"telegram": "ok"}
    assert "SECRET-TOKEN" not in caplog.text


async def test_real_money_waits_for_a_pin_that_is_not_the_published_one(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import Config
    from app.core import security
    from app.engine import Engine
    from app.settings import LIVE_CONFIRM_PHRASE

    engine = Engine(Config(db_path=str(tmp_path / "pin.db"), engine_enabled=False))
    monkeypatch.setattr(security, "using_default_pin", lambda: True)
    with pytest.raises(SettingsError, match="PIN"):
        await engine.update_settings({"delta_active": True, "delta_env": "live"}, LIVE_CONFIRM_PHRASE)
    monkeypatch.setattr(security, "using_default_pin", lambda: False)
    assert (await engine.update_settings({"delta_active": True, "delta_env": "live"}, LIVE_CONFIRM_PHRASE)).is_real_money
    await engine.stop()


# -- leverage --------------------------------------------------------------------------------------


async def test_a_perpetuals_margin_uses_the_brokers_own_leverage(env: Any) -> None:
    env.feed.set_perp(80_000.0, bid=79_999.5, ask=80_000.5)
    env.configure(paper_leverage=2)
    view = await env.trading.open_directional("paper", "BTC", "buy", 5, 79_500, 81_000, "manual", "manual", "req-lev-1", "")
    assert view["margin"] == pytest.approx(view["legs"][0]["entry_price"] * 5 * 0.001 / 2)
    with session() as s:
        assert s.exec(select(Structure)).first() is not None
