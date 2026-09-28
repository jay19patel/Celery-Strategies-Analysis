"""Events <-> JSON, for crossing process boundaries (ZeroMQ, Celery)."""

from __future__ import annotations

import json
from dataclasses import fields
from typing import Any

from tradebuddy import events
from tradebuddy.delta import Candle

EVENT_TYPES: dict[str, type[events.Event]] = {
    cls.__name__: cls
    for cls in vars(events).values()
    if isinstance(cls, type) and issubclass(cls, events.Event)
}


def encode(event: events.Event) -> bytes:
    return json.dumps(event.to_dict(), separators=(",", ":"), default=str).encode()


def decode(raw: bytes | str | dict[str, Any]) -> events.Event:
    data = json.loads(raw) if isinstance(raw, bytes | str) else dict(raw)
    cls = EVENT_TYPES.get(data.pop("type", ""))
    if cls is None:
        raise ValueError(f"unknown event type in {str(data)[:80]}")
    names = {f.name for f in fields(cls)}
    kwargs = {k: v for k, v in data.items() if k in names}
    if isinstance(kwargs.get("candle"), dict):
        kwargs["candle"] = Candle(**kwargs["candle"])
    return cls(**kwargs)
