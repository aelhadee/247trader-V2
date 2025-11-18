"""
Slippage and Fee Model for Realistic Backtesting

Simulates realistic fill prices accounting for:
- Bid-ask spread
- Market impact (price moves against you)
- Coinbase maker/taker fees
- Tier-based slippage tolerance

Based on industry standards and Coinbase Advanced Trade fee structure.
"""
import logging
from dataclasses import dataclass
from typing import Literal, Optional

logger = logging.getLogger(__name__)


@dataclass
class SlippageConfig:
    """Configuration for slippage simulation"""
    # Coinbase Advanced Trade fees (bps)
    maker_fee_bps: float = 40.0  # 0.40% maker
    taker_fee_bps: float = 60.0  # 0.60% taker
    
    # Slippage budgets per tier (bps from mid price)
    tier1_slippage_bps: float = 10.0   # BTC/ETH: tight spread, low slippage
    tier2_slippage_bps: float = 25.0   # Top altcoins: moderate slippage
    tier3_slippage_bps: float = 50.0   # Long-tail: wider spreads
    
    # Market impact multiplier (larger orders = more impact)
    # 1.0 = no impact, 1.5 = 50% increase in slippage
    market_impact_multiplier: float = 1.2
    
    # Volatility-based slippage scaling
    # High volatility → wider spreads → more slippage
    volatility_multiplier: float = 1.5  # Max 1.5x slippage in high vol
    high_volatility_threshold_pct: float = 5.0  # 5% move = high vol
    
    # Partial fill simulation
    enable_partial_fills: bool = True
    partial_fill_probability: float = 0.1  # 10% chance of partial fill
    partial_fill_min_pct: float = 0.5  # At least 50% filled
    
    # Default to taker orders (conservative assumption)
    default_order_type: Literal["maker", "taker"] = "taker"


