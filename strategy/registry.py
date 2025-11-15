"""
Strategy Registry for Multi-Strategy Framework

Manages loading, discovery, and execution of multiple trading strategies.

Architecture:
- Load strategies from config/strategies.yaml
- Dynamically instantiate strategy classes
- Enforce enabled/disabled toggles
- Provide interface for main_loop to get active strategies

REQ-STR2: Per-strategy feature flags (enable/disable toggles)
"""

from typing import List, Dict, Any, Optional, Type, TYPE_CHECKING
from pathlib import Path
import yaml
import logging

from strategy.base_strategy import BaseStrategy, StrategyContext
from strategy.rules_engine import RulesEngine

# Avoid circular import
if TYPE_CHECKING:
    from strategy.rules_engine import TradeProposal

logger = logging.getLogger(__name__)


class StrategyRegistry:
    """
    Central registry for all trading strategies.
    
    Responsibilities:
    1. Load strategy configurations from strategies.yaml
    2. Instantiate strategy objects
    3. Track enabled/disabled status
    4. Provide filtered list of active strategies
    5. Aggregate proposals from all strategies
    
    REQ-STR2 Compliance:
    - Strategies default to disabled
    - Only enabled strategies generate proposals
    - Clear logging for enabled/disabled state
    """
    
    # Map strategy type to class
    STRATEGY_CLASSES: Dict[str, Type[BaseStrategy]] = {
        "rules_engine": RulesEngine,
        # Add new strategies here:
        # "mean_reversion": MeanReversionStrategy,
        # "momentum": MomentumStrategy,
    }
    
    def __init__(self, config_path: Optional[Path] = None):
        """
        Initialize registry and load strategy configurations.
        
        Args:
            config_path: Path to strategies.yaml (defaults to config/strategies.yaml)
        """
        if config_path is None:
            config_path = Path(__file__).parent.parent / "config" / "strategies.yaml"
        
        self.config_path = config_path
        self.strategies: Dict[str, BaseStrategy] = {}
        self._load_strategies()
    
    def _load_strategies(self) -> None:
        """
        Load strategy configurations and instantiate strategy objects.
        
        Creates strategy instances from config and stores in registry.
        Only loads strategies defined in STRATEGY_CLASSES mapping.
        """
        if not self.config_path.exists():
            logger.warning(
                f"Strategy config not found at {self.config_path}, "
                f"creating default with RulesEngine only"
            )
            self._create_default_config()
        
        # Load config
        with open(self.config_path, 'r') as f:
            config = yaml.safe_load(f)
        
        strategies_config = config.get("strategies", {})
        
        if not strategies_config:
            logger.warning("No strategies defined in config, using RulesEngine default")
            strategies_config = self._get_default_strategies_config()
        
        # Instantiate each strategy
        loaded_count = 0
        enabled_count = 0
        
        for strategy_name, strategy_config in strategies_config.items():
            try:
                # Get strategy type (defaults to strategy name)
                strategy_type = strategy_config.get("type", strategy_name)
                
                # Check if strategy class exists
                if strategy_type not in self.STRATEGY_CLASSES:
                    logger.warning(
                        f"Strategy type '{strategy_type}' not found in STRATEGY_CLASSES, "
                        f"skipping '{strategy_name}'"
                    )
                    continue
                
                # Get strategy class
                strategy_class = self.STRATEGY_CLASSES[strategy_type]
                
                # Instantiate strategy
                strategy = strategy_class(name=strategy_name, config=strategy_config)
                
                # Store in registry
                self.strategies[strategy_name] = strategy
                loaded_count += 1
                
                if strategy.enabled:
                    enabled_count += 1
                    logger.info(f"✅ Loaded and ENABLED strategy: {strategy_name} ({strategy_type})")
                else:
                    logger.info(f"⚪ Loaded but DISABLED strategy: {strategy_name} ({strategy_type})")
                
            except Exception as e:
                logger.error(
                    f"Failed to load strategy '{strategy_name}': {e}",
                    exc_info=True
                )
        
        logger.info(
            f"Strategy registry initialized: {loaded_count} strategies loaded, "
            f"{enabled_count} enabled"
        )
        
        if enabled_count == 0:
            logger.warning("⚠️  No strategies enabled! Trading loop will not generate proposals.")
    
    def _create_default_config(self) -> None:
        """Create default strategies.yaml with RulesEngine."""
        default_config = {
            "strategies": {
                "rules_engine": {
                    "enabled": True,
                    "type": "rules_engine",
                    "description": "Default deterministic rules engine (baseline strategy)",
                    "risk_budgets": {
                        "max_at_risk_pct": 15.0,
                        "max_trades_per_cycle": 5
                    },
                    "params": {}
                }
            }
        }
        
        # Ensure config directory exists
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Write default config
        with open(self.config_path, 'w') as f:
            yaml.dump(default_config, f, default_flow_style=False, sort_keys=False)
        
        logger.info(f"Created default strategy config at {self.config_path}")
    
    def _get_default_strategies_config(self) -> Dict[str, Any]:
        """Get default strategies configuration for backward compatibility."""
        return {
            "rules_engine": {
                "enabled": True,
                "type": "rules_engine",
                "description": "Default deterministic rules engine",
                "risk_budgets": {
                    "max_at_risk_pct": 15.0,
                    "max_trades_per_cycle": 5
                },
                "params": {}
            }
        }
    
    def get_enabled_strategies(self) -> List[BaseStrategy]:
        """
        Get list of enabled strategies.
        
        Returns:
            List of strategy objects with enabled=True
        """
        enabled = [
            strategy for strategy in self.strategies.values()
            if strategy.enabled
        ]
        
        return enabled
    
    def get_strategy(self, name: str) -> Optional[BaseStrategy]:
        """
        Get strategy by name.
        
        Args:
            name: Strategy name
            
        Returns:
            Strategy object or None if not found
        """
        return self.strategies.get(name)
    
    def list_strategies(self) -> Dict[str, Dict[str, Any]]:
        """
        List all strategies with their status.
        
        Returns:
            Dict mapping strategy name to status info
        """
        return {
            name: {
                "enabled": strategy.enabled,
                "type": strategy.__class__.__name__,
                "description": strategy.description,
                "max_at_risk_pct": strategy.max_at_risk_pct,
                "max_trades_per_cycle": strategy.max_trades_per_cycle
            }
            for name, strategy in self.strategies.items()
        }
    
    def generate_proposals(self, context: StrategyContext) -> Dict[str, List[Any]]:
        """
        Generate proposals from all enabled strategies.
        
        Args:
            context: Strategy context with market data
            
        Returns:
            Dict mapping strategy name to list of proposals
        """
        all_proposals = {}
        enabled_strategies = self.get_enabled_strategies()
        
        if not enabled_strategies:
            logger.warning("No enabled strategies, returning empty proposals")
            return all_proposals
        
        logger.info(f"Running {len(enabled_strategies)} enabled strategies")
        
        for strategy in enabled_strategies:
            try:
                proposals = strategy.run(context)
                all_proposals[strategy.name] = proposals
                
                logger.info(
                    f"[{strategy.name}] Generated {len(proposals)} proposals"
                )
                
            except Exception as e:
                logger.error(
                    f"[{strategy.name}] Failed to generate proposals: {e}",
                    exc_info=True
                )
                all_proposals[strategy.name] = []
        
        total_proposals = sum(len(props) for props in all_proposals.values())
        logger.info(
            f"Total proposals across all strategies: {total_proposals} "
            f"(from {len(enabled_strategies)} strategies)"
        )
        
        return all_proposals
    
    def aggregate_proposals(
        self,
        context: StrategyContext,
        dedupe_by_symbol: bool = True
    ) -> List[TradeProposal]:
        """
        Generate and aggregate proposals from all strategies.
        
        Args:
            context: Strategy context
            dedupe_by_symbol: If True, keep only highest confidence proposal per symbol
            
        Returns:
            Aggregated list of proposals
        """
        # Generate proposals from all strategies
        proposals_by_strategy = self.generate_proposals(context)
        
        # Flatten to single list
        all_proposals = []
        for strategy_name, proposals in proposals_by_strategy.items():
            for proposal in proposals:
                # Ensure strategy name is in metadata
                proposal.metadata["strategy_source"] = strategy_name
                all_proposals.append(proposal)
        
        if not all_proposals:
            return []
        
        # Dedupe by symbol if requested
        if dedupe_by_symbol:
            all_proposals = self._dedupe_proposals(all_proposals)
        
        logger.info(f"Aggregated {len(all_proposals)} proposals for execution pipeline")
        
        return all_proposals
    
    def _dedupe_proposals(self, proposals: List[TradeProposal]) -> List[TradeProposal]:
        """
        Deduplicate proposals by symbol, keeping highest confidence.
        
        Args:
            proposals: List of proposals (potentially duplicate symbols)
            
        Returns:
            Deduplicated list with one proposal per symbol
        """
        # Group by symbol
        by_symbol: Dict[str, List[TradeProposal]] = {}
        for proposal in proposals:
            if proposal.symbol not in by_symbol:
                by_symbol[proposal.symbol] = []
            by_symbol[proposal.symbol].append(proposal)
        
        # Keep highest confidence per symbol
        deduped = []
        for symbol, symbol_proposals in by_symbol.items():
            if len(symbol_proposals) == 1:
                deduped.append(symbol_proposals[0])
            else:
                # Sort by confidence (descending)
                symbol_proposals.sort(key=lambda p: p.confidence, reverse=True)
                winner = symbol_proposals[0]
                
                logger.debug(
                    f"Deduped {len(symbol_proposals)} proposals for {symbol}, "
                    f"keeping {winner.metadata.get('strategy_source')} "
                    f"with confidence {winner.confidence:.2f}"
                )
                
                deduped.append(winner)
        
        return deduped
    
    def reload(self) -> None:
        """
        Reload strategy configurations from disk.
        
        Useful for hot-reloading strategy configs without restarting.
        """
        logger.info("Reloading strategy registry")
        self.strategies.clear()
        self._load_strategies()
    
    def __repr__(self) -> str:
        """String representation for logging."""
        enabled_count = len(self.get_enabled_strategies())
        total_count = len(self.strategies)
        return (
            f"StrategyRegistry({total_count} strategies, "
            f"{enabled_count} enabled)"
        )
