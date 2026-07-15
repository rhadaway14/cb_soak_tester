"""Configuration loading for cb_soak_tester.

Config comes from a YAML file, with environment variables overriding secrets
and connection details so credentials never have to live on disk.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import yaml


@dataclass
class ClusterConfig:
    connstr: str = "couchbase://localhost"
    username: str = "Administrator"
    password: str = "password"
    bucket: str = "soak"
    scope: str = "_default"
    collection: str = "_default"
    kv_timeout_s: float = 5.0
    query_timeout_s: float = 30.0
    tls: bool = False
    tls_cert_path: Optional[str] = None
    # RAM quota (MB) used when `seed` auto-creates a missing bucket.
    bucket_ram_quota_mb: int = 256

    @property
    def keyspace(self) -> str:
        """Backtick-quoted fully-qualified keyspace for N1QL."""
        return f"`{self.bucket}`.`{self.scope}`.`{self.collection}`"


@dataclass
class WorkloadConfig:
    duration_s: int = 3600
    concurrency: int = 128
    ramp_up_s: int = 30
    key_space: int = 100000
    doc_bytes: int = 512
    target_ops_per_sec: Optional[float] = None
    mix: Dict[str, int] = field(
        default_factory=lambda: {"kv_get": 45, "kv_upsert": 25, "query": 30}
    )
    # {keyspace} is already backtick-quoted (see ClusterConfig.keyspace); do
    # not wrap it in additional backticks.
    query: str = (
        "SELECT META().id FROM {keyspace} "
        "WHERE type = $type AND region = $region LIMIT 20"
    )
    regions: List[str] = field(
        default_factory=lambda: ["us-east", "us-west", "eu-central", "ap-south", "sa-east"]
    )


@dataclass
class MetricsConfig:
    enabled: bool = True
    host: str = "0.0.0.0"
    port: int = 9099


@dataclass
class ReportConfig:
    reservoir_size: int = 50000
    json_out: Optional[str] = "soak-report.json"


@dataclass
class Config:
    cluster: ClusterConfig = field(default_factory=ClusterConfig)
    workload: WorkloadConfig = field(default_factory=WorkloadConfig)
    metrics: MetricsConfig = field(default_factory=MetricsConfig)
    report: ReportConfig = field(default_factory=ReportConfig)


def _coerce(section_cls, data: dict):
    """Build a dataclass from a dict, ignoring unknown keys."""
    known = {f.name for f in section_cls.__dataclass_fields__.values()}
    return section_cls(**{k: v for k, v in (data or {}).items() if k in known})


def _apply_env(cfg: Config) -> None:
    """Environment variables override file values for connection + secrets."""
    env_map = {
        "COUCHBASE_CONNSTR": "connstr",
        "COUCHBASE_USERNAME": "username",
        "COUCHBASE_PASSWORD": "password",
        "COUCHBASE_BUCKET": "bucket",
        "COUCHBASE_SCOPE": "scope",
        "COUCHBASE_COLLECTION": "collection",
    }
    for env, attr in env_map.items():
        val = os.environ.get(env)
        if val:
            setattr(cfg.cluster, attr, val)


def load_config(path: Optional[str]) -> Config:
    raw: dict = {}
    if path:
        with open(path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
    cfg = Config(
        cluster=_coerce(ClusterConfig, raw.get("cluster", {})),
        workload=_coerce(WorkloadConfig, raw.get("workload", {})),
        metrics=_coerce(MetricsConfig, raw.get("metrics", {})),
        report=_coerce(ReportConfig, raw.get("report", {})),
    )
    _apply_env(cfg)
    return cfg
