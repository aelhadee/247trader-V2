# Latency Tracking & Monitoring

**Status:** ✅ Production-ready  
**Feature:** Comprehensive latency tracking for API calls, decision cycle phases, and submission pipeline  
**Implementation Date:** 2025-11-15

## Overview

The latency tracking system provides detailed visibility into API call performance, decision cycle timings, and submission pipeline metrics. This is essential for:

- **Watchdog timers**: Detecting runaway operations and hung API calls
- **Alerting accuracy**: Identifying performance degradation before it impacts trading
- **Capacity planning**: Understanding system bottlenecks and scaling needs
- **SLA monitoring**: Tracking adherence to latency budgets

## Architecture

### Components

1. **`infra/latency_tracker.py`**: Core tracking infrastructure
   - `LatencyTracker`: Main tracker class with per-operation statistics
   - `LatencyStats`: Aggregated statistics (count, min, max, mean, p50, p95, p99)
   - `LatencyMeasurement`: Individual measurement with timestamp and metadata
   - Context manager for automatic timing: `with tracker.measure("operation"):`

2. **Instrumentation Points**:
   - **CoinbaseExchange**: All API endpoints (automatically via `_record_api_metrics`)
   - **TradingLoop**: Decision cycle stages (via `_stage_timer` context manager)
   - **StateStore**: Latency stats persistence for historical analysis

3. **Alerting Integration**:
   - Threshold violations trigger alerts via `AlertService`
   - Configurable per-operation and aggregate thresholds
   - Alert after N consecutive violations to reduce noise

## Configuration

### Policy Configuration (`config/policy.yaml`)

```yaml
latency:
  # API call latency thresholds in milliseconds
  api_thresholds_ms:
    api_list_products: 2000
    api_get_accounts: 1500
    api_list_open_orders: 1500
    api_list_fills: 2000
    api_get_quote: 500
    api_place_order: 2000
    api_cancel_order: 1500
    api_preview_order: 1000
  
  # Decision cycle stage budgets in seconds
  stage_budgets:
    order_reconcile: 2.0
    universe_build: 6.0
    trigger_scan: 6.0
    rules_engine: 4.0
    risk_engine: 4.0
    execution: 15.0
    open_order_maintenance: 3.0
    exit_checks: 3.0
    exit_execution: 3.0
  
  # Total cycle budget in seconds
  total_seconds: 45.0
  
  # Alert after N consecutive violations
  alert_after_violations: 3
```

### Threshold Tuning

**API Thresholds**: Set based on observed P95 latency + 50% headroom
- Too tight: False positive alerts during market volatility
- Too loose: Miss genuine performance degradation

**Stage Budgets**: Based on worst-case expected duration
- Universe build: 6s (dynamic discovery + metadata fetch)
- Trigger scan: 6s (OHLCV fetch for all symbols)
- Rules engine: 4s (proposal generation + sizing)
- Risk engine: 4s (exposure checks + circuit breakers)
- Execution: 15s (order preview + placement + confirmation)

**Total Budget**: 45s hard limit (60s cycle interval - 15s buffer)

## Usage

### Automatic Tracking

Latency tracking is automatically enabled for:

1. **API calls** (via `CoinbaseExchange._record_api_metrics`)
2. **Decision cycle stages** (via `TradingLoop._stage_timer`)

No manual instrumentation needed for standard operations.

### Manual Tracking

For custom operations:

```python
from infra.latency_tracker import get_global_tracker

tracker = get_global_tracker()

# Context manager (recommended)
with tracker.measure("custom_operation", metadata={"param": value}):
    result = expensive_operation()

# Manual recording
tracker.record("custom_operation", duration_ms=123.45, metadata={"status": "success"})
```

### Retrieving Statistics

