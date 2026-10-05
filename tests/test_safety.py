"""Manual orders, per-broker actions, protection edits, restart reconciliation and the kill switch."""

from __future__ import annotations

import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from tradebuddy.api import Api, ApiError
from tradebuddy.app import local_app
from tradebuddy.delta import DeltaClient, DeltaError
from tradebuddy.system import System
from tradebuddy.trading import MANUAL, client_order_id, manual_order_id

from .test_pipeline import BAR, close_bar, events, make, skipped

DELTA_BTC = {"product_symbol": "BTCUSD", "size": 2, "entry_price": "100", "mark_price": "100"}


@pytest.fixture
async def paper(cfg, exchange):
    s = await make(cfg, exchange)
    yield s
    await s.bus.stop()


@pytest.fixture
async def both(cfg, exchange):
    s = await make(cfg, exchange, ("paper", "delta"))
    yield s
    await s.bus.stop()


async def order(system, request_id="ticket-0001", broker="paper", **kw):
    params = {"brokers": [broker], "symbol": "BTCUSD", "side": "buy", "size": 5, "stop_loss": 99.0, "take_profit": 102.0} | kw
    result = await Api(system).place_order(request_id=request_id, **params)
    await system.drain()
    return result


# -- manual orders ----------------------------------------------------------------


async def test_manual_order_is_recorded_and_sent_by_the_executor(paper):
    result = await order(paper)
    cid = manual_order_id("paper", "ticket-0001")
    assert result["orders"][0]["client_order_id"] == cid and result["errors"] == {}
    row = paper.store.order(cid)
    assert (row["strategy"], row["status"], row["size"]) == (MANUAL, "filled", 5)
    assert [e["client_order_id"] for e in events(paper, "OrderRequested")] == [cid]
    [pos] = await paper.paper.positions()
    assert (pos.strategy, pos.stop_loss, pos.take_profit) == (MANUAL, 99.0, 102.0)


async def test_same_ticket_twice_is_one_order(paper):
    await order(paper)
    await order(paper)
    assert len(events(paper, "OrderRequested")) == 1


async def test_manual_order_obeys_the_trading_switch(paper):
    paper.set_toggle("trading:paper", False)
    with pytest.raises(ApiError) as info:
        await order(paper)
    assert info.value.status == 409 and "switched off" in info.value.detail
    await paper.drain()
    assert skipped(paper, MANUAL) == ["trading is switched off"]
    assert events(paper, "OrderRequested") == []


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"stop_loss": 101.0}, "stop loss must be below"),
        ({"side": "sell"}, "stop loss must be above"),  # SL 99 is on the wrong side for a short
        ({"take_profit": 98.0}, "take profit must be above"),
        ({"stop_loss": None}, "stop loss is required"),
        ({"size": 0.5}, "whole number of contracts"),
        ({"side": "BUY"}, "side must be buy or sell"),
        ({"broker": "delta"}, "not an active broker"),
    ],
)
async def test_manual_order_is_validated(paper, change, reason):
    with pytest.raises(ApiError) as info:
        await order(paper, **change)
    assert reason in info.value.detail
    assert await paper.paper.positions() == []


async def test_manual_order_blocks_on_an_open_position(paper):
    await close_bar(paper)  # always_buy opens BTCUSD
    with pytest.raises(ApiError, match="position is already open"):
        await order(paper)


def test_order_route_rejects_bad_bodies(cfg, exchange):
    client = TestClient(local_app(System(cfg, strategies=[], client_factory=exchange.client)))
    good = {"brokers": ["paper"], "symbol": "BTCUSD", "side": "buy", "size": 1, "request_id": "ticket-0001", "stop_loss": 99}
    for bad in ({"side": "long"}, {"size": 1.5}, {"size": 0}, {"stop_loss": None}, {"request_id": "x"}, {"size": -1}):
        assert client.post("/api/orders", json=good | bad).status_code == 422, bad


async def test_order_ticket_sizes_in_contracts(paper):
    ticket = await Api(paper).order_ticket("BTCUSD", "sell")
    # 20% of $1,000 = $200 margin, 10x -> $2,000 notional, at $100 * 0.001 per contract -> 20,000 contracts
    assert [(b["broker"], b["size"]) for b in ticket["brokers"]] == [("paper", 20_000)]
    assert (ticket["stop_loss"], ticket["take_profit"]) == (101.0, 98.0)


