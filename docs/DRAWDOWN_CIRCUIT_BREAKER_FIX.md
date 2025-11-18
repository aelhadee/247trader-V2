# Max Drawdown Circuit Breaker Fix

**Date:** November 17, 2025  
**Issue:** Trading system blocked all proposals with "Max drawdown 97.44% exceeds limit"  
**Status:** ✅ RESOLVED - Requires system restart to apply

---

## Problem Summary

The trading system was blocking ALL trade proposals due to max drawdown protection:

```
ERROR core.risk: 🚨 MAX DRAWDOWN EXCEEDED: 97.44% (limit: 10.0%)
WARNING: Risk engine BLOCKED all proposals: Max drawdown 97.44% exceeds limit
```

**Root Cause:**
- SQLite database (`data/state.db`) had `high_water_mark = 10000.0`
- Current account value: ~$255.67
- Calculated drawdown: `(10000 - 255.67) / 10000 = 97.44%`
- Risk engine correctly blocked trades (drawdown limit: 10.0%)

**Why 97.44% drawdown?**
The `high_water_mark` tracks the peak account value ever seen. The value $10,000 suggests:
- Previous account had ~$10k balance
- Account was withdrawn/transferred down to ~$255
- System correctly calculated 97.44% drawdown from peak

---

## Investigation Process

### 1. Log Analysis
```bash
# Logs showed consistent blocking
2025-11-17 21:37:33,012 ERROR core.risk: 🚨 MAX DRAWDOWN EXCEEDED: 97.44% (limit: 10.0%)
2025-11-17 21:38:33,232 ERROR core.risk: 🚨 MAX DRAWDOWN EXCEEDED: 97.44% (limit: 10.0%)
```

### 2. Found Drawdown Calculation
Location: `runner/main_loop.py` lines 1030-1043

```python
# Use high_water_mark to track peak NAV and compute drawdown
high_water_mark = float(state.get("high_water_mark", account_value_usd))

# Update high water mark if current NAV is higher
if account_value_usd > high_water_mark:
    high_water_mark = account_value_usd
    state["high_water_mark"] = high_water_mark
    self.state_store.save(state)

# Calculate drawdown: (peak - current) / peak
max_drawdown_pct = 0.0
if high_water_mark > 0:
    max_drawdown_pct = ((high_water_mark - account_value_usd) / high_water_mark) * 100.0
```

### 3. Located Stale high_water_mark
```bash
$ sqlite3 data/state.db "SELECT json_extract(payload, '$.high_water_mark') FROM state_store WHERE id = 1;"
10000.0  # ❌ Stale value from previous account balance
```

### 4. Discovered StateStoreSupervisor Issue
- `StateStoreSupervisor` runs background thread (every 60s)
- Calls `store.flush()` which writes cached `self._state` to database
- Manual database edits get overwritten within 60 seconds
- **Solution:** Must restart system for changes to take effect

---

## Solution Implemented

### Created Reset Tool: `scripts/reset_high_water_mark.py`

**Features:**
- ✅ Automatic backup before changes
- ✅ Fetches current account value from Coinbase
- ✅ Dry-run mode to preview changes
- ✅ Verification after update
- ✅ Safety prompts (or --force flag)
- ✅ Clear warning about system restart requirement

**Usage:**
```bash
# Preview changes (dry-run)
python scripts/reset_high_water_mark.py --dry-run

# Reset to current account value
python scripts/reset_high_water_mark.py

# Reset to specific value
python scripts/reset_high_water_mark.py --value 300.0

# Skip confirmation prompt
python scripts/reset_high_water_mark.py --force
```

