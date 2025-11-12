# Environment Runtime Gates Implementation

**Status:** ✅ Complete  
**Production Blocker:** #4 (FINAL)  
**Tests:** 12 passing (178 total unit tests passing)

## Overview

Comprehensive mode and read_only validation that prevents accidental LIVE trading. Enforces safety ladder (DRY_RUN → PAPER → LIVE) with explicit configuration requirements for real-money execution.

## Problem Statement

Without proper runtime gates, the system could:
- **Accidentally execute real trades** in testing/development
- **Proceed to LIVE** without explicit configuration confirmation
- **Bypass safety checks** through configuration errors
- **Enable real trading** through default settings

The safety requirement is absolute: **Only** mode=LIVE + read_only=false should enable real orders.

## Solution

Multi-layered validation enforcing fail-closed behavior:

1. **Mode validation** at ExecutionEngine initialization
2. **Early gate check** at execute() entry point (before any processing)
3. **Mode-specific routing** to DRY_RUN/PAPER/LIVE handlers
4. **Exchange-level protection** via read_only flag
5. **Configuration defaults** that fail-safe (DRY_RUN + read_only=true)

## Mode Definitions

### DRY_RUN
- **Exchange interaction:** NONE
- **Order placement:** Simulated only
- **Data access:** No live data required
- **Use case:** Development, testing, strategy validation
- **Safety:** Maximum (zero exchange risk)

### PAPER
- **Exchange interaction:** Read-only (quotes, balances, products)
- **Order placement:** Simulated with live prices
- **Data access:** Real-time market data
- **Use case:** Strategy validation with realistic fills
- **Safety:** High (no real orders, but uses live data)

### LIVE
- **Exchange interaction:** Full (requires read_only=false)
- **Order placement:** REAL ORDERS with real money
- **Data access:** Real-time market data
- **Use case:** Production trading
- **Safety:** REQUIRES explicit configuration

## Configuration

### config/app.yaml

```yaml
app:
  mode: "LIVE"  # DRY_RUN | PAPER | LIVE

exchange:
  read_only: false  # MUST be false for LIVE trading
```

### Safety Matrix

| Mode | read_only | Real Orders? | Description |
|------|-----------|--------------|-------------|
| DRY_RUN | true/false | ❌ NO | Pure simulation, no exchange interaction |
| PAPER | true/false | ❌ NO | Live data, simulated execution |
| LIVE | true | ❌ NO | BLOCKED by read_only safety gate |
| LIVE | false | ✅ YES | **ONLY this enables real trading** |

## Implementation

### Code Changes

1. **core/execution.py** (lines 999-1026)
   - Added early validation in `execute()` method
   - Checks LIVE mode + read_only flag BEFORE any processing
   - Raises ValueError with clear message if misconfigured
   - Prevents execution before trading pair lookup or other logic

2. **runner/main_loop.py** (lines 74-82)
   - Enforces read_only logic: `(mode != "LIVE") or read_only_cfg`
   - DRY_RUN and PAPER modes ALWAYS use read_only=true
   - LIVE mode respects config (but defaults to true)
   - Passes read_only to CoinbaseExchange

3. **core/exchange_coinbase.py** (lines 716-718, 950-951, etc.)
   - Existing read_only checks at exchange level
   - Blocks place_order(), preview_order(), cancel_order() when read_only=true
   - Returns error or raises ValueError

4. **tests/test_environment_gates.py** (322 lines, new file)
   - 12 comprehensive tests covering all combinations
   - Mode validation, read_only enforcement, default safety
   - Integration between ExecutionEngine and TradingLoop

### Validation Flow

```
User calls ExecutionEngine.execute(symbol, side, size)
│
├─ [NEW] Early gate check:
│  └─ if mode==LIVE and exchange.read_only==true: RAISE ValueError
│
├─ Extract base symbol
├─ Check cooldown
│
├─ Route by mode:
│  ├─ DRY_RUN → _execute_dry_run()  (no exchange calls)
│  ├─ PAPER → _execute_paper()      (get_quote only, simulate fill)
│  └─ LIVE → _execute_live()        (real orders)
│     └─ [EXISTING] if exchange.read_only: RAISE ValueError
│
└─ Return ExecutionResult
```

### Error Messages

**Early gate (execute()):**
```
ValueError: Cannot execute LIVE orders with read_only exchange. 
Set exchange.read_only=false in config/app.yaml to enable real trading.
```

**Late gate (_execute_live()):**
```
ValueError: Cannot execute LIVE orders with read_only exchange
```

Both provide clear guidance for operators.

## Test Coverage

All 12 tests passing:

### Mode Validation Tests
1. ✅ **DRY_RUN never executes** - No exchange interaction even with read_only=false
2. ✅ **PAPER simulates only** - Gets quotes but never places real orders
3. ✅ **LIVE + read_only=true blocks** - Raises ValueError immediately
4. ✅ **LIVE + read_only=false allows** - Permits real order execution
5. ✅ **Invalid mode rejected** - ValueError for unknown modes

### Configuration Tests
6. ✅ **Mode case normalization** - "dry_run", "Paper", "LIVE" all normalized
7. ✅ **TradingLoop read_only enforcement** - Logic test for mode/config combinations
8. ✅ **Default mode is DRY_RUN** - Missing config defaults to safe mode
9. ✅ **Default read_only is true** - Missing config defaults to safe

