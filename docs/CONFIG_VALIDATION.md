# Configuration Validation

**Status**: ✅ Production-ready  
**Owner**: tools/config_validator.py  
**Tests**: 132/132 passing (12 new tests in `test_config_validation.py`)

## Overview

Validates all YAML configuration files (`policy.yaml`, `universe.yaml`, `signals.yaml`) against **Pydantic schemas** at system startup. Ensures configurations are correct before any trading logic executes—**fail fast on misconfiguration**.

**Key Features**:
- Pydantic v2 schema validation with type checking
- Field-level constraints (ranges, enums, patterns)
- Cross-field validation (e.g., max_order_usd >= min_order_usd)
- Aggregated error reporting
- Standalone CLI tool for pre-flight checks
- Integrated into TradingLoop startup

## Why Config Validation?

**Without validation:**
- Typos cause runtime crashes (e.g., `"limt_post_only"` → KeyError)
- Invalid values cause silent failures (e.g., `max_spread_bps: -50`)
- Missing required fields discovered mid-execution
- Debugging config errors wastes time

**With validation:**
- Catch errors **before** startup
- Clear error messages with field paths
- Type safety (floats, ints, enums)
- Business logic validation (percentages 0-100, positive values)
- Fail fast → no bad configs in production

## Implementation

### Location

- **Validator**: `tools/config_validator.py` (560 lines)
- **Tests**: `tests/test_config_validation.py` (12 tests)
- **Integration**: `runner/main_loop.py` (TradingLoop.__init__)

### Validated Files

1. **policy.yaml**: Risk limits, position sizing, execution, circuit breakers
2. **universe.yaml**: Tiers, clusters, liquidity filters, regime modifiers
3. **signals.yaml**: Trigger parameters, regime multipliers

### Schemas

#### PolicySchema

```python
class PolicySchema(BaseModel):
    risk: RiskConfig
    position_sizing: PositionSizingConfig
    liquidity: LiquidityConfig
    triggers: TriggersConfig
    strategy: StrategyConfig
    microstructure: MicrostructureConfig
    execution: ExecutionConfig
    data: DataConfig
    circuit_breaker: CircuitBreakerConfig
```

**Key validations:**
- `risk.max_total_at_risk_pct`: 0 < value ≤ 100
- `risk.daily_stop_pnl_pct`: ≤ 0 (negative values only)
- `position_sizing.method`: Must be `"fixed"`, `"risk_parity"`, or `"kelly"`
- `execution.default_order_type`: Must be `"market"`, `"limit"`, or `"limit_post_only"`
- `position_sizing`: max_order_usd ≥ min_order_usd

#### UniverseSchema

```python
class UniverseSchema(BaseModel):
    clusters: ClusterDefinitions
    exclusions: ExclusionsConfig
    liquidity: LiquidityConfig
    regime_modifiers: RegimeModifiers
    tiers: Dict[str, TierConfig]
    universe: UniverseTopLevel
```

**Key validations:**
- `clusters.definitions`: Each cluster must have ≥ 1 symbol
- `tiers.*.refresh`: Must be `"hourly"`, `"daily"`, or `"weekly"`
- `regime_modifiers.*`: All multipliers ≥ 0
- `universe.method`: Must be `"static"` or `"dynamic_discovery"`

#### SignalsSchema

```python
class SignalsSchema(BaseModel):
    triggers: SignalTriggersConfig
```

**Key validations:**
- `triggers.min_trigger_score`: 0 ≤ value ≤ 1
- `triggers.min_trigger_confidence`: 0 ≤ value ≤ 1
- `triggers.volume_spike_min_ratio`: > 0
- `triggers.max_triggers_per_cycle`: > 0

### Validation Functions

#### validate_all_configs()

Main entry point for validation:

```python
from tools.config_validator import validate_all_configs

errors = validate_all_configs("config")
if errors:
    for error in errors:
        print(f"ERROR: {error}")
    sys.exit(1)
```

**Returns**: List of error strings (empty if valid)

#### validate_policy() / validate_universe() / validate_signals()

Individual validators for each config file:

```python
from pathlib import Path
from tools.config_validator import validate_policy

errors = validate_policy(Path("config"))
```

### Error Reporting

**Example error output:**

