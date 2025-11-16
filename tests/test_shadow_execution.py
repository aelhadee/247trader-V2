"""
Tests for Shadow DRY_RUN Mode

Validates comprehensive execution logging without order submission.
"""

import pytest
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import Mock, MagicMock, patch

from core.execution import ExecutionEngine
from core.exchange_coinbase import Quote
from core.shadow_execution import ShadowExecutionLogger, create_shadow_order


@pytest.fixture
def shadow_log_file(tmp_path):
    """Temporary shadow log file"""
    return tmp_path / "shadow_test.jsonl"


@pytest.fixture
def shadow_logger(shadow_log_file):
    """Shadow logger with temp file"""
    return ShadowExecutionLogger(str(shadow_log_file))


@pytest.fixture
def mock_exchange():
    """Mock exchange with quote data"""
    exchange = Mock()
    exchange.read_only = True
    
    # Default quote
    quote = Quote(
        symbol="BTC-USD",
        bid=50000.0,
        ask=50050.0,
        spread_bps=10.0,
        timestamp=datetime.now(timezone.utc)
    )
    exchange.get_quote = Mock(return_value=quote)
    
    # Default orderbook
    orderbook = {
        'bids': [
            {'price': '50000', 'size': '1.0'},
            {'price': '49990', 'size': '2.0'},
        ],
        'asks': [
            {'price': '50050', 'size': '1.0'},
            {'price': '50060', 'size': '2.0'},
        ]
    }
    exchange.get_product_book = Mock(return_value=orderbook)
    
    return exchange


@pytest.fixture
def execution_engine(mock_exchange, shadow_log_file):
    """Execution engine in DRY_RUN mode"""
    policy = {
        "execution": {
            "maker_fee_bps": 40,
            "taker_fee_bps": 60,
            "preferred_quote_currencies": ["USD", "USDC"],
            "default_order_type": "limit_post_only"
        },
        "microstructure": {
            "max_expected_slippage_bps": 50.0,
            "max_spread_bps": 100.0,
            "max_quote_age_seconds": 30
        },
        "risk": {
            "min_trade_notional_usd": 100.0
        }
    }
    
    engine = ExecutionEngine(
        mode="DRY_RUN",
        exchange=mock_exchange,
        policy=policy
    )
    
    # Override shadow logger path
    engine.shadow_logger = ShadowExecutionLogger(str(shadow_log_file))
    
    return engine


def test_shadow_logger_creation(shadow_log_file, shadow_logger):
    """Test shadow logger creates log file"""
    assert shadow_log_file.exists()
    assert shadow_log_file.is_file()


def test_shadow_order_logging(shadow_logger):
    """Test logging a shadow order"""
    quote = Quote(
        symbol="BTC-USD",
        bid=50000.0,
        ask=50050.0,
        spread_bps=10.0,
        timestamp=datetime.now(timezone.utc)
    )
    
    shadow_order = create_shadow_order(
        symbol="BTC-USD",
        side="BUY",
        size_usd=1000.0,
        size_units=0.02,
        quote=quote,
        intended_route="limit_post",
        intended_price=50050.0,
        expected_slippage_bps=5.0,
        expected_fees_usd=4.0,
        tier="T1",
        client_order_id="test_123",
        passed_spread_check=True,
        passed_depth_check=True,
        would_place=True,
        rejection_reason=None,
        config_hash="abc123",
        confidence=0.75,
        conviction=0.80,
        orderbook_depth_20bps_usd=100000.0
    )
    
    shadow_logger.log_order(shadow_order)
    
    # Read back and verify
    log_file = Path(shadow_logger.log_file)
    lines = log_file.read_text().strip().split('\n')
    assert len(lines) == 1
    
    logged = json.loads(lines[0])
    assert logged['symbol'] == "BTC-USD"
    assert logged['side'] == "BUY"
    assert logged['size_usd'] == 1000.0
    assert logged['would_place'] is True
    assert logged['tier'] == "T1"


def test_shadow_rejection_logging(shadow_logger):
    """Test logging order rejection"""
    shadow_logger.log_rejection(
        symbol="BTC-USD",
        side="BUY",
        size_usd=1000.0,
        reason="Spread too wide",
        context={"spread_bps": 150.0}
    )
    
    # Read back
    log_file = Path(shadow_logger.log_file)
    lines = log_file.read_text().strip().split('\n')
    assert len(lines) == 1
    
    logged = json.loads(lines[0])
    assert logged['type'] == "rejection"
    assert logged['reason'] == "Spread too wide"
    assert logged['context']['spread_bps'] == 150.0


