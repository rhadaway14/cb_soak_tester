#!/usr/bin/env bash
#
# Install Prometheus + Grafana natively on an EC2 host (no Docker) and wire them
# to the soak tester's metrics endpoint on localhost:9099. Both run as systemd
# services. Uses the official release tarballs, so it works on Amazon Linux or
# Ubuntu, x86_64 or arm64 (Graviton) — only curl, tar, and systemd are needed.
#
#   sudo ./scripts/install-observability.sh            # install + start
#   sudo ./scripts/install-observability.sh uninstall  # stop + remove
#
# Grafana admin password defaults to "admin"; override with GF_ADMIN_PASSWORD.
# Open ports 3000 (Grafana) and 9090 (Prometheus) to YOUR IP in the security
# group — do not expose them to the world.
set -euo pipefail

PROM_VERSION="${PROM_VERSION:-2.54.1}"
GRAFANA_VERSION="${GRAFANA_VERSION:-11.2.0}"
GF_ADMIN_USER="${GF_ADMIN_USER:-admin}"
GF_ADMIN_PASSWORD="${GF_ADMIN_PASSWORD:-admin}"
SOAK_TARGET="${SOAK_TARGET:-localhost:9099}"

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DASHBOARD_SRC="$REPO_DIR/deploy/grafana/dashboards/couchbase-soak.json"

if [[ $EUID -ne 0 ]]; then
  echo "this installs system services — run it with sudo" >&2
  exit 1
fi

case "$(uname -m)" in
  x86_64)  ARCH="amd64" ;;
  aarch64) ARCH="arm64" ;;
  *) echo "unsupported architecture: $(uname -m)" >&2; exit 1 ;;
esac

ensure_user() {
  local user="$1"
  if ! id "$user" >/dev/null 2>&1; then
    useradd --system --shell /bin/false "$user"
  fi
}

uninstall() {
  echo ">> stopping and removing Prometheus + Grafana"
  systemctl disable --now prometheus grafana 2>/dev/null || true
  rm -f /etc/systemd/system/prometheus.service /etc/systemd/system/grafana.service
  systemctl daemon-reload
  rm -rf /opt/prometheus /opt/grafana /etc/prometheus /etc/grafana
  echo ">> left data dirs /var/lib/prometheus and /var/lib/grafana in place"
  echo "   (remove them by hand if you want a clean slate)"
}

if [[ "${1:-install}" == "uninstall" ]]; then
  uninstall
  exit 0
fi

if [[ ! -f "$DASHBOARD_SRC" ]]; then
  echo "dashboard not found at $DASHBOARD_SRC — run this from the repo checkout" >&2
  exit 1
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# ---------------------------------------------------------------- Prometheus --
echo ">> installing Prometheus $PROM_VERSION ($ARCH)"
PROM_TGZ="prometheus-${PROM_VERSION}.linux-${ARCH}.tar.gz"
curl -fsSL -o "$TMP/$PROM_TGZ" \
  "https://github.com/prometheus/prometheus/releases/download/v${PROM_VERSION}/${PROM_TGZ}"
tar -xzf "$TMP/$PROM_TGZ" -C "$TMP"
PROM_DIR="$TMP/prometheus-${PROM_VERSION}.linux-${ARCH}"

ensure_user prometheus
install -d -o prometheus -g prometheus /opt/prometheus /var/lib/prometheus /etc/prometheus
install -o root -g root -m 0755 "$PROM_DIR/prometheus" /opt/prometheus/prometheus
install -o root -g root -m 0755 "$PROM_DIR/promtool" /opt/prometheus/promtool
cp -r "$PROM_DIR/consoles" "$PROM_DIR/console_libraries" /opt/prometheus/

cat > /etc/prometheus/prometheus.yml <<EOF
global:
  scrape_interval: 5s
  evaluation_interval: 5s

scrape_configs:
  - job_name: "cb-soak-tester"
    static_configs:
      - targets: ["${SOAK_TARGET}"]
EOF
chown prometheus:prometheus /etc/prometheus/prometheus.yml