```python
# Get stats for single operation
stats = tracker.get_stats("api_get_quote")
print(f"Mean: {stats.mean_ms:.2f}ms, P95: {stats.p95_ms:.2f}ms")

# Get all operations
all_stats = tracker.get_all_stats()
for operation, stats in all_stats.items():
    print(f"{operation}: {stats.mean_ms:.2f}ms")

# Check threshold
violation = tracker.check_threshold("api_get_quote", threshold_ms=500.0)
if violation:
    print(f"Threshold exceeded: {violation:.2f}ms")

# Get recent measurements
recent = tracker.get_recent_measurements("api_get_quote", limit=10)
for m in recent:
    print(f"{m.timestamp}: {m.duration_ms:.2f}ms")
```

### StateStore Integration

Latency stats are persisted in StateStore on every cycle:

```python
# Automatic (called by TradingLoop)
self.state_store.update_latency_stats(self.latency_tracker.to_state_dict())

# Manual retrieval
latency_data = self.state_store.get_latency_stats()
operations = latency_data.get("operations", {})
```

## Metrics Exposed

### Per-Operation Statistics

For each tracked operation:
- **count**: Total measurements
- **min_ms**: Minimum latency
- **max_ms**: Maximum latency
- **mean_ms**: Average latency
- **p50_ms**: Median (50th percentile)
- **p95_ms**: 95th percentile
- **p99_ms**: 99th percentile
- **last_timestamp**: Timestamp of most recent measurement

### Operation Categories

**API Calls** (automatically tracked):
- `api_list_products`
- `api_get_accounts`
- `api_list_open_orders`
- `api_list_fills`
- `api_get_quote`
- `api_place_order`
- `api_cancel_order`
- `api_preview_order`
- `api_get_product_metadata`
- `api_get_orderbook`
- `api_get_ohlcv`

**Decision Cycle Stages** (automatically tracked):
- `cycle_pending_purge`
- `cycle_state_reconcile`
- `cycle_order_reconcile`
- `cycle_portfolio_snapshot`
- `cycle_universe_build`
- `cycle_trigger_scan`
- `cycle_rules_engine`
- `cycle_risk_engine`
- `cycle_execution`
- `cycle_open_order_maintenance`
- `cycle_exit_checks`
- `cycle_exit_execution`

## Alerting

### Alert Triggers

1. **API Latency Threshold Exceeded**
   - Severity: `WARNING`
   - Trigger: Mean latency > threshold for operation
   - Message: `"API latency threshold exceeded: api_get_quote 750.0ms > 500ms"`

2. **Stage Budget Exceeded**
   - Severity: `WARNING`
   - Trigger: Stage duration > budget
   - Message: `"Latency budget exceeded: trigger_scan 8.50s>6.00s"`

3. **Total Cycle Budget Exceeded**
   - Severity: `WARNING`
   - Trigger: Total cycle duration > total_seconds
   - Message: `"Latency budget exceeded: total 52.00s>45.00s"`

### Alert Payload

Includes detailed context for troubleshooting:

```json
{
  "violations": [
    {
      "operation": "api_get_quote",
      "mean_ms": 750.0,
      "threshold_ms": 500
    }
  ],
  "total_duration": 52.0,
  "total_budget": 45.0
}
```

## Monitoring Dashboard Queries

### Prometheus Metrics (if MetricsRecorder enabled)

```promql
# API latency P95
histogram_quantile(0.95, 
  rate(api_call_duration_seconds_bucket[5m])
) by (endpoint)

# Stage duration P95
histogram_quantile(0.95,
  rate(stage_duration_seconds_bucket[5m])
) by (stage)

# Threshold violations
rate(latency_threshold_violations_total[5m])
```

### Log Analysis

```bash
# API latency violations
grep "API latency threshold exceeded" logs/247trader-v2.log

# Stage budget violations
grep "Latency budget exceeded" logs/247trader-v2.log | grep "cycle_"

# Total cycle violations
grep "Latency budget exceeded" logs/247trader-v2.log | grep "total"
```

## Troubleshooting

### High API Latency

**Symptoms**: `api_*` operations consistently exceeding thresholds

**Common Causes**:
1. Network latency/packet loss
2. Coinbase API degradation
3. Rate limiting (429 responses)
4. DNS resolution delays

