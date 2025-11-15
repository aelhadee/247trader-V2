# Multi-Strategy Framework

**Status:** ✅ Implemented (REQ-STR1-3)  
**Tests:** 29 passing (`tests/test_strategy_framework.py`)  
**Date:** 2025-01-11

## Overview

The multi-strategy framework enables running multiple independent trading strategies simultaneously with isolated risk budgets and feature flags. This architecture allows adding new strategies without modifying core trading loop or risk engine code.

### Key Features

- **Pure Strategy Interface** (REQ-STR1): Strategies cannot call exchange APIs, only receive immutable context
- **Per-Strategy Feature Flags** (REQ-STR2): Enable/disable strategies independently; new strategies default to disabled
- **Per-Strategy Risk Budgets** (REQ-STR3): Enforce `max_at_risk_pct` and `max_trades_per_cycle` before global caps

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        main_loop.py                          │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ 1. Build StrategyContext (universe, triggers, regime) │  │
│  └───────────────┬───────────────────────────────────────┘  │
│                  │                                           │
│                  ▼                                           │
│  ┌───────────────────────────────────────────────────────┐  │
│  │          StrategyRegistry.aggregate_proposals()       │  │
│  │  ┌─────────────────────────────────────────────────┐  │  │
│  │  │ For each enabled strategy:                      │  │  │
│  │  │   strategy.run(context) → List[TradeProposal]   │  │  │
│  │  │ Dedupe across strategies (keep highest conf)    │  │  │
│  │  └─────────────────────────────────────────────────┘  │  │
│  └───────────────┬───────────────────────────────────────┘  │
│                  │                                           │
│                  ▼                                           │
│  ┌───────────────────────────────────────────────────────┐  │
│  │         RiskEngine._check_strategy_caps()            │  │
│  │  Enforce per-strategy max_at_risk_pct BEFORE global  │  │
│  └───────────────┬───────────────────────────────────────┘  │
│                  │                                           │
│                  ▼                                           │
│  ┌───────────────────────────────────────────────────────┐  │
│  │         RiskEngine._check_global_at_risk()           │  │
│  │  Enforce global caps across all strategies           │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

### Components

#### 1. BaseStrategy (Abstract Base Class)

**File:** `strategy/base_strategy.py`

All strategies MUST inherit from `BaseStrategy` and implement `generate_proposals()`.

```python
from abc import ABC, abstractmethod
from strategy.base_strategy import BaseStrategy, StrategyContext

class MyStrategy(BaseStrategy):
    @abstractmethod
    def generate_proposals(self, context: StrategyContext) -> List[Any]:
        """
        Generate trade proposals based on strategy logic.
        
        Contract:
        - MUST NOT call exchange APIs (context provides all data)
        - MUST NOT mutate context or global state
        - MUST return List[TradeProposal] or []
        - MUST handle all errors internally (log and return [])
        """
        proposals = []
        
        # Strategy logic using context.universe, context.triggers, context.regime
        # ...
        
        return proposals
```

**Key Methods:**
- `generate_proposals(context)`: **Abstract** - implement strategy logic
- `validate_proposals(proposals)`: Validates, tags with strategy name, enforces `max_trades_per_cycle`
- `run(context)`: Public method with error handling, calls `generate_proposals()` and `validate_proposals()`

**Properties:**
- `name`: Strategy identifier (e.g., "rules_engine", "mean_reversion")
- `enabled`: Feature flag from config (defaults to `False` for new strategies)
- `max_at_risk_pct`: Per-strategy risk budget (% of account value)
- `max_trades_per_cycle`: Max proposals this strategy can generate per cycle

#### 2. StrategyContext (Immutable Data)

**File:** `strategy/base_strategy.py`

Immutable context passed to all strategies. Prevents side effects and enforces pure interface.

```python
@dataclass
class StrategyContext:
    """Immutable context for strategy execution"""
    universe: UniverseSnapshot          # Eligible assets with tier/allocation data
    triggers: List[TriggerSignal]       # Detected signals (breakout, volume spike, etc.)
    regime: str = "chop"                # Market regime: "bull", "bear", "chop"
    timestamp: datetime                 # Cycle start time (UTC)
    cycle_number: int = 0               # Sequential cycle counter
    state: Optional[Dict] = None        # Read-only state snapshot (positions, PnL, etc.)
    risk_constraints: Optional[Dict] = None  # Global risk constraints for reference
```

