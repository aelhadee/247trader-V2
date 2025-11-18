"""Unit tests for AiTraderAgent portfolio allocator."""

from datetime import datetime, timezone

from ai.llm_client import AiTradeDecision, MockAiTraderClient
from ai.trader_agent import AiTraderAgent, TraderAgentSettings
from core.risk import PortfolioState
from core.triggers import TriggerSignal
from core.universe import UniverseAsset, UniverseSnapshot


def _make_universe() -> UniverseSnapshot:
    now = datetime.now(timezone.utc)
    btc = UniverseAsset(
        symbol="BTC-USD",
        tier=1,
        allocation_min_pct=5.0,
        allocation_max_pct=40.0,
        volume_24h=1_000_000_000.0,
        spread_bps=15.0,
        depth_usd=500_000.0,
        eligible=True,
    )
    eth = UniverseAsset(
        symbol="ETH-USD",
        tier=1,
        allocation_min_pct=5.0,
        allocation_max_pct=35.0,
        volume_24h=800_000_000.0,
        spread_bps=18.0,
        depth_usd=300_000.0,
        eligible=True,
    )
    sol = UniverseAsset(
        symbol="SOL-USD",
        tier=2,
        allocation_min_pct=2.0,
        allocation_max_pct=15.0,
        volume_24h=200_000_000.0,
        spread_bps=30.0,
        depth_usd=120_000.0,
        eligible=True,
    )

    return UniverseSnapshot(
        timestamp=now,
        regime="chop",
        tier_1_assets=[btc, eth],
        tier_2_assets=[sol],
        tier_3_assets=[],
        excluded_assets=[],
        total_eligible=3,
    )


def _make_portfolio(nav: float = 100_000.0) -> PortfolioState:
    return PortfolioState(
        account_value_usd=nav,
        open_positions={
            "BTC-USD": {"usd": 4_000.0, "units": 0.1},
            "ETH-USD": {"usd": 6_000.0, "units": 1.5},
        },
        daily_pnl_pct=0.0,
        max_drawdown_pct=0.0,
        trades_today=0,
        trades_this_hour=0,
    )


def _guardrails() -> dict:
    return {
        "max_total_at_risk_pct": 25.0,
        "max_position_size_pct": 5.0,
        "min_trade_notional": 5.0,
        "max_trades_per_cycle": 5,
        "max_trades_per_day": 120,
    }


def _basic_triggers():
    now = datetime.now(timezone.utc)
    return [
        TriggerSignal(
            symbol="BTC-USD",
            trigger_type="momentum",
            strength=0.7,
            confidence=0.8,
            reason="test",
            timestamp=now,
            current_price=60_000.0,
        )
    ]


class TestAiTraderAgent:
    def test_generates_delta_trades(self):
        """AI agent should emit BUY/SELL trades sized by delta to target weights."""
        decisions = [
            AiTradeDecision(
                symbol="BTC-USD",
                action="BUY",
                target_weight_pct=8.0,
                confidence=0.82,
                time_horizon_minutes=180,
                rationale="Increase BTC core position",
            ),
            AiTradeDecision(
                symbol="ETH-USD",
                action="SELL",
                target_weight_pct=1.0,
                confidence=0.74,
                time_horizon_minutes=90,
                rationale="Trim ETH exposure",
            ),
            AiTradeDecision(
                symbol="SOL-USD",
                action="BUY",
                target_weight_pct=3.0,
                confidence=0.9,
                time_horizon_minutes=120,
                rationale="New SOL position",
            ),
            AiTradeDecision(
                symbol="ADA-USD",
                action="BUY",
                target_weight_pct=0.1,
                confidence=0.95,
                time_horizon_minutes=120,
                rationale="Below min delta",
            ),
        ]

        settings = TraderAgentSettings(
            max_decisions=5,
            min_confidence=0.6,
            min_rebalance_delta_pct=0.25,
            max_position_pct=5.0,
            max_single_trade_pct=3.0,
            tag="ai_trader_agent",
        )
        agent = AiTraderAgent(
            enabled=True,
            client=MockAiTraderClient(decisions=decisions),
            settings=settings,
        )

        proposals = agent.generate_proposals(
            universe=_make_universe(),
            triggers=_basic_triggers(),
            portfolio=_make_portfolio(),
            guardrails=_guardrails(),
            metadata={"cycle_number": 1},
        )

        symbols_to_side = {p.symbol: (p.side, round(p.size_pct, 3)) for p in proposals}

        # BTC already 4%, target capped at 5% by guardrail → 1% BUY
        assert symbols_to_side.get("BTC-USD") == ("BUY", 1.0)
        # ETH 6% → target 1%, but trade capped at max_single_trade_pct=3%
        assert symbols_to_side.get("ETH-USD") == ("SELL", 3.0)
        # SOL new position should be added at requested 3%
        assert symbols_to_side.get("SOL-USD") == ("BUY", 3.0)
        # ADA not in universe / below delta threshold should be absent
        assert "ADA-USD" not in symbols_to_side

    def test_skips_low_confidence_and_missing_positions(self):
        """Agent should ignore low-confidence or non-existent sell proposals."""
        decisions = [
            AiTradeDecision(
                symbol="BTC-USD",
                action="SELL",
                target_weight_pct=0.0,
                confidence=0.4,  # below threshold
                time_horizon_minutes=30,
                rationale="Low confidence",
            ),
            AiTradeDecision(
                symbol="XRP-USD",
                action="SELL",
                target_weight_pct=0.0,
                confidence=0.9,
                time_horizon_minutes=45,
                rationale="No position to sell",
            ),
        ]

        settings = TraderAgentSettings(
            max_decisions=3,
            min_confidence=0.6,
            min_rebalance_delta_pct=0.2,
            max_position_pct=5.0,
            max_single_trade_pct=2.0,
            tag="ai_trader_agent",
        )
        agent = AiTraderAgent(
            enabled=True,
            client=MockAiTraderClient(decisions=decisions),
            settings=settings,
        )

        proposals = agent.generate_proposals(
            universe=_make_universe(),
            triggers=_basic_triggers(),
            portfolio=_make_portfolio(),
            guardrails=_guardrails(),
            metadata={"cycle_number": 99},
        )

        assert proposals == []
*** End of File"}