class SlippageModel:
    """
    Realistic slippage and fee model for backtest fill simulation.
    
    Models:
    1. **Spread crossing** - Pay bid-ask spread when taking liquidity
    2. **Market impact** - Price moves against large orders
    3. **Exchange fees** - Maker (0.40%) or Taker (0.60%) fees
    4. **Tier-based slippage** - Different assets have different liquidity
    
    Example:
        BUY 1 BTC at mid=$50,000 (tier1, taker order)
        - Mid: $50,000
        - Slippage (10bps): +$5
        - Market impact (20%): +$1
        - Fill price: $50,006
        - Fee (60bps): +$300
        - Total cost: $50,306 ($306 worse than mid)
    """
    
    def __init__(self, config: Optional[SlippageConfig] = None):
        """
        Initialize slippage model.
        
        Args:
            config: Slippage configuration (uses defaults if None)
        """
        self.config = config or SlippageConfig()
        logger.info(
            f"Initialized SlippageModel: maker={self.config.maker_fee_bps}bps, "
            f"taker={self.config.taker_fee_bps}bps"
        )
    
    def calculate_fill_price(
        self,
        mid_price: float,
        side: Literal["buy", "sell"],
        tier: Literal["tier1", "tier2", "tier3"] = "tier2",
        order_type: Optional[Literal["maker", "taker"]] = None,
        notional_usd: Optional[float] = None,
        volatility_pct: Optional[float] = None,
    ) -> float:
        """
        Calculate realistic fill price with slippage and spread.
        
        Args:
            mid_price: Mid-market price (average of bid/ask)
            side: "buy" or "sell"
            tier: Asset tier (affects base slippage)
            order_type: "maker" or "taker" (uses default if None)
            notional_usd: Order size in USD (affects market impact)
            volatility_pct: Recent volatility (e.g., 1h ATR as % of price)
        
        Returns:
            Fill price (worse than mid for realistic simulation)
        """
        if mid_price <= 0:
            raise ValueError(f"Invalid mid_price: {mid_price}")
        
        # Get base slippage for tier
        if tier == "tier1":
            base_slippage_bps = self.config.tier1_slippage_bps
        elif tier == "tier2":
            base_slippage_bps = self.config.tier2_slippage_bps
        else:
            base_slippage_bps = self.config.tier3_slippage_bps
        
        # Calculate market impact (larger orders = more slippage)
        impact_multiplier = 1.0
        if notional_usd is not None and notional_usd > 10000:
            # Scale impact with order size (log scale to prevent extreme values)
            # $10k = 1.0x, $100k = 1.2x, $1M = 1.4x
            import math
            size_factor = math.log10(notional_usd / 10000)
            impact_multiplier = 1.0 + (size_factor * 0.2)
            impact_multiplier = min(impact_multiplier, self.config.market_impact_multiplier)
        
        # Calculate volatility multiplier
        vol_multiplier = 1.0
        if volatility_pct is not None and volatility_pct > self.config.high_volatility_threshold_pct:
            # Scale slippage with volatility: 5% vol = 1.0x, 10% vol = 1.5x
            vol_factor = min(volatility_pct / self.config.high_volatility_threshold_pct, self.config.volatility_multiplier)
            vol_multiplier = vol_factor
        
        # Apply combined slippage
        total_slippage_bps = base_slippage_bps * impact_multiplier * vol_multiplier
        slippage_fraction = total_slippage_bps / 10000.0
        
        # Buy = pay more, Sell = receive less
        if side == "buy":
            fill_price = mid_price * (1 + slippage_fraction)
        else:
            fill_price = mid_price * (1 - slippage_fraction)
        
        return fill_price
    
    def calculate_total_cost(
        self,
        fill_price: float,
        quantity: float,
        side: Literal["buy", "sell"],
        order_type: Optional[Literal["maker", "taker"]] = None,
    ) -> tuple[float, float]:
        """
        Calculate total cost including exchange fees.
        
        Args:
            fill_price: Execution price
            quantity: Number of units
            side: "buy" or "sell"
            order_type: "maker" or "taker" (uses default if None)
        
        Returns:
            (gross_notional, total_cost_with_fees)
            - gross_notional: fill_price × quantity
            - total_cost: gross + fees (for buys) or gross - fees (for sells)
        """
        order_type = order_type or self.config.default_order_type
        
        # Calculate gross notional
        gross_notional = fill_price * quantity
        
        # Calculate fee
        fee_bps = (
            self.config.maker_fee_bps if order_type == "maker"
            else self.config.taker_fee_bps
        )
        fee_usd = gross_notional * (fee_bps / 10000.0)
        
        # Buy = pay fee, Sell = lose fee from proceeds
        if side == "buy":
            total_cost = gross_notional + fee_usd
        else:
            total_cost = gross_notional - fee_usd
        
        return gross_notional, total_cost
    
    def calculate_pnl(
        self,
        entry_price: float,
        exit_price: float,
        quantity: float,
        entry_order_type: Optional[Literal["maker", "taker"]] = None,
        exit_order_type: Optional[Literal["maker", "taker"]] = None,
    ) -> tuple[float, float, float]:
        """
        Calculate realistic PnL accounting for fees on both sides.
        
        Args:
            entry_price: Entry fill price
            exit_price: Exit fill price
            quantity: Position size
            entry_order_type: Entry order type (maker/taker)
            exit_order_type: Exit order type (maker/taker)
        
        Returns:
            (pnl_usd, pnl_pct, total_fees_usd)
        """
        entry_order_type = entry_order_type or self.config.default_order_type
        exit_order_type = exit_order_type or self.config.default_order_type
        
        # Entry cost (buy + fee)
        _, entry_cost = self.calculate_total_cost(
            entry_price, quantity, "buy", entry_order_type
        )
        
        # Exit proceeds (sell - fee)
        _, exit_proceeds = self.calculate_total_cost(
            exit_price, quantity, "sell", exit_order_type
        )
        
        # PnL = proceeds - cost
        pnl_usd = exit_proceeds - entry_cost
        pnl_pct = (pnl_usd / entry_cost) * 100
        
        # Calculate total fees paid
        entry_fee_bps = (
            self.config.maker_fee_bps if entry_order_type == "maker"
            else self.config.taker_fee_bps
        )
        exit_fee_bps = (
            self.config.maker_fee_bps if exit_order_type == "maker"
            else self.config.taker_fee_bps
        )
        total_fees_usd = (
            (entry_price * quantity * entry_fee_bps / 10000.0) +
            (exit_price * quantity * exit_fee_bps / 10000.0)
        )
        
        return pnl_usd, pnl_pct, total_fees_usd
    
    def simulate_partial_fill(
        self,
        requested_quantity: float,
        tier: Literal["tier1", "tier2", "tier3"] = "tier2",
    ) -> tuple[float, bool]:
        """
        Simulate partial fill for maker orders in low liquidity.
        
        Args:
            requested_quantity: Desired quantity
            tier: Asset tier (tier3 more likely to partial fill)
        
        Returns:
            (filled_quantity, is_partial)
            - filled_quantity: Amount actually filled
            - is_partial: True if partially filled
        """
        if not self.config.enable_partial_fills:
            return requested_quantity, False
        
        # Partial fill more likely for tier3 (low liquidity)
        import random
        probability = self.config.partial_fill_probability
        if tier == "tier3":
            probability *= 2.0  # 2x chance for tier3
        elif tier == "tier1":
            probability *= 0.5  # Half chance for tier1
        
        if random.random() < probability:
            # Partial fill: 50-99% of requested
            fill_pct = random.uniform(
                self.config.partial_fill_min_pct, 
                0.99
            )
            filled = requested_quantity * fill_pct
            return filled, True
        
        return requested_quantity, False
    
    def simulate_fill(
        self,
        mid_price: float,
        side: Literal["buy", "sell"],
        quantity: float,
        tier: Literal["tier1", "tier2", "tier3"] = "tier2",
        order_type: Optional[Literal["maker", "taker"]] = None,
        volatility_pct: Optional[float] = None,
    ) -> dict:
        """
        Simulate a complete fill with all details.
        
        Args:
            mid_price: Mid-market reference price
            side: "buy" or "sell"
            quantity: Position size
            tier: Asset tier
            order_type: "maker" or "taker"
            volatility_pct: Recent volatility for slippage adjustment
        
        Returns:
            Dict with fill_price, gross_notional, total_cost, fee_usd, slippage_bps, 
            filled_quantity, is_partial_fill
        """
        order_type = order_type or self.config.default_order_type
        
        # Simulate partial fill for maker orders
        filled_quantity = quantity
        is_partial = False
        if order_type == "maker":
            filled_quantity, is_partial = self.simulate_partial_fill(quantity, tier)
        
        notional_usd = mid_price * filled_quantity
        
        # Calculate fill price with slippage (including volatility adjustment)
        fill_price = self.calculate_fill_price(
            mid_price, side, tier, order_type, notional_usd, volatility_pct
        )
        
        # Calculate costs with fees
        gross_notional, total_cost = self.calculate_total_cost(
            fill_price, filled_quantity, side, order_type
        )
        
        # Calculate fee
        fee_bps = (
            self.config.maker_fee_bps if order_type == "maker"
            else self.config.taker_fee_bps
        )
        fee_usd = gross_notional * (fee_bps / 10000.0)
        
        # Calculate slippage vs mid
        slippage_bps = abs((fill_price - mid_price) / mid_price) * 10000
        
        return {
            "mid_price": mid_price,
            "fill_price": fill_price,
            "quantity": filled_quantity,
            "requested_quantity": quantity,
            "is_partial_fill": is_partial,
            "fill_pct": (filled_quantity / quantity) * 100 if quantity > 0 else 0,
            "gross_notional": gross_notional,
            "total_cost": total_cost,
            "fee_usd": fee_usd,
            "fee_bps": fee_bps,
            "slippage_bps": slippage_bps,
            "order_type": order_type,
            "side": side,
            "tier": tier,
        }