# -- sizing -------------------------------------------------------------------------


async def test_sizing_failure_is_a_skip_not_a_lost_signal(both, exchange):
    both.set_toggle("trading:delta", True)
    exchange.leverage = DeltaError("leverage endpoint down")
    await close_bar(both)
    assert exchange.placed == []
    assert any(r.startswith("could not size the order") for r in skipped(both))
    assert len(await both.paper.positions()) == 1  # paper is independent


async def test_delta_sizes_with_the_account_leverage(both, exchange):
    both.set_toggle("trading:delta", True)
    exchange.leverage = 2.0
    await close_bar(both)
    # $90 available * 20% = $18 margin, 2x -> $36 notional / ($100 * 0.001) = 360 contracts
    assert exchange.placed[0]["size"] == 360


# -- per-broker actions ---------------------------------------------------------------


async def test_closing_on_paper_never_touches_delta(both, exchange):
    await close_bar(both)
    exchange.open_positions.append(DELTA_BTC)
    assert (await Api(both).close_position("paper", "BTCUSD"))["closed"] == ["BTCUSD"]
    assert exchange.placed == []


async def test_protection_targets_one_broker(both, exchange):
    await close_bar(both)
    await Api(both).protection("paper", "BTCUSD", stop_loss=95.0, take_profit=110.0)
    [pos] = await both.paper.positions()
    assert (pos.stop_loss, pos.take_profit) == (95.0, 110.0)
    assert exchange.protection == []


async def test_protection_errors_are_reported(both, exchange):
    await close_bar(both)
    with pytest.raises(ApiError, match="stop loss must be below"):
        await Api(both).protection("paper", "BTCUSD", stop_loss=105.0, take_profit=110.0)
    with pytest.raises(ApiError, match="stop loss is required"):
        await Api(both).protection("paper", "BTCUSD", stop_loss=0, take_profit=110.0)
    exchange.open_positions.append(DELTA_BTC)
    exchange.protection_result = DeltaError("protection unchanged — rejected")
    with pytest.raises(ApiError, match="protection unchanged"):
        await Api(both).protection("delta", "BTCUSD", stop_loss=95.0)


async def test_delta_protection_is_checked_against_the_mark(both, exchange):
    exchange.open_positions.append(DELTA_BTC)  # long, mark 100
    with pytest.raises(ApiError, match="stop loss must be below"):
        await Api(both).protection("delta", "BTCUSD", stop_loss=100.5)
    assert exchange.protection == []
    await Api(both).protection("delta", "BTCUSD", stop_loss=95.0, take_profit=None)
    assert exchange.protection == [{"symbol": "BTCUSD", "stop_loss": 95.0, "take_profit": None}]


# -- Delta REST: protection replace and close-all order ---------------------------------


class Recorder:
    """A Delta REST double that logs every call and can fail a chosen one."""

    def __init__(self, fail_on: str = "") -> None:
        self.calls: list[tuple[str, str, dict]] = []
        self.fail_on = fail_on
        self.next_id = 100

    def __call__(self, req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content) if req.content else {}
        self.calls.append((req.method, req.url.path, body))
        key = f"{req.method} {req.url.path} {body.get('stop_order_type', '')}".strip()
        if self.fail_on and self.fail_on == key:
            return httpx.Response(400, json={"success": False, "error": {"code": "bad_order"}})
        if req.url.path == "/v2/positions/margined":
            return self.ok([{"product_symbol": "BTCUSD", "size": 3}])
        if req.url.path == "/v2/products/BTCUSD":
            return self.ok({"id": 27, "tick_size": "0.5"})
        if req.method == "GET" and req.url.path == "/v2/orders":
            return self.ok([{"id": 7, "product_id": 27, "stop_order_type": "stop_loss_order"},
                            {"id": 8, "product_id": 27, "stop_order_type": "take_profit_order"},
                            {"id": 9, "product_id": 27, "order_type": "limit_order"}])
        if req.method == "POST" and req.url.path == "/v2/orders":
            self.next_id += 1
            return self.ok({"id": self.next_id, "state": "open"})
        return self.ok({})

    @staticmethod
    def ok(result):
        return httpx.Response(200, json={"success": True, "result": result})

    def summary(self):
        return [(m, b.get("stop_order_type") or b.get("id")) for m, path, b in self.calls if path == "/v2/orders" and m != "GET"]


