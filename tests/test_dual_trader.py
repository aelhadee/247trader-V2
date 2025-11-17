"""
Comprehensive tests for dual-trader system.

Tests:
- AI trader client (OpenAI/Anthropic/Mock)
- AI trader strategy
- Meta-arbitration logic
- AI arbiter client
- Integration scenarios
"""

import pytest
from datetime import datetime, timezone
from typing import Dict, Any
from unittest.mock import Mock, patch
from dataclasses import dataclass

from ai.llm_client import AiTradeDecision, AiTraderClient, MockAiTraderClient
from ai.arbiter_client import ArbiterInput, ArbiterOutput, MockAiArbiterClient
from strategy.ai_trader_strategy import AiTraderStrategy
from strategy.meta_arb import MetaArbitrator, ArbitrationDecision
from strategy.rules_engine import TradeProposal
from strategy.base_strategy import StrategyContext
from core.universe import UniverseSnapshot, UniverseAsset


# ─── Test Helpers ──────────────────────────────────────────────────────────

def make_asset(symbol: str, tier: int = 1) -> UniverseAsset:
    """Helper to create UniverseAsset with defaults."""
    return UniverseAsset(
        symbol=symbol,
        tier=tier,
        allocation_min_pct=5.0,
        allocation_max_pct=40.0,
        volume_24h=1000000.0,
        spread_bps=15.0,
        depth_usd=100000.0,
        eligible=True,
    )


def make_proposal(symbol: str, side: str, size_pct: float, conviction: float, source: str = "local") -> TradeProposal:
    """Helper to create TradeProposal."""
    return TradeProposal(
        symbol=symbol,
        side=side,
        size_pct=size_pct,
        reason=f"{source} decision",
        confidence=conviction,
    )


# ─── Test AI Trader Client ────────────────────────────────────────────────

class TestAiTraderClient:
    """Test AI trader client (mock mode)."""
    
    def test_mock_client_returns_decisions(self):
        """Mock client returns pre-configured decisions."""
        decisions = [
            AiTradeDecision(
                symbol="BTC-USD",
                action="BUY",
                target_weight_pct=5.0,
                confidence=0.85,
                time_horizon_minutes=120,
                rationale="Strong momentum",
            )
        ]
        
        client = MockAiTraderClient(decisions=decisions)
        result = client.get_decisions(snapshot={})
        
        assert len(result) == 1
        assert result[0].symbol == "BTC-USD"
        assert result[0].action == "BUY"
        assert result[0].confidence == 0.85
    
    def test_decision_clamping(self):
        """Decisions are clamped to valid ranges."""
        decision = AiTradeDecision(
            symbol="BTC-USD",
            action="BUY",
            target_weight_pct=150.0,  # Invalid: >100
            confidence=1.5,  # Invalid: >1
            time_horizon_minutes=2000,  # Invalid: >1440
            rationale="x" * 1000,  # Too long
        )
        
        assert decision.target_weight_pct == 100.0
        assert decision.confidence == 1.0
        assert decision.time_horizon_minutes == 1440
        assert len(decision.rationale) == 500


# ─── Test AI Trader Strategy ──────────────────────────────────────────────

class TestAiTraderStrategy:
    """Test AI trader strategy."""
    
    def test_strategy_generates_proposals(self):
        """Strategy converts AI decisions to proposals."""
        # Setup mock client
        decisions = [
            AiTradeDecision(
                symbol="BTC-USD",
                action="BUY",
                target_weight_pct=5.0,
                confidence=0.8,
                time_horizon_minutes=120,
                rationale="Test decision",
            )
        ]
        client = MockAiTraderClient(decisions=decisions)
        
        # Create strategy
        config = {
            "enabled": True,
            "max_decisions": 5,
            "min_confidence": 0.5,
        }
        strategy = AiTraderStrategy(name="ai_trader", config=config, ai_client=client)
        
        # Build context
        btc_asset = UniverseAsset(
            symbol="BTC-USD",
            tier=1,
            allocation_min_pct=5.0,
            allocation_max_pct=40.0,
            volume_24h=1000000.0,
            spread_bps=15.0,
            depth_usd=100000.0,
            eligible=True,
        )
        
        universe = UniverseSnapshot(
            timestamp=datetime.now(timezone.utc),
            regime="chop",
            tier_1_assets=[btc_asset],
            tier_2_assets=[],
            tier_3_assets=[],
            excluded_assets=[],
            total_eligible=1,
        )
        
        context = StrategyContext(
            universe=universe,
            triggers=[],
            regime="chop",
            timestamp=datetime.now(timezone.utc),
            cycle_number=1,
            nav=10000.0,
        )
        
        # Generate proposals
        proposals = strategy.generate_proposals(context)
        
        assert len(proposals) == 1
        assert proposals[0].product_id == "BTC-USD"
        assert proposals[0].side == "buy"
        assert proposals[0].size_pct == 5.0
        assert proposals[0].confidence == 0.8
        assert proposals[0].source == "ai_trader"
    
    def test_strategy_filters_low_confidence(self):
        """Strategy filters decisions below min_confidence."""
        decisions = [
            AiTradeDecision(
                symbol="BTC-USD",
                action="BUY",
                target_weight_pct=5.0,
                confidence=0.4,  # Below threshold
                time_horizon_minutes=120,
                rationale="Low confidence",
            )
        ]
        client = MockAiTraderClient(decisions=decisions)
        
        config = {
            "enabled": True,
            "max_decisions": 5,
            "min_confidence": 0.6,  # Higher than decision confidence
        }
        strategy = AiTraderStrategy(name="ai_trader", config=config, ai_client=client)
        
        btc_asset = make_asset("BTC-USD")
        universe = UniverseSnapshot(
            timestamp=datetime.now(timezone.utc),
            regime="chop",
            tier_1_assets=[btc_asset],
            tier_2_assets=[],
            tier_3_assets=[],
            excluded_assets=[],
            total_eligible=1,
        )
        
        context = StrategyContext(
            universe=universe,
            triggers=[],
            regime="chop",
            timestamp=datetime.now(timezone.utc),
            cycle_number=1,
            nav=10000.0,
        )
        
        proposals = strategy.generate_proposals(context)
        
        assert len(proposals) == 0  # Filtered out


