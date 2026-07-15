"""Async Couchbase client wrapper built on the ``acouchbase`` SDK.

Wraps connection setup and the three primitive operations (KV get, KV upsert,
N1QL query) so the runner can stay focused on scheduling and metrics.
"""
from __future__ import annotations

import asyncio
import random
import string
from datetime import timedelta
from typing import Optional

from acouchbase.cluster import Cluster
from couchbase.auth import PasswordAuthenticator
from couchbase.exceptions import (
    BucketAlreadyExistsException,
    BucketNotFoundException,
)
from couchbase.management.buckets import BucketType, CreateBucketSettings
from couchbase.options import (
    ClusterOptions,
    ClusterTimeoutOptions,
    QueryOptions,
)

from .config import ClusterConfig, WorkloadConfig

DOC_TYPE = "soak"
_ALPHANUM = string.ascii_letters + string.digits


class CouchbaseClient:
    def __init__(self, cluster_cfg: ClusterConfig, workload_cfg: WorkloadConfig) -> None:
        self._cfg = cluster_cfg
        self._wcfg = workload_cfg
        self._cluster: Optional[Cluster] = None
        self._collection = None

    async def connect(self, ensure_bucket_ram_mb: Optional[int] = None) -> None:
        """Connect the cluster and open the target bucket.

        If ``ensure_bucket_ram_mb`` is given, the bucket is created when missing
        (with that RAM quota) and we wait for it to become ready before opening
        it. When it is ``None`` (the default, used by the run path), a missing
        bucket fails fast with ``BucketNotFoundException``.
        """
        timeout = ClusterTimeoutOptions(
            kv_timeout=timedelta(seconds=self._cfg.kv_timeout_s),
            query_timeout=timedelta(seconds=self._cfg.query_timeout_s),
        )
        auth = PasswordAuthenticator(self._cfg.username, self._cfg.password)
        opts = ClusterOptions(auth, timeout_options=timeout)
        if self._cfg.tls and self._cfg.tls_cert_path:
            opts["cert_path"] = self._cfg.tls_cert_path

        self._cluster = await Cluster.connect(self._cfg.connstr, opts)

        if ensure_bucket_ram_mb is not None:
            await self._ensure_bucket(ensure_bucket_ram_mb)
            await self._open_bucket_with_retry()
        else:
            bucket = self._cluster.bucket(self._cfg.bucket)
            await bucket.on_connect()
            self._collection = bucket.scope(self._cfg.scope).collection(
                self._cfg.collection
            )

    async def _ensure_bucket(self, ram_quota_mb: int) -> bool:
        """Create the configured bucket if it does not already exist.

        Returns True if a bucket was created, False if it already existed.
        """
        mgr = self._cluster.buckets()
        try:
            await mgr.get_bucket(self._cfg.bucket)
            print(f"bucket '{self._cfg.bucket}' already exists")
            return False
        except BucketNotFoundException:
            pass

        try:
            await mgr.create_bucket(
                CreateBucketSettings(
                    name=self._cfg.bucket,
                    bucket_type=BucketType.COUCHBASE,
                    ram_quota_mb=ram_quota_mb,
                    flush_enabled=True,
                )
            )
            print(f"created bucket '{self._cfg.bucket}' ({ram_quota_mb} MB)")
        except BucketAlreadyExistsException:
            return False

        # Wait for the new bucket to be visible to the management API.
        for _ in range(30):
            try:
                await mgr.get_bucket(self._cfg.bucket)
                return True
            except BucketNotFoundException:
                await asyncio.sleep(1)
        return True

    async def _open_bucket_with_retry(self, attempts: int = 30, delay: float = 1.0) -> None:
        """Open the bucket, retrying while a freshly created bucket warms up."""
        last_exc: Optional[Exception] = None
        for _ in range(attempts):
            bucket = self._cluster.bucket(self._cfg.bucket)
            try:
                await bucket.on_connect()
                self._collection = bucket.scope(self._cfg.scope).collection(
                    self._cfg.collection
                )
                return
            except Exception as exc:  # noqa: BLE001 - retried below
                last_exc = exc
                await asyncio.sleep(delay)
        if last_exc is not None:
            raise last_exc

    async def close(self) -> None:
        if self._cluster is not None:
            await self._cluster.close()

    # ---- key / document helpers -------------------------------------------

    @staticmethod
    def key_for(index: int) -> str:
        return f"soak::{index}"

    def random_key(self) -> str:
        return self.key_for(random.randrange(self._wcfg.key_space))

    def make_doc(self, index: int) -> dict:
        region = random.choice(self._wcfg.regions)
        pad = "".join(random.choices(_ALPHANUM, k=max(0, self._wcfg.doc_bytes)))
        return {
            "type": DOC_TYPE,
            "seq": index,
            "region": region,
            "value": random.random(),
            "payload": pad,
        }

    # ---- operations -------------------------------------------------------

    async def kv_get(self) -> None:
        # Missing keys are expected early in a run; treat as a normal outcome.
        try:
            await self._collection.get(self.random_key())
        except Exception as exc:  # noqa: BLE001 - classified by runner
            from couchbase.exceptions import DocumentNotFoundException

            if isinstance(exc, DocumentNotFoundException):
                return
            raise

    async def kv_upsert(self) -> None:
        index = random.randrange(self._wcfg.key_space)
        await self._collection.upsert(self.key_for(index), self.make_doc(index))

    async def query(self) -> None:
        statement = self._wcfg.query.format(keyspace=self._cfg.keyspace)
        region = random.choice(self._wcfg.regions)
        result = self._cluster.query(
            statement,
            QueryOptions(named_parameters={"type": DOC_TYPE, "region": region}),
        )
        # Drain the stream so the query actually executes end to end.
        async for _ in result.rows():
            pass
