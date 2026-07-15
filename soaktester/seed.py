"""Seed the key space and create the index the default query needs.

Run once against a fresh bucket before a soak:  ``cb-soak seed -c config.yaml``
"""
from __future__ import annotations

import asyncio

from couchbase.exceptions import QueryIndexAlreadyExistsException

from ._platform import use_selector_loop
from .client import CouchbaseClient
from .config import Config
from rich.console import Console
from rich.progress import Progress

_console = Console()


async def _run_index_stmt(client: CouchbaseClient, stmt: str) -> None:
    # A freshly created bucket can take a moment to register with the query
    # service, so retry transient failures before giving up.
    last_exc: Exception | None = None
    for _ in range(30):
        try:
            result = client._cluster.query(stmt)  # noqa: SLF001 - internal use
            async for _ in result.rows():
                pass
            _console.print(f"[green]index ok[/green]  {stmt}")
            return
        except QueryIndexAlreadyExistsException:
            _console.print(f"[yellow]exists[/yellow]  {stmt}")
            return
        except Exception as exc:  # noqa: BLE001 - retried while bucket warms up
            last_exc = exc
            await asyncio.sleep(1)
    if last_exc is not None:
        raise last_exc


async def _ensure_index(client: CouchbaseClient, cfg: Config) -> None:
    keyspace = cfg.cluster.keyspace
    stmts = [
        f"CREATE PRIMARY INDEX IF NOT EXISTS ON {keyspace}",
        f"CREATE INDEX idx_soak_type_region IF NOT EXISTS "
        f"ON {keyspace}(type, region)",
    ]
    for stmt in stmts:
        await _run_index_stmt(client, stmt)


async def _seed_async(cfg: Config, batch_size: int, create_bucket: bool) -> None:
    client = CouchbaseClient(cfg.cluster, cfg.workload)
    ram = cfg.cluster.bucket_ram_quota_mb if create_bucket else None
    await client.connect(ensure_bucket_ram_mb=ram)
    try:
        await _ensure_index(client, cfg)
        total = cfg.workload.key_space
        with Progress(console=_console) as progress:
            task = progress.add_task("seeding documents", total=total)
            for start in range(0, total, batch_size):
                end = min(start + batch_size, total)
                await asyncio.gather(
                    *(
                        client._collection.upsert(  # noqa: SLF001 - internal use
                            client.key_for(i), client.make_doc(i)
                        )
                        for i in range(start, end)
                    )
                )
                progress.update(task, advance=end - start)
    finally:
        await client.close()
    _console.print(f"[bold green]Seeded {cfg.workload.key_space} documents.[/bold green]")


def seed(cfg: Config, batch_size: int = 1000, create_bucket: bool = True) -> None:
    use_selector_loop()
    asyncio.run(_seed_async(cfg, batch_size, create_bucket))