# ─── Test Meta-Arbitration ────────────────────────────────────────────────

class TestMetaArbitration:
    """Test meta-arbitration logic."""
    
    def test_single_local_proposal(self):
        """Single local proposal passes through."""
        config = {}
        arb = MetaArbitrator(config)
        
        local = [make_proposal("BTC-USD", "buy", 3.0, 0.6, "local")]
        
        final, log = arb.aggregate_proposals(local_proposals=local, ai_proposals=[])
        
        assert len(final) == 1
        assert final[0].symbol == "BTC-USD"
        assert log[0].resolution == "SINGLE"
    
    def test_single_ai_proposal_above_threshold(self):
        """Single AI proposal with high confidence passes."""
        config = {"min_ai_confidence": 0.6}
        arb = MetaArbitrator(config)
        
        ai = [
            make_proposal("BTC-USD", "buy", 4.0, 0.75, "ai")
        ]
        
        final, log = arb.aggregate_proposals(local_proposals=[], ai_proposals=ai)
        
        assert len(final) == 1
        assert final[0].symbol == "BTC-USD"
        assert log[0].resolution == "SINGLE"
    
    def test_single_ai_proposal_below_threshold(self):
        """Single AI proposal with low confidence blocked."""
        config = {"min_ai_confidence": 0.8}
        arb = MetaArbitrator(config)
        
        ai = [
            make_proposal("BTC-USD", "buy", 4.0, 0.5, "ai")
        ]
        
        final, log = arb.aggregate_proposals(local_proposals=[], ai_proposals=ai)
        
        assert len(final) == 0
        assert log[0].resolution == "NONE"
    
    def test_agreement_same_side(self):
        """Agreement on same side → blend conservatively."""
        config = {"blend_mode": "conservative"}
        arb = MetaArbitrator(config)
        
        local = [
            make_proposal("BTC-USD", "buy", 3.0, 0.6, "local")
        ]
        
        ai = [
            make_proposal("BTC-USD", "buy", 5.0, 0.8)
        ]
        
        final, log = arb.aggregate_proposals(local_proposals=local, ai_proposals=ai)
        
        assert len(final) == 1
        assert final[0].size_pct == 3.0  # min(3.0, 5.0)
        assert log[0].resolution == "BLEND"
    
    def test_conflict_low_ai_confidence(self):
        """Conflict with low AI confidence → trust local."""
        config = {"ai_override_threshold": 0.7}
        arb = MetaArbitrator(config)
        
        local = [
            make_proposal("BTC-USD", "buy", 3.0, 0.6, "local")
        ]
        
        ai = [
            make_proposal("BTC-USD", "sell", 2.0, 0.5)
        ]
        
        final, log = arb.aggregate_proposals(local_proposals=local, ai_proposals=ai)
        
        assert len(final) == 1
        assert final[0].side == "buy"  # Local wins
        assert log[0].resolution == "LOCAL"
    
    def test_conflict_weak_local_strong_ai(self):
        """Conflict with weak local + strong AI → trust AI."""
        config = {
            "ai_override_threshold": 0.7,
            "local_weak_conviction": 0.35,
            "ai_confidence_advantage": 0.25,
        }
        arb = MetaArbitrator(config)
        
        local = [
            make_proposal("BTC-USD", "buy", 3.0, 0.30)
        ]
        
        ai = [
            make_proposal("BTC-USD", "sell", 2.0, 0.80)
        ]
        
        final, log = arb.aggregate_proposals(local_proposals=local, ai_proposals=ai)
        
        assert len(final) == 1
        assert final[0].side == "sell"  # AI wins
        assert log[0].resolution == "AI"
    
    def test_conflict_unresolved(self):
        """Unresolved conflict → stand down."""
        config = {
            "ai_override_threshold": 0.7,
            "local_weak_conviction": 0.35,
            "ai_confidence_advantage": 0.25,
        }
        arb = MetaArbitrator(config)
        
        local = [
            make_proposal("BTC-USD", "buy", 3.0, 0.60)
        ]
        
        ai = [
            make_proposal("BTC-USD", "sell", 2.0, 0.65)
        ]
        
        final, log = arb.aggregate_proposals(local_proposals=local, ai_proposals=ai)
        
        assert len(final) == 0  # Stand down
        assert log[0].resolution == "NONE"


