"""
Trading Cycle Pipeline - Shared Core Logic

Extracts the core trading cycle pipeline that can be reused by:
- Live trading (runner/main_loop.py)
- Backtesting (backtest/engine.py)

This ensures backtests accurately reflect production behavior.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Dict, Any
import logging

from core.universe import UniverseManager, UniverseSnapshot
from core.triggers import TriggerEngine, TriggerSignal
from core.regime import RegimeDetector
from strategy.rules_engine import TradeProposal
from core.risk import RiskEngine, PortfolioState
from strategy.registry import StrategyRegistry
from strategy.base_strategy import StrategyContext

logger = logging.getLogger(__name__)


@dataclass
class CycleResult:
    """Result of a trading cycle execution"""
    success: bool
    universe: Optional[UniverseSnapshot]
    triggers: List[TriggerSignal]
    base_proposals: List[TradeProposal]
    risk_approved: List[TradeProposal]
    executed: List[TradeProposal]
    no_trade_reason: Optional[str]
    error: Optional[str] = None


class TradingCyclePipeline:
    """
    Reusable trading cycle pipeline.
    
    Implements the core flow:
    1. Build universe
    2. Scan triggers
    3. Generate proposals (multi-strategy)
    4. Risk approval
    5. Execution (delegated)
    
    Used by both live trading and backtesting.
    """
    
    def __init__(self,
                 universe_mgr: UniverseManager,
                 trigger_engine: TriggerEngine,
                 regime_detector: RegimeDetector,
                 risk_engine: RiskEngine,
                 strategy_registry: Optional[StrategyRegistry] = None,
                 policy_config: Optional[Dict] = None):
        """
        Initialize pipeline with core components.
        
        Args:
            universe_mgr: Universe manager
            trigger_engine: Trigger scanner
            regime_detector: Regime detection
            risk_engine: Risk approval
            strategy_registry: Multi-strategy registry (optional, falls back to RulesEngine)
            policy_config: Policy configuration
        """
        self.universe_mgr = universe_mgr
        self.trigger_engine = trigger_engine
        self.regime_detector = regime_detector
        self.risk_engine = risk_engine
        self.strategy_registry = strategy_registry
        self.policy_config = policy_config or {}
        
        logger.info("Initialized TradingCyclePipeline with shared components")
    
    def execute_cycle(self,
                      current_time: datetime,
                      portfolio: PortfolioState,
                      regime: str,
                      cycle_number: int,
                      state: Optional[Dict[str, Any]] = None) -> CycleResult:
        """
        Execute one trading cycle through the pipeline.
        
        Args:
            current_time: Current timestamp
            portfolio: Current portfolio state
            regime: Market regime
            cycle_number: Cycle counter
            state: Optional state dict for strategies
            
        Returns:
            CycleResult with universe, triggers, proposals, approvals
        """
        try:
            # Step 1: Build universe
            logger.debug(f"Pipeline Step 1: Building universe (regime={regime})")
            universe = self.universe_mgr.get_universe(regime=regime)
            
            if not universe or universe.total_eligible == 0:
                return CycleResult(
                    success=False,
                    universe=universe,
                    triggers=[],
                    base_proposals=[],
                    risk_approved=[],
                    executed=[],
                    no_trade_reason="empty_universe"
                )
            
            logger.debug(
                f"Universe: {universe.total_eligible} eligible "
                f"(T1:{len(universe.tier_1_assets)}, T2:{len(universe.tier_2_assets)}, "
                f"T3:{len(universe.tier_3_assets)})"
            )
            
            # Step 2: Scan for triggers
            logger.debug("Pipeline Step 2: Scanning for triggers")
            all_assets = universe.get_all_eligible()
            triggers = self.trigger_engine.scan(all_assets, regime=regime)
            
            if not triggers or len(triggers) == 0:
                return CycleResult(
                    success=False,
                    universe=universe,
                    triggers=[],
                    base_proposals=[],
                    risk_approved=[],
                    executed=[],
                    no_trade_reason="no_candidates_from_triggers"
                )
            
            logger.debug(f"Triggers: {len(triggers)} detected")
            
            # Step 3: Generate proposals (multi-strategy or fallback to RulesEngine)
            logger.debug("Pipeline Step 3: Generating trade proposals")
            if self.strategy_registry:
                # Multi-strategy framework
                strategy_context = StrategyContext(
                    universe=universe,
                    triggers=triggers,
                    regime=regime,
                    timestamp=current_time,
                    cycle_number=cycle_number,
                    state=state or {}
                )
                
                base_proposals = self.strategy_registry.aggregate_proposals(strategy_context)
            else:
                # Fallback to RulesEngine for backward compatibility
                from strategy.rules_engine import RulesEngine
                rules_engine = RulesEngine(config={})
                base_proposals = rules_engine.propose_trades(
                    universe=universe,
                    triggers=triggers,
                    regime=regime
                )
            
            if not base_proposals:
                return CycleResult(
                    success=False,
                    universe=universe,
                    triggers=triggers,
                    base_proposals=[],
                    risk_approved=[],
                    executed=[],
                    no_trade_reason="no_proposals_from_strategies"
                )
            
            logger.debug(f"Proposals: {len(base_proposals)} generated from strategies")
            
            # Step 4: Risk approval
            logger.debug("Pipeline Step 4: Risk approval")
            risk_result = self.risk_engine.check_all(base_proposals, portfolio, regime=regime)
            
            if not risk_result.approved:
                logger.debug(f"Risk rejection: {risk_result.rejection_reason}")
                return CycleResult(
                    success=False,
                    universe=universe,
                    triggers=triggers,
                    base_proposals=base_proposals,
                    risk_approved=[],
                    executed=[],
                    no_trade_reason=f"risk_blocked_{risk_result.rejection_reason}"
                )
            
            logger.debug(f"Risk approved: {len(base_proposals)} proposals passed")
            
            # Return cycle result (execution is delegated to caller)
            return CycleResult(
                success=True,
                universe=universe,
                triggers=triggers,
                base_proposals=base_proposals,
                risk_approved=base_proposals,  # All proposals if approved
                executed=[],  # Caller handles execution
                no_trade_reason=None
            )
            
        except Exception as e:
            logger.error(f"Pipeline cycle failed: {e}", exc_info=True)
            return CycleResult(
                success=False,
                universe=None,
                triggers=[],
                base_proposals=[],
                risk_approved=[],
                executed=[],
                no_trade_reason="pipeline_error",
                error=str(e)
            )
