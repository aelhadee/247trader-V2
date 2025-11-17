# 247trader-v2 Grafana Dashboard

Comprehensive monitoring dashboard for the 247trader-v2 crypto trading bot.

## Overview

This dashboard provides real-time visibility into:
- **Portfolio Performance**: Account value, PnL, positions, exposure
- **Strategy Metrics**: Triggers, proposals, selectivity ratio
- **Operational Health**: Rate limiting, API latency, cycle timing
- **Risk Management**: Drawdown, rejections, circuit breakers

## Quick Start

### 1. Start Prometheus Exporter (Built-in)

The bot automatically exposes Prometheus metrics on port 8000 when enabled in config:

```yaml
# config/app.yaml
monitoring:
  prometheus_enabled: true
  prometheus_port: 8000
```

Verify metrics are exposed:
```bash
curl http://localhost:8000/metrics | grep trader_
```

### 2. Install Prometheus

**macOS (Homebrew)**:
```bash
brew install prometheus
```

**Docker**:
```bash
docker run -d \
  --name prometheus \
  -p 9090:9090 \
  -v $(pwd)/monitoring/prometheus.yml:/etc/prometheus/prometheus.yml \
  prom/prometheus
```

**Linux**:
```bash
# Download latest release
wget https://github.com/prometheus/prometheus/releases/download/v2.48.0/prometheus-2.48.0.linux-amd64.tar.gz
tar xvfz prometheus-*.tar.gz
cd prometheus-*
./prometheus --config.file=prometheus.yml
```

### 3. Configure Prometheus

Create `monitoring/prometheus.yml`:

```yaml
global:
  scrape_interval: 10s
  evaluation_interval: 10s

scrape_configs:
  - job_name: '247trader-v2'
    static_configs:
      - targets: ['localhost:8000']
        labels:
          instance: 'live'
          mode: 'LIVE'
```

Reload Prometheus:
```bash
# macOS/Linux
killall -HUP prometheus

# Docker
docker restart prometheus
```

Verify scraping:
```bash
# Check targets
open http://localhost:9090/targets

# Query metrics
open http://localhost:9090/graph
```

### 4. Install Grafana

**macOS (Homebrew)**:
```bash
brew install grafana
brew services start grafana
```

**Docker**:
```bash
docker run -d \
  --name grafana \
  -p 3000:3000 \
  -e "GF_SECURITY_ADMIN_PASSWORD=admin" \
  grafana/grafana-oss
```

**Linux**:
```bash
# Ubuntu/Debian
sudo apt-get install -y software-properties-common
sudo add-apt-repository "deb https://packages.grafana.com/oss/deb stable main"
wget -q -O - https://packages.grafana.com/gpg.key | sudo apt-key add -
sudo apt-get update
sudo apt-get install grafana

sudo systemctl start grafana-server
sudo systemctl enable grafana-server
```

Access Grafana:
- URL: http://localhost:3000
- Default credentials: admin/admin (change on first login)

### 5. Add Prometheus Data Source

