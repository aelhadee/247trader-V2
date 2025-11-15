"""
Tests for Slippage Model

Validates realistic fill price calculation, fee accounting, and PnL simulation.
"""
import pytest
from backtest.slippage_model import SlippageModel, SlippageConfig


class TestSlippageConfig:
    """Test slippage configuration"""
    
    def test_default_config(self):
        """Default config has reasonable values"""
        config = SlippageConfig()
        
        assert config.maker_fee_bps == 40.0  # Coinbase standard
        assert config.taker_fee_bps == 60.0
        assert config.tier1_slippage_bps < config.tier2_slippage_bps < config.tier3_slippage_bps
        assert config.default_order_type == "taker"  # Conservative


class TestFillPriceCalculation:
    """Test fill price calculation with slippage"""
    
    def test_buy_pays_more_than_mid(self):
        """Buy orders pay more than mid price"""
        model = SlippageModel()
        
        fill_price = model.calculate_fill_price(
            mid_price=100.0,
            side="buy",
            tier="tier2"
        )
        
        assert fill_price > 100.0  # Pay more
    
    def test_sell_receives_less_than_mid(self):
        """Sell orders receive less than mid price"""
        model = SlippageModel()
        
        fill_price = model.calculate_fill_price(
            mid_price=100.0,
            side="sell",
            tier="tier2"
        )
        
        assert fill_price < 100.0  # Receive less
    
    def test_tier1_has_lower_slippage(self):
        """Tier 1 assets have tighter spreads"""
        model = SlippageModel()
        
        tier1_fill = model.calculate_fill_price(100.0, "buy", "tier1")
        tier2_fill = model.calculate_fill_price(100.0, "buy", "tier2")
        tier3_fill = model.calculate_fill_price(100.0, "buy", "tier3")
        
        # More slippage as tier decreases
        assert tier1_fill < tier2_fill < tier3_fill
    
    def test_larger_orders_have_more_impact(self):
        """Larger orders experience more market impact"""
        model = SlippageModel()
        
        small_fill = model.calculate_fill_price(
            100.0, "buy", "tier2", notional_usd=1000
        )
        large_fill = model.calculate_fill_price(
            100.0, "buy", "tier2", notional_usd=100000
        )
        
        assert large_fill > small_fill  # More impact
    
    def test_invalid_mid_price_raises_error(self):
        """Invalid mid price raises ValueError"""
        model = SlippageModel()
        
        with pytest.raises(ValueError, match="Invalid mid_price"):
            model.calculate_fill_price(0.0, "buy")
        
        with pytest.raises(ValueError, match="Invalid mid_price"):
            model.calculate_fill_price(-100.0, "buy")


class TestFeeCalculation:
    """Test fee calculation"""
    
    def test_maker_fee_lower_than_taker(self):
        """Maker fees are lower than taker fees"""
        model = SlippageModel()
        
        _, maker_cost = model.calculate_total_cost(
            100.0, 1.0, "buy", order_type="maker"
        )
        _, taker_cost = model.calculate_total_cost(
            100.0, 1.0, "buy", order_type="taker"
        )
        
        assert maker_cost < taker_cost
    
    def test_buy_adds_fee_to_cost(self):
        """Buy orders pay fee on top of fill price"""
        model = SlippageModel()
        
        gross, total = model.calculate_total_cost(
            100.0, 1.0, "buy", order_type="taker"
        )
        
        assert gross == 100.0  # fill × quantity
        assert total > gross  # fee added
        
        # Taker fee = 60bps = 0.60%
        expected_fee = 100.0 * 0.006
        assert abs(total - (gross + expected_fee)) < 0.01
    
    def test_sell_subtracts_fee_from_proceeds(self):
        """Sell orders lose fee from proceeds"""
        model = SlippageModel()
        
        gross, total = model.calculate_total_cost(
            100.0, 1.0, "sell", order_type="taker"
        )
        
        assert gross == 100.0
        assert total < gross  # Fee deducted
        
        expected_fee = 100.0 * 0.006
        assert abs(total - (gross - expected_fee)) < 0.01
    
    def test_fee_scales_with_notional(self):
        """Fee scales proportionally with order size"""
        model = SlippageModel()
        
        _, small_cost = model.calculate_total_cost(100.0, 1.0, "buy", "taker")
        _, large_cost = model.calculate_total_cost(100.0, 10.0, "buy", "taker")
        
        # 10x size = 10x cost
        assert abs(large_cost - (small_cost * 10)) < 0.01


