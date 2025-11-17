# Exposure Cap Analysis - Quick Reference

**When You See:** `RISK_REJECT * reason=below_min_after_caps`

---

## What It Means

**System is working correctly.** Position has reached risk limits and can't accept new trades that meet exchange minimums.

### Three-Way Constraint Collision

```
Exchange Minimum:  size ≥ $5.00 (Coinbase requirement)
Risk Cap Headroom: size ≤ $1.34 (per-asset/tier limits)
                   ↓
          No valid trade size → REJECT
```

---

## Diagnostic Pattern

```
RISK_REJECT HBAR-USD BUY reason=below_min_after_caps
(rules want $5.00, cap allows $1.34, but min_notional requires $5.00)
- insufficient capacity for minimum trade size; 
  consider closing position to free $3.66
```

**Translation:**
- Position is **$3.66 away** from allowing another $5 trade
- Need to **close/trim** existing position OR **raise caps**

---

## Quick Decision Tree

### 1. Is This Expected?
**YES** if position near/at configured max size  
→ **Action:** None (system protecting you)

**NO** if you want more exposure  
→ **Go to Step 2**

### 2. What Do You Want?

#### A. Keep Current Risk Profile (RECOMMENDED)
- Accept no more buys for this asset
- Let other triggers/assets trade
- **Confidence:** 90% GO for small accounts

#### B. Allow Larger Positions
**Option 1 - Increase Caps:**
```yaml
# config/policy.yaml
profiles:
  day_trader:
    max_position_pct:
      T2: 5.0  # Was lower, now allows more headroom
```

**Option 2 - Increase NAV:**
- Fund account more (e.g., $500+)
- Same % = bigger $ → more headroom
- Best long-term solution

#### C. Free Up Capacity
Run liquidation script:
```bash
python liquidate_holdings.py --symbols HBAR --pct 50
```
Or use auto-trim (already enabled in policy)

---

## Math Behind "Consider Closing Position to Free $X"

```python
# From risk rejection message
shortage = min_notional - cap_headroom
# "consider closing position to free $3.66"

# To unblock: need cap_headroom ≥ min_notional
required_headroom = min_notional_usd  # $5.00
current_headroom = 1.34               # from caps
shortage = 5.00 - 1.34 = 3.66         # ✓ matches message

# Solution: Close/trim ≥ $3.66 of position
```

**Practical:** Close/trim in $5 increments (exchange minimums)

---

## When to Worry

### 🟢 Normal (No Action)
```
RISK_REJECT HBAR-USD BUY reason=below_min_after_caps
```
- Single asset hitting cap
- Other proposals still trading
- Position count < max_positions

### 🟡 Review Settings
```
RISK_REJECT [multiple assets] reason=below_min_after_caps
```
- Many assets hitting caps simultaneously
- Consider: caps too tight for current NAV
- Check: `max_position_pct` vs NAV vs `min_notional`

### 🔴 Investigate
```
ALL proposals blocked by exposure caps
+ position_count = max_positions
```
- Portfolio fully allocated
- No capacity for any trades
- Action: Rebalance, trim, or accept no-trade

---

## Small Account ($200-300) Specifics

### Minimum Headroom Rule
```
Per-Asset Headroom ≥ $5.00 to allow new trades
```

**Example:** NAV = $250, T2 cap = 4.0%
```
Max position size: $250 × 0.04 = $10.00
If current position: $6.50
Then headroom: $10.00 - $6.50 = $3.50 < $5.00 ❌
```

**Solution:**
- Raise cap to 6.0% → headroom = $15 - $6.50 = $8.50 ✓
- Or close $2+ of position → headroom = $5.50 ✓

### Recommended Caps for $250 NAV

```yaml
profiles:
  day_trader:
    max_position_pct:
      T1: 8.0%   # $20 max → $15 headroom after $5 trade
      T2: 5.0%   # $12.50 max → $7.50 headroom after $5 trade
      T3: 3.0%   # $7.50 max → $2.50 headroom (tight!)
```

**Rule:** `max_pct × NAV - min_notional ≥ min_notional`  
Ensures at least 2 trades possible before hitting cap.

---

## Monitoring Commands

### Check Current Exposure
```bash
python diagnose_exposure.py
```

### Check Position Sizes
```bash
python check_portfolio.py | grep -E "(HBAR|NAV|exposure)"
```

### Watch Risk Rejections
```bash
tail -f logs/live_*.log | grep "RISK_REJECT.*below_min_after_caps"
```

---

## FAQ

**Q: Why not just lower min_notional to $2?**  
A: Can't go below exchange minimum (Coinbase enforces $5). Orders would be rejected.

**Q: Why does it say "rules want $5.00" when proposal was 1.9%?**  
A: Min notional clamping rounds up: 1.9% × $256.97 = $4.88 → $5.00 (minimum)

**Q: Can I disable exposure caps temporarily?**  
A: Not recommended. That's how "disciplined bot" becomes "memecoin degen script". Caps exist to protect you.

**Q: What if I want to trade but all assets are capped?**  
A: Options:
1. Liquidate underperformers (free capacity)
2. Increase NAV (more $ = more headroom)
3. Raise caps (more risk concentration)
4. Accept no-trade (wait for exits/stops)

---

## Related Docs

- `MIN_NOTIONAL_ENFORCEMENT_2025-11-16.md` - Sizing fix details
- `SMALL_ACCOUNT_CALIBRATION_QUICK_REF.md` - Tier sizing for small NAV
- `AUTO_PURGE_OPERATIONAL_GUIDE.md` - Position cleanup

---

**TL;DR:** `below_min_after_caps` = position too close to limit to allow $5+ trade. Either trim existing position, raise caps, or accept no more buys. This is risk protection working correctly, not a bug.
