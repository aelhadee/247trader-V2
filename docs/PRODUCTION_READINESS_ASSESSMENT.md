# Production Readiness Assessment - 247trader-v2

**Date:** November 11, 2025  
**Status:** ⚠️ **NOT PRODUCTION READY** - Critical bug + safety gaps identified

---

## 🔴 CRITICAL BUG DISCOVERED

### AlertService Missing Initialization

**Location:** `core/risk.py:151-167`  
**Severity:** **CRITICAL** - Crashes on safety triggers  
**Impact:** Kill switch, stop losses, and critical alerts will cause `AttributeError` crashes instead of protection

**Problem:**
```python
# __init__ never defines self.alert_service
def __init__(self, policy, universe_manager=None, exchange=None, state_store=None):
    # ... no self.alert_service = None ...

# But code references it in 8+ places:
if self.alert_service:  # AttributeError if not defined!
    self.alert_service.send_alert(...)
```

**Fix Applied:**
```python
def __init__(self, policy, universe_manager=None, exchange=None, state_store=None, alert_service=None):
    # ...
    self.alert_service = alert_service  # NOW DEFINED
```

**Status:** ✅ Fixed (optional parameter, backward compatible)

---

## 🔴 PRODUCTION SAFETY GAPS

Per PRODUCTION_TODO.md, these items are **incorrectly categorized** as "medium priority":

### 1. Latency Accounting (Critical TODOs line 56)
**Impact:** **HIGH**  
**Category:** Production Safety (NOT operational improvement)

**Why Critical:**
- Watchdog timers need accurate latency data to detect stuck loops
- SLA monitoring requires latency baselines for degradation detection  
- Without this: Silent failures, missed circuit breaker triggers, undetected API slowdowns

**Required Before Scale:**
- Track API call latency (per endpoint)
- Track decision cycle duration
- Track order submission pipeline timing
- Set alerting thresholds (e.g., >5s cycle time)

---

### 2. Jittered Scheduling (Critical TODOs line 57)
**Impact:** MEDIUM-HIGH  
**Category:** Production Safety (rate limit protection)

**Why Important:**
- Synchronized cycles create API burst patterns
- Burst patterns increase 429 rate limit risk
- Multiple instances can amplify the problem

**Required Before Multi-Instance:**
- Add random jitter to sleep intervals (±20% of cycle time)
- Prevents "thundering herd" to Coinbase APIs
- Reduces rate limit exhaustion probability

**Workaround:** Safe for single-instance with current backoff

---

### 3. Canonical Symbol Mapping (Data Integrity line 68)
**Impact:** MEDIUM  
**Category:** Data Quality

**Why Matters:**
- Exchange uses `BTC-USD`, internal code may use `BTCUSD`
- Mismatches cause: order rejections, reconciliation failures, PnL errors
- Current: Likely okay (Coinbase format used consistently)

**Required Before:**
- Integrating external data sources
- Adding new exchanges
- Cross-system reconciliation

**Workaround:** Manual review shows consistent `BTC-USD` format usage

---

### 4. UTC/Monotonic Time Sanity (Data Integrity line 69)
**Impact:** MEDIUM  
**Category:** Data Quality & Reliability

**Why Matters:**
- `datetime.utcnow()` deprecated (naive timestamps)
- Bar windowing needs explicit boundaries (avoid off-by-one)
- Clock skew can cause stale data acceptance

**Required Before:**
- Multi-day backtests
- Historical analysis
- Debugging timestamp-related issues

**Current Risk:** Low (30-60s staleness checks provide safety margin)

---

## 🟡 OPERATIONAL GAPS (MUST FIX BEFORE SCALE)

### 5. Alert Webhooks & Routing (Observability line 108)
**Impact:** **CRITICAL FOR OPERATIONS**  
**Status:** 🔴 **BLOCKER BEFORE $10K+**

**Missing:**
- Slack/email webhook configuration
- On-call routing tested
- Alert severity levels calibrated
- Escalation paths defined

**Why Critical:**
- Kill switch activation = no notification
- Stop loss hit = no notification  
- Circuit breaker trip = no notification
- API failures = no notification

**Action Required:**
1. Configure alert webhooks in `config/app.yaml`
2. Test staging alert (verify receipt)
3. Document on-call rotation
4. Set up PagerDuty/Opsgenie integration

---

### 6. Alert Matrix (Observability line 109)
**Impact:** **CRITICAL FOR OPERATIONS**  
**Status:** 🔴 **BLOCKER BEFORE $10K+**

**Missing Alerts:**
- Stop loss hits
- Reconciliation mismatches
- API failure spikes
- Empty universe (no eligible assets)
- Order rejection patterns
- Exception bursts

**Action Required:**
Create alert definitions with thresholds in `infra/alerting.py`

---

### 7. Metrics Dashboard (Observability line 110)
**Impact:** HIGH  
**Status:** 🟡 **NEEDED FOR CONFIDENCE**

**Missing Metrics:**
- `no_trade_reason` distribution
- Real-time exposure by asset/theme
- Fill ratio (orders filled/submitted)
- API error rates by endpoint
- Decision cycle latency (p50/p95/p99)

**Why Important:**
- Blind operations = higher risk
- Can't diagnose issues without metrics
- Can't optimize without performance data

