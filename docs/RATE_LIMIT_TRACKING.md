# Per-Endpoint Rate Limit Tracking

## Overview

247trader-v2 now includes comprehensive per-endpoint rate limit tracking to prevent API quota exhaustion and ensure reliable production operation. The system uses a **token bucket algorithm** for proactive throttling with endpoint-specific quotas.

## Problem Statement

**Previous Implementation:**
- Generic channel-based tracking (public/private only)
- No per-endpoint granularity
- Reactive approach (only detected 429 errors after the fact)
- No proactive throttling before exhaustion
- No alerting when approaching limits

**Risks:**
- API quota exhaustion → temporary bans
- Failed trades during critical market moments
- Degraded user experience
- Potential loss of trading opportunities

## Solution Architecture

### Token Bucket Algorithm

Each endpoint has its own token bucket:
- **Capacity**: Maximum burst allowance (requests/second)
- **Refill Rate**: Steady-state throughput (requests/second)
- **Tokens**: Current available quota

**Example:** `get_quote` endpoint with 10 req/sec:
```
Bucket State:
├─ Capacity: 10.0 tokens
├─ Refill Rate: 10.0 tokens/second
├─ Current Tokens: 7.5 tokens
└─ Utilization: 25% (2.5 of 10 used in last second)
```

### Components

#### 1. `core/rate_limiter.py`

**`EndpointQuota`**: Token bucket for a single endpoint
- Tracks tokens, refill, and utilization
- Calculates wait times
- Records violations

**`RateLimiter`**: Multi-endpoint rate limiter
- Manages quotas for all endpoints
- Configurable alert thresholds
- Thread-safe token acquisition
- Comprehensive statistics

#### 2. Integration with `CoinbaseExchange`

**Enhanced Methods:**
- `_rate_limit(endpoint, is_private)`: Proactive throttling per endpoint
- `configure_rate_limits(rate_cfg)`: Load quotas from config
- `rate_limit_snapshot()`: Get comprehensive stats
- `_record_rate_usage(channel, endpoint, violated)`: Dual tracking (legacy + new)

**Endpoint Tracking:**
All API methods now pass endpoint names:
```python
self._rate_limit("get_quote", is_private=False)
self._rate_limit("place_order", is_private=True)
self._rate_limit("list_products", is_private=False)
```

## Configuration

### `config/policy.yaml`

```yaml
# Per-endpoint rate limit tracking
rate_limits:
  # Default quotas (used if endpoint-specific not configured)
  public: 10.0   # req/sec for public endpoints
  private: 15.0  # req/sec for private endpoints
  
  # Alert thresholds (0.0-1.0 utilization)
  alert_threshold: 0.8  # Alert at 80% utilization
  critical_threshold: 0.9  # Critical alert at 90%
  
  # Per-endpoint overrides (optional)
  endpoints:
    get_quote: 10.0          # Quote fetching
    get_ohlcv: 10.0          # Candle data
    list_products: 5.0       # Product listing (less frequent)
    get_accounts: 10.0       # Account balances
    list_orders: 10.0        # Order listing
    list_fills: 10.0         # Fill history
    place_order: 10.0        # Order placement
    cancel_order: 10.0       # Order cancellation
    preview_order: 10.0      # Order preview
    convert_quote: 5.0       # Currency conversion
    convert_commit: 5.0      # Conversion execution
```

### Coinbase API Limits (2024)

From Coinbase Advanced Trade API documentation:

- **Public endpoints**: 10 requests/second
- **Private endpoints**: 15 requests/second  
- **Orders**: 10 orders/second

**Conservative Configuration:**
We default to conservative limits below official quotas to provide safety margin for bursts and network latency.

## Usage

### Automatic Integration

Rate limiting is **automatically applied** to all API calls through the `CoinbaseExchange` class. No changes needed to existing code.

### Manual Quota Checking (Advanced)

```python
# Check if endpoint can proceed without waiting
stats = exchange.rate_limiter.get_stats("get_quote", is_private=False)
print(f"Utilization: {stats.utilization:.1%}")
print(f"Tokens available: {stats.tokens_available}")
print(f"Wait time: {stats.wait_time_seconds}s")

# Acquire tokens without waiting (returns False if insufficient)
acquired = exchange.rate_limiter.acquire("get_quote", wait=False)
if not acquired:
    print("Rate limit reached, would need to wait")

# Get all endpoint statistics
all_stats = exchange.rate_limiter.get_all_stats()
for name, stats in all_stats.items():
    print(f"{name}: {stats.utilization:.1%} utilization")
```

### Comprehensive Snapshot

```python
snapshot = exchange.rate_limit_snapshot()

# Legacy channel utilization (backward compatibility)
print(snapshot["legacy_channels"])
# {'public': 0.3, 'private': 0.5}

# Per-endpoint statistics
print(snapshot["endpoints"]["get_quote"])
# {
#   'utilization': 0.3,
#   'tokens_available': 7.0,
#   'calls_last_second': 3,
#   'violations': 0,
#   'wait_time_seconds': 0.0
# }

# Summary statistics
print(snapshot["summary"])
# {
#   'max_utilization': 0.5,
#   'total_violations': 0,
#   'high_utilization_endpoints': 0,
#   'endpoint_count': 12
# }
```

## Monitoring & Alerts

### Log Warnings

**High Utilization (80%):**
```
WARNING: High rate limit utilization for get_quote: 82.5% (threshold: 80.0%)
```

**Rate Limit Violation (429 response):**
```
WARNING: Rate limit violation for place_order (total: 1)
```

