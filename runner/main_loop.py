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
        self.executor = ExecutionEngine(mode=self.mode, exchange=self.exchange, policy=self.policy_config)
        
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
            
            # Step 5a: Check if we need to rebalance BEFORE attempting execution (LIVE/PAPER only)
            if self.mode != "DRY_RUN" and approved_proposals:
                # Check actual USDC balance
                try:
                    accounts = self.exchange.get_accounts()
                    usdc_balance = 0.0
                    for acc in accounts:
                        if acc['currency'] in ['USDC', 'USD']:
                            usdc_balance += float(acc.get('available_balance', {}).get('value', 0))
                    
                    # Calculate required USDC (rough estimate)
                    total_needed = sum(p.size_pct * self.portfolio.account_value_usd / 100 for p in approved_proposals)
                    
                    if usdc_balance < total_needed * 0.5:  # If less than 50% of needed capital in USDC
                        logger.warning(
                            f"Low USDC balance: ${usdc_balance:.2f} available, "
                            f"${total_needed:.2f} needed for trades"
                        )
                        logger.info("💡 Auto-rebalancing to raise USDC capital...")
                        
                        if self._auto_rebalance_for_trade(approved_proposals):
                            logger.info("✅ Rebalancing successful, retrying execution...")
                        else:
                            logger.warning("⚠️ Rebalancing failed or declined")
                except Exception as e:
                    logger.error(f"Failed to check USDC balance for rebalancing: {e}")
            
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
    
    def _auto_rebalance_for_trade(self, proposals: List[TradeProposal]) -> bool:
        """
        Automatically liquidate worst-performing position to raise capital.
        
        Strategy:
        1. Find worst-performing holdings (by 24h change)
        2. Liquidate enough to cover the new trade
        3. Skip if new opportunity is worse than current holdings
        
        Returns:
            True if rebalancing succeeded, False otherwise
        """
        logger.info("💡 Auto-rebalancing: liquidating worst performer to raise capital...")
        
        try:
            # Get liquidation candidates (worst performers first)
            candidates = self.executor.get_liquidation_candidates(
                min_value_usd=5.0,  # Only consider holdings > $5
                sort_by="performance"
            )
            
            if not candidates:
                logger.warning("No liquidation candidates found")
                return False
            
            worst = candidates[0]
            logger.info(
                f"Worst performer: {worst['currency']} "
                f"({worst['change_24h_pct']:+.2f}% 24h, ${worst['value_usd']:.2f})"
            )
            
            # Check if new opportunity is better than worst holding
            # (Don't sell something that's only down -2% to buy something with low conviction)
            best_proposal = max(proposals, key=lambda p: p.confidence)
            
            if worst['change_24h_pct'] > -5.0 and best_proposal.confidence < 0.7:
                logger.info(
                    f"Skipping rebalance: worst holding only {worst['change_24h_pct']:.1f}% down, "
                    f"new opportunity confidence {best_proposal.confidence:.2f} not high enough"
                )
                return False
            
            # Get account UUIDs for conversion
            accounts = self.exchange.get_accounts()
            from_account = next((a for a in accounts if a['currency'] == worst['currency']), None)
            to_account = next((a for a in accounts if a['currency'] == 'USDC'), None)
            
            if not from_account or not to_account:
                logger.error(f"Cannot find account UUIDs for {worst['currency']} or USDC")
                return False
            
            # Liquidate worst performer to USDC
            logger.info(f"Converting {worst['currency']} → USDC to raise capital...")
            
            result = self.executor.convert_asset(
                from_currency=worst['currency'],
                to_currency='USDC',
                amount=str(worst['balance']),
                from_account_uuid=from_account['uuid'],
                to_account_uuid=to_account['uuid']
            )
            
            # convert_asset returns a dict, not ExecutionResult
            if isinstance(result, dict) and result.get('success'):
                amount_converted = result.get('amount', worst['value_usd'])
                logger.info(
                    f"✅ Liquidated {worst['currency']}: "
                    f"freed up ~${amount_converted:.2f} USDC"
                )
                
                # Update portfolio state with new balance
                self.portfolio = self._init_portfolio_state()
                return True
            else:
                error_msg = result.get('error', 'Unknown error') if isinstance(result, dict) else str(result)
                logger.warning(f"❌ Liquidation failed: {error_msg}")
                
                # If conversion failed, try selling via market order instead
                logger.info(f"Attempting to sell {worst['currency']} via market order...")
                return self._sell_via_market_order(worst['currency'], worst['balance'])
                
        except Exception as e:
            logger.error(f"Auto-rebalance failed: {e}")
            return False
    
    def _sell_via_market_order(self, currency: str, balance: float) -> bool:
        """
        Fallback: Sell asset via market order (e.g., XRP-USD) instead of Convert API.
        
        Args:
            currency: Asset to sell (e.g., "HBAR")
            balance: Amount of asset to sell (in units, not USD)
            
        Returns:
            True if sell succeeded, False otherwise
        """
        try:
            # Try to find a trading pair for this asset
            pair = f"{currency}-USD"
            
            logger.info(f"Trying to sell {balance:.2f} {currency} via {pair} market order...")
            
            # Get current price to convert units to USD value
            try:
                quote = self.exchange.get_quote(pair)
                size_usd = balance * quote.mid
            except Exception as e:
                logger.error(f"Failed to get quote for {pair}: {e}")
                return False
            
            logger.info(f"Estimated value: ${size_usd:.2f} USD")
            
            result = self.executor.execute(
                symbol=pair,
                side="SELL",
                size_usd=size_usd
            )
            
            if result.success:
                logger.info(f"✅ Sold {currency} via {pair}: ${result.filled_size * result.filled_price:.2f}")
                self.portfolio = self._init_portfolio_state()
                return True
            else:
                logger.warning(f"❌ Market order failed for {pair}: {result.error}")
                return False
                
        except Exception as e:
            logger.error(f"Market order sell failed: {e}")
            return False
    
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
