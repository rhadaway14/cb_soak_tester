#!/usr/bin/env bash
#
# One-shot bootstrap + soak runner for an EC2 host (Amazon Linux 2023 / Ubuntu).
#
# Usage:
#   COUCHBASE_CONNSTR=couchbase://10.0.0.10 \
#   COUCHBASE_USERNAME=Administrator \
#   COUCHBASE_PASSWORD=secret \
#   ./scripts/run-ec2.sh [seed|run]
#
# Env overrides (see config.example.yaml) are read directly by the tester, so
# credentials never need to touch config.yaml on the box.
set -euo pipefail

cd "$(dirname "$0")/.." || exit 1
ACTION="${1:-run}"

# --- Python venv -------------------------------------------------------------
if [[ ! -d .venv ]]; then
  echo ">> creating virtualenv"
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt

# --- config ------------------------------------------------------------------
if [[ ! -f config.yaml ]]; then
  echo ">> config.yaml not found; copying from example (env vars still override)"
  cp config.example.yaml config.yaml
fi

# --- run ---------------------------------------------------------------------
case "$ACTION" in
  seed)
    echo ">> seeding key space + indexes"
    python -m soaktester seed -c config.yaml
    ;;
  run)
    echo ">> starting soak (Ctrl-C to stop early and print the report)"
    python -m soaktester run -c config.yaml
    ;;
  *)
    echo "unknown action: $ACTION (expected 'seed' or 'run')" >&2
    exit 2
    ;;
esac