1. Login to Grafana (http://localhost:3000)
2. Go to **Configuration** → **Data Sources**
3. Click **Add data source**
4. Select **Prometheus**
5. Configure:
   - **Name**: `Prometheus`
   - **URL**: `http://localhost:9090` (Docker: `http://host.docker.internal:9090`)
   - **Access**: `Server (default)`
6. Click **Save & Test**

### 6. Import Dashboard

**Option A: Via UI**
1. Go to **Dashboards** → **Import**
2. Upload `monitoring/grafana_dashboard.json`
3. Select Prometheus data source
4. Click **Import**

**Option B: Via API**
```bash
curl -X POST \
  http://admin:admin@localhost:3000/api/dashboards/db \
  -H "Content-Type: application/json" \
  -d @monitoring/grafana_dashboard.json
```

**Option C: Via curl with proper format**
```bash
cat monitoring/grafana_dashboard.json | \
jq '{dashboard: ., overwrite: true}' | \
curl -X POST \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  http://localhost:3000/api/dashboards/db \
  -d @-
```

Access dashboard:
- URL: http://localhost:3000/d/247trader-v2

---

## Dashboard Panels

### Row 1: Portfolio Overview (Stats)
- **Account Value**: Current total account value in USD
- **Daily PnL %**: Today's profit/loss percentage
- **Open Positions**: Number of active positions
- **Exposure %**: Total exposure as % of account (red if >25%)

### Row 2: Performance Charts
- **Account Value & PnL Over Time**: Line chart showing account growth and daily PnL
- **Risk Metrics**: Exposure % and max drawdown % over time

### Row 3: Strategy Metrics (Stats)
- **Trades Per Hour**: Rolling trade frequency (alert if >10)
- **Triggers Detected**: Signals found in last cycle
- **Proposals Generated**: Trades proposed from triggers
- **Strategy Selectivity**: Gauge showing proposals/triggers ratio (0.2-0.5 healthy)

### Row 4: Strategy Performance
- **Triggers vs Proposals Over Time**: Line chart comparing signal generation to proposal conversion
- **Rate Limiter Utilization**: Per-endpoint API quota usage (warn at 80%, critical at 90%)

### Row 5: Operational Metrics
- **Trade Rate**: Trades per 5 minutes (by side and symbol)
- **Cycle Duration**: Trading loop execution time (should be <45s)

### Row 6: Health & Errors
- **Risk Rejections**: Rejected trades by reason (exposure_cap, cooldown, etc.)
- **API Latency (p95)**: 95th percentile API response times by endpoint

---

## Alert Configuration

### Recommended Alerts

Create alerts in Grafana for critical conditions:

#### 1. High Exposure
```promql
trader_exposure_pct > 25
```
**Action**: System approaching exposure cap, may start rejecting trades

#### 2. Max Drawdown Warning
```promql
trader_max_drawdown_pct > 8
```
**Action**: Approaching 10% drawdown limit, risk of circuit breaker

#### 3. Rate Limit Critical
```promql
max(trader_rate_limiter_utilization_pct) > 90
```
**Action**: API throttling imminent, reduce request frequency

#### 4. No Trades (Signal Drought)
```promql
increase(trader_trades_total[1h]) == 0 AND trader_triggers_detected == 0
```
**Action**: Zero signals for extended period, check trigger thresholds

#### 5. Excessive Trading
```promql
trader_trades_per_hour > 10
```
**Action**: Trading too frequently, risk exhausting limits

#### 6. Cycle Duration Timeout
```promql
trader_cycle_duration_seconds > 45
```
**Action**: Cycle taking too long, may skip intervals

#### 7. Low Strategy Selectivity
```promql
trader_triggers_to_proposals_ratio < 0.1 AND trader_triggers_detected > 5
```
**Action**: Strategy too conservative, missing opportunities

#### 8. High Strategy Selectivity
```promql
trader_triggers_to_proposals_ratio > 0.8
```
**Action**: Strategy too aggressive, low signal quality

### Setting Up Alerts

1. **In Grafana**:
   - Go to panel → Edit → Alert tab
   - Configure alert rule with query above
   - Set evaluation interval (e.g., 1 minute)
   - Configure notification channels (Slack, email, PagerDuty)

2. **In Prometheus**:
   - Edit `prometheus.yml`
   - Add alerting rules in `rules` section
   - Configure Alertmanager for notifications

Example Prometheus alert rule:
```yaml
# prometheus_alerts.yml
groups:
  - name: 247trader
    interval: 30s
    rules:
      - alert: HighExposure
        expr: trader_exposure_pct > 25
        for: 2m
        labels:
          severity: warning
        annotations:
          summary: "High exposure detected"
          description: "Exposure at {{ $value }}% (limit 25%)"
      
      - alert: RateLimitCritical
        expr: max(trader_rate_limiter_utilization_pct) > 90
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "Rate limit critical"
          description: "API utilization at {{ $value }}%"
```

---

## Metrics Reference

### Portfolio Metrics
| Metric | Type | Description |
|--------|------|-------------|
| `trader_account_value_usd` | Gauge | Total account value in USD |
| `trader_daily_pnl_usd` | Gauge | Daily profit/loss in USD |
| `trader_daily_pnl_pct` | Gauge | Daily profit/loss percentage |
| `trader_open_positions` | Gauge | Number of open positions |
| `trader_exposure_pct` | Gauge | Total exposure as % of account |
| `trader_max_drawdown_pct` | Gauge | Maximum drawdown percentage |

### Strategy Metrics (New)
| Metric | Type | Description |
|--------|------|-------------|
| `trader_trades_per_hour` | Gauge | Rolling trades per hour count |
| `trader_triggers_detected` | Gauge | Triggers detected in last cycle |
| `trader_proposals_generated` | Gauge | Proposals from triggers |
| `trader_triggers_to_proposals_ratio` | Gauge | Strategy selectivity (0.0-1.0) |

### Operational Metrics (New)
| Metric | Type | Description |
|--------|------|-------------|
| `trader_rate_limiter_utilization_pct` | Gauge | Rate limit usage by endpoint |
| `trader_cycle_duration_seconds` | Histogram | Cycle execution time |
| `trader_api_latency_seconds` | Histogram | API call latency by endpoint |

### Trading Metrics
| Metric | Type | Description |
|--------|------|-------------|
| `trader_trades_total` | Counter | Total trades executed (by side/symbol) |
| `trader_orders_placed_total` | Counter | Orders placed (by side/symbol) |
| `trader_orders_filled_total` | Counter | Orders filled (by side/symbol) |

### Risk Metrics
| Metric | Type | Description |
|--------|------|-------------|
| `trader_risk_rejections_total` | Counter | Trades rejected by risk engine |
| `trader_circuit_breaker_trips` | Counter | Circuit breaker activations |

---

## Troubleshooting

### Metrics Not Showing

1. **Check bot is running**:
   ```bash
   ps aux | grep "[p]ython.*main_loop"
   ```

2. **Verify Prometheus endpoint**:
   ```bash
   curl http://localhost:8000/metrics | head -20
   ```

3. **Check Prometheus scraping**:
   - Go to http://localhost:9090/targets
   - Should show `247trader-v2` as UP
   - If DOWN, check firewall and bot status

4. **Verify data in Prometheus**:
   ```bash
   # Query metrics
   curl 'http://localhost:9090/api/v1/query?query=trader_account_value_usd'
   ```

### Dashboard Shows "No Data"

1. **Check time range**: Set to "Last 6 hours" (dashboard default)
2. **Verify data source**: Should be "Prometheus" in dropdown
3. **Inspect panel queries**: Edit panel → Check PromQL syntax
4. **Check bot has generated data**: Run at least one trading cycle

### Alerts Not Firing

1. **Verify alert rules**: Check Prometheus rules file loaded
2. **Check evaluation**: Go to Prometheus → Alerts tab
3. **Verify Alertmanager**: Check notification channels configured
4. **Test manually**: Trigger condition and verify alert fires

### High Rate Limit Warnings Persist

Despite product caching optimization:
1. **Check cache is enabled**:
   ```bash
   grep products_cache_minutes config/universe.yaml
   # Should show: products_cache_minutes: 5
   ```
2. **Verify cache hits in logs**:
   ```bash
   tail -f logs/*.log | grep "Products cache HIT"
   ```
3. **Increase cache TTL** if safe:
   ```yaml
   # config/universe.yaml
   universe:
     products_cache_minutes: 15  # Increase from 5
   ```

---

## Customization

### Adding Custom Panels

1. **In Grafana**: Click "Add panel" → "Add a new panel"
2. **Query builder**: Enter PromQL expression
3. **Visualization**: Choose panel type (graph, stat, gauge, etc.)
4. **Format**: Configure axes, legends, thresholds
5. **Save**: Add to dashboard

Example custom query:
```promql
# Win rate (last 24h)
sum(increase(trader_trades_total{side="SELL"}[24h])) 
/ 
sum(increase(trader_trades_total[24h]))

# Average trade size
avg(trader_position_value_usd)

# Trades by symbol (top 10)
topk(10, sum by (symbol) (increase(trader_trades_total[6h])))
```

### Modifying Thresholds

Edit panel → Field tab → Thresholds:
- Green: Healthy
- Yellow: Warning (e.g., exposure >20%)
- Red: Critical (e.g., exposure >25%)

### Changing Refresh Rate

Dashboard settings → Time options → Refresh:
- Options: 5s, 10s, 30s, 1m, 5m
- Recommended: 10s for LIVE trading

---

## Best Practices

1. **Monitor during first 24 hours**: Watch for anomalies after deployment
2. **Set up alerts**: Don't rely on manual monitoring
3. **Review weekly**: Check trends in selectivity, latency, rejections
4. **Correlate events**: Match dashboard spikes with audit logs
5. **Baseline metrics**: Note normal ranges for alerts
6. **Backup dashboards**: Export JSON regularly
7. **Use annotations**: Mark deployments, config changes

---

## Performance Notes

- **Scrape interval**: 10s recommended (balance freshness vs load)
- **Retention**: Prometheus default is 15 days (adjust in prometheus.yml)
- **Query optimization**: Use recording rules for expensive queries
- **Resource usage**: Prometheus ~100MB RAM, Grafana ~50MB RAM

---

## Support

- **Dashboard issues**: Check Grafana logs: `/var/log/grafana/grafana.log`
- **Prometheus issues**: Check logs: `/var/log/prometheus.log`
- **Bot metrics issues**: Check bot logs: `logs/247trader-v2.log`

For optimization recommendations, see: `docs/OPTIMIZATIONS_2025-11-16.md`