cat > /etc/systemd/system/prometheus.service <<'EOF'
[Unit]
Description=Prometheus
After=network-online.target
Wants=network-online.target

[Service]
User=prometheus
Group=prometheus
Type=simple
ExecStart=/opt/prometheus/prometheus \
  --config.file=/etc/prometheus/prometheus.yml \
  --storage.tsdb.path=/var/lib/prometheus \
  --storage.tsdb.retention.time=15d \
  --web.console.templates=/opt/prometheus/consoles \
  --web.console.libraries=/opt/prometheus/console_libraries \
  --web.listen-address=0.0.0.0:9090
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# ------------------------------------------------------------------ Grafana --
echo ">> installing Grafana $GRAFANA_VERSION ($ARCH)"
GF_TGZ="grafana-${GRAFANA_VERSION}.linux-${ARCH}.tar.gz"
curl -fsSL -o "$TMP/$GF_TGZ" \
  "https://dl.grafana.com/oss/release/${GF_TGZ}"
tar -xzf "$TMP/$GF_TGZ" -C "$TMP"
GF_DIR="$(find "$TMP" -maxdepth 1 -type d -name 'grafana-*' | head -n1)"

ensure_user grafana
rm -rf /opt/grafana
cp -r "$GF_DIR" /opt/grafana
install -d -o grafana -g grafana \
  /var/lib/grafana /var/lib/grafana/dashboards /var/log/grafana \
  /etc/grafana/provisioning/datasources /etc/grafana/provisioning/dashboards
chown -R grafana:grafana /opt/grafana

# Datasource -> local Prometheus.
cat > /etc/grafana/provisioning/datasources/datasource.yml <<'EOF'
apiVersion: 1
datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://localhost:9090
    isDefault: true
    editable: true
EOF

# Dashboard provider -> /var/lib/grafana/dashboards.
cat > /etc/grafana/provisioning/dashboards/dashboard.yml <<'EOF'
apiVersion: 1
providers:
  - name: "cb-soak"
    orgId: 1
    folder: "Couchbase Soak"
    type: file
    disableDeletion: false
    updateIntervalSeconds: 10
    allowUiUpdates: true
    options:
      path: /var/lib/grafana/dashboards
      foldersFromFilesStructure: false
EOF

cp "$DASHBOARD_SRC" /var/lib/grafana/dashboards/couchbase-soak.json
chown -R grafana:grafana /etc/grafana /var/lib/grafana /var/log/grafana

cat > /etc/systemd/system/grafana.service <<EOF
[Unit]
Description=Grafana
After=network-online.target
Wants=network-online.target

[Service]
User=grafana
Group=grafana
Type=simple
WorkingDirectory=/opt/grafana
ExecStart=/opt/grafana/bin/grafana server --homepath=/opt/grafana
Environment=GF_PATHS_DATA=/var/lib/grafana
Environment=GF_PATHS_LOGS=/var/log/grafana
Environment=GF_PATHS_PROVISIONING=/etc/grafana/provisioning
Environment=GF_SECURITY_ADMIN_USER=${GF_ADMIN_USER}
Environment=GF_SECURITY_ADMIN_PASSWORD=${GF_ADMIN_PASSWORD}
Environment=GF_USERS_ALLOW_SIGN_UP=false
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# --------------------------------------------------------------------- start --
systemctl daemon-reload
systemctl enable --now prometheus grafana

PUBLIC_IP="$(curl -fsS -m 2 http://169.254.169.254/latest/meta-data/public-ipv4 2>/dev/null || echo '<ec2-host>')"

cat <<EOF

observability is up.
  Prometheus : http://${PUBLIC_IP}:9090   (scraping ${SOAK_TARGET})
  Grafana    : http://${PUBLIC_IP}:3000   (user ${GF_ADMIN_USER}, dashboard "Couchbase Soak Test")

check status : systemctl status prometheus grafana
logs         : journalctl -u prometheus -f   |   journalctl -u grafana -f
uninstall    : sudo $0 uninstall

Open ports 3000 and 9090 to your IP in the instance security group.
Start the soak so Prometheus has something to scrape:  ./scripts/run-ec2.sh run
EOF
