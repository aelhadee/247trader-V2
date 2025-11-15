# Alert Matrix: Implementation Plan & Partial Completion

**Status:** ✅ **1/5 Complete** (API Error Bursts)  
**Date:** 2025-11-15  
**Priority:** P1 (Production Operational Observability)

---

## Executive Summary

Alert coverage assessment shows **4 of 9 critical alert types missing**. This document tracks implementation status, provides code examples, and outlines remaining work.

**Current Coverage:** 5/9 (56%)
**Target Coverage:** 9/9 (100%)
**Effort Remaining:** ~3-4 hours

---

## Alert Matrix Status

### ✅ IMPLEMENTED (5/9)

| Alert Type | Severity | Trigger | Location | Test Coverage |
|------------|----------|---------|----------|---------------|
| **Kill Switch Activated** | CRITICAL | `data/KILL_SWITCH` file detected | `core/risk.py:1005` | 6 tests (test_kill_switch_sla.py) |
| **Daily Stop Loss Hit** | CRITICAL | PnL < -3% for day | `core/risk.py:1057` | Verified in CRITICAL_GAPS_FIXED.md |
| **Weekly Stop Loss Hit** | CRITICAL | PnL < -7% for week | `core/risk.py:1094` | Verified in CRITICAL_GAPS_FIXED.md |
| **Max Drawdown Breached** | CRITICAL | Drawdown > 10% | `core/risk.py:1128` | Verified in CRITICAL_GAPS_FIXED.md |
| **API Error Burst** | WARNING | ≥2 consecutive API errors | `core/risk.py:2133` | ✅ **JUST ADDED** |

---

## 🔴 MISSING ALERTS (4/9)

### 1. **Reconcile Mismatch** - Position Drift Detection
**Priority:** CRITICAL  
**Effort:** 1 hour

**Trigger:** Local position quantity diverges from exchange snapshot by >5% or >$10

**Location to Wire:** `core/execution.py::reconcile_fills()` line 3719

**Implementation:**
```python
# In reconcile_fills() after processing all fills:

# Compare local vs exchange positions
position_mismatches = []
for symbol, local_pos in state.get("positions", {}).items():
    local_qty = float(local_pos.get("quantity", 0.0))
    exchange_qty = exchange_positions.get(symbol, {}).get("quantity", 0.0)
    
    # Check for significant drift
    if local_qty > 0 or exchange_qty > 0:
        diff_pct = abs(local_qty - exchange_qty) / max(local_qty, exchange_qty, 0.0001) * 100
        diff_usd = abs(local_qty - exchange_qty) * local_pos.get("entry_price", 0.0)
        
        if diff_pct > 5.0 or diff_usd > 10.0:
            position_mismatches.append({
                "symbol": symbol,
                "local_qty": local_qty,
                "exchange_qty": exchange_qty,
                "diff_pct": round(diff_pct, 2),
                "diff_usd": round(diff_usd, 2)
            })

# Alert on mismatches
if position_mismatches and self.alert_service:
    from infra.alerting import AlertSeverity
    self.alert_service.notify(
        severity=AlertSeverity.WARNING,
        title="⚠️ Position Reconciliation Mismatch",
        message=f"{len(position_mismatches)} positions diverged from exchange",
        context={
            "mismatches": position_mismatches[:5],  # Top 5
            "total_count": len(position_mismatches),
            "action": "syncing_to_exchange_truth"
        }
    )

# Return mismatch info in summary
return {
    ...existing fields...,
    "position_mismatches": position_mismatches
}
```

