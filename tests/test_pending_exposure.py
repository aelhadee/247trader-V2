"""
Test pending order exposure accounting in risk checks.

CRITICAL SAFETY TEST: Verifies that open orders count toward risk caps
to prevent over-allocation while orders are working.
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import Mock, patch
from decimal import Decimal

from core.risk import RiskEngine, PortfolioState, RiskCheckResult
from strategy.rules_engine import TradeProposal


@pytest.fixture
def policy_config():
    """Minimal policy for testing"""
    return {
        "risk": {
            "max_total_at_risk_pct": 15.0,  # 15% max exposure
            "max_position_size_pct": 5.0,    # 5% per position
            "max_per_asset_pct": 5.0,
            "min_position_size_pct": 0.5,
            "daily_stop_pnl_pct": -3.0,
            "max_trades_per_day": 10,
            "max_trades_per_hour": 4,
        },
        "circuit_breakers": {
            "check_exchange_status": False,
            "check_product_status": False,
        },
        "governance": {
            "kill_switch_enabled": True,
            "kill_switch_file": "data/KILL_SWITCH_TEST",
        }
    }


@pytest.fixture
def risk_engine(policy_config):
    """RiskEngine with test policy"""
    return RiskEngine(
        policy=policy_config,
        universe_manager=None,
        exchange=None,
        state_store=None,
        alert_service=None
    )


def test_pending_buy_order_counts_toward_total_exposure(risk_engine):
    """
    CRITICAL: Pending BUY orders must count toward max_total_at_risk_pct.
    
    Scenario:
    - NAV: $10,000
    - Max total at risk: 15% = $1,500
    - Pending BUY order: $600 (6% of NAV)
    - Existing position: $500 (5% of NAV) 
    - Proposed trade: $900 (9% of NAV)
    - Total: $600 + $500 + $900 = $2,000 (20% of NAV)
    
    Expected: REJECT (exceeds 15% cap)
    """
    portfolio = PortfolioState(
        account_value_usd=10_000.0,
        open_positions={
            "ETH-USD": {"units": 0.3, "avg_entry": 1666.67, "usd": 500.0}
        },
        daily_pnl_pct=0.0,
        max_drawdown_pct=0.0,
        trades_today=0,
        trades_this_hour=0,
        consecutive_losses=0,
        last_loss_time=None,
        current_time=datetime.now(timezone.utc),
        weekly_pnl_pct=0.0,
        # CRITICAL: Pending BUY order that should reduce headroom
        pending_orders={
            "buy": {"BTC-USD": 600.0},  # $600 pending
            "sell": {}
        }
    )
    
    proposal = TradeProposal(
        symbol="SOL-USD",
        side="buy",
        size_pct=9.0,  # 9% of NAV = $900
        confidence=0.8,
        reason="test"
    )
    
    result = risk_engine.check_all([proposal], portfolio)
    
    # Should REJECT because:
    # - Current positions: $500 (5%)
    # - Pending orders: $600 (6%)
    # - Proposed: $900 (9%)
    # - Total: $2,000 (20%) > 15% limit
    assert not result.approved, "Should reject when pending + positions + proposed > max_total_at_risk_pct"
    assert "max_total_at_risk_pct" in result.violated_checks or "max_total_at_risk" in result.violated_checks or "global_exposure" in result.violated_checks


def test_pending_buy_order_counts_toward_per_symbol_cap(risk_engine):
    """
    CRITICAL: Pending BUY for same symbol must count toward per-symbol cap.
    
    Scenario:
    - NAV: $10,000
    - Max per position: 5% = $500
    - Pending BUY BTC-USD: $300
    - Proposed BUY BTC-USD: $300
    - Total for BTC-USD: $600 (6% of NAV)
    
    Expected: REJECT (exceeds 5% per-asset cap)
    """
    portfolio = PortfolioState(
        account_value_usd=10_000.0,
        open_positions={},
        daily_pnl_pct=0.0,
        max_drawdown_pct=0.0,
        trades_today=0,
        trades_this_hour=0,
        consecutive_losses=0,
        last_loss_time=None,
        current_time=datetime.now(timezone.utc),
        weekly_pnl_pct=0.0,
        pending_orders={
            "buy": {"BTC-USD": 300.0},  # $300 pending for BTC
            "sell": {}
        }
    )
    
    proposal = TradeProposal(
        symbol="BTC-USD",
        side="buy",
        size_pct=3.0,  # Another $300 for BTC
        confidence=0.8,
        reason="test",
    )
    
    result = risk_engine.check_all([proposal], portfolio)
    
    # Should REJECT: $300 pending + $300 proposed = $600 (6%) > 5% limit
    assert not result.approved, "Should reject when pending + proposed > max_position_size_pct for same symbol"
    assert "position_size" in result.violated_checks or "per_asset" in result.violated_checks or "max_position_size_pct" in result.violated_checks or "max_per_asset_pct" in result.violated_checks


def test_pending_orders_allow_room_for_valid_trade(risk_engine):
    """
    Verify that pending orders are correctly accounted but don't over-block.
    
    Scenario:
    - NAV: $10,000
    - Max total: 15% = $1,500
    - Pending: $400 (4%)
    - Existing: $300 (3%)
    - Proposed: $500 (5%)
    - Total: $1,200 (12%) < 15%
    
    Expected: APPROVE
    """
    portfolio = PortfolioState(
        account_value_usd=10_000.0,
        open_positions={
            "ETH-USD": {"units": 0.2, "avg_entry": 1500.0, "usd": 300.0}
        },
        daily_pnl_pct=0.0,
        max_drawdown_pct=0.0,
        trades_today=0,
        trades_this_hour=0,
        consecutive_losses=0,
        last_loss_time=None,
        current_time=datetime.now(timezone.utc),
        weekly_pnl_pct=0.0,
        pending_orders={
            "buy": {"BTC-USD": 400.0},
            "sell": {}
        }
    )
    
    proposal = TradeProposal(
        symbol="SOL-USD",
        side="buy",
        size_pct=5.0,
        confidence=0.8,
        reason="test",
    )
    
    result = risk_engine.check_all([proposal], portfolio)
    
    # Should APPROVE: $300 + $400 + $500 = $1,200 (12%) < 15%
    assert result.approved, f"Should approve valid trade with pending orders: {result.reason}"


def test_empty_pending_orders_backward_compatible(risk_engine):
    """
    Verify empty pending_orders dict works (backward compatibility).
    """
    portfolio = PortfolioState(
        account_value_usd=10_000.0,
        open_positions={},
        daily_pnl_pct=0.0,
        max_drawdown_pct=0.0,
        trades_today=0,
        trades_this_hour=0,
        consecutive_losses=0,
        last_loss_time=None,
        current_time=datetime.now(timezone.utc),
        weekly_pnl_pct=0.0,
        pending_orders={}  # Empty
    )
    
    proposal = TradeProposal(
        symbol="BTC-USD",
        side="buy",
        size_pct=5.0,
        confidence=0.8,
        reason="test",
    )
    
    result = risk_engine.check_all([proposal], portfolio)
    
    # Should work without errors
    assert result.approved, f"Should handle empty pending_orders: {result.reason}"


def test_sell_orders_not_counted_in_buy_exposure(risk_engine):
    """
    Verify SELL orders don't count toward BUY exposure limits.
    """
    portfolio = PortfolioState(
        account_value_usd=10_000.0,
        open_positions={
            "BTC-USD": {"units": 0.02, "avg_entry": 50000.0, "usd": 1000.0}
        },
        daily_pnl_pct=0.0,
        max_drawdown_pct=0.0,
        trades_today=0,
        trades_this_hour=0,
        consecutive_losses=0,
        last_loss_time=None,
        current_time=datetime.now(timezone.utc),
        weekly_pnl_pct=0.0,
        pending_orders={
            "buy": {},
            "sell": {"BTC-USD": 500.0}  # Pending SELL doesn't reduce buy capacity
        }
    )
    
    proposal = TradeProposal(
        symbol="ETH-USD",
        side="buy",
        size_pct=4.0,  # 4% of NAV
        confidence=0.8,
        reason="test",
    )
    
    result = risk_engine.check_all([proposal], portfolio)
    
    # Should APPROVE: only $1000 position + $400 proposed = $1400 (14%) < 15%
    # Pending SELL of $500 doesn't count
    assert result.approved, "Pending SELL should not reduce buy capacity"


def test_multiple_pending_buys_aggregate_correctly(risk_engine):
    """
    Verify multiple pending BUY orders for different symbols aggregate.
    """
    portfolio = PortfolioState(
        account_value_usd=10_000.0,
        open_positions={},
        daily_pnl_pct=0.0,
        max_drawdown_pct=0.0,
        trades_today=0,
        trades_this_hour=0,
        consecutive_losses=0,
        last_loss_time=None,
        current_time=datetime.now(timezone.utc),
        weekly_pnl_pct=0.0,
        pending_orders={
            "buy": {
                "BTC-USD": 400.0,  # 4%
                "ETH-USD": 300.0,  # 3%
                "SOL-USD": 200.0   # 2%
            },
            "sell": {}
        }
    )
    
    # Total pending: $900 (9%)
    # Proposed: $700 (7%)
    # Total: $1,600 (16%) > 15% limit
    
    proposal = TradeProposal(
        symbol="AVAX-USD",
        side="buy",
        size_pct=7.0,
        confidence=0.8,
        reason="test",
    )
    
    result = risk_engine.check_all([proposal], portfolio)
    
    # Should REJECT: $900 pending + $700 proposed = $1,600 (16%) > 15%
    assert not result.approved, "Should aggregate all pending buys toward total exposure"
    assert "max_total_at_risk" in result.violated_checks or "global_exposure" in result.violated_checks or "max_total_at_risk_pct" in result.violated_checks


def test_integration_with_main_loop_state_hydration():
    """
    Integration test: Verify _build_pending_orders_from_state works correctly.
    """
    from runner.main_loop import TradingLoop
    
    # Mock state with open orders
    mock_state = {
        "open_orders": {
            "order-123": {
                "order_id": "order-123",
                "symbol": "BTC-USD",
                "side": "buy",
                "size": 0.01,
                "price": 50000.0,
                "order_value_usd": 500.0,
                "status": "OPEN"
            },
            "order-456": {
                "order_id": "order-456",
                "symbol": "ETH-USD",
                "side": "buy",
                "size": 0.2,
                "price": 2500.0,
                "order_value_usd": 500.0,
                "status": "OPEN"
            },
            "order-789": {
                "order_id": "order-789",
                "symbol": "BTC-USD",
                "side": "sell",
                "size": 0.005,
                "price": 51000.0,
                "order_value_usd": 255.0,
                "status": "OPEN"
            }
        }
    }
    
    # Create trading loop instance (use minimal config)
    with patch("runner.main_loop.TradingLoop._load_yaml") as mock_load:
        mock_load.return_value = {
            "app": {"mode": "DRY_RUN"},
            "exchange": {"read_only": True},
            "logging": {"level": "ERROR"}
        }
        
        with patch("tools.config_validator.validate_all_configs", return_value=[]):
            loop = TradingLoop.__new__(TradingLoop)
            loop.config_dir = "config"
            
            # Call the method directly
            pending = loop._build_pending_orders_from_state(mock_state)
    
    # Verify pending orders structure
    assert "buy" in pending
    assert "sell" in pending
    
    # BTC-USD should have $500 pending BUY
    assert pending["buy"]["BTC-USD"] == 500.0
    
    # ETH-USD should have $500 pending BUY
    assert pending["buy"]["ETH-USD"] == 500.0
    
    # BTC-USD should have $255 pending SELL (tracked separately)
    assert pending["sell"]["BTC-USD"] == 255.0
    
    print("✅ Integration test passed: pending orders correctly hydrated from state")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