### Integration Tests
10. ✅ **Exchange respects read_only** - place_order() blocked when read_only=true
11. ✅ **ExecutionEngine logs mode** - Audit trail includes mode in logs
12. ✅ **All three modes distinct** - DRY_RUN/PAPER/LIVE follow different code paths

## Fail-Closed Behavior

System defaults to maximum safety:

1. **Missing mode config** → DRY_RUN
2. **Missing read_only config** → true (read-only)
3. **DRY_RUN or PAPER mode** → Forces read_only=true (ignores config)
4. **LIVE mode** → Respects read_only config (defaults to true)
5. **Configuration errors** → ValueError raised early

Only explicit LIVE + read_only=false combination enables real trading.

## Safety Checklist for LIVE Trading

Before enabling real trading, verify ALL:

- [ ] `config/app.yaml` has `app.mode: "LIVE"`
- [ ] `config/app.yaml` has `exchange.read_only: false`
- [ ] API keys are properly configured
- [ ] Account has sufficient balance for trades
- [ ] Policy risk limits are appropriately configured
- [ ] Kill switch file (`data/KILL_SWITCH`) does NOT exist
- [ ] Logs confirm "mode=LIVE, read_only=False"

If ANY item fails, system will block real orders.

## Monitoring

### Logs to Watch

**Initialization (INFO):**
```
Initialized ExecutionEngine (mode=LIVE, ...)
Starting 247trader-v2 in mode=LIVE, read_only=false
```

**Execution (WARNING for LIVE):**
```
LIVE: Executing BUY $100.00 of BTC-USD
```

**Blocked Execution (ERROR):**
```
LIVE mode execution attempted with read_only=true exchange
Cannot execute LIVE orders with read_only exchange
```

### Metrics

- Mode distribution (DRY_RUN/PAPER/LIVE executions per hour)
- read_only violations attempted
- Configuration validation failures
- Mode transitions (if system allows runtime changes)

## Operational Procedures

### Starting DRY_RUN (Development)
```yaml
# config/app.yaml
app:
  mode: "DRY_RUN"

exchange:
  read_only: true  # Doesn't matter, DRY_RUN forces safety
```

### Starting PAPER (Pre-Production)
```yaml
# config/app.yaml
app:
  mode: "PAPER"

exchange:
  read_only: true  # Doesn't matter, PAPER forces safety
```

### Starting LIVE (Production)
```yaml
# config/app.yaml
app:
  mode: "LIVE"

exchange:
  read_only: false  # REQUIRED for real trading
```

### Emergency Stop
```bash
# Immediate: Create kill switch
touch data/KILL_SWITCH

# Failsafe: Revert to read-only
# Edit config/app.yaml
exchange:
  read_only: true

# Restart system
```

## Rollback Plan

If environment gates cause issues:

1. **Immediate (config change):**
   ```yaml
   app:
     mode: "PAPER"  # Or "DRY_RUN"
   exchange:
     read_only: true
   ```
   Restart system - no code changes required

2. **Gradual (testing):**
   - Test in DRY_RUN mode first
   - Validate in PAPER mode
   - Enable LIVE mode with small capital
   - Scale up gradually

3. **Complete rollback (code revert):**
   - Revert commit adding early gate check
   - System falls back to existing _execute_live() check only
   - Still protected but less fail-fast

## Related Blockers

This completes **ALL 4 critical production blockers:**

- ✅ **Blocker #1:** Exchange status circuit breaker (9 tests)
- ✅ **Blocker #2:** Fee-adjusted minimum notional rounding (11 tests)
- ✅ **Blocker #3:** Outlier/bad-tick guards (15 tests)
- ✅ **Blocker #4:** Environment runtime gates (12 tests) ← **THIS (FINAL)**

**System now ready for LIVE trading scale-up with all critical safety features in place.**

## Testing

### Unit Tests
```bash
pytest tests/test_environment_gates.py -v
# 12 tests, all passing
```

### Integration Test
```bash
# DRY_RUN mode test
python -c "
from core.execution import ExecutionEngine
engine = ExecutionEngine(mode='DRY_RUN')
result = engine.execute('BTC-USD', 'BUY', 100.0)
assert result.success
assert 'dry_run' in result.route
print('✓ DRY_RUN works')
"

# LIVE mode with read_only should fail
python -c "
from core.execution import ExecutionEngine
from core.exchange_coinbase import CoinbaseExchange
exchange = CoinbaseExchange(read_only=True)
engine = ExecutionEngine(mode='LIVE', exchange=exchange)
try:
    engine.execute('BTC-USD', 'SELL', 100.0)
    assert False, 'Should have raised ValueError'
except ValueError as e:
    assert 'read_only' in str(e)
    print('✓ LIVE + read_only=true properly blocked')
"
```

### Manual Verification
1. Set `mode: "LIVE"` and `read_only: true` in config
2. Start system and attempt trade
3. Verify ERROR log and ValueError
4. Change `read_only: false`
5. Restart and verify WARNING log for LIVE execution

## References

- **Config:** `config/app.yaml` (mode and read_only settings)
- **Implementation:** 
  - `core/execution.py` (lines 999-1026: early gate; lines 1091-1126: mode routing)
  - `runner/main_loop.py` (lines 74-82: read_only enforcement)
  - `core/exchange_coinbase.py` (read_only checks throughout)
- **Tests:** `tests/test_environment_gates.py` (322 lines, 12 tests)
- **Total Tests:** 178 passing (166 baseline + 12 new)
