"""Background jobs: what each process runs on a timer, and how each run went.

A loop wraps one pass in `job.tick()`; the registry keeps runs, errors, timings and the next due
time, and every process sends its registry with its heartbeat so the System page shows them all.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Job:
    name: str
    what: str  # one line, for the dashboard
    every: float | None = None  # seconds between runs; None = runs once or reacts to events
    process: str = ""
    state: str = "waiting"  # waiting | running | idle | failed
    runs: int = 0
    errors: int = 0
    last_at: float = 0.0
    last_ms: float = 0.0
    max_ms: float = 0.0
    next_at: float | None = None
    last_error: str = ""
    last_error_at: float = 0.0
    note: str = ""  # what the last run did, when the job says (e.g. "pruned 120 rows")
    _started: float = field(default=0.0, repr=False)

    @contextmanager
    def tick(self) -> Iterator[Job]:
        """One pass. An exception is recorded and re-raised; the loop decides whether to go on."""
        self.state, self.last_at, self._started = "running", time.time(), time.perf_counter()
        try:
            yield self
        except Exception as exc:
            self.errors += 1
            self.last_error, self.last_error_at = f"{type(exc).__name__}: {exc}"[:300], time.time()
            self.state = "failed"
            raise
        else:
            self.state = "idle"
        finally:
            self.runs += 1
            self.last_ms = round(1000 * (time.perf_counter() - self._started), 1)
            self.max_ms = max(self.max_ms, self.last_ms)
            self.next_at = time.time() + self.every if self.every else None

    def snapshot(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("_started")
        return data


class Jobs:
    def __init__(self, process: str) -> None:
        self.process = process
        self._jobs: dict[str, Job] = {}

    def add(self, name: str, what: str, every: float | None = None) -> Job:
        job = self._jobs.setdefault(name, Job(name=name, what=what, every=every, process=self.process))
        job.what, job.every = what, every
        return job

    def snapshot(self) -> list[dict[str, Any]]:
        return [j.snapshot() for j in self._jobs.values()]


NULL_JOB = Job(name="-", what="untracked")  # the default, so a loop never has to check for None
