"""
Configuration Validation Module

Validates policy.yaml, universe.yaml, and signals.yaml against Pydantic schemas.
Ensures config files are correct before system startup.

Usage:
    from tools.config_validator import validate_all_configs
    
    errors = validate_all_configs("config")
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        sys.exit(1)
"""
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from pydantic import BaseModel, Field, field_validator, ValidationError

logger = logging.getLogger(__name__)


# ===== Policy Schema =====
class RiskConfig(BaseModel):
    """Risk management parameters"""
    max_total_at_risk_pct: float = Field(gt=0, le=100, description="Max total exposure %")
    max_position_size_pct: float = Field(gt=0, le=100, description="Max per-asset %")
    max_per_asset_pct: float = Field(gt=0, le=100, description="Alias for max_position_size_pct")
    min_position_size_pct: float = Field(ge=0, le=100, description="Min position size %")
    max_open_positions: int = Field(gt=0, description="Max concurrently held symbols")
    max_per_theme_pct: Dict[str, float] = Field(default_factory=dict, description="Cluster exposure limits")
    daily_stop_pnl_pct: float = Field(le=0, description="Daily PnL circuit breaker (negative)")
    weekly_stop_pnl_pct: float = Field(le=0, description="Weekly PnL circuit breaker (negative)")
    max_drawdown_pct: float = Field(gt=0, le=100, description="Max drawdown %")
    max_trades_per_day: int = Field(gt=0, description="Max trades per day")
    max_trades_per_hour: int = Field(gt=0, description="Max trades per hour")
    max_new_trades_per_hour: int = Field(gt=0, description="Max new trades per hour")
    min_seconds_between_trades: int = Field(ge=0, description="Global cooldown between any two trades (seconds)")
    per_symbol_trade_spacing_seconds: int = Field(ge=0, description="Cooldown between trades on the same symbol")
    cooldown_after_loss_trades: int = Field(ge=0, description="Consecutive losses before cooldown")
    cooldown_minutes: int = Field(ge=0, description="Cooldown duration (minutes)")
    per_symbol_cooldown_enabled: bool = Field(description="Enable per-symbol cooldowns")
    per_symbol_cooldown_minutes: int = Field(ge=0, description="Per-symbol cooldown (minutes)")
    per_symbol_cooldown_after_stop: int = Field(ge=0, description="Cooldown after stop-loss (minutes)")
    min_trade_notional_usd: float = Field(gt=0, description="Minimum trade size USD")
    dust_threshold_usd: float = Field(default=0.0, ge=0, description="Positions below this USD value are treated as dust")
    allow_adds_when_over_cap: bool = Field(default=True, description="Allow add-ons to existing positions when caps are saturated")
    count_open_orders_in_cap: bool = Field(default=True, description="Count open buy orders toward max position cap")
    allow_pyramiding: bool = Field(default=False, description="Allow adds on existing positions")
    pyramid_cooldown_seconds: int = Field(default=0, ge=0, description="Cooldown between pyramid adds")
    max_adds_per_asset_per_day: int = Field(default=0, ge=0, description="Max add-ons per asset per day")
    stop_loss_pct: float = Field(gt=0, le=100, description="Stop loss %")
    take_profit_pct: float = Field(gt=0, description="Take profit %")
    count_external_positions: bool = Field(default=True, description="Count non-managed holdings toward exposure cap")
    managed_position_tag: str = Field(default="247trader", min_length=1, description="Client order prefix identifying managed trades")
    external_exposure_buffer_pct: float = Field(default=0.0, ge=0, le=100, description="Buffer percent of external exposure ignored by cap")

    @field_validator('max_per_theme_pct')
    @classmethod
    def validate_theme_percentages(cls, v: Dict[str, float]) -> Dict[str, float]:
        """Validate theme percentages are positive and ≤100"""
        for theme, pct in v.items():
            if pct <= 0 or pct > 100:
                raise ValueError(f"Theme {theme} percentage must be 0 < pct ≤ 100, got {pct}")
        return v


