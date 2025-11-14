# Spec Implementation Status

Status of the production trading specification implementation.

## ✅ Fully Implemented in Config

### 1. Risk Limits
```yaml
risk:
  max_total_at_risk_pct: 15.0           ✅ In config
  max_per_asset_pct: 5.0                ✅ Added (alias for max_position_size_pct)
  max_per_theme_pct:                    ✅ In config
    L2: 10.0
    MEME: 5.0
    DEFI: 10.0
  max_trades_per_day: 10                ✅ In config
  max_new_trades_per_hour: 4            ✅ Added
  daily_stop_pnl_pct: -3.0              ✅ In config + code reads it
  weekly_stop_pnl_pct: -7.0             ✅ In config
    min_trade_notional_usd: 15            ✅ In config + ExecutionEngine reads it
  cooldown:                             ✅ In config
    after_loss_trades: 3
    cooldown_minutes: 60
```

### 2. Liquidity Filters
```yaml
liquidity:
  min_24h_volume_usd: 20_000_000        ✅ Added (spec default)
  max_spread_bps: 60                    ✅ Added (spec default)
  min_depth_20bps_usd: 50_000           ✅ Added (spec requirement)
```

### 3. Triggers (NEW)
```yaml
triggers:
  price_move:
    pct_15m: 3.5                        ✅ Added
    pct_60m: 6.0                        ✅ Added
  volume_spike:
    ratio_1h_vs_24h: 1.8                ✅ Added
  breakout:
    lookback_hours: 24                  ✅ Added
  min_score: 0.2                        ✅ Added
```

### 4. Strategy (NEW)
```yaml
strategy:
  base_position_pct:                    ✅ Added
    tier1: 0.02
    tier2: 0.01
    tier3: 0.005
  max_open_positions: 8                 ✅ Added
  min_conviction_to_propose: 0.5        ✅ Added
```

### 5. Execution
```yaml
execution:
  default_order_type: "limit_post_only" ✅ Updated
  max_slippage_bps: 40                  ✅ Added
  hard_max_spread_bps: 60               ✅ Added
  cancel_after_seconds: 60              ✅ Added
  partial_fill_min_pct: 0.25            ✅ Added
  high_volatility:                      ✅ Added
    lookback_minutes: 60
    move_threshold_pct: 8.0
    size_reduction_factor: 0.5
```

---

## ⚠️ Needs Code Implementation

### 1. TriggerEngine - Price Move Detection
**Status:** TriggerEngine exists but doesn't use these params

**What's needed:**
```python
# core/triggers.py needs to read:
triggers_config = policy['triggers']
pct_15m = triggers_config['price_move']['pct_15m']  # 3.5%
pct_60m = triggers_config['price_move']['pct_60m']  # 6.0%

# Logic:
for symbol in universe:
    price_now = get_price(symbol)
    price_15m_ago = get_historical_price(symbol, minutes=15)
    price_60m_ago = get_historical_price(symbol, minutes=60)
    
    move_15m = abs((price_now - price_15m_ago) / price_15m_ago * 100)
    move_60m = abs((price_now - price_60m_ago) / price_60m_ago * 100)
    
    if move_15m >= pct_15m or move_60m >= pct_60m:
        candidates.append(symbol)
```

**Current behavior:** TriggerEngine.scan() returns empty list or basic triggers without price move logic.

---

### 2. TriggerEngine - Volume Spike Detection
**Status:** Not implemented

**What's needed:**
```python
# core/triggers.py needs:
ratio_threshold = triggers_config['volume_spike']['ratio_1h_vs_24h']  # 1.8

for symbol in universe:
    volume_24h = get_24h_volume(symbol)
    volume_1h = get_1h_volume(symbol)
    
    avg_hourly = volume_24h / 24
    ratio = volume_1h / avg_hourly
    
    if ratio >= ratio_threshold:
        candidates.append(symbol)
        score += 0.3  # volume spike adds to trigger score
```

---

### 3. TriggerEngine - Breakout Detection
**Status:** Not implemented

