"""
247trader-v2 Core: Cost Model

Centralized fee and slippage calculations for both backtest simulation and live PnL attribution.
Single source of truth for trading costs.

Pattern: Freqtrade-style cost modeling with tier-based spread estimation.
"""

from dataclasses import dataclass
from typing import Optional, Literal
import logging

logger = logging.getLogger(__name__)


@dataclass
class TradeCost:
    """Trade cost breakdown"""
    fee_usd: float  # Total fee paid
    fee_pct: float  # Fee as percentage of trade size
    slippage_usd: float  # Slippage cost
    slippage_bps: float  # Slippage in basis points
    total_cost_usd: float  # Fee + slippage
    total_cost_pct: float  # Total as percentage
    is_maker: bool  # True if maker, False if taker


@dataclass
class CostConfig:
    """Cost model configuration"""
    maker_fee_pct: float = 0.004  # 40 bps (0.4%)
    taker_fee_pct: float = 0.006  # 60 bps (0.6%)

    # Tier-based spread assumptions (half-spread for slippage estimation)
    tier1_spread_bps: float = 10.0  # 10 bps = $100 on BTC at $100k
    tier2_spread_bps: float = 20.0  # 20 bps
    tier3_spread_bps: float = 40.0  # 40 bps

    # Slippage multipliers
    market_order_slippage_multiplier: float = 0.5  # Cross half spread for market orders
    aggressive_limit_slippage_multiplier: float = 0.25  # Aggressive limits cross 1/4 spread

    # Partial fill assumptions
    post_only_fill_rate: float = 0.85  # 85% of post_only orders fill


