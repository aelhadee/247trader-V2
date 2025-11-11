"""
247trader-v2 Runner: Main Loop

Orchestrates the core trading cycle.
Pattern: Freqtrade-style main loop + clean separation of concerns

Flow:
1. Load universe (eligible assets)
2. Scan for triggers (deterministic signals)
3. Generate trade proposals (rules engine)
4. Apply risk checks (hard constraints)
5. [Phase 3+] Optionally enhance with AI (M1/M2/M3)
6. [Phase 5] Execute approved trades

This module implements Phase 1: Core skeleton (no AI, DRY_RUN only)
"""

import json
import time
import yaml
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from pathlib import Path
import logging

from core.exchange_coinbase import get_exchange
from core.universe import UniverseManager
from core.triggers import TriggerEngine
from strategy.rules_engine import RulesEngine, TradeProposal
from core.risk import RiskEngine, PortfolioState
from core.execution import get_executor, ExecutionEngine
from infra.state_store import get_state_store, StateStore

logger = logging.getLogger(__name__)


class TradingLoop:
    """
    Main trading loop orchestrator.
    
    Responsibilities:
    - Load config
    - Run periodic cycles
    - Coordinate core modules
    - Output structured summaries
    - Handle errors gracefully
    """
    
    def __init__(self, config_dir: str = "config"):
        self.config_dir = Path(config_dir)
        
        # Load configs
        self.app_config = self._load_yaml("app.yaml")
        self.policy_config = self._load_yaml("policy.yaml")
        self.universe_config = self._load_yaml("universe.yaml")
        
        # Ensure log directory exists
        log_file = self.app_config.get("logging", {}).get("file", "logs/247trader-v2.log")
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        logger.info(f"Log directory ensured: {log_path.parent}")
        
        # Mode
        self.mode = self.app_config.get("app", {}).get("mode", "DRY_RUN")
        
        # Initialize modules
        self.exchange = get_exchange()
        self.universe_mgr = UniverseManager(self.config_dir / "universe.yaml")
        self.trigger_engine = TriggerEngine()
        self.rules_engine = RulesEngine(config={})
        self.risk_engine = RiskEngine(self.policy_config, universe_manager=self.universe_mgr)
        self.executor = get_executor(mode=self.mode, policy=self.policy_config)
        self.state_store = get_state_store()
        
        # State
        self.portfolio = self._init_portfolio_state()
        self.current_regime = "chop"  # TODO: Replace with regime detector
        
        logger.info(f"Initialized TradingLoop in {self.mode} mode")
    
    def _load_yaml(self, filename: str) -> dict:
        """Load YAML config file"""
        path = self.config_dir / filename
        with open(path) as f:
            return yaml.safe_load(f)
    
    def _init_portfolio_state(self) -> PortfolioState:
        """Initialize portfolio state from state store"""
        state = self.state_store.load()
        
        return PortfolioState(
            account_value_usd=10_000.0,  # TODO: Get from Coinbase API
            open_positions=state.get("positions", {}),
            daily_pnl_pct=state.get("pnl_today", 0.0),
            max_drawdown_pct=0.0,  # TODO: Calculate from history
            trades_today=state.get("trades_today", 0),
            trades_this_hour=state.get("trades_this_hour", 0),
            consecutive_losses=state.get("consecutive_losses", 0),
            last_loss_time=datetime.fromisoformat(state["last_loss_time"]) if state.get("last_loss_time") else None,
            current_time=datetime.utcnow()
        )
    
    def run_once(self) -> Dict:
        """
        Execute one trading cycle.
        
        Returns:
            Structured summary dict
        """
        cycle_start = datetime.utcnow()
        logger.info("=" * 80)
        logger.info(f"CYCLE START: {cycle_start.isoformat()}")
        logger.info("=" * 80)
        
        try:
            # Step 1: Build universe
            logger.info("Step 1: Building universe...")
            universe = self.universe_mgr.get_universe(regime=self.current_regime)
            logger.info(
                f"Universe: {universe.total_eligible} eligible "
                f"({len(universe.tier_1_assets)} core, "
                f"{len(universe.tier_2_assets)} rotational, "
                f"{len(universe.tier_3_assets)} event-driven)"
            )
            
            # Step 2: Scan for triggers
            logger.info("Step 2: Scanning for triggers...")
            all_assets = universe.get_all_eligible()
            triggers = self.trigger_engine.scan(all_assets, regime=self.current_regime)
            logger.info(f"Triggers: {len(triggers)} detected")
            
            # Step 3: Generate trade proposals
            logger.info("Step 3: Generating trade proposals...")
            proposals = self.rules_engine.propose_trades(
                universe=universe,
                triggers=triggers,
                regime=self.current_regime
            )
            logger.info(f"Proposals: {len(proposals)} generated")
            
            # Step 4: Apply risk checks
            logger.info("Step 4: Applying risk checks...")
            risk_result = self.risk_engine.check_all(
                proposals=proposals,
                portfolio=self.portfolio,
                regime=self.current_regime
            )
            
            if not risk_result.approved:
                logger.warning(f"Risk check FAILED: {risk_result.reason}")
                return self._build_summary(
                    universe=universe,
                    triggers=triggers,
                    proposals=proposals,
                    approved_trades=[],
                    executed_trades=[],
                    no_trade_reason=risk_result.reason,
                    violated_checks=risk_result.violated_checks,
                    cycle_start=cycle_start
                )
            
            # Use the filtered approved proposals from risk engine
            approved_proposals = risk_result.approved_proposals
            logger.info(f"Risk checks PASSED: {len(approved_proposals)}/{len(proposals)} proposals approved")
            
            # Step 5: Execute trades
            logger.info(f"Step 5: Executing {len(approved_proposals)} approved trades...")
            executed_trades = []
            
            if self.mode == "DRY_RUN":
                logger.info("DRY_RUN mode - no execution")
            else:
                # Execute each approved proposal
                for proposal in approved_proposals:
                    logger.info(f"Executing: {proposal.side} {proposal.symbol} (size={proposal.size_pct}%)")
                    
                    result = self.executor.execute(proposal)
                    
                    if result["status"] == "EXECUTED":
                        logger.info(f"✅ Trade executed: {proposal.symbol} - {result.get('message', 'Success')}")
                        executed_trades.append({
                            "proposal": proposal,
                            "result": result
                        })
                    elif result["status"] == "SIMULATED":
                        logger.info(f"📝 Trade simulated: {proposal.symbol} - {result.get('message', 'Paper trade')}")
                        executed_trades.append({
                            "proposal": proposal,
                            "result": result
                        })
                    else:
                        logger.warning(f"⚠️ Trade failed: {proposal.symbol} - {result.get('error', 'Unknown error')}")
                
                logger.info(f"Execution complete: {len(executed_trades)}/{len(approved_proposals)} trades successful")
            
            # Build summary (use approved_proposals, not original proposals)
            summary = self._build_summary(
                universe=universe,
                triggers=triggers,
                proposals=proposals,
                approved_trades=approved_proposals,  # Use filtered list from risk engine
                executed_trades=executed_trades,
                no_trade_reason=None,
                violated_checks=risk_result.violated_checks,
                cycle_start=cycle_start
            )
            
            cycle_end = datetime.utcnow()
            cycle_duration = (cycle_end - cycle_start).total_seconds()
            logger.info(f"CYCLE COMPLETE: {cycle_duration:.2f}s")
            
            return summary
            
        except Exception as e:
            logger.exception("Cycle failed with exception")
            return {
                "status": "ERROR",
                "error": str(e),
                "timestamp": datetime.utcnow().isoformat()
            }
    
    def _build_summary(self,
                      universe,
                      triggers,
                      proposals,
                      approved_trades,
                      executed_trades,
                      no_trade_reason,
                      violated_checks,
                      cycle_start) -> Dict:
        """Build structured JSON summary"""
        
        # Determine status
        if no_trade_reason:
            status = "NO_TRADE"
        elif executed_trades:
            status = "EXECUTED"
        elif approved_trades:
            status = "APPROVED_DRY_RUN"
        else:
            status = "NO_OPPORTUNITIES"
        
        summary = {
            "status": status,
            "timestamp": cycle_start.isoformat(),
            "mode": self.mode,
            "regime": self.current_regime,
            
            # Universe
            "universe_size": universe.total_eligible,
            "universe_breakdown": {
                "tier_1_core": len(universe.tier_1_assets),
                "tier_2_rotational": len(universe.tier_2_assets),
                "tier_3_event_driven": len(universe.tier_3_assets)
            },
            
            # Triggers
            "triggers_detected": len(triggers),
            "top_triggers": [
                {
                    "symbol": t.symbol,
                    "type": t.trigger_type,
                    "strength": round(t.strength, 2),
                    "confidence": round(t.confidence, 2),
                    "reason": t.reason
                }
                for t in triggers[:5]  # Top 5
            ],
            
            # Proposals
            "proposals_generated": len(proposals),
            "proposals_approved": len(approved_trades),
            "base_trades": [
                {
                    "symbol": p.symbol,
                    "side": p.side,
                    "size_pct": round(p.size_pct, 2),
                    "confidence": round(p.confidence, 2),
                    "reason": p.reason,
                    "stop_loss_pct": p.stop_loss_pct,
                    "take_profit_pct": p.take_profit_pct
                }
                for p in approved_trades[:10]  # Top 10
            ],
            
            # Execution
            "executed_trades": [
                # TODO: Phase 5
            ],
            
            # Risk/Governance
            "portfolio": {
                "account_value_usd": self.portfolio.account_value_usd,
                "open_positions": len(self.portfolio.open_positions),
                "daily_pnl_pct": round(self.portfolio.daily_pnl_pct, 2),
                "trades_today": self.portfolio.trades_today
            }
        }
        
        # Add rejection info if applicable
        if no_trade_reason:
            summary["no_trade_reason"] = no_trade_reason
            summary["violated_checks"] = violated_checks
        
        return summary
    
    def run_forever(self, interval_minutes: float = 15):
        """
        Run trading loop continuously.
        
        Args:
            interval_minutes: Minutes between cycles (can be fractional, e.g., 0.5 for 30 seconds)
        """
        logger.info(f"Starting continuous loop (interval={interval_minutes}m)")
        
        while True:
            try:
                # Run cycle
                summary = self.run_once()
                
                # Log summary
                print("\n" + "=" * 80)
                print("CYCLE SUMMARY")
                print("=" * 80)
                print(json.dumps(summary, indent=2))
                print("=" * 80 + "\n")
                
                # Wait for next cycle
                logger.info(f"Sleeping for {interval_minutes} minutes...")
                time.sleep(interval_minutes * 60)
                
            except KeyboardInterrupt:
                logger.info("Received interrupt signal, shutting down...")
                break
            except Exception as e:
                logger.exception("Loop iteration failed")
                # Continue running (don't crash on single failure)
                time.sleep(60)  # Wait 1 minute before retry


def main():
    """Entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(description="247trader-v2 Trading Bot")
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    parser.add_argument("--interval", type=float, default=15, help="Minutes between cycles")
    parser.add_argument("--config-dir", default="config", help="Config directory")
    
    args = parser.parse_args()
    
    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # Create loop
    loop = TradingLoop(config_dir=args.config_dir)
    
    if args.once:
        # Run once
        summary = loop.run_once()
        print(json.dumps(summary, indent=2))
    else:
        # Run forever
        loop.run_forever(interval_minutes=args.interval)


if __name__ == "__main__":
    main()
