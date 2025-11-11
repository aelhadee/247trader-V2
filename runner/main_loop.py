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
import signal
import yaml
from typing import Dict, List, Optional
from datetime import datetime, timedelta, timezone
from pathlib import Path
import logging

from core.exchange_coinbase import CoinbaseExchange
from core.universe import UniverseManager
from core.triggers import TriggerEngine
from strategy.rules_engine import RulesEngine, TradeProposal
from core.risk import RiskEngine, PortfolioState
from core.execution import ExecutionEngine
from infra.state_store import StateStore
from core.audit_log import AuditLogger

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
        
        # Mode & safety
        self.mode = self.app_config.get("app", {}).get("mode", "DRY_RUN").upper()
        
        if self.mode not in ("DRY_RUN", "PAPER", "LIVE"):
            raise ValueError(f"Invalid mode: {self.mode}")
        
        # Exchange read_only: True unless explicitly false in LIVE mode
        exchange_config = self.app_config.get("exchange", {})
        read_only_cfg = exchange_config.get("read_only", True)
        self.read_only = (self.mode != "LIVE") or read_only_cfg
        
        # Logging setup
        log_cfg = self.app_config.get("logging", {})
        log_file = log_cfg.get("file", "logs/247trader-v2.log")
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        
        logging.basicConfig(
            level=getattr(logging, log_cfg.get("level", "INFO").upper()),
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
            handlers=[logging.FileHandler(log_file), logging.StreamHandler()]
        )
        
        logger.info(f"Starting 247trader-v2 in mode={self.mode}, read_only={self.read_only}")
        
        # Initialize core components
        self.exchange = CoinbaseExchange(read_only=self.read_only)
        self.state_store = StateStore()
        self.audit = AuditLogger(audit_file=log_file.replace('.log', '_audit.jsonl'))
        
        self.universe_mgr = UniverseManager(self.config_dir / "universe.yaml")
        self.trigger_engine = TriggerEngine()
        self.rules_engine = RulesEngine(config={})
        self.risk_engine = RiskEngine(self.policy_config, universe_manager=self.universe_mgr)
        self.executor = ExecutionEngine(exchange=self.exchange, policy=self.policy_config)
        
        # State
        self.portfolio = self._init_portfolio_state()
        self.current_regime = "chop"  # TODO: Replace with regime detector
        
        # Shutdown flag
        self._running = True
        signal.signal(signal.SIGINT, self._handle_stop)
        signal.signal(signal.SIGTERM, self._handle_stop)
        
        logger.info(f"Initialized TradingLoop in {self.mode} mode")
    
    def _handle_stop(self, *_):
        """Handle shutdown signals"""
        logger.warning("Shutdown signal received, stopping after current cycle.")
        self._running = False
    
    def _load_yaml(self, filename: str) -> dict:
        """Load YAML config file"""
        path = self.config_dir / filename
        with open(path) as f:
            return yaml.safe_load(f)
    
    def _init_portfolio_state(self) -> PortfolioState:
        """Initialize portfolio state from state store"""
        state = self.state_store.load()
        
        # Get real account value from Coinbase (fallback to 10k for DRY_RUN)
        if self.mode == "DRY_RUN":
            account_value_usd = 10_000.0
        else:
            try:
                accounts = self.exchange.get_accounts()
                account_value_usd = 0.0
                
                # Sum all balances (convert to USD)
                for acc in accounts:
                    currency = acc['currency']
                    balance = float(acc.get('available_balance', {}).get('value', 0))
                    
                    if balance == 0:
                        continue
                    
                    # USD/USDC/USDT are 1:1
                    if currency in ['USD', 'USDC', 'USDT']:
                        account_value_usd += balance
                    else:
                        # Get USD value for crypto holdings
                        try:
                            pair = f"{currency}-USD"
                            quote = self.exchange.get_quote(pair)
                            account_value_usd += balance * quote.mid
                        except:
                            # Try USDC if USD fails
                            try:
                                pair = f"{currency}-USDC"
                                quote = self.exchange.get_quote(pair)
                                account_value_usd += balance * quote.mid
                            except:
                                pass
                
                logger.info(f"Real account value: ${account_value_usd:.2f}")
            except Exception as e:
                logger.warning(f"Could not fetch account value: {e}, using fallback")
                account_value_usd = 10_000.0
        
        return PortfolioState(
            account_value_usd=account_value_usd,
            open_positions=state.get("positions", {}),
            daily_pnl_pct=state.get("pnl_today", 0.0),
            max_drawdown_pct=0.0,  # TODO: Calculate from history
            trades_today=state.get("trades_today", 0),
            trades_this_hour=state.get("trades_this_hour", 0),
            consecutive_losses=state.get("consecutive_losses", 0),
            last_loss_time=datetime.fromisoformat(state["last_loss_time"]) if state.get("last_loss_time") else None,
            current_time=datetime.utcnow()
        )
    
    def run_cycle(self):
        """
        Execute one trading cycle with full safety guarantees.
        
        Any exception -> NO_TRADE, audit, continue
        """
        cycle_started = datetime.now(timezone.utc)
        logger.info("=" * 80)
        logger.info(f"CYCLE START: {cycle_started.isoformat()}")
        logger.info("=" * 80)
        
        try:
            # Step 1: Build universe
            logger.info("Step 1: Building universe...")
            universe = self.universe_mgr.get_universe(regime=self.current_regime)
            
            if not universe or universe.total_eligible == 0:
                reason = "empty_universe"
                logger.info(f"NO_TRADE: {reason}")
                self.audit.log_cycle(
                    ts=cycle_started,
                    mode=self.mode,
                    universe=universe,
                    triggers=None,
                    base_proposals=[],
                    risk_approved=[],
                    final_orders=[],
                    no_trade_reason=reason,
                )
                return
            
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
            
            if not triggers or len(triggers) == 0:
                reason = "no_candidates_from_triggers"
                logger.debug(f"NO_TRADE: {reason}")
                self.audit.log_cycle(
                    ts=cycle_started,
                    mode=self.mode,
                    universe=universe,
                    triggers=triggers,
                    base_proposals=[],
                    risk_approved=[],
                    final_orders=[],
                    no_trade_reason=reason,
                )
                return
            
            logger.info(f"Triggers: {len(triggers)} detected")
            
            # Step 3: Generate trade proposals
            logger.info("Step 3: Generating trade proposals...")
            proposals = self.rules_engine.propose_trades(
                universe=universe,
                triggers=triggers,
                regime=self.current_regime
            )
            
            if not proposals:
                reason = "rules_engine_no_proposals"
                logger.info(f"NO_TRADE: {reason}")
                self.audit.log_cycle(
                    ts=cycle_started,
                    mode=self.mode,
                    universe=universe,
                    triggers=triggers,
                    base_proposals=[],
                    risk_approved=[],
                    final_orders=[],
                    no_trade_reason=reason,
                )
                return
            
            logger.info(f"Proposals: {len(proposals)} generated")
            
            # Step 4: Apply risk checks
            logger.info("Step 4: Applying risk checks...")
            risk_result = self.risk_engine.check_all(
                proposals=proposals,
                portfolio=self.portfolio,
                regime=self.current_regime
            )
            
            if not risk_result.approved or not risk_result.approved_proposals:
                reason = risk_result.reason or "all_proposals_blocked_by_risk"
                logger.warning(f"Risk check FAILED: {reason}")
                self.audit.log_cycle(
                    ts=cycle_started,
                    mode=self.mode,
                    universe=universe,
                    triggers=triggers,
                    base_proposals=proposals,
                    risk_approved=[],
                    final_orders=[],
                    no_trade_reason=reason,
                    risk_violations=risk_result.violated_checks,
                )
                return
            
            # Use the filtered approved proposals from risk engine
            approved_proposals = risk_result.approved_proposals
            logger.info(f"Risk checks PASSED: {len(approved_proposals)}/{len(proposals)} proposals approved")
            
            # Step 5: Execute trades (respects mode: DRY_RUN/PAPER/LIVE)
            logger.info(f"Step 5: Executing {len(approved_proposals)} approved trades...")
            
            # Get available capital and adjust position sizes
            adjusted_proposals = self.executor.adjust_proposals_to_capital(
                approved_proposals, 
                self.portfolio.account_value_usd
            )
            
            if len(adjusted_proposals) < len(approved_proposals):
                logger.warning(f"Capital constraints: executing {len(adjusted_proposals)}/{len(approved_proposals)} trades")
            
            final_orders = []
            
            if self.mode == "DRY_RUN":
                logger.info("DRY_RUN mode - no actual execution")
            else:
                # Execute each adjusted proposal
                for proposal, size_usd in adjusted_proposals:
                    logger.info(f"Executing: {proposal.side} {proposal.symbol} (${size_usd:.2f})")
                    
                    result = self.executor.execute(
                        symbol=proposal.symbol,
                        side=proposal.side,
                        size_usd=size_usd
                    )
                    
                    if result.success:
                        logger.info(f"✅ Trade executed: {proposal.symbol} - Order ID: {result.order_id}")
                        final_orders.append(result)
                    else:
                        logger.warning(f"⚠️ Trade failed: {proposal.symbol} - {result.error}")
            
            # Update state after fills
            if final_orders:
                self.state_store.update_from_fills(final_orders, self.portfolio)
                logger.info(f"Executed {len(final_orders)} order(s)")
            else:
                logger.info("NO_TRADE: execution layer filtered all proposals (liquidity/slippage/notional/etc)")
            
            # Audit cycle
            self.audit.log_cycle(
                ts=cycle_started,
                mode=self.mode,
                universe=universe,
                triggers=triggers,
                base_proposals=proposals,
                risk_approved=approved_proposals,
                final_orders=final_orders,
                no_trade_reason=None if final_orders else "no_orders_after_execution_filter",
            )
            
            cycle_end = datetime.now(timezone.utc)
            cycle_duration = (cycle_end - cycle_started).total_seconds()
            logger.info(f"CYCLE COMPLETE: {cycle_duration:.2f}s")
            
        except Exception as e:
            # Hard rule: any unexpected error => NO_TRADE this cycle
            logger.exception(f"Error in run_cycle: {e}")
            self.audit.log_cycle(
                ts=cycle_started,
                mode=self.mode,
                universe=None,
                triggers=None,
                base_proposals=[],
                risk_approved=[],
                final_orders=[],
                no_trade_reason=f"exception:{type(e).__name__}",
            )
    
    def run_forever(self, interval_seconds: int = 300):
        """
        Run trading loop continuously with time-aware sleep.
        
        Args:
            interval_seconds: Seconds between cycle starts
        """
        logger.info(f"Starting continuous loop (interval={interval_seconds}s)")
        
        while self._running:
            start = time.monotonic()
            self.run_cycle()
            elapsed = time.monotonic() - start
            sleep_for = max(1.0, interval_seconds - elapsed)
            logger.info(f"Cycle took {elapsed:.2f}s, sleeping {sleep_for:.2f}s")
            time.sleep(sleep_for)
        
        logger.info("Trading loop stopped cleanly.")


def main():
    """Entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(description="247trader-v2 Trading Bot")
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    parser.add_argument("--interval", type=float, default=300, help="Seconds between cycles (default: 300)")
    parser.add_argument("--config-dir", default="config", help="Config directory")
    
    args = parser.parse_args()
    
    # Create loop (logging configured in __init__)
    loop = TradingLoop(config_dir=args.config_dir)
    
    if args.once:
        # Run once
        loop.run_cycle()
    else:
        # Run forever
        loop.run_forever(interval_seconds=args.interval)


if __name__ == "__main__":
    main()