**What's needed:**
```python
# core/triggers.py needs:
lookback_hours = triggers_config['breakout']['lookback_hours']  # 24

for symbol in universe:
    price_now = get_price(symbol)
    high_24h = get_high(symbol, hours=lookback_hours)
    low_24h = get_low(symbol, hours=lookback_hours)
    volume_spike = check_volume_spike(symbol)  # From above
    
    if price_now >= high_24h and volume_spike:
        candidates.append(symbol)
        reason = "breakout_upside"
    elif price_now <= low_24h and volume_spike:
        candidates.append(symbol)
        reason = "breakdown_downside"
```

---

### 4. RulesEngine - Tier-based Sizing
**Status:** RulesEngine exists but doesn't use `strategy.base_position_pct`

**What's needed:**
```python
# strategy/rules_engine.py needs:
strategy_config = policy['strategy']
base_pct = strategy_config['base_position_pct']

for candidate in candidates:
    tier = universe.get_tier(candidate.symbol)  # tier1, tier2, tier3
    
    if tier == 'tier1':
        size_pct = base_pct['tier1']  # 0.02 (2%)
    elif tier == 'tier2':
        size_pct = base_pct['tier2']  # 0.01 (1%)
    else:
        size_pct = base_pct['tier3']  # 0.005 (0.5%)
    
    proposal = TradeProposal(
        symbol=candidate.symbol,
        side='BUY',
        size_pct=size_pct,  # Use tier-based sizing
        confidence=calculate_conviction(candidate),
        ...
    )
```

**Current behavior:** Uses `position_sizing.risk_per_trade_pct` (1.0%) for all tiers.

---

### 5. RulesEngine - Conviction Threshold
**Status:** Not enforced

**What's needed:**
```python
# strategy/rules_engine.py needs:
min_conviction = strategy_config['min_conviction_to_propose']  # 0.5

proposals = []
for candidate in candidates:
    conviction = calculate_conviction(candidate)  # Returns 0.0-1.0
    
    if conviction < min_conviction:
        logger.debug(f"Skipping {candidate.symbol}: conviction {conviction} < {min_conviction}")
        continue
    
    proposals.append(create_proposal(candidate, conviction))

if not proposals:
    return []  # Triggers "rules_engine_no_proposals"
```

---

### 6. RiskEngine - Max Open Positions
**Status:** Not enforced

**What's needed:**
```python
# core/risk.py needs:
strategy_config = policy['strategy']
max_open = strategy_config['max_open_positions']  # 8

current_positions = len(portfolio.open_positions)

for proposal in proposals:
    if proposal.side == 'BUY':
        if current_positions >= max_open:
            violations.append(f"max_open_positions_exceeded: {current_positions}/{max_open}")
            continue
```

---

### 7. RiskEngine - Per-Theme Limits Enforcement
**Status:** In config but not enforced in code

**What's needed:**
```python
# core/risk.py needs:
max_per_theme = risk_config['max_per_theme_pct']  # {'L2': 10.0, 'MEME': 5.0, ...}

for proposal in proposals:
    theme = universe.get_theme(proposal.symbol)  # 'L2', 'MEME', 'DEFI', etc.
    
    if theme in max_per_theme:
        current_theme_exposure = calculate_theme_exposure(portfolio, theme)
        new_theme_exposure = current_theme_exposure + proposal.size_usd
        
        limit = max_per_theme[theme] / 100 * portfolio.account_value_usd
        
        if new_theme_exposure > limit:
            violations.append(f"max_per_theme_pct_exceeded: {theme} {new_theme_exposure:.0f} > {limit:.0f}")
```

---

### 8. RiskEngine - Max New Trades Per Hour
**Status:** Config updated, not enforced in code

**What's needed:**
```python
# core/risk.py needs:
max_new_per_hour = risk_config['max_new_trades_per_hour']  # 4

# In StateStore, track trade timestamps
recent_trades = state_store.get_trades_last_hour()

if len(recent_trades) >= max_new_per_hour:
    return RiskCheckResult(
        approved=False,
        reason=f"max_new_trades_per_hour_exceeded: {len(recent_trades)}/{max_new_per_hour}",
        ...
    )
```

---

### 9. ExecutionEngine - Volatility-Aware Sizing
**Status:** Config added, not implemented in code

