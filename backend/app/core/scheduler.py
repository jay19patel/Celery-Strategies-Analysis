"""Every timed job in the engine, on one APScheduler running inside the engine's event loop.

In-process on purpose: the jobs read the live option book and write trading state, and the engine
is the only writer. A job never overlaps itself (`max_instances=1`), a run missed while the loop was
busy is run once rather than replayed (`coalesce`), and every run is recorded so the System page
can show when each job last ran, how long it took and why it last failed.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

log = logging.getLogger(__name__)

Job = Callable[[], Awaitable[Any]]


@dataclass
class JobStatus:
    id: str
    label: str
    schedule: str
    runs: int = 0
    failures: int = 0
    last_run: float | None = None
    last_ms: float | None = None
    last_error: str = ""
    last_result: str = ""


class Scheduler:
    def __init__(self) -> None:
        self._aps = AsyncIOScheduler(job_defaults={"max_instances": 1, "coalesce": True, "misfire_grace_time": 30})
        self.jobs: dict[str, JobStatus] = {}

    def start(self) -> None:
        self._aps.start()

    def shutdown(self) -> None:
        if self._aps.running:
            self._aps.shutdown(wait=False)

    def every(self, job_id: str, label: str, seconds: float, func: Job, run_now: bool = False) -> None:
        trigger = IntervalTrigger(seconds=seconds)
        self._put(job_id, label, f"every {_human(seconds)}", trigger, func, run_now)

    def daily(self, job_id: str, label: str, hour: int, minute: int, timezone: str, func: Job) -> None:
        trigger = CronTrigger(hour=hour, minute=minute, timezone=timezone)
        self._put(job_id, label, f"daily {hour:02d}:{minute:02d} {timezone}", trigger, func, False)

    def remove(self, job_id: str) -> None:
        if self._aps.get_job(job_id):
            self._aps.remove_job(job_id)
        self.jobs.pop(job_id, None)

    async def run_now(self, job_id: str) -> None:
        job = self._aps.get_job(job_id)
        if job is None:
            raise KeyError(job_id)
        await job.func()

    def _put(self, job_id: str, label: str, schedule: str, trigger: Any, func: Job, run_now: bool) -> None:
        status = self.jobs.get(job_id) or JobStatus(id=job_id, label=label, schedule=schedule)
        status.label, status.schedule = label, schedule
        self.jobs[job_id] = status

        async def tracked() -> None:
            started = time.perf_counter()
            status.last_run = time.time()
            status.runs += 1
            try:
                result = await func()
                status.last_error = ""
                status.last_result = "" if result is None else str(result)[:200]
            except Exception as exc:
                status.failures += 1
                status.last_error = f"{type(exc).__name__}: {exc}"[:300]
                log.exception("job_failed id=%s", job_id)
            finally:
                status.last_ms = round(1000 * (time.perf_counter() - started), 1)

        extra = {"next_run_time": _now()} if run_now else {}
        self._aps.add_job(tracked, trigger, id=job_id, name=label, replace_existing=True, **extra)

    def status(self) -> list[dict[str, Any]]:
        out = []
        for job_id, status in self.jobs.items():
            job = self._aps.get_job(job_id)
            nxt = job.next_run_time.timestamp() if job and job.next_run_time else None
            out.append(asdict(status) | {"next_run": nxt})
        return out


def _now() -> Any:
    from datetime import UTC, datetime

    return datetime.now(UTC)


def _human(seconds: float) -> str:
    if seconds % 3600 == 0:
        return f"{int(seconds // 3600)}h"
    if seconds % 60 == 0:
        return f"{int(seconds // 60)}m"
    return f"{seconds:g}s"
