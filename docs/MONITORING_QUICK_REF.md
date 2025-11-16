# Monitoring Quick Reference

## Single Command for Everything

```bash
# Start or restart monitoring (stops existing services first)
./scripts/start_monitoring.sh
```

**What it does:**
1. ✅ Stops any running Prometheus/Grafana (Docker or Homebrew)
2. ✅ Starts monitoring stack using available method (Docker preferred)
3. ✅ Verifies services are running
4. ✅ Shows access URLs and next steps

## Access URLs

**Homebrew Setup:**
- Grafana: http://localhost:3000 (admin/admin)
- Prometheus: http://localhost:9091
- Bot Metrics: http://localhost:9090/metrics

**Docker Setup:**
- Grafana: http://localhost:3000 (admin/admin)
- Prometheus: http://localhost:9090
- Bot Metrics: http://localhost:8000/metrics

## Setup Grafana (First Time Only)

1. Open http://localhost:3000 (admin/admin)
2. Add Prometheus data source:
   - Homebrew: `http://localhost:9091`
   - Docker: `http://prometheus:9090`
3. Import dashboard: `config/grafana/dashboards/trading-dashboard.json`

Done! You'll see 10 panels with real-time trading metrics.

## Troubleshooting

### Services won't start
```bash
# Check what's using the ports
lsof -i :3000    # Grafana
lsof -i :9090    # Prometheus (Docker) or Bot (Homebrew)
lsof -i :9091    # Prometheus (Homebrew)

# View logs
tail -f logs/prometheus.log
tail -f logs/grafana.log
```

### Dashboard shows "No Data"
1. Verify bot is running and exposing metrics
2. Check Prometheus is scraping: Visit targets page
3. Verify data source URL in Grafana matches your setup

### Need to fully stop services
```bash
# The start script stops everything first, but if needed:
pkill -9 prometheus
pkill -9 grafana-server
docker-compose -f docker-compose.monitoring.yml down
```

## Installation Options

### Option 1: Docker (Recommended)
```bash
# Install Docker Desktop from https://www.docker.com/products/docker-desktop
# Then just run: ./scripts/start_monitoring.sh
```

### Option 2: Homebrew
```bash
brew install prometheus grafana
./scripts/start_monitoring.sh
```

## Complete Documentation

- **Quick Start**: This file
- **Homebrew Setup**: `docs/HOMEBREW_MONITORING_SETUP.md`
- **Comprehensive Guide**: `docs/MONITORING_SETUP.md`
- **Implementation Details**: `docs/GRAFANA_INTEGRATION_COMPLETE.md`