**Test Coverage:**
```python
def test_reconcile_mismatch_alert(mock_executor, mock_alert_service):
    # Setup: Local state has 0.01 BTC, exchange has 0.009 BTC
    mock_executor.state_store.load()["positions"]["BTC-USD"] = {
        "quantity": 0.01,
        "entry_price": 50000.0
    }
    mock_executor.exchange.get_accounts.return_value = [
        {"currency": "BTC", "available_balance": {"value": "0.009"}}
    ]
    
    result = mock_executor.reconcile_fills()
    
    # Verify alert fired
    assert mock_alert_service.notify.called
    call = mock_alert_service.notify.call_args
    assert call[1]["severity"] == AlertSeverity.WARNING
    assert "Reconciliation Mismatch" in call[1]["title"]
    assert "BTC-USD" in str(call[1]["context"])
```

---

### 2. **Order Rejection Burst** - Exchange Rejections
**Priority:** HIGH  
**Effort:** 1 hour

**Trigger:** >3 order rejections in 10-minute window

**Location to Wire:** `core/execution.py::execute()` line ~3500 (error handling section)

**Implementation:**
```python
class ExecutionEngine:
    def __init__(self, ...):
        # Add rejection tracking
        self._rejection_history = []  # List of (timestamp, symbol, reason) tuples
        self._rejection_window_seconds = 600  # 10 minutes
        self._rejection_threshold = 3

    def execute(self, symbol: str, side: str, ...) -> ExecutionResult:
        try:
            # ... existing execution logic ...
            pass
        except Exception as e:
            # Track rejection
            now = datetime.now(timezone.utc)
            self._rejection_history.append((now, symbol, str(e)))
            
            # Clean old rejections
            cutoff = now - timedelta(seconds=self._rejection_window_seconds)
            self._rejection_history = [
                (ts, sym, reason) for ts, sym, reason in self._rejection_history
                if ts > cutoff
            ]
            
            # Alert on burst
            if len(self._rejection_history) >= self._rejection_threshold:
                if hasattr(self, 'alert_service') and self.alert_service:
                    from infra.alerting import AlertSeverity
                    rejection_reasons = {}
                    for _, sym, reason in self._rejection_history:
                        rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1
                    
                    self.alert_service.notify(
                        severity=AlertSeverity.WARNING,
                        title="⚠️ Order Rejection Burst",
                        message=f"{len(self._rejection_history)} orders rejected in {self._rejection_window_seconds}s",
                        context={
                            "rejection_count": len(self._rejection_history),
                            "window_seconds": self._rejection_window_seconds,
                            "rejection_reasons": rejection_reasons,
                            "affected_symbols": list(set(sym for _, sym, _ in self._rejection_history))
                        }
                    )
            
            # Return error result
            return ExecutionResult(
                success=False,
                error=str(e),
                ...
            )
```

**Wiring Required:**
1. Add `alert_service` parameter to `ExecutionEngine.__init__()`
2. Pass `alerts` from `TradingLoop` to executor:
   ```python
   # runner/main_loop.py line 227
   self.executor = ExecutionEngine(
       mode=self.mode,
       exchange=self.exchange,
       policy=self.policy_config,
       state_store=self.state_store,
       alert_service=self.alerts  # ADD THIS
   )
   ```

**Test Coverage:**
```python
def test_order_rejection_burst_alert(mock_executor, mock_alert_service):
    mock_executor.alert_service = mock_alert_service
    
    # Trigger 4 rejections in quick succession
    for i in range(4):
        result = mock_executor.execute(
            symbol=f"TEST{i}-USD",
            side="BUY",
            size_usd=100.0
        )
        assert not result.success
    
    # Verify alert fired on 3rd rejection
    assert mock_alert_service.notify.call_count >= 1
    call = mock_alert_service.notify.call_args
    assert call[1]["severity"] == AlertSeverity.WARNING
    assert "Rejection Burst" in call[1]["title"]
    assert call[1]["context"]["rejection_count"] >= 3
```

---

### 3. **Empty Universe** - No Eligible Assets
**Priority:** CRITICAL  
**Effort:** 30 minutes

**Trigger:** Universe build returns <2 eligible assets (min_eligible_assets threshold)

**Location to Wire:** `core/universe.py::get_universe()` line ~50 (after building universe)

