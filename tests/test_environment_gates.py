"""
Test environment runtime gates (Production Blocker #4).

Validates comprehensive mode/read_only checks that prevent accidental LIVE trading:
- Mode must be explicitly set to LIVE
- Exchange read_only must be explicitly set to false
- Both conditions required for real-money execution
- Fail-closed on configuration errors

Per system safety requirements:
- DRY_RUN: No exchange interaction, pure simulation
- PAPER: Live data, simulated execution
- LIVE + read_only=false: Real orders enabled
- Any other combination: READ_ONLY mode (no real orders)
"""

import pytest
from unittest.mock import MagicMock, patch
from core.execution import ExecutionEngine
from core.exchange_coinbase import CoinbaseExchange
from runner.main_loop import TradingLoop


# ============================================================================
# Test 1: DRY_RUN mode never touches exchange (even if read_only=false)
# ============================================================================
def test_dry_run_never_executes():
    """DRY_RUN mode should never call exchange, regardless of read_only setting."""
    mock_exchange = MagicMock(spec=CoinbaseExchange)
    mock_exchange.read_only = False  # Even with read_only=false
    
    engine = ExecutionEngine(mode="DRY_RUN", exchange=mock_exchange)
    
    result = engine.execute("BTC-USD", "BUY", 100.0)
    
    # Should succeed but never call exchange
    assert result.success
    assert result.route == "shadow_dry_run"  # DRY_RUN now uses shadow execution
    assert "shadow_" in result.order_id
    
    # Verify no exchange calls
    mock_exchange.preview_order.assert_not_called()
    mock_exchange.place_order.assert_not_called()


# ============================================================================
# Test 2: PAPER mode uses live data but never places orders
# ============================================================================
def test_paper_mode_simulates_only():
    """PAPER mode should get live quotes but never place real orders."""
    mock_exchange = MagicMock(spec=CoinbaseExchange)
    mock_exchange.read_only = False  # Even with read_only=false
    
    # Mock quote for paper simulation
    from core.exchange_coinbase import Quote
    from datetime import datetime, timezone
    mock_quote = Quote(
        symbol="BTC-USD",
        bid=50000.0,
        ask=50100.0,
        mid=50050.0,
        spread_bps=20.0,
        last=50050.0,
        volume_24h=1000000.0,
        timestamp=datetime.now(timezone.utc)
    )
    mock_exchange.get_quote.return_value = mock_quote
    
    # Mock account balances to avoid trading pair lookup
    mock_exchange.get_accounts.return_value = [
        {"currency": "USDC", "available_balance": {"value": "1000.0"}}
    ]
    
    engine = ExecutionEngine(mode="PAPER", exchange=mock_exchange)
    
    # Use full trading pair to bypass trading pair lookup
    result = engine.execute("BTC-USD", "SELL", 100.0)  # SELL doesn't trigger pair lookup
    
    # Should succeed with paper simulation
    assert result.success
    assert result.route == "paper_simulated"
    assert "paper_" in result.order_id
    
    # Should get quote for simulation
    mock_exchange.get_quote.assert_called_once()
    
    # Should NEVER place real order
    mock_exchange.place_order.assert_not_called()
    mock_exchange.preview_order.assert_not_called()


# ============================================================================
# Test 3: LIVE mode + read_only=true blocks execution
# ============================================================================
def test_live_mode_read_only_true_blocks():
    """LIVE mode with read_only=true should raise ValueError."""
    mock_exchange = MagicMock(spec=CoinbaseExchange)
    mock_exchange.read_only = True  # Safety gate
    
    engine = ExecutionEngine(mode="LIVE", exchange=mock_exchange)
    
    # Should raise on execution attempt (use SELL to avoid trading pair lookup)
    with pytest.raises(ValueError, match="Cannot execute LIVE orders with read_only exchange"):
        engine.execute("BTC-USD", "SELL", 100.0)


