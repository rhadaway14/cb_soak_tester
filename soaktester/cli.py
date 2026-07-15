"""Command-line entry point: ``cb-soak seed`` and ``cb-soak run``."""
from __future__ import annotations

import argparse
import asyncio
import signal
import time

from rich.console import Console
from rich.live import Live
from rich.table import Table

from . import __version__
from ._platform import use_selector_loop
from .client import CouchbaseClient
from .config import Config, load_config
from .metrics import Stats, start_metrics_server
from .report import print_report
from .runner import Runner
from .seed import seed

_console = Console()


def _live_panel(runner: Runner, stats: Stats) -> Table:
    elapsed = runner.elapsed
    total = stats.total_count
    remaining = max(0.0, runner.cfg.workload.duration_s - elapsed)
    table = Table.grid(padding=(0, 2))
    table.add_column(justify="right", style="dim")
    table.add_column()
    table.add_row("elapsed", f"{elapsed:,.0f}s  (remaining {remaining:,.0f}s)")
    table.add_row("operations", f"{total:,}")
    table.add_row("throughput", f"{(total / elapsed) if elapsed else 0:,.0f} ops/s")
    table.add_row("errors", f"{stats.total_errors:,}")
    per_op = "  ".join(
        f"{name}={o.count:,}" for name, o in sorted(stats.ops.items())
    )
    table.add_row("by op", per_op or "-")
    return table


async def _run_async(cfg: Config) -> None:
    stats = Stats(reservoir_size=cfg.report.reservoir_size)

    if cfg.metrics.enabled:
        start_metrics_server(cfg.metrics.host, cfg.metrics.port)
        _console.print(
            f"[green]metrics[/green] serving on "
            f"http://{cfg.metrics.host}:{cfg.metrics.port}/metrics"
        )

    client = CouchbaseClient(cfg.cluster, cfg.workload)
    _console.print(f"connecting to [bold]{cfg.cluster.connstr}[/bold] …")
    await client.connect()
    _console.print("[green]connected[/green]")

    runner = Runner(cfg, client, stats)

    loop = asyncio.get_running_loop()
    try:
        loop.add_signal_handler(signal.SIGINT, runner.request_stop)
        loop.add_signal_handler(signal.SIGTERM, runner.request_stop)
    except NotImplementedError:
        # Signal handlers are unavailable on Windows event loops; Ctrl-C
        # still raises KeyboardInterrupt below.
        pass

    run_task = asyncio.create_task(runner.run())
    started = time.monotonic()
    try:
        with Live(_live_panel(runner, stats), console=_console, refresh_per_second=2) as live:
            while not run_task.done():
                live.update(_live_panel(runner, stats))
                await asyncio.sleep(0.5)
    except KeyboardInterrupt:
        runner.request_stop()
    finally:
        await run_task
        await client.close()

    elapsed = time.monotonic() - started
    print_report(cfg, stats, elapsed, console=_console)


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("-c", "--config", default="config.yaml", help="Path to YAML config file")


def main() -> None:
    # acouchbase needs a SelectorEventLoop (see _platform); set it before any
    # asyncio.run below.
    use_selector_loop()

    parser = argparse.ArgumentParser(
        prog="cb-soak",
        description="High-concurrency Couchbase KV + N1QL soak tester.",
    )
    parser.add_argument("--version", action="version", version=f"cb-soak {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_seed = sub.add_parser("seed", help="Populate the key space and create indexes")
    _add_common(p_seed)
    p_seed.add_argument("--batch-size", type=int, default=1000)

    p_run = sub.add_parser("run", help="Run the soak test")
    _add_common(p_run)
    p_run.add_argument("--duration", type=int, help="Override duration in seconds")
    p_run.add_argument("--concurrency", type=int, help="Override worker concurrency")
    p_run.add_argument(
        "--target-ops", type=float, help="Override global ops/sec rate cap"
    )
    p_run.add_argument(
        "--no-metrics", action="store_true", help="Disable the Prometheus endpoint"
    )

    args = parser.parse_args()
    cfg = load_config(args.config)

    if args.command == "seed":
        seed(cfg, batch_size=args.batch_size)
        return

    # run: apply CLI overrides
    if args.duration is not None:
        cfg.workload.duration_s = args.duration
    if args.concurrency is not None:
        cfg.workload.concurrency = args.concurrency
    if args.target_ops is not None:
        cfg.workload.target_ops_per_sec = args.target_ops
    if args.no_metrics:
        cfg.metrics.enabled = False

    try:
        asyncio.run(_run_async(cfg))
    except KeyboardInterrupt:
        _console.print("\n[yellow]interrupted[/yellow]")


if __name__ == "__main__":
    main()