class PositionSizingConfig(BaseModel):
    """Position sizing parameters"""
    method: str = Field(pattern="^(fixed|risk_parity|kelly)$", description="Sizing method")
    risk_per_trade_pct: float = Field(gt=0, le=100, description="Risk per trade %")
    fixed_size_usd: float = Field(gt=0, description="Fixed size USD")
    min_order_usd: float = Field(gt=0, description="Minimum order USD")
    max_order_usd: float = Field(gt=0, description="Maximum order USD")
    allow_pyramiding: bool = Field(description="Allow pyramiding")
    max_pyramid_positions: int = Field(ge=0, description="Max pyramid positions")

    @field_validator('max_order_usd')
    @classmethod
    def validate_order_sizes(cls, v: float, info) -> float:
        """Ensure max_order_usd >= min_order_usd"""
        min_order = info.data.get('min_order_usd', 0)
        if v < min_order:
            raise ValueError(f"max_order_usd ({v}) must be >= min_order_usd ({min_order})")
        return v


class LiquidityConfig(BaseModel):
    """Liquidity requirements"""
    min_24h_volume_usd: float = Field(gt=0, description="Minimum 24h volume USD")
    max_spread_bps: float = Field(gt=0, le=10000, description="Max spread (bps)")
    min_depth_20bps_usd: float = Field(gt=0, description="Min orderbook depth USD")


class TriggersConfig(BaseModel):
    """Trigger parameters"""
    price_move: Optional[Dict[str, float]] = Field(default=None, description="Price move thresholds")
    volume_spike: Optional[Dict[str, float]] = Field(default=None, description="Volume spike params")
    breakout: Optional[Dict[str, int]] = Field(default=None, description="Breakout params")
    min_score: float = Field(ge=0, le=1, description="Minimum trigger score")


class StrategyConfig(BaseModel):
    """Strategy parameters"""
    base_position_pct: Dict[str, float] = Field(description="Base position % by tier")
    max_open_positions: int = Field(gt=0, description="Max open positions")
    min_conviction_to_propose: float = Field(ge=0, le=1, description="Min conviction threshold")

    @field_validator('base_position_pct')
    @classmethod
    def validate_position_percentages(cls, v: Dict[str, float]) -> Dict[str, float]:
        """Validate position percentages are positive and reasonable"""
        for tier, pct in v.items():
            if pct <= 0 or pct > 100:
                raise ValueError(f"Tier {tier} position % must be 0 < pct ≤ 100, got {pct}")
        return v


class MicrostructureConfig(BaseModel):
    """Microstructure parameters"""
    max_expected_slippage_bps: float = Field(gt=0, le=10000, description="Max expected slippage (bps)")
    max_quote_age_seconds: int = Field(gt=0, description="Max quote age (seconds)")


