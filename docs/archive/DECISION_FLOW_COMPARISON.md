# Trading Decision Flow - Actual vs Spec

Visual comparison of what happens now vs what should happen.

## Current Behavior (As Implemented)

```
Cycle Start
    ↓
Universe Building ✅
    → Loads from universe.yaml
    → Filters by regime (chop/bull/bear)
    → Returns 10 tier1 assets
    ↓
TriggerEngine.scan() ❌
    → Currently returns [] (empty list)
    → NO price move detection implemented
    → NO volume spike detection implemented
    → NO breakout detection implemented
    → Result: NO_TRADE reason = "no_candidates_from_triggers"
    ↓
[STOPS HERE - No proposals generated]
```

---

## Spec Behavior (What Should Happen)

```
Cycle Start
    ↓
1. Universe Building ✅
    → min_24h_volume >= $20M
    → max_spread <= 0.60%
    → min_depth_20bps >= $50K
    → Tier1: BTC, ETH, SOL (always)
    → Tier2: Auto-qualified assets
    → Tier3: Disabled by default
    ↓
2. Trigger Scanning (NEEDS IMPLEMENTATION)
    For each symbol in universe:
    
    ┌─ Price Move Trigger ❌
    │   → |Δ15m| >= 3.5%? → Score +0.3
    │   → |Δ60m| >= 6.0%? → Score +0.4
    │
    ┌─ Volume Spike Trigger ❌
    │   → 1h_vol / avg_hourly >= 1.8x? → Score +0.3
    │
    └─ Breakout Trigger ❌
        → New 24h high + volume spike? → Score +0.4
        → New 24h low + volume spike? → Score +0.4
    
    If score >= 0.2 → Add to candidates
    If no candidates → NO_TRADE ("no_candidates_from_triggers") ✅
    ↓
3. Rules Engine (NEEDS TIER-BASED SIZING)
    For each candidate:
    
    Calculate conviction (0.0-1.0):
    - Trend strength
    - Volume confirmation
    - Spread/depth quality
    - Current position size
    
    If conviction < 0.5 → Skip ❌
    
    Determine size by tier: ❌
    - Tier1 → 2% NLV
    - Tier2 → 1% NLV
    - Tier3 → 0.5% NLV
    
    Create proposal:
    - symbol
    - side (BUY/SELL)
    - size_pct (from tier)
    - conviction
    - reason
    
    If no proposals → NO_TRADE ("rules_engine_no_proposals") ✅
    ↓
4. Risk Checks
    
    Global checks: ✅
    - daily_pnl <= -3%? → Block all new entries
    - weekly_pnl <= -7%? → Tighten
    - trades_today >= 10? → Block
    - trades_this_hour >= 4? → Block ❌ (not enforced)
    - cooldown active? → Block
    
    Per-proposal checks:
    - New position > 5% NLV? → Reject ✅
    - New theme exposure > limit? → Reject ❌ (not enforced)
    - Total at-risk > 15% NLV? → Reject ✅
    - Size < $100? → Reject ✅ (currently $5 for small account)
    - Open positions >= 8? → Reject ❌ (not enforced)
    
    If all rejected → NO_TRADE ("all_proposals_blocked_by_risk") ✅
    ↓
5. Execution Filter
    
    For each approved proposal:
    
    Adjust size to available capital: ✅
    - Recompute size_usd = size_pct * NLV
    - If > available → Adjust down to 99% of balance
    
    Check microstructure: ⚠️
    - Spread <= 60 bps? (uses 100 bps from microstructure.*)
    - Slippage <= 40 bps? (uses 50 bps from microstructure.*)
    - Depth sufficient? (checks min_orderbook_depth_usd)
    
    Volatility check: ❌
    - Move_1h > 8%? → Cut size in half
    
    Find trading pair: ✅
    - Try preferred quotes: USDC, USD, USDT, BTC, ETH
    - If none work → Suggest conversion
    
    If no valid orders → NO_TRADE ("no_orders_after_execution_filter") ✅
    ↓
6. Order Placement
    
    Mode = DRY_RUN: ✅
    - Log simulated orders
    - No real execution
    
    Mode = LIVE + read_only=false: ✅
    - Place limit_post_only orders
    - Track order IDs
    - Update state after fills
    
    Order cancellation: ❌
    - Should cancel after 60s if not filled
    - Currently: fire and forget
    ↓
7. State Update ✅
    - state_store.update_from_fills(orders)
    - Increment trades_today, trades_this_hour
    - Log events
    ↓
8. Audit Log ✅
    - Write JSONL entry with full decision tree
    - Include NO_TRADE reasons
    - Include risk violations
    ↓
Cycle Complete
```

