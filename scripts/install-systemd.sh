#!/usr/bin/env bash
#
# Install cb_soak_tester as a systemd service so the soak survives logout and
# reboots. Renders the unit template with this checkout's path + the run user,
# seeds an /etc/cb-soak.env for credentials, and reloads systemd.
#
#   sudo ./scripts/install-systemd.sh
#
# Then edit /etc/cb-soak.env, seed the cluster, and start the service:
#   sudo systemctl start cb-soak
set -euo pipefail

cd "$(dirname "$0")/.."
WORKDIR="$PWD"
RUN_USER="${SUDO_USER:-$USER}"
UNIT_SRC="deploy/systemd/cb-soak.service"
UNIT_DST="/etc/systemd/system/cb-soak.service"
ENV_SRC="deploy/systemd/cb-soak.env.example"
ENV_DST="/etc/cb-soak.env"

if [[ $EUID -ne 0 ]]; then
  echo "this script installs a system unit — run it with sudo" >&2
  exit 1
fi

if [[ ! -x "$WORKDIR/.venv/bin/python" ]]; then
  echo "no virtualenv at $WORKDIR/.venv" >&2
  echo "create it first (as $RUN_USER):  ./scripts/run-ec2.sh run  (Ctrl-C once it starts), or:" >&2
  echo "  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

# config.yaml must exist (ExecStart passes -c config.yaml); env vars override it.
if [[ ! -f "$WORKDIR/config.yaml" ]]; then
  cp "$WORKDIR/config.example.yaml" "$WORKDIR/config.yaml"
  chown "$RUN_USER":"$RUN_USER" "$WORKDIR/config.yaml"
  echo "created config.yaml from example (tune workload.* to taste)"
fi

sed -e "s|__WORKDIR__|$WORKDIR|g" -e "s|__USER__|$RUN_USER|g" "$UNIT_SRC" > "$UNIT_DST"
echo "installed $UNIT_DST  (User=$RUN_USER, WorkingDirectory=$WORKDIR)"

if [[ ! -f "$ENV_DST" ]]; then
  cp "$ENV_SRC" "$ENV_DST"
  chmod 600 "$ENV_DST"
  echo "created $ENV_DST — EDIT IT with your Couchbase credentials"
else
  echo "$ENV_DST already exists — leaving it untouched"
fi

systemctl daemon-reload

cat <<EOF

installed. next steps:
  sudo nano $ENV_DST                    # set COUCHBASE_CONNSTR / USERNAME / PASSWORD
  ./scripts/run-ec2.sh seed             # seed the key space once (as $RUN_USER)
  sudo systemctl start cb-soak          # start the hour-long soak
  journalctl -u cb-soak -f              # watch progress + the final report
  sudo systemctl stop cb-soak           # graceful stop (prints the report)

enable on boot (optional):  sudo systemctl enable cb-soak
EOF
