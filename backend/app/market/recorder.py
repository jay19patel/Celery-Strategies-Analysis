"""The option chain, recorded for backtesting.

Delta keeps no history of its option quotes, and an options desk cannot be tested without one. Every
few minutes each live quote — bid, ask, sizes, mark IV and greeks — is appended as one JSON line to a
gzipped file per underlying and UTC day (`<dir>/BTC/2026-11-01.jsonl.gz`). Appending opens a new
gzip member each time, which `gzip.open` reads back as one stream. Files older than the retention are
deleted, so the store stays bounded.
"""

from __future__ import annotations

import gzip
import json
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from app.market.options import OptionQuote

RETENTION_DAYS = 90


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%d")


def record(directory: Path, underlying: str, quotes: list[OptionQuote], spot: float | None, now: float | None = None) -> int:
    """Append one snapshot of `quotes`. Returns the rows written."""
    if not quotes:
        return 0
    now = time.time() if now is None else now
    path = directory / underlying / f"{_day(now)}.jsonl.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({"ts": round(now, 3), "underlying_spot": spot} | asdict(q), separators=(",", ":")) for q in quotes]
    with gzip.open(path, "at", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return len(lines)


def read(path: Path) -> list[dict]:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def prune(directory: Path, days: int = RETENTION_DAYS, now: float | None = None) -> int:
    """Delete day files older than `days`. Returns how many were deleted."""
    if not directory.exists():
        return 0
    cutoff = _day((time.time() if now is None else now) - days * 86_400)
    gone = 0
    for path in directory.glob("*/*.jsonl.gz"):
        if path.name.removesuffix(".jsonl.gz") < cutoff:
            path.unlink()
            gone += 1
    return gone
