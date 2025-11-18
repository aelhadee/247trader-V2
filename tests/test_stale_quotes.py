"""
Tests for stale quote rejection feature.

Validates that quotes older than max_quote_age_seconds are rejected
before trading decisions to prevent execution on outdated market data.
"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import Mock, patch
from dataclasses import dataclass

from core.execution import ExecutionEngine


@dataclass
class MockQuote:
    """Mock Quote for testing"""
    symbol: str
    bid: float
    ask: float
    mid: float
    spread_bps: float
    last: float
    volume_24h: float
    timestamp: datetime


class TestQuoteFreshnessValidation:
    """Test quote freshness validation logic"""
    
    def setup_method(self):
        """Setup test fixtures"""
        self.policy = {
            "microstructure": {
                "max_quote_age_seconds": 30,
                "max_spread_bps": 100
            },
            "risk": {
                "min_trade_notional_usd": 100
            },
            "execution": {
                "maker_fee_bps": 40,
                "taker_fee_bps": 60
            }
        }
        
        # Create mock exchange
        self.mock_exchange = Mock()
        
        # Create engine
        self.engine = ExecutionEngine(
            mode="DRY_RUN",
            exchange=self.mock_exchange,
            policy=self.policy
        )
    
    def test_fresh_quote_passes(self):
        """Fresh quote (< 30s) passes validation"""
        fresh_quote = MockQuote(
            symbol="BTC-USD",
            bid=50000.0,
            ask=50010.0,
            mid=50005.0,
            spread_bps=20.0,
            last=50005.0,
            volume_24h=1000000.0,
            timestamp=datetime.now(timezone.utc) - timedelta(seconds=10)
        )
        
        error = self.engine._validate_quote_freshness(fresh_quote, "BTC-USD")
        assert error is None
    
    def test_stale_quote_rejected(self):
        """Stale quote (> 30s) is rejected"""
        stale_quote = MockQuote(
            symbol="BTC-USD",
            bid=50000.0,
            ask=50010.0,
            mid=50005.0,
            spread_bps=20.0,
            last=50005.0,
            volume_24h=1000000.0,
            timestamp=datetime.now(timezone.utc) - timedelta(seconds=45)
        )
        
        error = self.engine._validate_quote_freshness(stale_quote, "BTC-USD")
        assert error is not None
        assert "too stale" in error.lower()
        assert "45" in error  # Should mention age
        assert "30" in error  # Should mention threshold
    
    def test_boundary_case_exactly_30_seconds(self):
        """Quote at exactly 30s boundary"""
        boundary_quote = MockQuote(
            symbol="BTC-USD",
            bid=50000.0,
            ask=50010.0,
            mid=50005.0,
            spread_bps=20.0,
            last=50005.0,
            volume_24h=1000000.0,
            timestamp=datetime.now(timezone.utc) - timedelta(seconds=30)
        )
        
        error = self.engine._validate_quote_freshness(boundary_quote, "BTC-USD")
        # At exactly 30s, should be rejected (age == max is too old)
        assert error is not None
        assert "too stale" in error.lower()
    
    def test_very_stale_quote(self):
        """Very old quote (5 minutes) is rejected"""
        very_stale_quote = MockQuote(
            symbol="BTC-USD",
            bid=50000.0,
            ask=50010.0,
            mid=50005.0,
            spread_bps=20.0,
            last=50005.0,
            volume_24h=1000000.0,
            timestamp=datetime.now(timezone.utc) - timedelta(minutes=5)
        )
        
        error = self.engine._validate_quote_freshness(very_stale_quote, "BTC-USD")
        assert error is not None
        assert "300" in error or "5.0" in error  # Should show large age
    
    def test_future_timestamp_rejected(self):
        """Quote with future timestamp (clock skew) is rejected"""
        future_quote = MockQuote(
            symbol="BTC-USD",
            bid=50000.0,
            ask=50010.0,
            mid=50005.0,
            spread_bps=20.0,
            last=50005.0,
            volume_24h=1000000.0,
            timestamp=datetime.now(timezone.utc) + timedelta(seconds=10)
        )
        
        error = self.engine._validate_quote_freshness(future_quote, "BTC-USD")
        assert error is not None
        assert "future" in error.lower() or "ahead" in error.lower()
        assert "clock skew" in error.lower()
    
    def test_none_quote_rejected(self):
        """None quote is rejected"""
        error = self.engine._validate_quote_freshness(None, "BTC-USD")
        assert error is not None
        assert "none" in error.lower()
    
    def test_quote_missing_timestamp_rejected(self):
        """Quote without timestamp is rejected"""
        quote_no_timestamp = Mock()
        quote_no_timestamp.timestamp = None
        
        error = self.engine._validate_quote_freshness(quote_no_timestamp, "BTC-USD")
        assert error is not None
        assert "missing timestamp" in error.lower()
    
    def test_naive_timestamp_handled(self):
        """Naive timestamp (no timezone) is assumed UTC"""
        # Use timezone-aware UTC timestamp to avoid clock skew issues
        naive_timestamp = datetime.now(timezone.utc) - timedelta(seconds=10)
        # Create truly naive timestamp by removing tzinfo
        naive_timestamp = naive_timestamp.replace(tzinfo=None)
        
        naive_quote = MockQuote(
            symbol="BTC-USD",
            bid=50000.0,
            ask=50010.0,
            mid=50005.0,
            spread_bps=20.0,
            last=50005.0,
            volume_24h=1000000.0,
            timestamp=naive_timestamp  # No timezone
        )
        
        # Should not error - assumes UTC and accepts fresh quotes
        error = self.engine._validate_quote_freshness(naive_quote, "BTC-USD")
        assert error is None
    
    def test_custom_threshold(self):
        """Different staleness threshold from policy"""
        custom_policy = {
            "microstructure": {
                "max_quote_age_seconds": 60,  # 60s instead of 30s
                "max_spread_bps": 100
            },
            "risk": {
                "min_trade_notional_usd": 100
            },
            "execution": {}
        }
        
        engine = ExecutionEngine(
            mode="DRY_RUN",
            exchange=self.mock_exchange,
            policy=custom_policy
        )
        
        # 45s quote should pass with 60s threshold
        quote_45s = MockQuote(
            symbol="BTC-USD",
            bid=50000.0,
            ask=50010.0,
            mid=50005.0,
            spread_bps=20.0,
            last=50005.0,
            volume_24h=1000000.0,
            timestamp=datetime.now(timezone.utc) - timedelta(seconds=45)
        )
        
        error = engine._validate_quote_freshness(quote_45s, "BTC-USD")
        assert error is None
        
        # 70s quote should fail
        quote_70s = MockQuote(
            symbol="BTC-USD",
            bid=50000.0,
            ask=50010.0,
            mid=50005.0,
            spread_bps=20.0,
            last=50005.0,
            volume_24h=1000000.0,
            timestamp=datetime.now(timezone.utc) - timedelta(seconds=70)
        )
        
        error = engine._validate_quote_freshness(quote_70s, "BTC-USD")
        assert error is not None


class TestPreviewOrderStalenessCheck:
    """Test preview_order rejects stale quotes"""
    
    def setup_method(self):
        """Setup test fixtures"""
        self.policy = {
            "microstructure": {
                "max_quote_age_seconds": 30,
                "max_spread_bps": 100
            },
            "risk": {
                "min_trade_notional_usd": 100
            },
            "execution": {
                "maker_fee_bps": 40,
                "taker_fee_bps": 60
            }
        }
        
        self.mock_exchange = Mock()
        self.engine = ExecutionEngine(
            mode="DRY_RUN",
            exchange=self.mock_exchange,
            policy=self.policy
        )
    
    def test_preview_rejects_stale_quote(self):
        """preview_order rejects stale quote"""
        stale_quote = MockQuote(
            symbol="BTC-USD",
            bid=50000.0,
            ask=50010.0,
            mid=50005.0,
            spread_bps=20.0,
            last=50005.0,
            volume_24h=1000000.0,
            timestamp=datetime.now(timezone.utc) - timedelta(seconds=45)
        )
        
        self.mock_exchange.get_quote.return_value = stale_quote
        
        result = self.engine.preview_order("BTC-USD", "BUY", 1000.0)
        
        assert result["success"] is False
        assert "too stale" in result["error"].lower()
    
    def test_preview_accepts_fresh_quote(self):
        """preview_order accepts fresh quote"""
        fresh_quote = MockQuote(
            symbol="BTC-USD",
            bid=50000.0,
            ask=50010.0,
            mid=50005.0,
            spread_bps=20.0,
            last=50005.0,
            volume_24h=1000000.0,
            timestamp=datetime.now(timezone.utc) - timedelta(seconds=10)
        )
        
        self.mock_exchange.get_quote.return_value = fresh_quote
        
        # Mock orderbook for depth check
        mock_orderbook = Mock()
        mock_orderbook.ask_depth_usd = 10000.0
        mock_orderbook.bid_depth_usd = 10000.0
        self.mock_exchange.get_orderbook.return_value = mock_orderbook
        
        result = self.engine.preview_order("BTC-USD", "BUY", 1000.0)
        
        # Should not fail on staleness (might fail on other checks)
        if not result["success"]:
            assert "too stale" not in result.get("error", "").lower()


class TestExecuteLiveStalenessCheck:
    """Test _execute_live rejects stale quotes"""
    
    def setup_method(self):
        """Setup test fixtures"""
        self.policy = {
            "microstructure": {
                "max_quote_age_seconds": 30,
                "max_spread_bps": 100
            },
            "risk": {
                "min_trade_notional_usd": 100
            },
            "execution": {
                "maker_fee_bps": 40,
                "taker_fee_bps": 60
            }
        }
        
        self.mock_exchange = Mock()
        self.mock_state_store = Mock()
        
        self.engine = ExecutionEngine(
            mode="LIVE",
            exchange=self.mock_exchange,
            policy=self.policy,
            state_store=self.mock_state_store
        )
    
    def test_execute_live_rejects_stale_quote(self):
        """_execute_live rejects stale quote before execution"""
        stale_quote = MockQuote(
            symbol="BTC-USD",
            bid=50000.0,
            ask=50010.0,
            mid=50005.0,
            spread_bps=20.0,
            last=50005.0,
            volume_24h=1000000.0,
            timestamp=datetime.now(timezone.utc) - timedelta(seconds=50)
        )
        
        self.mock_exchange.get_quote.return_value = stale_quote
        self.mock_exchange.read_only = False
        
        # Mock state store and order state machine to allow new order
        self.engine.state_store.has_open_order = Mock(return_value=False)
        self.engine.order_state_machine.is_duplicate = Mock(return_value=False)
        self.engine.order_state_machine.create_order = Mock()
        
        result = self.engine._execute_live(
            symbol="BTC-USD",
            side="BUY",
            size_usd=1000.0,
            client_order_id="test_stale_order_456",
            max_slippage_bps=50.0
        )
        
        assert result.success is False
        assert "too stale" in result.error.lower()
        assert result.route == "live_rejected"
    
    def test_execute_live_accepts_fresh_quote(self):
        """_execute_live proceeds with fresh quote"""
        fresh_quote = MockQuote(
            symbol="BTC-USD",
            bid=50000.0,
            ask=50010.0,
            mid=50005.0,
            spread_bps=20.0,
            last=50005.0,
            volume_24h=1000000.0,
            timestamp=datetime.now(timezone.utc) - timedelta(seconds=5)
        )
        
        self.mock_exchange.get_quote.return_value = fresh_quote
        self.mock_exchange.read_only = False
        
        # Mock preview to pass
        with patch.object(self.engine, 'preview_order') as mock_preview:
            mock_preview.return_value = {
                "success": False,
                "error": "Some other reason"  # Will fail later, but not on staleness
            }
            
            result = self.engine._execute_live(
                symbol="BTC-USD",
                side="BUY",
                size_usd=1000.0,
                client_order_id="test_order_123",
                max_slippage_bps=50.0
            )
            
            # Should not fail on staleness
            if not result.success:
                assert "too stale" not in result.error.lower()


class TestCircuitBreakerThreshold:
    """Test circuit breaker staleness threshold (60s from policy)"""
    
    def test_circuit_breaker_threshold_configured(self):
        """Circuit breaker has separate 60s threshold"""
        # This is for documentation - circuit breaker threshold
        # exists in policy.yaml at circuit_breakers.max_quote_age_seconds: 60
        # ExecutionEngine uses microstructure.max_quote_age_seconds: 30
        # for execution-time validation
        
        policy = {
            "microstructure": {
                "max_quote_age_seconds": 30  # Execution threshold
            },
            "circuit_breakers": {
                "max_quote_age_seconds": 60  # Circuit breaker threshold
            },
            "risk": {
                "min_trade_notional_usd": 100
            },
            "execution": {}
        }
        
        mock_exchange = Mock()
        engine = ExecutionEngine(
            mode="DRY_RUN",
            exchange=mock_exchange,
            policy=policy
        )
        
        # Execution uses 30s threshold
        assert engine.max_quote_age_seconds == 30
        
        # Note: Circuit breaker threshold (60s) would be enforced
        # in core/risk.py RiskEngine, not ExecutionEngine


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
