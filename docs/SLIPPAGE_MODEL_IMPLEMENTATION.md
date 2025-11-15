# Slippage Model Implementation

**Date:** 2025-11-15  
**Status:** ✅ Complete (20 tests passing, integrated into BacktestEngine)  
**Impact:** HIGH - Backtests now account for real-world trading costs

---

## Overview

Implemented realistic slippage and fee model for backtesting to ensure strategies profitable in simulation remain profitable in live trading. Model captures Coinbase Advanced Trade API costs including:

- Exchange fees (maker/taker)
- Bid-ask spread slippage
- Market impact for large orders
- Tier-based liquidity differences

## Problem Statement

**Before:** Backtests used mid price for both entry and exit, ignoring fees and slippage. This created unrealistic expectations:
- Assumed perfect fills at mid price
- No accounting for Coinbase fees (40-60 bps)
- No consideration of bid-ask spread
- Large orders had no impact modeling
- Strategies appeared more profitable than reality

**Risk:** Deploy strategy that looks profitable in backtest but loses money in live trading due to hidden costs.

## Solution Architecture

### Core Components

**1. SlippageConfig** - Configurable parameters
```python
@dataclass
class SlippageConfig:
    maker_fee_bps: float = 40.0      # Coinbase standard tier
    taker_fee_bps: float = 60.0
    tier1_slippage_bps: float = 10.0  # BTC/ETH tight spreads
    tier2_slippage_bps: float = 25.0  # Liquid altcoins
    tier3_slippage_bps: float = 50.0  # Long-tail assets
    default_order_type: str = "taker" # Conservative assumption
```

**2. SlippageModel** - Calculation engine
```python
class SlippageModel:
    def calculate_fill_price(mid_price, side, tier, order_type, notional_usd)
    def calculate_total_cost(fill_price, quantity, side, order_type)
    def calculate_pnl(entry_price, exit_price, quantity, entry_order_type, exit_order_type)
    def simulate_fill(mid_price, side, quantity, tier, order_type)
```

### Slippage Calculation

**Base Slippage:**
- `fill_price = mid ± (tier_slippage_bps / 10000)`
- Buy pays more: `mid + slippage`
- Sell receives less: `mid - slippage`

**Market Impact Multiplier:**
```python
# Large orders move the market more
if notional_usd > 10_000:
    impact_multiplier = 1.0 + log10(notional_usd / 10_000) * 0.2
    effective_slippage = base_slippage * impact_multiplier
```

**Example:**
- $10k order: 1.0x multiplier (no additional impact)
- $100k order: 1.2x multiplier (+20% slippage)
- $1M order: 1.4x multiplier (+40% slippage)

### Fee Calculation

**Exchange Fees:**
- Maker: 40 bps (0.40%)
- Taker: 60 bps (0.60%)

**Total Cost (Buy):**
```
gross_notional = fill_price × quantity
fee = gross_notional × fee_bps / 10000
total_cost = gross_notional + fee
```

**Total Proceeds (Sell):**
```
gross_notional = fill_price × quantity
fee = gross_notional × fee_bps / 10000
net_proceeds = gross_notional - fee
```

### PnL Calculation

**Round-Trip Fees:**
```python
entry_cost = entry_price × quantity × (1 + entry_fee_bps / 10000)
exit_proceeds = exit_price × quantity × (1 - exit_fee_bps / 10000)
pnl_usd = exit_proceeds - entry_cost
pnl_pct = (pnl_usd / entry_cost) × 100
```

**Example (BTC Trade):**
- Buy 1 BTC @ $50,000 (taker)
  - Fill: $50,055 (mid $50k + 10bps tier1 + 1.1x impact)
  - Fee: $300.33 (60bps)
  - Total: $50,355.33
- Sell 1 BTC @ $52,000 (taker)
  - Fill: $51,945 (mid $52k - 10bps tier1 - 1.1x impact)
  - Fee: $311.67 (60bps)
  - Proceeds: $51,633.33
- **Net PnL: $1,278 (2.54%)**
  - Gross gain: $2,000 (4%)
  - Slippage cost: $110 (entry $55 + exit $55)
  - Fee cost: $612 (entry $300 + exit $312)

## Integration with BacktestEngine

### Modified Methods

**1. _execute_proposal() - Entry with Slippage**
```python
# Get asset tier (tier1/tier2/tier3)
tier = getattr(asset, "tier", "tier2")

# Calculate realistic fill price
fill_price = self.slippage_model.calculate_fill_price(
    mid_price=mid_price,
    side="buy",
    tier=tier,
    order_type="taker",
    notional_usd=size_usd
)

# Calculate total cost including fees
gross_notional, total_cost = self.slippage_model.calculate_total_cost(
    fill_price=fill_price,
    quantity=quantity,
    side="buy",
    order_type="taker"
)
```