# ─── Test AI Arbiter ───────────────────────────────────────────────────────

class TestAiArbiter:
    """Test AI arbiter client."""
    
    def test_mock_arbiter(self):
        """Mock arbiter returns deterministic resolution."""
        arbiter = MockAiArbiterClient(default_resolution="LOCAL")
        
        arb_input = ArbiterInput(
            symbol="BTC-USD",
            market_snapshot={"price": 50000.0},
            local_decision={"side": "buy", "size_pct": 3.0, "conviction": 0.6},
            ai_decision={"side": "sell", "size_pct": 2.0, "confidence": 0.7},
            guardrails={"max_position_size_pct": 7.0},
        )
        
        output = arbiter.arbitrate(arb_input)
        
        assert output.resolution == "LOCAL"
    
    def test_arbiter_output_clamping(self):
        """Arbiter output is clamped to valid ranges."""
        output = ArbiterOutput(
            resolution="BLEND",
            final_size_pct=150.0,  # Invalid: >100
            comment="x" * 1000,  # Too long
        )
        
        assert output.final_size_pct == 100.0
        assert len(output.comment) == 500


# ─── Integration Tests ─────────────────────────────────────────────────────

class TestIntegration:
    """End-to-end integration tests."""
    
    def test_dual_trader_flow(self):
        """Complete dual-trader flow: local + AI → arbitration."""
        # Setup AI client
        ai_decisions = [
            AiTradeDecision(
                symbol="BTC-USD",
                action="BUY",
                target_weight_pct=5.0,
                confidence=0.85,
                time_horizon_minutes=120,
                rationale="AI says buy",
            ),
            AiTradeDecision(
                symbol="ETH-USD",
                action="SELL",
                target_weight_pct=2.0,
                confidence=0.70,
                time_horizon_minutes=60,
                rationale="AI says sell",
            ),
        ]
        ai_client = MockAiTraderClient(decisions=ai_decisions)
        
        # Setup AI strategy
        ai_strategy_config = {
            "enabled": True,
            "max_decisions": 5,
            "min_confidence": 0.5,
        }
        ai_strategy = AiTraderStrategy(
            name="ai_trader",
            config=ai_strategy_config,
            ai_client=ai_client,
        )
        
        # Setup context
        btc_asset = make_asset("BTC-USD")
        eth_asset = make_asset("ETH-USD")
        universe = UniverseSnapshot(
            timestamp=datetime.now(timezone.utc),
            regime="chop",
            tier_1_assets=[btc_asset, eth_asset],
            tier_2_assets=[],
            tier_3_assets=[],
            excluded_assets=[],
            total_eligible=2,
        )
        
        context = StrategyContext(
            universe=universe,
            triggers=[],
            regime="chop",
            timestamp=datetime.now(timezone.utc),
            cycle_number=1,
            nav=10000.0,
        )
        
        # Generate AI proposals
        ai_proposals = ai_strategy.generate_proposals(context)
        
        # Setup local proposals
        local_proposals = [
            make_proposal("BTC-USD", "buy", 3.0, 0.6, "local"),
            make_proposal("MATIC-USD", "buy", 1.5, 0.55, "local"),
        ]
        
        # Arbitrate
        arb_config = {"blend_mode": "conservative"}
        arbitrator = MetaArbitrator(arb_config)
        final_proposals, arb_log = arbitrator.aggregate_proposals(
            local_proposals=local_proposals,
            ai_proposals=ai_proposals,
        )
        
        # Verify results
        assert len(final_proposals) == 3  # BTC (blended), ETH (AI only), MATIC (local only)
        
        # Find BTC proposal (should be blended)
        btc_proposal = [p for p in final_proposals if p.product_id == "BTC-USD"][0]
        assert btc_proposal.size_pct == 3.0  # min(3.0, 5.0)
        
        # Find arbitration log entries
        btc_arb = [d for d in arb_log if d.symbol == "BTC-USD"][0]
        assert btc_arb.resolution == "BLEND"
        
        eth_arb = [d for d in arb_log if d.symbol == "ETH-USD"][0]
        assert eth_arb.resolution == "SINGLE"


# ─── Run Tests ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
