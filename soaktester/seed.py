"""Seed the key space and create the index the default query needs.

Run once against a fresh bucket before a soak:  ``cb-soak seed -c config.yaml``
"""
from __future__ import annotations

import asyncio

from couchbase.exceptions import QueryIndexAlreadyExistsException

from .client import CouchbaseClient
from .config import Config
from rich.console import Console
from rich.progress import Progress

_console = Console()


async def _ensure_index(client: CouchbaseClient, cfg: Config) -> None:
    keyspace = cfg.cluster.keyspace
    stmts = [
        f"CREATE PRIMARY INDEX IF NOT EXISTS ON {keyspace}",
        f"CREATE INDEX idx_soak_type_region IF NOT EXISTS "
        f"ON {keyspace}(type, region)",
    ]
    for stmt in stmts:
        try:
            result = client._cluster.query(stmt)  # noqa: SLF001 - internal use
            async for _ in result.rows():
                pass
            _console.print(f"[green]index ok[/green]  {stmt}")
        except QueryIndexAlreadyExistsException:
            _console.print(f"[yellow]exists[/yellow]  {stmt}")


async def _seed_async(cfg: Config, batch_size: int) -> None:
    client = CouchbaseClient(cfg.cluster, cfg.workload)
    await client.connect()
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


def seed(cfg: Config, batch_size: int = 1000) -> None:
    asyncio.run(_seed_async(cfg, batch_size))