def test_shadow_stats(shadow_logger):
    """Test shadow logger statistics"""
    quote = Quote(
        symbol="BTC-USD",
        bid=50000.0,
        ask=50050.0,
        spread_bps=10.0,
        timestamp=datetime.now(timezone.utc)
    )
    
    # Log 2 placements and 1 rejection
    for i in range(2):
        shadow_order = create_shadow_order(
            symbol="BTC-USD",
            side="BUY",
            size_usd=1000.0,
            size_units=0.02,
            quote=quote,
            intended_route="limit_post",
            intended_price=50050.0,
            expected_slippage_bps=5.0,
            expected_fees_usd=4.0,
            tier="T1",
            client_order_id=f"test_{i}",
            passed_spread_check=True,
            passed_depth_check=True,
            would_place=True,
            rejection_reason=None,
            config_hash="abc123"
        )
        shadow_logger.log_order(shadow_order)
    
    shadow_logger.log_rejection(
        symbol="BTC-USD",
        side="BUY",
        size_usd=1000.0,
        reason="Spread too wide"
    )
    
    stats = shadow_logger.get_stats()
    assert stats['total'] == 3
    assert stats['would_place'] == 2
    assert stats['rejected'] == 1
    assert 'Spread too wide' in stats['rejection_reasons']


def test_shadow_execution_basic(execution_engine, shadow_log_file):
    """Test basic shadow execution"""
    result = execution_engine.execute(
        symbol="BTC-USD",
        side="BUY",
        size_usd=1000.0,
        tier=1
    )
    
    assert result.success is True
    assert result.route == "shadow_dry_run"
    assert result.filled_size == 0.0  # No actual fill
    assert result.order_id.startswith("shadow_")
    
    # Check log file exists and has entry
    assert shadow_log_file.exists()
    lines = shadow_log_file.read_text().strip().split('\n')
    assert len(lines) == 1
    
    logged = json.loads(lines[0])
    assert logged['symbol'] == "BTC-USD"
    assert logged['side'] == "BUY"
    assert logged['size_usd'] == 1000.0


def test_shadow_execution_with_fresh_quote(execution_engine, shadow_log_file):
    """Test shadow execution logs quote details"""
    result = execution_engine.execute(
        symbol="BTC-USD",
        side="BUY",
        size_usd=1000.0,
        tier=1,
        confidence=0.8
    )
    
    assert result.success is True
    
    # Check logged details
    lines = shadow_log_file.read_text().strip().split('\n')
    logged = json.loads(lines[0])
    
    # Quote details
    assert logged['quote_bid'] == 50000.0
    assert logged['quote_ask'] == 50050.0
    assert logged['quote_spread_bps'] == 10.0
    assert logged['quote_age_ms'] >= 0  # Should be recent
    
    # Execution plan
    assert logged['intended_price'] == 50050.0  # Buy at ask
    assert logged['intended_route'] == "limit_post"
    assert logged['expected_slippage_bps'] == 5.0  # Half spread
    assert logged['expected_fees_usd'] > 0
    
    # Risk context
    assert logged['tier'] == "T1"
    assert logged['confidence'] == 0.8
    
    # Liquidity checks
    assert logged['passed_spread_check'] is True
    assert logged['passed_depth_check'] is True


def test_shadow_execution_stale_quote(execution_engine, mock_exchange, shadow_log_file):
    """Test shadow execution rejects stale quote"""
    # Mock stale quote
    stale_quote = Quote(
        symbol="BTC-USD",
        bid=50000.0,
        ask=50050.0,
        spread_bps=10.0,
        timestamp=datetime.now(timezone.utc) - timedelta(seconds=60)  # 60s old
    )
    mock_exchange.get_quote.return_value = stale_quote
    
    result = execution_engine.execute(
        symbol="BTC-USD",
        side="BUY",
        size_usd=1000.0
    )
    
    assert result.success is True  # DRY_RUN always succeeds
    
    # Check logged rejection
    lines = shadow_log_file.read_text().strip().split('\n')
    # Should have 2 entries: shadow order + rejection
    assert len(lines) == 2
    
    shadow_entry = json.loads(lines[0])
    assert shadow_entry['would_place'] is False
    assert "stale" in shadow_entry['rejection_reason'].lower()
    
    rejection_entry = json.loads(lines[1])
    assert rejection_entry['type'] == "rejection"
    assert "stale" in rejection_entry['reason'].lower()