```
❌ Configuration Validation Failed:

  • policy.yaml: risk -> max_total_at_risk_pct: Input should be less than or equal to 100
  • policy.yaml: execution -> default_order_type: Input should be 'market', 'limit', or 'limit_post_only'
  • universe.yaml: clusters -> definitions -> EMPTY_CLUSTER: Cluster must have at least one symbol
  • signals.yaml: triggers -> min_trigger_score: Input should be less than or equal to 1
```

**Error format**: `<file>: <field_path>: <error_message>`

## Integration

### TradingLoop Startup

**Location**: `runner/main_loop.py` (lines 54-67)

```python
def __init__(self, config_dir: str = "config"):
    self.config_dir = Path(config_dir)
    
    # Validate configs before loading
    from tools.config_validator import validate_all_configs
    validation_errors = validate_all_configs(config_dir)
    if validation_errors:
        logger.error("=" * 80)
        logger.error("CONFIGURATION VALIDATION FAILED")
        logger.error("=" * 80)
        for error in validation_errors:
            logger.error(f"  • {error}")
        logger.error("=" * 80)
        raise ValueError(f"Invalid configuration: {len(validation_errors)} error(s) found")
    
    # Load configs (YAML parsing only after validation passes)
    self.app_config = self._load_yaml("app.yaml")
    self.policy_config = self._load_yaml("policy.yaml")
    self.universe_config = self._load_yaml("universe.yaml")
```

**Flow:**
1. Validate all configs **before** loading
2. Fail fast with detailed error messages
3. Only proceed to YAML loading if validation passes

### Standalone CLI Tool

**Run from command line:**

```bash
# Validate default config directory
python tools/config_validator.py config

# Validate custom directory
python tools/config_validator.py /path/to/configs

# Example output (success)
INFO: ✅ policy.yaml validation passed
INFO: ✅ universe.yaml validation passed
INFO: ✅ signals.yaml validation passed
INFO: ✅ All config files validated successfully

✅ All configuration files are valid!

# Example output (failure)
ERROR: ❌ 2 validation error(s) found

❌ Configuration Validation Failed:

  • policy.yaml: risk -> max_total_at_risk_pct: Input should be less than or equal to 100
  • universe.yaml: clusters -> definitions -> EMPTY_CLUSTER: Cluster must have at least one symbol

[Exit code: 1]
```

## Testing

**Test Suite**: `tests/test_config_validation.py` (12 tests, 100% passing)

### Test Classes

**1. TestPolicyValidation** (3 tests):
- `test_valid_policy_config`: Valid config passes
- `test_invalid_risk_percentages`: Percentages > 100 fail
- `test_invalid_order_type`: Invalid enum values fail

**2. TestUniverseValidation** (3 tests):
- `test_valid_universe_config`: Valid config passes
- `test_empty_cluster_fails`: Empty cluster definitions fail
- `test_invalid_refresh_frequency`: Invalid refresh frequency fails

**3. TestSignalsValidation** (2 tests):
- `test_valid_signals_config`: Valid config passes
- `test_invalid_trigger_score_range`: Scores outside 0-1 fail

**4. TestFileValidation** (3 tests):
- `test_missing_file_returns_error`: Missing files detected
- `test_malformed_yaml_returns_error`: Malformed YAML detected
- `test_validate_all_configs_aggregates_errors`: All errors aggregated

**5. TestConfigIntegration** (1 test):
- `test_actual_config_files_are_valid`: Validates actual config files

### Running Tests

```bash
# Config validation tests only
pytest tests/test_config_validation.py -v

# Full test suite (includes config validation)
pytest tests/ -v

# Expected: 132/132 passing
```

## Usage Examples

### Programmatic Validation

```python
from tools.config_validator import validate_all_configs

# Validate configs
errors = validate_all_configs("config")

if errors:
    print("Validation failed:")
    for error in errors:
        print(f"  - {error}")
    sys.exit(1)
else:
    print("All configs valid!")
```

### Pre-Commit Hook

Add to `.git/hooks/pre-commit`:

```bash
#!/bin/bash
python tools/config_validator.py config
if [ $? -ne 0 ]; then
    echo "Config validation failed - commit aborted"
    exit 1
fi
```

### CI/CD Pipeline

Add to GitHub Actions workflow:

```yaml
- name: Validate Configs
  run: python tools/config_validator.py config
```

## Validation Rules

### Common Constraints

**Percentages**:
- Must be 0 < value ≤ 100 for most percentage fields
- Exception: PnL stops (negative values only)

**Positive Numbers**:
- Volume thresholds, notional sizes, spreads: Must be > 0

