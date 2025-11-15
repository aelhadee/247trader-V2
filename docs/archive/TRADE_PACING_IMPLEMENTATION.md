# Trade Pacing Implementation

**Date:** 2025-11-13  
**Problem:** Bot was burning through hourly trade quota in first 10 minutes  
**Solution:** Implement pacing logic, not just caps

---

## The Problem

**Before pacing implementation:**
```
Cycle 1 (19:00:00): XRP trigger → TRADE ✅
Cycle 2 (19:01:00): XLM trigger → TRADE ✅  
Cycle 3 (19:02:00): DOGE trigger → TRADE ✅
Cycle 4 (19:03:00): XRP trigger → TRADE ✅
Cycle 5 (19:04:00): "Hourly trade limit reached (4/4)" ❌
... 56 minutes of NO_TRADE waiting for hourly reset
```

**Issue:** Just increasing `max_trades_per_hour` would move the wall but not fix the clustering behavior.

---

## Two-Layer Solution

### 1. **Pacing Controls** (Primary Throttle)
Shape *how* trades are distributed over time

### 2. **Frequency Caps** (Backup Guardrails)  
Last-resort protection against logic bugs or crazy markets

---

## Implementation

### A. Config Changes (`config/policy.yaml`)

```yaml
risk:
  # Trade frequency caps (backup guardrails, not primary throttle)
  max_trades_per_day: 40      # High enough to not bottleneck active trading
  max_trades_per_hour: 8      # Reasonable hourly cap (primary control is pacing)
  max_new_trades_per_hour: 8
  
  # Trade pacing (prevents burning quota in first 10 minutes)
  min_seconds_between_trades: 120  # Force 2min spacing between ANY trades (global)
  per_symbol_trade_spacing_seconds: 600  # 10min minimum between trades on same symbol
```

**Key changes:**
- **Lowered** `max_trades_per_hour` from 12 → 8 (realistic cap)
- **Raised** `max_trades_per_day` from 30 → 40 (high enough not to bottleneck)
- **Added** `min_seconds_between_trades: 120` (global 2-minute spacing)
- **Added** `per_symbol_trade_spacing_seconds: 600` (10-minute per-symbol spacing)

### B. RiskEngine Changes (`core/risk.py`)

#### 1. Global Trade Spacing Check
```python
def _check_global_trade_spacing(self) -> RiskCheckResult:
    """
    Check minimum time between ANY trades (global pacing).
    Prevents burning through hourly quota in first few minutes.
    """
    min_spacing = self.risk_config.get("min_seconds_between_trades", 0)
    # Check state.last_trade_timestamp
    # Reject if elapsed < min_spacing
```

**Effect:** Forces 2-minute gap between *any* trade, regardless of symbol.

#### 2. Enhanced Per-Symbol Filtering
```python
def _filter_cooled_symbols(self, proposals: List[TradeProposal]) -> List[TradeProposal]:
    """
    Two types of per-symbol filtering:
    1. Loss cooldowns: After losses/stop-outs (30-60 min)
    2. Trade pacing: Minimum time between ANY trades on same symbol (10 min)
    """
    # Check both is_cooldown_active() (loss-based)
    # AND per_symbol_last_trade timestamps (pacing)
```

**Effect:** 
- XRP can trigger every 60 seconds, but **only trades once per 10 minutes**
- Prevents churning the same asset repeatedly

#### 3. Updated check_all() Flow
```python
# 4. Global trade spacing (pacing control) ← NEW
result = self._check_global_trade_spacing()

# 4a. Trade frequency caps (backup guardrails) ← RENAMED
result = self._check_trade_frequency(proposals, portfolio)

# 5. Per-symbol cooldowns (includes pacing now) ← ENHANCED
proposals = self._filter_cooled_symbols(proposals)
```

### C. StateStore Changes (`infra/state_store.py`)

```python
def update_from_fills(self, filled_orders: list, portfolio: Any):
    """
    Tracks:
    - Global last_trade_timestamp (for global pacing)
    - Per-symbol last_trade_timestamp (for per-symbol pacing)
    """
    state["last_trade_timestamp"] = now.isoformat()  # Global
    state.setdefault("per_symbol_last_trade", {})[symbol] = now.isoformat()  # Per-symbol
```

---

## Expected Behavior

### Before (No Pacing):
```
19:00:00 → XRP  ✅
19:01:00 → XLM  ✅
19:02:00 → DOGE ✅
19:03:00 → XRP  ✅
19:04:00 → ❌ BLOCKED (4/4 hourly)
19:05-59 → ❌ NO_TRADE
```

