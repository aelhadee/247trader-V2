"""
247trader-v2 Core: Audit Logger

Structured logging of all trading decisions for compliance, debugging, and analysis.
"""

import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class AuditLogger:
    """
    Structured audit trail logger.
    
    Logs every cycle decision including:
    - Universe composition
    - Trigger signals
    - Proposals generated
    - Risk check results
    - Execution outcomes
    - NO_TRADE reasons
    
    Output format: JSONL (one JSON object per line)
    """
    
    def __init__(self, audit_file: Optional[str] = None):
        """
        Initialize audit logger.
        
        Args:
            audit_file: Path to audit log file (default: logs/audit.jsonl)
        """
        if audit_file:
            self.audit_file = Path(audit_file)
        else:
            self.audit_file = Path("logs/audit.jsonl")
        
        # Ensure directory exists
        self.audit_file.parent.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"Initialized AuditLogger at {self.audit_file}")
    
    def log_cycle(self,
                  ts: datetime,
                  mode: str,
                  universe: Optional[Any],
                  triggers: Optional[Any],
                  base_proposals: List[Any],
                  risk_approved: List[Any],
                  final_orders: List[Any],
                  no_trade_reason: Optional[str] = None,
                  risk_violations: Optional[List[str]] = None,
                  proposal_rejections: Optional[Dict[str, List[str]]] = None,
                  state_store: Optional[Any] = None,
                  stage_latencies: Optional[Dict[str, float]] = None,
                  config_hash: Optional[str] = None,
                  arbitration_log: Optional[List[Any]] = None) -> None:
        """
        Log a complete trading cycle.
        
        Args:
            ts: Cycle timestamp
            mode: Trading mode (DRY_RUN, PAPER, LIVE)
            universe: Universe object
            triggers: Trigger results
            base_proposals: Initial proposals from rules engine
            risk_approved: Proposals that passed risk checks
            final_orders: Orders that were executed
            no_trade_reason: Reason if no trade occurred
            risk_violations: List of violated risk checks
            stage_latencies: Optional per-stage timing snapshot for the cycle
        """
        try:
            # Build structured log entry
            entry = {
                "timestamp": ts.isoformat(),
                "mode": mode,
                "status": self._determine_status(final_orders, no_trade_reason),
                "no_trade_reason": no_trade_reason,
                "config_hash": config_hash,  # Configuration drift detection
            }

            if stage_latencies:
                entry["stage_latencies"] = stage_latencies
            
            # PnL summary from state_store
            if state_store:
                try:
                    state = state_store.load()
                    entry["pnl"] = {
                        "daily_usd": round(state.get("pnl_today", 0.0), 2),
                        "weekly_usd": round(state.get("pnl_week", 0.0), 2),
                        "open_positions": len(state.get("positions", {})),
                        "consecutive_losses": state.get("consecutive_losses", 0)
                    }
                except Exception as e:
                    logger.warning(f"Failed to read PnL from state: {e}")
                    entry["pnl"] = None
            else:
                entry["pnl"] = None
            
            # Universe summary
            if universe:
                entry["universe"] = {
                    "total_eligible": getattr(universe, 'total_eligible', 0),
                    "tier_1": len(getattr(universe, 'tier_1_assets', [])),
                    "tier_2": len(getattr(universe, 'tier_2_assets', [])),
                    "tier_3": len(getattr(universe, 'tier_3_assets', [])),
                }
            else:
                entry["universe"] = None
            
            # Triggers summary
            if triggers:
                if hasattr(triggers, '__len__'):
                    entry["triggers"] = {
                        "count": len(triggers),
                        "top_3": [
                            {
                                "symbol": t.symbol,
                                "type": t.trigger_type,
                                "strength": round(t.strength, 3),
                            }
                            for t in (triggers[:3] if triggers else [])
                        ]
                    }
                elif hasattr(triggers, 'candidates'):
                    entry["triggers"] = {
                        "count": len(triggers.candidates),
                        "top_3": [
                            {
                                "symbol": c,
                                "type": "candidate"
                            }
                            for c in list(triggers.candidates)[:3]
                        ]
                    }
            else:
                entry["triggers"] = None
            
            # Proposals summary
            entry["proposals"] = {
                "base_count": len(base_proposals),
                "risk_approved_count": len(risk_approved),
                "final_executed_count": len(final_orders),
            }
            
            # Dual-trader arbitration log
            if arbitration_log:
                entry["arbitration"] = [
                    {
                        "symbol": decision.symbol,
                        "resolution": decision.resolution,
                        "reason": decision.reason,
                        "local_side": decision.local_proposal.side if decision.local_proposal else None,
                        "local_size_pct": decision.local_proposal.target_weight_pct if decision.local_proposal else None,
                        "local_conviction": decision.local_proposal.conviction if decision.local_proposal else None,
                        "ai_side": decision.ai_proposal.side if decision.ai_proposal else None,
                        "ai_size_pct": decision.ai_proposal.target_weight_pct if decision.ai_proposal else None,
                        "ai_confidence": decision.ai_proposal.conviction if decision.ai_proposal else None,
                        "final_side": decision.final_proposal.side if decision.final_proposal else None,
                        "final_size_pct": decision.final_proposal.target_weight_pct if decision.final_proposal else None,
                    }
                    for decision in arbitration_log
                ]
            
            # Risk violations
            if risk_violations:
                entry["risk_violations"] = risk_violations

            if proposal_rejections:
                entry["proposal_rejections"] = proposal_rejections
            
            # Final orders detail
            if final_orders:
                entry["orders"] = [
                    self._serialize_order(order)
                    for order in final_orders
                ]
            else:
                entry["orders"] = []
            
            # Write JSONL (one JSON per line)
            with open(self.audit_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
            
            logger.debug(f"Audited cycle: status={entry['status']}")
            
        except Exception as e:
            logger.error(f"Failed to write audit log: {e}")
    
    def _determine_status(self, final_orders: List[Any], no_trade_reason: Optional[str]) -> str:
        """Determine cycle status"""
        if no_trade_reason:
            return "NO_TRADE"
        elif final_orders:
            return "EXECUTED"
        else:
            return "NO_OPPORTUNITIES"
    
    def _serialize_order(self, order: Any) -> Dict[str, Any]:
        """Serialize an order for logging"""
        if hasattr(order, '__dict__'):
            # Object with attributes
            return {
                "symbol": getattr(order, 'symbol', None),
                "side": getattr(order, 'side', None),
                "size_usd": getattr(order, 'size_usd', None),
                "order_id": getattr(order, 'order_id', None),
                "success": getattr(order, 'success', None),
                "error": getattr(order, 'error', None),
            }
        elif isinstance(order, dict):
            # Dict result
            return {
                "symbol": order.get('symbol'),
                "side": order.get('side'),
                "size_usd": order.get('size_usd'),
                "order_id": order.get('order_id'),
                "success": order.get('success'),
                "error": order.get('error'),
            }
        else:
            return {"raw": str(order)}
    
    def get_recent_cycles(self, n: int = 10) -> List[Dict[str, Any]]:
        """
        Get the N most recent cycle logs.
        
        Args:
            n: Number of cycles to retrieve
            
        Returns:
            List of cycle log entries (most recent first)
        """
        if not self.audit_file.exists():
            return []
        
        try:
            with open(self.audit_file, "r", encoding="utf-8") as f:
                lines = f.readlines()
            
            # Parse last N lines
            cycles = []
            for line in lines[-n:]:
                try:
                    cycles.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            
            return list(reversed(cycles))  # Most recent first
            
        except Exception as e:
            logger.error(f"Failed to read audit log: {e}")
            return []
