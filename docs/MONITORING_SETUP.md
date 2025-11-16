# Grafana Monitoring Setup Guide

## Overview

The 247trader-v2 project includes comprehensive monitoring via **Prometheus** (metrics collection) and **Grafana** (visualization). This gives you real-time insight into:

- **Trading Performance**: PnL, account value, trades/day, win rate
- **Risk Metrics**: Exposure %, drawdown, position counts, risk rejections
- **System Health**: API latency, circuit breaker trips, cycle duration, data staleness

---

## Architecture

```
┌──────────────────┐         ┌──────────────────┐         ┌──────────────────┐
│  Trading Bot     │ exposes │   Prometheus     │ scraped │     Grafana      │
│  (Port 8000)     ├────────>│   (Port 9090)    ├────────>│   (Port 3000)    │
│  /metrics        │ metrics │   Time-series DB │   by    │   Dashboards     │
└──────────────────┘         └──────────────────┘         └──────────────────┘
```

---

## Quick Start (5 minutes)

### 1. Install Requirements

```bash
# Add prometheus_client to your Python environment
pip install prometheus-client
```

### 2. Start Monitoring Stack

```bash
# From project root
./scripts/start_monitoring.sh
```

This launches:
- **Prometheus** at http://localhost:9090
- **Grafana** at http://localhost:3000

### 3. Configure Grafana

1. Open http://localhost:3000
2. Login: `admin` / `admin` (change password when prompted)
3. Add Prometheus data source:
   - Navigate: **Configuration** → **Data Sources** → **Add data source**
   - Select **Prometheus**
   - URL: `http://prometheus:9090`
   - Click **Save & Test**

4. Import dashboard:
   - Navigate: **Dashboards** → **Import**
   - Click **Upload JSON file**
   - Select `config/grafana/dashboards/trading-dashboard.json`
   - Select Prometheus data source
   - Click **Import**

### 4. Start Trading Bot

Ensure `config/app.yaml` has:

```yaml
monitoring:
  prometheus_enabled: true
  prometheus_port: 8000
```

Then run your bot normally:

```bash
./app_run_live.sh
```

### 5. View Metrics

- **Raw metrics**: http://localhost:8000/metrics (Prometheus format)
- **Grafana dashboard**: http://localhost:3000/dashboards

---

## Available Metrics

### Trading Performance
- `trader_account_value_usd` - Total account value in USD
- `trader_daily_pnl_usd` - Daily profit/loss
- `trader_pnl_pct` - PnL as percentage
- `trader_trades_total{outcome="win|loss"}` - Trade count by outcome
- `trader_max_drawdown_pct` - Maximum drawdown

### Positions & Exposure
- `trader_open_positions` - Number of open positions
- `trader_exposure_pct` - Total exposure as % of portfolio
- `trader_position_value_usd{symbol="..."}` - Per-symbol position value

### Risk Management
- `trader_risk_rejections_total{reason="..."}` - Rejected proposals by reason
  - Reasons: `max_open_positions`, `exposure_cap`, `cooldown`, `cluster_limit`, `mode_gate`, `data_stale`, `circuit_breaker`
- `trader_circuit_breaker_trips_total{reason="..."}` - Circuit breaker activations
  - Reasons: `volatility`, `drawdown`, `api_errors`, `data_staleness`, `exchange_degraded`

### Execution
- `trader_orders_placed_total` - Orders submitted
- `trader_orders_filled_total` - Orders filled
- `trader_orders_canceled_total` - Orders canceled
- `trader_order_fill_ratio` - Fill rate (0-1)

### System Health
- `trader_api_latency_seconds` - API call latency (p50, p95, p99)
- `trader_api_errors_total{endpoint="..."}` - API errors by endpoint
- `trader_cycle_duration_seconds` - Main loop duration
- `trader_data_staleness_seconds` - Age of latest data

---

## Dashboard Panels

The included dashboard provides:

1. **Account Value (graph)** - Portfolio value over time
2. **Daily PnL % (graph)** - Daily percentage returns
3. **Open Positions (stat)** - Current position count
4. **Exposure % (gauge)** - Risk exposure with color thresholds:
   - Green: <70%
   - Yellow: 70-90%
   - Red: >90%
5. **Max Drawdown (stat)** - Worst peak-to-trough decline
6. **Total Trades (stat)** - Cumulative trade count
7. **Risk Rejections by Reason (graph)** - Why proposals were blocked
8. **API Latency p95 (graph)** - 95th percentile response times
9. **Circuit Breaker Trips (graph)** - Safety shutdowns over time
10. **Cycle Duration p95 (graph)** - Main loop execution time