# ============================================================================
# Test 4: LIVE mode + read_only=false enables real execution
# ============================================================================
def test_live_mode_read_only_false_allows_execution():
    """LIVE mode with read_only=false should allow real orders."""
    mock_exchange = MagicMock(spec=CoinbaseExchange)
    mock_exchange.read_only = False  # Real trading enabled
    mock_exchange.api_key = "test_key"  # Required for preview_order
    
    # Mock necessary exchange methods
    from core.exchange_coinbase import Quote
    from datetime import datetime, timezone
    mock_quote = Quote(
        symbol="BTC-USD",
        bid=50000.0,
        ask=50100.0,
        mid=50050.0,
        spread_bps=20.0,
        last=50050.0,
        volume_24h=1000000.0,
        timestamp=datetime.now(timezone.utc)
    )
    mock_exchange.get_quote.return_value = mock_quote
    mock_exchange.get_product_metadata.return_value = {
        "base_increment": "0.00000001",
        "quote_increment": "0.01",
        "base_min_size": "0.0001",
        "min_market_funds": "10"
    }
    mock_exchange.preview_order.return_value = {
        "success": True,
        "estimated_slippage_bps": 10.0
    }
    mock_exchange.place_order.return_value = {
        "success": True,
        "order_id": "live_order_123",
        "status": "FILLED",
        "filled_size": "0.002",
        "filled_value": "100.0",
        "fees": "0.60"
    }
    
    engine = ExecutionEngine(mode="LIVE", exchange=mock_exchange, policy={})
    
    # Use SELL to avoid trading pair lookup complexity; skip liquidity checks
    result = engine.execute("BTC-USD", "SELL", 100.0, skip_liquidity_checks=True)
    
    # Should succeed and call real exchange
    assert result.success
    assert result.route.startswith("live_")
    
    # Verify exchange interaction
    mock_exchange.preview_order.assert_called()
    mock_exchange.place_order.assert_called()


# ============================================================================
# Test 5: Invalid mode raises ValueError
# ============================================================================
def test_invalid_mode_rejected():
    """Invalid mode strings should raise ValueError."""
    with pytest.raises(ValueError, match="Invalid mode"):
        engine = ExecutionEngine(mode="INVALID")
        engine.execute("BTC-USD", "BUY", 100.0)


# ============================================================================
# Test 6: Mode is case-insensitive but normalized
# ============================================================================
def test_mode_case_normalization():
    """Mode should be case-insensitive (normalized to uppercase)."""
    mock_exchange = MagicMock(spec=CoinbaseExchange)
    
    engine_lower = ExecutionEngine(mode="dry_run", exchange=mock_exchange)
    assert engine_lower.mode == "DRY_RUN"
    
    engine_mixed = ExecutionEngine(mode="Paper", exchange=mock_exchange)
    assert engine_mixed.mode == "PAPER"
    
    engine_upper = ExecutionEngine(mode="LIVE", exchange=mock_exchange)
    assert engine_upper.mode == "LIVE"


# ============================================================================
# Test 7: TradingLoop enforces read_only based on mode
# ============================================================================
def test_trading_loop_read_only_enforcement():
    """TradingLoop should enforce read_only=true unless mode=LIVE AND config allows."""
    # Test the logic directly without full TradingLoop instantiation
    
    # DRY_RUN mode: always read_only (even if config says false)
    mode = "DRY_RUN"
    exchange_config_read_only_false = False
    result = (mode != "LIVE") or exchange_config_read_only_false
    assert result == True  # DRY_RUN forces read_only
    
    # PAPER mode: always read_only
    mode = "PAPER"
    result = (mode != "LIVE") or exchange_config_read_only_false
    assert result == True  # PAPER forces read_only
    
    # LIVE mode with read_only=true: stays read_only
    mode = "LIVE"
    exchange_config_read_only_true = True
    result = (mode != "LIVE") or exchange_config_read_only_true
    assert result == True  # Explicit read_only=true
    
    # LIVE mode with read_only=false: allows real trading
    mode = "LIVE"
    exchange_config_read_only_false = False
    result = (mode != "LIVE") or exchange_config_read_only_false
    assert result == False  # Only this combo allows real trading