class ExecutionConfig(BaseModel):
    """Execution parameters"""
    default_order_type: str = Field(pattern="^(market|limit|limit_post_only)$", description="Default order type")
    maker_fee_bps: float = Field(ge=0, le=1000, description="Maker fee (bps)")
    taker_fee_bps: float = Field(ge=0, le=1000, description="Taker fee (bps)")
    maker_first: bool = Field(default=True, description="Attempt maker route first")
    maker_max_reprices: int = Field(default=0, ge=0, description="Number of reprices before giving up")
    maker_max_ttl_sec: int = Field(default=0, ge=0, description="Max TTL for maker orders")
    maker_first_min_ttl_sec: int = Field(default=0, ge=0, description="Initial TTL for maker attempts")
    maker_retry_min_ttl_sec: int = Field(default=0, ge=0, description="Minimum TTL for retry attempts")
    maker_reprice_decay: float = Field(default=1.0, gt=0, description="Decay applied to TTL per retry")
    taker_fallback: bool = Field(default=False, description="Allow taker fallback when maker fails")
    prefer_ioc: bool = Field(default=True, description="Use IOC for taker fallbacks")
    taker_max_slippage_bps: Dict[str, float] = Field(default_factory=dict, description="Per-tier taker slippage caps")
    purge_maker_ttl_sec: int = Field(default=0, ge=0, description="Maker TTL when purging positions")
    preferred_quote_currencies: List[str] = Field(min_length=1, description="Preferred quote currencies")
    auto_convert_preferred_quote: bool = Field(description="Auto-convert to preferred quote")
    clamp_small_trades: bool = Field(description="Clamp trades below minimum")
    small_order_market_threshold_usd: float = Field(ge=0, description="Small order threshold USD")
    allow_min_bump_in_risk: bool = Field(default=True, description="Allow risk engine to bump small proposals up to min notional")
    failed_order_cooldown_seconds: int = Field(ge=0, description="Failed order cooldown (seconds)")
    cancel_after_seconds: int = Field(ge=0, description="Cancel stale orders after (seconds)")
    post_only_ttl_seconds: int = Field(ge=0, description="Cancel post-only orders after (seconds)")
    partial_fill_min_pct: float = Field(default=0.0, ge=0, le=1, description="Minimum partial fill percent treated as success")
    max_order_age_seconds: int = Field(default=0, ge=0, description="Max age before force-canceling orders")
    post_trade_reconcile_wait_seconds: float = Field(ge=0, description="Wait after trade (seconds)")
    min_notional_usd: float = Field(default=0.0, ge=0, description="Execution-layer minimum notional (USD)")
    max_slippage_bps: float = Field(default=0.0, ge=0, description="Max allowed slippage vs reference")
    hard_max_spread_bps: float = Field(default=0.0, ge=0, description="Maximum acceptable spread at execution")
    slippage_budget_t1_bps: float = Field(default=0.0, ge=0, description="Slippage budget for tier 1 assets")
    slippage_budget_t2_bps: float = Field(default=0.0, ge=0, description="Slippage budget for tier 2 assets")
    slippage_budget_t3_bps: float = Field(default=0.0, ge=0, description="Slippage budget for tier 3 assets")
    cancel_retry_backoff_ms: List[int] = Field(default_factory=list, description="Retry backoff schedule for cancel attempts (milliseconds)")
    promote_to_taker_if_budget_allows: bool = Field(default=False, description="Promote to taker orders when total cost fits budget")
    taker_promotion_requirements: Dict[str, float] = Field(default_factory=dict, description="Requirements for taker promotion decisions")


class DataConfig(BaseModel):
    """Data quality parameters"""
    max_age_s: int = Field(gt=0, description="Max data age (seconds)")
    max_quote_staleness_seconds: int = Field(gt=0, description="Max quote staleness (seconds)")


class CircuitBreakerConfig(BaseModel):
    """Circuit breaker parameters"""
    api_error_threshold: int = Field(gt=0, description="API error threshold")
    api_error_window_minutes: int = Field(gt=0, description="API error window (minutes)")
    rate_limit_threshold: int = Field(gt=0, description="Rate limit threshold")
    rate_limit_window_minutes: int = Field(gt=0, description="Rate limit window (minutes)")


class PolicySchema(BaseModel):
    """Complete policy configuration schema"""
    risk: RiskConfig
    position_sizing: PositionSizingConfig
    liquidity: LiquidityConfig
    triggers: TriggersConfig
    strategy: StrategyConfig
    microstructure: MicrostructureConfig
    execution: ExecutionConfig
    data: DataConfig
    circuit_breaker: CircuitBreakerConfig


# ===== Universe Schema =====
class TierConstraints(BaseModel):
    """Tier-specific constraints"""
    max_allocation_pct: float = Field(gt=0, le=100, description="Max allocation %")
    max_spread_bps: float = Field(gt=0, le=10000, description="Max spread (bps)")
    min_24h_volume_usd: float = Field(gt=0, description="Min 24h volume USD")
    max_drawdown_pct: Optional[float] = Field(default=None, gt=0, le=100, description="Max drawdown %")
    max_hold_period_hours: Optional[int] = Field(default=None, gt=0, description="Max hold period (hours)")