# Example usage
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    model = SlippageModel()
    
    # Example 1: Buy 1 BTC (tier1, normal volatility)
    print("\n=== Example 1: Buy 1 BTC at $50,000 (Normal Vol) ===")
    fill = model.simulate_fill(
        mid_price=50000.0,
        side="buy",
        quantity=1.0,
        tier="tier1",
        order_type="taker",
        volatility_pct=2.0  # 2% recent volatility
    )
    print(f"Mid: ${fill['mid_price']:,.2f}")
    print(f"Fill: ${fill['fill_price']:,.2f}")
    print(f"Slippage: {fill['slippage_bps']:.1f} bps")
    print(f"Fee: ${fill['fee_usd']:,.2f} ({fill['fee_bps']:.0f} bps)")
    print(f"Total cost: ${fill['total_cost']:,.2f}")
    print(f"Filled: {fill['fill_pct']:.1f}% ({'PARTIAL' if fill['is_partial_fill'] else 'FULL'})")
    
    # Example 2: Same trade in high volatility
    print("\n=== Example 2: Buy 1 BTC at $50,000 (High Vol) ===")
    fill_hv = model.simulate_fill(
        mid_price=50000.0,
        side="buy",
        quantity=1.0,
        tier="tier1",
        order_type="taker",
        volatility_pct=8.0  # 8% recent volatility (high)
    )
    print(f"Fill: ${fill_hv['fill_price']:,.2f}")
    print(f"Slippage: {fill_hv['slippage_bps']:.1f} bps (vs {fill['slippage_bps']:.1f} bps normal)")
    print(f"Extra cost from volatility: ${fill_hv['fill_price'] - fill['fill_price']:,.2f}")
    
    # Example 3: Tier3 maker order (partial fill likely)
    print("\n=== Example 3: Buy Tier3 Altcoin (Maker Order) ===")
    fill_t3 = model.simulate_fill(
        mid_price=1.50,
        side="buy",
        quantity=10000.0,
        tier="tier3",
        order_type="maker",
        volatility_pct=6.0
    )
    print(f"Requested: {fill_t3['requested_quantity']:,.0f} units")
    print(f"Filled: {fill_t3['quantity']:,.0f} units ({fill_t3['fill_pct']:.1f}%)")
    print(f"Status: {'PARTIAL FILL' if fill_t3['is_partial_fill'] else 'FULL FILL'}")
    print(f"Slippage: {fill_t3['slippage_bps']:.1f} bps")
    
    # Example 4: Calculate round-trip PnL
    print("\n=== Example 4: Round-trip PnL (buy $50k, sell $52k) ===")
    pnl_usd, pnl_pct, fees = model.calculate_pnl(
        entry_price=50000.0,
        exit_price=52000.0,
        quantity=1.0,
        entry_order_type="taker",
        exit_order_type="taker"
    )
    print("Entry: $50,000 | Exit: $52,000")
    print("Gross gain: $2,000 (4.00%)")
    print(f"Fees: ${fees:,.2f}")
    print(f"Net PnL: ${pnl_usd:,.2f} ({pnl_pct:.2f}%)")
