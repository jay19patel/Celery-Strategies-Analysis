"""The engine's schedule: every guard is on it, a run never overlaps itself, one engine per database."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.core.scheduler import Scheduler


async def test_the_engine_schedules_every_guard(tmp_path: Path) -> None:
    """The daily loss limit is a job like any other: dropped from the schedule, it silently stops
    halting anything and the day's starting equity is never recorded."""
    from app.config import Config
    from app.engine import Engine

    engine = Engine(Config(db_path=str(tmp_path / "test.db"), engine_enabled=False))
    engine._schedule()
    try:
        assert {"guard.structures", "guard.daily", "auto.cycle", "strategies.run", "reconcile", "housekeeping"} <= set(engine.scheduler.jobs)
    finally:
        engine.scheduler.shutdown()


async def test_a_manual_run_never_runs_beside_the_scheduled_one() -> None:
    """A "Run now" click while the auto-trader is mid-cycle would be a second cycle on the same
    decision: it waits for the one in progress instead."""
    runs, gate = [], asyncio.Event()

    async def job() -> None:
        runs.append("start")
        await gate.wait()

    sch = Scheduler()
    sch.every("slow", "slow job", 3600, job)
    sch.start()
    try:
        first = asyncio.create_task(sch.run_now("slow"))
        await asyncio.sleep(0)
        second = asyncio.create_task(sch.run_now("slow"))
        await asyncio.sleep(0)
        gate.set()
        await asyncio.gather(first, second)
        assert runs == ["start"]
        assert sch.jobs["slow"].runs == 1
    finally:
        sch.shutdown()


async def test_a_second_engine_on_the_same_database_refuses_to_start(tmp_path: Path) -> None:
    from app.config import Config
    from app.engine import AlreadyRunning, Engine

    cfg = Config(db_path=str(tmp_path / "test.db"), engine_enabled=False)
    first, second = Engine(cfg), Engine(cfg)
    first._take_lock()
    try:
        with pytest.raises(AlreadyRunning, match="single worker"):
            second._take_lock()
    finally:
        await first.stop()
    second._take_lock()  # the first one's stop released it
    await second.stop()