class TestPnLCalculation:
    """Test PnL calculation with fees"""
    
    def test_winning_trade_pnl(self):
        """Winning trade calculates correct PnL"""
        model = SlippageModel()
        
        pnl_usd, pnl_pct, fees = model.calculate_pnl(
            entry_price=100.0,
            exit_price=110.0,
            quantity=1.0,
            entry_order_type="taker",
            exit_order_type="taker"
        )
        
        # Gross gain = $10
        # Entry cost = $100 + 0.6% = $100.60
        # Exit proceeds = $110 - 0.6% = $109.34
        # Net PnL = $109.34 - $100.60 = $8.74
        assert pnl_usd > 0  # Winning trade
        assert pnl_usd < 10.0  # Less than gross gain due to fees
        assert abs(pnl_usd - 8.74) < 0.01
    
    def test_losing_trade_pnl(self):
        """Losing trade calculates correct PnL"""
        model = SlippageModel()
        
        pnl_usd, pnl_pct, fees = model.calculate_pnl(
            entry_price=100.0,
            exit_price=90.0,
            quantity=1.0,
            entry_order_type="taker",
            exit_order_type="taker"
        )
        
        assert pnl_usd < 0  # Losing trade
        assert pnl_usd < -10.0  # Worse than gross loss due to fees
    
    def test_breakeven_trade_loses_to_fees(self):
        """Breakeven trade loses money due to fees"""
        model = SlippageModel()
        
        pnl_usd, pnl_pct, fees = model.calculate_pnl(
            entry_price=100.0,
            exit_price=100.0,  # Same price
            quantity=1.0,
            entry_order_type="taker",
            exit_order_type="taker"
        )
        
        # Even at same price, lose fees on both sides
        assert pnl_usd < 0
        assert fees > 0
        assert abs(pnl_usd + fees) < 0.01  # PnL ≈ -fees
    
    def test_maker_orders_have_lower_fees(self):
        """Maker orders pay lower fees than taker"""
        model = SlippageModel()
        
        _, _, maker_fees = model.calculate_pnl(
            100.0, 110.0, 1.0, "maker", "maker"
        )
        _, _, taker_fees = model.calculate_pnl(
            100.0, 110.0, 1.0, "taker", "taker"
        )
        
        assert maker_fees < taker_fees
    
    def test_pnl_percentage_calculated_correctly(self):
        """PnL percentage calculated from entry cost"""
        model = SlippageModel()
        
        pnl_usd, pnl_pct, _ = model.calculate_pnl(
            entry_price=100.0,
            exit_price=110.0,
            quantity=1.0,
            entry_order_type="taker",
            exit_order_type="taker"
        )
        
        # PnL% = (pnl_usd / entry_cost) × 100
        entry_cost = 100.0 * 1.006  # $100 + 0.6% fee
        expected_pnl_pct = (pnl_usd / entry_cost) * 100
        assert abs(pnl_pct - expected_pnl_pct) < 0.01


class TestFullSimulation:
    """Test complete fill simulation"""
    
    def test_simulate_fill_returns_all_details(self):
        """simulate_fill returns complete fill details"""
        model = SlippageModel()
        
        fill = model.simulate_fill(
            mid_price=50000.0,
            side="buy",
            quantity=1.0,
            tier="tier1",
            order_type="taker"
        )
        
        # Check all expected fields
        assert "mid_price" in fill
        assert "fill_price" in fill
        assert "quantity" in fill
        assert "gross_notional" in fill
        assert "total_cost" in fill
        assert "fee_usd" in fill
        assert "fee_bps" in fill
        assert "slippage_bps" in fill
        assert "order_type" in fill
        assert "side" in fill
        assert "tier" in fill
    
    def test_simulate_buy_tier1_btc(self):
        """Simulate realistic BTC buy"""
        model = SlippageModel()
        
        fill = model.simulate_fill(
            mid_price=50000.0,
            side="buy",
            quantity=1.0,
            tier="tier1",
            order_type="taker"
        )
        
        # Tier 1 slippage = 10bps = $5 + market impact
        # $50k notional → ~1.1x impact → fill price ≈ $50,055
        assert 50000 < fill["fill_price"] < 50100
        
        # Taker fee = 60bps on ~$50,055 ≈ $300
        assert 280 < fill["fee_usd"] < 320
        
        # Total cost = fill + fee ≈ $50,355
        assert 50280 < fill["total_cost"] < 50400
    
    def test_simulate_sell_tier2_altcoin(self):
        """Simulate realistic altcoin sell"""
        model = SlippageModel()
        
        fill = model.simulate_fill(
            mid_price=1000.0,
            side="sell",
            quantity=10.0,
            tier="tier2",
            order_type="taker"
        )
        
        # Tier 2 slippage = 25bps
        # Sell receives less than mid
        assert fill["fill_price"] < 1000.0
        
        # Fee deducted from proceeds
        assert fill["total_cost"] < fill["gross_notional"]


class TestCustomConfig:
    """Test custom configuration"""
    
    def test_custom_fee_structure(self):
        """Custom fees apply correctly"""
        config = SlippageConfig(
            maker_fee_bps=20.0,  # Lower tier
            taker_fee_bps=40.0
        )
        model = SlippageModel(config)
        
        _, cost = model.calculate_total_cost(100.0, 1.0, "buy", "maker")
        
        # Should use 20bps fee instead of default 40bps
        expected_fee = 100.0 * 0.002
        assert abs(cost - (100.0 + expected_fee)) < 0.01
    
    def test_custom_slippage_budgets(self):
        """Custom slippage budgets apply"""
        config = SlippageConfig(
            tier1_slippage_bps=5.0,  # Tighter
            tier2_slippage_bps=15.0,
            tier3_slippage_bps=30.0
        )
        model = SlippageModel(config)
        
        fill = model.calculate_fill_price(100.0, "buy", "tier1")
        
        # Should use 5bps slippage instead of default 10bps
        assert fill < 100.06  # Less than 6bps total


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
