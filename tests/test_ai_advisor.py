"""
Tests for AI Advisor Module

Validates:
- Schema validation and type safety
- Size clamping (never >1.0)
- Fallback behavior on errors
- Timeout handling
- Response sanitization
- Integration with risk profiles
"""

import pytest
from unittest.mock import Mock, patch
from datetime import datetime, timezone

from ai.schemas import (
    AIAdvisorInput,
    AIAdvisorOutput,
    AIMarketSnapshot,
    AIPortfolioSnapshot,
    AIProposalIn,
    AIProposalDecision,
    RiskMode,
)
from ai.advisor import AIAdvisorService
from ai.model_client import MockClient, create_model_client
from ai.risk_profile import get_risk_profile, apply_risk_profile_to_caps


class TestAISchemas:
    """Test AI data structures and type safety."""
    
    def test_proposal_in_creation(self):
        """Test AIProposalIn dataclass creation."""
        p = AIProposalIn(
            symbol="BTC-USD",
            side="BUY",
            tier="T1",
            conviction=0.75,
            notional=100.0,
            reason="Strong trend signal",
        )
        assert p.symbol == "BTC-USD"
        assert p.conviction == 0.75
    
    def test_market_snapshot_optional_fields(self):
        """Test AIMarketSnapshot with optional fields."""
        m = AIMarketSnapshot(
            regime="trend",
            nav=10000.0,
            exposure_pct=50.0,
        )
        assert m.regime == "trend"
        assert m.drawdown_24h_pct is None
        assert m.realized_vol_24h is None
    
    def test_advisor_input_complete(self):
        """Test complete AIAdvisorInput creation."""
        input_data = AIAdvisorInput(
            run_id="test-123",
            timestamp_iso=datetime.now(timezone.utc).isoformat(),
            market=AIMarketSnapshot(
                regime="chop",
                nav=5000.0,
                exposure_pct=30.0,
                drawdown_24h_pct=-2.5,
                realized_vol_24h=0.035,
            ),
            portfolio=AIPortfolioSnapshot(
                positions={"BTC-USD": 1000.0, "ETH-USD": 500.0},
                realized_pnl_24h=-50.0,
                num_positions=2,
            ),
            proposals=[
                AIProposalIn(
                    symbol="SOL-USD",
                    side="BUY",
                    tier="T2",
                    conviction=0.6,
                    notional=250.0,
                    reason="Breakout signal",
                )
            ],
        )
        assert input_data.run_id == "test-123"
        assert len(input_data.proposals) == 1
        assert input_data.portfolio.num_positions == 2