def test_shadow_execution_wide_spread(execution_engine, mock_exchange, shadow_log_file):
    """Test shadow execution rejects wide spread"""
    # Mock wide spread quote
    wide_quote = Quote(
        symbol="BTC-USD",
        bid=50000.0,
        ask=50600.0,  # 120bps spread
        spread_bps=120.0,
        timestamp=datetime.now(timezone.utc)
    )
    mock_exchange.get_quote.return_value = wide_quote
    
    result = execution_engine.execute(
        symbol="BTC-USD",
        side="BUY",
        size_usd=1000.0
    )
    
    assert result.success is True
    
    # Check rejection logged
    lines = shadow_log_file.read_text().strip().split('\n')
    assert len(lines) == 2
    
    shadow_entry = json.loads(lines[0])
    assert shadow_entry['would_place'] is False
    assert "spread" in shadow_entry['rejection_reason'].lower()
    assert shadow_entry['passed_spread_check'] is False


def test_shadow_execution_insufficient_depth(execution_engine, mock_exchange, shadow_log_file):
    """Test shadow execution rejects insufficient depth"""
    # Mock thin orderbook
    thin_book = {
        'bids': [{'price': '50000', 'size': '0.001'}],  # Only $50 depth
        'asks': [{'price': '50050', 'size': '0.001'}]
    }
    mock_exchange.get_product_book.return_value = thin_book
    
    result = execution_engine.execute(
        symbol="BTC-USD",
        side="BUY",
        size_usd=1000.0  # Needs $2000 depth (2x multiplier)
    )
    
    assert result.success is True
    
    # Check rejection logged
    lines = shadow_log_file.read_text().strip().split('\n')
    assert len(lines) == 2
    
    shadow_entry = json.loads(lines[0])
    assert shadow_entry['would_place'] is False
    assert "depth" in shadow_entry['rejection_reason'].lower()
    assert shadow_entry['passed_depth_check'] is False


def test_shadow_execution_sell_side(execution_engine, shadow_log_file):
    """Test shadow execution for SELL orders"""
    result = execution_engine.execute(
        symbol="BTC-USD",
        side="SELL",
        size_usd=1000.0
    )
    
    assert result.success is True
    
    # Check logged details
    lines = shadow_log_file.read_text().strip().split('\n')
    logged = json.loads(lines[0])
    
    assert logged['side'] == "SELL"
    assert logged['intended_price'] == 50000.0  # Sell at bid


def test_shadow_execution_quote_failure(execution_engine, mock_exchange, shadow_log_file):
    """Test shadow execution handles quote fetch failure"""
    mock_exchange.get_quote.side_effect = Exception("API error")
    
    result = execution_engine.execute(
        symbol="BTC-USD",
        side="BUY",
        size_usd=1000.0
    )
    
    assert result.success is True  # DRY_RUN always succeeds
    
    # Check rejection logged
    lines = shadow_log_file.read_text().strip().split('\n')
    assert len(lines) == 1
    
    shadow_entry = json.loads(lines[0])
    assert shadow_entry['would_place'] is False
    assert "quote" in shadow_entry['rejection_reason'].lower()


def test_shadow_execution_multiple_orders(execution_engine, shadow_log_file):
    """Test multiple shadow orders are logged"""
    symbols = ["BTC-USD", "ETH-USD", "SOL-USD"]
    
    for symbol in symbols:
        execution_engine.execute(
            symbol=symbol,
            side="BUY",
            size_usd=1000.0
        )
    
    # Check all logged
    lines = shadow_log_file.read_text().strip().split('\n')
    assert len(lines) == 3
    
    logged_symbols = [json.loads(line)['symbol'] for line in lines]
    assert logged_symbols == symbols


def test_shadow_logger_clear(shadow_logger, shadow_log_file):
    """Test clearing shadow log"""
    # Log some orders
    shadow_logger.log_rejection("BTC-USD", "BUY", 1000.0, "test")
    assert len(shadow_log_file.read_text().strip().split('\n')) == 1
    
    # Clear
    shadow_logger.clear_log()
    
    # Should be empty but exist
    assert shadow_log_file.exists()
    content = shadow_log_file.read_text().strip()
    assert content == ""


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