### After (With Pacing):
```
19:00:00 → XRP  ✅
19:02:00 → ❌ BLOCKED (global spacing: 2min not elapsed)
19:02:30 → XLM ✅ (2min elapsed from last trade)
19:04:00 → ❌ BLOCKED (global spacing)
19:04:45 → DOGE ✅ (2min elapsed)
19:07:00 → ❌ BLOCKED (global spacing)
19:07:15 → SOL ✅ (2min elapsed, new symbol)
19:09:00 → ❌ BLOCKED (global spacing)
19:09:30 → ❌ XRP BLOCKED (per-symbol: 10min not elapsed since 19:00)
19:10:00 → ❌ BLOCKED (global spacing)
19:10:15 → XRP ✅ (10min elapsed from first XRP trade)
```

**Result:** 
- Trades spread evenly over hour
- Max ~30 trades/hour with 2min spacing (vs 8/hour cap)
- Per-symbol limit prevents churning same asset
- **Never** hit 8/hour cap unless truly active with many different symbols

---

## Rate vs Pacing

| Parameter | Old Value | New Value | Purpose |
|-----------|-----------|-----------|---------|
| `max_trades_per_hour` | 4 → 12 | **8** | Backup cap (rarely hit with pacing) |
| `max_trades_per_day` | 10 | **40** | High enough not to bottleneck |
| `min_seconds_between_trades` | ❌ None | **120** | Global spacing (primary control) |
| `per_symbol_trade_spacing_seconds` | ❌ None | **600** | Prevents symbol churning |

---

## Safety Analysis

**With 2-minute global spacing:**
- Theoretical max: 30 trades/hour
- **Hourly cap (8)** kicks in if you're trading 8+ different symbols actively
- **Daily cap (40)** allows full day of activity without hitting wall

**With 10-minute per-symbol spacing:**
- Each symbol trades max 6×/hour
- If universe has 10 eligible assets, all can trade once per 10-minute window
- **Prevents:** Scalping XRP every minute (would burn fees quickly)

**Fee projection with pacing:**
- 8 trades/hour × $15 avg × 0.6% taker = **$0.72/hour**
- 40 trades/day × $15 avg × 0.6% = **$3.60/day** (very reasonable)

---

## Rollback Plan

If pacing is too restrictive:

**Option 1: Tighten global, loosen per-symbol**
```yaml
min_seconds_between_trades: 90   # 1.5 min
per_symbol_trade_spacing_seconds: 300  # 5 min
```

**Option 2: Disable pacing, rely on caps**
```yaml
min_seconds_between_trades: 0   # Disabled
per_symbol_trade_spacing_seconds: 0  # Disabled
max_trades_per_hour: 12  # Raise cap
```

**Option 3: Aggressive day trader (not recommended)**
```yaml
min_seconds_between_trades: 60   # 1 min
per_symbol_trade_spacing_seconds: 180  # 3 min
max_trades_per_hour: 15
```

---

## Testing Checklist

- [ ] Restart bot: `./app_run_live.sh --loop`
- [ ] First trade executes normally
- [ ] Second trade blocked by global spacing (2min)
- [ ] Same-symbol trade blocked by per-symbol spacing (10min)
- [ ] Different-symbol trades allowed after global spacing
- [ ] Verify `state.last_trade_timestamp` updates after fills
- [ ] Verify `state.per_symbol_last_trade[symbol]` updates
- [ ] Check logs show spacing rejections with remaining time
- [ ] Confirm trades spread out over hour, not clustered
- [ ] Verify hourly cap (8) rarely/never hit with pacing active

---

## Log Examples

**Global spacing block:**
```
Risk check FAILED: Global trade spacing active (87s remaining, min 120s between trades)
```

**Per-symbol pacing block:**
```
Filtered XRP-USD: per-symbol trade pacing active (423s remaining, min 600s between trades)
```

**Trade executed:**
```
Executed 1 order(s)
StateStore: Updated last_trade_timestamp = 2025-11-13T19:15:32
StateStore: Updated per_symbol_last_trade[XRP-USD] = 2025-11-13T19:15:32
```

---

## Philosophy

> **Caps are guardrails. Pacing is the steering wheel.**

- **Without pacing:** Bot drives at max speed until hitting wall
- **With pacing:** Bot drives at steady pace, rarely needs guardrails

This matches professional day trading: **controlled entries spaced appropriately**, not frantic scalping every minute.

---

**Status:** ✅ Implementation complete, ready for testing
