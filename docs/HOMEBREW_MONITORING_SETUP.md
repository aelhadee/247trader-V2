# Grafana/Prometheus Monitoring - Homebrew Setup ✅

**Status:** Running Successfully  
**Date:** November 15, 2025

## Services Running

✅ **Prometheus**: http://localhost:9091 (scrapes bot metrics from port 9090)  
✅ **Grafana**: http://localhost:3000 (admin/admin)  
✅ **Trading Bot**: Running with metrics on http://localhost:9090/metrics

## Quick Commands

```bash
# Start monitoring
./scripts/start_monitoring_brew.sh

# Stop monitoring
./scripts/stop_monitoring_brew.sh

# View logs
tail -f logs/prometheus.log
tail -f logs/grafana.log

# Check status
ps aux | grep -E "(prometheus|grafana)" | grep -v grep
lsof -i :9091  # Prometheus
lsof -i :3000  # Grafana
lsof -i :9090  # Bot metrics
```

## Setup Grafana Dashboard

1. **Open Grafana**: http://localhost:3000
   - Login: admin / admin
   - Change password when prompted

2. **Add Prometheus Data Source**:
   - Navigate: **Configuration** → **Data Sources** → **Add data source**
   - Select: **Prometheus**
   - URL: `http://localhost:9091`
   - Click: **Save & Test**

3. **Import Dashboard**:
   - Navigate: **Dashboards** → **Import**
   - Upload: `config/grafana/dashboards/trading-dashboard.json`
   - Select Prometheus data source
   - Click: **Import**

## Port Configuration

- **9090**: Bot's MetricsRecorder (internal metrics)
- **9091**: Prometheus UI (time-series DB)
- **3000**: Grafana (dashboards)
- **8000**: Bot's Prometheus exporter (trading metrics)

Note: The bot exposes TWO metrics endpoints:
- Port 9090: Built-in MetricsRecorder (cycle stats, latency)
- Port 8000: PrometheusExporter (trading metrics for Grafana)

## Differences from Docker Setup

**Docker (`docker-compose.monitoring.yml`):**
- Prometheus on port 9090
- Uses Docker networking (host.docker.internal)
- Containers managed together

**Homebrew (`start_monitoring_brew.sh`):**
- Prometheus on port 9091 (bot uses 9090)
- Direct localhost access
- Processes run as daemons

Use Docker if you have Docker Desktop.  
Use Homebrew if you prefer native processes.

## Troubleshooting

### Prometheus won't start (port in use)
```bash
# Check what's on port 9091
lsof -i :9091

# Stop and restart
./scripts/stop_monitoring_brew.sh
./scripts/start_monitoring_brew.sh
```

### Grafana dashboard shows "No Data"
1. Verify bot is running: `curl http://localhost:9090/metrics`
2. Check Prometheus is scraping: http://localhost:9091/targets
3. Verify data source URL in Grafana: `http://localhost:9091`

### Services not accessible after 30s
```bash
# Check logs
tail -50 logs/prometheus.log
tail -50 logs/grafana.log

# Check processes
ps aux | grep -E "(prometheus|grafana)"
```

## Next Steps

1. ✅ Services running
2. ⏳ Configure Grafana (add data source + import dashboard)
3. ⏳ View metrics in real-time
4. ⏳ Configure alerts (optional)

See `docs/MONITORING_SETUP.md` for comprehensive guide.
