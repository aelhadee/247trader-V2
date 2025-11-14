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
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime, timedelta, timezone
from pathlib import Path
import logging
from uuid import uuid4

from core.exchange_coinbase import CoinbaseExchange
from core.exceptions import CriticalDataUnavailable
from core.universe import UniverseManager
from core.triggers import TriggerEngine
from strategy.rules_engine import RulesEngine, TradeProposal
from core.risk import RiskEngine, PortfolioState
from core.execution import ExecutionEngine, ExecutionResult
from core.position_manager import PositionManager
from infra.alerting import AlertService, AlertSeverity
from infra.state_store import StateStore, StateStoreSupervisor, create_state_store_from_config
from infra.metrics import MetricsRecorder, CycleStats
from core.audit_log import AuditLogger
from core.order_state import OrderStatus

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
            for idx, error in enumerate(validation_errors, start=1):
                lines = str(error).splitlines()
                if not lines:
                    continue
                logger.error(f"{idx:>2}. {lines[0]}")
                for extra in lines[1:]:
                    logger.error(f"    {extra}")
            logger.error("=" * 80)
            raise ValueError(f"Invalid configuration: {len(validation_errors)} error(s) found")
        
        # Load configs
        self.app_config = self._load_yaml("app.yaml")
        self.policy_config = self._load_yaml("policy.yaml")
        self.universe_config = self._load_yaml("universe.yaml")

        loop_policy_cfg = (self.policy_config.get("loop") or {})
        loop_app_cfg = (self.app_config.get("loop") or {})
        self.loop_policy_config = loop_policy_cfg
        self.loop_app_config = loop_app_cfg

        loop_cache_ttl = loop_policy_cfg.get("universe_cache_seconds") or loop_app_cfg.get("universe_cache_seconds")
        self._universe_cache_ttl = float(loop_cache_ttl) if loop_cache_ttl is not None else None

        interval_seconds = loop_policy_cfg.get("interval_seconds") or loop_app_cfg.get("interval_seconds")
        if interval_seconds is None:
            interval_cfg = loop_app_cfg.get("interval") if isinstance(loop_app_cfg.get("interval"), dict) else {}
            interval_seconds = interval_cfg.get("seconds")
        if interval_seconds is None:
            minutes_value = loop_app_cfg.get("interval_minutes")
            if minutes_value is not None:
                try:
                    interval_seconds = float(minutes_value) * 60.0
                except (TypeError, ValueError):
                    interval_seconds = None

        self.loop_interval_seconds = float(interval_seconds) if interval_seconds else None
        
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
        state_cfg = self.app_config.get("state") or {}
        self.state_store = create_state_store_from_config(state_cfg)
        persist_interval = state_cfg.get("persist_interval_seconds")
        if persist_interval is not None:
            try:
                persist_interval = float(persist_interval)
            except (TypeError, ValueError):
                logger.warning("Invalid persist_interval_seconds=%s; disabling periodic flush", persist_interval)
                persist_interval = None
        backup_cfg = {
            "enabled": state_cfg.get("backup_enabled", False),
            "interval_hours": state_cfg.get("backup_interval_hours"),
            "interval_seconds": state_cfg.get("backup_interval_seconds"),
            "path": state_cfg.get("backup_path"),
            "max_files": state_cfg.get("backup_max_files", 10),
        }
        self.state_store_supervisor = StateStoreSupervisor(
            self.state_store,
            persist_interval_seconds=persist_interval,
            backup_config=backup_cfg,
        )
        self.state_store_supervisor.start()
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

        metrics_enabled = monitoring_cfg.get("metrics_enabled", False)
        metrics_port = int(monitoring_cfg.get("metrics_port", 9090))
        self.metrics = MetricsRecorder(enabled=metrics_enabled, port=metrics_port)
        self.metrics.start()
        
        self.universe_mgr = UniverseManager(
            self.config_dir / "universe.yaml",
            cache_ttl_seconds=self._universe_cache_ttl,
        )
        self.trigger_engine = TriggerEngine()
        self.rules_engine = RulesEngine(config={})
        self.risk_engine = RiskEngine(
            self.policy_config, 
            universe_manager=self.universe_mgr,
            exchange=self.exchange,
            state_store=self.state_store,
            alert_service=self.alerts  # CRITICAL: Wire alerts for safety notifications
        )
        self.executor = ExecutionEngine(
            mode=self.mode,
            exchange=self.exchange,
            policy=self.policy_config,
            state_store=self.state_store,
        )
        
        self.position_manager = PositionManager(
            policy=self.policy_config,
            state_store=self.state_store,
        )

        try:
            self.executor.reconcile_open_orders()
        except Exception as exc:
            logger.debug("Initial open-order reconciliation skipped: %s", exc)
        
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

        self._stop_state_store_supervisor()
        
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

    def _stop_state_store_supervisor(self) -> None:
        supervisor = getattr(self, "state_store_supervisor", None)
        if not supervisor:
            return
        try:
            supervisor.stop()
        except Exception as exc:
            logger.warning("State store supervisor stop failed: %s", exc)
    
    def _load_yaml(self, filename: str) -> dict:
        """Load YAML config file"""
        path = self.config_dir / filename
        with open(path) as f:
            return yaml.safe_load(f)
    
    def _apply_bounded_auto_loosen(self) -> None:
        """
        Apply bounded auto-loosening to regime thresholds after prolonged zero-trigger period.
        
        Uses auto_tune configuration from app.yaml:
        - Reads loosen deltas and floors
        - Applies adjustments to signals.yaml regime_thresholds (chop regime)
        - Respects hard floors to prevent runaway loosening
        - One-shot adjustment (flag prevents repeated loosening)
        
        IMPORTANT: This modifies signals.yaml on disk. Bot should detect file changes
        and reload configuration (or require restart).
        """
        signals_path = self.config_dir / "signals.yaml"
        
        try:
            # Load auto_tune configuration
            auto_tune_cfg = self.app_config.get("auto_tune", {})
            loosen_cfg = auto_tune_cfg.get("loosen", {})
            floors_cfg = auto_tune_cfg.get("floors", {})
            
            pct_15m_delta = loosen_cfg.get("pct_change_15m_delta", -0.2)
            pct_60m_delta = loosen_cfg.get("pct_change_60m_delta", -0.3)
            conviction_delta = loosen_cfg.get("min_conviction_delta", -0.02)
            
            floor_15m = floors_cfg.get("pct_change_15m", 1.2)
            floor_60m = floors_cfg.get("pct_change_60m", 2.5)
            floor_conviction = floors_cfg.get("min_conviction", 0.30)
            
            # Load current signals config
            with open(signals_path, 'r') as f:
                signals = yaml.safe_load(f)
            
            # Adjust chop regime thresholds
            regime_thresholds = signals.get("triggers", {}).get("regime_thresholds", {})
            chop = regime_thresholds.get("chop", {})
            
            current_15m = chop.get("pct_change_15m", 2.0)
            current_60m = chop.get("pct_change_60m", 4.0)
            
            new_15m = max(current_15m + pct_15m_delta, floor_15m)
            new_60m = max(current_60m + pct_60m_delta, floor_60m)
            
            chop["pct_change_15m"] = new_15m
            chop["pct_change_60m"] = new_60m
            
            logger.warning(f"Auto-loosened chop thresholds: 15m {current_15m:.1f}% → {new_15m:.1f}%, "
                          f"60m {current_60m:.1f}% → {new_60m:.1f}%")
            
            # Also adjust min_conviction in policy.yaml
            policy_path = self.config_dir / "policy.yaml"
            with open(policy_path, 'r') as f:
                policy = yaml.safe_load(f)
            
            strategy = policy.get("strategy", {})
            current_conviction = strategy.get("min_conviction_to_propose", 0.34)
            new_conviction = max(current_conviction + conviction_delta, floor_conviction)
            strategy["min_conviction_to_propose"] = new_conviction
            
            logger.warning(f"Auto-loosened min_conviction: {current_conviction:.2f} → {new_conviction:.2f}")
            
            # Write updated configs back to disk
            with open(signals_path, 'w') as f:
                yaml.dump(signals, f, default_flow_style=False, sort_keys=False)
            
            with open(policy_path, 'w') as f:
                yaml.dump(policy, f, default_flow_style=False, sort_keys=False)
            
            logger.warning("=" * 80)
            logger.warning("BOUNDED AUTO-TUNE APPLIED - RESTART BOT TO APPLY CHANGES")
            logger.warning("Modified: config/signals.yaml (chop thresholds)")
            logger.warning("Modified: config/policy.yaml (min_conviction)")
            logger.warning(f"Floors enforced: 15m>={floor_15m}%, 60m>={floor_60m}%, conviction>={floor_conviction}")
            logger.warning("=" * 80)
            
            # Send alert if enabled
            if self.alerts.is_enabled():
                self.alerts.send(
                    severity="warning",
                    summary="Bounded auto-tune triggered",
                    details=f"Zero triggers for {auto_tune_cfg.get('zero_trigger_cycles', 12)} cycles. "
                            f"Loosened chop thresholds and min_conviction within bounded floors. Restart bot to apply."
                )
            
        except Exception as e:
            logger.error(f"Failed to apply bounded auto-loosen: {e}")
    
    def _init_portfolio_state(self) -> PortfolioState:
        """Initialize portfolio state from state store"""
        state = self.state_store.load()

        # Default to persisted snapshot in case live lookups fail
        positions_source: Dict[str, Any] = state.get("positions", {})
        normalized_positions_override: Optional[Dict[str, Dict[str, Any]]] = None

        # Get real account value from Coinbase (fallback to 10k for DRY_RUN)
        if self.mode == "DRY_RUN":
            account_value_usd = 10_000.0
        else:
            try:
                accounts = self._require_accounts("portfolio_init")
            except CriticalDataUnavailable as data_exc:
                logger.warning(
                    "Account lookup failed (%s); using last stored cash balance",
                    data_exc.source,
                )
                stored_balances = state.get("cash_balances", {})
                account_value_usd = sum(float(v) for v in stored_balances.values())
                normalized_positions_override = self._normalize_positions_for_risk(positions_source)
                if normalized_positions_override:
                    account_value_usd += sum(
                        float(pos.get("usd", 0.0))
                        for pos in normalized_positions_override.values()
                    )
                if account_value_usd <= 0:
                    account_value_usd = 10_000.0
                    normalized_positions_override = None
            else:
                snapshot = self._build_account_snapshot(accounts)
                account_value_usd = snapshot["account_value_usd"]

                if account_value_usd > 0:
                    positions_source = snapshot["positions"]
                    logger.info("Real account value: $%.2f", account_value_usd)
                else:
                    logger.warning(
                        "Account snapshot returned zero NAV; falling back to stored balances"
                    )
                    positions_source = state.get("positions", {})
                    stored_balances = state.get("cash_balances", {})
                    account_value_usd = sum(float(v) for v in stored_balances.values())
                    normalized_positions_override = self._normalize_positions_for_risk(positions_source)
                    if normalized_positions_override:
                        account_value_usd += sum(
                            float(pos.get("usd", 0.0))
                            for pos in normalized_positions_override.values()
                        )
                    if account_value_usd <= 0:
                        account_value_usd = 10_000.0
                        normalized_positions_override = None

        positions = (
            normalized_positions_override
            if normalized_positions_override is not None
            else self._normalize_positions_for_risk(positions_source)
        )

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
            managed_positions=dict(state.get("managed_positions", {})),
        )

    def _build_account_snapshot(self, accounts: List[dict]) -> Dict[str, Any]:
        """Aggregate Coinbase account balances into USD-valued snapshot for risk."""

        # Treat USD and stablecoins as cash; everything else is priced as exposure
        cash_equivalents = set(
            self.policy_config.get("risk", {}).get(
                "cash_equivalents", ["USD", "USDC", "USDT"]
            )
        )

        price_cache: Dict[str, Optional[float]] = {"USD": 1.0, "USDC": 1.0, "USDT": 1.0}

        def _get_mid(currency: str) -> Optional[float]:
            if currency in price_cache:
                return price_cache[currency]

            for quote_currency in ("USD", "USDC", "USDT"):
                product_id = f"{currency}-{quote_currency}"
                try:
                    quote = self.exchange.get_quote(product_id)
                    mid = float(getattr(quote, "mid", 0.0))
                except Exception:
                    mid = 0.0

                if mid > 0:
                    # If we priced against a stablecoin, convert stablecoin → USD (assume 1:1)
                    if quote_currency != "USD":
                        mid *= price_cache.get(quote_currency, 1.0)

                    price_cache[currency] = mid
                    return mid

            price_cache[currency] = None
            logger.debug("Unable to price %s against USD/USDC/USDT", currency)
            return None

        snapshot: Dict[str, Any] = {
            "account_value_usd": 0.0,
            "positions": {},
            "cash_balances": {},
        }

        for account in accounts or []:
            currency = account.get("currency")
            if not currency:
                continue

            available = float(account.get("available_balance", {}).get("value", 0.0) or 0.0)
            hold = float(account.get("hold", {}).get("value", 0.0) or 0.0)
            total_units = available + hold

            if total_units <= 0:
                continue

            if currency in cash_equivalents:
                snapshot["cash_balances"][currency] = (
                    snapshot["cash_balances"].get(currency, 0.0) + total_units
                )
                snapshot["account_value_usd"] += total_units
                continue

            mid_price = _get_mid(currency)
            if mid_price is None or mid_price <= 0:
                continue

            usd_value = total_units * mid_price
            snapshot["account_value_usd"] += usd_value

            entry = snapshot["positions"].setdefault(
                currency,
                {
                    "currency": currency,
                    "available": 0.0,
                    "hold": 0.0,
                    "total": 0.0,
                    "usd_value": 0.0,
                },
            )

            entry["available"] += available
            entry["hold"] += hold
            entry["total"] += total_units
            entry["usd_value"] += usd_value

        logger.debug(
            "Account snapshot aggregated NAV=$%.2f (%d positions, %d cash currencies)",
            snapshot["account_value_usd"],
            len(snapshot["positions"]),
            len(snapshot["cash_balances"]),
        )

        return snapshot
    
    def _build_pending_orders_from_state(self, state: Dict[str, Any]) -> Dict[str, Dict[str, float]]:
        """
        Build pending_orders dict from persisted open_orders for risk accounting.
        
        Returns: {"buy": {"BTC-USD": notional_usd, ...}, "sell": {"ETH-USD": notional_usd, ...}}
        
        CRITICAL: This ensures open orders count toward exposure caps in RiskEngine.
        Without this, we can over-allocate while orders are working.
        """
        pending = {"buy": {}, "sell": {}}

        try:
            self.state_store.purge_expired_pending()
        except Exception:
            pass
        
        open_orders = state.get("open_orders", {})
        if not open_orders:
            # Even without tracked open orders, pending markers may exist
            markers = state.get("pending_markers", {})
            for record in markers.values():
                if not isinstance(record, dict):
                    continue
                if (record.get("side") or "").upper() != "BUY":
                    continue
                product_id = record.get("product_id") or record.get("base")
                if not product_id:
                    continue
                if "-" not in product_id:
                    product_id = f"{product_id}-USD"
                notional = record.get("notional_usd")
                if notional is None or notional <= 0:
                    notional = self.executor.min_notional_usd
                pending["buy"][product_id] = max(pending["buy"].get(product_id, 0.0), float(notional))
            return pending
        
        for order_id, order_data in open_orders.items():
            if not isinstance(order_data, dict):
                continue
            
            # Extract order details
            side = order_data.get("side", "").lower()  # "buy" or "sell"
            symbol = order_data.get("symbol") or order_data.get("product_id") or ""
            if symbol and '-' not in symbol:
                symbol = f"{symbol}-USD"
            
            # Calculate notional value
            # For BUY: use order_value or (size * price)
            # For SELL: we don't count sell orders toward buy exposure
            if side == "buy":
                notional_usd = float(order_data.get("order_value_usd", 0.0) or 0.0)

                # Fallbacks: quote_size_usd > size*price
                if notional_usd == 0.0:
                    notional_usd = float(order_data.get("quote_size_usd", 0.0) or 0.0)

                if notional_usd == 0.0:
                    size = float(order_data.get("size", 0.0) or 0.0)
                    price = float(order_data.get("price", 0.0) or 0.0)
                    notional_usd = size * price
                
                if symbol and notional_usd > 0:
                    pending["buy"][symbol] = pending["buy"].get(symbol, 0.0) + notional_usd
            
            elif side == "sell":
                # Track sell orders separately (not counted in buy exposure)
                notional_usd = float(order_data.get("order_value_usd", 0.0) or 0.0)
                if notional_usd == 0.0:
                    notional_usd = float(order_data.get("quote_size_usd", 0.0) or 0.0)
                if notional_usd == 0.0:
                    size = float(order_data.get("size", 0.0) or 0.0)
                    price = float(order_data.get("price", 0.0) or 0.0)
                    notional_usd = size * price
                
                if symbol and notional_usd > 0:
                    pending["sell"][symbol] = pending["sell"].get(symbol, 0.0) + notional_usd

        # Merge lightweight pending markers to avoid losing exposure when cancels occur mid-cycle
        markers = state.get("pending_markers", {})
        for record in markers.values():
            if not isinstance(record, dict):
                continue
            side = (record.get("side") or "").upper()
            if side != "BUY":
                continue
            product_id = record.get("product_id") or record.get("base")
            if not product_id:
                continue
            if "-" not in product_id:
                product_id = f"{product_id}-USD"
            notional = record.get("notional_usd")
            if notional is None or notional <= 0:
                notional = self.executor.min_notional_usd
            pending["buy"][product_id] = max(pending["buy"].get(product_id, 0.0), float(notional))
        
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

            units = entry.get("base_qty")
            if units is None:
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
        self._record_cycle_metrics(
            status=reason,
            proposals=0,
            approved=0,
            executed=0,
            started_at=cycle_started,
        )

    def _require_accounts(self, context: str) -> List[dict]:
        try:
            return self.exchange.get_accounts()
        except CriticalDataUnavailable:
            raise
        except Exception as exc:
            raise CriticalDataUnavailable(f"accounts:{context}", exc) from exc
    
    def _count_open_positions(self) -> int:
        """Return number of meaningful open positions (ignores dust)."""
        if not isinstance(self.portfolio.open_positions, dict):
            return 0

        threshold = max(self.executor.min_notional_usd, 5.0)
        count = 0
        for data in self.portfolio.open_positions.values():
            if not isinstance(data, dict):
                continue
            raw_value = data.get("usd") if "usd" in data else data.get("usd_value")
            try:
                value = float(raw_value)
            except (TypeError, ValueError):
                continue
            if value >= threshold:
                count += 1
        return count

    def _trim_worst_position_for_capacity(self) -> bool:
        """Force-sell the worst performer to free a position slot."""
        pm_cfg = self.policy_config.get("portfolio_management", {}) or {}
        min_value = float(pm_cfg.get("min_liquidation_value_usd", self.executor.min_notional_usd) or self.executor.min_notional_usd)

        candidates = self.executor.get_liquidation_candidates(
            min_value_usd=min_value,
            sort_by="performance",
        )
        if not candidates:
            logger.warning("Capacity check: no liquidation candidates available to free slot")
            return False

        candidate = candidates[0]
        balance = float(candidate.get("balance", 0.0) or 0.0)
        price = float(candidate.get("price", 0.0) or 0.0)
        if balance <= 0 or price <= 0:
            logger.warning("Capacity check: candidate %s has invalid balance/price", candidate.get("currency"))
            return False

        usd_target = float(candidate.get("value_usd") or (balance * price))
        tier = self._infer_tier_from_config(candidate.get("pair")) or 3

        logger.info(
            "Max-open trim: liquidating %s (~$%.2f) to free a slot (balance=%.6f)",
            candidate.get("currency"),
            usd_target,
            balance,
        )

        success = self._sell_via_market_order(
            candidate.get("currency"),
            balance,
            usd_target=usd_target,
            tier=tier,
            preferred_pair=candidate.get("pair"),
        )

        if success:
            logger.info(
                "Capacity trim complete: freed slot by selling %s (~$%.2f)",
                candidate.get("currency"),
                usd_target,
            )
        return success

    def _ensure_capacity_for_new_positions(self) -> Optional[str]:
        """Ensure we have room for new positions; trim if max_open is saturated."""
        strategy_cfg = self.policy_config.get("strategy", {}) or {}
        max_open = int(strategy_cfg.get("max_open_positions", 8) or 8)
        current_open = self._count_open_positions()

        if current_open < max_open:
            return None

        logger.warning(
            "Max open positions reached (%d/%d). Attempting to free a slot before proposing new trades.",
            current_open,
            max_open,
        )

        trimmed = self._trim_worst_position_for_capacity()
        if trimmed:
            return "max_open_positions_trimmed"
        return "max_open_positions_saturated"

    def run_cycle(self):
        """
        Execute one trading cycle with full safety guarantees.
        
        Any exception -> NO_TRADE, audit, continue
        """
        cycle_started = datetime.now(timezone.utc)
        proposals_count = 0
        approved_count = 0
        executed_count = 0
        logger.info("=" * 80)
        logger.info(f"CYCLE START: {cycle_started.isoformat()}")
        logger.info("=" * 80)
        
        try:
            try:
                self.state_store.purge_expired_pending()
            except Exception as exc:
                logger.debug("Pending purge skipped: %s", exc)

            try:
                self._reconcile_exchange_state()
            except CriticalDataUnavailable as data_exc:
                self._abort_cycle_due_to_data(
                    cycle_started,
                    data_exc.source,
                    str(data_exc.original) if data_exc.original else None,
                )
                return

            # Reconcile exchange state and purge expired pending markers BEFORE building portfolio
            # This ensures pending_markers are cleared before being read into PortfolioState
            try:
                self.executor.reconcile_open_orders()
                self.state_store.purge_expired_pending()
            except Exception as exc:
                logger.debug("Cycle reconciliation skipped: %s", exc)

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

            try:
                trimmed = self._auto_trim_to_risk_cap()
            except CriticalDataUnavailable as data_exc:
                self._abort_cycle_due_to_data(
                    cycle_started,
                    data_exc.source,
                    str(data_exc.original) if data_exc.original else None,
                )
                return

            if trimmed:
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

            try:
                capacity_reason = self._ensure_capacity_for_new_positions()
            except CriticalDataUnavailable as data_exc:
                self._abort_cycle_due_to_data(
                    cycle_started,
                    data_exc.source,
                    str(data_exc.original) if data_exc.original else None,
                )
                return

            if capacity_reason:
                if capacity_reason == "max_open_positions_trimmed":
                    logger.info(
                        "Capacity freed this cycle; skipping new proposals to allow state to settle."
                    )
                else:
                    logger.warning(
                        "Max open positions remain saturated; skipping proposal generation this cycle."
                    )

                self.audit.log_cycle(
                    ts=cycle_started,
                    mode=self.mode,
                    universe=None,
                    triggers=None,
                    base_proposals=[],
                    risk_approved=[],
                    final_orders=[],
                    no_trade_reason=capacity_reason,
                    state_store=self.state_store,
                )
                self._record_cycle_metrics(
                    status=capacity_reason,
                    proposals=proposals_count,
                    approved=approved_count,
                    executed=executed_count,
                    started_at=cycle_started,
                )
                return

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
                self._record_cycle_metrics(
                    status=reason,
                    proposals=proposals_count,
                    approved=approved_count,
                    executed=executed_count,
                    started_at=cycle_started,
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
                logger.info(f"NO_TRADE: {reason} (0 triggers)")
                
                # Zero-trigger sentinel: Track consecutive cycles with 0 triggers
                zero_trigger_count = self.state_store.get("zero_trigger_cycles", 0) + 1
                state = self.state_store.load()
                state["zero_trigger_cycles"] = zero_trigger_count
                self.state_store.save(state)
                
                # Auto-loosen if stuck at 0 triggers for configured cycles (bounded)
                auto_tune_cfg = self.app_config.get("auto_tune", {})
                trigger_threshold = auto_tune_cfg.get("zero_trigger_cycles", 12)
                
                if zero_trigger_count >= trigger_threshold and not state.get("auto_tune_applied", False):
                    logger.warning(f"Zero-trigger sentinel triggered after {zero_trigger_count} cycles - applying bounded auto-loosen")
                    self._apply_bounded_auto_loosen()
                    state["auto_tune_applied"] = True
                    self.state_store.save(state)
                
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
                self._record_cycle_metrics(
                    status=reason,
                    proposals=proposals_count,
                    approved=approved_count,
                    executed=executed_count,
                    started_at=cycle_started,
                )
                return
            
            # Triggers detected - reset zero-trigger counter
            state = self.state_store.load()
            if state.get("zero_trigger_cycles", 0) > 0:
                state["zero_trigger_cycles"] = 0
                state["auto_tune_applied"] = False  # Reset flag when triggers resume
                self.state_store.save(state)
            
            logger.info(f"Triggers: {len(triggers)} detected")
            
            # Step 3: Generate trade proposals
            logger.info("Step 3: Generating trade proposals...")
            proposals = self.rules_engine.propose_trades(
                universe=universe,
                triggers=triggers,
                regime=self.current_regime
            )
            proposals_count = len(proposals or [])
            
            if not proposals:
                reason = "rules_engine_no_proposals"
                logger.info(f"NO_TRADE: {reason}")
                
                # Note: Zero-trigger sentinel already handled earlier in cycle
                # No need for separate zero-proposal tracking
                
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
                self._record_cycle_metrics(
                    status=reason,
                    proposals=proposals_count,
                    approved=approved_count,
                    executed=executed_count,
                    started_at=cycle_started,
                )
                return
            
            # Avoid stacking buys while there are outstanding orders for the same base asset
            # Use pending_buy_notional from portfolio snapshot (aggregated from open_orders)
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
                # Note: removed "fast guard" short-circuit - let RiskEngine see true state
                # and make the decision based on actual live orders after reconciliation
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
                self._record_cycle_metrics(
                    status=reason,
                    proposals=proposals_count,
                    approved=approved_count,
                    executed=executed_count,
                    started_at=cycle_started,
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
                    if getattr(risk_result, "proposal_rejections", None):
                        logger.warning(
                            "Risk check FAILED: %s | rejections=%s",
                            reason,
                            risk_result.proposal_rejections,
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
                    proposal_rejections=risk_result.proposal_rejections,
                )
                self._record_cycle_metrics(
                    status=reason,
                    proposals=proposals_count,
                    approved=approved_count,
                    executed=executed_count,
                    started_at=cycle_started,
                )
                return
            
            # Use the filtered approved proposals from risk engine
            approved_proposals = risk_result.approved_proposals
            approved_count = len(approved_proposals or [])
            logger.info(f"Risk checks PASSED: {len(approved_proposals)}/{len(proposals)} proposals approved")
            if getattr(risk_result, "proposal_rejections", None):
                logger.info(
                    "Risk filtered proposals: %s",
                    risk_result.proposal_rejections,
                )
            
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
                # Check governance flag (dead man's switch for LIVE trading)
                governance_config = self.policy_config.get("governance", {})
                live_trading_enabled = governance_config.get("live_trading_enabled", True)
                
                if self.mode == "LIVE" and not live_trading_enabled:
                    logger.error(
                        "🚨 LIVE TRADING DISABLED via governance.live_trading_enabled=false in policy.yaml"
                    )
                    self._log_no_trade(
                        cycle_started,
                        "governance_live_trading_disabled",
                        "LIVE trading is disabled by governance flag in policy.yaml",
                    )
                    self._record_cycle_metrics(
                        status="governance_live_trading_disabled",
                        proposals=proposals_count,
                        approved=approved_count,
                        executed=executed_count,
                        started_at=cycle_started,
                    )
                    return
                
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
                        if result.filled_size and result.filled_size > 0:
                            logger.info(
                                "✅ Order filled: %s %.6f @ $%.2f (route=%s, order_id=%s)",
                                proposal.symbol,
                                result.filled_size,
                                result.filled_price,
                                result.route,
                                result.order_id,
                            )
                        else:
                            logger.info(
                                "🕒 Order accepted: %s %s (route=%s, order_id=%s)",
                                proposal.side,
                                proposal.symbol,
                                result.route,
                                result.order_id,
                            )
                        final_orders.append(result)
                    else:
                        logger.warning(f"⚠️ Trade failed: {proposal.symbol} - {result.error}")
            
            # Update state after fills
            executed_count = len(final_orders)

            if final_orders:
                self.state_store.update_from_fills(final_orders, self.portfolio)
                
                # Update managed position targets from proposals (for BUY orders only)
                for order, (proposal, _) in zip(final_orders, adjusted_proposals):
                    if order.success and proposal.side.upper() == "BUY":
                        symbol = proposal.symbol.replace("-USD", "")
                        self.state_store.update_managed_position_targets(
                            symbol=symbol,
                            stop_loss_pct=proposal.stop_loss_pct,
                            take_profit_pct=proposal.take_profit_pct,
                            max_hold_hours=proposal.max_hold_hours,
                        )
                
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
            self._record_cycle_metrics(
                status="executed" if final_orders else "no_orders_after_execution_filter",
                proposals=proposals_count,
                approved=approved_count,
                executed=executed_count,
                started_at=cycle_started,
            )

            # Post-cycle maintenance: cancel stale open orders (LIVE/PAPER)
            try:
                if self.mode in ("LIVE", "PAPER"):
                    self.executor.manage_open_orders()
            except Exception as e:
                logger.warning(f"Open order maintenance skipped: {e}")
            
            # Position exit management: check for stop-loss/take-profit (LIVE/PAPER/DRY_RUN)
            try:
                exit_proposals = self._check_position_exits()
                if exit_proposals:
                    logger.info(f"Position exit check generated {len(exit_proposals)} SELL proposal(s)")
                    # Execute exit proposals immediately (bypass normal approval flow for exits)
                    self._execute_exit_proposals(exit_proposals)
            except Exception as exit_exc:
                logger.warning(f"Position exit check failed: {exit_exc}", exc_info=True)
            
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
            self._record_cycle_metrics(
                status=f"exception:{type(e).__name__}",
                proposals=proposals_count,
                approved=approved_count,
                executed=executed_count,
                started_at=cycle_started,
            )

    def _record_cycle_metrics(
        self,
        *,
        status: str,
        proposals: int,
        approved: int,
        executed: int,
        started_at: datetime,
    ) -> None:
        metrics = getattr(self, "metrics", None)
        if metrics is None or not metrics.is_enabled():
            return

        duration = max((datetime.now(timezone.utc) - started_at).total_seconds(), 0.0)
        stats = CycleStats(
            status=status,
            proposals=proposals,
            approved=approved,
            executed=executed,
            duration_seconds=duration,
        )
        metrics.observe_cycle(stats)

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

            asset = None
            if hasattr(universe, "get_asset"):
                try:
                    asset = universe.get_asset(symbol)
                except Exception:
                    asset = None

            tier = asset.tier if asset else self._infer_tier_from_config(symbol)
            tier = tier or 3

            tag = "excluded" if is_excluded else "ineligible"
            logger.info(f"Purge: selling {balance:.6f} {currency} ({tag}), ~${value_usd:.2f}")

            if self._sell_via_market_order(
                currency,
                balance,
                usd_target=value_usd,
                tier=tier,
                preferred_pair=symbol,
            ):
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
            # Filter out ghost orders that were recently canceled (API eventual consistency)
            order_id = order.get("order_id")
            client_id = order.get("client_order_id")
            if self.executor.is_recently_canceled(order_id=order_id, client_order_id=client_id):
                logger.info(
                    "GHOST_ORDER_FILTERED: %s %s order %s still in API list after cancel",
                    order.get("product_id"),
                    order.get("side"),
                    order_id or client_id,
                )
                continue

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

    def _auto_trim_to_risk_cap(self) -> bool:
        """Liquidate enough exposure to fall back under the global risk cap."""

        pm_cfg = self.policy_config.get("portfolio_management", {})
        if not pm_cfg.get("auto_trim_to_risk_cap", False):
            return False

        if self.mode == "DRY_RUN":
            logger.debug("Auto trim skipped: DRY_RUN mode")
            return False

        risk_cfg = self.policy_config.get("risk", {})
        max_total_at_risk = float(risk_cfg.get("max_total_at_risk_pct", 0.0) or 0.0)
        if max_total_at_risk <= 0:
            logger.debug("Auto trim skipped: invalid max_total_at_risk_pct")
            return False

        nav = float(self.portfolio.account_value_usd or 0.0)
        if nav <= 0:
            logger.debug("Auto trim skipped: no account value data")
            return False

        exposure_usd = self.portfolio.get_total_exposure_usd()
        exposure_usd += self.portfolio.get_pending_notional_usd("buy")
        exposure_pct = (exposure_usd / nav) * 100 if nav else 0.0

        tolerance_pct = float(pm_cfg.get("trim_tolerance_pct", 0.25) or 0.0)
        if exposure_pct <= max_total_at_risk + tolerance_pct:
            return False

        buffer_pct = float(pm_cfg.get("trim_target_buffer_pct", 1.0) or 0.0)
        target_pct = max(0.0, max_total_at_risk - buffer_pct)
        tolerance_usd = max((tolerance_pct / 100.0) * nav, 0.0)

        excess_pct = max(0.0, exposure_pct - target_pct)
        excess_usd = (excess_pct / 100.0) * nav

        if excess_usd <= max(tolerance_usd, self.executor.min_notional_usd):
            return False

        logger.warning(
            "Global exposure %.1f%% exceeds cap %.1f%%. Trimming portfolio toward %.1f%% target (excess $%.2f)",
            exposure_pct,
            max_total_at_risk,
            target_pct,
            excess_usd,
        )

        min_liq_value = float(pm_cfg.get("trim_min_value_usd", self.executor.min_notional_usd) or self.executor.min_notional_usd)
        max_liqs = int(pm_cfg.get("trim_max_liquidations", 5) or 5)
        slippage_buffer_pct = max(0.0, float(pm_cfg.get("trim_slippage_buffer_pct", 5.0) or 0.0) / 100.0)
        preferred_quotes = pm_cfg.get("trim_preferred_quotes", ["USDC", "USD", "USDT"]) or []

        candidates = self.executor.get_liquidation_candidates(
            min_value_usd=min_liq_value,
            sort_by="performance",
        )

        if not candidates:
            logger.warning("Auto trim skipped: no liquidation candidates available")
            return False

        accounts = self._require_accounts("auto_trim")
        preferred_target = next(
            (acc for acc in accounts if acc.get("currency") in preferred_quotes),
            None,
        )

        target_currency = preferred_target.get("currency") if preferred_target else None
        target_account_uuid = preferred_target.get("uuid") if preferred_target else None

        remaining_excess_usd = excess_usd
        trimmed_any = False

        for candidate in candidates:
            if remaining_excess_usd <= tolerance_usd:
                break
            if max_liqs <= 0:
                logger.info("Auto trim stopping after reaching liquidation limit")
                break

            balance = float(candidate.get("balance", 0.0) or 0.0)
            price = float(candidate.get("price", 0.0) or 0.0)
            value_usd = float(candidate.get("value_usd", 0.0) or 0.0)
            account_uuid = candidate.get("account_uuid")

            if balance <= 0 or price <= 0 or value_usd <= 0 or not account_uuid:
                continue

            available_usd = min(value_usd, balance * price)
            if available_usd < self.executor.min_notional_usd:
                continue

            usd_to_free = min(remaining_excess_usd, available_usd)
            if usd_to_free < self.executor.min_notional_usd:
                continue

            units_to_liquidate = min(balance, (usd_to_free / price) * (1.0 + slippage_buffer_pct))
            if units_to_liquidate * price < self.executor.min_notional_usd:
                continue

            freed_usd = units_to_liquidate * price
            success = False

            currency = candidate.get("currency")

            can_attempt_convert = (
                target_currency
                and target_account_uuid
                and getattr(self.executor, "can_convert", lambda *_args: True)(
                    currency, target_currency
                )
            )

            if can_attempt_convert:
                convert_result = self.executor.convert_asset(
                    currency,
                    target_currency,
                    f"{units_to_liquidate:.8f}",
                    account_uuid,
                    target_account_uuid,
                )
                success = bool(convert_result and convert_result.get("success"))
            else:
                if target_currency and target_account_uuid:
                    logger.debug(
                        "Skipping convert %s→%s (convert not supported)",
                        currency,
                        target_currency,
                    )

            if not success:
                tier = self._infer_tier_from_config(candidate.get("pair")) or 3
                success = self._sell_via_market_order(
                    currency,
                    units_to_liquidate,
                    usd_target=min(freed_usd, remaining_excess_usd),
                    tier=tier,
                    preferred_pair=candidate.get("pair"),
                )

                if success:
                    logger.info(
                        "Auto trim sold %s via TWAP fallback (~$%.2f)",
                        currency,
                        min(freed_usd, remaining_excess_usd),
                    )
                else:
                    logger.warning("Auto trim failed to reduce %s position", currency)
                    continue
            else:
                logger.info(
                    "Auto trim converted %.6f %s → %s (~$%.2f)",
                    units_to_liquidate,
                    currency,
                    target_currency,
                    freed_usd,
                )

            trimmed_any = True
            remaining_excess_usd = max(0.0, remaining_excess_usd - freed_usd)
            max_liqs -= 1

        if not trimmed_any:
            return False

        try:
            self._reconcile_exchange_state()
        except CriticalDataUnavailable:
            raise
        except Exception as exc:
            logger.warning("Post-trim reconcile skipped: %s", exc)

        try:
            self.portfolio = self._init_portfolio_state()
        except CriticalDataUnavailable:
            raise
        except Exception as exc:
            logger.warning("Failed to refresh portfolio after trimming: %s", exc)

        logger.info("Auto trim complete. Remaining excess exposure $%.2f", remaining_excess_usd)
        return True

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

                logger.info(f"Attempting to liquidate {worst['currency']} via maker TWAP...")
                pair = f"{worst['currency']}-USD"
                tier = self._infer_tier_from_config(pair) or 3
                return self._sell_via_market_order(
                    worst['currency'],
                    worst['balance'],
                    usd_target=usd_needed * 1.05,
                    tier=tier,
                    preferred_pair=pair,
                )
                
        except CriticalDataUnavailable:
            raise
        except Exception as e:
            logger.error(f"Auto-rebalance failed: {e}")
            return False
    
    def _infer_tier_from_config(self, symbol: str) -> Optional[int]:
        """Infer asset tier from universe configuration for slippage budgets."""
        tiers_cfg = (self.universe_config or {}).get("tiers", {}) if hasattr(self, "universe_config") else {}
        tier_mapping = {
            "tier_1_core": 1,
            "tier_2_rotational": 2,
            "tier_3_event_driven": 3,
        }

        for tier_name, tier_value in tier_mapping.items():
            symbols = tiers_cfg.get(tier_name, {}).get("symbols", []) or []
            if symbol in symbols:
                return tier_value
        return None

    def _sell_via_market_order(
        self,
        currency: str,
        balance: float,
        usd_target: Optional[float] = None,
        tier: Optional[int] = None,
        preferred_pair: Optional[str] = None,
    ) -> bool:
        """
        Liquidate a position using maker-only TWAP (Time-Weighted Average Price) slices.

        This replaces the legacy market-order purge path with a safer post-only flow that
        respects slippage budgets, quote freshness, and depth checks. Orders are sliced
        into configurable USD notional chunks and refreshed on a fixed cadence.
        """
        if balance <= 0:
            logger.debug("TWAP purge skipped: zero balance for %s", currency)
            return True

        if self.mode != "LIVE":
            logger.info(
                "%s mode: would TWAP liquidate %.6f %s (~%s target) using maker slices",
                self.mode,
                balance,
                currency,
                f"${usd_target:.2f}" if usd_target else "full position",
            )
            return True

        pm_cfg = self.policy_config.get("portfolio_management", {})
        purge_cfg = pm_cfg.get("purge_execution", {})
        twap_cfg = self.policy_config.get("twap", {}) or {}

        slice_usd = float(purge_cfg.get("slice_usd", 15.0))
        replace_seconds = float(twap_cfg.get("replace_seconds", purge_cfg.get("replace_seconds", 20.0)))
        max_duration = float(purge_cfg.get("max_duration_seconds", 180.0))
        poll_interval = float(purge_cfg.get("poll_interval_seconds", 2.0))
        max_slices = int(purge_cfg.get("max_slices", 20))
        max_residual = float(purge_cfg.get("max_residual_usd", self.executor.min_notional_usd))
        max_consecutive_no_fill = int(
            twap_cfg.get("max_consecutive_no_fill", purge_cfg.get("max_consecutive_no_fill", 3))
        )

        slice_usd = max(slice_usd, self.executor.min_notional_usd)
        poll_interval = max(0.2, poll_interval)
        max_consecutive_no_fill = max(1, max_consecutive_no_fill)

        # Determine candidate trading pairs (USD preferred, USDC fallback)
        candidates = []
        for candidate in (preferred_pair, f"{currency}-USD", f"{currency}-USDC"):
            if candidate and candidate not in candidates:
                candidates.append(candidate)

        quote = None
        pair = None
        for candidate in candidates:
            try:
                quote = self.exchange.get_quote(candidate)
                pair = candidate
                break
            except CriticalDataUnavailable:
                raise
            except Exception as exc:
                logger.debug("TWAP: quote fetch failed for %s: %s", candidate, exc)

        if not quote or not pair:
            logger.error("TWAP: unable to determine liquidation pair for %s", currency)
            return False

        tier = tier or self._infer_tier_from_config(pair) or 3

        bid_price = max(quote.bid, 0.0)
        if bid_price <= 0:
            logger.warning("TWAP: invalid bid price %.8f for %s", bid_price, pair)
            return False

        gross_value = balance * bid_price
        target_value_usd = gross_value if usd_target is None else min(gross_value, usd_target)
        if target_value_usd <= 0:
            logger.info("TWAP: nothing to liquidate for %s (target=$%.2f)", currency, target_value_usd)
            return True

        logger.info(
            "TWAP purge start: %.6f %s (~$%.2f) via %s (tier T%d, slice=$%.2f, replace=%.1fs)",
            balance,
            currency,
            target_value_usd,
            pair,
            tier,
            slice_usd,
            replace_seconds,
        )

        start_time = time.monotonic()
        total_filled_usd = 0.0
        total_filled_units = 0.0
        total_fees = 0.0
        attempt = 0

        consecutive_no_fill = 0

        while total_filled_usd + 1e-6 < target_value_usd:
            if attempt >= max_slices:
                logger.warning("TWAP: reached max slices (%d) for %s", max_slices, pair)
                break
            if (time.monotonic() - start_time) >= max_duration:
                logger.warning("TWAP: exceeded max duration %.1fs for %s", max_duration, pair)
                break

            attempt += 1

            try:
                quote = self.exchange.get_quote(pair)
            except CriticalDataUnavailable:
                raise
            except Exception as exc:
                logger.warning("TWAP: failed to refresh quote for %s: %s", pair, exc)
                break

            ask_price = max(quote.ask, 0.0)
            if ask_price <= 0:
                logger.warning("TWAP: invalid ask price %.8f for %s", ask_price, pair)
                break

            remaining_usd = max(target_value_usd - total_filled_usd, 0.0)
            remaining_units = max(balance - total_filled_units, 0.0)
            max_affordable_usd = remaining_units * ask_price

            slice_notional = min(slice_usd, remaining_usd, max_affordable_usd)
            if slice_notional < self.executor.min_notional_usd:
                if total_filled_usd == 0:
                    logger.warning(
                        "TWAP: requested liquidation $%.2f below minimum notional $%.2f for %s",
                        slice_notional,
                        self.executor.min_notional_usd,
                        pair,
                    )
                    return False
                logger.info(
                    "TWAP: residual $%.2f below minimum notional $%.2f, stopping",
                    slice_notional,
                    self.executor.min_notional_usd,
                )
                break

            client_order_id = f"purge_{uuid4().hex[:18]}"

            try:
                result = self.executor.execute(
                    symbol=pair,
                    side="SELL",
                    size_usd=slice_notional,
                    client_order_id=client_order_id,
                    force_order_type="limit_post_only",
                    skip_liquidity_checks=False,
                    tier=tier,
                    bypass_slippage_budget=True,
                    bypass_failed_order_cooldown=True,
                )
            except CriticalDataUnavailable:
                raise
            except Exception as exc:
                logger.warning("TWAP: execution error on slice %d for %s: %s", attempt, pair, exc)
                break

            if not result.success:
                logger.warning(
                    "TWAP: slice %d for %s failed (%s)",
                    attempt,
                    pair,
                    result.error,
                )
                break

            filled_value, filled_units, fees, fills, status = self._await_twap_slice(
                pair=pair,
                order_id=result.order_id,
                client_order_id=client_order_id,
                slice_target_usd=slice_notional,
                replace_seconds=replace_seconds,
                poll_interval=poll_interval,
            )

            total_filled_usd += filled_value
            total_fees += fees
            total_filled_units += filled_units

            status_upper = (status or "").upper()

            if filled_value <= 0:
                if status_upper in {"CANCELED", "CANCELLED", "EXPIRED", "REJECTED"}:
                    logger.info(
                        "TWAP: slice %d for %s ended %s with no fills; widening and retrying",
                        attempt,
                        pair,
                        status_upper,
                    )
                    consecutive_no_fill = 0
                    continue

                consecutive_no_fill += 1
                if consecutive_no_fill >= max_consecutive_no_fill:
                    logger.warning(
                        "TWAP: slice %d for %s produced no fill (status=%s); max consecutive retries %d reached",
                        attempt,
                        pair,
                        status,
                        max_consecutive_no_fill,
                    )
                    
                    # Aggressive purge fallback: use taker/market for tiny junk positions
                    allow_taker = purge_cfg.get("allow_taker_fallback", False)
                    taker_threshold = float(purge_cfg.get("taker_fallback_threshold_usd", 50.0))
                    taker_max_slippage = float(purge_cfg.get("taker_max_slippage_bps", 100))
                    
                    remaining_usd = max(target_value_usd - total_filled_usd, 0.0)
                    
                    if allow_taker and remaining_usd > 0 and remaining_usd <= taker_threshold:
                        logger.warning(
                            "TWAP: activating aggressive purge mode for %s (remaining=$%.2f < $%.2f threshold)",
                            pair,
                            remaining_usd,
                            taker_threshold,
                        )
                        
                        # Try one final taker/IOC order to force completion
                        try:
                            taker_client_id = f"purge_taker_{uuid4().hex[:14]}"
                            logger.info(
                                "TWAP: forcing taker sell ~$%.2f %s (max_slippage=%.1f%%)",
                                remaining_usd,
                                pair,
                                taker_max_slippage / 100,
                            )
                            
                            taker_result = self.executor.execute(
                                symbol=pair,
                                side="SELL",
                                size_usd=remaining_usd,
                                client_order_id=taker_client_id,
                                force_order_type="limit_ioc",  # IOC to force immediate fill or cancel
                                skip_liquidity_checks=True,
                                tier=tier,
                                bypass_slippage_budget=True,
                                bypass_failed_order_cooldown=True,
                            )
                            
                            if taker_result.success and taker_result.filled_usd > 0:
                                total_filled_usd += taker_result.filled_usd
                                total_filled_units += taker_result.filled_size
                                total_fees += taker_result.total_fees
                                logger.info(
                                    "TWAP: taker fallback filled $%.2f (%.6f %s), total now $%.2f",
                                    taker_result.filled_usd,
                                    taker_result.filled_size,
                                    currency,
                                    total_filled_usd,
                                )
                                # Exit the loop - we forced completion
                                break
                            else:
                                logger.warning(
                                    "TWAP: taker fallback failed for %s: %s",
                                    pair,
                                    taker_result.error or "no fill",
                                )
                        except Exception as exc:
                            logger.error("TWAP: taker fallback exception for %s: %s", pair, exc)
                    
                    break

                logger.info(
                    "TWAP: slice %d for %s produced no fill (status=%s); retrying (%d/%d)",
                    attempt,
                    pair,
                    status,
                    consecutive_no_fill,
                    max_consecutive_no_fill,
                )
                continue

            consecutive_no_fill = 0

            liquidity_tags = sorted({str(fill.get("liquidity_indicator")) for fill in fills if isinstance(fill, dict) and fill.get("liquidity_indicator")})
            fee_bps = (fees / filled_value * 10_000) if filled_value > 0 else 0.0

            logger.info(
                "TWAP slice %d: filled %.6f %s (~$%.2f), fees=$%.2f, status=%s",
                attempt,
                filled_units,
                currency,
                filled_value,
                fees,
                status,
            )
            if liquidity_tags:
                logger.debug(
                    "TWAP slice %d liquidity=%s, effective_fee_bps=%.2f",
                    attempt,
                    ",".join(liquidity_tags),
                    fee_bps,
                )

        residual = max(target_value_usd - total_filled_usd, 0.0)
        if residual > max_residual:
            logger.warning(
                "TWAP: residual ~$%.2f for %s exceeds threshold $%.2f", residual, pair, max_residual
            )
            return False

        if total_filled_usd <= 0:
            logger.warning("TWAP: no fills recorded for %s liquidation", pair)
            return False

        logger.info(
            "✅ TWAP liquidation complete: sold %.6f %s (~$%.2f) via %d slices, fees=$%.2f, residual=$%.2f",
            total_filled_units,
            currency,
            total_filled_usd,
            attempt,
            total_fees,
            residual,
        )
        self.portfolio = self._init_portfolio_state()
        return True

    def _await_twap_slice(
        self,
        pair: str,
        order_id: Optional[str],
        client_order_id: str,
        slice_target_usd: float,
        replace_seconds: float,
        poll_interval: float,
    ) -> Tuple[float, float, float, List[Dict[str, Any]], str]:
        """Poll order status for a TWAP slice and aggregate fills."""
        if not order_id:
            logger.warning("TWAP: missing order_id for client %s", client_order_id)
            return 0.0, 0.0, 0.0, [], "missing_order"

        terminal_states = {"FILLED", "DONE", "CANCELED", "CANCELLED", "EXPIRED", "FAILED"}
        deadline = time.monotonic() + max(replace_seconds, 1.0)
        last_status = None

        while time.monotonic() < deadline:
            try:
                status = self.exchange.get_order_status(order_id)
            except CriticalDataUnavailable:
                raise
            except Exception as exc:
                logger.debug("TWAP: status poll failed for %s: %s", order_id, exc)
                status = None

            if status:
                last_status = status.get("status") or last_status
                if (last_status or "").upper() in terminal_states:
                    break

            time.sleep(poll_interval)

        if not last_status or (last_status.upper() not in terminal_states):
            try:
                self.exchange.cancel_order(order_id)
                last_status = "CANCELED"
            except Exception as exc:
                logger.warning("TWAP: cancel failed for %s: %s", order_id, exc)
                last_status = "CANCEL_FAILED"

        try:
            fills = self.exchange.list_fills(order_id=order_id, product_id=pair) or []
        except CriticalDataUnavailable:
            raise
        except Exception as exc:
            logger.debug("TWAP: list_fills failed for %s: %s", order_id, exc)
            fills = []

        filled_units = 0.0
        filled_value = 0.0
        fees = 0.0
        for fill in fills:
            try:
                size = float(fill.get("size") or 0.0)
                price = float(fill.get("price") or 0.0)
                fee = float(fill.get("commission") or fill.get("fee") or 0.0)
            except Exception:
                continue

            filled_units += size
            filled_value += size * price
            fees += fee

        osm = getattr(self.executor, "order_state_machine", None)
        if osm:
            if filled_units > 0:
                osm.update_fill(
                    client_order_id=client_order_id,
                    filled_size=filled_units,
                    filled_value=filled_value,
                    fees=fees,
                    fills=fills,
                )
            else:
                try:
                    osm.transition(client_order_id, OrderStatus.CANCELED, error="twap_timeout_no_fill")
                except Exception:
                    pass

        closer = getattr(self.executor, "_close_order_in_state_store", None)
        if closer:
            status_payload = {
                "order_id": order_id,
                "client_order_id": client_order_id,
                "filled_size": filled_units,
                "filled_value": filled_value,
                "fees": fees,
                "status": (last_status or "unknown").lower(),
                "slice_target_usd": slice_target_usd,
            }
            try:
                closer(client_order_id, status_payload["status"], status_payload)
            except Exception as exc:
                logger.debug("TWAP: state store close failed for %s: %s", order_id, exc)

        return filled_value, filled_units, fees, fills, last_status or "unknown"
    
    def run_forever(self, interval_seconds: Optional[float] = None):
        """
        Run trading loop continuously with time-aware sleep.
        
        Args:
            interval_seconds: Seconds between cycle starts
        """
        configured_interval = float(interval_seconds) if interval_seconds else (self.loop_interval_seconds or 60.0)
        configured_interval = max(configured_interval, 1.0)

        logger.info(f"Starting continuous loop (interval={configured_interval}s)")
        
        while self._running:
            start = time.monotonic()
            self.run_cycle()
            elapsed = time.monotonic() - start
            
            # Add ±10% jitter to avoid synchronized bursts
            import random
            jitter = random.uniform(-0.1, 0.1) * configured_interval
            base_sleep = configured_interval - elapsed
            sleep_for = max(1.0, base_sleep + jitter)
            
            # Auto-backoff if cycle utilization > 70%
            utilization = elapsed / configured_interval
            if utilization > 0.7:
                backoff = 15.0
                sleep_for += backoff
                logger.warning(f"High cycle utilization ({utilization:.1%}), adding {backoff}s backoff")
            
            logger.info(f"Cycle took {elapsed:.2f}s, sleeping {sleep_for:.2f}s (util: {utilization:.1%})")
            time.sleep(sleep_for)
        
        logger.info("Trading loop stopped cleanly.")
    
    def _check_position_exits(self) -> List[TradeProposal]:
        """
        Check all open positions for exit conditions (stop-loss, take-profit, max hold).
        
        Returns:
            List of SELL TradeProposal objects for positions meeting exit criteria
        """
        try:
            # Get current positions and managed metadata
            state = self.state_store.load()
            positions = state.get("positions", {})
            managed_positions = state.get("managed_positions", {})
            
            # Get current prices from exchange
            current_prices = {}
            for symbol in positions.keys():
                pair = f"{symbol}-USD"
                try:
                    quote = self.exchange.get_quote(pair)
                    if quote and quote.ask > 0:
                        current_prices[symbol] = quote.ask
                except Exception as price_exc:
                    logger.debug(f"Failed to get price for {symbol}: {price_exc}")
            
            # Evaluate positions via PositionManager
            exit_proposals = self.position_manager.evaluate_positions(
                positions=positions,
                managed_positions=managed_positions,
                current_prices=current_prices,
            )
            
            return exit_proposals
            
        except Exception as e:
            logger.warning(f"Position exit check failed: {e}", exc_info=True)
            return []
    
    def _execute_exit_proposals(self, exit_proposals: List[TradeProposal]) -> None:
        """
        Execute exit proposals (SELL orders) immediately without risk approval.
        
        Exits bypass normal risk checks since they:
        - Reduce risk (closing positions)
        - Are time-sensitive (protect capital)
        - Have high confidence (rules-based)
        
        Args:
            exit_proposals: List of SELL TradeProposal objects
        """
        if not exit_proposals:
            return
        
        logger.info(f"Executing {len(exit_proposals)} position exit(s)")
        
        executed_exits = []
        for proposal in exit_proposals:
            logger.info(
                f"EXIT: {proposal.side.upper()} {proposal.symbol} "
                f"({proposal.trigger_name}) - {proposal.notes.get('exit_reason', 'unknown')}"
            )
            
            if self.mode == "DRY_RUN":
                logger.info(f"DRY_RUN: Would execute exit {proposal.symbol}")
                continue
            
            try:
                # Execute via ExecutionEngine
                result = self.executor.execute(
                    symbol=proposal.symbol,
                    side=proposal.side,
                    size_usd=proposal.notional_usd,
                    tier=None,  # Exits don't need tier
                )
                
                if result.success:
                    logger.info(
                        f"✅ Exit filled: {proposal.symbol} "
                        f"{result.filled_size:.6f} @ ${result.filled_price:.4f} "
                        f"(PnL: {proposal.notes.get('pnl_pct', 0):.2f}%)"
                    )
                    executed_exits.append(result)
                    
                    # Remove from managed_positions after successful exit
                    self._remove_managed_position(proposal.symbol.replace("-USD", ""))
                else:
                    logger.warning(f"⚠️ Exit failed: {proposal.symbol} - {result.error}")
                    
            except Exception as exec_exc:
                logger.error(f"Exit execution exception for {proposal.symbol}: {exec_exc}", exc_info=True)
        
        # Update state after successful exits
        if executed_exits:
            self.state_store.update_from_fills(executed_exits, self.portfolio)
            logger.info(f"Completed {len(executed_exits)} position exit(s)")
    
    def _remove_managed_position(self, symbol: str) -> None:
        """Remove a symbol from managed_positions after full exit."""
        try:
            state = self.state_store.load()
            managed = state.get("managed_positions", {})
            if symbol in managed:
                del managed[symbol]
                state["managed_positions"] = managed
                self.state_store.save(state)
                logger.debug(f"Removed {symbol} from managed_positions")
        except Exception as e:
            logger.warning(f"Failed to remove managed position {symbol}: {e}")


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