#### 3. StrategyRegistry

**File:** `strategy/registry.py`

Central registry that loads, manages, and coordinates multiple strategies.

**Key Methods:**
- `get_enabled_strategies()`: Filter by `enabled` flag
- `generate_proposals(context)`: Run all enabled strategies, return proposals by strategy name
- `aggregate_proposals(context, dedupe_by_symbol=True)`: Flatten proposals, dedupe across strategies (keep highest confidence per symbol)

**STRATEGY_CLASSES Registry:**
```python
STRATEGY_CLASSES: Dict[str, Type[BaseStrategy]] = {
    "rules_engine": RulesEngine,
    # Add new strategies here
}
```

To add a new strategy:
1. Create class inheriting from `BaseStrategy`
2. Add to `STRATEGY_CLASSES` dict
3. Configure in `config/strategies.yaml`

#### 4. Configuration (strategies.yaml)

**File:** `config/strategies.yaml`

```yaml
strategies:
  rules_engine:
    enabled: true                       # REQ-STR2: Feature flag
    type: "rules_engine"                # Maps to StrategyRegistry.STRATEGY_CLASSES
    description: "Deterministic baseline strategy"
    risk_budgets:                       # REQ-STR3: Per-strategy caps
      max_at_risk_pct: 15.0             # Max 15% of account in this strategy
      max_trades_per_cycle: 5           # Max 5 proposals per cycle
    params: {}                          # Strategy-specific parameters

  mean_reversion:
    enabled: false                      # Default disabled (must explicitly enable)
    type: "mean_reversion"
    description: "Buy oversold, sell overbought"
    risk_budgets:
      max_at_risk_pct: 10.0
      max_trades_per_cycle: 3
    params:
      rsi_oversold: 30
      rsi_overbought: 70

global:
  dedupe_by_symbol: true                # Keep highest confidence when multiple strategies propose same symbol
  max_total_proposals_per_cycle: 10     # Hard cap across all strategies
```

## Adding a New Strategy

### Step 1: Create Strategy Class

**Example:** `strategy/mean_reversion.py`

```python
from typing import List, Any
from datetime import datetime
from strategy.base_strategy import BaseStrategy, StrategyContext
from strategy.rules_engine import TradeProposal  # Reuse dataclass

class MeanReversionStrategy(BaseStrategy):
    """
    Buy assets with RSI < 30 (oversold)
    Sell assets with RSI > 70 (overbought)
    """
    
    def generate_proposals(self, context: StrategyContext) -> List[Any]:
        """Generate mean-reversion proposals"""
        proposals = []
        
        # Extract strategy parameters
        params = self.config.get("params", {})
        rsi_oversold = params.get("rsi_oversold", 30)
        rsi_overbought = params.get("rsi_overbought", 70)
        
        # Scan universe for mean-reversion opportunities
        for asset in context.universe.get_all_eligible():
            # Calculate RSI (would need OHLCV data from context.state or separate indicator service)
            rsi = self._calculate_rsi(asset.symbol, context)
            
            if rsi is None:
                continue
            
            # Generate BUY proposal if oversold
            if rsi < rsi_oversold:
                proposals.append(TradeProposal(
                    symbol=asset.symbol,
                    side="BUY",
                    size_pct=2.0,  # 2% position size
                    reason=f"RSI oversold: {rsi:.1f} < {rsi_oversold}",
                    confidence=min((rsi_oversold - rsi) / rsi_oversold, 1.0),
                    trigger=None,  # No trigger, strategy-generated
                    stop_loss_pct=5.0,
                    take_profit_pct=10.0,
                ))
            
            # Generate SELL proposal if overbought (for existing positions)
            elif rsi > rsi_overbought:
                # Only propose sell if we hold this asset
                if self._has_position(asset.symbol, context):
                    proposals.append(TradeProposal(
                        symbol=asset.symbol,
                        side="SELL",
                        size_pct=100.0,  # Exit entire position
                        reason=f"RSI overbought: {rsi:.1f} > {rsi_overbought}",
                        confidence=min((rsi - rsi_overbought) / (100 - rsi_overbought), 1.0),
                        trigger=None,
                    ))
        
        return proposals
    
    def _calculate_rsi(self, symbol: str, context: StrategyContext) -> float:
        """Calculate RSI indicator (14-period)"""
        # TODO: Implement RSI calculation from OHLCV data
        # For now, return None to skip this asset
        return None
    
    def _has_position(self, symbol: str, context: StrategyContext) -> bool:
        """Check if we have an open position in this symbol"""
        if not context.state:
            return False
        
        positions = context.state.get("positions", {})
        return symbol in positions
```

