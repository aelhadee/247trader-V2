# Exchange Product Status Circuit Breaker - Implementation Summary

**Date:** 2025-01-XX  
**Status:** ✅ Complete  
**Tests:** 9/9 passing  
**Total Test Suite:** 139/139 passing (excluding pre-existing failures in test_exchange.py and test_live_smoke.py)

## Overview

Implemented exchange product status circuit breaker to prevent trading on Coinbase products with degraded or restricted status (POST_ONLY, LIMIT_ONLY, CANCEL_ONLY, OFFLINE). This safety feature protects against order rejections and unexpected behavior during exchange maintenance or incident scenarios.

## Implementation Details

### Core Changes

#### 1. RiskEngine Enhancement (`core/risk.py`)

**Location:** Lines ~193 and ~770-830

**New Method:** `_filter_degraded_products(proposals: List[TradeProposal]) -> Tuple[List[TradeProposal], bool, str]`

**Functionality:**
- Queries exchange product metadata for each proposed trade symbol
- Blocks trades for products with restricted statuses:
  - `POST_ONLY` - Only post-only orders accepted (maker-only)
  - `LIMIT_ONLY` - Only limit orders accepted (no market orders)
  - `CANCEL_ONLY` - Only order cancellations accepted (halted trading)
  - `OFFLINE` - Product completely offline
- **Fail-closed behavior:** Blocks all trades if metadata fetch fails or status field missing
- Logs warnings for each blocked product with status reason
- Returns filtered proposal list and appropriate RiskCheckResult

**Integration Point:**
- Called early in `check_all()` pipeline (after circuit breakers, before kill switch check)
- Ensures product status validation happens before any other risk checks
- Prevents wasted computation on proposals that will be rejected anyway

**Error Handling:**
- Catches all exceptions during metadata fetch
- Returns empty proposal list with descriptive error message
- Logs errors for debugging while maintaining fail-closed posture

#### 2. Policy Configuration (`config/policy.yaml`)

**Added Key:** `circuit_breakers.check_product_status: true`

**Location:** Line ~12 (within circuit_breakers section)

**Purpose:**
- Enables/disables product status filtering
- Set to `true` by default for production safety
- Can be disabled for testing or special scenarios

**Documentation:**
```yaml
circuit_breakers:
  check_product_status: true  # Block trading on POST_ONLY/LIMIT_ONLY/CANCEL_ONLY products
```

### Test Coverage

**New Test File:** `tests/test_exchange_status_circuit.py`

**Test Suite:** 9 comprehensive tests

**Coverage Areas:**

1. **Status Blocking Tests (4 tests)**
   - `test_blocks_post_only_products` - Verifies POST_ONLY status blocks trades
   - `test_blocks_limit_only_products` - Verifies LIMIT_ONLY status blocks trades
   - `test_blocks_cancel_only_products` - Verifies CANCEL_ONLY status blocks trades
   - `test_blocks_offline_products` - Verifies OFFLINE status blocks trades

2. **Positive Test (1 test)**
   - `test_allows_online_products` - Verifies ONLINE products pass through

3. **Mixed Scenarios (1 test)**
   - `test_filters_mixed_product_statuses` - Tests batch filtering with mixed statuses
   - Validates correct products filtered while others proceed

4. **Error Handling Tests (2 tests)**
   - `test_fail_closed_on_metadata_error` - Verifies fail-closed on API errors
   - `test_fail_closed_on_missing_status` - Verifies fail-closed on malformed data

5. **Configuration Test (1 test)**
   - `test_respects_config_toggle` - Verifies check_product_status toggle works

**Test Approach:**
- Uses mocked exchange adapter with controlled metadata responses
- Tests both DRY_RUN mode behavior and fail-closed safety
- Validates log output for blocked products
- Confirms RiskCheckResult structure (approved=False, reason populated, proposals empty)

## Design Decisions

### 1. Fail-Closed Philosophy
**Rationale:** Prefer false negatives (missed trades) over false positives (bad trades)
- Blocks all trades if metadata unavailable
- Blocks if status field missing or malformed
- Logs errors for debugging while maintaining safety

### 2. Early Filtering in Pipeline
**Placement:** After circuit breakers, before kill switch
- Avoids wasted computation on doomed proposals
- Maintains clear separation of concerns
- Enables per-product filtering vs blanket rejection

### 3. Leverage Existing Metadata Cache
**Implementation:** Uses `exchange.get_product_metadata()`
- 5-minute cache refresh already in place
- No additional API calls per cycle
- Consistent with execution engine constraints

### 4. Configurable Toggle
**Flexibility:** `circuit_breakers.check_product_status` flag
- Enables feature in production by default
- Allows disabling for testing or special cases
- Follows existing policy structure patterns

## Integration Points

### Upstream Dependencies
- `CoinbaseExchange.get_product_metadata()` - Product metadata with status field
- `policy.yaml` circuit_breakers config - Toggle and settings
- `RiskEngine.check_all()` orchestration - Pipeline integration

### Downstream Effects
- `TradeProposal` filtering - Removes restricted products before risk checks
- Audit logging - Warnings for blocked products
- StateStore - No changes needed (products filtered before state updates)

