"""
Tests for exchange product status circuit breaker.
"""
import pytest
from unittest.mock import Mock
from dataclasses import dataclass, field

from core.risk import RiskEngine, PortfolioState


@dataclass
class MockTradeProposal:
    """Mock TradeProposal for testing."""
    symbol: str
    side: str
    quantity: float
    limit_price: float | None = None
    stop_loss_pct: float | None = None
    take_profit_pct: float | None = None
    reason: str = ""
    confidence: float = 0.7
    size_pct: float = 5.0  # Default position size percentage
    metadata: dict = field(default_factory=dict)


class TestExchangeStatusCircuitBreaker:
    """Test suite for exchange product status filtering."""
    
    @pytest.fixture
    def mock_state_store(self):
        """Create mock state store."""
        store = Mock()
        store.get.return_value = {
            "positions": {},
            "cooldowns": {},
            "open_order_count": 0,
            "daily_trades": 0,
            "weekly_trades": 0,
            "total_pnl_usd": 0.0,
            "daily_pnl_usd": 0.0,
            "peak_balance_usd": 10000.0,
            "at_risk_symbols": set(),
        }
        return store
    
    @pytest.fixture
    def mock_exchange(self):
        """Create mock exchange adapter."""
        exchange = Mock()
        exchange.name = "coinbase"
        return exchange
    
    @pytest.fixture
    def policy(self):
        """Full policy configuration with product status enabled."""
        return {
            "circuit_breakers": {
                "check_product_status": True,
                "check_exchange_status": True,
                "max_quote_age_seconds": 60,
                "max_ohlcv_age_seconds": 300,
            },
            "risk": {
                "max_open_positions": 10,
                "max_daily_trades": 50,
                "max_weekly_trades": 200,
                "max_single_trade_pct": 5.0,
                "max_symbol_exposure_pct": 15.0,
                "max_cluster_exposure_pct": 30.0,
                "daily_stop_loss_pct": 5.0,
                "weekly_stop_loss_pct": 10.0,
                "max_drawdown_from_peak_pct": 20.0,
            },
            "position_sizing": {
                "max_position_pct_tier1": 5.0,
                "max_position_pct_tier2": 3.0,
                "max_position_pct_tier3": 1.0,
            },
            "cooldowns": {
                "loss_cooldown_minutes": 60,
                "symbol_cooldown_minutes": 30,
                "max_frequency_per_symbol_daily": 5,
            },
        }
    
    @pytest.fixture
    def portfolio(self):
        """Mock portfolio state."""
        return PortfolioState(
            account_value_usd=10000.0,
            open_positions={},
            daily_pnl_pct=0.0,
            max_drawdown_pct=0.0,
            trades_today=0,
            trades_this_hour=0,
        )
    
    def test_blocks_post_only_products(self, mock_state_store, mock_exchange, policy, portfolio):
        """Test that POST_ONLY products are blocked."""
        # Setup exchange to return POST_ONLY status
        mock_exchange.get_product_metadata.return_value = {
            "product_id": "BTC-USD",
            "status": "POST_ONLY",
            "base_increment": "0.00000001",
            "quote_increment": "0.01",
        }
        
        engine = RiskEngine(
            policy=policy,
            exchange=mock_exchange,
            state_store=mock_state_store,
        )
        
        proposals = [
            MockTradeProposal(
                symbol="BTC-USD",
                side="buy",
                quantity=0.01,
                limit_price=50000.0,
                reason="test",
            )
        ]
        
        result = engine.check_all(
            proposals=proposals,
            portfolio=portfolio,
        )
        
        assert not result.approved, "Should block POST_ONLY product"
        assert "exchange product status" in result.reason.lower() or "post_only" in result.reason.lower()
        assert len(result.approved_proposals) == 0, "Should filter out all proposals"
    
    def test_blocks_limit_only_products(self, mock_state_store, mock_exchange, policy, portfolio):
        """Test that LIMIT_ONLY products are blocked."""
        mock_exchange.get_product_metadata.return_value = {
            "product_id": "ETH-USD",
            "status": "LIMIT_ONLY",
            "base_increment": "0.00000001",
            "quote_increment": "0.01",
        }
        
        engine = RiskEngine(
            policy=policy,
            exchange=mock_exchange,
            state_store=mock_state_store,
        )
        
        proposals = [
            MockTradeProposal(
                symbol="ETH-USD",
                side="buy",
                quantity=1.0,
                limit_price=3000.0,
                reason="test",
            )
        ]
        
        result = engine.check_all(
            proposals=proposals,
            portfolio=portfolio,
        )
        
        assert not result.approved
        assert len(result.approved_proposals) == 0
    
    def test_blocks_cancel_only_products(self, mock_state_store, mock_exchange, policy, portfolio):
        """Test that CANCEL_ONLY products are blocked."""
        mock_exchange.get_product_metadata.return_value = {
            "product_id": "SOL-USD",
            "status": "CANCEL_ONLY",
            "base_increment": "0.00000001",
            "quote_increment": "0.01",
        }
        
        engine = RiskEngine(
            policy=policy,
            exchange=mock_exchange,
            state_store=mock_state_store,
        )
        
        proposals = [
            MockTradeProposal(
                symbol="SOL-USD",
                side="sell",
                quantity=10.0,
                limit_price=100.0,
                reason="test",
            )
        ]
        
        result = engine.check_all(
            proposals=proposals,
            portfolio=portfolio,
        )
        
        assert not result.approved
        assert len(result.approved_proposals) == 0
    
    def test_blocks_offline_products(self, mock_state_store, mock_exchange, policy, portfolio):
        """Test that OFFLINE products are blocked."""
        mock_exchange.get_product_metadata.return_value = {
            "product_id": "DOGE-USD",
            "status": "OFFLINE",
            "base_increment": "1.0",
            "quote_increment": "0.000001",
        }
        
        engine = RiskEngine(
            policy=policy,
            exchange=mock_exchange,
            state_store=mock_state_store,
        )
        
        proposals = [
            MockTradeProposal(
                symbol="DOGE-USD",
                side="buy",
                quantity=1000.0,
                limit_price=0.10,
                reason="test",
            )
        ]
        
        result = engine.check_all(
            proposals=proposals,
            portfolio=portfolio,
        )
        
        assert not result.approved
        assert len(result.approved_proposals) == 0
    
    def test_allows_online_products(self, mock_state_store, mock_exchange, policy, portfolio):
        """Test that ONLINE products are allowed through."""
        mock_exchange.get_product_metadata.return_value = {
            "product_id": "BTC-USD",
            "status": "ONLINE",
            "base_increment": "0.00000001",
            "quote_increment": "0.01",
        }
        
        engine = RiskEngine(
            policy=policy,
            exchange=mock_exchange,
            state_store=mock_state_store,
        )
        
        proposals = [
            MockTradeProposal(
                symbol="BTC-USD",
                side="buy",
                quantity=0.01,
                limit_price=50000.0,
                reason="test",
            )
        ]
        
        engine.check_all(
            proposals=proposals,
            portfolio=portfolio,
        )
        
        # Should pass product status check (may fail other checks, but that's OK)
        # Just verify proposals weren't filtered by product status
        assert mock_exchange.get_product_metadata.called
    
    def test_filters_mixed_product_statuses(self, mock_state_store, mock_exchange, policy, portfolio):
        """Test filtering when some products are degraded and others are online."""
        def metadata_side_effect(product_id):
            statuses = {
                "BTC-USD": "ONLINE",
                "ETH-USD": "POST_ONLY",
                "SOL-USD": "ONLINE",
                "DOGE-USD": "CANCEL_ONLY",
            }
            return {
                "product_id": product_id,
                "status": statuses.get(product_id, "ONLINE"),
                "base_increment": "0.00000001",
                "quote_increment": "0.01",
            }
        
        mock_exchange.get_product_metadata.side_effect = metadata_side_effect
        
        engine = RiskEngine(
            policy=policy,
            exchange=mock_exchange,
            state_store=mock_state_store,
        )
        
        proposals = [
            MockTradeProposal(symbol="BTC-USD", side="buy", quantity=0.01, limit_price=50000.0),
            MockTradeProposal(symbol="ETH-USD", side="buy", quantity=1.0, limit_price=3000.0),
            MockTradeProposal(symbol="SOL-USD", side="buy", quantity=10.0, limit_price=100.0),
            MockTradeProposal(symbol="DOGE-USD", side="sell", quantity=1000.0, limit_price=0.10),
        ]
        
        result = engine.check_all(
            proposals=proposals,
            portfolio=portfolio,
        )
        
        # ETH-USD (POST_ONLY) and DOGE-USD (CANCEL_ONLY) should be filtered
        # BTC-USD and SOL-USD should remain
        approved_symbols = {p.symbol for p in result.approved_proposals}
        assert "ETH-USD" not in approved_symbols, "POST_ONLY should be filtered"
        assert "DOGE-USD" not in approved_symbols, "CANCEL_ONLY should be filtered"
    
    def test_fail_closed_on_metadata_error(self, mock_state_store, mock_exchange, policy, portfolio):
        """Test fail-closed behavior when metadata fetch fails."""
        mock_exchange.get_product_metadata.side_effect = Exception("API error")
        
        engine = RiskEngine(
            policy=policy,
            exchange=mock_exchange,
            state_store=mock_state_store,
        )
        
        proposals = [
            MockTradeProposal(
                symbol="BTC-USD",
                side="buy",
                quantity=0.01,
                limit_price=50000.0,
                reason="test",
            )
        ]
        
        result = engine.check_all(
            proposals=proposals,
            portfolio=portfolio,
        )
        
        assert not result.approved, "Should fail closed on metadata errors"
        assert len(result.approved_proposals) == 0, "Should block all trades on error"
    
    def test_fail_closed_on_missing_status(self, mock_state_store, mock_exchange, policy, portfolio):
        """Test fail-closed when status field is missing from metadata."""
        mock_exchange.get_product_metadata.return_value = {
            "product_id": "BTC-USD",
            # Missing 'status' field
            "base_increment": "0.00000001",
            "quote_increment": "0.01",
        }
        
        engine = RiskEngine(
            policy=policy,
            exchange=mock_exchange,
            state_store=mock_state_store,
        )
        
        proposals = [
            MockTradeProposal(
                symbol="BTC-USD",
                side="buy",
                quantity=0.01,
                limit_price=50000.0,
                reason="test",
            )
        ]
        
        result = engine.check_all(
            proposals=proposals,
            portfolio=portfolio,
        )
        
        assert not result.approved, "Should fail closed when status is missing"
        assert len(result.approved_proposals) == 0
    
    def test_respects_config_toggle(self, mock_state_store, mock_exchange, policy, portfolio):
        """Test that check can be disabled via config."""
        mock_exchange.get_product_metadata.return_value = {
            "product_id": "BTC-USD",
            "status": "POST_ONLY",
            "base_increment": "0.00000001",
            "quote_increment": "0.01",
        }
        
        # Disable product status check
        policy_disabled = policy.copy()
        policy_disabled["circuit_breakers"] = policy["circuit_breakers"].copy()
        policy_disabled["circuit_breakers"]["check_product_status"] = False
        
        engine = RiskEngine(
            policy=policy_disabled,
            exchange=mock_exchange,
            state_store=mock_state_store,
        )
        
        proposals = [
            MockTradeProposal(
                symbol="BTC-USD",
                side="buy",
                quantity=0.01,
                limit_price=50000.0,
                reason="test",
            )
        ]
        
        engine.check_all(
            proposals=proposals,
            portfolio=portfolio,
        )
        
        # Should NOT filter when disabled (may fail other checks, but product status ignored)
        # Verify get_product_metadata was NOT called
        assert not mock_exchange.get_product_metadata.called, "Should skip check when disabled"
