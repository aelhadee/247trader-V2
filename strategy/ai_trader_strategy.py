"""
AI Trader Strategy - LLM-driven trade proposal generation.

Implements BaseStrategy interface to generate proposals from AI model decisions.
Builds rich market snapshot and converts AI decisions to TradeProposals.
"""

import logging
from typing import List, Dict, Any

from strategy.base_strategy import BaseStrategy, StrategyContext
from strategy.rules_engine import TradeProposal
from ai.llm_client import AiTraderClient, AiTradeDecision
from ai.snapshot_builder import build_ai_snapshot

logger = logging.getLogger(__name__)


class AiTraderStrategy(BaseStrategy):
    """
    Strategy that generates proposals from LLM model decisions.
    
    Flow:
    1. Build rich snapshot from StrategyContext
    2. Call AI client with snapshot
    3. Convert AI decisions to TradeProposals
    4. Filter HOLD/NONE actions
    5. Validate and return proposals
    
    Safety:
    - AI can only propose BUY/SELL/HOLD/NONE
    - All proposals go through RiskEngine vetting
    - Failures return [] (no trades)
    - Full audit trail via source="ai_trader"
    """
    
    def __init__(
        self,
        name: str,
        config: Dict[str, Any],
        ai_client: AiTraderClient,
    ):
        """
        Initialize AI trader strategy.
        
        Args:
            name: Strategy name (e.g., "ai_trader")
            config: Strategy config from strategies.yaml
            ai_client: AI trader client for LLM calls
        """
        super().__init__(name, config)
        self.ai_client = ai_client
        
        # Extract AI-specific config
        self.max_decisions = config.get("max_decisions", 5)
        self.min_confidence = config.get("min_confidence", 0.0)
        self.enable_hold_signals = config.get("enable_hold_signals", False)
        
        logger.info(
            f"AI trader strategy initialized: max_decisions={self.max_decisions}, "
            f"min_confidence={self.min_confidence}"
        )
    
    def generate_proposals(self, context: StrategyContext) -> List[TradeProposal]:
        """
        Generate proposals from AI model decisions.
        
        Args:
            context: Strategy context with universe, triggers, regime, etc.
            
        Returns:
            List of TradeProposals (empty on error or no valid decisions)
        """
        try:
            # Build snapshot for AI
            snapshot = self._build_snapshot(context)
            
            # Get AI decisions
            ai_decisions = self.ai_client.get_decisions(
                snapshot=snapshot,
                max_decisions=self.max_decisions,
            )
            
            if not ai_decisions:
                logger.info("AI trader returned no decisions")
                return []
            
            # Convert to proposals
            proposals = self._convert_to_proposals(ai_decisions, context)
            
            logger.info(
                f"AI trader generated {len(proposals)} proposals from {len(ai_decisions)} decisions"
            )
            
            return proposals
            
        except Exception as e:
            logger.error(f"AI trader failed: {e}", exc_info=True)
            return []
    
    def _build_snapshot(self, context: StrategyContext) -> Dict[str, Any]:
        """
        Build rich market snapshot from strategy context.
        
        Args:
            context: Strategy context
            
        Returns:
            Snapshot dict ready for AI client
        """
        # Extract universe data
        universe_data = []
        for asset in context.universe.assets:
            universe_data.append({
                "symbol": asset.symbol,
                "price": asset.price,
                "volume_24h": asset.volume_24h,
                "spread_pct": getattr(asset, "spread_pct", 0.0),
                "change_1h_pct": getattr(asset, "change_1h_pct", 0.0),
                "change_24h_pct": getattr(asset, "change_24h_pct", 0.0),
                "volatility": getattr(asset, "volatility", 0.0),
                "tier": asset.tier,
            })
        
        # Extract positions (from state if available)
        positions = []
        if context.state and "positions" in context.state:
            positions = context.state["positions"]
        
        # Extract available capital
        available_capital = context.nav
        if context.state and "available_capital" in context.state:
            available_capital = context.state["available_capital"]
        
        # Extract guardrails
        guardrails = context.risk_constraints or {}
        
        # Convert triggers to dict format
        triggers_data = []
        for t in context.triggers:
            triggers_data.append({
                "symbol": t.symbol,
                "type": t.trigger_type,
                "strength": t.strength,
                "confidence": t.confidence,
                "volatility": t.volatility,
            })
        
        # Build snapshot
        snapshot = build_ai_snapshot(
            universe_snapshot=universe_data,
            positions=positions,
            available_capital_usd=available_capital,
            regime=context.regime,
            guardrails=guardrails,
            triggers=triggers_data,
            metadata={
                "cycle_number": context.cycle_number,
                "timestamp": context.timestamp.isoformat(),
            },
        )
        
        return snapshot
    
    def _convert_to_proposals(
        self,
        ai_decisions: List[AiTradeDecision],
        context: StrategyContext,
    ) -> List[TradeProposal]:
        """
        Convert AI decisions to TradeProposals.
        
        Args:
            ai_decisions: List of AI trade decisions
            context: Strategy context for validation
            
        Returns:
            List of TradeProposals
        """
        proposals = []
        
        # Build symbol set for validation
        valid_symbols = {asset.symbol for asset in context.universe.assets}
        
        for decision in ai_decisions:
            # Filter by action
            if decision.action == "HOLD":
                if not self.enable_hold_signals:
                    continue
                # HOLD signals don't generate trades, but could be logged
                logger.debug(f"AI suggests HOLD for {decision.symbol}")
                continue
            
            if decision.action == "NONE":
                continue
            
            # Validate symbol in universe
            if decision.symbol not in valid_symbols:
                logger.warning(
                    f"AI proposed {decision.action} for {decision.symbol} "
                    f"but symbol not in universe"
                )
                continue
            
            # Filter by confidence
            if decision.confidence < self.min_confidence:
                logger.debug(
                    f"Skipping {decision.symbol} {decision.action}: "
                    f"confidence {decision.confidence:.2f} < {self.min_confidence}"
                )
                continue
            
            # Convert action to side
            side = decision.action.lower()  # "buy" or "sell"
            
            # Create proposal
            proposal = TradeProposal(
                product_id=decision.symbol,
                side=side,
                target_weight_pct=decision.target_weight_pct,
                conviction=decision.confidence,
                source="ai_trader",
                notes=(
                    f"AI decision (conf={decision.confidence:.2f}, "
                    f"horizon={decision.time_horizon_minutes}m): "
                    f"{decision.rationale[:200]}"
                ),
                stop_loss_pct=None,  # AI doesn't set stops directly
                take_profit_pct=None,
            )
            
            proposals.append(proposal)
        
        return proposals