**What's needed:**
```python
# core/execution.py needs:
volatility_config = execution_config.get('high_volatility', {})
lookback_min = volatility_config.get('lookback_minutes', 60)
move_threshold = volatility_config.get('move_threshold_pct', 8.0)
size_reduction = volatility_config.get('size_reduction_factor', 0.5)

for proposal in proposals:
    price_now = get_price(proposal.symbol)
    price_1h_ago = get_historical_price(proposal.symbol, minutes=lookback_min)
    
    move_pct = abs((price_now - price_1h_ago) / price_1h_ago * 100)
    
    if move_pct >= move_threshold:
        logger.warning(f"High volatility detected for {proposal.symbol}: {move_pct:.1f}%")
        proposal.size_usd *= size_reduction  # Cut size in half
```

---

### 10. ExecutionEngine - Order Cancellation
**Status:** Config added, not implemented

**What's needed:**
```python
# core/execution.py needs:
cancel_after = execution_config.get('cancel_after_seconds', 60)

# After placing limit order:
order_id = place_limit_order(...)
start_time = time.time()

while True:
    time.sleep(5)
    status = check_order_status(order_id)
    
    if status == 'filled':
        break
    
    if time.time() - start_time > cancel_after:
        cancel_order(order_id)
        logger.warning(f"Cancelled order {order_id} after {cancel_after}s timeout")
        break
```

---

## 📊 Implementation Summary

| Component | Config | Code | Status |
|-----------|--------|------|--------|
| Risk limits | ✅ | ✅ | DONE |
| Liquidity filters | ✅ | ⚠️ | Config done, code uses microstructure.* |
| **Triggers - Price moves** | ✅ | ❌ | **NEEDS CODE** |
| **Triggers - Volume spike** | ✅ | ❌ | **NEEDS CODE** |
| **Triggers - Breakouts** | ✅ | ❌ | **NEEDS CODE** |
| **Strategy - Tier sizing** | ✅ | ❌ | **NEEDS CODE** |
| **Strategy - Conviction threshold** | ✅ | ❌ | **NEEDS CODE** |
| **Strategy - Max open positions** | ✅ | ❌ | **NEEDS CODE** |
| **Risk - Per-theme limits** | ✅ | ❌ | **NEEDS CODE** |
| **Risk - Hourly trade limit** | ✅ | ❌ | **NEEDS CODE** |
| Execution - Order type | ✅ | ✅ | DONE |
| **Execution - Volatility sizing** | ✅ | ❌ | **NEEDS CODE** |
| **Execution - Order cancellation** | ✅ | ❌ | **NEEDS CODE** |

---

## 🎯 Priority Order for Implementation

### P0 (Critical - System won't trade properly without these):
1. **TriggerEngine - Price moves** - Currently returns empty, no trades possible
2. **RulesEngine - Tier-based sizing** - Currently uses wrong sizing method
3. **RulesEngine - Conviction threshold** - Currently no quality filter

### P1 (Important - Risk management gaps):
4. **RiskEngine - Per-theme limits** - Prevents concentration risk
5. **RiskEngine - Max open positions** - Prevents over-diversification
6. **RiskEngine - Hourly trade limit** - Prevents runaway trading

### P2 (Nice to have - Enhancement features):
7. **TriggerEngine - Volume spike** - Improves signal quality
8. **TriggerEngine - Breakouts** - Adds momentum signals
9. **ExecutionEngine - Volatility sizing** - Risk-aware position sizing
10. **ExecutionEngine - Order cancellation** - Better order management

---

## 🚀 Next Steps

1. **Run system with current config** - Will fail at trigger stage (no candidates)
2. **Implement P0 items** - Get basic trading working
3. **Implement P1 items** - Close risk management gaps
4. **Implement P2 items** - Enhance signal quality
5. **Test with real data** - Validate behavior matches spec

---

## ✅ What Works Right Now

- Main loop with audit logging
- State persistence
- Signal handling (SIGINT/SIGTERM)
- Time-aware sleep
- Exception safety
- Basic risk checks (daily stop, trade frequency)
- Execution engine with quote currency routing
- Convert API integration
- All tests passing

**Current limitation:** System won't generate trades because TriggerEngine doesn't detect price moves/volume spikes yet.
