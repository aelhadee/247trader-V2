"""
Tests for Enhanced Slippage Model

Tests volatility-based adjustments and partial fill simulation.
"""

import pytest
from backtest.slippage_model import SlippageModel, SlippageConfig


def test_volatility_adjustment():
    """Test that high volatility increases slippage"""
    model = SlippageModel()
    
    # Normal volatility (2%)
    fill_normal = model.calculate_fill_price(
        mid_price=50000.0,
        side="buy",
        tier="tier1",
        volatility_pct=2.0
    )
    
    # High volatility (10%)
    fill_high_vol = model.calculate_fill_price(
        mid_price=50000.0,
        side="buy",
        tier="tier1",
        volatility_pct=10.0
    )
    
    # High vol should result in worse fill price
    assert fill_high_vol > fill_normal
    
    # Calculate slippage increase
    slippage_normal = (fill_normal - 50000) / 50000 * 10000
    slippage_high = (fill_high_vol - 50000) / 50000 * 10000
    
    # High vol should have more slippage
    assert slippage_high > slippage_normal
    print(f"Normal vol slippage: {slippage_normal:.1f} bps")
    print(f"High vol slippage: {slippage_high:.1f} bps")


def test_no_volatility_adjustment_below_threshold():
    """Test that low volatility doesn't increase slippage"""
    model = SlippageModel()
    
    # Low volatility (1%)
    fill_low = model.calculate_fill_price(
        mid_price=50000.0,
        side="buy",
        tier="tier1",
        volatility_pct=1.0
    )
    
    # No volatility data
    fill_none = model.calculate_fill_price(
        mid_price=50000.0,
        side="buy",
        tier="tier1",
        volatility_pct=None
    )
    
    # Should be similar (no vol adjustment for low vol)
    assert abs(fill_low - fill_none) < 10  # Within $10


def test_partial_fill_tier3():
    """Test that tier3 has higher partial fill rate"""
    model = SlippageModel(SlippageConfig(
        enable_partial_fills=True,
        partial_fill_probability=0.1
    ))
    
    # Run multiple simulations
    partial_fills_t1 = 0
    partial_fills_t3 = 0
    iterations = 100
    
    for _ in range(iterations):
        fill_t1 = model.simulate_fill(
            mid_price=50000.0,
            side="buy",
            quantity=1.0,
            tier="tier1",
            order_type="maker"
        )
        fill_t3 = model.simulate_fill(
            mid_price=1.50,
            side="buy",
            quantity=10000.0,
            tier="tier3",
            order_type="maker"
        )
        
        if fill_t1["is_partial_fill"]:
            partial_fills_t1 += 1
        if fill_t3["is_partial_fill"]:
            partial_fills_t3 += 1
    
    # Tier3 should have more partial fills
    print(f"Tier1 partial fills: {partial_fills_t1}/{iterations}")
    print(f"Tier3 partial fills: {partial_fills_t3}/{iterations}")
    assert partial_fills_t3 > partial_fills_t1


def test_partial_fill_disabled():
    """Test that partial fills can be disabled"""
    model = SlippageModel(SlippageConfig(enable_partial_fills=False))
    
    # Run many simulations
    for _ in range(50):
        fill = model.simulate_fill(
            mid_price=1.0,
            side="buy",
            quantity=1000.0,
            tier="tier3",
            order_type="maker"
        )
        
        # Should never be partial
        assert fill["is_partial_fill"] is False
        assert fill["quantity"] == fill["requested_quantity"]
        assert fill["fill_pct"] == 100.0


def test_taker_orders_no_partial_fills():
    """Test that taker orders always fill completely"""
    model = SlippageModel()
    
    for _ in range(50):
        fill = model.simulate_fill(
            mid_price=1.0,
            side="buy",
            quantity=1000.0,
            tier="tier3",
            order_type="taker"  # Taker = immediate execution
        )
        
        # Taker orders should never be partial
        assert fill["is_partial_fill"] is False
        assert fill["quantity"] == fill["requested_quantity"]


def test_simulate_fill_with_volatility():
    """Test full fill simulation with volatility"""
    model = SlippageModel()
    
    fill = model.simulate_fill(
        mid_price=50000.0,
        side="buy",
        quantity=1.0,
        tier="tier1",
        order_type="taker",
        volatility_pct=8.0  # High volatility
    )
    
    # Check all fields present
    assert "mid_price" in fill
    assert "fill_price" in fill
    assert "quantity" in fill
    assert "requested_quantity" in fill
    assert "is_partial_fill" in fill
    assert "fill_pct" in fill
    assert "slippage_bps" in fill
    
    # Fill price should be worse than mid
    assert fill["fill_price"] > fill["mid_price"]
    
    # Slippage should be elevated due to volatility
    assert fill["slippage_bps"] > 10  # Base tier1 slippage


def test_market_impact_large_orders():
    """Test that large orders have more impact"""
    model = SlippageModel()
    
    # Small order ($10k)
    fill_small = model.calculate_fill_price(
        mid_price=50000.0,
        side="buy",
        tier="tier1",
        notional_usd=10000.0
    )
    
    # Large order ($100k)
    fill_large = model.calculate_fill_price(
        mid_price=50000.0,
        side="buy",
        tier="tier1",
        notional_usd=100000.0
    )
    
    # Large order should have worse price
    assert fill_large > fill_small
    
    slippage_small = (fill_small - 50000) / 50000 * 10000
    slippage_large = (fill_large - 50000) / 50000 * 10000
    
    print(f"Small order slippage: {slippage_small:.1f} bps")
    print(f"Large order slippage: {slippage_large:.1f} bps")
    assert slippage_large > slippage_small


def test_combined_vol_and_impact():
    """Test combined volatility and market impact"""
    model = SlippageModel()
    
    # Small order, normal vol
    fill_baseline = model.calculate_fill_price(
        mid_price=50000.0,
        side="buy",
        tier="tier2",
        notional_usd=10000.0,
        volatility_pct=2.0
    )
    
    # Large order, high vol
    fill_worst = model.calculate_fill_price(
        mid_price=50000.0,
        side="buy",
        tier="tier2",
        notional_usd=100000.0,
        volatility_pct=10.0
    )
    
    # Combined effects should result in much worse price
    slippage_baseline = (fill_baseline - 50000) / 50000 * 10000
    slippage_worst = (fill_worst - 50000) / 50000 * 10000
    
    print(f"Baseline slippage: {slippage_baseline:.1f} bps")
    print(f"Worst case slippage: {slippage_worst:.1f} bps")
    
    # Worst case should be at least 1.5x baseline (combined vol + impact)
    assert slippage_worst > slippage_baseline * 1.5


def test_tier_differences():
    """Test that different tiers have different slippage"""
    model = SlippageModel()
    
    fills = {}
    for tier in ["tier1", "tier2", "tier3"]:
        fill = model.simulate_fill(
            mid_price=50000.0,
            side="buy",
            quantity=1.0,
            tier=tier,
            order_type="taker"
        )
        fills[tier] = fill["slippage_bps"]
    
    # Tier3 should have most slippage
    print(f"Tier1 slippage: {fills['tier1']:.1f} bps")
    print(f"Tier2 slippage: {fills['tier2']:.1f} bps")
    print(f"Tier3 slippage: {fills['tier3']:.1f} bps")
    
    assert fills["tier3"] > fills["tier2"] > fills["tier1"]


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