class TestAIAdvisorService:
    """Test AIAdvisorService core logic."""
    
    def test_advisor_disabled(self):
        """Test advisor does nothing when disabled."""
        advisor = AIAdvisorService(enabled=False)
        
        input_data = self._create_test_input()
        client = MockClient()
        
        output = advisor.advise(input_data, client)
        
        assert output.risk_mode is None
        assert len(output.proposal_decisions) == 0
        assert output.error == "disabled"
    
    def test_advisor_no_proposals(self):
        """Test advisor handles empty proposal list."""
        advisor = AIAdvisorService(enabled=True)
        
        input_data = self._create_test_input(proposals=[])
        client = MockClient()
        
        output = advisor.advise(input_data, client)
        
        assert output.error == "no_proposals"
    
    def test_advisor_accepts_all_proposals(self):
        """Test advisor accepting all proposals at full size."""
        advisor = AIAdvisorService(enabled=True)
        
        input_data = self._create_test_input()
        client = MockClient(fixed_response={
            "risk_mode": "NORMAL",
            "decisions": [
                {
                    "symbol": "BTC-USD",
                    "side": "BUY",
                    "decision": "accept",
                    "size_factor": 1.0,
                    "comment": "High conviction",
                }
            ],
        })
        
        output = advisor.advise(input_data, client)
        
        assert output.risk_mode == "NORMAL"
        assert len(output.proposal_decisions) == 1
        assert output.proposal_decisions[0].decision == "accept"
        assert output.proposal_decisions[0].size_factor == 1.0
    
    def test_advisor_reduces_proposal_size(self):
        """Test advisor reducing proposal size."""
        advisor = AIAdvisorService(enabled=True)
        
        input_data = self._create_test_input()
        client = MockClient(fixed_response={
            "risk_mode": "DEFENSIVE",
            "decisions": [
                {
                    "symbol": "BTC-USD",
                    "side": "BUY",
                    "decision": "reduce",
                    "size_factor": 0.5,
                    "comment": "Uncertain market",
                }
            ],
        })
        
        output = advisor.advise(input_data, client)
        
        assert output.risk_mode == "DEFENSIVE"
        assert output.proposal_decisions[0].decision == "reduce"
        assert output.proposal_decisions[0].size_factor == 0.5
    
    def test_advisor_skips_proposal(self):
        """Test advisor skipping a proposal."""
        advisor = AIAdvisorService(enabled=True)
        
        input_data = self._create_test_input()
        client = MockClient(fixed_response={
            "risk_mode": "NORMAL",
            "decisions": [
                {
                    "symbol": "BTC-USD",
                    "side": "BUY",
                    "decision": "skip",
                    "size_factor": 0.0,
                    "comment": "Low confidence",
                }
            ],
        })
        
        output = advisor.advise(input_data, client)
        
        assert output.proposal_decisions[0].decision == "skip"
        assert output.proposal_decisions[0].size_factor == 0.0
    
    def test_size_factor_clamped_to_max_scale_up(self):
        """Test size_factor is clamped to max_scale_up."""
        advisor = AIAdvisorService(enabled=True, max_scale_up=1.0)
        
        input_data = self._create_test_input()
        client = MockClient(fixed_response={
            "risk_mode": "AGGRESSIVE",
            "decisions": [
                {
                    "symbol": "BTC-USD",
                    "side": "BUY",
                    "decision": "accept",
                    "size_factor": 1.5,  # Try to scale up
                    "comment": "Very bullish",
                }
            ],
        })
        
        output = advisor.advise(input_data, client)
        
        # Should be clamped to 1.0
        assert output.proposal_decisions[0].size_factor == 1.0
    
    def test_negative_size_factor_clamped_to_zero(self):
        """Test negative size_factor is clamped to 0."""
        advisor = AIAdvisorService(enabled=True)
        
        input_data = self._create_test_input()
        client = MockClient(fixed_response={
            "risk_mode": "OFF",
            "decisions": [
                {
                    "symbol": "BTC-USD",
                    "side": "BUY",
                    "decision": "skip",
                    "size_factor": -0.5,  # Invalid negative
                    "comment": "Kill switch",
                }
            ],
        })
        
        output = advisor.advise(input_data, client)
        
        assert output.proposal_decisions[0].size_factor == 0.0
    
    def test_hallucinated_proposals_ignored(self):
        """Test advisor ignores proposals not in input."""
        advisor = AIAdvisorService(enabled=True)
        
        input_data = self._create_test_input()
        client = MockClient(fixed_response={
            "risk_mode": "NORMAL",
            "decisions": [
                {
                    "symbol": "ETH-USD",  # Not in input
                    "side": "BUY",
                    "decision": "accept",
                    "size_factor": 1.0,
                    "comment": "Hallucinated",
                },
                {
                    "symbol": "BTC-USD",  # Valid
                    "side": "BUY",
                    "decision": "accept",
                    "size_factor": 1.0,
                    "comment": "Real proposal",
                }
            ],
        })
        
        output = advisor.advise(input_data, client)
        
        # Only valid proposal should be included
        assert len(output.proposal_decisions) == 1
        assert output.proposal_decisions[0].symbol == "BTC-USD"
    
    def test_invalid_decision_type_treated_as_skip(self):
        """Test invalid decision types are treated as skip."""
        advisor = AIAdvisorService(enabled=True)
        
        input_data = self._create_test_input()
        client = MockClient(fixed_response={
            "risk_mode": "NORMAL",
            "decisions": [
                {
                    "symbol": "BTC-USD",
                    "side": "BUY",
                    "decision": "hold",  # Invalid
                    "size_factor": 1.0,
                    "comment": "Invalid decision",
                }
            ],
        })
        
        output = advisor.advise(input_data, client)
        
        assert output.proposal_decisions[0].decision == "skip"
    
    def test_fallback_on_client_error(self):
        """Test advisor falls back gracefully on client error."""
        advisor = AIAdvisorService(enabled=True, fallback_on_error=True)
        
        input_data = self._create_test_input()
        
        # Client that raises exception
        client = Mock()
        client.call = Mock(side_effect=Exception("API timeout"))
        
        output = advisor.advise(input_data, client)
        
        # Should return empty output with error
        assert output.risk_mode is None
        assert len(output.proposal_decisions) == 0
        assert "error" in output.error.lower()
    
    def test_comment_truncation(self):
        """Test long comments are truncated."""
        advisor = AIAdvisorService(enabled=True)
        
        long_comment = "A" * 500  # 500 chars
        input_data = self._create_test_input()
        client = MockClient(fixed_response={
            "risk_mode": "NORMAL",
            "decisions": [
                {
                    "symbol": "BTC-USD",
                    "side": "BUY",
                    "decision": "accept",
                    "size_factor": 1.0,
                    "comment": long_comment,
                }
            ],
        })
        
        output = advisor.advise(input_data, client)
        
        # Comment should be truncated to 200 chars
        assert len(output.proposal_decisions[0].comment) == 200
    
    def _create_test_input(self, proposals=None):
        """Helper to create test input."""
        if proposals is None:
            proposals = [
                AIProposalIn(
                    symbol="BTC-USD",
                    side="BUY",
                    tier="T1",
                    conviction=0.7,
                    notional=1000.0,
                    reason="Test signal",
                )
            ]
        
        return AIAdvisorInput(
            run_id="test-456",
            timestamp_iso=datetime.now(timezone.utc).isoformat(),
            market=AIMarketSnapshot(
                regime="trend",
                nav=10000.0,
                exposure_pct=40.0,
            ),
            portfolio=AIPortfolioSnapshot(
                positions={},
                realized_pnl_24h=0.0,
                num_positions=0,
            ),
            proposals=proposals,
        )