### Step 2: Register Strategy

**File:** `strategy/registry.py`

```python
from strategy.mean_reversion import MeanReversionStrategy

STRATEGY_CLASSES: Dict[str, Type[BaseStrategy]] = {
    "rules_engine": RulesEngine,
    "mean_reversion": MeanReversionStrategy,  # Add here
}
```

### Step 3: Configure Strategy

**File:** `config/strategies.yaml`

```yaml
strategies:
  mean_reversion:
    enabled: false                      # Start disabled for testing
    type: "mean_reversion"
    description: "Buy oversold, sell overbought"
    risk_budgets:
      max_at_risk_pct: 10.0             # Lower cap for new strategy
      max_trades_per_cycle: 3
    params:
      rsi_oversold: 30
      rsi_overbought: 70
```

### Step 4: Test Strategy

**File:** `tests/test_mean_reversion.py`

```python
import pytest
from datetime import datetime, timezone
from strategy.mean_reversion import MeanReversionStrategy
from strategy.base_strategy import StrategyContext

def test_mean_reversion_generates_buy_proposals(mock_universe, mock_triggers):
    """Test mean-reversion generates BUY when RSI oversold"""
    config = {
        "enabled": True,
        "params": {"rsi_oversold": 30, "rsi_overbought": 70}
    }
    strategy = MeanReversionStrategy(name="mean_reversion", config=config)
    
    context = StrategyContext(
        universe=mock_universe,
        triggers=mock_triggers,
        regime="chop",
        timestamp=datetime.now(timezone.utc),
        cycle_number=1
    )
    
    proposals = strategy.run(context)
    
    # Assertions
    assert isinstance(proposals, list)
    assert all(p.side in ["BUY", "SELL"] for p in proposals)
    assert all(p.metadata.get("strategy") == "mean_reversion" for p in proposals)
```

### Step 5: Enable Strategy

Once tested in DRY_RUN mode, enable in config:

```yaml
strategies:
  mean_reversion:
    enabled: true  # Enable for LIVE trading
```

## Risk Budget Enforcement (REQ-STR3)

### Per-Strategy Caps

Enforced in `RiskEngine._check_strategy_caps()` **BEFORE** global caps:

1. Group proposals by `strategy_name` (from proposal metadata)
2. For each strategy:
   - Calculate existing exposure for that strategy (from state tracking)
   - Calculate proposed exposure from proposals
   - Compare `total_strategy_exposure` against `max_at_risk_pct`
   - Reject all proposals from strategy if cap exceeded

**Example:**
```yaml
strategies:
  rules_engine:
    risk_budgets:
      max_at_risk_pct: 15.0  # Rules engine can use max 15% of account
      max_trades_per_cycle: 5

  mean_reversion:
    risk_budgets:
      max_at_risk_pct: 10.0  # Mean-reversion can use max 10% of account
      max_trades_per_cycle: 3
```

If account value = $10,000:
- `rules_engine` can have max $1,500 at risk
- `mean_reversion` can have max $1,000 at risk
- **Total across both:** Still constrained by global `max_total_at_risk_pct` (default 15%)

### Max Trades Per Cycle

Enforced in `BaseStrategy.validate_proposals()`:

