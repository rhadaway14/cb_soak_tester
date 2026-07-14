"""Concurrency engine: ramp workers up, run a weighted op mix to a deadline,
optionally rate-limit, and record latency/outcome for every operation.
"""
from __future__ import annotations

import asyncio
import random
import time
from typing import Callable, Dict, List, Optional

from .client import CouchbaseClient
from .config import Config
from .metrics import INFLIGHT, WORKERS, Stats


class RateLimiter:
    """Simple monotonic-clock token pacer for a global ops/sec cap."""

    def __init__(self, ops_per_sec: float) -> None:
        self._interval = 1.0 / ops_per_sec
        self._next = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait = self._next - now
            if wait > 0:
                await asyncio.sleep(wait)
                self._next += self._interval
            else:
                # Fell behind; reset the schedule to now to avoid a burst.
                self._next = now + self._interval


class Runner:
    def __init__(self, cfg: Config, client: CouchbaseClient, stats: Stats) -> None:
        self.cfg = cfg
        self.client = client
        self.stats = stats
        self._stop = asyncio.Event()
        self._started_at = 0.0
        self._deadline = 0.0

        w = cfg.workload
        self._ops: Dict[str, Callable] = {
            "kv_get": client.kv_get,
            "kv_upsert": client.kv_upsert,
            "query": client.query,
        }
        # Build a weighted selection list from the configured mix.
        self._choices: List[str] = [k for k in w.mix if k in self._ops]
        self._weights: List[int] = [w.mix[k] for k in self._choices]
        if not self._choices:
            raise ValueError("workload.mix has no valid operations")

        self._limiter: Optional[RateLimiter] = (
            RateLimiter(w.target_ops_per_sec) if w.target_ops_per_sec else None
        )

    def request_stop(self) -> None:
        self._stop.set()

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self._started_at if self._started_at else 0.0

    async def _worker(self, worker_id: int) -> None:
        # Ramp: hold each worker until its slice of the ramp window elapses.
        ramp = self.cfg.workload.ramp_up_s
        concurrency = self.cfg.workload.concurrency
        if ramp > 0 and concurrency > 1:
            delay = ramp * (worker_id / concurrency)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
                return  # stop requested during ramp
            except asyncio.TimeoutError:
                pass

        WORKERS.inc()
        try:
            while not self._stop.is_set() and time.monotonic() < self._deadline:
                if self._limiter is not None:
                    await self._limiter.acquire()
                op = random.choices(self._choices, weights=self._weights, k=1)[0]
                await self._run_one(op)
        finally:
            WORKERS.dec()

    async def _run_one(self, op: str) -> None:
        fn = self._ops[op]
        INFLIGHT.inc()
        start = time.perf_counter()
        ok = True
        try:
            await fn()
        except Exception:  # noqa: BLE001 - any failure is a recorded error
            ok = False
        finally:
            latency = time.perf_counter() - start
            INFLIGHT.dec()
            self.stats.record(op, latency, ok)

    async def run(self) -> None:
        self._started_at = time.monotonic()
        self._deadline = self._started_at + self.cfg.workload.duration_s
        workers = [
            asyncio.create_task(self._worker(i))
            for i in range(self.cfg.workload.concurrency)
        ]
        # Wake up when the deadline passes even if no worker sets the event.
        async def _deadline_watch() -> None:
            remaining = self._deadline - time.monotonic()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=max(0.0, remaining))
            except asyncio.TimeoutError:
                pass
            self._stop.set()

        watch = asyncio.create_task(_deadline_watch())
        await asyncio.gather(*workers, return_exceptions=True)
        watch.cancel()
