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
from datetime import UTC, datetime
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.base import BaseTrigger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

log = logging.getLogger(__name__)

Job = Callable[[], Awaitable[Any]]
WindowGetter = Callable[[float], tuple[float, float, str]]


class EventWindowTrigger(BaseTrigger):
    """An event-window trigger that fires at high frequency inside a defined window
    (e.g., expiry countdown or funding intervals) and sets a precise wakeup timer when outside.

    Used for Zero-Hero options momentum, pre-settlement rolls, funding rate harvesting, etc.
    """

    def __init__(
        self,
        get_window: WindowGetter,
        active_interval: float = 60.0,
        idle_heartbeat: float = 1800.0,
    ) -> None:
        self.get_window = get_window
        self.active_interval = max(5.0, active_interval)
        self.idle_heartbeat = max(60.0, idle_heartbeat)

    def get_next_fire_time(self, previous_fire_time: datetime | None, now: datetime) -> datetime:
        now_ts = now.timestamp()
        w_start, w_end, _ = self.get_window(now_ts)

        # 1. Inside active window: fire every active_interval seconds
        if w_start <= now_ts < w_end:
            next_ts = now_ts + self.active_interval
            if next_ts < w_end:
                return datetime.fromtimestamp(next_ts, tz=now.tzinfo)
            # Final firing at the window boundary
            return datetime.fromtimestamp(w_end, tz=now.tzinfo)

        # 2. Before active window: fire when window opens (or at heartbeat if far away)
        if now_ts < w_start:
            target_ts = min(w_start, now_ts + self.idle_heartbeat)
            return datetime.fromtimestamp(target_ts, tz=now.tzinfo)

        # 3. After window: query the next future cycle
        next_start, _, _ = self.get_window(w_end + 1.0)
        target_ts = min(next_start, now_ts + self.idle_heartbeat)
        return datetime.fromtimestamp(target_ts, tz=now.tzinfo)


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

    def windowed(
        self,
        job_id: str,
        label: str,
        get_window: WindowGetter,
        active_interval: float,
        func: Job,
        idle_heartbeat: float = 1800.0,
        run_now: bool = False,
    ) -> None:
        """Schedule a job that activates during a market window and sleeps otherwise."""
        trigger = EventWindowTrigger(get_window, active_interval, idle_heartbeat)
        _, _, desc = get_window(time.time())
        schedule_label = f"windowed ({desc}, active: {_human(active_interval)})"
        self._put(job_id, label, schedule_label, trigger, func, run_now)

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
