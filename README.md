# cb_soak_tester

High-concurrency **soak / load tester for Couchbase**. It drives a realistic mix
of **KV operations and N1QL queries** against a cluster, exposes live
**Prometheus metrics** for a bundled **Grafana dashboard**, and prints a
**CLI table report** (plus a JSON summary) at the end of the run.

Built to run for an hour at high concurrency from an EC2 host to prove out
cluster performance under sustained, app-like load.

---

## What it does

- **Async, high-concurrency engine** (`asyncio` + the official `acouchbase` SDK).
  Hundreds of concurrent workers share one cluster connection, with optional
  linear ramp-up and an optional global ops/sec rate cap.
- **Mixed workload** — KV `get`, KV `upsert`, and parameterized N1QL `query`,
  selected by configurable weights. Default is a **KV-heavy 45/25/30** mix.
- **Observability** — a Prometheus `/metrics` endpoint (ops counters, latency
  histograms, in-flight gauge, active workers) and a ready-made Grafana
  dashboard showing throughput, per-op latency p50/p95/p99, and error rate.
- **CLI report** — a Rich results table with counts, throughput, error rate,
  and latency percentiles per operation, plus a `soak-report.json` summary.

---

## Quick start (local)

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp config.example.yaml config.yaml                  # then edit connection details
# ...or supply connection details via env vars (recommended for secrets):
export COUCHBASE_CONNSTR="couchbase://localhost"
export COUCHBASE_USERNAME="Administrator"
export COUCHBASE_PASSWORD="password"

python -m soaktester seed -c config.yaml            # create key space + indexes (once)
python -m soaktester run  -c config.yaml            # run the soak
```

The `cb-soak` console script is also installed via `pip install -e .`:

```bash
cb-soak seed -c config.yaml
cb-soak run  -c config.yaml --duration 3600 --concurrency 128
```

---

## Running an hour-long soak on EC2

1. Launch an EC2 instance in the same VPC/subnet as the Couchbase cluster
   (a `c7i.2xlarge` or similar gives plenty of headroom to be the *load
   generator*, not the bottleneck). Open the cluster's security group to it.
2. Clone this repo onto the box and provide connection details as env vars.
3. Seed once, then run:

```bash
export COUCHBASE_CONNSTR="couchbase://<cluster-ip>"   # couchbases:// for TLS/Capella
export COUCHBASE_USERNAME="Administrator"
export COUCHBASE_PASSWORD="<secret>"

./scripts/run-ec2.sh seed      # populate the key space + indexes
./scripts/run-ec2.sh run       # 1-hour soak (duration/concurrency from config.yaml)
```

`run-ec2.sh` creates a virtualenv, installs dependencies, copies
`config.example.yaml` to `config.yaml` if needed, and starts the run. The
defaults in `config.example.yaml` are a **1-hour (3600s) run at concurrency
128** — tune `workload.*` to taste.

> Run the tester inside `tmux`/`screen` (or as a `nohup`/systemd service) so an
> SSH disconnect doesn't end the soak. Ctrl-C stops early and still prints the
> full report.

---

## Grafana dashboard

The tester serves Prometheus metrics on `:9099` by default. Bring up Prometheus
and Grafana with the bundled stack (run it on the same EC2 host as the tester):

```bash
cd deploy
docker compose up -d
```

- **Grafana** → http://<ec2-host>:3000 (default `admin` / `admin`; override with
  `GF_ADMIN_USER` / `GF_ADMIN_PASSWORD`). The **Couchbase Soak Test** dashboard
  is auto-provisioned.
- **Prometheus** → http://<ec2-host>:9090.

Prometheus scrapes `host.docker.internal:9099` by default (the tester running on
the host). To scrape a tester on another host, edit the target in
[`deploy/prometheus/prometheus.yml`](deploy/prometheus/prometheus.yml).

> Expose ports 3000/9090 only to your own IP in the instance security group.

---

## Configuration

All behavior is driven by `config.yaml` (see
[`config.example.yaml`](config.example.yaml) for the fully-commented reference).
Highlights:

| Key | Meaning | Default |
| --- | --- | --- |
| `workload.duration_s` | Total run length in seconds | `3600` |
| `workload.concurrency` | Concurrent async workers | `128` |
| `workload.ramp_up_s` | Linear worker ramp-up window | `30` |
| `workload.key_space` | Number of KV documents | `100000` |
| `workload.doc_bytes` | Approx payload size per doc | `512` |
| `workload.target_ops_per_sec` | Global rate cap (`null` = open loop) | `null` |
| `workload.mix` | Relative op weights (`kv_get`/`kv_upsert`/`query`) | `45/25/30` |
| `metrics.port` | Prometheus `/metrics` port | `9099` |

**Secrets** (`COUCHBASE_CONNSTR`, `COUCHBASE_USERNAME`, `COUCHBASE_PASSWORD`, and
the bucket/scope/collection) can be supplied via environment variables, which
always override the file — keep credentials out of `config.yaml`.

CLI overrides for a run: `--duration`, `--concurrency`, `--target-ops`,
`--no-metrics`.

---

## Metrics reference

| Metric | Type | Labels | Description |
| --- | --- | --- | --- |
| `soak_ops_total` | counter | `operation`, `status` | Operations attempted (`ok`/`error`) |
| `soak_op_latency_seconds` | histogram | `operation` | Per-operation latency |
| `soak_inflight_operations` | gauge | — | Operations currently in flight |
| `soak_active_workers` | gauge | — | Active worker coroutines |

---

## Project layout

```
soaktester/          # the tool
  cli.py             #   argparse CLI + live display
  config.py          #   YAML + env config
  client.py          #   acouchbase wrapper (KV + N1QL ops)
  runner.py          #   concurrency engine (ramp, mix, rate limit, deadline)
  metrics.py         #   Prometheus metrics + reservoir latency stats
  report.py          #   Rich CLI table + JSON summary
  seed.py            #   key-space + index seeding
deploy/              # observability stack
  docker-compose.yml #   Prometheus + Grafana
  prometheus/        #   scrape config
  grafana/           #   datasource + dashboard provisioning
scripts/run-ec2.sh   # bootstrap + run helper for EC2
config.example.yaml  # fully-commented config reference
```

## License

MIT — see [LICENSE](LICENSE).
