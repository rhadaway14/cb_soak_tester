"""Prometheus metrics and in-process latency statistics.

Two layers:
  * Prometheus counters/histograms exposed on /metrics for Grafana.
  * A lightweight in-process ``Stats`` accumulator (reservoir-sampled
    latencies) used for the end-of-run CLI table and the JSON summary.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Dict, List

from prometheus_client import Counter, Gauge, Histogram, start_http_server

# Latency buckets (seconds) spanning sub-millisecond KV reads to slow queries.
_LATENCY_BUCKETS = (
    0.0005, 0.001, 0.0025, 0.005, 0.01, 0.025, 0.05,
    0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0,
)

OPS = Counter(
    "soak_ops_total",
    "Total operations attempted, by operation and outcome.",
    ["operation", "status"],
)
LATENCY = Histogram(
    "soak_op_latency_seconds",
    "Operation latency in seconds, by operation.",
    ["operation"],
    buckets=_LATENCY_BUCKETS,
)
INFLIGHT = Gauge(
    "soak_inflight_operations",
    "Operations currently in flight.",
)
WORKERS = Gauge(
    "soak_active_workers",
    "Number of active worker coroutines.",
)


def start_metrics_server(host: str, port: int) -> None:
    start_http_server(port, addr=host)


@dataclass
class OpStats:
    """Exact counts + reservoir-sampled latencies for one operation type."""

    reservoir_size: int = 50000
    count: int = 0
    errors: int = 0
    total_latency: float = 0.0
    min_latency: float = math.inf
    max_latency: float = 0.0
    _reservoir: List[float] = field(default_factory=list)
    _seen: int = 0

    def record(self, latency_s: float, ok: bool) -> None:
        self.count += 1
        if not ok:
            self.errors += 1
        self.total_latency += latency_s
        if latency_s < self.min_latency:
            self.min_latency = latency_s
        if latency_s > self.max_latency:
            self.max_latency = latency_s
        # Reservoir sampling keeps memory bounded over a long soak.
        self._seen += 1
        if len(self._reservoir) < self.reservoir_size:
            self._reservoir.append(latency_s)
        else:
            j = random.randint(0, self._seen - 1)
            if j < self.reservoir_size:
                self._reservoir[j] = latency_s

    @property
    def mean_latency(self) -> float:
        return self.total_latency / self.count if self.count else 0.0

    def percentile(self, p: float) -> float:
        """Approximate percentile (p in [0, 100]) from the reservoir sample."""
        if not self._reservoir:
            return 0.0
        ordered = sorted(self._reservoir)
        k = max(0, min(len(ordered) - 1, int(round((p / 100.0) * (len(ordered) - 1)))))
        return ordered[k]


class Stats:
    """Aggregate stats across all operation types for one run."""

    def __init__(self, reservoir_size: int = 50000) -> None:
        self._reservoir_size = reservoir_size
        self.ops: Dict[str, OpStats] = {}

    def record(self, operation: str, latency_s: float, ok: bool) -> None:
        op = self.ops.get(operation)
        if op is None:
            op = OpStats(reservoir_size=self._reservoir_size)
            self.ops[operation] = op
        op.record(latency_s, ok)
        # Mirror into Prometheus.
        OPS.labels(operation=operation, status="ok" if ok else "error").inc()
        LATENCY.labels(operation=operation).observe(latency_s)

    @property
    def total_count(self) -> int:
        return sum(o.count for o in self.ops.values())

    @property
    def total_errors(self) -> int:
        return sum(o.errors for o in self.ops.values())