class TierConfig(BaseModel):
    """Tier configuration"""
    constraints: TierConstraints
    refresh: str = Field(pattern="^(hourly|daily|weekly)$", description="Refresh frequency")
    symbols: Optional[List[str]] = Field(default=None, description="Fixed symbols")
    filters: Optional[List[str]] = Field(default=None, description="Dynamic filters")
    max_tier_3_symbols: Optional[int] = Field(default=None, ge=0, description="Max tier 3 symbols")
    min_news_sources: Optional[int] = Field(default=None, gt=0, description="Min news sources")
    require_verification: Optional[bool] = Field(default=None, description="Require verification")
    event_types_allowed: Optional[List[str]] = Field(default=None, description="Allowed event types")


class ClusterDefinitions(BaseModel):
    """Cluster/theme definitions"""
    definitions: Dict[str, List[str]] = Field(description="Cluster symbol mappings")
    enabled: bool = Field(description="Enable cluster tracking")

    @field_validator('definitions')
    @classmethod
    def validate_definitions(cls, v: Dict[str, List[str]]) -> Dict[str, List[str]]:
        """Ensure each cluster has at least one symbol"""
        for cluster, symbols in v.items():
            if not symbols or len(symbols) == 0:
                raise ValueError(f"Cluster {cluster} must have at least one symbol")
        return v


class ExclusionsConfig(BaseModel):
    """Exclusion rules"""
    never_trade: List[str] = Field(description="Symbols never to trade")
    red_flags: List[str] = Field(description="Red flag event types")
    temporary_ban_hours: int = Field(gt=0, description="Temporary ban duration (hours)")


class RegimeModifiers(BaseModel):
    """Regime-based allocation modifiers"""
    bear: Dict[str, float] = Field(description="Bear market multipliers")
    bull: Dict[str, float] = Field(description="Bull market multipliers")
    chop: Dict[str, float] = Field(description="Choppy market multipliers")
    crash: Dict[str, float] = Field(description="Crash market multipliers")

    @field_validator('bear', 'bull', 'chop', 'crash')
    @classmethod
    def validate_multipliers(cls, v: Dict[str, float]) -> Dict[str, float]:
        """Validate multipliers are non-negative"""
        for tier, mult in v.items():
            if mult < 0:
                raise ValueError(f"Multiplier for {tier} must be >= 0, got {mult}")
        return v


class DynamicUniverseConfig(BaseModel):
    """Dynamic universe discovery config"""
    max_spread_bps: float = Field(gt=0, le=10000, description="Max spread (bps)")
    min_price_usd: float = Field(gt=0, description="Min price USD")
    tier1_max_symbols: int = Field(gt=0, description="Max tier 1 symbols")
    tier1_min_volume_usd: float = Field(gt=0, description="Min tier 1 volume USD")
    tier2_max_symbols: int = Field(gt=0, description="Max tier 2 symbols")
    tier2_min_volume_usd: float = Field(gt=0, description="Min tier 2 volume USD")
    tier3_max_symbols: int = Field(ge=0, description="Max tier 3 symbols")
    tier3_min_volume_usd: float = Field(gt=0, description="Min tier 3 volume USD")


class UniverseTopLevel(BaseModel):
    """Universe-level configuration"""
    method: str = Field(pattern="^(static|dynamic_discovery)$", description="Universe method")
    max_universe_size: int = Field(gt=0, description="Max universe size")
    refresh_interval_hours: int = Field(gt=0, description="Refresh interval (hours)")
    dynamic_config: DynamicUniverseConfig


class UniverseSchema(BaseModel):
    """Complete universe configuration schema"""
    clusters: ClusterDefinitions
    exclusions: ExclusionsConfig
    liquidity: LiquidityConfig
    regime_modifiers: RegimeModifiers
    tiers: Dict[str, TierConfig]
    universe: UniverseTopLevel


