#!/bin/bash
# Stop Grafana/Prometheus monitoring stack (Homebrew installations)

set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "🛑 Stopping 247trader-v2 monitoring stack..."

# Stop Prometheus
PROM_STOPPED=false
if [ -f "${PROJECT_DIR}/data/prometheus.pid" ]; then
    PROM_PID=$(cat "${PROJECT_DIR}/data/prometheus.pid")
    if ps -p $PROM_PID > /dev/null 2>&1; then
        echo "   Stopping Prometheus (PID: $PROM_PID)..."
        kill $PROM_PID
        sleep 2
        # Force kill if still running
        if ps -p $PROM_PID > /dev/null 2>&1; then
            kill -9 $PROM_PID 2>/dev/null || true
        fi
        PROM_STOPPED=true
    fi
    rm -f "${PROJECT_DIR}/data/prometheus.pid"
fi

# Also kill any prometheus processes using our config file
pkill -9 -f "prometheus.*${PROJECT_DIR}/config/prometheus.yml" 2>/dev/null && PROM_STOPPED=true || true

# Verify prometheus stopped
if pgrep -f "prometheus.*${PROJECT_DIR}/config/prometheus.yml" > /dev/null 2>&1; then
    echo "⚠️  Warning: Some Prometheus processes may still be running"
    echo "   Try: pkill -9 prometheus"
fi

# Stop Grafana
if [ -f "${PROJECT_DIR}/data/grafana.pid" ]; then
    GRAFANA_PID=$(cat "${PROJECT_DIR}/data/grafana.pid")
    if ps -p $GRAFANA_PID > /dev/null 2>&1; then
        echo "   Stopping Grafana (PID: $GRAFANA_PID)..."
        kill $GRAFANA_PID
        sleep 2
        # Force kill if still running
        if ps -p $GRAFANA_PID > /dev/null 2>&1; then
            kill -9 $GRAFANA_PID 2>/dev/null || true
        fi
    fi
    rm -f "${PROJECT_DIR}/data/grafana.pid"
else
    # Try to find and kill by process name
    pkill -f "grafana-server" 2>/dev/null || true
fi

echo "✅ Monitoring stack stopped"
echo ""
echo "💡 To start again: ./scripts/start_monitoring_brew.sh"
