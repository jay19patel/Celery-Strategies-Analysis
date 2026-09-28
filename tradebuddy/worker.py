"""Celery: strategy evaluation on worker processes.

    celery -A tradebuddy.worker worker -Q strategies --concurrency 4

A task evaluates one strategy on one closed bar and pushes the result to the
engine over ZeroMQ. Workers never trade: they only produce StrategyEvaluated
events, and the engine decides. A task delivered twice yields the same
client_order_id, so the engine sends at most one order.
"""

from __future__ import annotations

import asyncio
import os
import socket
import threading
import time
from typing import Any

from celery import Celery

from tradebuddy.config import load_config
from tradebuddy.delta import RESOLUTION_SECONDS, DeltaClient
from tradebuddy.events import StrategyEvaluated
from tradebuddy.runner import MarketData, evaluate
from tradebuddy.settings import ENVIRONMENTS
from tradebuddy.strategies import Strategy, discover
from tradebuddy.transport import ResultPusher

TASK = "tradebuddy.evaluate"
QUEUE = "strategies"

cfg = load_config()
app = Celery("tradebuddy", broker=cfg.celery_broker_url)
app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    task_ignore_result=True,  # results travel over ZeroMQ, not a result backend
    task_acks_late=True,  # a worker that dies mid-task hands it to another
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_default_queue=QUEUE,
    task_soft_time_limit=30,
    task_time_limit=45,
    broker_connection_retry_on_startup=True,
    worker_hijack_root_logger=False,
)

_strategies: dict[str, Strategy] | None = None
_pusher = ResultPusher(cfg.zmq_results_url)


def strategies() -> dict[str, Strategy]:
    global _strategies
    if _strategies is None:
        _strategies = {s.name: s for s in discover()}
    return _strategies


class SnapshotPrices:
    """Workers have no live feed; they see the prices the engine had when it dispatched the job."""

    def __init__(self, prices: dict[str, float]) -> None:
        self._prices = prices

    def price(self, symbol: str) -> float | None:
        return self._prices.get(symbol)


def worker_name() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


async def run_job(job: dict[str, Any], client_factory=DeltaClient, registry: dict[str, Strategy] | None = None) -> StrategyEvaluated:
    strategy = (strategies() if registry is None else registry).get(job["strategy"])
    if strategy is None:
        return StrategyEvaluated(
            strategy=job["strategy"], version=job.get("version", 0), symbol=job["symbol"], resolution=job["resolution"],
            bar_time=job["bar_time"], error="strategy not found on this worker — redeploy workers", worker=worker_name(),
            dispatched_at=job.get("dispatched_at", 0.0),
        )
    client = client_factory(ENVIRONMENTS[job["data_env"]][0])
    try:
        return await evaluate(strategy, job, MarketData(client, SnapshotPrices(job.get("prices") or {})), worker=worker_name())
    finally:
        await client.aclose()


@app.task(name=TASK)
def evaluate_task(job: dict[str, Any]) -> None:
    _pusher.send(asyncio.run(run_job(job)))


class CeleryEvaluator:
    """Engine side: dispatches jobs to Celery. Results come back through the engine's ZeroMQ PULL socket."""

    name = "celery"

    def __init__(self, celery_app: Celery = app) -> None:
        self.app = celery_app
        self.submitted = 0
        self.failed_to_submit = 0
        self.last_error = ""
        self.workers: list[str] = []
        self.workers_checked_at = 0.0
        self._pinging = False

    async def submit(self, strategy: Strategy, job: dict[str, Any]) -> None:
        # A job that waits longer than one bar would trade on an old market: let the broker drop it.
        expires = RESOLUTION_SECONDS.get(job["resolution"], 60)
        try:
            await asyncio.to_thread(self.app.send_task, TASK, args=[job], queue=QUEUE, expires=expires)
            self.submitted += 1
        except Exception as exc:
            self.failed_to_submit += 1
            self.last_error = f"could not reach the Celery broker: {exc}"
            raise

    def stats(self) -> dict[str, Any]:
        if time.time() - self.workers_checked_at > 10 and not self._pinging:
            self._pinging = True
            threading.Thread(target=self._ping, daemon=True).start()
        return {
            "mode": self.name,
            "broker": self.app.conf.broker_url,
            "submitted": self.submitted,
            "failed_to_submit": self.failed_to_submit,
            "last_error": self.last_error,
            "workers": self.workers,
            "workers_checked_at": self.workers_checked_at,
        }

    def _ping(self) -> None:
        try:
            replies = self.app.control.ping(timeout=1.0) or []
            self.workers = sorted(name for reply in replies for name in reply)
        except Exception as exc:
            self.last_error = f"worker ping failed: {exc}"
            self.workers = []
        finally:
            self.workers_checked_at = time.time()
            self._pinging = False
