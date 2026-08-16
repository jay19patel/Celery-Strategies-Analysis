"""MongoDB persistence for the Portfolio paper-trading portfolio.

Two collections (created lazily on first write, matching this repo's existing
lazy-connect pattern in mongodb.py):
  portfolio_state  - ONE document (_id="portfolio") holding current
                              balance, peak_equity, throttled flag, open
                              positions, pending entries, and the timestamp of
                              the last candle already processed.
  portfolio_trades - one document per CLOSED trade, append-only -
                              the full history used to actually place real
                              trades off of, so every field here (entry/exit
                              price, stop, target, size, leverage) matches
                              exactly what PortfolioManager computed.
"""

from datetime import datetime, timezone

import numpy as np
import pandas as pd

from app.database.mongodb import get_collection
from app.core.logger import get_mongodb_logger

logger = get_mongodb_logger()

STATE_DOC_ID = "portfolio"


def _to_bson_native(value):
    """PortfolioManager computes everything from numpy arrays, so directions,
    prices, and equity are numpy scalars (np.int64/np.float64) - pymongo's BSON
    encoder rejects those outright. Recursively unwrap them to native int/float/bool
    right before the Mongo write rather than trusting every upstream caller to do it."""
    if isinstance(value, dict):
        return {k: _to_bson_native(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_bson_native(v) for v in value]
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    return value


def _serialize_position(p):
    p = dict(p)
    p["entry_time"] = p["entry_time"].isoformat() if hasattr(p["entry_time"], "isoformat") else str(p["entry_time"])
    return p


def _deserialize_position(p):
    p = dict(p)
    p["entry_time"] = pd.Timestamp(p["entry_time"])
    return p


def load_state():
    """Returns {} if this is the very first run ever."""
    doc = get_collection("portfolio_state").find_one({"_id": STATE_DOC_ID})
    if not doc:
        return {}
    doc.pop("_id", None)
    return doc


def save_state(state: dict):
    """state must already have JSON/BSON-safe values (see engine's serialize
    helpers) except open_positions, which this function serializes itself."""
    payload = dict(state)
    payload["open_positions"] = [_serialize_position(p) for p in state.get("open_positions", [])]
    payload = _to_bson_native(payload)
    payload["updated_at"] = datetime.now(timezone.utc)
    get_collection("portfolio_state").replace_one({"_id": STATE_DOC_ID}, {"_id": STATE_DOC_ID, **payload}, upsert=True)
    logger.info(f"💾 Portfolio portfolio state saved | balance={payload.get('balance')}")


def deserialize_open_positions(raw_positions):
    return [_deserialize_position(p) for p in raw_positions]


def append_trades(trades: list):
    if not trades:
        return
    collection = get_collection("portfolio_trades")
    docs = []
    for t in trades:
        doc = dict(t)
        doc["entry_time"] = doc["entry_time"].isoformat() if hasattr(doc["entry_time"], "isoformat") else str(doc["entry_time"])
        doc["exit_time"] = doc["exit_time"].isoformat() if hasattr(doc["exit_time"], "isoformat") else str(doc["exit_time"])
        doc = _to_bson_native(doc)
        doc["recorded_at"] = datetime.now(timezone.utc)
        docs.append(doc)
    collection.insert_many(docs)
    logger.info(f"💾 {len(docs)} new Portfolio trade(s) saved to MongoDB")


def get_trades(limit: int = 200, skip: int = 0):
    collection = get_collection("portfolio_trades")
    cursor = collection.find({}, {"_id": 0}).sort("exit_time", -1).skip(skip).limit(limit)
    return list(cursor)


def count_trades():
    return get_collection("portfolio_trades").count_documents({})
