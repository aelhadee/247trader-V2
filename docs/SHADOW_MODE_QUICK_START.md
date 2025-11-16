# Shadow DRY_RUN Mode - Quick Start

## What is Shadow Mode?

Enhanced DRY_RUN that logs comprehensive execution details WITHOUT submitting orders. Shows what would happen with real market data.

## Usage

### Run in Shadow Mode

```bash
# Set DRY_RUN mode in config/app.yaml
execution:
  mode: DRY_RUN

# Run main loop
python -m runner.main_loop
```

Every order attempt logs to `logs/shadow_orders.jsonl`.

### Analyze Shadow Log

```python
from core.shadow_execution import ShadowExecutionLogger
import json

logger = ShadowExecutionLogger("logs/shadow_orders.jsonl")

# Get statistics
stats = logger.get_stats()
print(f"Would place: {stats['would_place']}/{stats['total']}")
print(f"Rejected: {stats['rejected']} - {stats['rejection_reasons']}")

# Read entries
with open("logs/shadow_orders.jsonl") as f:
    for line in f:
        order = json.loads(line)
        if not order.get('would_place'):
            print(f"REJECTED: {order['symbol']} - {order['rejection_reason']}")
        else:
            print(f"OK: {order['symbol']} {order['side']} ${order['size_usd']:.2f}")
            print(f"  Quote: ${order['intended_price']:.2f} (spread {order['quote_spread_bps']:.1f}bps)")
            print(f"  Expected fees: ${order['expected_fees_usd']:.2f}")
```

### Parallel Validation (Shadow + LIVE)

Run shadow alongside LIVE to compare predictions vs actual:

```python
# In your trading loop
shadow_engine = ExecutionEngine(mode="DRY_RUN", ...)
live_engine = ExecutionEngine(mode="LIVE", ...)

# Same order, both engines
shadow_result = shadow_engine.execute("BTC-USD", "BUY", 1000.0)
live_result = live_engine.execute("BTC-USD", "BUY", 1000.0)

# Compare later:
# - Shadow predicted spread/slippage vs actual
# - Shadow rejected for same reasons?
# - Expected fees vs actual
```

## What's Logged

Each shadow order includes:
- **Quotes**: bid/ask/mid/spread/age
- **Plan**: intended route, price, expected slippage, fees
- **Checks**: spread check, depth check, orderbook depth
- **Context**: tier, confidence, conviction
- **Decision**: would_place (true/false), rejection_reason

## Rejection Reasons

Common rejections:
- `"Quote too stale"` - Quote > 30s old
- `"Spread too wide"` - Spread > 100bps
- `"Insufficient depth"` - Orderbook < 2x order size
- `"Failed to fetch quote"` - API error

## Clear Log

```python
from core.shadow_execution import ShadowExecutionLogger

logger = ShadowExecutionLogger()
logger.clear_log()  # Careful - deletes all entries
```

## Files

- `core/shadow_execution.py` - Logger implementation
- `logs/shadow_orders.jsonl` - Log file (JSONL format)
- `tests/test_shadow_execution.py` - 13 tests
- `docs/TASK_4_SHADOW_DRY_RUN_COMPLETE.md` - Full documentation