**Implementation:**
```python
# In UniverseManager.get_universe() after universe construction:

eligible_count = sum(1 for asset in universe.assets.values() if asset.eligible)

# Alert if universe too small
min_eligible = self.config.get("universe", {}).get("min_eligible_assets", 2)
if eligible_count < min_eligible:
    if hasattr(self, 'alert_service') and self.alert_service:
        from infra.alerting import AlertSeverity
        
        # Collect ineligibility reasons
        ineligible_reasons = {}
        for asset in universe.assets.values():
            if not asset.eligible and asset.ineligible_reason:
                ineligible_reasons[asset.ineligible_reason] = ineligible_reasons.get(asset.ineligible_reason, 0) + 1
        
        self.alert_service.notify(
            severity=AlertSeverity.CRITICAL,
            title="🚨 Empty Universe",
            message=f"Only {eligible_count} eligible assets (minimum: {min_eligible})",
            context={
                "eligible_count": eligible_count,
                "threshold": min_eligible,
                "total_assets": len(universe.assets),
                "ineligibility_reasons": ineligible_reasons,
                "action": "trading_paused"
            }
        )
```

**Wiring Required:**
1. Add `alert_service` parameter to `UniverseManager.__init__()`
2. Pass `alerts` from `TradingLoop` to universe manager (already done in line 207):
   ```python
   # runner/main_loop.py
   self.universe = UniverseManager(
       universe_config,
       self.exchange,
       state_store=self.state_store,
       alert_service=self.alerts  # ADD THIS
   )
   ```

**Test Coverage:**
```python
def test_empty_universe_alert(mock_universe_manager, mock_alert_service):
    mock_universe_manager.alert_service = mock_alert_service
    
    # Mock universe with only 1 eligible asset
    mock_exchange.list_products.return_value = [
        {"product_id": "BTC-USD", "status": "OFFLINE"}  # Ineligible
    ]
    
    universe = mock_universe_manager.get_universe()
    
    # Verify alert fired
    assert mock_alert_service.notify.called
    call = mock_alert_service.notify.call_args
    assert call[1]["severity"] == AlertSeverity.CRITICAL
    assert "Empty Universe" in call[1]["title"]
    assert call[1]["context"]["eligible_count"] < 2
```

---

### 4. **Exception Burst** - Trading Loop Crashes
**Priority:** CRITICAL  
**Effort:** 30 minutes

**Trigger:** >2 exceptions in 5-minute window

**Location to Wire:** `runner/main_loop.py::run_cycle()` line ~1930 (exception handler)

**Implementation:**
```python
class TradingLoop:
    def __init__(self, ...):
        # Add exception tracking
        self._exception_history = []  # List of (timestamp, exception_type) tuples
        self._exception_window_seconds = 300  # 5 minutes
        self._exception_threshold = 2

    def run_cycle(self):
        try:
            # ... existing cycle logic ...
            pass
        except Exception as e:
            # Track exception
            now = datetime.now(timezone.utc)
            exc_type = type(e).__name__
            self._exception_history.append((now, exc_type))
            
            # Clean old exceptions
            cutoff = now - timedelta(seconds=self._exception_window_seconds)
            self._exception_history = [
                (ts, et) for ts, et in self._exception_history if ts > cutoff
            ]
            
            # Check for burst
            if len(self._exception_history) >= self._exception_threshold:
                # Count exception types
                exc_counts = {}
                for _, et in self._exception_history:
                    exc_counts[et] = exc_counts.get(et, 0) + 1
                
                # Alert with enhanced context
                self.alerts.notify(
                    AlertSeverity.CRITICAL,
                    "🚨 Exception Burst",
                    f"{len(self._exception_history)} exceptions in {self._exception_window_seconds}s",
                    {
                        "exception_count": len(self._exception_history),
                        "window_seconds": self._exception_window_seconds,
                        "exception_types": exc_counts,
                        "latest_exception": exc_type,
                        "mode": self.mode,
                        "action": "check_for_systemic_issue"
                    }
                )
            else:
                # Existing single exception alert (already wired)
                self.alerts.notify(
                    AlertSeverity.CRITICAL,
                    "Trading loop exception",
                    str(e),
                    {
                        "exception": exc_type,
                        "mode": self.mode,
                    },
                )
            
            # ... rest of exception handling ...
```

