"""The chain recorder: snapshots append to one file per day, read back whole, and age out."""

from __future__ import annotations

import time
from pathlib import Path

from app.market import recorder
from tests.conftest import load_quotes


def test_snapshots_append_to_the_days_file_and_read_back(tmp_path: Path) -> None:
    quotes = load_quotes()
    now = time.time() // 86_400 * 86_400 + 3600  # an hour into the UTC day: both snapshots land in one file
    assert recorder.record(tmp_path, "BTC", quotes, 80_000.0, now) == len(quotes)
    assert recorder.record(tmp_path, "BTC", quotes, 80_100.0, now + 300) == len(quotes)
    [path] = list((tmp_path / "BTC").glob("*.jsonl.gz"))
    rows = recorder.read(path)
    assert len(rows) == 2 * len(quotes)
    first = rows[0]
    assert first["symbol"] == quotes[0].symbol and first["bid"] == quotes[0].bid and first["underlying_spot"] == 80_000.0
    assert {"iv", "delta", "ask", "oi", "expiry"} <= set(first)


def test_nothing_is_written_without_quotes(tmp_path: Path) -> None:
    assert recorder.record(tmp_path, "BTC", [], None) == 0
    assert not (tmp_path / "BTC").exists()


def test_old_days_are_pruned(tmp_path: Path) -> None:
    quotes = load_quotes()[:3]
    now = time.time()
    recorder.record(tmp_path, "BTC", quotes, 1.0, now - 100 * 86_400)
    recorder.record(tmp_path, "BTC", quotes, 1.0, now)
    assert recorder.prune(tmp_path, 90, now) == 1
    assert len(list((tmp_path / "BTC").glob("*.jsonl.gz"))) == 1
