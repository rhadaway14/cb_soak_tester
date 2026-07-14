"""End-of-run reporting: a Rich CLI table plus an optional JSON summary."""
from __future__ import annotations

import json
import platform
from datetime import datetime, timezone
from typing import Optional

from rich.console import Console
from rich.table import Table

from .config import Config
from .metrics import Stats


def _fmt_ms(seconds: float) -> str:
    return f"{seconds * 1000:.2f}"


def build_summary(cfg: Config, stats: Stats, elapsed_s: float) -> dict:
    ops = {}
    for name, o in sorted(stats.ops.items()):
        ops[name] = {
            "count": o.count,
            "errors": o.errors,
            "error_rate": (o.errors / o.count) if o.count else 0.0,
            "throughput_ops_s": (o.count / elapsed_s) if elapsed_s else 0.0,
            "latency_ms": {
                "min": o.min_latency * 1000 if o.count else 0.0,
                "mean": o.mean_latency * 1000,
                "p50": o.percentile(50) * 1000,
                "p95": o.percentile(95) * 1000,
                "p99": o.percentile(99) * 1000,
                "max": o.max_latency * 1000,
            },
        }
    total = stats.total_count
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "host": platform.node(),
        "duration_s": elapsed_s,
        "concurrency": cfg.workload.concurrency,
        "target_ops_per_sec": cfg.workload.target_ops_per_sec,
        "totals": {
            "operations": total,
            "errors": stats.total_errors,
            "error_rate": (stats.total_errors / total) if total else 0.0,
            "throughput_ops_s": (total / elapsed_s) if elapsed_s else 0.0,
        },
        "operations": ops,
    }


def print_report(cfg: Config, stats: Stats, elapsed_s: float, console: Optional[Console] = None) -> dict:
    console = console or Console()
    summary = build_summary(cfg, stats, elapsed_s)

    table = Table(title="Couchbase Soak Test — Results", header_style="bold cyan")
    table.add_column("Operation", style="bold")
    table.add_column("Count", justify="right")
    table.add_column("Errors", justify="right")
    table.add_column("Err %", justify="right")
    table.add_column("Ops/s", justify="right")
    table.add_column("mean ms", justify="right")
    table.add_column("p50 ms", justify="right")
    table.add_column("p95 ms", justify="right")
    table.add_column("p99 ms", justify="right")
    table.add_column("max ms", justify="right")

    for name, o in sorted(stats.ops.items()):
        err_pct = (o.errors / o.count * 100) if o.count else 0.0
        err_style = "red" if err_pct > 0 else "green"
        table.add_row(
            name,
            f"{o.count:,}",
            f"{o.errors:,}",
            f"[{err_style}]{err_pct:.2f}[/{err_style}]",
            f"{o.count / elapsed_s:,.1f}" if elapsed_s else "0",
            _fmt_ms(o.mean_latency),
            _fmt_ms(o.percentile(50)),
            _fmt_ms(o.percentile(95)),
            _fmt_ms(o.percentile(99)),
            _fmt_ms(o.max_latency),
        )

    t = summary["totals"]
    table.add_section()
    table.add_row(
        "[bold]TOTAL[/bold]",
        f"[bold]{t['operations']:,}[/bold]",
        f"[bold]{t['errors']:,}[/bold]",
        f"[bold]{t['error_rate'] * 100:.2f}[/bold]",
        f"[bold]{t['throughput_ops_s']:,.1f}[/bold]",
        "", "", "", "", "",
    )
    console.print()
    console.print(table)
    console.print(
        f"Ran [bold]{elapsed_s:,.0f}s[/bold] at concurrency "
        f"[bold]{cfg.workload.concurrency}[/bold] — "
        f"[bold]{t['throughput_ops_s']:,.0f}[/bold] ops/s aggregate, "
        f"[bold]{t['error_rate'] * 100:.3f}%[/bold] errors."
    )

    if cfg.report.json_out:
        with open(cfg.report.json_out, "w", encoding="utf-8") as fh:
            json.dump(summary, fh, indent=2)
        console.print(f"JSON summary written to [bold]{cfg.report.json_out}[/bold]")

    return summary