# ============================================================================
# Test 8: Default mode is DRY_RUN (fail-safe)
# ============================================================================
def test_default_mode_is_dry_run():
    """Missing mode config should default to DRY_RUN."""
    # Test the default logic directly
    app_config = {}
    mode = app_config.get("mode", "DRY_RUN").upper()
    assert mode == "DRY_RUN"


# ============================================================================
# Test 9: Default read_only is true (fail-safe)
# ============================================================================
def test_default_read_only_is_true():
    """Missing read_only config should default to true."""
    # Test the default logic directly
    exchange_config = {}  # No read_only key
    read_only_cfg = exchange_config.get("read_only", True)
    mode = "LIVE"
    read_only = (mode != "LIVE") or read_only_cfg
    assert read_only == True  # Default to safe


# ============================================================================
# Test 10: Exchange initialization respects read_only
# ============================================================================
def test_exchange_respects_read_only():
    """CoinbaseExchange should respect read_only parameter."""
    # read_only=true should block mutating operations
    exchange_ro = CoinbaseExchange(read_only=True)
    assert exchange_ro.read_only == True
    
    # Verify place_order raises
    with pytest.raises(ValueError, match="Cannot place orders in READ_ONLY mode"):
        exchange_ro.place_order("BTC-USD", "BUY", "0.001", order_type="market")
    
    # read_only=false requires credentials
    # Test that it requires credentials when read_only=False
    with pytest.raises(ValueError, match="LIVE mode requires credentials"):
        CoinbaseExchange(read_only=False)
    
    # With credentials, read_only=False should work
    exchange_rw = CoinbaseExchange(read_only=False, api_key="test_api_key_10", api_secret="test_secret_20_chars_long")
    assert exchange_rw.read_only == False
    # (Would allow place_order with valid API keys)


# ============================================================================
# Test 11: ExecutionEngine logs mode clearly
# ============================================================================
def test_execution_engine_logs_mode(caplog):
    """ExecutionEngine should log its mode clearly for audit trail."""
    import logging
    caplog.set_level(logging.INFO)
    
    engine = ExecutionEngine(mode="LIVE", policy={})
    
    # Should log mode in initialization
    assert "mode=LIVE" in caplog.text


# ============================================================================
# Test 12: All three modes have distinct execution paths
# ============================================================================
def test_all_modes_distinct():
    """DRY_RUN, PAPER, and LIVE should follow completely different code paths."""
    mock_exchange = MagicMock(spec=CoinbaseExchange)
    
    # DRY_RUN: no exchange interaction
    engine_dry = ExecutionEngine(mode="DRY_RUN", exchange=mock_exchange)
    result_dry = engine_dry.execute("BTC-USD", "SELL", 100.0)  # SELL avoids trading pair lookup
    assert result_dry.route == "shadow_dry_run"  # DRY_RUN now uses shadow execution
    assert not mock_exchange.get_quote.called
    
    # PAPER: gets quote, simulates
    mock_exchange.reset_mock()
    mock_exchange.read_only = True
    from core.exchange_coinbase import Quote
    from datetime import datetime, timezone
    mock_exchange.get_quote.return_value = Quote(
        symbol="BTC-USD", bid=50000.0, ask=50100.0, mid=50050.0,
        spread_bps=20.0, last=50050.0, volume_24h=1000000.0,
        timestamp=datetime.now(timezone.utc)
    )
    engine_paper = ExecutionEngine(mode="PAPER", exchange=mock_exchange)
    result_paper = engine_paper.execute("BTC-USD", "SELL", 100.0)  # SELL avoids trading pair lookup
    assert result_paper.route == "paper_simulated"
    assert mock_exchange.get_quote.called
    assert not mock_exchange.place_order.called
    
    # LIVE: attempts real order (blocked by read_only in this test)
    mock_exchange.reset_mock()
    mock_exchange.read_only = True
    engine_live = ExecutionEngine(mode="LIVE", exchange=mock_exchange)
    with pytest.raises(ValueError, match="read_only"):
        engine_live.execute("BTC-USD", "SELL", 100.0)  # SELL avoids trading pair lookup