---

## Example: BTC Move Scenario

### Current System Behavior:
```
15:00 - BTC pumps 4.2% in 15 minutes
15:05 - Cycle runs
        → Universe: BTC in tier1 ✅
        → Triggers: scan() returns [] ❌
        → NO_TRADE: "no_candidates_from_triggers"
        → MISSED OPPORTUNITY
```

### Spec Behavior:
```
15:00 - BTC pumps 4.2% in 15 minutes
15:05 - Cycle runs
        → Universe: BTC in tier1 ✅
        → Triggers:
            ✓ Price move: 4.2% > 3.5% threshold → Score +0.3
            ✓ Volume spike: 1h vol 2.1x avg → Score +0.3
            ✓ Total score: 0.6 >= 0.2 threshold
            → BTC added to candidates ✅
        → Rules:
            ✓ Conviction: 0.72 (strong move + volume)
            ✓ Tier1 sizing: 2% NLV = $11.52 (on $576 account)
            → Proposal created ✅
        → Risk:
            ✓ Daily PnL: 0% (no trades yet)
            ✓ Position size: 2% < 5% limit
            ✓ Total at-risk: 2% < 15% limit
            ✓ Size: $11.52 > $5 minimum
            ✓ Trades today: 0 < 10 limit
            → Proposal approved ✅
        → Execution:
            ✓ Find pair: BTC-USDC exists
            ✓ Balance: $0.07 USDC available
            ✗ Insufficient: need $11.52, have $0.07
            → Suggest: liquidate holdings to USDC first
            → NO_TRADE: "insufficient_quote_currency"
```

---

## Example: After Liquidation

### With Holdings Liquidated to USDC ($400 available):
```
15:00 - ETH moves 3.8% in 15 minutes
15:05 - Cycle runs
        → Universe: ETH in tier1 ✅
        → Triggers:
            ✓ Price move: 3.8% > 3.5% → Score +0.3
            ✓ Volume spike: 1.9x avg → Score +0.3
            → ETH added to candidates ✅
        → Rules:
            ✓ Conviction: 0.68
            ✓ Tier1 sizing: 2% * $576 = $11.52
            → Proposal created ✅
        → Risk:
            ✓ All checks pass
            → Proposal approved ✅
        → Execution:
            ✓ Find pair: ETH-USDC exists
            ✓ Balance: $400 USDC available
            ✓ Size: $11.52 < $400
            ✓ Spread: 0.42% < 0.60% limit
            ✓ Depth: $82K > $50K limit
            ✓ Volatility: 3.8% < 8.0% (no size cut)
            → Order placed: BUY 0.0029 ETH @ $3,985 ✅
        → State:
            ✓ trades_today: 0 → 1
            ✓ positions: {} → {"ETH": 0.0029}
        → Audit:
            ✓ Logged: EXECUTED, 1 order, $11.52
        → TRADE SUCCESSFUL ✅
```

---

## Key Gaps Summary

| Decision Point | Current | Spec | Impact |
|----------------|---------|------|--------|
| Price move detection | ❌ | 3.5%/15m, 6%/60m | **No triggers fire** |
| Volume spike detection | ❌ | 1.8x ratio | **No confirmation** |
| Breakout detection | ❌ | 24h high/low | **Misses momentum** |
| Tier-based sizing | ❌ | 2%/1%/0.5% | **Wrong position sizes** |
| Conviction filter | ❌ | >= 0.5 threshold | **No quality filter** |
| Per-theme limits | ❌ | 10% L2, 5% MEME | **Concentration risk** |
| Max open positions | ❌ | 8 limit | **Over-diversification** |
| Hourly trade limit | ❌ | 4/hour | **Runaway trading** |
| Volatility sizing | ❌ | Cut 50% if >8% move | **No vol adjustment** |
| Order cancellation | ❌ | 60s timeout | **Orphaned orders** |

**Critical path blocker:** TriggerEngine returns empty → No trades possible.

**Fix priority:**
1. Implement price move triggers (unblocks trading)
2. Implement tier-based sizing (correct position sizes)
3. Implement conviction threshold (quality filter)
4. Implement risk limit enforcement (safety)
