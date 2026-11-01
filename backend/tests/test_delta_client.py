"""What the Delta client sends for the orders that protect a position. No network: a mock transport."""

from __future__ import annotations

import json
from typing import Any

import httpx

from app.brokers.delta import DeltaBroker
from app.market.delta import DeltaClient


def client(seen: list[httpx.Request], result: Any) -> DeltaClient:
    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.startswith("/v2/products/"):
            return httpx.Response(200, json={"success": True, "result": {"id": 27, "tick_size": "0.5"}})
        return httpx.Response(200, json={"success": True, "result": result})

    return DeltaClient("https://stub", "key", "secret", http=httpx.AsyncClient(transport=httpx.MockTransport(handle)))


async def test_a_stop_is_a_reduce_only_stop_market_on_the_mark() -> None:
    seen: list[httpx.Request] = []
    broker = DeltaBroker(client(seen, {"id": 991}))
    assert await broker.protect("BTCUSD", "sell", 5, 79_500.3, "sabc0k") == "991"
    order = seen[-1]
    assert order.method == "POST" and order.url.path == "/v2/orders" and order.headers["signature"]
    assert json.loads(order.content) == {
        "product_id": 27, "size": 5, "side": "sell", "order_type": "market_order",
        "stop_order_type": "stop_loss_order", "stop_price": "79500.5", "stop_trigger_method": "mark_price",
        "client_order_id": "sabc0k", "reduce_only": "true",
    }


async def test_a_stop_is_cancelled_by_its_exchange_id() -> None:
    seen: list[httpx.Request] = []
    await DeltaBroker(client(seen, {})).unprotect("BTCUSD", "991")
    cancel = seen[-1]
    assert cancel.method == "DELETE" and cancel.url.path == "/v2/orders"
    assert json.loads(cancel.content) == {"id": 991, "product_id": 27}