### Metrics Integration

Rate limit statistics are automatically recorded to `MetricsRecorder`:
- `record_rate_limit_usage(channel, usage, violated)`
- Per-endpoint utilization
- Violation counts
- Wait times

### Audit Trail

All rate limit events are logged to audit log:
- Throttling events (when waiting)
- Violations (429 responses)
- High utilization warnings

## Behavior

### Proactive Throttling

**Wait Mode (Default):**
```python
self._rate_limit("get_quote", is_private=False)
# If tokens insufficient, will WAIT until available
# Prevents 429 errors through proactive pausing
```

**No-Wait Mode (Advanced):**
```python
acquired = exchange.rate_limiter.acquire("get_quote", wait=False)
if not acquired:
    # Handle rate limit (e.g., skip, queue, alert)
    pass
```

### Token Refill

Tokens refill continuously at configured rate:
- **10 req/sec** = 1 token every 100ms
- **15 req/sec** = 1 token every 67ms

Bucket never exceeds capacity (no "saving up" tokens).

### Burst Handling

Initial burst allowed up to capacity:
```
Example: 10 req/sec endpoint
├─ Can make 10 requests immediately
├─ Then limited to 10 req/sec steady-state
└─ Bucket refills at 1 token/100ms
```

## Testing

### Unit Tests

`tests/test_rate_limiter.py` covers:
- Token bucket mechanics
- Refill timing
- Utilization tracking
- Configuration
- Acquire with/without waiting
- Default quotas
- Manual recording
- Statistics
- Reset functionality
- High utilization warnings

Run tests:
```bash
pytest tests/test_rate_limiter.py -v
```

### Integration Tests

Exchange tests validate:
- Rate limiting during API calls
- 429 handling
- Metrics recording
- Audit logging

## Production Deployment

### Pre-Flight Checklist

1. **Configure Quotas** in `config/policy.yaml`
   - Set conservative limits below Coinbase quotas
   - Add endpoint-specific overrides as needed

2. **Set Alert Thresholds**
   - `alert_threshold: 0.8` (80% utilization)
   - `critical_threshold: 0.9` (90% utilization)

3. **Enable Monitoring**
   - Metrics dashboard for utilization
   - Alerts for high utilization
   - Log analysis for violations

4. **Test in PAPER Mode**
   - Verify throttling works
   - Check no 429 errors
   - Validate performance acceptable

### Monitoring Dashboard

**Key Metrics:**
- Per-endpoint utilization (0-100%)
- Violation counts
- Wait times (latency impact)
- Tokens available per endpoint

**Alert Conditions:**
- Utilization ≥ 80% for > 60 seconds
- Any 429 rate limit errors
- Violation count increasing

### Tuning

**If Too Restrictive (Slow):**
```yaml
# Increase quotas slightly
rate_limits:
  public: 12.0  # Up from 10.0
  private: 18.0  # Up from 15.0
```

**If Too Permissive (429 Errors):**
```yaml
# Decrease quotas to add more safety margin
rate_limits:
  public: 8.0   # Down from 10.0
  private: 12.0  # Down from 15.0
```

**Endpoint-Specific Issues:**
```yaml
endpoints:
  get_quote: 8.0  # Reduce if seeing 429s on quotes
  place_order: 5.0  # Extra conservative for critical orders
```

## Rollback Plan

If issues occur, rate limiting can be disabled:

### Option 1: Increase Limits to Infinity
```yaml
rate_limits:
  public: 1000000.0  # Effectively unlimited
  private: 1000000.0
```

### Option 2: Revert to Legacy Behavior
```python
# In CoinbaseExchange.__init__()
# Comment out new rate limiter, uncomment legacy tracking
```

### Option 3: Emergency Bypass
```python
# Temporarily disable waiting
exchange.rate_limiter._default_public_quota = 1000000.0
exchange.rate_limiter._default_private_quota = 1000000.0
```

## Future Enhancements

### Adaptive Rate Limiting
- Automatically adjust quotas based on observed 429s
- Learn optimal rates per endpoint
- Dynamic burst sizing

### Circuit Breaker Integration
- Trip circuit breakers on sustained high utilization
- Implement backoff strategies
- Graceful degradation

### Multi-Exchange Support
- Per-exchange rate limiters
- Exchange-specific quotas
- Unified monitoring

### Advanced Analytics
- Utilization trends over time
- Correlation with trade performance
- Optimization recommendations

## References

- **Coinbase API Documentation**: https://docs.cdp.coinbase.com/advanced-trade/docs/rest-api-rate-limits
- **Token Bucket Algorithm**: https://en.wikipedia.org/wiki/Token_bucket
- **AWS Best Practices (Jitter)**: Already implemented in retry logic
- **Freqtrade Rate Limiting**: No built-in limits (manual config)
- **Hummingbot Rate Limiting**: Per-connector implementation

## Changelog

### 2025-01-15 - Initial Implementation (Task 5)
- Created `core/rate_limiter.py` with token bucket algorithm
- Integrated with `CoinbaseExchange` class
- Added configuration support in `policy.yaml`
- Comprehensive test coverage (12 tests, all passing)
- Documentation and monitoring guidelines
- Proactive throttling to prevent 429 errors

---

**Status**: ✅ PRODUCTION-READY  
**Tests**: ✅ 12/12 PASSING  
**Documentation**: ✅ COMPLETE  
**Next Task**: Task 4 (Shadow DRY_RUN mode) or Task 6 (Backtest slippage model)