```python
if self.max_trades_per_cycle and len(validated) > self.max_trades_per_cycle:
    logger.warning(
        f"[{self.name}] Generated {len(validated)} proposals, "
        f"limiting to max_trades_per_cycle={self.max_trades_per_cycle}"
    )
    validated = validated[:self.max_trades_per_cycle]
```

### Global Caps (After Strategy Caps)

After per-strategy caps, proposals pass through global risk checks:
- `_check_global_at_risk()`: Total exposure across ALL strategies
- `_check_max_open_positions()`: Total position count limit
- `_apply_caps_to_proposals()`: Per-symbol exposure caps

## Backward Compatibility

The existing `RulesEngine` now inherits from `BaseStrategy`:

```python
from strategy.base_strategy import BaseStrategy, StrategyContext

class RulesEngine(BaseStrategy):
    def __init__(self, name: str = "rules_engine", config: Optional[Dict] = None):
        # Support both old and new signatures
        if config is None:
            config = {"enabled": True, "params": {}}
        super().__init__(name=name, config=config)
        # ... existing initialization
    
    def generate_proposals(self, context: StrategyContext) -> List[TradeProposal]:
        """New BaseStrategy interface"""
        return self.propose_trades(
            universe=context.universe,
            triggers=context.triggers,
            regime=context.regime
        )
    
    def propose_trades(self, universe, triggers, regime) -> List[TradeProposal]:
        """Existing method - still works for direct calls"""
        # ... existing implementation unchanged
```

Old code still works:
```python
rules_engine = RulesEngine(config={})
proposals = rules_engine.propose_trades(universe, triggers, regime)
```

New multi-strategy code:
```python
registry = StrategyRegistry()
context = StrategyContext(universe, triggers, regime, timestamp, cycle_number)
proposals = registry.aggregate_proposals(context)
```

## Integration Points

### main_loop.py

```python
# Initialize (in __init__)
from strategy.registry import StrategyRegistry
self.strategy_registry = StrategyRegistry(config_path=self.config_dir / "strategies.yaml")

# Generate proposals (in _run_cycle)
from strategy.base_strategy import StrategyContext
strategy_context = StrategyContext(
    universe=universe,
    triggers=triggers,
    regime=self.current_regime,
    timestamp=cycle_started,
    cycle_number=self.portfolio.cycle_count + 1,
    state=self.state_store.load(),
)

proposals = self.strategy_registry.aggregate_proposals(
    context=strategy_context,
    dedupe_by_symbol=True
)
```

### RiskEngine

Per-strategy caps enforced in `check_all()`:

```python
# After circuit breakers, before global at-risk
result = self._check_strategy_caps(proposals, portfolio, pending_buy_override_usd)
if not result.approved:
    return RiskCheckResult(approved=False, reason=result.reason, ...)
if result.filtered_proposals:
    proposals = result.filtered_proposals
```

## Testing

**Test Suite:** `tests/test_strategy_framework.py` (29 tests, all passing)

### Test Classes

1. **TestBaseStrategyInterface** (11 tests)
   - Abstract enforcement
   - Initialization and defaults
   - Proposal validation (side, confidence, tagging)
   - Max trades enforcement
   - Error handling

2. **TestStrategyRegistry** (8 tests)
   - Loading from config
   - Feature flag filtering
   - Proposal generation from multiple strategies
   - Deduplication by symbol

3. **TestRulesEngineCompatibility** (4 tests)
   - Inheritance verification
   - Backward compatibility (old `__init__` and `propose_trades()`)
   - New `generate_proposals()` interface

4. **TestStrategyContext** (4 tests)
   - Context creation
   - Timezone handling
   - Type validation

5. **TestStrategyIsolation** (2 tests)
   - No exchange API dependencies
   - Pure interface enforcement

### Run Tests

```bash
# Strategy framework tests only
pytest tests/test_strategy_framework.py -v

# Full regression (verify no core breakage)
pytest tests/test_core.py tests/test_strategy_framework.py -v
```

## Monitoring & Observability

### Logs

