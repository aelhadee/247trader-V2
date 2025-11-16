# Task 9 Pre-Requisite: PAPER Rehearsal Setup

**Status:** ⏳ BLOCKED - Credentials Required  
**Blocker:** Coinbase API credentials not configured  
**Resolution:** Complete Task 7 first (Enforce secrets via environment)

---

## Current Situation

PAPER mode rehearsal requires **real Coinbase API credentials** to:
- Execute paper trades on Coinbase's paper trading environment
- Validate rate limiting with real API endpoints
- Test order execution and fill reconciliation
- Collect real market data for triggers

**Missing Prerequisites:**
1. ❌ Coinbase API key not set (`COINBASE_API_KEY` or `CB_API_KEY`)
2. ❌ Coinbase API secret not set (`COINBASE_API_SECRET` or `CB_API_SECRET`)
3. ❌ Environment-based credential loading not enforced (Task 7)

---

## Recommended Path Forward

### Option A: Complete Task 7 First (Recommended)

**Task 7:** Enforce secrets via environment only

**Why This Order:**
1. Task 7 hardens credential loading (removes file-based fallbacks)
2. Forces proper environment setup before PAPER run
3. Reduces security risk (no credentials in files)
4. Task 9 (PAPER rehearsal) requires working credentials anyway

**Effort:** 30-45 minutes  
**Benefit:** Security + prerequisite completion

**Steps:**
1. Remove file-based credential loading code
2. Add runtime validation for required environment variables
3. Add clear error messages if credentials missing
4. Update documentation
5. Test with mock credentials
6. **Then** set up real credentials for PAPER rehearsal

### Option B: Set Up Credentials Now (Quick Start)

**Steps:**
1. Obtain Coinbase API credentials (paper trading account)
2. Set environment variables:
   ```bash
   export COINBASE_API_KEY="your-key-here"
   export COINBASE_API_SECRET="your-secret-here"
   ```
3. Verify credentials:
   ```bash
   python -c "from core.exchange_coinbase import CoinbaseExchange; ex = CoinbaseExchange(); print('✅ Credentials loaded')"
   ```
4. Proceed with PAPER rehearsal

**Effort:** 10-15 minutes (if credentials available)  
**Risk:** May skip security hardening (Task 7)

### Option C: Shadow DRY_RUN Mode (No Credentials Required)

**Task 4:** Implement shadow DRY_RUN mode

**Why:** Validates execution logic without API access
- Logs intended orders without submitting
- Tests circuit breakers and risk engine
- No credentials required
- Lower risk validation

**Effort:** 45-60 minutes  
**Benefit:** Immediate validation progress

---

## Decision Matrix

| Option | Effort | Risk | Credentials Needed | Production Value |
|--------|--------|------|-------------------|------------------|
| A (Task 7 first) | 45 min | LOW | Later | HIGH (security) |
| B (Credentials now) | 15 min | MEDIUM | Yes | MEDIUM |
| C (Task 4 instead) | 60 min | LOW | No | MEDIUM |

**Recommendation:** **Option A** (Task 7 → Task 9)

This follows secure-by-default principle and ensures production deployment has proper credential handling.

---

## Task 7 Scope

**What Needs to Change:**

1. **Remove file-based loading** in `core/exchange_coinbase.py`:
   - Remove any `CB_API_SECRET_FILE` logic
   - Remove fallback to config files
   - Keep only environment variable loading

2. **Add validation** in startup:
   - Check `COINBASE_API_KEY` and `COINBASE_API_SECRET` present
   - Fail fast with clear error message
   - Validate secret format (not empty, looks like valid key)

3. **Update documentation**:
   - `docs/CREDENTIALS_MIGRATION_GUIDE.md`
   - `README.md` setup instructions
   - `app_run_live.sh` startup checks

4. **Test with mocks**:
   - Tests should use mock credentials
   - CI should not require real credentials
   - Add environment check to test setup

---

## Next Steps

**Immediate Action:** Proceed with Task 7 implementation

**Sequence:**
1. Task 7: Enforce secrets via environment (30-45 min)
2. Set up real Coinbase credentials (10-15 min)
3. Task 9: PAPER rehearsal (24-48 hours runtime)
4. Task 10: LIVE burn-in validation

**ETA to PAPER Rehearsal:** 45-60 minutes (after Task 7 complete)

---

## Rollback to Task 7

Updating todo list to reflect dependency...