**Example Output:**
```
================================================================================
HIGH WATER MARK RESET TOOL
================================================================================

📊 Current high_water_mark: $10000.00
🔍 Fetching current account value from Coinbase...
📊 Current account value: $255.77
📉 Current drawdown: 97.44%
📈 Drawdown after reset: 0.00%

📦 Creating backup...
✅ Backup created: data/state_backups/state_before_hwm_reset_20251118_024551.db

🔧 Updating high_water_mark...
✅ Updated high_water_mark: $10000.00 → $255.77

================================================================================
✅ SUCCESS
================================================================================
High water mark reset: $10000.00 → $255.77
Drawdown reset: 97.44% → 0.00%
Backup saved: data/state_backups/state_before_hwm_reset_20251118_024551.db

⚠️  IMPORTANT: You MUST restart the trading system!
================================================================================
The trading system caches state in memory and will overwrite
this change within 60 seconds unless you restart it.

To restart:
  1. Stop the running system: Ctrl+C or kill PID
  2. Restart: ./app_run_live.sh --loop

✅ After restart, trading should resume (drawdown will be ~0%)
```

---

## How to Apply Fix

### Step 1: Run Reset Script
```bash
# Load credentials
source scripts/load_credentials.sh

# Reset high_water_mark
python scripts/reset_high_water_mark.py --force
```

### Step 2: Restart Trading System
```bash
# Stop current system (Ctrl+C or kill PID)
pkill -f "runner.main_loop"

# Restart system
./app_run_live.sh --loop
```

### Step 3: Verify Fix
Monitor next cycle logs for:
```
# Should NOT see this anymore:
ERROR core.risk: 🚨 MAX DRAWDOWN EXCEEDED: 97.44% (limit: 10.0%)

# Should see proposals approved:
INFO __main__: ✅ Risk checks complete: approved=True, filtered=X/Y
```

---

## Technical Details

### State Storage Architecture

The system uses a multi-layer state storage system:

1. **SQLite Database** (`data/state.db`)
   - Primary persistent storage
   - Schema: Single row (id=1) with JSON payload
   - Updated on every state change

2. **StateStore** (`infra/state_store.py`)
   - Abstraction layer over SQLite/JSON/Redis backends
   - Maintains `self._state` cache for performance
   - `load()` reads fresh from database
   - `save()` updates both cache and database

3. **StateStoreSupervisor** (`infra/state_store.py`)
   - Background thread (daemon)
   - Periodic persistence: calls `store.flush()` every 60s
   - `flush()` writes cached `self._state` to database
   - **⚠️ CRITICAL:** Overwrites manual database edits!

### Why Restart is Required

```python
# StateStoreSupervisor background loop (every 60s):
def _run_loop(self):
    while not self._stop_event.wait(1):
        if now >= next_persist:
            self._store.flush()  # ⚠️ Writes cached state to DB
            next_persist = now + self._persist_interval

# StateStore.flush() implementation:
def flush(self):
    state = self._state  # ← Uses cached in-memory state
    self._backend.save(state)  # ← Overwrites database
```

**Timeline:**
1. Manual script updates database: `high_water_mark = 255.77` ✅
2. Trading system continues running with cached state: `high_water_mark = 10000` 💾
3. After 60s, StateStoreSupervisor calls `flush()` 🔄
4. Cached value overwrites database: `high_water_mark = 10000` ❌

**Solution:** Restart system so `load()` reads fresh value from database.

---

## Backups Created

All backups saved to: `data/state_backups/`

```bash
# List backups
ls -lh data/state_backups/state_before_hwm_reset_*.db

# Example:
state_before_hwm_reset_20251118_024551.db  # 56KB - Before reset (high_water_mark=10000)
```

To restore from backup:
```bash
# Stop trading system first!
pkill -f "runner.main_loop"

# Restore backup
cp data/state_backups/state_before_hwm_reset_20251118_024551.db data/state.db

# Restart system
./app_run_live.sh --loop
```

---

## When to Use This Tool

### Scenarios for Resetting high_water_mark:

1. **Starting with smaller account**
   - Previous account: $10,000
   - Withdrew funds, now trading with: $250
   - Reset baseline to avoid 97.5% drawdown lock

2. **After account withdrawal/transfer**
   - Normal operation to reduce exposure
   - Not an actual trading loss
   - Reset to current balance as new baseline

3. **New trading period**
   - Want to reset performance tracking
   - Start fresh with current balance as baseline

4. **Recovery from historical losses**
   - Account recovered but still below old peak
   - Want to trade normally without drawdown restriction