class TestRiskProfiles:
    """Test risk profile mappings and constraints."""
    
    def test_get_risk_profile_defensive(self):
        """Test DEFENSIVE profile."""
        profile = get_risk_profile("DEFENSIVE")
        assert profile["trade_size_multiplier"] == 0.5
        assert profile["max_at_risk_pct"] == 10.0
    
    def test_get_risk_profile_normal(self):
        """Test NORMAL profile."""
        profile = get_risk_profile("NORMAL")
        assert profile["trade_size_multiplier"] == 1.0
        assert profile["max_at_risk_pct"] == 15.0
    
    def test_get_risk_profile_off(self):
        """Test OFF profile."""
        profile = get_risk_profile("OFF")
        assert profile["trade_size_multiplier"] == 0.0
        assert profile["max_at_risk_pct"] == 0.0
    
    def test_apply_risk_profile_respects_policy_caps(self):
        """Test risk profile never exceeds policy caps."""
        caps = apply_risk_profile_to_caps(
            mode="NORMAL",
            policy_max_at_risk_pct=10.0,  # Policy is stricter
            policy_max_positions=3,
        )
        
        # Should use policy cap (10.0) not profile cap (15.0)
        assert caps["max_at_risk_pct"] == 10.0
    
    def test_apply_risk_profile_trade_size_never_exceeds_one(self):
        """Test trade size multiplier never exceeds 1.0."""
        caps = apply_risk_profile_to_caps(
            mode="AGGRESSIVE",  # Profile has 1.0
            policy_max_at_risk_pct=20.0,
            policy_max_positions=10,
        )
        
        # Should be clamped to 1.0 in v1
        assert caps["trade_size_multiplier"] == 1.0


