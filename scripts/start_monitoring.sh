#!/bin/bash
# Unified monitoring script - stops existing services then starts fresh
# Supports both Docker and Homebrew installations

set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "� 247trader-v2 Monitoring Stack"
echo "============================================================"

# ============================================================
# STOP EXISTING SERVICES
# ============================================================
echo ""
echo "🛑 Stopping any existing monitoring services..."

# Stop Docker containers if running
if command -v docker &> /dev/null && docker info > /dev/null 2>&1; then
    if docker ps -a | grep -E "(trader-prometheus|trader-grafana)" > /dev/null 2>&1; then
        echo "   Stopping Docker containers..."
        docker-compose -f docker-compose.monitoring.yml down 2>/dev/null || true
    fi
fi

# Stop Homebrew processes
if [ -f "${PROJECT_DIR}/data/prometheus.pid" ]; then
    PROM_PID=$(cat "${PROJECT_DIR}/data/prometheus.pid")
    if ps -p $PROM_PID > /dev/null 2>&1; then
        echo "   Stopping Prometheus (PID: $PROM_PID)..."
        kill $PROM_PID 2>/dev/null || true
        sleep 1
        kill -9 $PROM_PID 2>/dev/null || true
    fi
    rm -f "${PROJECT_DIR}/data/prometheus.pid"
fi

if [ -f "${PROJECT_DIR}/data/grafana.pid" ]; then
    GRAFANA_PID=$(cat "${PROJECT_DIR}/data/grafana.pid")
    if ps -p $GRAFANA_PID > /dev/null 2>&1; then
        echo "   Stopping Grafana (PID: $GRAFANA_PID)..."
        kill $GRAFANA_PID 2>/dev/null || true
        sleep 1
        kill -9 $GRAFANA_PID 2>/dev/null || true
    fi
    rm -f "${PROJECT_DIR}/data/grafana.pid"
fi

# Kill any remaining prometheus/grafana processes using our config
pkill -9 -f "prometheus.*${PROJECT_DIR}/config/prometheus.yml" 2>/dev/null || true
pkill -9 -f "grafana-server" 2>/dev/null || true

sleep 2
echo "   ✅ Cleanup complete"

# ============================================================
# START SERVICES
# ============================================================
echo ""
echo "🚀 Starting monitoring services..."

# Try Docker first (if available and running)
if command -v docker &> /dev/null && docker info > /dev/null 2>&1; then
    echo ""
    echo "� Using Docker (docker-compose.monitoring.yml)"
    echo "   Starting Prometheus and Grafana containers..."
    docker-compose -f "${PROJECT_DIR}/docker-compose.monitoring.yml" up -d

    
    # Wait and verify
    sleep 5
    if docker ps | grep -q trader-prometheus && docker ps | grep -q trader-grafana; then
        echo ""
        echo "============================================================"
        echo "✅ Monitoring stack started successfully (Docker)"
        echo ""
        echo "📊 Prometheus: http://localhost:9090"
        echo "📈 Grafana: http://localhost:3000 (admin/admin)"
        echo "🤖 Bot metrics: http://localhost:8000/metrics (when bot running)"
        echo ""
        echo "🔧 Next steps:"
        echo "1. Open Grafana: http://localhost:3000 (admin/admin)"
        echo "2. Add data source: http://prometheus:9090"
        echo "3. Import: config/grafana/dashboards/trading-dashboard.json"
        echo "4. Enable in config/app.yaml: prometheus_enabled: true"
        echo "5. Start bot: ./app_run_live.sh"
        echo ""
        echo "🛑 To stop: $0"
        echo "============================================================"
        exit 0
    else
        echo "❌ Docker containers failed to start"
        echo "   Check: docker-compose -f docker-compose.monitoring.yml logs"
        exit 1
    fi

# Fall back to Homebrew
elif command -v prometheus &> /dev/null && command -v grafana-server &> /dev/null; then
    echo ""
    echo "🍺 Using Homebrew installations"
    
    # Check for required files
    PROMETHEUS_CONFIG="${PROJECT_DIR}/config/prometheus.yml"
    if [ ! -f "$PROMETHEUS_CONFIG" ]; then
        echo "❌ Prometheus config not found: $PROMETHEUS_CONFIG"
        exit 1
    fi
    
    # Start Prometheus (port 9091 - bot uses 9090)
    echo "   Starting Prometheus..."
    prometheus --config.file="$PROMETHEUS_CONFIG" \
        --storage.tsdb.path="${PROJECT_DIR}/data/prometheus" \
        --storage.tsdb.retention.time=30d \
        --web.listen-address=:9091 \
        > "${PROJECT_DIR}/logs/prometheus.log" 2>&1 &
    PROM_PID=$!
    echo $PROM_PID > "${PROJECT_DIR}/data/prometheus.pid"
    echo "      PID: $PROM_PID"
    
    sleep 2
    if ! ps -p $PROM_PID > /dev/null; then
        echo "❌ Prometheus failed to start. Check logs/prometheus.log"
        exit 1
    fi
    
    # Start Grafana
    echo "   Starting Grafana..."
    
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
    echo $GRAFANA_PID > "${PROJECT_DIR}/data/grafana.pid"
    echo "      PID: $GRAFANA_PID"
    
    sleep 3
    if ! ps -p $GRAFANA_PID > /dev/null; then
        echo "❌ Grafana failed to start. Check logs/grafana.log"
        kill $PROM_PID 2>/dev/null || true
        exit 1
    fi
    
    echo ""
    echo "============================================================"
    echo "✅ Monitoring stack started successfully (Homebrew)"
    echo ""
    echo "📊 Prometheus: http://localhost:9091 (PID: $PROM_PID)"
    echo "📈 Grafana: http://localhost:3000 (PID: $GRAFANA_PID, admin/admin)"
    echo "🤖 Bot metrics: http://localhost:9090/metrics (if bot running)"
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
    echo "3. Add data source: http://localhost:9091"
    echo "4. Import: config/grafana/dashboards/trading-dashboard.json"
    echo "5. Bot should already be running with metrics on port 9090"
    echo ""
    echo "🛑 To stop: $0"
    echo "============================================================"
    exit 0

else
    echo ""
    echo "❌ No monitoring tools found!"
    echo ""
    echo "Install one of:"
    echo "  • Docker Desktop (recommended): https://www.docker.com/products/docker-desktop"
    echo "  • Homebrew: brew install prometheus grafana"
    echo ""
    exit 1
fi
