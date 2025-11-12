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
from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta, timezone
from pathlib import Path
import logging

from core.exchange_coinbase import CoinbaseExchange
from core.exceptions import CriticalDataUnavailable
from core.universe import UniverseManager
from core.triggers import TriggerEngine
from strategy.rules_engine import RulesEngine, TradeProposal
from core.risk import RiskEngine, PortfolioState
from core.execution import ExecutionEngine, ExecutionResult
from infra.alerting import AlertService, AlertSeverity
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
        
        # Validate configs before loading
        from tools.config_validator import validate_all_configs
        validation_errors = validate_all_configs(config_dir)
        if validation_errors:
            logger.error("=" * 80)
            logger.error("CONFIGURATION VALIDATION FAILED")
            logger.error("=" * 80)
            for error in validation_errors:
                logger.error(f"  • {error}")
            logger.error("=" * 80)
            raise ValueError(f"Invalid configuration: {len(validation_errors)} error(s) found")
        
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
        
        # CRITICAL: Acquire single-instance lock to prevent double-trading
        from infra.instance_lock import check_single_instance
        self.instance_lock = check_single_instance("247trader-v2", lock_dir="data")
        if not self.instance_lock:
            logger.error("=" * 80)
            logger.error("ANOTHER INSTANCE IS ALREADY RUNNING")
            logger.error("=" * 80)
            logger.error("Cannot start - only ONE instance allowed to prevent:")
            logger.error("  • Double trading (exceeding risk limits)")
            logger.error("  • State corruption (concurrent writes)")
            logger.error("  • API rate limit exhaustion")
            logger.error("")
            logger.error("If you're sure no other instance is running, check for stale PID file:")
            logger.error("  rm data/247trader-v2.pid")
            logger.error("=" * 80)
            raise RuntimeError("Another trading bot instance is already running")
        
        logger.info("✅ Single-instance lock acquired")
        
        # Initialize core components
        self.exchange = CoinbaseExchange(read_only=self.read_only)
        self.state_store = StateStore()
        self.audit = AuditLogger(audit_file=log_file.replace('.log', '_audit.jsonl'))

        monitoring_cfg = self.app_config.get("monitoring", {}) or {}
        self.alerts = AlertService.from_config(
            monitoring_cfg.get("alerts_enabled", False),
            monitoring_cfg.get("alerts"),
        )
        if self.alerts.is_enabled():
            logger.info(
                "Alerting enabled (min_severity=%s)",
                monitoring_cfg.get("alerts", {}).get("min_severity", "warning"),
            )
        
        self.universe_mgr = UniverseManager(self.config_dir / "universe.yaml")
        self.trigger_engine = TriggerEngine()
        self.rules_engine = RulesEngine(config={})
        self.risk_engine = RiskEngine(
            self.policy_config, 
            universe_manager=self.universe_mgr,
            exchange=self.exchange,
            alert_service=self.alerts  # CRITICAL: Wire alerts for safety notifications
        )
        self.executor = ExecutionEngine(
            mode=self.mode,
            exchange=self.exchange,
            policy=self.policy_config,
            state_store=self.state_store,
        )
        
        # State
        self.portfolio = self._init_portfolio_state()
        self.current_regime = "chop"  # TODO: Replace with regime detector
        
        # Shutdown flag
        self._running = True
        signal.signal(signal.SIGINT, self._handle_stop)
        signal.signal(signal.SIGTERM, self._handle_stop)
        
        logger.info(f"Initialized TradingLoop in {self.mode} mode")
    
    def _handle_stop(self, *_):
        """
        Handle shutdown signals with graceful cleanup.
        
        Strategy:
        1. Set _running = False to stop after current cycle
        2. Cancel all active orders from OrderStateMachine
        3. Flush StateStore to disk
        4. Log cleanup summary
        
        Safety:
        - Only cancels orders if not in DRY_RUN mode
        - Logs all actions for audit trail
        - Continues even if individual cleanup steps fail
        """
        logger.warning("=" * 80)
        logger.warning("SHUTDOWN SIGNAL RECEIVED - Initiating graceful shutdown")
        logger.warning("=" * 80)
        
        # Stop loop after current cycle
        self._running = False
        
        # Graceful cleanup (only if not DRY_RUN)
        if self.mode == "DRY_RUN":
            logger.info("DRY_RUN mode - skipping order cancellation")
            return
        
        cleanup_summary = {
            "orders_canceled": 0,
            "cancel_errors": 0,
            "state_flushed": False,
        }
        
        try:
            # Step 1: Cancel all active orders
            logger.info("Step 1: Canceling active orders...")
            
            # Get all active (non-terminal) orders from OrderStateMachine
            active_orders = self.executor.order_state_machine.get_active_orders()
            
            if not active_orders:
                logger.info("No active orders to cancel")
            else:
                logger.info(f"Found {len(active_orders)} active orders to cancel")
                
                # Group by exchange order ID for batch cancellation
                order_ids_to_cancel = []
                client_id_map = {}
                
                for order in active_orders:
                    if order.order_id:
                        order_ids_to_cancel.append(order.order_id)
                        client_id_map[order.order_id] = order.client_order_id
                        logger.info(f"  - {order.symbol} {order.side} (order_id={order.order_id})")
                    else:
                        logger.warning(f"  - {order.symbol} {order.side} (no exchange order_id, skipping)")
                
                # Cancel orders via exchange
                if order_ids_to_cancel:
                    try:
                        if len(order_ids_to_cancel) == 1:
                            # Single order cancel
                            result = self.exchange.cancel_order(order_ids_to_cancel[0])
                            if result.get("success"):
                                cleanup_summary["orders_canceled"] = 1
                                logger.info(f"✅ Canceled order {order_ids_to_cancel[0]}")
                            else:
                                cleanup_summary["cancel_errors"] = 1
                                logger.warning(f"⚠️ Failed to cancel order {order_ids_to_cancel[0]}: {result.get('error')}")
                        else:
                            # Batch cancel
                            result = self.exchange.cancel_orders(order_ids_to_cancel)
                            if result.get("success") or "results" in result:
                                cleanup_summary["orders_canceled"] = len(order_ids_to_cancel)
                                logger.info(f"✅ Batch canceled {len(order_ids_to_cancel)} orders")
                            else:
                                cleanup_summary["cancel_errors"] = len(order_ids_to_cancel)
                                logger.warning(f"⚠️ Batch cancel failed: {result.get('error')}")
                        
                        # Transition canceled orders to CANCELED state
                        from core.order_state import OrderStatus
                        for order_id, client_id in client_id_map.items():
                            if client_id:
                                try:
                                    self.executor.order_state_machine.transition(
                                        client_id,
                                        OrderStatus.CANCELED
                                    )
                                    # Close in state store
                                    if self.state_store:
                                        self.executor._close_order_in_state_store(
                                            client_id,
                                            "canceled",
                                            {"reason": "graceful_shutdown"}
                                        )
                                except Exception as e:
                                    logger.warning(f"Failed to transition order {client_id} to CANCELED: {e}")
                        
                    except Exception as e:
                        cleanup_summary["cancel_errors"] = len(order_ids_to_cancel)
                        logger.error(f"Failed to cancel orders: {e}", exc_info=True)
            
            # Step 2: Flush StateStore to disk
            logger.info("Step 2: Flushing StateStore to disk...")
            try:
                # StateStore saves automatically on each operation, but force a final save
                state = self.state_store.load()
                self.state_store.save(state)
                cleanup_summary["state_flushed"] = True
                logger.info("✅ StateStore flushed to disk")
            except Exception as e:
                logger.error(f"Failed to flush StateStore: {e}", exc_info=True)
            
            # Step 3: Log cleanup summary
            logger.warning("=" * 80)
            logger.warning("GRACEFUL SHUTDOWN COMPLETE")
            logger.warning(f"  Orders canceled: {cleanup_summary['orders_canceled']}")
            logger.warning(f"  Cancel errors: {cleanup_summary['cancel_errors']}")
            logger.warning(f"  State flushed: {cleanup_summary['state_flushed']}")
            logger.warning("=" * 80)
            
        except Exception as e:
            logger.error(f"Error during graceful shutdown: {e}", exc_info=True)
            logger.warning("Shutdown will continue despite cleanup errors")
        
        finally:
            # CRITICAL: Release single-instance lock
            if hasattr(self, 'instance_lock') and self.instance_lock:
                logger.info("Releasing single-instance lock...")
                self.instance_lock.release()
                logger.info("✅ Lock released")
    
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
                accounts = self._require_accounts("portfolio_init")
            except CriticalDataUnavailable as data_exc:
                logger.warning("Account lookup failed (%s); using last stored cash balance", data_exc.source)
                stored_balances = state.get("cash_balances", {})
                account_value_usd = sum(float(v) for v in stored_balances.values())
                if account_value_usd <= 0:
                    account_value_usd = 10_000.0
                accounts = []
            else:
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
                        except Exception:
                            # Try USDC if USD fails
                            try:
                                pair = f"{currency}-USDC"
                                quote = self.exchange.get_quote(pair)
                                account_value_usd += balance * quote.mid
                            except Exception:
                                continue

                logger.info(f"Real account value: ${account_value_usd:.2f}")
        
        positions = self._normalize_positions_for_risk(state.get("positions", {}))

        pnl_today_usd = float(state.get("pnl_today", 0.0) or 0.0)
        pnl_week_usd = float(state.get("pnl_week", 0.0) or 0.0)

        def _pct(pnl_usd: float) -> float:
            baseline = account_value_usd - pnl_usd
            if baseline <= 0:
                baseline = account_value_usd if account_value_usd > 0 else 1.0
            try:
                return (pnl_usd / baseline) * 100.0 if baseline else 0.0
            except ZeroDivisionError:
                return 0.0

        daily_pnl_pct = _pct(pnl_today_usd)
        weekly_pnl_pct = _pct(pnl_week_usd)

        # CRITICAL: Calculate actual max drawdown from state history
        # Use high_water_mark to track peak NAV and compute drawdown
        high_water_mark = float(state.get("high_water_mark", account_value_usd))
        
        # Update high water mark if current NAV is higher
        if account_value_usd > high_water_mark:
            high_water_mark = account_value_usd
            state["high_water_mark"] = high_water_mark
            self.state_store.save(state)
        
        # Calculate drawdown: (peak - current) / peak
        max_drawdown_pct = 0.0
        if high_water_mark > 0:
            max_drawdown_pct = ((high_water_mark - account_value_usd) / high_water_mark) * 100.0

        # CRITICAL: Hydrate pending_orders from open_orders to count toward risk caps
        pending_orders = self._build_pending_orders_from_state(state)
        
        return PortfolioState(
            account_value_usd=account_value_usd,
            open_positions=positions,
            daily_pnl_pct=daily_pnl_pct,
            max_drawdown_pct=max_drawdown_pct,
            trades_today=state.get("trades_today", 0),
            trades_this_hour=state.get("trades_this_hour", 0),
            consecutive_losses=state.get("consecutive_losses", 0),
            last_loss_time=datetime.fromisoformat(state["last_loss_time"]) if state.get("last_loss_time") else None,
            # Use timezone-aware UTC to avoid deprecation warning
            current_time=datetime.now(timezone.utc),
            weekly_pnl_pct=weekly_pnl_pct,
            pending_orders=pending_orders,
        )
    
    def _build_pending_orders_from_state(self, state: Dict[str, Any]) -> Dict[str, Dict[str, float]]:
        """
        Build pending_orders dict from persisted open_orders for risk accounting.
        
        Returns: {"buy": {"BTC-USD": notional_usd, ...}, "sell": {"ETH-USD": notional_usd, ...}}
        
        CRITICAL: This ensures open orders count toward exposure caps in RiskEngine.
        Without this, we can over-allocate while orders are working.
        """
        pending = {"buy": {}, "sell": {}}
        
        open_orders = state.get("open_orders", {})
        if not open_orders:
            return pending
        
        for order_id, order_data in open_orders.items():
            if not isinstance(order_data, dict):
                continue
            
            # Extract order details
            side = order_data.get("side", "").lower()  # "buy" or "sell"
            symbol = order_data.get("symbol", "")
            
            # Calculate notional value
            # For BUY: use order_value or (size * price)
            # For SELL: we don't count sell orders toward buy exposure
            if side == "buy":
                notional_usd = float(order_data.get("order_value_usd", 0.0))
                
                # Fallback: calculate from size * price if order_value not stored
                if notional_usd == 0.0:
                    size = float(order_data.get("size", 0.0))
                    price = float(order_data.get("price", 0.0))
                    notional_usd = size * price
                
                if symbol and notional_usd > 0:
                    pending["buy"][symbol] = pending["buy"].get(symbol, 0.0) + notional_usd
            
            elif side == "sell":
                # Track sell orders separately (not counted in buy exposure)
                notional_usd = float(order_data.get("order_value_usd", 0.0))
                if notional_usd == 0.0:
                    size = float(order_data.get("size", 0.0))
                    price = float(order_data.get("price", 0.0))
                    notional_usd = size * price
                
                if symbol and notional_usd > 0:
                    pending["sell"][symbol] = pending["sell"].get(symbol, 0.0) + notional_usd
        
        logger.debug(f"Pending orders from state: buy={pending['buy']}, sell={pending['sell']}")
        return pending

    @staticmethod
    def _normalize_positions_for_risk(raw_positions: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        """Ensure position entries expose 'usd' for risk calculations."""
        if not raw_positions:
            return {}

        normalized: Dict[str, Dict[str, Any]] = {}

        for key, value in raw_positions.items():
            if not isinstance(value, dict):
                continue

            entry = dict(value)

            units = entry.get("units")
            if units is None:
                units = entry.get("total") or entry.get("quantity")
            try:
                units_f = float(units) if units is not None else 0.0
            except (TypeError, ValueError):
                units_f = 0.0

            usd_value = entry.get("usd")
            if usd_value is None:
                usd_value = entry.get("usd_value") or entry.get("notional")
            try:
                usd_f = float(usd_value) if usd_value is not None else 0.0
            except (TypeError, ValueError):
                usd_f = 0.0

            if usd_f <= 0 and abs(units_f) <= 1e-9:
                continue

            symbol = key
            if "-" not in symbol:
                base = entry.get("currency") or key
                symbol = f"{base}-USD"

            entry["units"] = units_f
            entry["usd"] = usd_f
            entry.setdefault("currency", symbol.split('-')[0])
            normalized[symbol] = entry

        return normalized

    def _post_trade_refresh(self, executed_orders: List[ExecutionResult]) -> None:
        """Pull a fresh exchange snapshot after trades settle to avoid state drift."""
        if self.mode == "DRY_RUN":
            return

        if not executed_orders:
            return

        filled = [order for order in executed_orders if order and order.filled_size > 0]
        if not filled:
            logger.debug("Post-trade refresh skipped: no fills recorded yet.")
            return

        wait_cfg = self.app_config.get("execution", {}).get("post_trade_reconcile_wait_seconds")
        try:
            wait_seconds = float(wait_cfg) if wait_cfg is not None else 0.5
        except (TypeError, ValueError):
            wait_seconds = 0.5

        wait_seconds = max(0.0, min(wait_seconds, 5.0))
        if wait_seconds > 0:
            time.sleep(wait_seconds)

        # CRITICAL: Reconcile fills from exchange to update positions/fees/PnL
        try:
            logger.info("Reconciling fills from exchange...")
            reconcile_summary = self.executor.reconcile_fills(lookback_minutes=5)
            logger.info(
                f"Fill reconciliation complete: {reconcile_summary.get('fills_processed', 0)} fills, "
                f"{reconcile_summary.get('orders_updated', 0)} orders updated, "
                f"${reconcile_summary.get('total_fees', 0):.2f} fees, "
                f"PnL: ${reconcile_summary.get('realized_pnl_usd', 0):.2f}"
            )
        except Exception as exc:
            logger.error(f"Fill reconciliation failed: {exc}", exc_info=True)
            # Continue - reconcile_exchange_state will still update positions

        try:
            self._reconcile_exchange_state()
        except CriticalDataUnavailable as data_exc:
            logger.warning(
                "Post-trade reconcile failed (%s): %s",
                data_exc.source,
                str(data_exc.original) if data_exc.original else "unknown error",
            )
            return
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Post-trade reconcile skipped: %s", exc)
            return

        try:
            self.portfolio = self._init_portfolio_state()
            logger.debug("Portfolio snapshot refreshed after trade fills.")
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Failed to refresh portfolio after fills: %s", exc)
    
    def _apply_cooldowns_after_trades(
        self, 
        executed_orders: List[ExecutionResult],
        proposals: List[TradeProposal]
    ) -> None:
        """
        Apply per-symbol cooldowns after trade execution.
        
        For SELL orders (position closes), we apply cooldowns to prevent
        repeatedly trading losing positions. Currently applies cooldown to
        all SELL orders; future enhancement: track PnL to differentiate wins/losses.
        
        Args:
            executed_orders: Successfully executed orders
            proposals: Original proposals that led to these orders
        """
        if not self.policy_config.get("risk", {}).get("per_symbol_cooldown_enabled", True):
            return
        
        # Build proposal lookup by symbol
        proposal_by_symbol = {p.symbol: p for p in proposals}
        
        for order in executed_orders:
            if not order.success:
                continue
            
            # Extract symbol - handle both dict and object
            if isinstance(order, dict):
                symbol = order.get('symbol')
                side = order.get('side')
            else:
                symbol = getattr(order, 'symbol', None)
                # ExecutionResult doesn't have side, need to look up from proposal
                proposal = proposal_by_symbol.get(symbol)
                side = proposal.side if proposal else None
            
            if not symbol or not side:
                continue
            
            # Apply cooldown to SELL orders (position closes)
            # TODO: Track PnL to differentiate wins/losses and apply appropriate cooldown
            if side == "SELL":
                # For now, treat all sells as potential losses and apply standard cooldown
                # Future: detect stop-loss hits specifically and apply longer cooldown
                is_stop_loss = False  # TODO: detect actual stop-loss hits
                self.risk_engine.apply_symbol_cooldown(symbol, is_stop_loss=is_stop_loss)
                logger.info(f"Applied cooldown to {symbol} after SELL order")

    def _abort_cycle_due_to_data(self, cycle_started: datetime, source: str,
                                 detail: Optional[str] = None) -> None:
        reason = f"data_unavailable:{source}"
        msg = f"NO_TRADE: {reason}"
        if detail:
            msg += f" ({detail})"
        logger.warning(msg)
        self.alerts.notify(
            AlertSeverity.CRITICAL,
            "Data unavailable",
            msg,
            {
                "source": source,
                "detail": detail,
                "mode": self.mode,
            },
        )
        self.audit.log_cycle(
            ts=cycle_started,
            mode=self.mode,
            universe=None,
            triggers=None,
            base_proposals=[],
            risk_approved=[],
            final_orders=[],
            no_trade_reason=reason,
            state_store=self.state_store,
        )

    def _require_accounts(self, context: str) -> List[dict]:
        try:
            return self.exchange.get_accounts()
        except CriticalDataUnavailable:
            raise
        except Exception as exc:
            raise CriticalDataUnavailable(f"accounts:{context}", exc) from exc
    
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
            try:
                self._reconcile_exchange_state()
            except CriticalDataUnavailable as data_exc:
                self._abort_cycle_due_to_data(
                    cycle_started,
                    data_exc.source,
                    str(data_exc.original) if data_exc.original else None,
                )
                return

            # Refresh portfolio snapshot now that state store has authoritative data
            try:
                self.portfolio = self._init_portfolio_state()
            except CriticalDataUnavailable as data_exc:
                self._abort_cycle_due_to_data(
                    cycle_started,
                    data_exc.source,
                    str(data_exc.original) if data_exc.original else None,
                )
                return

            try:
                pending_orders = self._get_open_order_exposure()
            except CriticalDataUnavailable as data_exc:
                self._abort_cycle_due_to_data(
                    cycle_started,
                    data_exc.source,
                    str(data_exc.original) if data_exc.original else None,
                )
                return
            else:
                self.portfolio.pending_orders = pending_orders

            # Step 1: Build universe
            logger.info("Step 1: Building universe...")
            universe = self.universe_mgr.get_universe(regime=self.current_regime)

            # Optional purge: liquidate excluded/ineligible holdings proactively
            try:
                pm_cfg = self.policy_config.get("portfolio_management", {})
                if self.mode != "DRY_RUN" and pm_cfg.get("auto_liquidate_ineligible", False):
                    self._purge_ineligible_holdings(universe)
            except CriticalDataUnavailable as data_exc:
                self._abort_cycle_due_to_data(
                    cycle_started,
                    data_exc.source,
                    str(data_exc.original) if data_exc.original else None,
                )
                return
            except Exception as e:
                logger.warning(f"Purge step skipped: {e}")
            
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
            state_store=self.state_store,
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
            state_store=self.state_store,
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
                
                # Zero-trade sentinel: Track consecutive cycles with 0 proposals
                zero_count = self.state_store.increment_zero_proposal_cycles()
                
                # Auto-loosen if stuck at 0 proposals for 20 cycles (once only)
                if zero_count >= 20 and not self.state_store.has_auto_loosen_applied():
                    logger.warning(f"Zero-proposal sentinel triggered after {zero_count} cycles - auto-loosening thresholds")
                    self._apply_auto_loosen()
                    self.state_store.mark_auto_loosen_applied()
                
                self.audit.log_cycle(
                    ts=cycle_started,
                    mode=self.mode,
                    universe=universe,
                    triggers=triggers,
                    base_proposals=[],
                    risk_approved=[],
                    final_orders=[],
                    no_trade_reason=reason,
            state_store=self.state_store,
                )
                return
            
            # Proposals generated - reset zero-proposal counter
            self.state_store.reset_zero_proposal_cycles()
            
            # Avoid stacking buys while there are outstanding orders for the same base asset
            pending_buy_notional = (pending_orders or {}).get("buy", {})
            filtered = []
            for proposal in proposals:
                base = proposal.symbol.split('-')[0]
                if (
                    proposal.side.upper() == "BUY"
                    and pending_buy_notional.get(base, 0.0) >= self.executor.min_notional_usd
                ):
                    logger.info(
                        f"Skipping proposal for {proposal.symbol}: ${pending_buy_notional.get(base, 0.0):.2f} already pending"
                    )
                    continue
                filtered.append(proposal)

            if not filtered:
                reason = "open_orders_pending"
                logger.info(f"NO_TRADE: {reason}")
                self.audit.log_cycle(
                    ts=cycle_started,
                    mode=self.mode,
                    universe=universe,
                    triggers=triggers,
                    base_proposals=proposals,
                    risk_approved=[],
                    final_orders=[],
                    no_trade_reason=reason,
            state_store=self.state_store,
                )
                return

            if len(filtered) < len(proposals):
                logger.info(
                    f"Filtered proposals due to pending orders: {len(filtered)}/{len(proposals)} remain"
                )

            proposals = filtered

            logger.info(f"Proposals: {len(proposals)} generated")
            
            # Step 4: Apply risk checks (including circuit breakers)
            logger.info("Step 4: Applying risk checks...")
            risk_result = self.risk_engine.check_all(
                proposals=proposals,
                portfolio=self.portfolio,
                regime=self.current_regime
            )
            
            # Record successful API operations for circuit breaker tracking
            self.risk_engine.record_api_success()
            
            if not risk_result.approved or not risk_result.approved_proposals:
                reason = risk_result.reason or "all_proposals_blocked_by_risk"
                
                # Check if this was a circuit breaker trip
                if any(check in ['rate_limit_cooldown', 'api_health', 'exchange_connectivity', 
                               'exchange_health', 'volatility_crash'] 
                       for check in risk_result.violated_checks):
                    logger.error(f"CIRCUIT BREAKER TRIPPED: {reason}")
                    self.alerts.notify(
                        AlertSeverity.CRITICAL,
                        "Circuit breaker tripped",
                        reason,
                        {
                            "violated_checks": risk_result.violated_checks,
                            "mode": self.mode,
                        },
                    )
                else:
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
            state_store=self.state_store,
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
                try:
                    pm_cfg = self.policy_config.get("portfolio_management", {})
                    if not pm_cfg.get("auto_rebalance_worst_performer", True):
                        pass
                    else:
                        # Compute available stable buying power (USD + USDC + USDT)
                        try:
                            accounts = self._require_accounts("rebalance_check")
                        except CriticalDataUnavailable as data_exc:
                            self._abort_cycle_due_to_data(
                                cycle_started,
                                data_exc.source,
                                str(data_exc.original) if data_exc.original else None,
                            )
                            return
                        stable_currencies = {"USD", "USDC", "USDT"}
                        stable_available = sum(
                            float(acc.get('available_balance', {}).get('value', 0))
                            for acc in accounts if acc.get('currency') in stable_currencies
                        )

                        # Total required notional for this batch
                        total_needed = sum(p.size_pct * self.portfolio.account_value_usd / 100 for p in approved_proposals)

                        logger.info(
                            f"Stable buying power: ${stable_available:.2f} across USD/USDC/USDT; "
                            f"needed: ${total_needed:.2f}"
                        )

                        # If we don't have enough stable to fund the plan, liquidate worst performer
                        if stable_available + 1e-6 < total_needed:
                            logger.warning(
                                f"Insufficient stable capital: have ${stable_available:.2f}, "
                                f"need ${total_needed:.2f}. Attempting worst-performer liquidation."
                            )
                            deficit_usd = total_needed - stable_available
                            if self._auto_rebalance_for_trade(approved_proposals, deficit_usd):
                                logger.info("✅ Rebalancing successful, continuing to execution...")
                                # Refresh portfolio after rebalancing
                                self.portfolio = self._init_portfolio_state()
                            else:
                                logger.warning("⚠️ Rebalancing failed or declined")
                except Exception as e:
                    logger.error(f"Failed to evaluate rebalancing need: {e}")
            
            # Get available capital and adjust position sizes
            try:
                adjusted_proposals = self.executor.adjust_proposals_to_capital(
                    approved_proposals,
                    self.portfolio.account_value_usd,
                )
            except CriticalDataUnavailable as data_exc:
                self._abort_cycle_due_to_data(
                    cycle_started,
                    data_exc.source,
                    str(data_exc.original) if data_exc.original else None,
                )
                return
            
            if len(adjusted_proposals) < len(approved_proposals):
                logger.warning(f"Capital constraints: executing {len(adjusted_proposals)}/{len(approved_proposals)} trades")
            
            final_orders = []
            
            if self.mode == "DRY_RUN":
                logger.info("DRY_RUN mode - no actual execution")
            else:
                # Execute each adjusted proposal
                for proposal, size_usd in adjusted_proposals:
                    logger.info(f"Executing: {proposal.side} {proposal.symbol} (${size_usd:.2f})")
                    
                    # Extract tier from proposal asset if available
                    tier = proposal.asset.tier if proposal.asset else None
                    
                    try:
                        result = self.executor.execute(
                            symbol=proposal.symbol,
                            side=proposal.side,
                            size_usd=size_usd,
                            tier=tier,
                        )
                    except CriticalDataUnavailable as data_exc:
                        self._abort_cycle_due_to_data(
                            cycle_started,
                            data_exc.source,
                            str(data_exc.original) if data_exc.original else None,
                        )
                        return
                    
                    if result.success:
                        logger.info(f"✅ Trade executed: {proposal.symbol} - Order ID: {result.order_id}")
                        final_orders.append(result)
                    else:
                        logger.warning(f"⚠️ Trade failed: {proposal.symbol} - {result.error}")
            
            # Update state after fills
            if final_orders:
                self.state_store.update_from_fills(final_orders, self.portfolio)
                self._post_trade_refresh(final_orders)
                self._apply_cooldowns_after_trades(final_orders, approved_proposals)
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
                state_store=self.state_store,
            )

            # Post-cycle maintenance: cancel stale open orders (LIVE/PAPER)
            try:
                if self.mode in ("LIVE", "PAPER"):
                    self.executor.manage_open_orders()
            except Exception as e:
                logger.warning(f"Open order maintenance skipped: {e}")
            
            cycle_end = datetime.now(timezone.utc)
            cycle_duration = (cycle_end - cycle_started).total_seconds()
            logger.info(f"CYCLE COMPLETE: {cycle_duration:.2f}s")
            
        except Exception as e:
            # Hard rule: any unexpected error => NO_TRADE this cycle
            logger.exception(f"Error in run_cycle: {e}")
            
            # Track API errors for circuit breaker
            import requests
            if isinstance(e, (requests.exceptions.RequestException, requests.exceptions.HTTPError)):
                self.risk_engine.record_api_error()
                if isinstance(e, requests.exceptions.HTTPError) and e.response and e.response.status_code == 429:
                    self.risk_engine.record_rate_limit()
            
            self.alerts.notify(
                AlertSeverity.CRITICAL,
                "Trading loop exception",
                str(e),
                {
                    "exception": type(e).__name__,
                    "mode": self.mode,
                },
            )
            self.audit.log_cycle(
                ts=cycle_started,
                mode=self.mode,
                universe=None,
                triggers=None,
                base_proposals=[],
                risk_approved=[],
                final_orders=[],
                no_trade_reason=f"exception:{type(e).__name__}",
                state_store=self.state_store,
            )

    def _purge_ineligible_holdings(self, universe) -> None:
        """Sell holdings that are excluded or currently ineligible.

        - Skips preferred quote currencies (USD/USDC/USDT/BTC/ETH)
        - Only sells if estimated USD value >= portfolio_management.min_liquidation_value_usd
        - Limits to portfolio_management.max_liquidations_per_cycle per cycle
        - Uses market sell fallback path and respects exchange constraints
        """
        pm_cfg = self.policy_config.get("portfolio_management", {})
        min_value = float(pm_cfg.get("min_liquidation_value_usd", 10))
        max_liqs = int(pm_cfg.get("max_liquidations_per_cycle", 2))

        excluded = set(universe.excluded_assets or [])
        eligible_symbols = {a.symbol for a in universe.get_all_eligible()}

        accounts = self._require_accounts("purge_ineligible")

        liquidations = 0
        for acc in accounts:
            if liquidations >= max_liqs:
                break

            currency = acc.get('currency')
            balance = float(acc.get('available_balance', {}).get('value', 0))

            if not currency or balance <= 0:
                continue

            # Skip preferred quote currencies
            if currency in ["USD", "USDC", "USDT", "BTC", "ETH"]:
                continue

            symbol = f"{currency}-USD"
            is_excluded = symbol in excluded
            is_not_eligible = symbol not in eligible_symbols

            if not (is_excluded or is_not_eligible):
                continue

            # Estimate USD value
            try:
                quote = self.exchange.get_quote(symbol)
                value_usd = balance * quote.mid
            except Exception as e:
                logger.info(f"Skip purge for {symbol}: cannot quote ({e})")
                continue

            if value_usd < min_value:
                continue

            tag = "excluded" if is_excluded else "ineligible"
            logger.info(f"Purge: selling {balance:.6f} {currency} ({tag}), ~${value_usd:.2f}")

            if self._sell_via_market_order(currency, balance):
                liquidations += 1
            else:
                logger.warning(f"Purge sell failed for {symbol}")
    
    def _reconcile_exchange_state(self) -> None:
        """Refresh the persistent state store with the latest exchange snapshot."""
        if self.mode == "DRY_RUN":
            return

        timestamp = datetime.now(timezone.utc)

        accounts = self._require_accounts("reconcile_state")

        cash_balances: Dict[str, float] = {}
        positions: Dict[str, Dict[str, float]] = {}

        for acc in accounts:
            currency = acc.get("currency")
            if not currency:
                continue

            available = float(acc.get("available_balance", {}).get("value", 0))
            hold = float(acc.get("hold", {}).get("value", 0))
            total = available + hold

            if currency in {"USD", "USDC", "USDT"}:
                cash_balances[currency] = available
                continue

            if total <= 0:
                continue

            position = {
                "available": available,
                "hold": hold,
                "total": total,
            }

            try:
                quote = self.exchange.get_quote(f"{currency}-USD")
                position["usd_value"] = total * quote.mid
            except Exception:
                try:
                    quote = self.exchange.get_quote(f"{currency}-USDC")
                    position["usd_value"] = total * quote.mid
                except Exception:
                    position["usd_value"] = 0.0

            positions[currency] = position

        try:
            open_orders_raw = self.exchange.list_open_orders()
        except CriticalDataUnavailable:
            raise
        except Exception as exc:
            raise CriticalDataUnavailable("open_orders", exc) from exc

        open_orders_snapshot: Dict[str, Dict[str, Any]] = {}
        for order in open_orders_raw or []:
            built = ExecutionEngine.build_state_store_order_payload(order)
            if not built:
                continue
            key, payload = built
            open_orders_snapshot[key] = payload

        self.state_store.reconcile_exchange_snapshot(
            positions=positions,
            cash_balances=cash_balances,
            open_orders=open_orders_snapshot,
            timestamp=timestamp,
        )

        logger.debug(
            "Reconciled state snapshot (positions=%d, open_orders=%d)",
            len(positions),
            len(open_orders_snapshot),
        )

    def _get_open_order_exposure(self) -> Dict[str, Dict[str, float]]:
        """Aggregate outstanding open-order notional grouped by side/base."""
        exposure = {"buy": {}, "sell": {}}

        try:
            orders = self.exchange.list_open_orders()
        except CriticalDataUnavailable:
            raise
        except Exception as exc:
            raise CriticalDataUnavailable("open_orders", exc) from exc

        for order in orders:
            product = order.get("product_id") or ""
            if '-' not in product:
                continue

            side = (order.get("side") or "").upper()
            if side not in {"BUY", "SELL"}:
                continue

            config = order.get("order_configuration") or {}
            notional = 0.0

            limit_conf = config.get("limit_limit_gtc")
            market_conf = config.get("market_market_ioc")

            if limit_conf:
                base_units = float(limit_conf.get("base_size") or 0.0)
                px_raw = limit_conf.get("limit_price")
                price = float(px_raw) if px_raw else None
                if price is None and base_units > 0:
                    try:
                        quote = self.exchange.get_quote(product)
                        price = quote.mid
                    except Exception:
                        price = 0.0
                notional = base_units * price if price else 0.0
            elif market_conf:
                if side == "BUY":
                    notional = float(market_conf.get("quote_size") or 0.0)
                else:
                    base_units = float(market_conf.get("base_size") or 0.0)
                    if base_units > 0:
                        try:
                            quote = self.exchange.get_quote(product)
                            notional = base_units * quote.mid
                        except Exception:
                            notional = 0.0

            if notional <= 0:
                continue

            symbol_key = product
            bucket = exposure["buy"] if side == "BUY" else exposure["sell"]
            bucket[symbol_key] = bucket.get(symbol_key, 0.0) + notional

        # Keep state store in sync even if we are only computing exposure
        try:
            self.executor.sync_open_orders_snapshot(orders)
        except Exception as exc:
            logger.debug("Open order sync skipped: %s", exc)

        return exposure

    def _auto_rebalance_for_trade(self, proposals: List[TradeProposal], deficit_usd: float) -> bool:
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
            accounts = self._require_accounts("auto_rebalance")
            from_account = next((a for a in accounts if a['currency'] == worst['currency']), None)
            to_account = next((a for a in accounts if a['currency'] == 'USDC'), None)
            
            if not from_account or not to_account:
                logger.error(f"Cannot find account UUIDs for {worst['currency']} or USDC")
                return False
            
            price = max(worst.get('price', 0.0), 1e-8)
            usd_needed = max(deficit_usd, self.executor.min_notional_usd)
            units_needed = min(worst['balance'], (usd_needed * 1.05) / price)
            if units_needed * price < self.executor.min_notional_usd:
                units_needed = min(worst['balance'], self.executor.min_notional_usd / price)

            logger.info(
                "Converting %.6f %s (~$%.2f) → USDC to raise capital...",
                units_needed,
                worst['currency'],
                units_needed * price,
            )

            result = self.executor.convert_asset(
                from_currency=worst['currency'],
                to_currency='USDC',
                amount=str(units_needed),
                from_account_uuid=from_account['uuid'],
                to_account_uuid=to_account['uuid']
            )

            if isinstance(result, dict) and result.get('success'):
                amount_converted = units_needed * price
                logger.info(
                    f"✅ Liquidated {worst['currency']}: freed up ~${amount_converted:.2f} USDC"
                )

                self.portfolio = self._init_portfolio_state()
                return True
            else:
                error_msg = result.get('error', 'Unknown error') if isinstance(result, dict) else str(result)
                logger.warning(f"❌ Liquidation failed: {error_msg}")

                logger.info(f"Attempting to sell {worst['currency']} via market order...")
                return self._sell_via_market_order(
                    worst['currency'],
                    worst['balance'],
                    usd_target=usd_needed * 1.05
                )
                
        except CriticalDataUnavailable:
            raise
        except Exception as e:
            logger.error(f"Auto-rebalance failed: {e}")
            return False
    
    def _sell_via_market_order(self, currency: str, balance: float, usd_target: Optional[float] = None) -> bool:
        """
        Fallback: Sell asset via market order (e.g., XRP-USD) instead of Convert API.
        
        Args:
            currency: Asset to sell (e.g., "HBAR")
            balance: Amount of asset to sell (in units, not USD)
            usd_target: Optional USD amount to raise
            
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
                if usd_target:
                    size_usd = min(size_usd, usd_target)
            except Exception as e:
                logger.error(f"Failed to get quote for {pair}: {e}")
                return False
            
            logger.info(f"Estimated value: ${size_usd:.2f} USD")
            
            result = self.executor.execute(
                symbol=pair,
                side="SELL",
                size_usd=size_usd,
                # Purge must bypass post-only default
                force_order_type="market",
                # Skip depth/spread checks for excluded/ineligible liquidation
                skip_liquidity_checks=True
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
            
            # Add ±10% jitter to avoid synchronized bursts
            import random
            jitter = random.uniform(-0.1, 0.1) * interval_seconds
            base_sleep = interval_seconds - elapsed
            sleep_for = max(1.0, base_sleep + jitter)
            
            # Auto-backoff if cycle utilization > 70%
            utilization = elapsed / interval_seconds
            if utilization > 0.7:
                backoff = 15.0
                sleep_for += backoff
                logger.warning(f"High cycle utilization ({utilization:.1%}), adding {backoff}s backoff")
            
            logger.info(f"Cycle took {elapsed:.2f}s, sleeping {sleep_for:.2f}s (util: {utilization:.1%})")
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