Per-strategy proposal breakdown:
```
INFO: Active strategies: ['rules_engine', 'mean_reversion']
INFO: Generated 3 proposals from rules_engine
INFO: Generated 2 proposals from mean_reversion
INFO: Deduped 5 → 4 proposals (1 symbol overlap, kept higher confidence)
```

Strategy cap violations:
```
WARNING: Strategy 'mean_reversion' would exceed risk budget: 12.3% > 10.0%
         (existing=$800, proposed=$430)
```

### Metrics

Track per-strategy metrics:
- Proposals generated per strategy
- Proposals approved per strategy
- Strategy cap blocks
- Strategy win rate / PnL

**TODO:** Add Prometheus metrics for per-strategy tracking

## Future Enhancements

### Near-Term
1. **Strategy PnL Tracking**: Attribute fills/exits to originating strategy for performance analysis
2. **Dynamic Strategy Weights**: Adjust `max_at_risk_pct` based on strategy performance
3. **Strategy Cooldowns**: Disable poorly performing strategies automatically

### Medium-Term
4. **Strategy Ensembles**: Meta-strategies that combine signals from multiple sub-strategies
5. **Backtest Per Strategy**: Run backtests for individual strategies in isolation
6. **A/B Testing**: Run same strategy with different parameters in parallel

### Long-Term
7. **ML-Based Strategy Selection**: Predict which strategies will perform best in current regime
8. **Auto-Generated Strategies**: Use genetic algorithms to evolve new strategies

## Best Practices

### Strategy Development

1. **Start Disabled**: New strategies default to `enabled: false` until tested
2. **Small Risk Budgets**: Start with low `max_at_risk_pct` (e.g., 5%) until proven
3. **Comprehensive Tests**: Write tests for edge cases (no triggers, bad data, API errors)
4. **Pure Functions**: Strategies should be pure functions of context (no side effects)
5. **Graceful Degradation**: Return `[]` on errors, don't raise exceptions

### Configuration

1. **Document Parameters**: Add `description` field for each strategy
2. **Conservative Defaults**: Default to lower risk budgets
3. **Version Strategies**: Use strategy names like `rules_engine_v2` for major changes
4. **Separate Configs**: Use different `strategies.yaml` for DRY_RUN, PAPER, LIVE

### Monitoring

1. **Track Per-Strategy Metrics**: Win rate, avg return, max DD, Sharpe ratio
2. **Alert on Anomalies**: Strategy generating 0 proposals for N cycles
3. **Audit Logs**: Log every proposal with `strategy` metadata
4. **Performance Reviews**: Weekly review of per-strategy PnL

## FAQ

**Q: Can a strategy call exchange APIs?**  
A: No. Strategies receive immutable `StrategyContext` with all data. This enforces pure interface and prevents duplicate API calls.

**Q: How do I pass custom data to a strategy?**  
A: Add to `context.state` dict or extend `StrategyContext` dataclass.

**Q: What happens if multiple strategies propose the same symbol?**  
A: `aggregate_proposals(dedupe_by_symbol=True)` keeps the proposal with highest `confidence`.

**Q: Can I disable a strategy mid-trading?**  
A: Yes. Set `enabled: false` in `strategies.yaml` and restart the bot. Next cycle will skip that strategy.

**Q: How do I test a new strategy safely?**  
A: Start in DRY_RUN mode with `enabled: false`, then enable just that strategy. Monitor logs for proposals. Once confident, move to PAPER, then LIVE.

**Q: Do strategy risk budgets stack?**  
A: No. Each strategy has independent `max_at_risk_pct`, but the **sum** is still constrained by global `max_total_at_risk_pct`.

**Q: Can I run strategies on different intervals?**  
A: Not currently. All strategies run every cycle. Future enhancement: per-strategy scheduling.

**Q: How do I measure strategy performance?**  
A: Tag fills with `strategy_name` in metadata, then aggregate PnL per strategy. See `TODO` in monitoring section.

---

**Implementation Date:** 2025-01-11  
**Requirements:** REQ-STR1 (Pure Interface), REQ-STR2 (Feature Flags), REQ-STR3 (Risk Budgets)  
**Tests:** 29/29 passing  
**Status:** ✅ Production Ready