class TestModelClient:
    """Test model client factory and mock client."""
    
    def test_create_mock_client(self):
        """Test mock client creation."""
        client = create_model_client(provider="mock")
        assert client is not None
    
    def test_mock_client_default_response(self):
        """Test mock client returns safe defaults."""
        client = MockClient()
        
        request = {
            "market": {"regime": "trend", "nav": 10000.0, "exposure_pct": 50.0},
            "portfolio": {"positions": {}, "realized_pnl_24h": 0.0, "num_positions": 0},
            "proposals": [
                {
                    "symbol": "BTC-USD",
                    "side": "BUY",
                    "tier": "T1",
                    "conviction": 0.7,
                    "notional": 1000.0,
                    "reason": "Test",
                }
            ],
        }
        
        response = client.call(request, timeout=1.0)
        
        assert response["risk_mode"] == "NORMAL"
        assert len(response["decisions"]) == 1
        assert response["decisions"][0]["decision"] == "accept"
    
    def test_mock_client_fixed_response(self):
        """Test mock client with fixed response."""
        fixed = {
            "risk_mode": "DEFENSIVE",
            "decisions": [
                {
                    "symbol": "BTC-USD",
                    "side": "BUY",
                    "decision": "reduce",
                    "size_factor": 0.5,
                    "comment": "Test reduction",
                }
            ],
        }
        
        client = MockClient(fixed_response=fixed)
        response = client.call({}, timeout=1.0)
        
        assert response["risk_mode"] == "DEFENSIVE"
        assert response["decisions"][0]["size_factor"] == 0.5
    
    def test_create_client_invalid_provider(self):
        """Test invalid provider raises error."""
        with pytest.raises(ValueError, match="Unknown provider"):
            create_model_client(provider="invalid")
    
    def test_create_openai_client_requires_api_key(self):
        """Test OpenAI client requires API key."""
        with pytest.raises(ValueError, match="requires api_key"):
            create_model_client(provider="openai")
    
    def test_create_anthropic_client_requires_api_key(self):
        """Test Anthropic client requires API key."""
        with pytest.raises(ValueError, match="requires api_key"):
            create_model_client(provider="anthropic")


class TestIntegration:
    """Integration tests for full AI advisor flow."""
    
    def test_end_to_end_accept_flow(self):
        """Test complete flow: input → advisor → accept."""
        advisor = AIAdvisorService(enabled=True, max_scale_up=1.0)
        
        input_data = AIAdvisorInput(
            run_id="integration-test",
            timestamp_iso=datetime.now(timezone.utc).isoformat(),
            market=AIMarketSnapshot(
                regime="trend",
                nav=10000.0,
                exposure_pct=30.0,
            ),
            portfolio=AIPortfolioSnapshot(
                positions={"BTC-USD": 2000.0},
                realized_pnl_24h=100.0,
                num_positions=1,
            ),
            proposals=[
                AIProposalIn(
                    symbol="ETH-USD",
                    side="BUY",
                    tier="T1",
                    conviction=0.8,
                    notional=1000.0,
                    reason="Strong momentum",
                )
            ],
        )
        
        client = MockClient(fixed_response={
            "risk_mode": "NORMAL",
            "decisions": [
                {
                    "symbol": "ETH-USD",
                    "side": "BUY",
                    "decision": "accept",
                    "size_factor": 1.0,
                    "comment": "Good signal",
                }
            ],
        })
        
        output = advisor.advise(input_data, client)
        
        assert output.risk_mode == "NORMAL"
        assert len(output.proposal_decisions) == 1
        assert output.proposal_decisions[0].size_factor == 1.0
        assert output.latency_ms is not None
        assert output.error is None
    
    def test_end_to_end_defensive_mode(self):
        """Test complete flow with DEFENSIVE mode and size reduction."""
        advisor = AIAdvisorService(enabled=True)
        
        input_data = AIAdvisorInput(
            run_id="defensive-test",
            timestamp_iso=datetime.now(timezone.utc).isoformat(),
            market=AIMarketSnapshot(
                regime="chop",
                nav=5000.0,
                exposure_pct=60.0,
                drawdown_24h_pct=-5.0,
            ),
            portfolio=AIPortfolioSnapshot(
                positions={"BTC-USD": 2000.0, "ETH-USD": 1000.0},
                realized_pnl_24h=-200.0,
                num_positions=2,
            ),
            proposals=[
                AIProposalIn(
                    symbol="SOL-USD",
                    side="BUY",
                    tier="T2",
                    conviction=0.55,
                    notional=500.0,
                    reason="Weak signal",
                )
            ],
        )
        
        client = MockClient(fixed_response={
            "risk_mode": "DEFENSIVE",
            "decisions": [
                {
                    "symbol": "SOL-USD",
                    "side": "BUY",
                    "decision": "reduce",
                    "size_factor": 0.3,
                    "comment": "High drawdown, choppy regime",
                }
            ],
        })
        
        output = advisor.advise(input_data, client)
        
        assert output.risk_mode == "DEFENSIVE"
        assert output.proposal_decisions[0].decision == "reduce"
        assert output.proposal_decisions[0].size_factor == 0.3