# ===== Signals Schema =====
class TriggerRegimeMultipliers(BaseModel):
    """Regime multipliers for triggers"""
    bull: float = Field(ge=0, description="Bull market multiplier")
    chop: float = Field(ge=0, description="Choppy market multiplier")
    bear: float = Field(ge=0, description="Bear market multiplier")
    crash: float = Field(ge=0, description="Crash market multiplier")


class SignalTriggersConfig(BaseModel):
    """Signal trigger parameters"""
    volume_spike_min_ratio: float = Field(gt=0, description="Volume spike ratio")
    volume_lookback_periods: int = Field(gt=0, description="Volume lookback periods")
    breakout_lookback_bars: int = Field(gt=0, description="Breakout lookback bars")
    breakout_threshold_pct: float = Field(gt=0, description="Breakout threshold %")
    min_trigger_score: float = Field(ge=0, le=1, description="Min trigger score")
    min_trigger_confidence: float = Field(ge=0, le=1, description="Min trigger confidence")
    max_triggers_per_cycle: int = Field(gt=0, description="Max triggers per cycle")
    regime_multipliers: TriggerRegimeMultipliers


class SignalsSchema(BaseModel):
    """Complete signals configuration schema"""
    triggers: SignalTriggersConfig


# ===== Validation Functions =====
def _format_yaml_error(file_path: Path, error: yaml.YAMLError) -> str:
    """Return enriched message with line/column context for YAML errors."""

    message = f"Malformed YAML in {file_path}: {error}"
    mark = getattr(error, "problem_mark", None)
    if mark is None:
        return message

    line = getattr(mark, "line", None)
    column = getattr(mark, "column", None)

    if line is None or column is None:
        return message

    try:
        raw_lines = file_path.read_text().splitlines()
    except Exception:
        return (
            f"Malformed YAML in {file_path}: line {line + 1}, column {column + 1}: "
            f"{getattr(error, 'problem', str(error))}"
        )

    start = max(line - 2, 0)
    end = min(line + 3, len(raw_lines))

    snippet_lines: List[str] = []
    for idx in range(start, end):
        pointer = "▶" if idx == line else " "
        snippet_lines.append(f"{pointer} {idx + 1:04d} | {raw_lines[idx]}")

    snippet = "\n".join(snippet_lines)
    problem = getattr(error, "problem", str(error))

    return (
        f"Malformed YAML in {file_path}: line {line + 1}, column {column + 1}: {problem}\n"
        f"Context:\n{snippet}"
    )


def load_yaml_file(file_path: Path) -> Dict[str, Any]:
    """
    Load YAML file and return as dict.
    
    Args:
        file_path: Path to YAML file
        
    Returns:
        Parsed YAML as dict
        
    Raises:
        FileNotFoundError: If file doesn't exist
        yaml.YAMLError: If YAML is malformed
    """
    if not file_path.exists():
        raise FileNotFoundError(f"Config file not found: {file_path}")
    
    with open(file_path, 'r') as f:
        try:
            return yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise yaml.YAMLError(_format_yaml_error(file_path, e))


def validate_policy(config_dir: Path) -> List[str]:
    """
    Validate policy.yaml against schema.
    
    Args:
        config_dir: Path to config directory
        
    Returns:
        List of error messages (empty if valid)
    """
    errors = []
    policy_path = config_dir / "policy.yaml"
    
    try:
        config = load_yaml_file(policy_path)
        PolicySchema(**config)
        logger.info("✅ policy.yaml validation passed")
    except FileNotFoundError as e:
        errors.append(f"policy.yaml: {e}")
    except yaml.YAMLError as e:
        errors.append(f"policy.yaml: Invalid YAML - {e}")
    except ValidationError as e:
        for error in e.errors():
            field = " -> ".join(str(loc) for loc in error['loc'])
            errors.append(f"policy.yaml: {field}: {error['msg']}")
    except Exception as e:
        errors.append(f"policy.yaml: Unexpected error - {e}")
    
    return errors