**2. _close_trade() - Exit with Slippage**
```python
# Calculate exit fill price
exit_fill_price = self.slippage_model.calculate_fill_price(
    mid_price=mid_price,
    side="sell",  # Opposite of entry
    tier=tier,
    order_type="taker",
    notional_usd=trade.size_usd
)

# Calculate PnL with fees on both sides
pnl_usd, pnl_pct, total_fees = self.slippage_model.calculate_pnl(
    entry_price=trade.entry_price,
    exit_price=exit_fill_price,
    quantity=quantity,
    entry_order_type="taker",
    exit_order_type="taker"
)
```

## Testing

### Test Coverage (20 tests, all passing)

**TestSlippageConfig:**
- ✅ Default config has reasonable values
- ✅ Maker fees < taker fees
- ✅ Tier slippage ordered correctly (tier1 < tier2 < tier3)

**TestFillPriceCalculation:**
- ✅ Buy pays more than mid
- ✅ Sell receives less than mid
- ✅ Tier1 has lower slippage than tier2/tier3
- ✅ Larger orders have more market impact
- ✅ Invalid mid price raises error

**TestFeeCalculation:**
- ✅ Maker fees lower than taker
- ✅ Buy adds fee to cost
- ✅ Sell subtracts fee from proceeds
- ✅ Fee scales with notional

**TestPnLCalculation:**
- ✅ Winning trade PnL accurate
- ✅ Losing trade PnL accurate
- ✅ Breakeven trade loses to fees
- ✅ Maker orders have lower fees than taker
- ✅ PnL percentage calculated correctly

**TestFullSimulation:**
- ✅ simulate_fill returns all details
- ✅ BTC buy simulation realistic
- ✅ Altcoin sell simulation realistic

**TestCustomConfig:**
- ✅ Custom fee structure applies
- ✅ Custom slippage budgets apply

### Backtest Integration Tests

All 17 backtest regression tests passing:
- ✅ Deterministic with same seed
- ✅ Different seeds produce different results
- ✅ JSON report export works
- ✅ Regression gates function
- ✅ Full workflow integration

## Impact Analysis

### Before vs After

**Scenario: 100 trades, 55% win rate, avg +5% wins, avg -3% losses**

**Before (No Fees/Slippage):**
- 55 wins @ +5% = +275%
- 45 losses @ -3% = -135%
- **Net: +140%**

**After (With Fees/Slippage):**
- 55 wins @ +3.4% (after 120 bps round-trip) = +187%
- 45 losses @ -4.2% (fees widen losses) = -189%
- **Net: -2%**

**Reality Check:** Strategy must achieve **~58% win rate** to break even with real costs.

### Cost Breakdown

**Per Trade Costs (Conservative):**
- Entry slippage: 10-50 bps (tier dependent)
- Entry fee: 60 bps (taker)
- Exit slippage: 10-50 bps
- Exit fee: 60 bps (taker)
- **Total: 140-220 bps per round trip**

**Implications:**
- Need +1.5-2.5% profit to break even after costs
- Higher frequency = more fee drag
- Large positions = more slippage
- Maker orders save 40 bps vs taker (but uncertain fills)

## Configuration Options

### Conservative (Default)
```python
config = SlippageConfig(
    maker_fee_bps=40.0,
    taker_fee_bps=60.0,
    tier1_slippage_bps=10.0,
    tier2_slippage_bps=25.0,
    tier3_slippage_bps=50.0,
    default_order_type="taker"  # Assume worst case
)
```

### Optimistic (Lower Tier / Maker Orders)
```python
config = SlippageConfig(
    maker_fee_bps=30.0,  # Higher tier pricing
    taker_fee_bps=50.0,
    tier1_slippage_bps=5.0,
    tier2_slippage_bps=15.0,
    tier3_slippage_bps=30.0,
    default_order_type="maker"  # Better pricing but fill risk
)
```

### Aggressive (Worst Case)
```python
config = SlippageConfig(
    maker_fee_bps=40.0,
    taker_fee_bps=60.0,
    tier1_slippage_bps=20.0,  # Worse market conditions
    tier2_slippage_bps=40.0,
    tier3_slippage_bps=80.0,
    default_order_type="taker"
)
```

## Usage Examples

### Basic Usage
```python
from backtest.slippage_model import SlippageModel

model = SlippageModel()

# Simulate BTC buy
fill = model.simulate_fill(
    mid_price=50000.0,
    side="buy",
    quantity=1.0,
    tier="tier1",
    order_type="taker"
)

print(f"Fill price: ${fill['fill_price']:,.2f}")
print(f"Fee: ${fill['fee_usd']:,.2f}")
print(f"Total cost: ${fill['total_cost']:,.2f}")
```