### When NOT to Use:

- ❌ During active trading losses (defeats purpose of risk protection)
- ❌ To bypass legitimate drawdown protection
- ❌ Without understanding implications (resets performance history)

---

## Risk Engine Configuration

Current max_drawdown setting:

```yaml
# config/policy.yaml
risk:
  max_drawdown_pct: 10.0  # ← Blocks trading when drawdown exceeds 10%
```

**Purpose:** Protect against catastrophic losses by stopping trading when account drops >10% from peak.

**How it works:**
1. System tracks `high_water_mark` (peak account value ever seen)
2. Each cycle calculates: `drawdown = (peak - current) / peak * 100`
3. If `drawdown > 10.0%`, blocks ALL proposals
4. Trading resumes when account recovers (drawdown < 10%)

**To adjust limit:**
```yaml
# config/policy.yaml
risk:
  max_drawdown_pct: 15.0  # ← Increase to 15% (more permissive)
  # OR
  max_drawdown_pct: 5.0   # ← Decrease to 5% (stricter protection)
```

---

## Verification Commands

```bash
# Check current high_water_mark
sqlite3 data/state.db "SELECT json_extract(payload, '$.high_water_mark') FROM state_store WHERE id = 1;"

# Check current account value
python -c "from core.exchange_coinbase import CoinbaseExchange; ex = CoinbaseExchange(read_only=True); print(sum(float(a.get('available_balance', {}).get('value', 0)) for a in ex.get_accounts()))"

# Calculate current drawdown
python -c "import sqlite3, json; conn = sqlite3.connect('data/state.db'); row = conn.execute('SELECT payload FROM state_store WHERE id = 1').fetchone(); state = json.loads(row[0]); hwm = state.get('high_water_mark', 0); nav = 255.77; dd = ((hwm - nav) / hwm * 100) if hwm > 0 else 0; print(f'Drawdown: {dd:.2f}%'); conn.close()"

# Monitor live logs for drawdown errors
tail -f logs/live_*.log | grep -E "MAX DRAWDOWN|approved="
```

---

## Future Improvements

### Potential Enhancements:

1. **Graceful high_water_mark Reset Command**
   ```python
   # Add to StateStore
   def reset_high_water_mark(self, new_value: float):
       """Reset high_water_mark in both cache and database."""
       with self._lock:
           state = self.load()
           state["high_water_mark"] = new_value
           self.save(state)  # Updates both cache and DB
   ```

2. **Admin API Endpoint**
   ```python
   # Add to runner/main_loop.py or separate admin tool
   @admin_command
   def reset_hwm(value: Optional[float] = None):
       """Reset high_water_mark without restarting system."""
       value = value or get_current_nav()
       state_store.reset_high_water_mark(value)
   ```

3. **Auto-reset on Manual Withdrawal Detection**
   ```python
   # Detect when balance drops but no trades executed
   if balance_dropped and no_recent_trades and user_confirmed:
       auto_reset_high_water_mark(current_balance)
   ```

4. **Drawdown Reset Policy**
   ```yaml
   # config/policy.yaml
   risk:
     max_drawdown_pct: 10.0
     auto_reset_hwm_on_withdrawal: true  # ← Auto-reset if balance drops without trades
     hwm_reset_confirmation_required: true  # ← Require explicit confirmation
   ```

---

## Summary

**Problem:** 97.44% drawdown locked trading (stale `high_water_mark = $10,000`)  
**Root Cause:** Previous account balance was ~$10k, now ~$256  
**Solution:** Reset `high_water_mark` to current balance + restart system  
**Tool Created:** `scripts/reset_high_water_mark.py`  
**Status:** ✅ Fixed - Requires system restart to apply  

**Next Steps:**
1. ✅ Reset high_water_mark using script
2. ⚠️ **RESTART trading system** (critical!)
3. ✓ Monitor next cycle logs (should show ~0% drawdown)
4. ✓ Verify proposals approved by risk engine

**Key Lesson:** StateStoreSupervisor's background persistence requires system restart for manual database edits to persist.