**Action Required:**
- Instrument key code paths
- Export to Prometheus/Datadog
- Build Grafana dashboard

---

## 📋 REVISED PRIORITY CLASSIFICATION

### 🔴 **CRITICAL - MUST FIX BEFORE ANY LIVE TRADING**

1. ✅ AlertService initialization (FIXED)

### 🔴 **HIGH - MUST FIX BEFORE $10K+ SCALE**

2. Alert webhooks & routing (#5)
3. Alert matrix implementation (#6)  
4. Latency accounting (#1)

### 🟡 **MEDIUM - NEEDED BEFORE $50K+ SCALE**

5. Metrics dashboard (#7)
6. Jittered scheduling (#2) - if multi-instance
7. PnL circuit breaker wiring (Pending Validation #3)

### 🟢 **LOW - TECHNICAL DEBT**

8. Canonical symbol mapping (#3)
9. UTC/monotonic time (#4)
10. Remaining items in PRODUCTION_TODO.md

---

## ✅ WHAT'S ACTUALLY COMPLETE

**All 4 Critical Production Blockers:**
1. ✅ Exchange status circuit breaker (9 tests)
2. ✅ Fee-adjusted minimum notional (11 tests)
3. ✅ Outlier/bad-tick guards (15 tests)
4. ✅ Environment runtime gates (12 tests)

**Safety Infrastructure:**
- ✅ Kill switch mechanism
- ✅ Circuit breakers (data staleness, API health, volatility)
- ✅ Risk limits (exposure, position size, drawdown)
- ✅ Cooldowns (per-symbol, after losses)
- ✅ Order state machine with timeouts
- ✅ Graceful shutdown
- ✅ Fee-aware sizing
- ✅ Product constraints enforcement
- ✅ PnL tracking (realized)
- ✅ Fill reconciliation

**Total Tests:** 178 passing (47 new safety tests)

---

## 🎯 RECOMMENDED LAUNCH PLAN

### Phase 0: Critical Fixes (NOW - 1 day)
- ✅ Fix AlertService initialization
- ⏳ Configure alert webhooks
- ⏳ Test staging alerts
- ⏳ Implement basic latency tracking

**Gate:** All alerts working, latency monitored

### Phase 1: Micro-Scale Launch ($100-$1K, 1 week)
- Start DRY_RUN for 24h (validate logic)
- Switch to PAPER for 24h (validate fills)
- Enable LIVE with $100-$500 capital
- Max $10-20 per trade
- Manual monitoring (no dashboard yet)

**Success Criteria:**
- Zero crashes over 7 days
- Alerts firing correctly
- PnL tracking accurate vs Coinbase
- No unexpected order rejections

### Phase 2: Small Scale ($1K-$10K, 2 weeks)
- Implement alert matrix
- Add basic metrics (no dashboard)
- Scale to $1K-$5K capital
- Max $50-100 per trade

**Success Criteria:**
- Alert matrix covering all scenarios
- Latency p95 < 5s per cycle
- Fill reconciliation 100% accurate

### Phase 3: Medium Scale ($10K-$50K, 1 month)
- Build metrics dashboard
- Implement jittered scheduling
- Wire PnL to circuit breakers
- Scale to $10K-$50K capital

**Success Criteria:**
- Dashboard shows real-time health
- No rate limit issues
- Automated stop losses working

### Phase 4: Production Scale ($50K+, ongoing)
- Address remaining technical debt
- Backtest parity for strategy iteration
- Optimize based on metrics

---

## ⚠️ RISK ASSESSMENT

### Current State: **UNSAFE FOR PRODUCTION**

**Why:**
1. ~~AlertService crash on safety triggers~~ ✅ FIXED
2. No operational visibility (alerts/metrics)
3. Latency not tracked (can't detect degradation)

### After Phase 0 Fixes: **SAFE FOR MICRO-SCALE** ($100-$1K)

**Why:**
- Safety infrastructure complete
- Alerts working
- Can manually monitor via logs
- Small enough capital to absorb any issues

### After Phase 2: **SAFE FOR SMALL SCALE** ($1K-$10K)

**Why:**
- Alert matrix complete
- Metrics available
- Proven track record

### After Phase 3: **SAFE FOR MEDIUM SCALE** ($10K-$50K)

**Why:**
- Full operational visibility
- Automated monitoring
- Validated over time

---

## 📊 CONCLUSION

**Original Claim:** "Production-ready for LIVE trading scale-up"  
**Assessment:** **PARTIALLY CORRECT** with critical caveats

**What's True:**
- Core safety features complete (4 blockers done)
- Code quality high, well-tested (178 tests)
- Risk framework solid

**What's Missing:**
- ~~Critical bug in AlertService~~ ✅ FIXED
- Operational visibility (alerts, metrics)
- Latency monitoring
- Production validation

**Corrected Status:**
✅ **Ready for micro-scale launch** ($100-$1K) **AFTER Phase 0 fixes**  
🟡 Needs Phase 2 before $10K+ scale  
🟡 Needs Phase 3 before $50K+ scale

**Bottom Line:** System has excellent safety foundations but lacks operational maturity for confident scale-up. Phased rollout is the correct approach.