class CostModel:
    """
    Centralized cost model for trading fees and slippage.

    Used by:
    - BacktestEngine: Simulate realistic costs
    - Live PnL attribution: Decompose realized costs
    - Risk/Sizing: Account for costs in position sizing

    Cost components:
    1. Fees: Maker (40 bps) vs Taker (60 bps)
    2. Slippage: Based on spread, order type, and tier
    """

    def __init__(self, config: Optional[CostConfig] = None):
        self.config = config or CostConfig()

    def calculate_trade_cost(
        self,
        size_usd: float,
        is_maker: bool,
        tier: int = 2,
        spread_bps: Optional[float] = None,
        order_type: Literal["market", "limit_post_only", "limit_aggressive"] = "limit_post_only"
    ) -> TradeCost:
        """
        Calculate total cost for a trade.

        Args:
            size_usd: Trade size in USD
            is_maker: True if maker order (filled passively)
            tier: Universe tier (1=highest liquidity, 3=lowest)
            spread_bps: Observed spread in bps (overrides tier default)
            order_type: Order type for slippage estimation

        Returns:
            TradeCost with breakdown
        """
        # 1. Fee calculation
        fee_pct = self.config.maker_fee_pct if is_maker else self.config.taker_fee_pct
        fee_usd = size_usd * fee_pct

        # 2. Slippage estimation
        if spread_bps is None:
            # Use tier-based defaults
            if tier == 1:
                spread_bps = self.config.tier1_spread_bps
            elif tier == 2:
                spread_bps = self.config.tier2_spread_bps
            else:
                spread_bps = self.config.tier3_spread_bps

        # Slippage depends on order type
        if order_type == "market":
            # Market orders cross the spread immediately
            slippage_bps = spread_bps * self.config.market_order_slippage_multiplier
        elif order_type == "limit_aggressive":
            # Aggressive limits cross part of the spread
            slippage_bps = spread_bps * self.config.aggressive_limit_slippage_multiplier
        else:  # limit_post_only
            # Post-only orders provide liquidity, minimal slippage
            # But account for price improvement uncertainty
            slippage_bps = spread_bps * 0.1  # 10% of spread

        slippage_usd = size_usd * (slippage_bps / 10000.0)

        # 3. Total cost
        total_cost_usd = fee_usd + slippage_usd
        total_cost_pct = (total_cost_usd / size_usd) if size_usd > 0 else 0.0

        return TradeCost(
            fee_usd=fee_usd,
            fee_pct=fee_pct,
            slippage_usd=slippage_usd,
            slippage_bps=slippage_bps,
            total_cost_usd=total_cost_usd,
            total_cost_pct=total_cost_pct,
            is_maker=is_maker
        )

    def calculate_min_profitable_move(
        self,
        is_maker: bool,
        tier: int = 2,
        spread_bps: Optional[float] = None,
        round_trip: bool = True
    ) -> float:
        """
        Calculate minimum price move (in %) needed to break even after costs.

        Args:
            is_maker: Assume maker fills
            tier: Universe tier
            spread_bps: Observed spread (overrides tier)
            round_trip: If True, account for both entry and exit costs

        Returns:
            Minimum profitable move as percentage (e.g., 0.012 = 1.2%)
        """
        # Get cost for a $1000 trade (size doesn't matter for percentage)
        cost = self.calculate_trade_cost(
            size_usd=1000.0,
            is_maker=is_maker,
            tier=tier,
            spread_bps=spread_bps,
            order_type="limit_post_only"
        )

        # Round trip doubles the cost (entry + exit)
        multiplier = 2.0 if round_trip else 1.0

        return cost.total_cost_pct * multiplier

    def adjust_size_for_fees(
        self,
        target_size_usd: float,
        is_maker: bool,
        ensure_post_fee_minimum: float
    ) -> float:
        """
        Adjust position size to ensure post-fee amount meets minimum.

        Used by ExecutionEngine to round up sizing when fees would push
        the trade below exchange minimums.

        Args:
            target_size_usd: Desired trade size
            is_maker: Assume maker fees
            ensure_post_fee_minimum: Minimum post-fee amount required

        Returns:
            Adjusted size (may be higher than target)
        """
        fee_pct = self.config.maker_fee_pct if is_maker else self.config.taker_fee_pct

        # Post-fee amount = size - (size * fee_pct) = size * (1 - fee_pct)
        post_fee_amount = target_size_usd * (1.0 - fee_pct)

        if post_fee_amount >= ensure_post_fee_minimum:
            return target_size_usd

        # Need to round up: solve for size where (size * (1 - fee_pct)) >= minimum
        adjusted_size = ensure_post_fee_minimum / (1.0 - fee_pct)

        logger.debug(
            f"Adjusted size from ${target_size_usd:.2f} to ${adjusted_size:.2f} "
            f"to ensure ${ensure_post_fee_minimum:.2f} post-fee minimum"
        )

        return adjusted_size

    def estimate_fill_probability(
        self,
        order_type: Literal["market", "limit_post_only", "limit_aggressive"],
        tier: int = 2
    ) -> float:
        """
        Estimate probability of order filling based on type and tier.

        Used by backtest to simulate partial fills and no-fills.

        Returns:
            Probability from 0.0 to 1.0
        """
        if order_type == "market":
            # Market orders almost always fill (unless liquidity crash)
            return 0.98

        if order_type == "limit_aggressive":
            # Aggressive limits have high fill rate
            if tier == 1:
                return 0.95
            elif tier == 2:
                return 0.90
            else:
                return 0.80

        # Post-only orders: lower fill rate, depends on tier liquidity
        if tier == 1:
            return self.config.post_only_fill_rate
        elif tier == 2:
            return 0.75
        else:
            return 0.60

    def get_summary(self) -> dict:
        """Get cost model configuration summary for logging"""
        return {
            "maker_fee_pct": f"{self.config.maker_fee_pct * 100:.2f}%",
            "taker_fee_pct": f"{self.config.taker_fee_pct * 100:.2f}%",
            "tier1_spread_bps": self.config.tier1_spread_bps,
            "tier2_spread_bps": self.config.tier2_spread_bps,
            "tier3_spread_bps": self.config.tier3_spread_bps,
            "post_only_fill_rate": f"{self.config.post_only_fill_rate * 100:.0f}%",
            "min_profitable_move_maker_t2_roundtrip": f"{self.calculate_min_profitable_move(True, 2) * 100:.2f}%",
        }


# Singleton for easy import
_default_model: Optional[CostModel] = None


def get_cost_model(config: Optional[CostConfig] = None) -> CostModel:
    """Get singleton cost model instance"""
    global _default_model
    if _default_model is None or config is not None:
        _default_model = CostModel(config)
    return _default_model