def validate_universe(config_dir: Path) -> List[str]:
    """
    Validate universe.yaml against schema.
    
    Args:
        config_dir: Path to config directory
        
    Returns:
        List of error messages (empty if valid)
    """
    errors = []
    universe_path = config_dir / "universe.yaml"
    
    try:
        config = load_yaml_file(universe_path)
        UniverseSchema(**config)
        logger.info("✅ universe.yaml validation passed")
    except FileNotFoundError as e:
        errors.append(f"universe.yaml: {e}")
    except yaml.YAMLError as e:
        errors.append(f"universe.yaml: Invalid YAML - {e}")
    except ValidationError as e:
        for error in e.errors():
            field = " -> ".join(str(loc) for loc in error['loc'])
            errors.append(f"universe.yaml: {field}: {error['msg']}")
    except Exception as e:
        errors.append(f"universe.yaml: Unexpected error - {e}")
    
    return errors


def validate_signals(config_dir: Path) -> List[str]:
    """
    Validate signals.yaml against schema.
    
    Args:
        config_dir: Path to config directory
        
    Returns:
        List of error messages (empty if valid)
    """
    errors = []
    signals_path = config_dir / "signals.yaml"
    
    try:
        config = load_yaml_file(signals_path)
        SignalsSchema(**config)
        logger.info("✅ signals.yaml validation passed")
    except FileNotFoundError as e:
        errors.append(f"signals.yaml: {e}")
    except yaml.YAMLError as e:
        errors.append(f"signals.yaml: Invalid YAML - {e}")
    except ValidationError as e:
        for error in e.errors():
            field = " -> ".join(str(loc) for loc in error['loc'])
            errors.append(f"signals.yaml: {field}: {error['msg']}")
    except Exception as e:
        errors.append(f"signals.yaml: Unexpected error - {e}")
    
    return errors