### Backtest with Slippage
```python
from backtest.engine import BacktestEngine
from backtest.slippage_model import SlippageConfig

# Conservative config
config = SlippageConfig(taker_fee_bps=60.0)
engine = BacktestEngine(slippage_config=config)

# Run backtest - fills now realistic
metrics = engine.run(start_date, end_date, data_loader)
```

### Compare With/Without Slippage
```python
# No slippage (optimistic)
engine_optimistic = BacktestEngine(
    slippage_config=SlippageConfig(
        maker_fee_bps=0,
        taker_fee_bps=0,
        tier1_slippage_bps=0,
        tier2_slippage_bps=0,
        tier3_slippage_bps=0
    )
)
metrics_optimistic = engine_optimistic.run(...)

# With slippage (realistic)
engine_realistic = BacktestEngine()  # Default config
metrics_realistic = engine_realistic.run(...)

print(f"Optimistic PnL: {metrics_optimistic.total_pnl_pct}%")
print(f"Realistic PnL: {metrics_realistic.total_pnl_pct}%")
print(f"Cost of trading: {metrics_optimistic.total_pnl_pct - metrics_realistic.total_pnl_pct}%")
```

## Validation

### Manual Validation
```bash
# Run slippage model tests
pytest tests/test_slippage_model.py -v

# Expected: 20 tests passing
# ✅ TestSlippageConfig (1 test)
# ✅ TestFillPriceCalculation (5 tests)
# ✅ TestFeeCalculation (4 tests)
# ✅ TestPnLCalculation (5 tests)
# ✅ TestFullSimulation (3 tests)
# ✅ TestCustomConfig (2 tests)
```

### Backtest Integration Validation
```bash
# Run backtest regression tests
pytest tests/test_backtest_regression.py -v

# Expected: 17 tests passing
# Confirms slippage integration doesn't break existing functionality
```

### Live Validation (Post-Rehearsal)
```bash
# Compare backtest vs PAPER trading results
# 1. Run backtest with recent data
# 2. Run PAPER mode for same period
# 3. Compare PnL (should be within 10% after accounting for execution timing)
```

## Maintenance

### Updating Fee Structure
When Coinbase changes fees or achieves higher tier:
```python
# Update config/backtest.yaml (if we add one)
slippage:
  maker_fee_bps: 30.0  # Updated from 40.0
  taker_fee_bps: 50.0  # Updated from 60.0
```

### Adding New Tiers
If introducing tier0 for ultra-liquid pairs:
```python
# In slippage_model.py
@dataclass
class SlippageConfig:
    tier0_slippage_bps: float = 5.0  # New tier
    tier1_slippage_bps: float = 10.0
    # ...
```

### Monitoring
Track actual vs simulated slippage:
```python
# After live fills, compare:
actual_slippage = abs(fill_price - mid_price) / mid_price * 10000
simulated_slippage = model.calculate_fill_price(...) - mid_price

if actual_slippage > simulated_slippage * 1.5:
    logger.warning("Actual slippage exceeds model by 50%")
```

## Known Limitations

1. **Static Spread Model:** Uses fixed bps per tier, doesn't account for intraday volatility changes
2. **Linear Impact:** Market impact uses log scale, may underestimate for very large orders (>$1M)
3. **No Partial Fills:** Assumes full fill at single price (reality may be multiple fills)
4. **Taker Assumption:** Defaults to taker orders (conservative but may overestimate costs)
5. **No Maker Fill Rate:** Doesn't model maker order fill probability (always assume taker)

## Future Enhancements

**Phase 2 (Optional):**
- Dynamic spread based on volatility regime
- Partial fill simulation for large orders
- Maker/taker order routing logic with fill probability
- Order book impact modeling
- Time-of-day spread adjustment

**Phase 3 (Advanced):**
- Calibrate model parameters from live execution data
- Per-symbol slippage profiles
- Adaptive impact estimation
- Real-time spread monitoring

## References

- Coinbase Advanced Trade Fee Structure: https://www.coinbase.com/advanced-fees
- Market Impact Models: Almgren-Chriss (2000)
- Backtest Best Practices: QuantConnect, Backtrader documentation

---

## Summary

✅ **Complete:** Slippage model implemented, tested, and integrated  
✅ **Impact:** Backtests now reflect real-world trading costs  
✅ **Validation:** 20 slippage tests + 17 backtest tests passing  
✅ **Conservative:** Default config uses taker fees and realistic slippage  
✅ **Flexible:** Easy to adjust parameters for different scenarios  

**Next Steps:**
1. ✅ Run backtest with new slippage model
2. ✅ Compare results to previous runs (expect lower PnL)
3. 🔄 Adjust strategy parameters if needed to maintain profitability
4. 🔄 Validate during PAPER rehearsal
5. 🔄 Monitor actual vs simulated costs in LIVE
