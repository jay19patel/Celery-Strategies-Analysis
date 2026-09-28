import json
import time

import httpx
import pytest

from tradebuddy.delta import DeltaClient, DeltaError, DeltaTimeout, round_to_tick, sign


def client(handler) -> DeltaClient:
    return DeltaClient("https://x.test", "key", "secret", http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def ok(result):
    return httpx.Response(200, json={"success": True, "result": result})


async def test_signed_request_matches_delta_scheme():
    seen = {}

    def handler(req: httpx.Request):
        seen["req"] = req
        return ok([])

    await client(handler).request("GET", "/v2/positions/margined", params={"a": "1"}, auth=True)
    req = seen["req"]
    ts = req.headers["timestamp"]
    assert req.headers["api-key"] == "key"
    assert req.headers["signature"] == sign("secret", "GET" + ts + "/v2/positions/margined" + "?a=1")


async def test_auth_without_credentials_is_refused():
    c = DeltaClient("https://x.test", http=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: ok([]))))
    with pytest.raises(DeltaError):
        await c.positions()


async def test_forming_candle_is_dropped():
    now = int(time.time())
    last_open = now - now % 60
    rows = [{"time": last_open - 60 * i, "open": 1, "high": 1, "low": 1, "close": i, "volume": 1} for i in range(5)]
    candles = await client(lambda r: ok(rows)).candles("BTCUSD", "1m", 10)
    assert [c.time for c in candles] == sorted(c.time for c in candles)
    assert all(c.time + 60 <= now for c in candles)
    assert last_open not in [c.time for c in candles]


@pytest.mark.parametrize("status", [500, 502, 503, 504])
async def test_server_error_is_ambiguous(status):
    with pytest.raises(DeltaTimeout):
        await client(lambda r: httpx.Response(status)).request("GET", "/v2/x")


async def test_network_error_is_ambiguous():
    def handler(req):
        raise httpx.ReadTimeout("slow", request=req)

    with pytest.raises(DeltaTimeout):
        await client(handler).request("GET", "/v2/x")


async def test_rejection_is_not_a_timeout():
    resp = httpx.Response(400, json={"success": False, "error": {"code": "insufficient_margin"}})
    with pytest.raises(DeltaError) as info:
        await client(lambda r: resp).request("GET", "/v2/x")
    assert not isinstance(info.value, DeltaTimeout)


async def test_order_carries_client_id_and_bracket():
    bodies = []

    def handler(req: httpx.Request):
        if req.url.path == "/v2/products/BTCUSD":
            return ok({"id": 27, "tick_size": "0.5"})
        bodies.append(json.loads(req.content))
        return ok({"id": 1, "state": "closed"})

    await client(handler).place_order("BTCUSD", "buy", 1, "tbabc", stop_loss=99.26, take_profit=101.74)
    body = bodies[0]
    assert body["product_id"] == 27 and body["client_order_id"] == "tbabc" and body["order_type"] == "market_order"
    assert body["bracket_stop_loss_price"] == "99.5" and body["bracket_take_profit_price"] == "101.5"


async def test_order_lookup_not_found_is_none():
    resp = httpx.Response(404, json={"success": False, "error": {"code": "not_found"}})
    assert await client(lambda r: resp).order_by_client_id("tb1") is None


async def test_option_chain_parses_symbols():
    rows = [
        {"symbol": "C-BTC-100000-290926", "oi": "5"},
        {"symbol": "P-BTC-90000-290926", "oi": "7"},
        {"symbol": "BTCUSD", "oi": "1"},
    ]
    chain = await client(lambda r: ok(rows)).option_chain("BTC")
    assert [(q.kind, q.strike, q.oi) for q in chain] == [("call", 100000, 5), ("put", 90000, 7)]
    assert chain[0].expiry.isoformat() == "2026-09-29"


async def test_calls_are_counted():
    c = client(lambda r: ok([]))
    await c.request("GET", "/v2/x")
    assert c.calls["GET /v2/x"]["count"] == 1 and c.calls["GET /v2/x"]["errors"] == 0


def test_round_to_tick():
    assert round_to_tick(65000.26, 0.5) == "65000.5"
    assert round_to_tick(0.123456, 0.0001) == "0.1235"
