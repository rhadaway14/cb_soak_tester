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
# The couchbase SDK only ships wheels for Python >= 3.10; on 3.9 pip falls back
# to compiling the C++ core from source (needs cmake/gcc). Pick a >= 3.10
# interpreter, and recreate the venv if it was built with an older one.
pick_python() {
  local cand ver
  for cand in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$cand" >/dev/null 2>&1; then
      ver="$("$cand" -c 'import sys; print(sys.version_info[0]*100 + sys.version_info[1])' 2>/dev/null || echo 0)"
      if [[ "$ver" -ge 310 ]]; then echo "$cand"; return 0; fi
    fi
  done
  return 1
}

if [[ -d .venv ]]; then
  venv_ver="$(.venv/bin/python -c 'import sys; print(sys.version_info[0]*100 + sys.version_info[1])' 2>/dev/null || echo 0)"
  if [[ "$venv_ver" -lt 310 ]]; then
    echo ">> existing .venv uses Python < 3.10; recreating"
    rm -rf .venv
  fi
fi

if [[ ! -d .venv ]]; then
  if ! PYTHON="$(pick_python)"; then
    echo "ERROR: need Python >= 3.10 (the couchbase SDK has no wheels for 3.9)." >&2
    echo "  Amazon Linux 2023:  sudo dnf install -y python3.11" >&2
    echo "  Ubuntu/Debian:      sudo apt-get install -y python3.11 python3.11-venv" >&2
    exit 1
  fi
  echo ">> creating virtualenv with $PYTHON ($("$PYTHON" --version 2>&1))"
  "$PYTHON" -m venv .venv
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