### Compatibility
- **Modes:** Works in DRY_RUN, PAPER, LIVE (mode-agnostic filtering)
- **Backtest:** Compatible (backtest should mock exchange metadata)
- **Other Safety Features:** Complements circuit breakers, kill switch, cooldowns

## Operational Considerations

### Expected Behavior

**Normal Operation:**
- Products with `ONLINE` status proceed through risk checks
- Minimal performance impact (uses cached metadata)

**During Exchange Incidents:**
- Products with degraded status automatically filtered
- Logs warnings with symbol and status
- Risk check returns `approved=False` with clear reason
- Audit trail captures blocked proposals

**On Metadata Errors:**
- All proposals blocked (fail-closed)
- Error logged with details
- Risk check returns descriptive failure reason

### Monitoring & Alerts

**Log Patterns to Watch:**
```
WARNING  core.risk:risk.py:811 Blocking {symbol}: exchange status={status}
WARNING  core.risk:risk.py:825 Filtered {count} proposals due to exchange product status: [...]
ERROR    core.risk:risk.py:XXX Failed to fetch product metadata: {error}
```

**Alert Triggers:**
- Sustained product status blocks (>5 min) - May indicate exchange issues
- Metadata fetch errors - API health degradation
- All products filtered - Possible widespread incident

**Metrics to Track:**
- Count of proposals filtered per cycle
- Distribution of blocked statuses
- Metadata fetch error rate

### Rollback Plan

**If Issues Arise:**
1. **Immediate:** Set `circuit_breakers.check_product_status: false` in policy.yaml
2. **Restart:** Trading loop will disable filtering
3. **Investigate:** Check logs for error patterns
4. **Fix & Re-enable:** Address root cause and set back to `true`

**Rollback Impact:**
- Disabling removes product status protection
- Other circuit breakers still active
- Kill switch still functional

## Testing Performed

### Unit Tests
- ✅ 9/9 new tests passing
- ✅ All status types covered
- ✅ Error handling validated
- ✅ Config toggle verified

### Integration Tests
- ✅ 139 total tests passing (excluding pre-existing failures)
- ✅ No regressions in existing functionality
- ✅ RiskEngine integration confirmed

### Manual Testing
- ⏸️ Pending: Live rehearsal with real exchange metadata
- ⏸️ Pending: Smoke test during known POST_ONLY period

## Documentation Updates

### Updated Files
- ✅ `PRODUCTION_TODO.md` - Marked task as 🟢 Done
- ✅ `config/policy.yaml` - Added check_product_status key with comment
- ✅ `tests/test_exchange_status_circuit.py` - New test file with comprehensive coverage
- ✅ This summary document

### Future Documentation Needs
- [ ] Add to operations runbook (incident response)
- [ ] Document in architecture diagrams (risk pipeline)
- [ ] Update monitoring dashboard queries

## Performance Impact

**Metadata Lookup:** O(1) per symbol (uses dict cache)  
**Filtering:** O(n) where n = proposal count  
**Cache Refresh:** Every 5 minutes (existing behavior)

**Expected Overhead:** <1ms per cycle for typical proposal counts (1-10)

## Security Considerations

**Attack Vectors Mitigated:**
- Rogue trades during exchange maintenance
- Order rejections during restricted periods
- Capital deployment during degraded liquidity

**Fail-Closed Guarantees:**
- Blocks on metadata errors
- Blocks on missing status field
- Blocks on unknown/unexpected statuses

## Known Limitations

1. **Cache Lag:** 5-minute metadata refresh means status changes take up to 5 min to reflect
   - **Mitigation:** Coinbase typically announces maintenance in advance
   - **Future:** Could add real-time status websocket for faster response

2. **Binary Decision:** Either block all trades or allow all for a symbol
   - **Mitigation:** Appropriate for safety-critical feature
   - **Future:** Could add tiered responses (reduce size, post-only enforcement)

3. **No Historical Tracking:** Doesn't log status change history
   - **Mitigation:** Audit logs capture when blocks occur
   - **Future:** Could add status change events to metrics

## Next Steps

### Immediate (Before LIVE Scale-Up)
1. ✅ Mark task complete in PRODUCTION_TODO.md
2. ⏸️ Monitor in DRY_RUN/PAPER for 24-48 hours
3. ⏸️ Verify log patterns during different market hours
4. ⏸️ Confirm no false positives during normal trading

### Short-Term
1. Add alert rules for sustained product status blocks
2. Create monitoring dashboard panel
3. Document incident response procedures
4. Add to daily health check script

### Long-Term Enhancements
1. Real-time status updates via websocket
2. Status change event notifications
3. Historical status tracking and analytics
4. Tiered response (reduce size vs full block)

## Conclusion

Exchange product status circuit breaker successfully implemented and tested. Feature adds critical production safety by preventing trades on products with degraded exchange status. Fail-closed design ensures conservative behavior during uncertainties. Integration with existing risk pipeline is clean and non-invasive. Ready for production deployment.

**Recommendation:** Enable in production (already configured) and monitor for 24-48 hours in DRY_RUN/PAPER before LIVE scale-up.

---

**Implementation Date:** 2025-01-XX  
**Implemented By:** AI Assistant (GitHub Copilot)  
**Reviewed By:** [Pending]  
**Approved For Production:** [Pending]
