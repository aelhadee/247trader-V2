"""
Base Strategy Interface for Multi-Strategy Framework

Defines the pure interface that all strategies must implement.
Ensures strategies remain isolated and cannot directly call exchange APIs.

Architecture:
- BaseStrategy: Abstract base class with generate_proposals() interface
- StrategyContext: Immutable context passed to strategies
- TradeProposal: Output from strategies (defined in rules_engine.py)

REQ-STR1: Pure strategy interface (no direct exchange calls)
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, TYPE_CHECKING
from dataclasses import dataclass, field
from datetime import datetime, timezone

from core.universe import UniverseSnapshot
from core.triggers import TriggerSignal

# Avoid circular import - TradeProposal defined in rules_engine
if TYPE_CHECKING:
    from strategy.rules_engine import TradeProposal  # noqa: F401

import logging

logger = logging.getLogger(__name__)


@dataclass
class StrategyContext:
    """
    Immutable context passed to strategies.
    
    Contains all market data needed for decision-making without
    allowing direct exchange API access.
    
    Attributes:
        universe: Current universe snapshot with eligible assets
        triggers: Detected trigger signals for this cycle
        regime: Market regime (bull/chop/bear/crash)
        timestamp: Current cycle timestamp
        cycle_number: Sequential cycle counter
        state: Strategy-specific state from previous cycle (optional)
        risk_constraints: Current risk limits (optional)
    """
    universe: UniverseSnapshot
    triggers: List[TriggerSignal]
    regime: str = "chop"
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    cycle_number: int = 0
    state: Optional[Dict[str, Any]] = None
    risk_constraints: Optional[Dict[str, Any]] = None
    
    def __post_init__(self):
        """Validate context after initialization."""
        if not isinstance(self.universe, UniverseSnapshot):
            raise TypeError(f"universe must be UniverseSnapshot, got {type(self.universe)}")
        
        if not isinstance(self.triggers, list):
            raise TypeError(f"triggers must be list, got {type(self.triggers)}")
        
        # Ensure timestamp is timezone-aware
        if self.timestamp and self.timestamp.tzinfo is None:
            self.timestamp = self.timestamp.replace(tzinfo=timezone.utc)


class BaseStrategy(ABC):
    """
    Abstract base class for all trading strategies.
    
    All strategies MUST:
    1. Inherit from this class
    2. Implement generate_proposals()
    3. Return List[TradeProposal] or empty list
    4. NOT call exchange APIs directly
    5. NOT mutate global state
    6. NOT modify Risk or Execution Engine configuration
    
    REQ-STR1 Compliance:
    - Pure interface: only takes StrategyContext, returns proposals
    - No exchange dependency injection
    - No side effects allowed
    """
    
    def __init__(self, name: str, config: Dict[str, Any]):
        """
        Initialize strategy with name and configuration.
        
        Args:
            name: Unique strategy identifier (e.g., "rules_engine", "mean_reversion")
            config: Strategy-specific configuration from strategies.yaml
        """
        self.name = name
        self.config = config
        self._enabled = config.get("enabled", False)
        self._description = config.get("description", "")
        
        # Extract risk budgets
        risk_budgets = config.get("risk_budgets", {})
        self.max_at_risk_pct = risk_budgets.get("max_at_risk_pct")
        self.max_trades_per_cycle = risk_budgets.get("max_trades_per_cycle")
        
        logger.info(
            f"Initialized strategy '{name}': enabled={self._enabled}, "
            f"max_at_risk={self.max_at_risk_pct}%, max_trades={self.max_trades_per_cycle}"
        )
    
    @property
    def enabled(self) -> bool:
        """Check if strategy is enabled."""
        return self._enabled
    
    @property
    def description(self) -> str:
        """Get strategy description."""
        return self._description
    
    @abstractmethod
    def generate_proposals(self, context: StrategyContext) -> List[Any]:
        """
        Generate trade proposals based on strategy logic.
        
        This is the ONLY method that strategies must implement.
        
        Args:
            context: Immutable context with market data and constraints
            
        Returns:
            List of trade proposals (TradeProposal objects, can be empty)
            
        Raises:
            Must not raise exceptions; log errors and return empty list
            
        Contract:
        - MUST return List[TradeProposal] or []
        - MUST NOT call exchange APIs
        - MUST NOT mutate context or global state
        - MUST handle all errors internally (log and return [])
        - SHOULD respect context.risk_constraints
        - SHOULD tag proposals with strategy name
        """
        pass
    
    def validate_proposals(self, proposals: List[Any]) -> List[Any]:
        """
        Validate proposals before returning.
        
        Ensures all proposals:
        - Have valid symbol, side, size_pct, confidence
        - Are tagged with strategy name
        - Don't exceed strategy's max_trades_per_cycle
        
        Args:
            proposals: Raw proposals from strategy logic
            
        Returns:
            Validated and tagged proposals
        """
        if not proposals:
            return []
        
        validated = []
        for proposal in proposals:
            # Validate required fields
            if not all([proposal.symbol, proposal.side, proposal.size_pct]):
                logger.warning(
                    f"[{self.name}] Invalid proposal missing required fields: "
                    f"symbol={proposal.symbol}, side={proposal.side}, size_pct={proposal.size_pct}"
                )
                continue
            
            # Validate side (only BUY or SELL allowed)
            if proposal.side not in ["BUY", "SELL"]:
                logger.warning(f"[{self.name}] Invalid side '{proposal.side}' for {proposal.symbol}")
                continue
            
            # Validate confidence range
            if not 0.0 <= proposal.confidence <= 1.0:
                logger.warning(
                    f"[{self.name}] Invalid confidence {proposal.confidence} "
                    f"for {proposal.symbol}, must be in [0.0, 1.0]"
                )
                continue
            
            # Tag with strategy name
            if self.name not in proposal.tags:
                proposal.tags.append(self.name)
            
            # Add strategy metadata (REQ-STR3: pass risk budgets to RiskEngine)
            proposal.metadata["strategy"] = self.name
            proposal.metadata["strategy_enabled"] = self._enabled
            if self.max_at_risk_pct is not None:
                proposal.metadata["strategy_max_at_risk_pct"] = self.max_at_risk_pct
            if self.max_trades_per_cycle is not None:
                proposal.metadata["strategy_max_trades_per_cycle"] = self.max_trades_per_cycle
            
            validated.append(proposal)
        
        # Enforce max_trades_per_cycle if configured
        if self.max_trades_per_cycle and len(validated) > self.max_trades_per_cycle:
            logger.warning(
                f"[{self.name}] Generated {len(validated)} proposals, "
                f"limiting to max_trades_per_cycle={self.max_trades_per_cycle}"
            )
            validated = validated[:self.max_trades_per_cycle]
        
        return validated
    
    def run(self, context: StrategyContext) -> List[Any]:
        """
        Execute strategy with error handling and validation.
        
        This is the public method called by StrategyRegistry.
        Wraps generate_proposals() with safety checks.
        
        Args:
            context: Strategy context
            
        Returns:
            Validated trade proposals or empty list on error
        """
        if not self._enabled:
            logger.debug(f"[{self.name}] Skipped (disabled)")
            return []
        
        try:
            # Generate proposals
            proposals = self.generate_proposals(context)
            
            # Validate and tag
            validated = self.validate_proposals(proposals)
            
            logger.info(
                f"[{self.name}] Generated {len(validated)} proposals "
                f"({len(proposals)} before validation)"
            )
            
            return validated
            
        except Exception as e:
            logger.error(
                f"[{self.name}] Error generating proposals: {e}",
                exc_info=True
            )
            return []
    
    def __repr__(self) -> str:
        """String representation for logging."""
        return (
            f"{self.__class__.__name__}(name='{self.name}', "
            f"enabled={self._enabled}, "
            f"max_at_risk={self.max_at_risk_pct}%, "
            f"max_trades={self.max_trades_per_cycle})"
        )
