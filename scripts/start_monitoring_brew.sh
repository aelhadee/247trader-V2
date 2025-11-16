#!/bin/bash
# Start Grafana/Prometheus monitoring stack using Homebrew installations

set -e

echo "🚀 Starting 247trader-v2 monitoring stack (Homebrew)..."

# Check if Prometheus is installed
if ! command -v prometheus &> /dev/null; then
    echo "❌ Prometheus not found. Install with:"
    echo "   brew install prometheus"
    exit 1
fi

# Check if Grafana is installed
if ! command -v grafana-server &> /dev/null; then
    echo "❌ Grafana not found. Install with:"
    echo "   brew install grafana"
    exit 1
fi

# Get the absolute path to the project directory
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_DIR="${PROJECT_DIR}/config"
PROMETHEUS_CONFIG="${CONFIG_DIR}/prometheus.yml"

# Check if Prometheus config exists
if [ ! -f "$PROMETHEUS_CONFIG" ]; then
    echo "❌ Prometheus config not found: $PROMETHEUS_CONFIG"
    exit 1
fi

# Start Prometheus in background
# NOTE: Bot metrics are on port 9090, Prometheus UI on 9091
echo "📊 Starting Prometheus..."
prometheus --config.file="$PROMETHEUS_CONFIG" \
    --storage.tsdb.path="${PROJECT_DIR}/data/prometheus" \
    --storage.tsdb.retention.time=30d \
    --web.listen-address=:9091 \
    > "${PROJECT_DIR}/logs/prometheus.log" 2>&1 &
PROM_PID=$!
echo "   PID: $PROM_PID"
echo $PROM_PID > "${PROJECT_DIR}/data/prometheus.pid"

# Wait a moment for Prometheus to start
sleep 2

# Check if Prometheus started successfully
if ! ps -p $PROM_PID > /dev/null; then
    echo "❌ Prometheus failed to start. Check logs/prometheus.log"
    exit 1
fi

# Start Grafana in background
echo "📈 Starting Grafana..."

# Detect Grafana paths (Intel vs ARM Mac)
if [ -d "/opt/homebrew/opt/grafana" ]; then
    GRAFANA_HOME="/opt/homebrew/opt/grafana/share/grafana"
    GRAFANA_CONFIG="/opt/homebrew/etc/grafana/grafana.ini"
else
    GRAFANA_HOME="/usr/local/opt/grafana/share/grafana"
    GRAFANA_CONFIG="/usr/local/etc/grafana/grafana.ini"
fi

grafana-server \
    --homepath="$GRAFANA_HOME" \
    --config="$GRAFANA_CONFIG" \
    > "${PROJECT_DIR}/logs/grafana.log" 2>&1 &
GRAFANA_PID=$!
echo "   PID: $GRAFANA_PID"
echo $GRAFANA_PID > "${PROJECT_DIR}/data/grafana.pid"

# Wait a moment for services to initialize
sleep 3

# Check if processes are still running
if ! ps -p $PROM_PID > /dev/null; then
    echo "❌ Prometheus failed to start. Check logs/prometheus.log"
    kill $GRAFANA_PID 2>/dev/null || true
    exit 1
fi

if ! ps -p $GRAFANA_PID > /dev/null; then
    echo "❌ Grafana failed to start. Check logs/grafana.log"
    kill $PROM_PID 2>/dev/null || true
    exit 1
fi

echo ""
echo "============================================================"
echo "✅ Monitoring stack started successfully!"
echo ""
echo "📊 Prometheus: http://localhost:9090 (PID: $PROM_PID)"
echo "📈 Grafana: http://localhost:3000 (PID: $GRAFANA_PID, admin/admin)"
echo "🤖 Bot metrics: http://localhost:8000/metrics (when bot running)"
echo ""
echo "⏳ Services may take 10-30 seconds to fully initialize"
echo ""
echo "📝 Logs:"
echo "   Prometheus: tail -f logs/prometheus.log"
echo "   Grafana: tail -f logs/grafana.log"
echo ""
echo "🔧 Next steps:"
echo "1. Wait ~30s for services to fully start"
echo "2. Open Grafana: http://localhost:3000 (admin/admin)"
echo "3. Add Prometheus data source: http://localhost:9090"
echo "4. Import dashboard: config/grafana/dashboards/trading-dashboard.json"
echo "5. Enable in config/app.yaml: prometheus_enabled: true"
echo "6. Start bot: ./app_run_live.sh"
echo ""
echo "� To stop: ./scripts/stop_monitoring_brew.sh"
echo "============================================================"