def validate_sanity_checks(config_dir: Path) -> List[str]:
    """
    Perform logical consistency checks across configuration files.
    
    Detects:
    - Contradictions (e.g., pyramiding enabled but max_adds=0)
    - Unsafe values (e.g., stop_loss > take_profit)
    - Deprecated keys (e.g., old parameter names)
    - Missing required fields for mode
    
    Args:
        config_dir: Path to config directory
        
    Returns:
        List of sanity check error messages (empty if all pass)
    """
    errors = []
    
    try:
        # Load all configs
        policy_path = config_dir / "policy.yaml"
        policy = load_yaml_file(policy_path)
        
        # === CONTRADICTION CHECKS ===
        
        # Check: Pyramiding enabled but no adds allowed
        risk = policy.get("risk", {})
        if risk.get("allow_pyramiding", False):
            if risk.get("max_adds_per_asset_per_day", 0) == 0:
                errors.append(
                    "CONTRADICTION: risk.allow_pyramiding=true but max_adds_per_asset_per_day=0 "
                    "(no adds possible). Set allow_pyramiding=false or increase max_adds."
                )
        
        # Check: Position sizing pyramiding vs risk pyramiding mismatch
        pos_sizing = policy.get("position_sizing", {})
        if pos_sizing.get("allow_pyramiding", False) and not risk.get("allow_pyramiding", False):
            errors.append(
                "CONTRADICTION: position_sizing.allow_pyramiding=true but risk.allow_pyramiding=false. "
                "Both must be aligned."
            )
        
        # Check: Max pyramid positions set but pyramiding disabled
        if pos_sizing.get("max_pyramid_positions", 0) > 0 and not pos_sizing.get("allow_pyramiding", False):
            errors.append(
                "CONTRADICTION: position_sizing.max_pyramid_positions > 0 but allow_pyramiding=false. "
                "Enable pyramiding or set max_pyramid_positions=0."
            )
        
        # === UNSAFE VALUE CHECKS ===
        
        # Check: Stop loss >= take profit (impossible to profit)
        stop_loss = risk.get("stop_loss_pct", 0)
        take_profit = risk.get("take_profit_pct", 0)
        if stop_loss > 0 and take_profit > 0:  # Both must be set
            if stop_loss >= take_profit:
                errors.append(
                    f"UNSAFE: risk.stop_loss_pct ({stop_loss}%) >= take_profit_pct ({take_profit}%). "
                    f"Take profit must exceed stop loss for profitable trades."
                )
        
        # Check: Negative percentages (should be positive magnitudes)
        if stop_loss < 0:
            errors.append(
                f"UNSAFE: risk.stop_loss_pct ({stop_loss}%) is negative. "
                f"Specify as positive magnitude (e.g., 10.0 for -10% stop)."
            )
        if take_profit < 0:
            errors.append(
                f"UNSAFE: risk.take_profit_pct ({take_profit}%) is negative. "
                f"Specify as positive magnitude."
            )
        
        # Check: Max position size exceeds total at-risk cap
        max_position_pct = risk.get("max_position_size_pct", 0)
        max_at_risk_pct = risk.get("max_total_at_risk_pct", 0)
        if max_position_pct > max_at_risk_pct:
            errors.append(
                f"UNSAFE: risk.max_position_size_pct ({max_position_pct}%) > max_total_at_risk_pct ({max_at_risk_pct}%). "
                f"Single position would exceed total exposure cap."
            )
        
        # Check: Max open positions * max position size exceeds total cap
        max_open_positions = risk.get("max_open_positions", 0)
        theoretical_max = max_open_positions * max_position_pct
        if theoretical_max > max_at_risk_pct:
            errors.append(
                f"UNSAFE: max_open_positions ({max_open_positions}) × max_position_size_pct ({max_position_pct}%) "
                f"= {theoretical_max}% exceeds max_total_at_risk_pct ({max_at_risk_pct}%). "
                f"System cannot fill all positions."
            )
        
        # Check: Daily stop >= weekly stop (should be tighter)
        daily_stop = abs(risk.get("daily_stop_pnl_pct", 0))
        weekly_stop = abs(risk.get("weekly_stop_pnl_pct", 0))
        if daily_stop > 0 and weekly_stop > 0:
            if daily_stop >= weekly_stop:
                errors.append(
                    f"UNSAFE: daily_stop_pnl_pct ({-daily_stop}%) >= weekly_stop_pnl_pct ({-weekly_stop}%). "
                    f"Daily stop should be tighter than weekly stop."
                )
        
        # Check: Min order > max order (execution impossible)
        exec_config = policy.get("execution", {})
        min_notional = exec_config.get("min_notional_usd", 0)
        if min_notional > 0:
            min_trade_notional = risk.get("min_trade_notional_usd", 0)
            if min_trade_notional > 0 and min_notional > min_trade_notional:
                errors.append(
                    f"UNSAFE: execution.min_notional_usd ({min_notional}) > risk.min_trade_notional_usd ({min_trade_notional}). "
                    f"Execution layer minimum exceeds trade sizing minimum."
                )
        
        # Check: Position sizing min > max order
        pos_min = pos_sizing.get("min_order_usd", 0)
        pos_max = pos_sizing.get("max_order_usd", 0)
        if pos_min > 0 and pos_max > 0:
            if pos_min > pos_max:
                errors.append(
                    f"UNSAFE: position_sizing.min_order_usd ({pos_min}) > max_order_usd ({pos_max}). "
                    f"No orders can be placed within range."
                )
        
        # Check: Unreasonable spread/slippage thresholds
        liquidity = policy.get("liquidity", {})
        max_spread_bps = liquidity.get("max_spread_bps", 0)
        if max_spread_bps > 1000:  # > 10%
            errors.append(
                f"UNSAFE: liquidity.max_spread_bps ({max_spread_bps}) > 1000 (10%). "
                f"Extremely wide spread threshold may indicate misconfiguration."
            )
        
        max_slippage_bps = exec_config.get("max_slippage_bps", 0)
        if max_slippage_bps > 500:  # > 5%
            errors.append(
                f"UNSAFE: execution.max_slippage_bps ({max_slippage_bps}) > 500 (5%). "
                f"Excessive slippage tolerance may lead to poor fills."
            )
        
        # === DEPRECATED KEY CHECKS ===
        
        # Check: Old exposure parameter name
        if "max_exposure_pct" in risk:
            errors.append(
                "DEPRECATED: risk.max_exposure_pct renamed to max_total_at_risk_pct. "
                "Update config to use new parameter name."
            )
        
        # Check: Old cache parameter (removed from UniverseManager)
        universe_path = config_dir / "universe.yaml"
        universe = load_yaml_file(universe_path)
        if "cache_ttl_seconds" in universe:
            errors.append(
                "DEPRECATED: universe.cache_ttl_seconds removed. "
                "Use universe.refresh_interval_hours instead."
            )
        
        # === MODE-SPECIFIC CHECKS ===
        
        # Check: LIVE mode requirements (when we detect LIVE intent)
        # Note: This is advisory since mode is set at runtime via CLI
        
        if risk.get("max_total_at_risk_pct", 0) > 50:
            errors.append(
                f"WARNING: max_total_at_risk_pct ({risk['max_total_at_risk_pct']}%) > 50%. "
                f"Consider using conservative profile (25%) for LIVE mode. "
                f"High exposure suitable for PAPER/DRY_RUN only."
            )
        
        # Check: Missing circuit breaker config
        circuit_breaker = policy.get("circuit_breaker", {})
        if not circuit_breaker:
            errors.append(
                "MISSING: policy.circuit_breaker section not found. "
                "Circuit breakers are required for safe operation."
            )
        
        # Check: Data staleness threshold too permissive
        data_config = policy.get("data", {})
        max_age_s = data_config.get("max_age_s", 0)
        if max_age_s > 300:  # > 5 minutes
            errors.append(
                f"UNSAFE: data.max_age_s ({max_age_s}s) > 300s (5 min). "
                f"Stale data threshold too permissive for fast markets."
            )
        
    except FileNotFoundError as e:
        errors.append(f"Sanity checks failed: {e}")
    except Exception as e:
        errors.append(f"Sanity checks failed: Unexpected error - {e}")
    
    if not errors:
        logger.info("✅ Configuration sanity checks passed")
    else:
        logger.warning(f"⚠️  {len(errors)} sanity check issue(s) found")
    
    return errors