**Test Coverage:**
```python
def test_exception_burst_alert(mock_trading_loop, mock_alert_service):
    mock_trading_loop.alerts = mock_alert_service
    
    # Trigger 3 exceptions rapidly
    for i in range(3):
        with pytest.raises(Exception):
            mock_trading_loop.run_cycle()
    
    # Verify burst alert fired
    assert mock_alert_service.notify.call_count >= 1
    burst_call = [
        call for call in mock_alert_service.notify.call_args_list
        if "Exception Burst" in str(call)
    ][0]
    assert burst_call[1]["severity"] == AlertSeverity.CRITICAL
    assert burst_call[1]["context"]["exception_count"] >= 2
```

---

## Implementation Plan

### Phase 1: Core Wiring (2-3 hours)

**Step 1:** Wire alert_service to ExecutionEngine (30 min)
- Add parameter to `ExecutionEngine.__init__()`
- Update `TradingLoop` initialization
- Add attribute: `self.alert_service = alert_service`

**Step 2:** Implement Reconcile Mismatch Alert (1 hour)
- Add position comparison logic to `reconcile_fills()`
- Wire alert on >5% drift or >$10 difference
- Add 3 test cases

**Step 3:** Implement Order Rejection Burst (1 hour)
- Add rejection tracking to ExecutionEngine
- Wire alert on >3 rejections in 10min
- Add 3 test cases

**Step 4:** Implement Empty Universe Alert (30 min)
- Wire alert_service to UniverseManager
- Add check after universe build
- Add 2 test cases

**Step 5:** Implement Exception Burst Alert (30 min)
- Add exception tracking to TradingLoop
- Enhance existing exception handler
- Add 2 test cases

### Phase 2: Testing & Validation (1 hour)

**Unit Tests:**
- 10 new tests across 4 alert types
- Verify alert firing conditions
- Check dedupe/escalation behavior

**Integration Test:**
- Run PAPER mode with forced error conditions
- Verify all 9 alert types can trigger
- Confirm Slack webhook delivery

**Smoke Test:**
```bash
# Force each alert type and verify Slack delivery
python scripts/test_alerts.py --production-scenarios
```

### Phase 3: Documentation (30 min)

**Update Files:**
- `docs/ALERT_SYSTEM_SETUP.md` - Add new alert types
- `PRODUCTION_TODO.md` - Mark alert matrix as complete
- `docs/ALERT_QUICK_START.md` - Update coverage stats

---

## Configuration

### Required Policy Settings

```yaml
# config/policy.yaml
risk:
  min_eligible_assets: 2  # Empty universe threshold

execution:
  max_order_rejections: 3
  rejection_window_seconds: 600  # 10 minutes

monitoring:
  max_exceptions_per_window: 2
  exception_window_seconds: 300  # 5 minutes
  
  reconcile_mismatch_threshold_pct: 5.0
  reconcile_mismatch_threshold_usd: 10.0
```

### Alert Service Configuration

```yaml
# config/app.yaml
monitoring:
  alert_service:
    enabled: true
    webhook_url: ${ALERT_WEBHOOK_URL}
    min_severity: WARNING
    dedupe_seconds: 60
    escalation_seconds: 120
```

---

## Testing Strategy

### Unit Tests (10 new tests)