def rest(handler) -> DeltaClient:
    return DeltaClient("https://x.test", "key", "secret", http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def test_new_protection_is_placed_before_the_old_is_cancelled():
    r = Recorder()
    result = await rest(r).update_position_protection("BTCUSD", stop_loss=90.2, take_profit=120.0)
    assert r.summary() == [("POST", "stop_loss_order"), ("POST", "take_profit_order"), ("DELETE", 7), ("DELETE", 8)]
    assert result["errors"] == []  # the limit order (id 9) is never touched


async def test_failed_new_leg_leaves_old_protection_in_place():
    r = Recorder(fail_on="POST /v2/orders take_profit_order")
    with pytest.raises(DeltaError, match="protection unchanged"):
        await rest(r).update_position_protection("BTCUSD", stop_loss=90.0, take_profit=120.0)
    # the new SL (101) is withdrawn; the old legs 7 and 8 are never cancelled
    assert r.summary() == [("POST", "stop_loss_order"), ("POST", "take_profit_order"), ("DELETE", 101)]


async def test_close_all_touches_nothing_when_positions_cannot_be_read():
    r = Recorder(fail_on="GET /v2/positions/margined")
    with pytest.raises(DeltaError):
        await rest(r).close_all()
    assert [c for c in r.calls if c[0] == "DELETE"] == []


async def test_close_all_flattens_before_cancelling_stops():
    r = Recorder()
    await rest(r).close_all()
    methods = [(m, p) for m, p, _ in r.calls if m != "GET"]
    assert methods == [("POST", "/v2/orders"), ("DELETE", "/v2/orders/all")]


# -- restart reconciliation ---------------------------------------------------------------


def reserve(system, cid, broker="paper", status="pending"):
    system.store.reserve_order(client_order_id=cid, broker=broker, strategy="s", symbol="BTCUSD", side="buy",
                               size=1, price=100.0, stop_loss=99.0, take_profit=None)
    system.store.update_order(cid, status)


async def test_restart_resolves_orders_left_in_flight(both, exchange):
    reserve(both, "never-sent")
    reserve(both, "made-it", broker="delta", status="unknown")
    exchange.lookup["made-it"] = {"id": 5, "state": "closed"}
    await both.executor.reconcile(before=time.time() + 1)
    await both.drain()
    assert both.store.order("never-sent")["status"] == "rejected"
    assert both.store.order("made-it")["status"] == "filled"


async def test_reconcile_leaves_orders_of_this_run_alone(paper):
    started = time.time()
    reserve(paper, "in-flight-now")
    await paper.executor.reconcile(before=started - 1)
    assert paper.store.order("in-flight-now")["status"] == "pending"


async def test_unreachable_broker_keeps_the_order_blocked_and_retries(both, exchange):
    reserve(both, "cid-x", broker="delta", status="unknown")
    exchange.lookup["cid-x"] = DeltaError("down")
    await both.executor.reconcile(before=time.time() + 1)
    for task in list(both.executor._lookups):
        await task
    assert both.store.order("cid-x")["status"] == "unknown"


# -- kill switch ------------------------------------------------------------------------------


async def test_kill_switch_reaches_a_deactivated_delta_account(both, exchange):
    exchange.open_positions.append(DELTA_BTC)
    await both.update_settings({"delta_active": False})
    result = await both.close_all()
    assert exchange.close_all_calls == 1 and "delta:BTCUSD" in result["closed"]


async def test_kill_switch_blocks_manual_orders_after(paper):
    await paper.close_all()
    with pytest.raises(ApiError, match="switched off"):
        await order(paper)


def test_strategy_order_ids_are_unchanged():
    assert client_order_id("paper", "s", "BTCUSD", BAR).startswith("tb") and len(manual_order_id("delta", "r" * 64)) == 32