def validate_all_configs(config_dir: str = "config") -> List[str]:
    """
    Validate all configuration files.
    
    Performs:
    1. Schema validation (Pydantic type checks)
    2. Sanity checks (logical consistency)
    
    Args:
        config_dir: Path to config directory (string or Path)
        
    Returns:
        List of all error messages (empty if all valid)
        
    Example:
        errors = validate_all_configs("config")
        if errors:
            for error in errors:
                print(f"ERROR: {error}")
            sys.exit(1)
    """
    config_path = Path(config_dir)
    
    all_errors = []
    
    # Schema validation
    all_errors.extend(validate_policy(config_path))
    all_errors.extend(validate_universe(config_path))
    all_errors.extend(validate_signals(config_path))
    
    # Sanity checks (only if schema validation passed)
    if not all_errors:
        all_errors.extend(validate_sanity_checks(config_path))
    
    if not all_errors:
        logger.info("✅ All config files validated successfully")
    else:
        logger.error(f"❌ {len(all_errors)} validation error(s) found")
    
    return all_errors


if __name__ == "__main__":
    """Run validation from command line"""
    import sys
    
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
    
    config_dir = sys.argv[1] if len(sys.argv) > 1 else "config"
    
    errors = validate_all_configs(config_dir)
    
    if errors:
        print("\n❌ Configuration Validation Failed:\n")
        for error in errors:
            print(f"  • {error}")
        print()
        sys.exit(1)
    else:
        print("\n✅ All configuration files are valid!\n")
        sys.exit(0)
