import asyncio
from tradebuddy.delta import DeltaClient
from tradebuddy.store import Store

async def main():
    store = Store("/app/data/tradebuddy.db")
    settings = store.load_settings()
    client = DeltaClient("https://api.testnet.deltaex.org", settings["delta_api_key"], settings["delta_api_secret"])
    
    positions = await client.positions()
    if not positions:
        print("No positions")
        return
    p = positions[0]
    symbol = p.get("product_symbol") or (p.get("product") or {}).get("symbol")
    product = await client.product(symbol)
    
    # payload 1: no margin_order_id
    body1 = {
        "product_id": product["id"],
        "bracket_stop_loss_price": str(float(p.get("entry_price")) * 0.9),
        "bracket_take_profit_price": str(float(p.get("entry_price")) * 1.1),
        "bracket_stop_trigger_method": "mark_price"
    }
    
    print("Testing POST /v2/orders/bracket without margin_order_id")
    try:
        res = await client.request("POST", "/v2/orders/bracket", body=body1, auth=True)
        print("Success 1:", res)
    except Exception as e:
        print("Error POST 1:", e)

    # payload 2: with margin_order_id
    body2 = body1.copy()
    body2["margin_order_id"] = p.get("id")
    print("Testing POST /v2/orders/bracket WITH margin_order_id", p.get("id"))
    try:
        res = await client.request("POST", "/v2/orders/bracket", body=body2, auth=True)
        print("Success 2:", res)
    except Exception as e:
        print("Error POST 2:", e)

asyncio.run(main())
