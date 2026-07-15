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

Requires **Python ≥ 3.10** (the couchbase SDK ships no 3.9 wheels).

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
2. Ensure **Python ≥ 3.10** is present — the couchbase SDK only ships wheels for
   3.10+, and on 3.9 pip tries (and fails) to compile it from source. Amazon
   Linux 2023 ships Python 3.9 by default, so install a newer one:

   ```bash
   sudo dnf install -y python3.11          # Amazon Linux 2023
   # Ubuntu/Debian: sudo apt-get install -y python3.11 python3.11-venv
   ```

   `run-ec2.sh` auto-detects a ≥3.10 interpreter (and recreates the venv if it
   was built with an older one), so once 3.11 is installed you're set.
3. Clone this repo onto the box and provide connection details as env vars.
4. Seed once, then run:

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

### Keeping the run alive across SSH drops

An hour-long soak shouldn't die when your SSH session does. Two wrappers are
included — pick one.

**tmux (simplest, no root):**

```bash
export COUCHBASE_CONNSTR=... COUCHBASE_USERNAME=... COUCHBASE_PASSWORD=...
./scripts/soak-tmux.sh start seed    # seed once (detached)
./scripts/soak-tmux.sh start         # start the soak, detached
./scripts/soak-tmux.sh attach        # watch the live display
./scripts/soak-tmux.sh logs          # or just tail the captured output
./scripts/soak-tmux.sh stop          # graceful stop — prints the report
```

**systemd (survives logout and reboots):**

```bash
./scripts/run-ec2.sh run             # once, Ctrl-C after it connects, to build .venv
sudo ./scripts/install-systemd.sh    # renders + installs the unit, seeds /etc/cb-soak.env
sudo nano /etc/cb-soak.env           # set Couchbase connstr / username / password
./scripts/run-ec2.sh seed            # seed the key space once
sudo systemctl start cb-soak         # start the hour-long soak
journalctl -u cb-soak -f             # watch progress + the final report
sudo systemctl stop cb-soak          # graceful stop (SIGINT) — prints the report
```

Both paths stop the tester with **SIGINT**, so an early stop still prints the
full report and writes `soak-report.json`. Credentials live in env vars
(`/etc/cb-soak.env` for systemd, `chmod 600`), never in `config.yaml`.

---

## Grafana dashboard

The tester serves Prometheus metrics on `:9099` by default. Bring up Prometheus
and Grafana one of two ways.

### Option A — native install, no Docker (recommended for a bare EC2 box)

Installs Prometheus + Grafana from their official release tarballs and runs them
as systemd services. No Docker, no package repos; works on Amazon Linux or
Ubuntu, x86_64 or arm64 (Graviton).

```bash
sudo ./scripts/install-observability.sh          # install + start both services
# override the Grafana password: sudo GF_ADMIN_PASSWORD=secret ./scripts/install-observability.sh
sudo ./scripts/install-observability.sh uninstall # stop + remove
```

It scrapes the tester on `localhost:9099`, provisions the datasource + the
**Couchbase Soak Test** dashboard, and prints the URLs. Manage it with
`systemctl status prometheus grafana` and `journalctl -u grafana -f`.

### Option B — Docker Compose

If the host already has Docker, the bundled stack does the same thing:

```bash
cd deploy
docker compose up -d
```

Here Prometheus scrapes `host.docker.internal:9099`; to scrape a tester on
another host, edit the target in
[`deploy/prometheus/prometheus.yml`](deploy/prometheus/prometheus.yml).

### Either way

- **Grafana** → http://<ec2-host>:3000 (default `admin` / `admin`; override with
  `GF_ADMIN_USER` / `GF_ADMIN_PASSWORD`). The **Couchbase Soak Test** dashboard
  is auto-provisioned.
- **Prometheus** → http://<ec2-host>:9090.

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
deploy/              # observability stack + service unit
  docker-compose.yml #   Prometheus + Grafana
  prometheus/        #   scrape config
  grafana/           #   datasource + dashboard provisioning
  systemd/           #   cb-soak.service unit + env-file example
scripts/
  run-ec2.sh              # bootstrap + run helper for EC2
  soak-tmux.sh            # run detached in tmux (survives SSH drops)
  install-systemd.sh      # install the tester as a systemd service
  install-observability.sh# install Prometheus + Grafana natively (no Docker)
config.example.yaml  # fully-commented config reference
```

## License

MIT — see [LICENSE](LICENSE).