**test_alert_matrix.py:**
```python
import pytest
from infra.alerting import AlertService, AlertSeverity
from core.execution import ExecutionEngine
from core.universe import UniverseManager
from runner.main_loop import TradingLoop

class TestAlertMatrix:
    def test_api_error_burst_alert(self, ...):
        """Verify API error burst triggers WARNING alert"""
        pass
    
    def test_reconcile_mismatch_alert(self, ...):
        """Verify position mismatch triggers WARNING alert"""
        pass
    
    def test_order_rejection_burst_alert(self, ...):
        """Verify >3 rejections in 10min triggers WARNING alert"""
        pass
    
    def test_empty_universe_alert(self, ...):
        """Verify <2 eligible assets triggers CRITICAL alert"""
        pass
    
    def test_exception_burst_alert(self, ...):
        """Verify >2 exceptions in 5min triggers CRITICAL alert"""
        pass
    
    # 5 more tests for edge cases (single errors, dedupe, escalation)
```

### Integration Test

**PAPER Mode Smoke Test:**
```python
# scripts/test_alert_integration.py
def test_full_alert_matrix_integration():
    """
    Force each alert type in PAPER mode and verify Slack delivery.
    
    Covers:
    1. Kill switch activation
    2. Daily stop loss hit
    3. Weekly stop loss hit
    4. Max drawdown breached
    5. API error burst
    6. Reconcile mismatch
    7. Order rejection burst
    8. Empty universe
    9. Exception burst
    """
    # Setup PAPER mode trading loop
    loop = TradingLoop(config_dir="config")
    
    # Force each alert condition...
    # Verify webhook calls...
    
    assert all_alerts_delivered
```

---

## Rollout Strategy

### Stage 1: Development (Current)
- Implement 4 missing alert types
- Add unit tests (10 tests)
- Update documentation

### Stage 2: PAPER Testing (1-2 days)
- Run PAPER mode with forced error conditions
- Verify all alerts trigger correctly
- Tune thresholds based on false positive rate

### Stage 3: Production Monitoring (1 week)
- Deploy to LIVE with alerts enabled
- Monitor alert frequency and false positives
- Adjust dedupe windows if needed

### Stage 4: On-Call Integration
- Document response procedures for each alert
- Train on-call team on alert meanings
- Create runbooks for each critical alert

---

## Success Criteria

✅ **All 9 alert types implemented and tested**  
✅ **10+ unit tests passing**  
✅ **PAPER mode integration test passes**  
✅ **Documentation updated (3 files)**  
✅ **False positive rate <5% in production**  
✅ **Alert-to-resolution time <5min for CRITICAL**  
✅ **On-call team trained and runbooks ready**

---

## Risk Assessment

**Implementation Risk:** LOW
- Alert additions are additive (no logic changes)
- Each alert type is independent
- Fail-safe: Missing alerts don't break trading

**Operational Risk:** LOW-MEDIUM
- False positives could cause alert fatigue
- Mitigation: Tune thresholds in PAPER mode
- Dedupe/escalation prevents noise

**Rollback Plan:**
```python
# Disable specific alert types in config/app.yaml
monitoring:
  alert_service:
    disabled_alerts:
      - "Order Rejection Burst"  # Example: Too noisy
```

---

## Next Steps (Immediate)

1. **Wire ExecutionEngine Alert Service** (30 min)
   ```python
   # runner/main_loop.py line 227
   self.executor = ExecutionEngine(
       ...,
       alert_service=self.alerts  # ADD THIS
   )
   ```

2. **Implement Reconcile Mismatch Alert** (1 hour)
   - See implementation section above
   - Add to `core/execution.py::reconcile_fills()`

3. **Run Tests** (30 min)
   ```bash
   pytest tests/test_alert_matrix.py -v
   ```

4. **Update PRODUCTION_TODO.md** (5 min)
   ```markdown
   | 🟢 Done | Complete alert matrix coverage | N/A | 9/9 alert types implemented...
   ```

---

**Document Version:** 1.0  
**Last Updated:** 2025-11-15  
**Status:** API Error Burst complete (1/5), 4 alerts pending