**Non-Negative**:
- Cooldowns, timeouts, counts: Must be ≥ 0

**Enums**:
- Order types: `"market"`, `"limit"`, `"limit_post_only"`
- Sizing methods: `"fixed"`, `"risk_parity"`, `"kelly"`
- Refresh frequencies: `"hourly"`, `"daily"`, `"weekly"`
- Universe methods: `"static"`, `"dynamic_discovery"`

**Cross-Field**:
- `max_order_usd` ≥ `min_order_usd`
- Stop losses negative, take profits positive

### Business Logic

**Clusters**:
- Each cluster must have ≥ 1 symbol
- Symbol format not validated (exchange handles that)

**Regime Multipliers**:
- All multipliers ≥ 0
- Crash regime typically 0.0 (no new positions)

**Theme Exposure**:
- Each theme percentage 0 < pct ≤ 100
- No check for sum (allows flexibility)

## Configuration Updates

### Adding New Fields

1. **Update schema** in `tools/config_validator.py`:

```python
class ExecutionConfig(BaseModel):
    # ... existing fields ...
    new_field: int = Field(gt=0, description="New field")
```

2. **Update config file** (e.g., `config/policy.yaml`):

```yaml
execution:
  # ... existing fields ...
  new_field: 100
```

3. **Run validation**:

```bash
python tools/config_validator.py config
```

4. **Update tests** if needed:

```python
def test_new_field_validation(self):
    config = {
        "execution": {
            # ... all required fields ...
            "new_field": 100
        }
    }
    schema = ExecutionConfig(**config)
    assert schema.new_field == 100
```

### Making Fields Optional

Change from:

```python
field_name: int = Field(description="Required field")
```

To:

```python
field_name: Optional[int] = Field(default=None, description="Optional field")
```

## Limitations

**Not Validated**:
- Symbol format (e.g., `"BTC-USD"` vs `"BTCUSD"`) → exchange handles
- Symbol availability on exchange → universe manager handles
- Secret values (API keys) → runtime validation only
- File paths existence → runtime validation only
- Network connectivity → runtime validation only

**YAML Only**:
- Only validates YAML structure and types
- Does not validate `app.yaml` (minimal structure)
- Does not validate environment variables

**No Semantic Validation**:
- Doesn't check if BTC-USD actually exists on Coinbase
- Doesn't validate API credentials work
- Doesn't check if portfolio has sufficient capital

These are intentionally left to runtime for flexibility.

## Troubleshooting

### Error: "Field required"

**Cause**: Missing required field in config

**Fix**: Add the field to config file:

```yaml
execution:
  post_trade_reconcile_wait_seconds: 0.5  # ADD THIS
```

### Error: "Input should be less than or equal to 100"

**Cause**: Percentage field > 100

**Fix**: Use valid percentage (0-100):

```yaml
risk:
  max_total_at_risk_pct: 15.0  # FIX: was 150.0
```

### Error: "Input should be 'market', 'limit', or 'limit_post_only'"

**Cause**: Invalid enum value

**Fix**: Use valid enum:

```yaml
execution:
  default_order_type: "limit_post_only"  # FIX: was "limt_post_only"
```

### Error: "Cluster EMPTY_CLUSTER must have at least one symbol"

**Cause**: Empty cluster definition

**Fix**: Add symbols or remove cluster:

```yaml
clusters:
  definitions:
    DEFI:
      - UNI-USD  # FIX: was []
```

## Production Checklist

**Before deploying configs:**

1. ✅ Run validator: `python tools/config_validator.py config`
2. ✅ Fix all validation errors
3. ✅ Run full test suite: `pytest tests/ -v`
4. ✅ Review diff: `git diff config/`
5. ✅ Test in DRY_RUN mode first
6. ✅ Monitor first PAPER run
7. ✅ Deploy to LIVE only after validation

**CI/CD Integration:**
- Add validation to GitHub Actions
- Block merges if validation fails
- Run on every config change

## References

- **Validator**: `tools/config_validator.py`
- **Integration**: `runner/main_loop.py` (TradingLoop.__init__)
- **Tests**: `tests/test_config_validation.py` (12 tests)
- **Configs**:
  - `config/policy.yaml`
  - `config/universe.yaml`
  - `config/signals.yaml`
- **Pydantic Docs**: https://docs.pydantic.dev/

---

**Last Updated**: 2025-11-11  
**Test Status**: ✅ 132/132 passing  
**Production Status**: Ready for deployment