**Remediation**:
```bash
# Check network connectivity
ping api.coinbase.com

# Check DNS resolution
time nslookup api.coinbase.com

# Monitor Coinbase status
curl -s https://status.coinbase.com/api/v2/status.json | jq .

# Review API error rates
grep "HTTPError" logs/247trader-v2.log | tail -20
```

### Slow Decision Cycles

**Symptoms**: `cycle_*` stages exceeding budgets

**Common Causes**:
1. Large universe (too many symbols)
2. Stale product metadata cache
3. Excessive OHLCV fetches
4. Complex risk calculations

**Remediation**:
```bash
# Check universe size
grep "Universe built" logs/247trader-v2.log | tail -1

# Review trigger scan timing
grep "Step 2: Scanning triggers" logs/247trader-v2.log | tail -5

# Analyze stage breakdown
grep "Latency summary" logs/247trader-v2.log | tail -5
```

### Total Cycle Timeout

**Symptoms**: Cycle duration > 45s

**Impact**: Misses next cycle interval, accumulates backlog

**Emergency Actions**:
1. Increase `loop.interval_seconds` to 120s
2. Reduce `max_open_positions` to lighten load
3. Disable `exits.enabled` temporarily if exit checks are slow
4. Consider increasing `latency.total_seconds` if consistently hitting limit

## Performance Impact

### Memory Overhead

- **Per operation**: ~1KB for 1000 measurements (default retention)
- **Total**: ~50KB for 50 tracked operations
- Negligible impact on overall system memory

### CPU Overhead

- **Context manager**: ~1µs per operation (timing overhead)
- **Statistics calculation**: ~10µs per operation (on-demand)
- **Percentile calculation**: ~50µs for 1000 measurements
- Total impact: <0.1% of cycle time

### Disk Overhead

- **StateStore updates**: ~5KB per cycle (latency stats JSON)
- **Audit logs**: No additional size (stage_latencies already logged)

## Testing

### Unit Tests

Run comprehensive test suite:

```bash
pytest tests/test_latency_tracker.py -v
```

Coverage:
- Basic measurement recording
- Context manager timing
- Exception handling (timing continues even on error)
- Percentile calculations
- Threshold checking
- State dict serialization
- Multi-operation tracking
- Global singleton behavior

### Integration Tests

Verify end-to-end latency tracking:

```bash
# Start bot in DRY_RUN mode
python -m runner.main_loop --mode DRY_RUN --cycles 3

# Check latency stats in state
python -c "
from infra.state_store import get_state_store
store = get_state_store()
stats = store.get_latency_stats()
print(stats.get('operations', {}).keys())
"
```

Expected operations:
- `api_*` (API calls)
- `cycle_*` (decision stages)

## Production Readiness

### Checklist

- [x] Core tracker implementation
- [x] Automatic instrumentation (API + cycle stages)
- [x] StateStore persistence
- [x] Alert integration
- [x] Configuration in policy.yaml
- [x] Comprehensive unit tests (26 tests)
- [x] Documentation
- [x] Threshold tuning guidelines

### Next Steps (Optional Enhancements)

1. **Persistent History**: Store latency trends in time-series DB
2. **Anomaly Detection**: ML-based detection of unusual latency patterns
3. **Adaptive Thresholds**: Auto-adjust based on P95 historical values
4. **Detailed Tracing**: Span-level tracing for complex operations
5. **Dashboard**: Grafana dashboard for real-time latency monitoring

## References

- Configuration: `config/policy.yaml` → `latency` section
- Implementation: `infra/latency_tracker.py`
- Integration: `runner/main_loop.py` → `_update_and_check_latency_thresholds()`
- Tests: `tests/test_latency_tracker.py`
- StateStore: `infra/state_store.py` → `update_latency_stats()`, `get_latency_stats()`

---

**Last Updated:** 2025-11-15  
**Version:** 1.0  
**Status:** Production-ready ✅
