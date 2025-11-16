#!/bin/bash
# Quick start script for Grafana monitoring stack

set -e

echo "🚀 Starting 247trader-v2 monitoring stack..."

# Check if Docker is running
if ! docker info > /dev/null 2>&1; then
    echo "❌ Docker is not running. Please start Docker first."
    exit 1
fi

# Start Prometheus and Grafana
echo "📊 Starting Prometheus and Grafana containers..."
docker-compose -f docker-compose.monitoring.yml up -d

# Wait for services to be ready
echo "⏳ Waiting for services to start..."
sleep 5

# Check if services are running
if docker ps | grep -q trader-prometheus && docker ps | grep -q trader-grafana; then
    echo "✅ Monitoring stack started successfully!"
    echo ""
    echo "📊 Prometheus: http://localhost:9090"
    echo "📈 Grafana: http://localhost:3000 (admin/admin)"
    echo ""
    echo "🔧 Next steps:"
    echo "1. Start your trading bot with prometheus_enabled: true in config/app.yaml"
    echo "2. Open Grafana at http://localhost:3000"
    echo "3. Add Prometheus data source (http://prometheus:9090)"
    echo "4. Import dashboard from config/grafana/dashboards/trading-dashboard.json"
    echo ""
    echo "To stop: docker-compose -f docker-compose.monitoring.yml down"
else
    echo "❌ Failed to start services. Check Docker logs:"
    echo "docker-compose -f docker-compose.monitoring.yml logs"
    exit 1
fi