---

## Querying with PromQL

Access Prometheus at http://localhost:9090 to run custom queries:

```promql
# Current exposure
trader_exposure_pct

# Trade win rate (last 24h)
sum(rate(trader_trades_total{outcome="win"}[24h])) / 
sum(rate(trader_trades_total[24h]))

# API error rate (last 5m)
rate(trader_api_errors_total[5m])

# P95 cycle duration (last 1h)
histogram_quantile(0.95, rate(trader_cycle_duration_seconds_bucket[1h]))
```

---

## Alerting (Optional)

### Grafana Alerts

Create alerts in Grafana for:

1. **High Exposure**: `trader_exposure_pct > 90`
2. **Large Drawdown**: `trader_max_drawdown_pct > 15`
3. **Data Staleness**: `trader_data_staleness_seconds > 300`
4. **High API Errors**: `rate(trader_api_errors_total[5m]) > 5`
5. **Circuit Breaker**: `increase(trader_circuit_breaker_trips_total[5m]) > 0`

Configure notification channels (Slack, email, PagerDuty) in **Alerting** → **Notification channels**.

---

## Maintenance

### View Logs

```bash
# Prometheus logs
docker logs trader-prometheus

# Grafana logs
docker logs trader-grafana
```

### Stop Services

```bash
docker-compose -f docker-compose.monitoring.yml down
```

### Restart Services

```bash
docker-compose -f docker-compose.monitoring.yml restart
```

### Data Persistence

- **Prometheus data**: Stored in `prometheus_data` Docker volume (30-day retention)
- **Grafana config**: Stored in `grafana_data` Docker volume

To wipe data:

```bash
docker-compose -f docker-compose.monitoring.yml down -v
```

---

## Troubleshooting

### Bot metrics not showing

1. Check bot is exposing metrics:
   ```bash
   curl http://localhost:8000/metrics
   ```
   Should return Prometheus format metrics.

2. Verify `config/app.yaml`:
   ```yaml
   prometheus_enabled: true
   prometheus_port: 8000
   ```

3. Check Prometheus is scraping:
   - Open http://localhost:9090/targets
   - Status should be **UP**

### Grafana dashboard empty

1. Verify data source connection:
   - **Configuration** → **Data Sources** → **Prometheus** → **Save & Test**

2. Check time range (top-right) - data might be outside window

3. Run query in Prometheus UI first to verify metrics exist

### Docker issues

```bash
# Check container status
docker ps

# View resource usage
docker stats

# Restart stack
docker-compose -f docker-compose.monitoring.yml restart
```

---

## Production Considerations

### Security

1. **Change Grafana password** (default: admin/admin)
2. **Enable authentication** on Prometheus (reverse proxy)
3. **Use HTTPS** for external access
4. **Restrict ports** with firewall rules

### Scaling

- Prometheus retention: Adjust `--storage.tsdb.retention.time=30d` in `docker-compose.monitoring.yml`
- Remote storage: Configure Prometheus remote_write for long-term storage (Thanos, Cortex, Mimir)

### Backups

```bash
# Backup Grafana dashboards
docker exec trader-grafana grafana-cli admin export-dashboard

# Backup Prometheus data
docker run --rm -v prometheus_data:/data -v $(pwd):/backup alpine tar czf /backup/prometheus-backup.tar.gz /data
```

---

## Integration with Code

The `PrometheusExporter` class in `infra/prometheus_exporter.py` is integrated into `runner/main_loop.py`:

```python
from infra.prometheus_exporter import get_exporter

exporter = get_exporter()  # Singleton
exporter.start()  # Start HTTP server

# In main loop
exporter.update_from_cycle_stats(stats)
exporter.record_trade(symbol, side, pnl, ...)

# In execution
exporter.record_order_placed(symbol, side, size)
exporter.record_order_filled(symbol, side, size, fill_pct)

# In risk engine
exporter.record_risk_rejection(reason)
exporter.record_circuit_breaker(reason)
```

---

## Resources

- [Prometheus Documentation](https://prometheus.io/docs/)
- [Grafana Dashboards](https://grafana.com/docs/grafana/latest/dashboards/)
- [PromQL Queries](https://prometheus.io/docs/prometheus/latest/querying/basics/)
- [Grafana Alerting](https://grafana.com/docs/grafana/latest/alerting/)
