"""
247trader-v2 Core: Execution Engine

Order placement with preview, route selection, and idempotency.
Ported from v1 with simplified logic for rules-first strategy.

Includes deterministic client order ID generation to prevent duplicate
submissions on network retries.
"""

import uuid
import hashlib
import time
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from typing import Any, Dict, List, Optional, Tuple, Set
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import logging

from requests import exceptions as requests_exceptions

from core.exchange_coinbase import CoinbaseExchange, get_exchange
from core.exceptions import CriticalDataUnavailable
from infra.state_store import StateStore
from core.order_state import get_order_state_machine, OrderStatus, OrderState

logger = logging.getLogger(__name__)


@dataclass
class ExecutionResult:
    """Result of order execution"""
    success: bool
    order_id: Optional[str]
    symbol: Optional[str]
    side: Optional[str]
    filled_size: float
    filled_price: float
    fees: float
    slippage_bps: float
    route: str  # "market_ioc" | "limit_post" | "dry_run"
    error: Optional[str] = None
    timestamp: datetime = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now(timezone.utc)
        if self.side:
            self.side = self.side.upper()


@dataclass
class PostOnlyTTLResult:
    """Outcome of post-only TTL enforcement."""

    triggered: bool
    canceled: bool = False
    status: Optional[str] = None
    fills: Optional[List[Dict[str, Any]]] = None
    filled_size: Optional[float] = None
    filled_price: Optional[float] = None
    filled_value: Optional[float] = None
    fees: Optional[float] = None
    error: Optional[str] = None


class ExecutionEngine:
    """
    Order execution engine.
    
    Responsibilities:
    - Preview orders before placement
    - Select best route (limit post-only vs market IOC)
    - Place orders with idempotency
    - Track fills and fees
    
    Safety:
    - DRY_RUN mode prevents real orders
    - Liquidity checks before placement
    - Slippage protection
    """
    
    def __init__(self, mode: str = "DRY_RUN", exchange: Optional[CoinbaseExchange] = None,
                 policy: Optional[Dict] = None, state_store: Optional[StateStore] = None):
        """
        Initialize execution engine.
        
        Args:
                product_metadata = constraints.get("metadata") or product_metadata
            mode: "DRY_RUN" | "PAPER" | "LIVE"
            exchange: Coinbase exchange instance
            policy: Policy configuration dict (optional, for reading limits)
        """
        self.mode = mode.upper()
        self.exchange = exchange or get_exchange()
        self.policy = policy or {}
        self.state_store = state_store
        self.order_state_machine = get_order_state_machine()
        
        # Load limits from policy or use defaults
        execution_config = self.policy.get("execution", {})
        microstructure_config = self.policy.get("microstructure", {})
        risk_config = self.policy.get("risk", {})
        portfolio_config = self.policy.get("portfolio_management", {})
        
        self.max_slippage_bps = microstructure_config.get("max_expected_slippage_bps", 50.0)
        self.max_spread_bps = microstructure_config.get("max_spread_bps", 100.0)
        self.min_notional_usd = risk_config.get("min_trade_notional_usd", 100.0)  # From policy.yaml
        self.min_depth_multiplier = 2.0  # Want 2x order size in depth
        
        # Fee structure (basis points)
        self.maker_fee_bps = execution_config.get("maker_fee_bps", 40)  # 0.40% default
        self.taker_fee_bps = execution_config.get("taker_fee_bps", 60)  # 0.60% default
        
        # Order type preference
        self.default_order_type = execution_config.get("default_order_type", "limit")
        self.limit_post_only = (self.default_order_type == "limit_post_only")
        
        # Quote currency preferences
        self.preferred_quotes = execution_config.get(
            "preferred_quote_currencies",
            ["USDC", "USD", "USDT", "BTC", "ETH"]
        )
        # Optional behavior flags
        self.auto_convert_preferred_quote = execution_config.get(
            "auto_convert_preferred_quote", False
        )
        self.clamp_small_trades = execution_config.get(
            "clamp_small_trades", True
        )
        self.small_order_market_threshold_usd = float(execution_config.get(
            "small_order_market_threshold_usd", 0.0
        ))
        self.failed_order_cooldown_seconds = int(execution_config.get(
            "failed_order_cooldown_seconds", 0
        ))
        self.post_only_ttl_seconds = int(execution_config.get(
            "post_only_ttl_seconds", 0
        ) or 0)
        self.convert_api_retry_seconds = int(execution_config.get(
            "convert_api_retry_seconds", 900
        ))
        self.cancel_retry_backoff_ms = [
            int(value)
            for value in execution_config.get("cancel_retry_backoff_ms", [250, 500, 1000])
            if isinstance(value, (int, float))
        ] or [250, 500, 1000]
        self.promote_to_taker_if_budget_allows = bool(
            execution_config.get("promote_to_taker_if_budget_allows", False)
        )
        self.taker_promotion_requirements = execution_config.get(
            "taker_promotion_requirements", {}
        ) or {}
        self.price_source_for_mid = execution_config.get("price_source_for_mid", "best_bid_ask_mid")
        self.execution_min_notional_usd = float(
            execution_config.get("min_notional_usd", 0.0) or 0.0
        )

        self.maker_first = bool(execution_config.get("maker_first", self.limit_post_only))
        self.maker_max_reprices = max(0, int(execution_config.get("maker_max_reprices", 1) or 0))
        self.maker_max_ttl_seconds = int(
            execution_config.get("maker_max_ttl_sec", execution_config.get("post_only_ttl_seconds", 12) or 12)
        )
        self.maker_first_min_ttl_seconds = int(execution_config.get("maker_first_min_ttl_sec", 6) or 6)
        self.maker_retry_min_ttl_seconds = int(execution_config.get("maker_retry_min_ttl_sec", 3) or 3)
        self.maker_reprice_decay = float(execution_config.get("maker_reprice_decay", 0.7) or 0.7)
        if self.post_only_ttl_seconds <= 0 and self.maker_max_ttl_seconds > 0:
            self.post_only_ttl_seconds = self.maker_max_ttl_seconds
        elif self.maker_max_ttl_seconds > 0:
            self.post_only_ttl_seconds = self.maker_max_ttl_seconds

        self.taker_fallback_enabled = bool(execution_config.get("taker_fallback", False))
        self.taker_prefer_ioc = bool(execution_config.get("prefer_ioc", True))
        self.taker_slippage_bps_per_tier = execution_config.get("taker_max_slippage_bps", {}) or {}

        # Track last failure by symbol to avoid retry spam
        self._last_fail = {}

        # Quote staleness threshold from policy
        self.max_quote_age_seconds = microstructure_config.get("max_quote_age_seconds", 30)

        # Slippage budgets by tier (slippage + fees must be < budget)
        self.slippage_budget_t1_bps = execution_config.get("slippage_budget_t1_bps", 20.0)
        self.slippage_budget_t2_bps = execution_config.get("slippage_budget_t2_bps", 35.0)
        self.slippage_budget_t3_bps = execution_config.get("slippage_budget_t3_bps", 60.0)

        raw_prefix = (
            portfolio_config.get("managed_order_prefix")
            or risk_config.get("managed_position_tag")
            or "247trader"
        )
        self.client_order_prefix = self._sanitize_client_prefix(str(raw_prefix))
        self._convert_denylist: Set[Tuple[str, str]] = set()
        self._convert_api_disabled = False
        self._convert_api_disabled_at: Optional[datetime] = None
        self._convert_api_last_error: Optional[str] = None

        logger.info(
            f"Initialized ExecutionEngine (mode={self.mode}, min_notional=${self.min_notional_usd}, "
            f"quotes={self.preferred_quotes}, auto_convert={self.auto_convert_preferred_quote}, "
            f"clamp_small_trades={self.clamp_small_trades}, maker_fee={self.maker_fee_bps}bps, "
            f"taker_fee={self.taker_fee_bps}bps, max_quote_age={self.max_quote_age_seconds}s, "
            f"post_only_ttl={self.post_only_ttl_seconds}s, "
            f"slippage_budget=[T1:{self.slippage_budget_t1_bps}, T2:{self.slippage_budget_t2_bps}, T3:{self.slippage_budget_t3_bps}]bps)"
        )

        if self.execution_min_notional_usd and self.execution_min_notional_usd > self.min_notional_usd:
            self.min_notional_usd = self.execution_min_notional_usd
    
    def _get_slippage_budget(self, tier: Optional[int]) -> float:
        """
        Get slippage budget for a given tier.
        
        Args:
            tier: Asset tier (1, 2, or 3), or None for default
            
        Returns:
            Slippage budget in bps
        """
        if tier == 1:
            return self.slippage_budget_t1_bps
        elif tier == 2:
            return self.slippage_budget_t2_bps
        elif tier == 3:
            return self.slippage_budget_t3_bps
        else:
            # Default to Tier 3 budget (most permissive) if tier unknown
            return self.slippage_budget_t3_bps
    
    def _validate_quote_freshness(self, quote, symbol: str) -> Optional[str]:
        """
        Validate quote timestamp is fresh enough for trading decisions.
        
        Args:
            quote: Quote object with timestamp field
            symbol: Symbol name for logging
            
        Returns:
            Error string if stale, None if fresh
        """
        if quote is None:
            return f"Quote is None for {symbol}"
        
        if not hasattr(quote, 'timestamp') or quote.timestamp is None:
            return f"Quote missing timestamp for {symbol}"
        
        now = datetime.now(timezone.utc)
        
        # Ensure quote timestamp is timezone-aware
        quote_ts = quote.timestamp
        if quote_ts.tzinfo is None:
            # Assume UTC if naive
            quote_ts = quote_ts.replace(tzinfo=timezone.utc)
        
        age_seconds = (now - quote_ts).total_seconds()
        
        if age_seconds > self.max_quote_age_seconds:
            return (f"Quote too stale for {symbol}: {age_seconds:.1f}s old "
                   f"(max: {self.max_quote_age_seconds}s)")
        
        if age_seconds < 0:
            # Future timestamp - clock skew issue
            return (f"Quote timestamp in future for {symbol}: {age_seconds:.1f}s ahead "
                   f"(possible clock skew)")
        
        logger.debug(f"Quote freshness OK for {symbol}: {age_seconds:.1f}s old")
        return None
    
    def _quantize_price(self, price: float, increment: str, cushion_ticks: int = 0) -> str:
        """
        Floor price to product increment with optional maker cushion.
        
        Args:
            price: Raw price value
            increment: Price increment from product spec (e.g. "0.01")
            cushion_ticks: Number of ticks to subtract for maker cushion (default 0)
            
        Returns:
            Quantized price as string
        """
        try:
            p_step = Decimal(increment)
            p_dec = Decimal(str(price))
            # Floor to increment
            quantized = (p_dec // p_step) * p_step
            # Apply cushion (subtract ticks for BUY, add ticks for SELL handled by caller)
            if cushion_ticks > 0:
                quantized = quantized - (p_step * cushion_ticks)
            # Ensure non-negative
            if quantized < 0:
                quantized = Decimal(0)
            return format(quantized, 'f')
        except (InvalidOperation, ValueError) as e:
            logger.warning(f"Price quantization failed: {e}, returning raw")
            return str(price)
    
    def _quantize_size(self, size: float, increment: str) -> str:
        """
        Floor size to product base increment.
        
        Args:
            size: Raw size value
            increment: Base increment from product spec (e.g. "0.1")
            
        Returns:
            Quantized size as string
        """
        try:
            s_step = Decimal(increment)
            s_dec = Decimal(str(size))
            quantized = (s_dec // s_step) * s_step
            # Ensure non-negative
            if quantized < 0:
                quantized = Decimal(0)
            return format(quantized, 'f')
        except (InvalidOperation, ValueError) as e:
            logger.warning(f"Size quantization failed: {e}, returning raw")
            return str(size)
    
    @staticmethod
    def _sanitize_client_prefix(prefix: str) -> str:
        """Ensure client order prefix is lowercase and ASCII-safe."""

        allowed = []
        for char in prefix.strip():
            if char.isalnum() or char in {"-", "_"}:
                allowed.append(char.lower())
            elif char.isspace():
                allowed.append("_")
        sanitized = "".join(allowed).strip("_")
        return sanitized or "247trader"

    def generate_client_order_id(self, symbol: str, side: str, size_usd: float, 
                                  timestamp: Optional[datetime] = None) -> str:
        """
        Generate deterministic client order ID from trade proposal attributes.
        
        This ensures that retries of the same trade proposal generate the same ID,
        enabling idempotent order submission and preventing duplicate orders on
        network failures.
        
        Args:
            symbol: Trading pair (e.g., "BTC-USD")
            side: "BUY" or "SELL"
            size_usd: Order size in USD
            timestamp: Optional timestamp (defaults to now, truncated to minute)
            
        Returns:
            Deterministic client order ID string
            
        Notes:
            - Timestamp is truncated to minute granularity to allow brief retries
            - Uses SHA256 hash for collision resistance
            - Format: "coid_" prefix + 16 hex chars
        """
        if timestamp is None:
            timestamp = datetime.now(timezone.utc)
        
        # Truncate timestamp to minute for brief retry window
        ts_minute = timestamp.replace(second=0, microsecond=0)
        ts_str = ts_minute.isoformat()
        
        # Round size to avoid floating point noise
        size_rounded = round(size_usd, 2)
        
        # Build deterministic input string
        input_str = f"{symbol}|{side.upper()}|{size_rounded}|{ts_str}"
        
        # Hash to deterministic ID
        hash_obj = hashlib.sha256(input_str.encode('utf-8'))
        hash_hex = hash_obj.hexdigest()[:16]  # First 16 chars for brevity
        
        base_id = f"coid_{hash_hex}"
        if self.client_order_prefix:
            return f"{self.client_order_prefix}_{base_id}"
        return base_id

    def _convert_api_available(self) -> Tuple[bool, Optional[float]]:
        """Return whether the convert API is currently available, with retry ETA."""

        if not self._convert_api_disabled:
            return True, None

        if self.convert_api_retry_seconds <= 0 or self._convert_api_disabled_at is None:
            return False, None

        elapsed = (datetime.now(timezone.utc) - self._convert_api_disabled_at).total_seconds()
        if elapsed >= self.convert_api_retry_seconds:
            logger.info(
                "Convert API retry window expired (%.1fs); re-enabling convert attempts",
                elapsed,
            )
            self._convert_api_disabled = False
            self._convert_api_disabled_at = None
            self._convert_api_last_error = None
            return True, None

        remaining = max(0.0, self.convert_api_retry_seconds - elapsed)
        return False, remaining

    def can_convert(self, from_currency: str, to_currency: str) -> bool:
        """Return True if convert API should be attempted for the pair."""

        pair = (from_currency.upper(), to_currency.upper())
        available, _ = self._convert_api_available()
        if not available:
            logger.debug(
                "Convert API disabled; skipping convert %s→%s and falling back to spot routing",
                pair[0],
                pair[1],
            )
            return False
        return pair not in self._convert_denylist
    
    def estimate_fee(self, size_usd: float, is_maker: bool = True) -> float:
        """
        Estimate trading fee for a given order size.
        
        Args:
            size_usd: Order size in USD
            is_maker: True for maker orders (limit post-only), False for taker (market/IOC)
            
        Returns:
            Estimated fee in USD
        """
        fee_bps = self.maker_fee_bps if is_maker else self.taker_fee_bps
        return size_usd * (fee_bps / 10000.0)
    
    def size_after_fees(self, gross_size_usd: float, is_maker: bool = True) -> float:
        """
        Calculate net position size after deducting fees.
        
        Args:
            gross_size_usd: Gross order size before fees
            is_maker: True for maker orders, False for taker
            
        Returns:
            Net size after fees in USD
        """
        fee = self.estimate_fee(gross_size_usd, is_maker)
        return gross_size_usd - fee
    
    def size_to_achieve_net(self, target_net_usd: float, is_maker: bool = True) -> float:
        """
        Calculate gross order size needed to achieve target net position after fees.
        
        For BUY: gross_size = target_net / (1 - fee_rate)
        For SELL: net_proceeds = gross_size * (1 - fee_rate)
        
        Args:
            target_net_usd: Desired net position size after fees
            is_maker: True for maker orders, False for taker
            
        Returns:
            Gross order size needed (before fees)
        """
        fee_bps = self.maker_fee_bps if is_maker else self.taker_fee_bps
        fee_rate = fee_bps / 10000.0
        return target_net_usd / (1.0 - fee_rate)

    def _build_execution_plan(self, force_order_type: Optional[str], size_usd: float) -> List[Dict[str, Any]]:
        """Construct ordered execution attempts respecting maker/taker preferences."""

        plan: List[Dict[str, Any]] = []
        forced = (force_order_type or "").lower()
        small_market = (
            self.small_order_market_threshold_usd
            and size_usd <= self.small_order_market_threshold_usd
        )

        if forced == "market":
            plan.append({"order_type": "market", "mode": "forced"})
            return plan

        if forced == "limit_post_only":
            attempts = max(1, self.maker_max_reprices + 1)
            for attempt in range(attempts):
                plan.append({"order_type": "limit_post_only", "mode": "forced", "attempt": attempt})
            return plan

        use_maker = bool(self.maker_first) and not small_market

        if use_maker:
            attempts = max(1, self.maker_max_reprices + 1)
            for attempt in range(attempts):
                plan.append({"order_type": "limit_post_only", "mode": "maker", "attempt": attempt})
            if self.taker_fallback_enabled:
                plan.append({"order_type": "market", "mode": "fallback"})
        else:
            plan.append({"order_type": "market", "mode": "primary"})

        return plan

    def _describe_execution_plan(self, plan: List[Dict[str, Any]]) -> str:
        """Return compact string summary for logging."""

        parts: List[str] = []
        maker_steps = [step for step in plan if step.get("order_type") == "limit_post_only"]
        fallback_steps = [step for step in plan if step.get("mode") == "fallback"]

        if maker_steps:
            count = len(maker_steps)
            parts.append(f"maker_x{count}")
        for step in plan:
            if step.get("order_type") == "market" and step.get("mode") == "forced":
                parts.append("market_forced")
        if fallback_steps:
            parts.append("taker_fallback")
        if not parts:
            parts.append("market")
        return "+".join(parts)

    def _adaptive_maker_ttl(self, quote, attempt_index: int) -> int:
        """Derive TTL for maker attempt using spread-aware heuristic."""

        if self.maker_max_ttl_seconds <= 0:
            return 0

        spread_bps = 0.0
        if quote is not None:
            try:
                spread_bps = float(getattr(quote, "spread_bps", 0.0) or 0.0)
            except (TypeError, ValueError):
                spread_bps = 0.0

        if spread_bps <= 2.0:
            ttl = 6
        elif spread_bps <= 5.0:
            ttl = 9
        else:
            ttl = 12

        ttl = min(ttl, self.maker_max_ttl_seconds)

        if attempt_index == 0:
            ttl = max(ttl, self.maker_first_min_ttl_seconds)
        else:
            decay = self.maker_reprice_decay if 0 < self.maker_reprice_decay < 1 else 0.7
            ttl = int(round(ttl * decay))
            ttl = max(ttl, self.maker_retry_min_ttl_seconds)

        return max(ttl, 1)
    
    def _clear_pending_marker(self, symbol: str, side: str, client_order_id: Optional[str] = None):
        """Clear pending marker for a symbol/side/client_order_id."""
        if self.state_store:
            try:
                self.state_store.clear_pending(symbol, side, coid=client_order_id)
                logger.debug(f"Cleared pending marker for {symbol} {side} (coid={client_order_id})")
            except Exception as e:
                logger.warning(f"Failed to clear pending marker for {symbol}: {e}")
    
    def _on_order_terminal(self, symbol: str, side: str, client_order_id: str, status: str):
        """
        Called when an order reaches a terminal state.
        Clears pending markers and updates order state machine.
        
        Terminal states: rejected, canceled, failed, filled
        """
        logger.debug(f"Order terminal: {client_order_id} {symbol} {side} → {status}")
        self._clear_pending_marker(symbol, side, client_order_id)
        
        # Transition order state machine if needed
        status_upper = status.upper()
        if status_upper == "REJECTED":
            self.order_state_machine.transition(client_order_id, OrderStatus.REJECTED)
        elif status_upper == "CANCELED" or status_upper == "CANCELLED":
            self.order_state_machine.transition(client_order_id, OrderStatus.CANCELLED)
        elif status_upper == "FAILED":
            self.order_state_machine.transition(client_order_id, OrderStatus.FAILED)
        elif status_upper == "FILLED":
            self.order_state_machine.transition(client_order_id, OrderStatus.FILLED)

    def _taker_slippage_budget(self, tier: Optional[int]) -> Optional[float]:
        if not self.taker_slippage_bps_per_tier:
            return None

        key = f"T{tier}" if tier is not None else None
        raw = None
        if key and key in self.taker_slippage_bps_per_tier:
            raw = self.taker_slippage_bps_per_tier.get(key)
        elif tier is not None and str(tier) in self.taker_slippage_bps_per_tier:
            raw = self.taker_slippage_bps_per_tier.get(str(tier))
        else:
            raw = self.taker_slippage_bps_per_tier.get("default")

        if raw is None:
            return None

        try:
            budget = float(raw)
        except (TypeError, ValueError):
            return None

        return budget if budget > 0 else None

    def _is_taker_slippage_allowed(self, estimated_slippage_bps: float, tier: Optional[int]) -> bool:
        budget = self._taker_slippage_budget(tier)
        if budget is None:
            return True
        try:
            return float(estimated_slippage_bps) <= budget
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _should_retry_maker(result: ExecutionResult) -> bool:
        if result.success:
            return False
        if not result.error:
            return False
        text = result.error.lower()
        return "post-only order canceled" in text
    
    def get_min_gross_size(self, is_maker: bool = True) -> float:
        """
        Get minimum gross order size that results in acceptable net position after fees.
        
        Returns:
            Minimum gross size in USD (accounts for fees)
        """
        # To achieve min_notional_usd net after fees, we need slightly more gross
        return self.size_to_achieve_net(self.min_notional_usd, is_maker=is_maker)
    
    def round_to_increment(self, value: float, increment: float, round_up: bool = False) -> float:
        """
        Round value to nearest multiple of increment.
        
        Args:
            value: Value to round
            increment: Increment size (e.g., 0.00000001 for BTC)
            round_up: If True, round up; if False, round down (default)
            
        Returns:
            Rounded value
        """
        if increment <= 0:
            return value
        if round_up:
            import math
            return float(math.ceil(value / increment) * increment)
        return float(int(value / increment) * increment)
    
    def enforce_product_constraints(self, symbol: str, size_usd: float, price: float, 
                                   is_maker: bool = True) -> dict:
        """
        Enforce Coinbase product constraints (increments, min size, min market funds).
        
        CRITICAL: After rounding, ensures net amount (post-fee) still exceeds minimums.
        This prevents orders from being rejected due to falling below thresholds after fees.
        
        Args:
            symbol: Trading pair (e.g., "BTC-USD")
            size_usd: Desired order size in USD (gross, before fees)
            price: Current price for size conversion
            is_maker: Whether order will be maker (for fee calculation)
            
        Returns:
            Dict with:
                - success: bool
                - adjusted_size_usd: float (rounded size, fee-adjusted if needed)
                - adjusted_size_base: float (rounded base quantity)
                - error: Optional[str]
                - fee_adjusted: bool (whether size was bumped for fee compliance)
        """
        # Get product metadata from exchange
        try:
            metadata = self.exchange.get_product_metadata(symbol)
        except Exception as e:
            logger.warning(f"Failed to fetch product metadata for {symbol}: {e}")
            # Fail open: return original size if metadata unavailable
            return {
                "success": True,
                "adjusted_size_usd": size_usd,
                "adjusted_size_base": size_usd / price if price > 0 else 0,
                "fee_adjusted": False,
                "warning": f"Product metadata unavailable: {e}"
            }
        
        if not metadata:
            logger.warning(f"No product metadata found for {symbol}")
            return {
                "success": True,
                "adjusted_size_usd": size_usd,
                "adjusted_size_base": size_usd / price if price > 0 else 0,
                "fee_adjusted": False,
                "warning": "No product metadata found"
            }
        
        # Extract constraints
        base_increment = float(metadata.get("base_increment") or 0)
        quote_increment = float(metadata.get("quote_increment") or 0)
        min_market_funds = float(metadata.get("min_market_funds") or 0)
        
        # Check minimum market funds (initial check)
        if min_market_funds > 0 and size_usd < min_market_funds:
            return {
                "success": False,
                "error": f"Size ${size_usd:.2f} below exchange minimum ${min_market_funds:.2f}"
            }
        
        # Calculate base size
        base_size = size_usd / price if price > 0 else 0
        
        # Round to base increment if specified
        if base_increment > 0:
            rounded_base = self.round_to_increment(base_size, base_increment)
            if rounded_base <= 0:
                return {
                    "success": False,
                    "error": f"Rounded base size {rounded_base} too small (increment: {base_increment})"
                }
            base_size = rounded_base
        
        # Recalculate USD size from rounded base
        adjusted_size_usd = base_size * price
        
        # Round to quote increment if specified
        if quote_increment > 0:
            adjusted_size_usd = self.round_to_increment(adjusted_size_usd, quote_increment)
        
        # ===== FEE-ADJUSTED MINIMUM NOTIONAL CHECK =====
        # After rounding, verify net amount (post-fee) still exceeds minimums
        fee_adjusted = False
        fee_bps = self.maker_fee_bps if is_maker else self.taker_fee_bps
        fee_rate = fee_bps / 10000.0
        net_after_fees = adjusted_size_usd * (1.0 - fee_rate)
        
        # Determine effective minimum (higher of exchange min and our policy min)
        effective_min = max(min_market_funds, self.min_notional_usd)
        
        # If net amount falls below minimum after fees, bump up the gross size
        if effective_min > 0 and net_after_fees < effective_min:
            # Calculate required gross size to achieve effective minimum net
            required_gross = effective_min / (1.0 - fee_rate)
            
            # Re-round to ensure compliance with increments
            if base_increment > 0:
                required_base = required_gross / price if price > 0 else 0
                rounded_base = self.round_to_increment(required_base, base_increment, round_up=True)
                adjusted_size_usd = rounded_base * price
            else:
                adjusted_size_usd = required_gross
            
            # Re-apply quote increment rounding (round up to maintain minimum)
            if quote_increment > 0:
                adjusted_size_usd = self.round_to_increment(adjusted_size_usd, quote_increment, round_up=True)
            
            # Recalculate base size for return
            base_size = adjusted_size_usd / price if price > 0 else 0
            fee_adjusted = True
            
            logger.debug(
                f"{symbol}: Fee-adjusted rounding bumped size ${net_after_fees:.2f} net "
                f"→ ${adjusted_size_usd:.2f} gross (min=${effective_min:.2f}, fee={fee_bps}bps)"
            )
        
        return {
            "success": True,
            "adjusted_size_usd": adjusted_size_usd,
            "adjusted_size_base": base_size,
            "fee_adjusted": fee_adjusted,
            "metadata": metadata
        }
    
    def _require_accounts(self, context: str) -> List[Dict]:
        try:
            return self.exchange.get_accounts()
        except CriticalDataUnavailable:
            raise
        except Exception as exc:
            raise CriticalDataUnavailable(f"accounts:{context}", exc) from exc

    def adjust_proposals_to_capital(self, proposals: List, portfolio_value_usd: float) -> List[Tuple]:
        """
        Adjust trade sizes based on available capital.
        
        Strategy:
        1. Get actual available balances (USDC, USD, etc.)
        2. Scale down position sizes to fit available capital
        3. Prioritize higher-conviction trades (by confidence score)
        4. Skip trades below minimum notional
        
        Args:
            proposals: List of TradeProposal objects
            portfolio_value_usd: Total portfolio value (for reference)
            
        Returns:
            List of (proposal, adjusted_size_usd) tuples
        """
        try:
            # Get fresh balances
            accounts = self._require_accounts("adjust_proposals")
            balances = {
                acc['currency']: float(acc.get('available_balance', {}).get('value', 0))
                for acc in accounts
            }
            
            # Convert all preferred quote currencies to USD
            available_capital = 0.0
            for quote in self.preferred_quotes:
                balance = balances.get(quote, 0)
                if balance == 0:
                    continue
                
                # USD/USDC/USDT are 1:1 with USD
                if quote in ['USD', 'USDC', 'USDT']:
                    available_capital += balance
                else:
                    # Convert crypto (BTC, ETH, etc.) to USD
                    try:
                        pair = f"{quote}-USD"
                        quote_obj = self.exchange.get_quote(pair)
                        usd_value = balance * quote_obj.mid
                        available_capital += usd_value
                        logger.debug(f"Converted {balance:.6f} {quote} to ${usd_value:.2f} USD")
                    except Exception as e:
                        logger.warning(f"Could not convert {quote} to USD: {e}")
                        # Skip this balance if conversion fails
                        continue
            
            logger.info(f"Available capital: ${available_capital:.2f} across {len([q for q in self.preferred_quotes if balances.get(q, 0) > 0])} currencies")
            
            if available_capital < self.min_notional_usd:
                logger.warning(f"Insufficient capital: ${available_capital:.2f} < ${self.min_notional_usd} minimum")
                return []
            
            # Calculate total requested size
            total_requested = sum(portfolio_value_usd * (p.size_pct / 100.0) for p in proposals)
            
            # If we have enough capital, no adjustment needed
            if total_requested <= available_capital:
                logger.info(f"Sufficient capital: ${total_requested:.2f} requested, ${available_capital:.2f} available")
                sized = []
                for p in proposals:
                    raw_size = portfolio_value_usd * (p.size_pct / 100.0)
                    if self.clamp_small_trades and raw_size < self.min_notional_usd:
                        logger.debug(
                            f"Clamping {p.symbol} raw size ${raw_size:.2f} → ${self.min_notional_usd:.2f} (min_notional)"
                        )
                        raw_size = self.min_notional_usd
                    if raw_size < self.min_notional_usd:
                        logger.debug(f"Skipping {p.symbol}: size ${raw_size:.2f} below minimum after clamp")
                        continue
                    sized.append((p, raw_size))
                return sized
            
            # Capital constrained - need to adjust
            logger.warning(f"Capital constrained: ${total_requested:.2f} requested > ${available_capital:.2f} available")
            
            # Sort proposals by confidence (highest first)
            sorted_proposals = sorted(proposals, key=lambda p: p.confidence, reverse=True)
            
            # Allocate capital proportionally, respecting minimums
            adjusted = []
            remaining_capital = available_capital
            
            for proposal in sorted_proposals:
                if remaining_capital < self.min_notional_usd:
                    logger.debug(f"Skipping {proposal.symbol}: insufficient remaining capital (${remaining_capital:.2f})")
                    break
                
                # Calculate proportional size
                requested_size = portfolio_value_usd * (proposal.size_pct / 100.0)
                if self.clamp_small_trades and requested_size < self.min_notional_usd:
                    logger.debug(
                        f"Clamping {proposal.symbol} requested size ${requested_size:.2f} → ${self.min_notional_usd:.2f}"
                    )
                    requested_size = self.min_notional_usd
                scale_factor = available_capital / total_requested
                adjusted_size = min(requested_size * scale_factor, remaining_capital)
                
                # Respect minimum notional
                if adjusted_size < self.min_notional_usd:
                    logger.debug(f"Skipping {proposal.symbol}: adjusted size ${adjusted_size:.2f} < ${self.min_notional_usd} minimum")
                    continue
                
                adjusted.append((proposal, adjusted_size))
                remaining_capital -= adjusted_size
                
                logger.info(f"Adjusted {proposal.symbol}: ${requested_size:.2f} → ${adjusted_size:.2f} (confidence={proposal.confidence:.2f})")
            
            return adjusted
            
        except CriticalDataUnavailable:
            raise
        except Exception as e:
            logger.error(f"Error adjusting proposals to capital: {e}")
            raise CriticalDataUnavailable("capital_adjustment", e) from e
    
    def get_liquidation_candidates(self, min_value_usd: float = 10.0, 
                                  sort_by: str = "performance") -> List[Dict]:
        """
        Identify holdings that could be liquidated for capital.
        
        Strategy:
        - By default, prioritize worst-performing assets (largest 24h loss)
        - Can also sort by lowest value for dust cleanup
        
        Args:
            min_value_usd: Only consider holdings worth more than this
            sort_by: "performance" (default) or "value"
            
        Returns:
            List of holdings with value and performance, sorted accordingly
        """
        try:
            accounts = self._require_accounts("liquidation_candidates")
            candidates = []
            
            for acc in accounts:
                currency = acc['currency']
                balance = float(acc.get('available_balance', {}).get('value', 0))
                account_uuid = acc.get('uuid', '')
                
                # Skip if balance too low or is a quote currency we prefer
                if balance == 0 or currency in self.preferred_quotes:
                    continue
                
                # Try to get USD value and performance
                try:
                    # Try direct USD pair first
                    pair = f"{currency}-USD"
                    quote = self.exchange.get_quote(pair)
                    value_usd = balance * quote.mid
                    
                    if value_usd >= min_value_usd:
                        # Get 24h performance
                        change_24h_pct = 0.0
                        try:
                            # Calculate from 24h volume and current price
                            # Note: This is approximate - actual historical data would be better
                            change_24h_pct = ((quote.last - quote.mid) / quote.mid) * 100 if quote.mid > 0 else 0.0
                        except:
                            pass
                        
                        candidates.append({
                            'currency': currency,
                            'account_uuid': account_uuid,
                            'balance': balance,
                            'value_usd': value_usd,
                            'price': quote.mid,
                            'pair': pair,
                            'change_24h_pct': change_24h_pct
                        })
                except:
                    # Try USDC pair as fallback
                    try:
                        pair = f"{currency}-USDC"
                        quote = self.exchange.get_quote(pair)
                        value_usd = balance * quote.mid
                        
                        if value_usd >= min_value_usd:
                            # Get 24h performance
                            change_24h_pct = 0.0
                            try:
                                change_24h_pct = ((quote.last - quote.mid) / quote.mid) * 100 if quote.mid > 0 else 0.0
                            except:
                                pass
                            
                            candidates.append({
                                'currency': currency,
                                'account_uuid': account_uuid,
                                'balance': balance,
                                'value_usd': value_usd,
                                'price': quote.mid,
                                'pair': pair,
                                'change_24h_pct': change_24h_pct
                            })
                    except:
                        pass
            
            # Sort based on strategy
            if sort_by == "performance":
                # Worst performers first (most negative change)
                candidates.sort(key=lambda x: x['change_24h_pct'])
            else:
                # Lowest value first (for dust cleanup)
                candidates.sort(key=lambda x: x['value_usd'])
            
            if candidates:
                total_value = sum(c['value_usd'] for c in candidates)
                logger.info(f"Found {len(candidates)} liquidation candidates worth ${total_value:.2f} (sorted by {sort_by})")
                if candidates:
                    worst = candidates[0]
                    logger.info(f"Top candidate: {worst['currency']} (${worst['value_usd']:.2f}, {worst['change_24h_pct']:+.2f}% 24h)")
            
            return candidates
            
        except CriticalDataUnavailable:
            raise
        except Exception as e:
            logger.error(f"Error finding liquidation candidates: {e}")
            raise CriticalDataUnavailable("accounts:liquidation_candidates", e) from e
    
    def _disable_convert_api(self, reason: str, status_code: Optional[int], pair: Tuple[str, str]) -> None:
        self._convert_api_disabled = True
        self._convert_api_disabled_at = datetime.now(timezone.utc)
        self._convert_api_last_error = reason
        logger.warning(
            "Convert API disabled%s for %s→%s: %s",
            f" (status={status_code})" if status_code is not None else "",
            pair[0],
            pair[1],
            reason,
        )

    def convert_asset(self, from_currency: str, to_currency: str, amount: str,
                     from_account_uuid: str, to_account_uuid: str) -> Dict:
        """
        Convert one crypto asset to another using Coinbase Convert API.
        
        Flow:
        1. Get quote for conversion
        2. Review quote (exchange rate, fees)
        3. Commit if acceptable
        
        Args:
            from_currency: Source currency (e.g., "PEPE")
            to_currency: Target currency (e.g., "USDC")
            amount: Amount in source currency
            from_account_uuid: Source account UUID
            to_account_uuid: Target account UUID
            
        Returns:
            Dict with success status and details
        """
        pair = (from_currency.upper(), to_currency.upper())

        available, retry_after = self._convert_api_available()
        if not available:
            logger.debug(
                "Convert API disabled; skipping convert %s→%s and falling back to spot routing",
                pair[0],
                pair[1],
            )
            return {
                'success': False,
                'error': 'convert_api_disabled',
                'from_currency': from_currency,
                'to_currency': to_currency,
                'retry_after_seconds': retry_after,
            }

        if pair in self._convert_denylist:
            logger.info(
                "Skipping convert %s→%s: cached as unsupported", pair[0], pair[1]
            )
            return {
                'success': False,
                'error': 'convert_pair_unsupported_cached',
                'from_currency': from_currency,
                'to_currency': to_currency,
            }

        try:
            logger.info(f"Converting {amount} {from_currency} → {to_currency}")

            quote_response = self.exchange.create_convert_quote(
                from_account=from_account_uuid,
                to_account=to_account_uuid,
                amount=amount,
            )
        except requests_exceptions.HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            error_text = exc.response.text.strip() if exc.response is not None else str(exc)
            lowered = error_text.lower()
            if status_code in {403, 404, 503} or "route is disabled" in lowered or "convert is disabled" in lowered:
                self._disable_convert_api(error_text, status_code, pair)
                retry_hint = float(self.convert_api_retry_seconds) if self.convert_api_retry_seconds > 0 else None
                return {
                    'success': False,
                    'error': 'convert_api_disabled',
                    'from_currency': from_currency,
                    'to_currency': to_currency,
                    'retry_after_seconds': retry_hint,
                }

            logger.warning(
                "Convert quote failed for %s→%s: %s",
                pair[0],
                pair[1],
                error_text,
            )
            self._convert_denylist.add(pair)
            self._convert_api_last_error = error_text
            return {'success': False, 'error': error_text}
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "Convert quote raised for %s→%s: %s", pair[0], pair[1], exc
            )
            self._convert_denylist.add(pair)
            self._convert_api_last_error = str(exc)
            return {'success': False, 'error': str(exc)}

        trade = quote_response.get('trade')
        if not isinstance(trade, dict):
            logger.warning(
                "Convert quote missing trade payload for %s→%s: %s",
                pair[0],
                pair[1],
                quote_response,
            )
            self._convert_denylist.add(pair)
            return {'success': False, 'error': 'Quote missing trade'}

        trade_id = trade.get('id')
        exchange_rate = trade.get('exchange_rate', {}).get('value', 0)
        total_fee = trade.get('total_fee', {}).get('amount', {}).get('value', 0)

        logger.info(
            "Quote received for %s→%s: rate=%s fee=%s trade_id=%s",
            pair[0],
            pair[1],
            exchange_rate,
            total_fee,
            trade_id,
        )

        try:
            commit_response = self.exchange.commit_convert_trade(
                trade_id=trade_id,
                from_account=from_account_uuid,
                to_account=to_account_uuid,
            )
        except requests_exceptions.HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            error_text = exc.response.text.strip() if exc.response is not None else str(exc)
            lowered = error_text.lower()
            if status_code in {403, 404, 503} or "route is disabled" in lowered or "convert is disabled" in lowered:
                self._disable_convert_api(error_text, status_code, pair)
                retry_hint = float(self.convert_api_retry_seconds) if self.convert_api_retry_seconds > 0 else None
                return {
                    'success': False,
                    'error': 'convert_api_disabled',
                    'trade_id': trade_id,
                    'from_currency': from_currency,
                    'to_currency': to_currency,
                    'retry_after_seconds': retry_hint,
                }

            logger.warning(
                "Convert commit failed for %s→%s: %s",
                pair[0],
                pair[1],
                error_text,
            )
            self._convert_denylist.add(pair)
            self._convert_api_last_error = error_text
            return {'success': False, 'error': error_text, 'trade_id': trade_id}
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "Convert commit raised for %s→%s: %s", pair[0], pair[1], exc
            )
            self._convert_denylist.add(pair)
            self._convert_api_last_error = str(exc)
            return {'success': False, 'error': str(exc), 'trade_id': trade_id}

        final_trade = commit_response.get('trade')
        if not isinstance(final_trade, dict):
            logger.warning(
                "Convert commit missing trade payload for %s→%s: %s",
                pair[0],
                pair[1],
                commit_response,
            )
            self._convert_denylist.add(pair)
            return {'success': False, 'error': 'Commit missing trade', 'trade_id': trade_id}

        status = final_trade.get('status', 'UNKNOWN')

        logger.info(f"✅ Conversion executed: {from_currency}→{to_currency}, status={status}")

        if self.state_store and from_currency:
            try:
                self.state_store.mark_position_managed(f"{from_currency}-USD")
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug(
                    "Failed to mark %s as managed after conversion: %s", from_currency, exc
                )

        self._convert_denylist.discard(pair)
        self._convert_api_disabled_at = None
        self._convert_api_last_error = None

        return {
            'success': True,
            'trade_id': trade_id,
            'status': status,
            'exchange_rate': exchange_rate,
            'fee': total_fee,
            'from_currency': from_currency,
            'to_currency': to_currency,
            'amount': amount
        }
    
    def _find_best_trading_pair(self, base_symbol: str, size_usd: float) -> Optional[Tuple[str, str, float]]:
        """
        Find the best trading pair based on available balance.
        
        Strategy:
        1. First try preferred quote currencies (USDC, USD, USDT, BTC, ETH)
        2. If none have sufficient balance, try ANY coin we hold
        3. This allows trading portfolio holdings against each other
        
        Args:
            base_symbol: Base asset (e.g., "HBAR", "XRP")
            size_usd: USD-equivalent size needed
            
        Returns:
            Tuple of (trading_pair, quote_currency, available_balance) or None
        """
        try:
            # Get FRESH account balances (critical - balance changes after each trade)
            accounts = self._require_accounts("find_best_pair")
            balances = {
                acc['currency']: float(acc.get('available_balance', {}).get('value', 0))
                for acc in accounts
            }
            
            logger.info(f"Looking for trading pair: {base_symbol} with ${size_usd:.2f} needed")
            logger.info(f"Current balances: {', '.join([f'{k}={v:.2f}' for k, v in balances.items() if v > 0])}")

            stable_currencies = {"USD", "USDC", "USDT"}
            total_stable = sum(balances.get(cur, 0.0) for cur in stable_currencies)
            
            # Track best option even if insufficient
            best_option = None
            best_balance_usd = 0
            
            # Build list of quote currencies to try:
            # 1. Preferred quotes first (USDC, USD, USDT, BTC, ETH)
            # 2. Then any coin we hold (for cross-pair trading)
            all_quote_candidates = list(self.preferred_quotes)
            
            # Add all holdings as potential quote currencies
            for currency in balances.keys():
                if currency not in all_quote_candidates and currency != base_symbol and balances[currency] > 0:
                    all_quote_candidates.append(currency)
            
            logger.debug(f"Trying {len(all_quote_candidates)} quote candidates: {', '.join(all_quote_candidates[:10])}...")
            
            # Try each quote currency
            for quote in all_quote_candidates:
                balance = balances.get(quote, 0)
                logger.debug(f"Trying quote {quote}: balance={balance:.6f}")
                if balance == 0:
                    logger.debug(f"  Skipping {quote}: zero balance")
                    continue
                
                # Convert balance to USD equivalent for comparison
                balance_usd = balance
                if quote in ['USD', 'USDC', 'USDT']:
                    # Stablecoins are 1:1 with USD
                    balance_usd = balance
                    logger.debug(f"  {quote} balance: {balance:.2f} = ${balance_usd:.2f} USD (stablecoin)")

                    if (
                        balance_usd + 1e-6 < size_usd
                        and total_stable >= size_usd
                        and self.mode == "LIVE"
                        and self.auto_convert_preferred_quote
                    ):
                        logger.info(
                            f"Attempting to top up {quote}: need ${size_usd:.2f}, have ${balance_usd:.2f}"
                        )
                        if self._top_up_stable_quote(quote, size_usd):
                            accounts = self._require_accounts("find_best_pair_refresh")
                            balances = {
                                acc['currency']: float(acc.get('available_balance', {}).get('value', 0))
                                for acc in accounts
                            }
                            balance = balances.get(quote, 0.0)
                            balance_usd = balance
                            total_stable = sum(balances.get(cur, 0.0) for cur in stable_currencies)
                            logger.info(
                                f"Top-up complete: {quote} balance now ${balance_usd:.2f}"
                            )
                        else:
                            logger.info(
                                f"Top-up for {quote} unavailable; proceeding with ${balance_usd:.2f}"
                            )
                else:
                    # Crypto holdings need USD conversion
                    try:
                        # Try direct USD pair first
                        quote_pair = f"{quote}-USD"
                        quote_obj = self.exchange.get_quote(quote_pair)
                        # Validate quote freshness
                        staleness_error = self._validate_quote_freshness(quote_obj, quote_pair)
                        if staleness_error:
                            logger.debug(f"  Skipping {quote_pair}: {staleness_error}")
                            continue
                        balance_usd = balance * quote_obj.mid
                        logger.debug(f"  {quote} balance: {balance:.6f} * ${quote_obj.mid:.2f} = ${balance_usd:.2f} USD")
                    except:
                        # Try USDC pair as fallback
                        try:
                            quote_pair = f"{quote}-USDC"
                            quote_obj = self.exchange.get_quote(quote_pair)
                            # Validate quote freshness
                            staleness_error = self._validate_quote_freshness(quote_obj, quote_pair)
                            if staleness_error:
                                logger.debug(f"  Skipping {quote_pair}: {staleness_error}")
                                continue
                            balance_usd = balance * quote_obj.mid
                            logger.debug(f"  {quote} balance: {balance:.6f} * ${quote_obj.mid:.2f} = ${balance_usd:.2f} USDC (≈USD)")
                        except Exception as e:
                            logger.debug(f"  Could not get USD value for {quote}: {e}")
                            continue
                
                # Check if trading pair exists
                pair = f"{base_symbol}-{quote}"
                try:
                    # Try to get a quote to verify pair exists
                    self.exchange.get_quote(pair)
                    
                    # Check if we have enough balance (prefer full balance, but track best option)
                    if balance_usd >= size_usd:
                        logger.info(f"✅ Selected trading pair: {pair} (balance: {balance:.6f} {quote} = ${balance_usd:.2f})")
                        return (pair, quote, balance)
                    elif balance_usd >= self.min_notional_usd and balance_usd > best_balance_usd:
                        # Track best partial option (above minimum)
                        best_option = (pair, quote, balance, balance_usd)
                        best_balance_usd = balance_usd
                        logger.debug(f"  {pair} is viable but insufficient (${balance_usd:.2f} < ${size_usd:.2f})")
                    else:
                        logger.debug(f"  {pair} balance ${balance_usd:.2f} below minimum ${self.min_notional_usd}")
                except Exception as e:
                    logger.warning(f"Pair {pair} not available or error: {type(e).__name__}: {str(e)[:100]}")
                    continue
            
            # If no quote has sufficient balance, use the best available (if above minimum)
            if best_option:
                pair, quote, balance, balance_usd = best_option
                logger.warning(f"Using best available: {pair} with ${balance_usd:.2f} (requested ${size_usd:.2f})")
                return (pair, quote, balance)
            
            # Last resort: suggest using Convert API for cross-pair trades
            # Find the largest holding that could be converted
            largest_holding = None
            largest_value = 0
            for currency, balance in balances.items():
                if currency == base_symbol or balance == 0:
                    continue
                # Try to get USD value
                try:
                    if currency in ['USD', 'USDC', 'USDT']:
                        value_usd = balance
                    else:
                        try:
                            pair = f"{currency}-USD"
                            quote_obj = self.exchange.get_quote(pair)
                            value_usd = balance * quote_obj.mid
                        except:
                            try:
                                pair = f"{currency}-USDC"
                                quote_obj = self.exchange.get_quote(pair)
                                value_usd = balance * quote_obj.mid
                            except:
                                continue
                    
                    if value_usd > largest_value and value_usd >= size_usd:
                        largest_holding = currency
                        largest_value = value_usd
                except:
                    continue
            
            if largest_holding:
                logger.info(f"💡 Suggestion: Convert {largest_holding} (${largest_value:.2f}) to USDC, then buy {base_symbol}")
                logger.info(f"   This requires implementing two-step conversion flow (Convert API + Buy)")
            
            logger.warning(f"No suitable trading pair found for {base_symbol} with size ${size_usd:.2f}")
            logger.warning(f"Available balances: {', '.join([f'{k}={v:.2f}' for k, v in balances.items() if v > 0])}")
            return None
            
        except CriticalDataUnavailable:
            raise
        except Exception as e:
            logger.error(f"Error finding trading pair: {e}")
            raise CriticalDataUnavailable("accounts:find_best_pair", e) from e
    
    def preview_order(self, symbol: str, side: str, size_usd: float, skip_liquidity_checks: bool = False) -> Dict:
        """
        Preview an order without placing it.
        
        Args:
            symbol: e.g. "BTC-USD"
            side: "BUY" or "SELL"
            size_usd: USD amount to trade
            
        Returns:
            Preview result with estimated fills, fees, slippage
        """
        if size_usd < self.min_notional_usd:
            return {
                "success": False,
                "error": f"Size ${size_usd:.2f} below minimum ${self.min_notional_usd}"
            }
        
        try:
            # Get quote for slippage estimate
            quote = self.exchange.get_quote(symbol)
            
            # Validate quote freshness
            staleness_error = self._validate_quote_freshness(quote, symbol)
            if staleness_error:
                return {
                    "success": False,
                    "error": staleness_error
                }

            if not skip_liquidity_checks:
                # Check spread
                if quote.spread_bps > self.max_spread_bps:
                    return {
                        "success": False,
                        "error": f"Spread {quote.spread_bps:.1f}bps exceeds max {self.max_spread_bps}bps"
                    }

                # Check orderbook depth (critical for LIVE mode)
                try:
                    orderbook = self.exchange.get_orderbook(symbol, depth_levels=20)

                    if side.upper() == "BUY":
                        depth_available_usd = orderbook.ask_depth_usd
                    else:
                        depth_available_usd = orderbook.bid_depth_usd

                    min_depth_required = size_usd * self.min_depth_multiplier
                    if depth_available_usd < min_depth_required:
                        return {
                            "success": False,
                            "error": f"Insufficient depth: ${depth_available_usd:.0f} < ${min_depth_required:.0f} required"
                        }
                    logger.debug(f"Depth check passed: ${depth_available_usd:.0f} available for ${size_usd:.0f} order")
                except Exception as e:
                    logger.warning(f"Depth check failed (continuing): {e}")
                    if self.mode == "LIVE":
                        return {
                            "success": False,
                            "error": f"Cannot verify orderbook depth: {e}"
                        }
            else:
                logger.debug(f"Skipping liquidity checks for {symbol} {side} purge/forced execution.")
            
            # Estimate fill
            if side.upper() == "BUY":
                estimated_price = quote.ask
            else:
                estimated_price = quote.bid
            
            estimated_size = size_usd / estimated_price
            
            # Use configured fee structure (default to maker fees for limit post-only)
            is_maker = self.limit_post_only
            estimated_fees = self.estimate_fee(size_usd, is_maker=is_maker)
            estimated_slippage_bps = quote.spread_bps / 2
            
            # If not DRY_RUN and auth available, call real preview API
            if self.mode != "DRY_RUN" and self.exchange.api_key:
                try:
                    api_preview = self.exchange.preview_order(symbol, side.lower(), size_usd)
                    # Parse API response if successful
                    if api_preview.get("success"):
                        # Update estimates from API
                        pass
                except Exception as e:
                    logger.warning(f"API preview failed, using estimates: {e}")
            
            return {
                "success": True,
                "symbol": symbol,
                "side": side,
                "size_usd": size_usd,
                "estimated_price": estimated_price,
                "estimated_size": estimated_size,
                "estimated_fees": estimated_fees,
                "estimated_slippage_bps": estimated_slippage_bps,
                "spread_bps": quote.spread_bps,
            }
            
        except Exception as e:
            logger.error(f"Preview failed for {symbol}: {e}")
            return {"success": False, "error": str(e)}
    
    def execute(self, symbol: str, side: str, size_usd: float,
                client_order_id: Optional[str] = None,
                max_slippage_bps: Optional[str] = None,
                force_order_type: Optional[str] = None,
                skip_liquidity_checks: bool = False,
                tier: Optional[int] = None,
                bypass_slippage_budget: bool = False,
                bypass_failed_order_cooldown: bool = False) -> ExecutionResult:
        """
        Execute a trade.
        
        Args:
            symbol: Base asset symbol or trading pair (e.g., "HBAR", "BTC-USD")
            side: "BUY" or "SELL"
            size_usd: USD-equivalent amount to trade
            client_order_id: Optional idempotency key
            max_slippage_bps: Optional slippage limit (overrides default)
            bypass_slippage_budget: Skip tier slippage+fee budget enforcement (for forced purges)
            bypass_failed_order_cooldown: Ignore recent failure cooldown gate (for safety purges)
            
        Returns:
            ExecutionResult with fill details
        """
        # Early validation: LIVE mode requires read_only=false
        if self.mode == "LIVE" and self.exchange.read_only:
            logger.error("LIVE mode execution attempted with read_only=true exchange")
            raise ValueError(
                "Cannot execute LIVE orders with read_only exchange. "
                "Set exchange.read_only=false in config/app.yaml to enable real trading."
            )
        
        # Extract base symbol if full pair provided (e.g., "BTC-USD" -> "BTC")
        base_symbol = symbol.split('-')[0] if '-' in symbol else symbol
        
        # Cooldown: skip if this symbol recently failed
        if not bypass_failed_order_cooldown and self.failed_order_cooldown_seconds > 0:
            now = datetime.now(timezone.utc).timestamp()
            last = self._last_fail.get(symbol.split('-')[0], 0)
            if last and (now - last) < self.failed_order_cooldown_seconds:
                return ExecutionResult(
                    success=False,
                    order_id=None,
                    symbol=symbol,
                    side=side,
                    filled_size=0.0,
                    filled_price=0.0,
                    fees=0.0,
                    slippage_bps=0.0,
                    route="skipped_cooldown",
                    error=f"Cooldown active for {symbol}"
                )

        # For BUY orders, find best trading pair based on available balance
        if side.upper() == "BUY" and self.mode in ("LIVE", "PAPER"):
            pair_info = self._find_best_trading_pair(base_symbol, size_usd)
            if pair_info:
                symbol = pair_info[0]  # Use the found trading pair
                quote_currency = pair_info[0].split('-')[1]  # Extract quote (USDC, USD, etc.)
                available_balance = pair_info[2]  # Raw balance in quote currency
                
                # Adjust size if available balance is less than requested (for stablecoins)
                if quote_currency in ['USD', 'USDC', 'USDT']:
                    available_balance_usd = available_balance
                    if available_balance_usd < size_usd:
                        logger.warning(f"Adjusting trade size: ${size_usd:.2f} → ${available_balance_usd:.2f} (limited by {quote_currency} balance)")
                        size_usd = max(self.min_notional_usd, available_balance_usd * 0.99)  # Use 99% to leave room for fees
                
                # If we ended up using a non-top preferred quote and auto-convert is enabled, 
                # try to acquire the preferred quote (e.g., convert USD → USDC) and re-select pair
                top_pref = self.preferred_quotes[0] if self.preferred_quotes else quote_currency
                if (
                    self.auto_convert_preferred_quote 
                    and quote_currency != top_pref 
                    and self.mode == "LIVE"
                ):
                    try:
                        if self._ensure_preferred_quote_liquidity(required_usd=size_usd, preferred_quote=top_pref):
                            logger.info(f"Acquired {top_pref} liquidity; re-selecting pair for {base_symbol}")
                            reselect = self._find_best_trading_pair(base_symbol, size_usd)
                            if reselect and reselect[0].split('-')[1] == top_pref:
                                symbol = reselect[0]
                                quote_currency = top_pref
                                available_balance = reselect[2]
                    except Exception as e:
                        logger.warning(f"Auto-convert to {top_pref} skipped/failed: {e}")

                logger.info(f"Using trading pair: {symbol} with ${size_usd:.2f}")
            else:
                # No direct pair found - try two-step conversion
                logger.warning(f"No direct trading pair found for {base_symbol}")
                logger.warning(f"Two-step conversion (holdings → USDC → {base_symbol}) not yet fully automated")
                logger.warning(f"For now, please liquidate holdings manually using examples/liquidate_worst_performers.py")
                return ExecutionResult(
                    success=False,
                    order_id=None,
                    symbol=symbol,
                    side=side,
                    filled_size=0.0,
                    filled_price=0.0,
                    fees=0.0,
                    slippage_bps=0.0,
                    route="failed",
                    error=f"No suitable trading pair found. Need to liquidate holdings to USDC first."
                )
        elif '-' not in symbol:
            # Default to USD if no pair specified and not buying
            symbol = f"{symbol}-USD"
        
        # Validate mode
        if self.mode == "DRY_RUN":
            logger.info(f"DRY_RUN: Would execute {side} ${size_usd:.2f} of {symbol}")
            # Generate deterministic ID for dry run tracking
            if not client_order_id:
                client_order_id = self.generate_client_order_id(symbol, side, size_usd)
            
            # Track order state even in DRY_RUN for monitoring
            self.order_state_machine.create_order(
                client_order_id=client_order_id,
                symbol=symbol,
                side=side,
                size_usd=size_usd,
                route="dry_run"
            )
            # Immediately transition to terminal state (not actually submitted)
            self.order_state_machine.transition(client_order_id, OrderStatus.FILLED)
            
            return ExecutionResult(
                success=True,
                order_id=f"dry_run_{client_order_id}",
                symbol=symbol,
                side=side,
                filled_size=0.0,
                filled_price=0.0,
                fees=0.0,
                slippage_bps=0.0,
                route="dry_run"
            )
        
        if self.mode == "PAPER":
            # Simulate execution with live quotes
            return self._execute_paper(symbol, side, size_usd, client_order_id)
        
        if self.mode == "LIVE":
            # Real execution
            return self._execute_live(
                symbol,
                side,
                size_usd,
                client_order_id,
                max_slippage_bps,
                force_order_type,
                skip_liquidity_checks,
                tier,
                bypass_slippage_budget,
            )
        
        raise ValueError(f"Invalid mode: {self.mode}")
    
    def _execute_paper(self, symbol: str, side: str, size_usd: float,
                      client_order_id: Optional[str]) -> ExecutionResult:
        """
        Simulate execution with live quotes (paper trading).
        """
        logger.info(f"PAPER: Simulating {side} ${size_usd:.2f} of {symbol}")
        
        # Generate deterministic ID if not provided
        if not client_order_id:
            client_order_id = self.generate_client_order_id(symbol, side, size_usd)
        
        # Create order state
        self.order_state_machine.create_order(
            client_order_id=client_order_id,
            symbol=symbol,
            side=side,
            size_usd=size_usd,
            route="paper"
        )
        
        try:
            # Get live quote
            quote = self.exchange.get_quote(symbol)
            
            # Transition to OPEN (simulated submission)
            self.order_state_machine.transition(
                client_order_id,
                OrderStatus.OPEN,
                order_id=f"paper_{client_order_id}"
            )
            
            # Simulate fill at ask (buy) or bid (sell)
            if side.upper() == "BUY":
                fill_price = quote.ask
            else:
                fill_price = quote.bid
            
            filled_size = size_usd / fill_price
            # Use configured fees (paper/dry-run uses maker fees)
            fees = self.estimate_fee(size_usd, is_maker=self.limit_post_only)
            slippage_bps = quote.spread_bps / 2
            
            # Update fill details and transition to FILLED
            self.order_state_machine.update_fill(
                client_order_id=client_order_id,
                filled_size=filled_size,
                filled_value=size_usd,
                fees=fees
            )
            
            return ExecutionResult(
                success=True,
                order_id=f"paper_{client_order_id}",
                symbol=symbol,
                side=side,
                filled_size=filled_size,
                filled_price=fill_price,
                fees=fees,
                slippage_bps=slippage_bps,
                route="paper_simulated"
            )
            
        except Exception as e:
            logger.error(f"Paper execution failed: {e}")
            # Transition to FAILED
            self.order_state_machine.transition(
                client_order_id,
                OrderStatus.FAILED,
                error=str(e)
            )
            return ExecutionResult(
                success=False,
                order_id=None,
                symbol=symbol,
                side=side,
                filled_size=0.0,
                filled_price=0.0,
                fees=0.0,
                slippage_bps=0.0,
                route="paper_simulated",
                error=str(e)
            )
    
    def _execute_live(self, symbol: str, side: str, size_usd: float,
                     client_order_id: Optional[str],
                     max_slippage_bps: Optional[float],
                     force_order_type: Optional[str] = None,
                     skip_liquidity_checks: bool = False,
                     tier: Optional[int] = None,
                     bypass_slippage_budget: bool = False) -> ExecutionResult:
        """
        Execute real order on Coinbase.
        """
        if self.exchange.read_only:
            raise ValueError("Cannot execute LIVE orders with read_only exchange")

        logger.warning(f"LIVE: Executing {side} ${size_usd:.2f} of {symbol}")

        generated_client_order_id = False
        if not client_order_id:
            base_client_order_id = self.generate_client_order_id(symbol, side, size_usd)
            generated_client_order_id = True
        else:
            base_client_order_id = client_order_id

        if (
            self.state_store
            and not generated_client_order_id
            and base_client_order_id
            and self.state_store.has_open_order(base_client_order_id)
        ):
            logger.warning(
                "Duplicate submission detected for client_order_id=%s; skipping execution",
                base_client_order_id,
            )
            return ExecutionResult(
                success=False,
                order_id=None,
                symbol=symbol,
                side=side,
                filled_size=0.0,
                filled_price=0.0,
                fees=0.0,
                slippage_bps=0.0,
                route="skipped_duplicate",
                error="duplicate_client_order",
            )

        plan = self._build_execution_plan(force_order_type, size_usd)
        if not plan:
            plan = [{"order_type": "market", "mode": "primary"}]

        active_client_order_id = base_client_order_id

        try:
            quote = None
            current_price = 0.0
            try:
                quote = self.exchange.get_quote(symbol)
                staleness_error = self._validate_quote_freshness(quote, symbol)
                if staleness_error:
                    logger.warning("Stale quote rejected in _execute_live: %s", staleness_error)
                    return ExecutionResult(
                        success=False,
                        order_id=None,
                        symbol=symbol,
                        side=side,
                        filled_size=0.0,
                        filled_price=0.0,
                        fees=0.0,
                        slippage_bps=0.0,
                        route="live_rejected",
                        error=staleness_error,
                    )
                current_price = quote.mid
            except Exception as quote_exc:
                logger.warning("Failed to get quote for %s: %s", symbol, quote_exc)
                current_price = 0.0

            first_is_maker = plan[0].get("order_type") == "limit_post_only"

            product_metadata: Optional[Dict[str, Any]] = None
            if current_price > 0:
                constraints = self.enforce_product_constraints(
                    symbol,
                    size_usd,
                    current_price,
                    is_maker=first_is_maker,
                )
                if not constraints.get("success"):
                    logger.warning("Product constraints failed for %s: %s", symbol, constraints.get("error"))
                    return ExecutionResult(
                        success=False,
                        order_id=None,
                        symbol=symbol,
                        side=side,
                        filled_size=0.0,
                        filled_price=0.0,
                        fees=0.0,
                        slippage_bps=0.0,
                        route="live_rejected",
                        error=constraints.get("error", "Product constraints failed"),
                    )

                adjusted_size = constraints.get("adjusted_size_usd", size_usd)
                if adjusted_size != size_usd:
                    fee_adj_note = " (fee-adjusted)" if constraints.get("fee_adjusted") else " (constraints)"
                    logger.info(
                        "Adjusted order size $%.4f → $%.4f for %s%s",
                        size_usd,
                        adjusted_size,
                        symbol,
                        fee_adj_note,
                    )
                    size_usd = adjusted_size

                product_metadata = constraints.get("metadata") or product_metadata
            else:
                try:
                    product_metadata = self.exchange.get_product_metadata(symbol)
                except Exception as metadata_exc:  # pragma: no cover - defensive
                    logger.debug("Failed to fetch product metadata for %s: %s", symbol, metadata_exc)

            plan = self._build_execution_plan(force_order_type, size_usd)
            if not plan:
                plan = [{"order_type": "market", "mode": "primary"}]

            preview = self.preview_order(
                symbol,
                side,
                size_usd,
                skip_liquidity_checks=skip_liquidity_checks,
            )
            if not preview.get("success"):
                return ExecutionResult(
                    success=False,
                    order_id=None,
                    symbol=symbol,
                    side=side,
                    filled_size=0.0,
                    filled_price=0.0,
                    fees=0.0,
                    slippage_bps=0.0,
                    route="live_market_ioc",
                    error=preview.get("error", "Preview failed"),
                )

            if isinstance(max_slippage_bps, str):
                try:
                    max_slippage_bps = float(max_slippage_bps)
                except (TypeError, ValueError):
                    max_slippage_bps = None

            max_slip = max_slippage_bps if max_slippage_bps is not None else self.max_slippage_bps
            est_slippage_bps = float(preview.get("estimated_slippage_bps", 0.0) or 0.0)
            if est_slippage_bps > max_slip:
                return ExecutionResult(
                    success=False,
                    order_id=None,
                    symbol=symbol,
                    side=side,
                    filled_size=0.0,
                    filled_price=0.0,
                    fees=0.0,
                    slippage_bps=est_slippage_bps,
                    route="live_market_ioc",
                    error=f"Slippage {est_slippage_bps:.1f}bps exceeds max {max_slip}bps",
                )

            maker_total_cost_bps = self.maker_fee_bps
            taker_total_cost_bps = est_slippage_bps + self.taker_fee_bps
            slippage_budget = self._get_slippage_budget(tier) if tier is not None else None

            if (
                plan
                and not bypass_slippage_budget
                and slippage_budget is not None
                and maker_total_cost_bps > slippage_budget
            ):
                maker_steps = [step for step in plan if step.get("order_type") == "limit_post_only"]
                if maker_steps:
                    logger.warning(
                        "Maker cost %.1fbps exceeds T%s budget %.1fbps; skipping maker attempts for %s",
                        maker_total_cost_bps,
                        tier if tier is not None else "?",
                        slippage_budget,
                        symbol,
                    )
                    plan = [step for step in plan if step.get("order_type") != "limit_post_only"]
                    if not plan:
                        return ExecutionResult(
                            success=False,
                            order_id=None,
                            symbol=symbol,
                            side=side,
                            filled_size=0.0,
                            filled_price=0.0,
                            fees=0.0,
                            slippage_bps=est_slippage_bps,
                            route="live_rejected_budget",
                            error=(
                                f"Maker cost {maker_total_cost_bps:.1f}bps exceeds T{tier} budget {slippage_budget:.1f}bps"
                                if tier is not None
                                else f"Maker cost {maker_total_cost_bps:.1f}bps exceeds slippage budget {slippage_budget:.1f}bps"
                            ),
                        )

            plan_summary = self._describe_execution_plan(plan)
            logger.info(
                "EXEC_PLAN %s %s usd=%.2f tier=%s plan=%s",
                symbol,
                side.upper(),
                size_usd,
                tier if tier is not None else "na",
                plan_summary,
            )

            attempt_index = 0
            last_result: Optional[ExecutionResult] = None

            for step_idx, step in enumerate(plan):
                order_type = step.get("order_type", "market")
                use_maker = order_type == "limit_post_only"
                mode = step.get("mode", "primary")
                attempt_label = step.get("attempt", attempt_index)
                attempt_client_order_id = (
                    base_client_order_id if attempt_index == 0 else f"{base_client_order_id}_r{attempt_index}"
                )
                attempt_index += 1
                active_client_order_id = attempt_client_order_id

                ttl_seconds = 0
                ttl_quote = quote if use_maker and attempt_index == 1 else None
                if use_maker:
                    if ttl_quote is None:
                        try:
                            ttl_quote = self.exchange.get_quote(symbol)
                        except Exception as ttl_exc:
                            ttl_quote = quote
                            logger.debug("Maker TTL quote fetch failed for %s: %s", symbol, ttl_exc)
                    ttl_seconds = self._adaptive_maker_ttl(
                        ttl_quote,
                        attempt_label if isinstance(attempt_label, int) else attempt_index - 1,
                    )
                    if ttl_quote is not None:
                        quote = ttl_quote
                else:
                    if not self._is_taker_slippage_allowed(est_slippage_bps, tier):
                        error_msg = (
                            f"Estimated slippage {est_slippage_bps:.1f}bps exceeds taker slippage budget"
                        )
                        logger.warning("Skipping taker route for %s: %s", symbol, error_msg)
                        return ExecutionResult(
                            success=False,
                            order_id=None,
                            symbol=symbol,
                            side=side,
                            filled_size=0.0,
                            filled_price=0.0,
                            fees=0.0,
                            slippage_bps=est_slippage_bps,
                            route="live_taker_blocked",
                            error=error_msg,
                        )
                    if (
                        not bypass_slippage_budget
                        and slippage_budget is not None
                        and taker_total_cost_bps > slippage_budget
                    ):
                        budget_msg = (
                            f"Total cost {taker_total_cost_bps:.1f}bps exceeds T{tier} budget {slippage_budget:.1f}bps"
                            if tier is not None
                            else f"Total cost {taker_total_cost_bps:.1f}bps exceeds slippage budget {slippage_budget:.1f}bps"
                        )
                        logger.warning("Skipping taker route for %s: %s", symbol, budget_msg)
                        return ExecutionResult(
                            success=False,
                            order_id=None,
                            symbol=symbol,
                            side=side,
                            filled_size=0.0,
                            filled_price=0.0,
                            fees=0.0,
                            slippage_bps=est_slippage_bps,
                            route="live_rejected_budget",
                            error=budget_msg,
                        )

                logger.info(
                    "EXEC_ATTEMPT %s %s attempt=%s type=%s client_id=%s ttl=%s mode=%s",
                    symbol,
                    side.upper(),
                    attempt_label,
                    order_type,
                    attempt_client_order_id,
                    ttl_seconds if use_maker else "na",
                    mode,
                )

                self.order_state_machine.create_order(
                    client_order_id=attempt_client_order_id,
                    symbol=symbol,
                    side=side,
                    size_usd=size_usd,
                    route=f"live_{order_type}",
                )

                # Post-only retry logic: handle INVALID_LIMIT_PRICE_POST_ONLY with wider cushion
                maker_cushion_ticks = 1  # Start with 1-tick cushion
                post_only_retry_count = 0
                max_post_only_retries = 1  # Allow 1 retry with wider cushion
                result = None
                
                while post_only_retry_count <= max_post_only_retries:
                    try:
                        result = self.exchange.place_order(
                            product_id=symbol,
                            side=side.lower(),
                            quote_size_usd=size_usd,
                            client_order_id=attempt_client_order_id,
                            order_type=order_type,
                            maker_cushion_ticks=maker_cushion_ticks if use_maker else 0,
                        )
                        
                        # Check for post-only error
                        if (
                            use_maker
                            and not result.get("success")
                            and result.get("error_response", {}).get("error") == "INVALID_LIMIT_PRICE_POST_ONLY"
                            and post_only_retry_count < max_post_only_retries
                        ):
                            # Retry with wider cushion
                            post_only_retry_count += 1
                            maker_cushion_ticks += 1
                            logger.warning(
                                "Post-only error on attempt %d for %s, retrying with %d-tick cushion",
                                post_only_retry_count,
                                symbol,
                                maker_cushion_ticks,
                            )
                            time.sleep(0.1)  # Small delay before retry
                            continue
                        else:
                            # Success or different error - exit retry loop
                            break
                    except Exception as place_exc:
                        logger.error("Order placement exception: %s", place_exc)
                        result = {"success": False, "error": str(place_exc)}
                        break
                
                # If post-only retries exhausted and still failing, attempt taker fallback
                if (
                    use_maker
                    and not result.get("success")
                    and result.get("error_response", {}).get("error") == "INVALID_LIMIT_PRICE_POST_ONLY"
                ):
                    logger.warning(
                        "Post-only retries exhausted for %s, attempting IOC taker fallback within slippage budget",
                        symbol,
                    )
                    # Clear pending marker for failed post-only
                    self._clear_pending_marker(symbol, side, attempt_client_order_id)
                    
                    # Check if taker is allowed by slippage budget
                    if self._is_taker_slippage_allowed(est_slippage_bps, tier):
                        taker_total_cost = est_slippage_bps + self.taker_fee_bps
                        slippage_budget = self._get_slippage_budget(tier) if tier is not None else None
                        
                        if slippage_budget is None or taker_total_cost <= slippage_budget:
                            logger.info(
                                "Taker fallback: placing IOC order for %s @ %.1f bps total cost (slippage %.1f + fee %.1f)",
                                symbol,
                                taker_total_cost,
                                est_slippage_bps,
                                self.taker_fee_bps,
                            )
                            # Place IOC taker order
                            result = self.exchange.place_order(
                                product_id=symbol,
                                side=side.lower(),
                                quote_size_usd=size_usd,
                                client_order_id=attempt_client_order_id,
                                order_type="market",
                            )
                            order_type = "market"  # Update for logging
                            use_maker = False
                        else:
                            logger.warning(
                                "Taker fallback blocked: total cost %.1f bps exceeds budget %.1f bps",
                                taker_total_cost,
                                slippage_budget,
                            )
                    else:
                        logger.warning("Taker fallback blocked: slippage %.1f bps not allowed", est_slippage_bps)

                route = f"live_{order_type}"
                logger.info("Order response: %s", result)

                order_id = result.get("order_id") or result.get("success_response", {}).get("order_id")
                status = (
                    result.get("status")
                    or result.get("success_response", {}).get("status")
                    or "open"
                )

                if not order_id and not result.get("success"):
                    error_msg = f"Order placement failed: {result.get('error', 'Unknown error')}"
                    self.order_state_machine.transition(
                        attempt_client_order_id,
                        OrderStatus.REJECTED,
                        rejection_reason=error_msg,
                    )
                    raise ValueError(error_msg)

                self.order_state_machine.transition(
                    attempt_client_order_id,
                    OrderStatus.OPEN,
                    order_id=order_id,
                )

                fills = result.get("fills") or []
                filled_size, filled_price, fees, filled_value = self._summarize_fills(fills, product_metadata)

                if not fills and order_type == "market" and order_id:
                    logger.info("Market order placed, polling for status then fills: %s", order_id)
                    try:
                        max_attempts = 10
                        poll_interval = 0.5
                        terminal_states = {"FILLED", "CANCELLED", "EXPIRED", "FAILED"}
                        for attempt in range(max_attempts):
                            time.sleep(poll_interval)
                            order_status = self.exchange.get_order_status(order_id)
                            if not order_status:
                                logger.warning("Attempt %d: No order status for %s", attempt + 1, order_id)
                                continue
                            status = order_status.get("status", "UNKNOWN")
                            filled_size_so_far = float(order_status.get("filled_size", 0))
                            logger.debug(
                                "Attempt %d: order %s status=%s filled_size=%.6f",
                                attempt + 1,
                                order_id,
                                status,
                                filled_size_so_far,
                            )
                            if status in terminal_states:
                                logger.info("Order %s reached terminal state: %s", order_id, status)
                                break
                        time.sleep(0.2)
                        fills = self.exchange.list_fills(order_id=order_id) or []
                        logger.info("Retrieved %d fills for order %s", len(fills), order_id)
                    except Exception as poll_err:
                        logger.warning("Failed to poll status/fills for %s: %s", order_id, poll_err)
                        fills = []
                    filled_size, filled_price, fees, filled_value = self._summarize_fills(fills, product_metadata)

                ttl_canceled = False
                ttl_error: Optional[str] = None

                if use_maker and ttl_seconds > 0:
                    ttl_result = self._handle_post_only_ttl(
                        order_id=order_id,
                        client_order_id=attempt_client_order_id,
                        symbol=symbol,
                        side=side,
                        current_status=status,
                        initial_fills=fills,
                        filled_size=filled_size,
                        filled_price=filled_price,
                        fees=fees,
                        ttl_seconds=ttl_seconds,
                    )

                    if ttl_result.triggered:
                        if ttl_result.status:
                            status = ttl_result.status
                        if ttl_result.fills is not None:
                            fills = ttl_result.fills
                            filled_size, filled_price, fees, filled_value = self._summarize_fills(fills, product_metadata)
                        if ttl_result.filled_size is not None:
                            filled_size = ttl_result.filled_size
                        if ttl_result.filled_price is not None:
                            filled_price = ttl_result.filled_price
                        if ttl_result.fees is not None:
                            fees = ttl_result.fees
                        if filled_size and filled_price:
                            filled_value = filled_size * filled_price
                        if ttl_result.filled_value is not None:
                            filled_value = ttl_result.filled_value
                        if ttl_result.fills is not None and ttl_result.filled_size is None:
                            _, _, _, ttl_quote_value = self._summarize_fills(ttl_result.fills, product_metadata)
                            filled_value = ttl_quote_value

                        ttl_canceled = ttl_result.canceled
                        ttl_error = ttl_result.error

                        if ttl_result.canceled:
                            result["ttl_cancelled"] = True
                            if filled_size > 0:
                                route = "live_limit_post_only_ttl_partial"
                            else:
                                route = "live_limit_post_only_timeout"
                        elif ttl_result.error:
                            result["ttl_warning"] = ttl_result.error

                if filled_size == 0.0 and filled_price == 0.0 and not ttl_canceled:
                    if preview.get("expected_fill_price"):
                        filled_price = preview["expected_fill_price"]
                        filled_size = size_usd / filled_price if filled_price > 0 else 0.0
                        filled_value = filled_size * filled_price
                        logger.warning(
                            "No fill data for %s; using preview estimate %.6f @ $%.6f",
                            order_id,
                            filled_size,
                            filled_price,
                        )

                if filled_size > 0:
                    if filled_value == 0 and filled_price > 0:
                        filled_value = filled_size * filled_price
                    self.order_state_machine.update_fill(
                        client_order_id=attempt_client_order_id,
                        filled_size=filled_size,
                        filled_value=filled_value,
                        fees=fees,
                        fills=fills,
                    )

                actual_slippage = est_slippage_bps

                self._update_state_store_after_execution(
                    symbol=symbol,
                    side=side,
                    size_usd=size_usd,
                    client_order_id=attempt_client_order_id,
                    order_id=order_id,
                    status=status,
                    route=route,
                    result_payload=result,
                    fills=fills,
                    filled_size=filled_size,
                    filled_price=filled_price,
                    fees=fees,
                    filled_value=filled_value,
                )

                success_flag = True
                error_message = None
                if use_maker and ttl_canceled and filled_size == 0.0:
                    success_flag = False
                    error_message = ttl_error or f"Post-only order canceled after {ttl_seconds}s TTL without fill"

                if mode == "fallback" and route.startswith("live_market"):
                    route = f"{route}_fallback"

                exec_result = ExecutionResult(
                    success=success_flag,
                    order_id=order_id,
                    symbol=symbol,
                    side=side,
                    filled_size=filled_size,
                    filled_price=filled_price,
                    fees=fees,
                    slippage_bps=actual_slippage,
                    route=route,
                    error=error_message,
                )

                logger.info(
                    "EXEC_RESULT %s %s success=%s route=%s filled=%.6f error=%s",
                    symbol,
                    side.upper(),
                    exec_result.success,
                    exec_result.route,
                    exec_result.filled_size,
                    exec_result.error,
                )

                if exec_result.success:
                    return exec_result

                last_result = exec_result

                if use_maker and self._should_retry_maker(exec_result):
                    continue

            if last_result is not None:
                return last_result

            return ExecutionResult(
                success=False,
                order_id=None,
                symbol=symbol,
                side=side,
                filled_size=0.0,
                filled_price=0.0,
                fees=0.0,
                slippage_bps=est_slippage_bps,
                route="live_no_plan",
                error="Execution plan exhausted without fill",
            )

        except Exception as e:
            logger.error(f"Live execution failed: {e}")
            try:
                self.order_state_machine.transition(
                    active_client_order_id,
                    OrderStatus.FAILED,
                    error=str(e),
                )
            except Exception:
                pass
            try:
                base_sym = symbol.split('-')[0] if '-' in symbol else symbol
                self._last_fail[base_sym] = datetime.now(timezone.utc).timestamp()
            except Exception:
                pass
            fallback_type = force_order_type or ("limit_post_only" if self.limit_post_only else "market")
            return ExecutionResult(
                success=False,
                order_id=None,
                symbol=symbol,
                side=side,
                filled_size=0.0,
                filled_price=0.0,
                fees=0.0,
                slippage_bps=0.0,
                route=f"live_{fallback_type}",
                error=str(e),
            )

    def _ensure_preferred_quote_liquidity(self, required_usd: float, preferred_quote: str = "USDC") -> bool:
        """
        Ensure we have at least required_usd in the preferred quote currency by doing a quick
        market conversion from USD if available.

        Currently supports USD → USDC via buying USDC-USD.

        Returns True if preferred liquidity is available or was acquired; False otherwise.
        """
        try:
            if self.mode != "LIVE":
                return False

            return self._top_up_stable_quote(preferred_quote, required_usd)
        except CriticalDataUnavailable:
            raise
        except Exception as e:
            logger.warning(f"Auto-convert to {preferred_quote} failed: {e}")
            return False

    def _top_up_stable_quote(self, target_quote: str, required_usd: float) -> bool:
        """Attempt to ensure target stable balance meets the required USD size."""
        stable_currencies = {"USD", "USDC", "USDT"}
        if target_quote not in stable_currencies or required_usd <= 0:
            return False

        try:
            accounts = self._require_accounts(f"top_up:{target_quote}")
            balances = {
                acc['currency']: float(acc.get('available_balance', {}).get('value', 0))
                for acc in accounts
            }

            current = balances.get(target_quote, 0.0)
            if current >= required_usd:
                return True

            deficit = required_usd - current
            donors = [c for c in stable_currencies if c != target_quote and balances.get(c, 0.0) > 0]
            donors.sort(key=lambda cur: balances.get(cur, 0.0), reverse=True)

            for donor in donors:
                available = balances.get(donor, 0.0)
                if available <= 0:
                    continue

                transfer = min(available, max(deficit * 1.05, self.min_notional_usd))
                if transfer < self.min_notional_usd:
                    continue

                logger.info(
                    f"Top-up: attempting convert {donor} → {target_quote} (~${transfer:.2f})"
                )

                if not self.exchange.convert_currency(donor, target_quote, transfer):
                    logger.debug("Convert %s → %s skipped or failed", donor, target_quote)
                    continue

                try:
                    accounts = self._require_accounts(f"top_up_refresh:{target_quote}")
                    balances = {
                        acc['currency']: float(acc.get('available_balance', {}).get('value', 0))
                        for acc in accounts
                    }
                except Exception as refresh_exc:
                    logger.warning("Failed to refresh balances after convert: %s", refresh_exc)
                    return True

                current = balances.get(target_quote, 0.0)
                deficit = required_usd - current
                if current >= required_usd:
                    return True

            return balances.get(target_quote, 0.0) >= required_usd
        except CriticalDataUnavailable:
            raise
        except Exception as exc:
            logger.warning(f"Top-up for {target_quote} failed: {exc}")
            raise CriticalDataUnavailable(f"accounts:top_up:{target_quote}", exc) from exc

    # ===== State store integration =====

    @staticmethod
    def _safe_float(value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _summarize_fills(
        fills: Optional[List[Dict[str, Any]]],
        product_metadata: Optional[Dict[str, Any]] = None,
    ) -> Tuple[float, float, float, float]:
        """Aggregate fill data into size, average price, total fees, and quote notional."""

        if not fills:
            return 0.0, 0.0, 0.0, 0.0

        def _as_decimal(value: Any) -> Optional[Decimal]:
            if value is None:
                return None
            try:
                dec = Decimal(str(value))
            except (InvalidOperation, TypeError, ValueError):
                return None
            return dec

        def _first_decimal(payload: Dict[str, Any], keys: Tuple[str, ...]) -> Optional[Decimal]:
            for key in keys:
                dec = _as_decimal(payload.get(key))
                if dec is not None:
                    return dec
            return None

        def _quantize(value: Decimal, increment: Optional[Decimal]) -> Decimal:
            if increment is None or increment <= 0:
                return value
            try:
                return value.quantize(increment, rounding=ROUND_DOWN)
            except (InvalidOperation, ValueError):
                return value

        base_increment = _as_decimal(product_metadata.get("base_increment")) if product_metadata else None
        price_increment = None
        quote_increment = None
        if product_metadata:
            price_increment = _as_decimal(
                product_metadata.get("price_increment") or product_metadata.get("quote_increment")
            )
            quote_increment = _as_decimal(product_metadata.get("quote_increment"))

        mismatch_tolerance = base_increment if base_increment and base_increment > 0 else Decimal("0.00000001")

        total_base = Decimal("0")
        total_quote = Decimal("0")
        total_fees = Decimal("0")
        reported_quote_total = Decimal("0")

        for fill in fills:
            price = _first_decimal(fill, ("price", "average_price"))
            if price is None or price <= 0:
                continue

            base_size = _first_decimal(fill, ("size", "base_size", "filled_size"))
            quote_size = _first_decimal(fill, ("size_in_quote", "quote_size", "filled_value"))
            fee = _first_decimal(fill, ("commission", "fee", "fee_amount")) or Decimal("0")

            effective_base = base_size
            effective_quote = None

            if quote_size is not None and quote_size > 0:
                implied_base = quote_size / price
                if effective_base is None or effective_base <= 0:
                    effective_base = implied_base
                else:
                    diff = abs(effective_base - implied_base)
                    if diff > mismatch_tolerance:
                        effective_base = implied_base
                effective_quote = quote_size
                reported_quote_total += quote_size

            if effective_base is None or effective_base <= 0:
                continue

            if effective_quote is None:
                effective_quote = price * effective_base

            total_base += effective_base
            total_quote += effective_quote
            total_fees += fee

        if total_base <= 0:
            return 0.0, 0.0, float(total_fees), float(total_quote)

        avg_price = total_quote / total_base

        # Quantize results using product metadata (if available)
        quantized_base = _quantize(total_base, base_increment)
        if quantized_base > 0:
            total_base = quantized_base
        # Ensure we don't lose fills if quantization zeroed a small amount

        avg_price = _quantize(avg_price, price_increment) if price_increment else avg_price
        total_quote = _quantize(total_quote, quote_increment) if quote_increment else total_quote

        if reported_quote_total > 0:
            mismatch = abs(total_quote - reported_quote_total)
            tolerance = max(Decimal("0.01"), reported_quote_total * Decimal("0.005"))
            if mismatch > tolerance:
                logger.warning(
                    "FILL_UNITS_MISMATCH base_total=%s price=%s quote_cost=%s reported_quote=%s",
                    total_base,
                    avg_price,
                    total_quote,
                    reported_quote_total,
                )

        return float(total_base), float(avg_price), float(total_fees), float(total_quote)

    def _map_exchange_status(self, status: Optional[str]) -> Optional[OrderStatus]:
        if not status:
            return None
        normalized = status.upper()
        if normalized in {"FILLED", "DONE", "DONEFORTHEDAY"}:
            return OrderStatus.FILLED
        if normalized in {"CANCELED", "CANCELLED", "EXPIRED", "CANCELLEDALL", "CANCELLED_PARTIAL"}:
            return OrderStatus.CANCELED
        if normalized in {"REJECTED"}:
            return OrderStatus.REJECTED
        if normalized in {"FAILED", "ERROR"}:
            return OrderStatus.FAILED
        if normalized in {"OPEN", "ACTIVE", "PENDING"}:
            return OrderStatus.OPEN
        if normalized in {"PARTIAL", "PARTIALLY_FILLED"}:
            return OrderStatus.PARTIAL_FILL
        return None

    def _clear_pending_marker(
        self,
        symbol: Optional[str],
        side: Optional[str],
        *,
        client_order_id: Optional[str] = None,
        order_id: Optional[str] = None,
    ) -> None:
        if not self.state_store or not symbol or not side:
            return
        try:
            self.state_store.clear_pending(
                symbol,
                side,
                client_order_id=client_order_id,
                order_id=order_id,
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("Pending marker clear failed for %s: %s", symbol, exc)

    def _safe_cancel(
        self,
        order_id: Optional[str],
        *,
        product_id: Optional[str] = None,
        side: Optional[str] = None,
        client_order_id: Optional[str] = None,
    ) -> bool:
        if not order_id:
            return False

        try:
            response = self.exchange.cancel_order(order_id)
        except Exception as exc:
            message = str(exc).lower()
            if "404" in message or "not found" in message:
                logger.info("Cancel order %s returned not-found; assuming closed", order_id)
                self._clear_pending_marker(product_id, side, client_order_id=client_order_id, order_id=order_id)
                return True
            logger.warning("Cancel order %s failed: %s", order_id, exc)
            return False

        if response.get("success"):
            self._clear_pending_marker(product_id, side, client_order_id=client_order_id, order_id=order_id)
            return True

        error_text = (response.get("error") or "").lower()
        if "not_found" in error_text or "404" in error_text:
            logger.info("Cancel order %s treated as already closed (payload=%s)", order_id, response)
            self._clear_pending_marker(product_id, side, client_order_id=client_order_id, order_id=order_id)
            return True

        return False

    def _resolve_and_finalize_missing_order(self, tracked: OrderState) -> None:
        if tracked is None or tracked.is_terminal():
            return

        order_id = tracked.order_id
        client_id = tracked.client_order_id
        status_payload: Optional[Dict[str, Any]] = None
        fills: List[Dict[str, Any]] = []

        if order_id:
            try:
                status_payload = self.exchange.get_order_status(order_id)
            except Exception as exc:
                logger.debug("Order status fetch failed for %s: %s", order_id, exc)

        resolved_status = self._map_exchange_status(
            (status_payload or {}).get("status")
        )

        if not resolved_status and tracked.status:
            resolved_status = self._map_exchange_status(tracked.status)

        if resolved_status is None or resolved_status in {OrderStatus.OPEN, OrderStatus.PARTIAL_FILL}:
            # Treat missing order as cancelled if we cannot confirm active status
            resolved_status = OrderStatus.CANCELED

        if status_payload:
            fills = status_payload.get("fills") or []

        if not fills and order_id:
            try:
                fills = self.exchange.list_fills(order_id=order_id)
            except Exception as exc:
                logger.debug("Fill fetch failed during reconciliation for %s: %s", order_id, exc)

        filled_size = filled_value = fees = 0.0
        filled_price = 0.0
        if fills:
            filled_size, filled_price, fees, filled_value = self._summarize_fills(fills)

        key = self._order_key(client_id, order_id)
        if key and self.state_store and resolved_status in {
            OrderStatus.FILLED,
            OrderStatus.CANCELED,
            OrderStatus.EXPIRED,
            OrderStatus.REJECTED,
            OrderStatus.FAILED,
        }:
            details = {
                "order_id": order_id,
                "client_order_id": client_id,
                "product_id": tracked.symbol,
                "symbol": tracked.symbol,
                "side": tracked.side,
                "filled_size": filled_size,
                "filled_value": filled_value,
                "fees": fees,
                "status": resolved_status.value,
            }
            self._close_order_in_state_store(key, resolved_status.value, details)

        if client_id:
            try:
                self.order_state_machine.transition(
                    client_id,
                    resolved_status,
                    order_id=order_id,
                    allow_override=True,
                )
            except Exception as exc:
                logger.debug("Order transition override failed for %s: %s", client_id, exc)

            if filled_size > 0:
                try:
                    self.order_state_machine.update_fill(
                        client_id,
                        filled_size=filled_size,
                        filled_value=filled_value,
                        fees=fees,
                        fills=fills if fills else None,
                    )
                except Exception as exc:
                    logger.debug("Fill update failed during reconciliation for %s: %s", client_id, exc)

        self._clear_pending_marker(
            tracked.symbol,
            tracked.side,
            client_order_id=client_id,
            order_id=order_id,
        )

    def reconcile_open_orders(self) -> None:
        """Ensure local order state matches exchange by reconciling missing orders."""

        live_identifiers: Set[str] = set()
        remote_orders: List[Dict[str, Any]] = []

        try:
            remote_orders = self.exchange.list_open_orders()
        except Exception as exc:
            logger.debug("Open order fetch failed during reconciliation: %s", exc)

        for remote in remote_orders or []:
            order_id = remote.get("order_id") or remote.get("id")
            client_id = remote.get("client_order_id") or remote.get("client_order_id_v2")
            if order_id:
                live_identifiers.add(order_id)
            if client_id:
                live_identifiers.add(client_id)

        if self.state_store:
            try:
                self.sync_open_orders_snapshot(remote_orders)
            except Exception as exc:
                logger.debug("Open order snapshot sync failed: %s", exc)
            
            # Backfill pending markers from open orders
            try:
                self._backfill_pending_markers(remote_orders)
            except Exception as exc:
                logger.debug("Pending marker backfill failed: %s", exc)
            
            self.state_store.purge_expired_pending()

        # Reconcile tracked orders
        active_orders = self.order_state_machine.get_active_orders()
        for tracked in active_orders:
            key = tracked.client_order_id or tracked.order_id
            if key and key in live_identifiers:
                continue
            self._resolve_and_finalize_missing_order(tracked)

    @staticmethod
    def _order_key(client_order_id: Optional[str], order_id: Optional[str]) -> Optional[str]:
        if client_order_id:
            return client_order_id
        if order_id:
            return order_id
        return None

    @classmethod
    def build_state_store_order_payload(cls, order: Dict[str, Any]) -> Optional[Tuple[str, Dict[str, Any]]]:
        order_id = order.get("order_id") or order.get("id")
        client_id = order.get("client_order_id") or order.get("client_order_id_v2")
        key = cls._order_key(client_id, order_id)
        if not key:
            return None

        status = (order.get("status") or "open").lower()
        product_id = order.get("product_id") or order.get("symbol")

        quote_size = cls._safe_float(
            order.get("quote_size")
            or order.get("quote_value")
            or order.get("notional")
            or order.get("filled_value")
        )
        base_size = cls._safe_float(order.get("base_size") or order.get("size") or order.get("filled_size"))

        config = order.get("order_configuration") or {}
        if config:
            limit_conf = (
                config.get("limit_limit_gtc")
                or config.get("limit_limit_gtc_post_only")
                or config.get("limit_limit_gtd")
            )
            market_conf = config.get("market_market_ioc")
            if limit_conf:
                base_conf = cls._safe_float(limit_conf.get("base_size"))
                price_conf = cls._safe_float(limit_conf.get("limit_price"))
                if base_conf:
                    base_size = max(base_size, base_conf)
                if base_conf and price_conf:
                    quote_size = max(quote_size, base_conf * price_conf)
            elif market_conf:
                quote_conf = cls._safe_float(market_conf.get("quote_size"))
                base_conf = cls._safe_float(market_conf.get("base_size"))
                if quote_conf:
                    quote_size = max(quote_size, quote_conf)
                if base_conf:
                    base_size = max(base_size, base_conf)

        payload: Dict[str, Any] = {
            "order_id": order_id,
            "client_order_id": client_id,
            "product_id": product_id,
            "side": (order.get("side") or "").lower(),
            "status": status,
            "quote_size_usd": quote_size,
            "base_size": base_size,
            "filled_size": cls._safe_float(order.get("filled_size")),
            "filled_value": cls._safe_float(order.get("filled_value")),
        }

        created_time = order.get("created_time") or order.get("submitted_at")
        if created_time:
            payload["created_time"] = created_time

        return key, payload

    def sync_open_orders_snapshot(self, orders: List[Dict[str, Any]]) -> None:
        if not self.state_store:
            return
        try:
            snapshot: Dict[str, Dict[str, Any]] = {}
            for order in orders:
                built = self.build_state_store_order_payload(order)
                if not built:
                    continue
                key, data = built
                snapshot[key] = data
            timestamp = datetime.now(timezone.utc)
            self.state_store.sync_open_orders(snapshot, timestamp)
        except Exception as exc:
            logger.warning("Failed to sync open orders into state store: %s", exc)

    def _close_order_in_state_store(
        self,
        key: Optional[str],
        status: str,
        details: Dict[str, Any],
    ) -> None:
        if not self.state_store:
            return

        candidates = []
        if details.get("client_order_id"):
            candidates.append(details["client_order_id"])
        if key and key not in candidates:
            candidates.append(key)
        if details.get("order_id"):
            oid = details["order_id"]
            if oid not in candidates:
                candidates.append(oid)

        for candidate in candidates:
            try:
                closed, _ = self.state_store.close_order(candidate, status=status, details=details)
                if closed:
                    return
            except Exception as exc:
                logger.warning("State store close_order failed for %s: %s", candidate, exc)

    def _backfill_pending_markers(self, remote_orders: List[Dict[str, Any]]) -> None:
        """Create pending markers from existing open orders during reconciliation."""
        if not self.state_store:
            return
        
        for order in remote_orders:
            product_id = order.get("product_id") or order.get("symbol")
            side = (order.get("side") or "").upper()
            
            if not product_id or side != "BUY":
                continue
            
            # Check if marker already exists
            if self.state_store.has_pending(product_id, side):
                continue
            
            order_id = order.get("order_id") or order.get("id")
            client_id = order.get("client_order_id") or order.get("client_order_id_v2")
            
            # Estimate notional
            config = order.get("order_configuration") or {}
            limit_conf = config.get("limit_limit_gtc") or config.get("limit_limit_gtc_post_only")
            market_conf = config.get("market_market_ioc")
            
            notional = 0.0
            if limit_conf:
                try:
                    base_size = float(limit_conf.get("base_size") or 0.0)
                    limit_price = float(limit_conf.get("limit_price") or 0.0)
                    if base_size > 0 and limit_price > 0:
                        notional = base_size * limit_price
                except (TypeError, ValueError):
                    pass
            elif market_conf:
                try:
                    notional = float(market_conf.get("quote_size") or 0.0)
                except (TypeError, ValueError):
                    pass
            
            if notional <= 0:
                notional = self.min_notional_usd
            
            # Set pending marker with TTL
            try:
                ttl_hint = max(self.post_only_ttl_seconds * 2, 180) if self.post_only_ttl_seconds else 180
                self.state_store.set_pending(
                    product_id,
                    side,
                    client_order_id=client_id,
                    order_id=order_id,
                    notional_usd=notional,
                    ttl_seconds=ttl_hint,
                )
                logger.debug(f"Backfilled pending marker for {product_id} BUY (${notional:.2f}, ttl={ttl_hint}s)")
            except Exception as exc:
                logger.debug("Pending marker backfill failed for %s: %s", product_id, exc)

    def _handle_post_only_ttl(
        self,
        *,
        order_id: Optional[str],
        client_order_id: Optional[str],
        symbol: str,
        side: str,
        current_status: str,
        initial_fills: Optional[List[Dict[str, Any]]],
        filled_size: float,
        filled_price: float,
        fees: float,
        ttl_seconds: int,
    ) -> PostOnlyTTLResult:
        """Poll order status and cancel if maker order exceeds TTL."""

        if ttl_seconds <= 0 or not order_id:
            return PostOnlyTTLResult(triggered=False)

        status_upper = (current_status or "").upper()
        terminal_states = {
            "FILLED",
            "COMPLETED",
            "DONE",
            "CANCELED",
            "CANCELLED",
            "EXPIRED",
            "FAILED",
            "REJECTED",
        }

        if filled_size > 0 or status_upper in terminal_states:
            return PostOnlyTTLResult(triggered=False)

        poll_interval = max(0.2, min(ttl_seconds / 5.0, 1.0))
        start = time.monotonic()
        last_error: Optional[str] = None
        latest_status = status_upper or "OPEN"
        latest_fills = initial_fills or []
        latest_size = filled_size
        latest_price = filled_price
        latest_fees = fees

        while (time.monotonic() - start) < ttl_seconds:
            try:
                snapshot = self.exchange.get_order_status(order_id)
            except Exception as exc:  # pragma: no cover - defensive
                last_error = str(exc)
                logger.debug("TTL poll failed for %s: %s", order_id, exc)
                break

            if snapshot:
                latest_status = (snapshot.get("status") or latest_status or "OPEN").upper()
                try:
                    latest_size = float(snapshot.get("filled_size", latest_size) or latest_size)
                except (TypeError, ValueError):
                    latest_size = latest_size

                if latest_status in terminal_states or latest_size > 0:
                    fills = self.exchange.list_fills(order_id=order_id) or latest_fills
                    size, price, total_fees, total_quote = self._summarize_fills(fills)
                    return PostOnlyTTLResult(
                        triggered=True,
                        canceled=False,
                        status=latest_status,
                        fills=fills,
                        filled_size=size,
                        filled_price=price,
                        filled_value=total_quote,
                        fees=total_fees,
                    )

            time.sleep(poll_interval)

        # TTL expired without terminal state; cancel to avoid resting risk
        backoffs = list(self.cancel_retry_backoff_ms) if self.cancel_retry_backoff_ms else [250, 500, 1000]
        attempts = len(backoffs) + 1
        cancel_error: Optional[str] = None
        snapshot_post_cancel: Optional[Dict[str, Any]] = None

        for attempt in range(attempts):
            cancel_success = self._safe_cancel(
                order_id,
                product_id=symbol,
                side=side,
                client_order_id=client_order_id,
            )
            cancel_error = None if cancel_success else "cancel_failed"
            already_resolved = cancel_success

            snapshot_post_cancel = self.exchange.get_order_status(order_id) or snapshot_post_cancel
            status_after = (snapshot_post_cancel or {}).get("status", latest_status).upper()
            raw_filled_after = (snapshot_post_cancel or {}).get("filled_size")
            filled_after: Optional[Decimal] = None
            try:
                if raw_filled_after is not None:
                    filled_after = Decimal(str(raw_filled_after))
                elif latest_size:
                    filled_after = Decimal(str(latest_size))
            except (InvalidOperation, TypeError):
                filled_after = None

            if cancel_success:
                status_after = "CANCELED" if status_after not in terminal_states else status_after
                logger.info(
                    "Post-only TTL expired for %s (%s %s); canceled after %ss (attempt=%d)",
                    order_id,
                    side,
                    symbol,
                    ttl_seconds,
                    attempt + 1,
                )
                if client_order_id:
                    try:
                        self.order_state_machine.transition(
                            client_order_id,
                            OrderStatus.CANCELED,
                            error="post_only_ttl_expired",
                        )
                    except Exception as exc:  # pragma: no cover - defensive
                        logger.debug("Order state transition failed after TTL cancel: %s", exc)

                fills = self.exchange.list_fills(order_id=order_id) or latest_fills
                size, price, total_fees, total_quote = self._summarize_fills(fills)
                return PostOnlyTTLResult(
                    triggered=True,
                    canceled=True,
                    status=status_after,
                    fills=fills,
                    filled_size=size,
                    filled_price=price,
                    filled_value=total_quote,
                    fees=total_fees,
                )

            # Exchange may report FINISHED despite cancel failure. Accept fills.
            has_fill_after = bool(filled_after and filled_after > 0)

            if already_resolved and status_after not in terminal_states and not has_fill_after:
                status_after = "CANCELLED"

            if status_after in terminal_states or has_fill_after or already_resolved:
                fills = self.exchange.list_fills(order_id=order_id) or latest_fills
                size, price, total_fees, total_quote = self._summarize_fills(fills)
                if size and size > 0 and status_after in {"CANCELED", "CANCELLED", "EXPIRED"}:
                    status_after = "FILLED"

                if (
                    already_resolved
                    and status_after in {"CANCELED", "CANCELLED", "EXPIRED"}
                    and client_order_id
                ):
                    try:
                        self.order_state_machine.transition(
                            client_order_id,
                            OrderStatus.CANCELED,
                            error="post_only_ttl_cancel_not_found",
                        )
                    except Exception as exc:  # pragma: no cover - defensive
                        logger.debug("Order state transition failed after 404 cancel: %s", exc)

                return PostOnlyTTLResult(
                    triggered=True,
                    canceled=status_after in {"CANCELED", "CANCELLED", "EXPIRED"},
                    status=status_after,
                    fills=fills,
                    filled_size=size if size else filled_after,
                    filled_price=price,
                    filled_value=total_quote if size else (filled_after * price if price else None),
                    fees=total_fees,
                )

            if attempt < len(backoffs):
                time.sleep(max(backoffs[attempt] / 1000.0, 0.05))

        logger.warning(
            "Failed to cancel post-only order %s after TTL expiry (last_error=%s)",
            order_id,
            cancel_error,
        )
        return PostOnlyTTLResult(
            triggered=True,
            canceled=False,
            status=(snapshot_post_cancel or {}).get("status", latest_status),
            fills=latest_fills,
            filled_size=latest_size,
            filled_price=latest_price,
            filled_value=(latest_size * latest_price if latest_size and latest_price else None),
            fees=latest_fees,
            error=f"ttl_cancel_failed:{cancel_error or last_error or 'unknown'}",
        )

    def _update_state_store_after_execution(
        self,
        *,
        symbol: str,
        side: str,
        size_usd: float,
        client_order_id: Optional[str],
        order_id: Optional[str],
        status: str,
        route: str,
        result_payload: Dict[str, Any],
        fills: List[Dict[str, Any]],
        filled_size: float,
        filled_price: float,
        fees: float,
        filled_value: float,
    ) -> None:
        if not self.state_store:
            return

        try:
            key = self._order_key(client_order_id, order_id)
            if not key:
                return

            status_lower = (status or "open").lower()
            payload: Dict[str, Any] = {
                "order_id": order_id,
                "client_order_id": client_order_id,
                "product_id": symbol,
                "symbol": symbol,
                "side": side.lower(),
                "quote_size_usd": size_usd,
                "order_value_usd": size_usd,
                "status": status_lower,
                "route": route,
                "filled_size": filled_size,
                "filled_price": filled_price,
                "filled_value": filled_value,
                "fees": fees,
                "fills": fills,
                "result_snapshot": result_payload,
            }

            if filled_value and size_usd:
                mismatch = abs(filled_value - size_usd)
                tolerance = max(0.01, size_usd * 0.005)
                if mismatch > tolerance:
                    logger.error(
                        "FILL_NOTIONAL_MISMATCH product=%s requested=%.6f filled=%.6f tolerance=%.4f payload=%s",
                        symbol,
                        size_usd,
                        filled_value,
                        tolerance,
                        fills,
                    )

            if result_payload and result_payload.get("ttl_cancelled"):
                payload["ttl_cancelled"] = True
            if result_payload and result_payload.get("ttl_warning"):
                payload["ttl_warning"] = result_payload.get("ttl_warning")

            terminal_statuses = {
                "done",
                "filled",
                "canceled",
                "cancelled",
                "expired",
                "rejected",
                "failed",
                "error",
            }

            if status_lower in terminal_statuses:
                self._close_order_in_state_store(key, status_lower, payload)
                self._clear_pending_marker(
                    symbol,
                    side,
                    client_order_id=client_order_id,
                    order_id=order_id,
                )
            else:
                self.state_store.record_open_order(key, payload)
                try:
                    ttl_hint = max(self.post_only_ttl_seconds * 2, 180) if self.post_only_ttl_seconds else 180
                    self.state_store.set_pending(
                        symbol,
                        side,
                        client_order_id=client_order_id,
                        order_id=order_id,
                        notional_usd=size_usd,
                        ttl_seconds=ttl_hint,
                    )
                except Exception as exc:  # pragma: no cover - defensive
                    logger.debug("Pending marker set failed for %s: %s", symbol, exc)
        except Exception as exc:
            logger.warning("State store update failed after execution: %s", exc)
    
    def execute_batch(self, orders: List[Dict]) -> List[ExecutionResult]:
        """
        Execute multiple orders sequentially.
        
        Args:
            orders: List of order dicts with keys: symbol, side, size_usd
            
        Returns:
            List of ExecutionResults
        """
        results = []
        for order in orders:
            result = self.execute(
                symbol=order["symbol"],
                side=order["side"],
                size_usd=order["size_usd"],
                client_order_id=order.get("client_order_id"),
                max_slippage_bps=order.get("max_slippage_bps")
            )
            results.append(result)
            
            # Stop on first failure if critical
            if not result.success and order.get("critical", False):
                logger.warning("Critical order failed, stopping batch execution")
                break
        
        return results

    # ===== Fill reconciliation =====
    def reconcile_fills(self, lookback_minutes: int = 60) -> Dict[str, Any]:
        """
        Poll fills from exchange and reconcile with order states and positions.
        
        Strategy:
        1. Fetch recent fills from exchange (lookback window)
        2. Match fills to tracked orders in OrderStateMachine
        3. Update fill details (filled_size, fees, prices)
        4. Transition orders to appropriate states (PARTIAL_FILL, FILLED)
        5. Update StateStore with fill details
        6. Return reconciliation summary
        
        Args:
            lookback_minutes: How far back to query fills (default: 60 minutes)
            
        Returns:
            Dict with:
                - fills_processed: int
                - orders_updated: int
                - total_fees: float
                - fills_by_symbol: Dict[str, int]
                - unmatched_fills: List[dict] (fills with no tracked order)
        """
        if self.mode == "DRY_RUN":
            return {"fills_processed": 0, "orders_updated": 0, "total_fees": 0.0}
        
        try:
            # Calculate lookback time
            start_time = datetime.now(timezone.utc) - timedelta(minutes=lookback_minutes)
            
            # Fetch fills from exchange
            fills = self.exchange.list_fills(limit=1000, start_time=start_time)
            
            if not fills:
                logger.debug("No fills to reconcile")
                return {"fills_processed": 0, "orders_updated": 0, "total_fees": 0.0}
            
            logger.info(f"Reconciling {len(fills)} fills from last {lookback_minutes} minutes")
            
            # Track reconciliation stats
            fills_processed = 0
            orders_updated = set()
            total_fees = 0.0
            fills_by_symbol = {}
            unmatched_fills = []
            
            for fill in fills:
                fills_processed += 1
                
                # Extract fill details
                order_id = fill.get("order_id")
                product_id = fill.get("product_id", "")
                side = (fill.get("side", "") or "").upper()
                trade_time = fill.get("trade_time", "")

                # Extract robust fill metrics (handles quote-sized market orders)
                base_size, avg_price, fees_value, quote_value = self._summarize_fills([fill])

                raw_size = fill.get("size")
                if base_size <= 0 and raw_size is not None:
                    try:
                        base_size = float(raw_size)
                    except (TypeError, ValueError):
                        base_size = 0.0

                raw_price = fill.get("price") or fill.get("average_price")
                if avg_price <= 0 and raw_price is not None:
                    try:
                        avg_price = float(raw_price)
                    except (TypeError, ValueError):
                        avg_price = 0.0

                raw_quote = fill.get("size_in_quote") or fill.get("quote_size") or fill.get("filled_value")
                if quote_value <= 0 and raw_quote is not None:
                    try:
                        quote_value = float(raw_quote)
                    except (TypeError, ValueError):
                        quote_value = 0.0

                if base_size > 0 and avg_price <= 0 and quote_value > 0:
                    avg_price = quote_value / base_size

                if quote_value <= 0 and base_size > 0 and avg_price > 0:
                    quote_value = base_size * avg_price

                if fees_value <= 0:
                    try:
                        fees_value = float(fill.get("commission", 0.0) or 0.0)
                    except (TypeError, ValueError):
                        fees_value = 0.0

                # Track fees
                total_fees += fees_value
                
                # Track fills by symbol
                fills_by_symbol[product_id] = fills_by_symbol.get(product_id, 0) + 1
                
                # Track position and calculate PnL
                if (
                    self.state_store
                    and hasattr(self.state_store, "record_fill")
                    and callable(self.state_store.record_fill)
                    and product_id
                    and side
                ):
                    try:
                        # Parse timestamp
                        fill_timestamp = datetime.now(timezone.utc)
                        if trade_time:
                            try:
                                fill_timestamp = datetime.fromisoformat(trade_time.replace('Z', '+00:00'))
                            except (ValueError, AttributeError):
                                pass

                        if base_size > 0 and avg_price > 0:
                            self.state_store.record_fill(
                                symbol=product_id,
                                side=side,
                                filled_size=base_size,
                                fill_price=avg_price,
                                fees=fees_value,
                                timestamp=fill_timestamp,
                                notional_usd=quote_value,
                            )
                            logger.debug(
                                "Recorded fill for PnL: %s %s base=%.8f price=%.8f notional=%.8f",
                                product_id,
                                side,
                                base_size,
                                avg_price,
                                quote_value,
                            )
                        else:
                            logger.debug(
                                "Skipping fill record for %s due to non-positive base/price (base=%.8f, price=%.8f)",
                                product_id,
                                base_size,
                                avg_price,
                            )
                    except Exception as e:
                        logger.debug(f"Could not record fill for PnL tracking: {e}")
                        # Continue processing other fills
                
                # Find matching order in state machine
                # We need to search by order_id since fills use exchange order_id
                matching_order = None
                for client_id, order_state in self.order_state_machine.orders.items():
                    if order_state.order_id == order_id:
                        matching_order = (client_id, order_state)
                        break
                
                if not matching_order:
                    logger.debug(f"No tracked order for fill: {order_id} ({product_id})")
                    unmatched_fills.append(fill)
                    continue
                
                client_id, order_state = matching_order
                orders_updated.add(client_id)
                
                # Calculate fill value from extracted metrics
                fill_value = base_size * avg_price if base_size > 0 and avg_price > 0 else 0.0
                
                # Update order state with fill details
                current_filled = order_state.filled_size or 0.0
                current_filled_value = order_state.filled_value or 0.0
                current_fees = order_state.fees or 0.0
                
                new_filled_size = current_filled + base_size
                new_filled_value = current_filled_value + fill_value
                new_fees = current_fees + fees_value
                
                # Store fill in order state
                if not hasattr(order_state, 'fills') or order_state.fills is None:
                    order_state.fills = []
                order_state.fills.append(fill)
                
                # Update fill totals
                self.order_state_machine.update_fill(
                    client_order_id=client_id,
                    filled_size=new_filled_size,
                    filled_value=new_filled_value,
                    fees=new_fees,
                    fills=order_state.fills
                )
                
                # Determine if order is fully filled
                # For now, we'll transition to FILLED when we see a fill (most are instant fills)
                # In a more sophisticated system, we'd check order_state.size_usd vs filled_value
                if order_state.status not in [OrderStatus.FILLED.value, OrderStatus.PARTIAL_FILL.value]:
                    # Check if this is a partial or complete fill
                    if order_state.size_usd and new_filled_value < order_state.size_usd * 0.95:
                        # Less than 95% filled = partial
                        self.order_state_machine.transition(
                            client_id,
                            OrderStatus.PARTIAL_FILL
                        )
                        logger.debug(f"Order {client_id} partially filled: ${new_filled_value:.2f}/${order_state.size_usd:.2f}")
                    else:
                        # 95%+ filled = complete
                        self.order_state_machine.transition(
                            client_id,
                            OrderStatus.FILLED
                        )
                        logger.info(f"Order {client_id} fully filled: ${new_filled_value:.2f}")
                        
                        # Close in state store
                        if self.state_store:
                            self._close_order_in_state_store(
                                client_id,
                                "filled",
                                {
                                    "order_id": order_id,
                                    "client_order_id": client_id,
                                    "product_id": product_id,
                                    "filled_size": new_filled_size,
                                    "filled_value": new_filled_value,
                                    "fees": new_fees,
                                    "fills": order_state.fills,
                                    "trade_time": trade_time
                                }
                            )
            
            # Get PnL metrics
            realized_pnl_usd = 0.0
            open_positions = 0
            if self.state_store and hasattr(self.state_store, 'load') and callable(self.state_store.load):
                try:
                    state = self.state_store.load()
                    realized_pnl_usd = float(state.get("pnl_today", 0.0))
                    open_positions = len(state.get("positions", {}))
                except Exception as e:
                    logger.debug(f"Could not read PnL from state: {e}")
            
            summary = {
                "fills_processed": fills_processed,
                "orders_updated": len(orders_updated),
                "total_fees": total_fees,
                "fills_by_symbol": fills_by_symbol,
                "unmatched_fills": len(unmatched_fills),
                "realized_pnl_usd": realized_pnl_usd,
                "open_positions": open_positions
            }
            
            if fills_processed > 0:
                logger.info(
                    f"Fill reconciliation complete: {fills_processed} fills processed, "
                    f"{len(orders_updated)} orders updated, ${total_fees:.4f} total fees, "
                    f"${realized_pnl_usd:.2f} daily PnL, {open_positions} open positions"
                )
            
            return summary
            
        except Exception as e:
            logger.error(f"Fill reconciliation failed: {e}", exc_info=True)
            return {
                "fills_processed": 0,
                "orders_updated": 0,
                "total_fees": 0.0,
                "error": str(e)
            }

    # ===== Open order management =====
    def manage_open_orders(self) -> None:
        """
        Cancel stale open limit orders based on policy timings.
        
        Uses OrderStateMachine to track order age and transitions canceled
        orders to CANCELED state for proper lifecycle tracking.
        
        Strategy:
        1. Get stale orders from OrderStateMachine (reliable age tracking)
        2. Cancel via exchange API
        3. Transition to CANCELED state
        4. Update StateStore
        """
        try:
            execution_config = self.policy.get("execution", {})
            cancel_after = int(execution_config.get("cancel_after_seconds", 60))
            
            if self.mode == "DRY_RUN":
                return
            
            if cancel_after <= 0:
                logger.debug("Order cancellation disabled (cancel_after_seconds <= 0)")
                return
            
            # Use OrderStateMachine for reliable stale order detection
            stale_orders = self.order_state_machine.get_stale_orders(max_age_seconds=cancel_after)
            
            if not stale_orders:
                logger.debug("No stale orders to cancel")
                return
            
            logger.info(f"Found {len(stale_orders)} stale orders (age >= {cancel_after}s)")
            
            # Group by exchange order ID for cancellation
            to_cancel = []
            order_id_to_client_id = {}
            
            for order in stale_orders:
                # Skip if already in terminal state (edge case)
                if order.is_terminal():
                    continue
                
                order_id = order.order_id
                client_order_id = order.client_order_id
                
                if order_id:
                    to_cancel.append(order_id)
                    order_id_to_client_id[order_id] = client_order_id
                    logger.info(
                        f"Marking for cancellation: {order.symbol} {order.side} "
                        f"(age: {order.age_seconds():.1f}s, client_id: {client_order_id})"
                    )
                else:
                    logger.warning(
                        f"Stale order {client_order_id} has no exchange order_id, "
                        "transitioning to CANCELED anyway"
                    )
                    # Transition to CANCELED even without exchange order_id
                    self.order_state_machine.transition(
                        client_order_id,
                        OrderStatus.CANCELED,
                        error="No exchange order_id available"
                    )
                    # Close in state store
                    if self.state_store and client_order_id:
                        self._close_order_in_state_store(
                            client_order_id,
                            "canceled",
                            {"reason": "stale_no_order_id"}
                        )
            
            # Execute cancellations
            if to_cancel:
                canceled_count = 0
                failed_count = 0
                
                # Try batch cancel first (more efficient)
                if len(to_cancel) > 1:
                    try:
                        result = self.exchange.cancel_orders(to_cancel)
                        if result.get("success", False) or "results" in result:
                            logger.info(f"Batch canceled {len(to_cancel)} stale orders")
                            canceled_count = len(to_cancel)
                        else:
                            # Batch failed, fall back to individual
                            logger.warning(f"Batch cancel failed: {result.get('error')}, trying individual")
                            raise Exception("Batch cancel failed")
                    except Exception as batch_exc:
                        logger.debug(f"Batch cancel exception: {batch_exc}, falling back to individual")
                        # Fall through to individual cancellation
                        for order_id in to_cancel:
                            try:
                                self.exchange.cancel_order(order_id)
                                canceled_count += 1
                                logger.debug(f"Canceled order {order_id}")
                            except Exception as cancel_exc:
                                failed_count += 1
                                logger.warning(f"Failed to cancel order {order_id}: {cancel_exc}")
                                # Transition to CANCELED anyway (order may already be gone)
                                client_id = order_id_to_client_id.get(order_id)
                                if client_id:
                                    self.order_state_machine.transition(
                                        client_id,
                                        OrderStatus.CANCELED,
                                        error=f"Cancel failed: {cancel_exc}"
                                    )
                else:
                    # Single order cancellation
                    order_id = to_cancel[0]
                    try:
                        self.exchange.cancel_order(order_id)
                        canceled_count = 1
                        logger.info(f"Canceled stale order {order_id}")
                    except Exception as cancel_exc:
                        failed_count = 1
                        logger.warning(f"Failed to cancel order {order_id}: {cancel_exc}")
                        # Transition to CANCELED anyway
                        client_id = order_id_to_client_id.get(order_id)
                        if client_id:
                            self.order_state_machine.transition(
                                client_id,
                                OrderStatus.CANCELED,
                                error=f"Cancel failed: {cancel_exc}"
                            )
                
                # Transition successfully canceled orders to CANCELED state
                for order_id, client_id in order_id_to_client_id.items():
                    if client_id:
                        # Check if already transitioned due to failure
                        order_state = self.order_state_machine.get_order(client_id)
                        if order_state and order_state.status != OrderStatus.CANCELED.value:
                            self.order_state_machine.transition(
                                client_id,
                                OrderStatus.CANCELED
                            )
                        
                        # Close in state store
                        if self.state_store:
                            self._close_order_in_state_store(
                                client_id,
                                "canceled",
                                {"reason": "stale_order_timeout", "age_seconds": cancel_after}
                            )
                
                logger.info(
                    f"Stale order cancellation complete: "
                    f"{canceled_count} canceled, {failed_count} failed"
                )
                
                # Refresh open orders from exchange to sync state
                try:
                    remaining = self.exchange.list_open_orders()
                    self.sync_open_orders_snapshot(remaining)
                    logger.debug(f"Synced {len(remaining)} remaining open orders")
                except Exception as refresh_exc:
                    logger.warning(f"Failed to refresh open orders after cancel: {refresh_exc}")
            
        except Exception as e:
            logger.warning(f"manage_open_orders failed: {e}", exc_info=True)


# Singleton instance
_executor = None


def get_executor(
    mode: str = "DRY_RUN",
    policy: Optional[Dict] = None,
    exchange: Optional[CoinbaseExchange] = None,
    state_store: Optional[StateStore] = None,
) -> ExecutionEngine:
    """Get singleton executor instance"""
    global _executor
    if _executor is None or _executor.mode != mode.upper():
        _executor = ExecutionEngine(
            mode=mode,
            policy=policy,
            exchange=exchange,
            state_store=state_store,
        )
    else:
        if exchange and _executor.exchange is not exchange:
            _executor.exchange = exchange
        if policy is not None:
            _executor.policy = policy
        if state_store and _executor.state_store is not state_store:
            _executor.state_store = state_store
    return _executor
