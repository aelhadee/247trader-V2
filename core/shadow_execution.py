"""
Shadow DRY_RUN Mode - Detailed Execution Logging

Logs comprehensive execution details without submitting orders.
Used for production validation and parallel comparison with live execution.
"""

import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


@dataclass
class ShadowOrder:
    """Shadow order for validation logging"""
    timestamp: str
    symbol: str
    side: str
    size_usd: float
    size_units: float
    quote_mid: float
    quote_bid: float
    quote_ask: float
    quote_spread_bps: float
    quote_age_ms: float
    
    # Execution plan
    intended_route: str  # "maker" | "taker" | "ioc"
    intended_price: float
    expected_slippage_bps: float
    expected_fees_usd: float
    
    # Risk context
    tier: str
    confidence: Optional[float]
    conviction: Optional[float]
    
    # Liquidity checks
    passed_spread_check: bool
    passed_depth_check: bool
    orderbook_depth_20bps_usd: Optional[float]
    
    # Order details
    client_order_id: str
    would_place: bool  # False if failed validation
    rejection_reason: Optional[str]
    
    # Metadata
    mode: str  # Should be "SHADOW_DRY_RUN"
    config_hash: str


class ShadowExecutionLogger:
    """
    Logs detailed execution plans without submitting orders.
    
    Purpose:
    - Production validation before scaling capital
    - Parallel comparison with live execution
    - Debugging and tuning without risk
    
    Usage:
        shadow_logger = ShadowExecutionLogger("logs/shadow_orders.jsonl")
        shadow_logger.log_order(...)
    """
    
    def __init__(self, log_file: str = "logs/shadow_orders.jsonl"):
        self.log_file = Path(log_file)
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        
        # Create file if it doesn't exist
        if not self.log_file.exists():
            self.log_file.touch()
            logger.info(f"Created shadow execution log: {self.log_file}")
    
    def log_order(self, shadow_order: ShadowOrder) -> None:
        """
        Log a shadow order to JSONL file.
        
        Each line is a complete JSON object for easy parsing.
        """
        try:
            with open(self.log_file, 'a') as f:
                json_line = json.dumps(asdict(shadow_order))
                f.write(json_line + '\n')
        except Exception as e:
            logger.error(f"Failed to write shadow order log: {e}")
    
    def log_rejection(self,
                     symbol: str,
                     side: str,
                     size_usd: float,
                     reason: str,
                     context: Optional[Dict[str, Any]] = None) -> None:
        """
        Log an order that would have been rejected.
        
        Args:
            symbol: Trading pair symbol
            side: BUY or SELL
            size_usd: Order size in USD
            reason: Rejection reason
            context: Additional context (optional)
        """
        rejection_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": "rejection",
            "symbol": symbol,
            "side": side,
            "size_usd": size_usd,
            "reason": reason,
            "context": context or {}
        }
        
        try:
            with open(self.log_file, 'a') as f:
                json_line = json.dumps(rejection_entry)
                f.write(json_line + '\n')
        except Exception as e:
            logger.error(f"Failed to write rejection log: {e}")
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Get statistics from shadow log.
        
        Returns:
            Dictionary with counts and metrics
        """
        if not self.log_file.exists():
            return {"total": 0, "placed": 0, "rejected": 0}
        
        total = 0
        placed = 0
        rejected = 0
        rejections_by_reason = {}
        
        try:
            with open(self.log_file, 'r') as f:
                for line in f:
                    if not line.strip():
                        continue
                    
                    entry = json.loads(line)
                    total += 1
                    
                    if entry.get("type") == "rejection":
                        rejected += 1
                        reason = entry.get("reason", "unknown")
                        rejections_by_reason[reason] = rejections_by_reason.get(reason, 0) + 1
                    elif entry.get("would_place"):
                        placed += 1
                    else:
                        rejected += 1
                        reason = entry.get("rejection_reason", "unknown")
                        rejections_by_reason[reason] = rejections_by_reason.get(reason, 0) + 1
        
        except Exception as e:
            logger.error(f"Failed to read shadow log stats: {e}")
            return {"error": str(e)}
        
        return {
            "total": total,
            "would_place": placed,
            "rejected": rejected,
            "rejection_reasons": rejections_by_reason
        }
    
    def clear_log(self) -> None:
        """Clear the shadow log file (use with caution)"""
        if self.log_file.exists():
            self.log_file.unlink()
            self.log_file.touch()
            logger.info(f"Cleared shadow execution log: {self.log_file}")


def create_shadow_order(
    symbol: str,
    side: str,
    size_usd: float,
    size_units: float,
    quote: Any,  # Quote dataclass
    intended_route: str,
    intended_price: float,
    expected_slippage_bps: float,
    expected_fees_usd: float,
    tier: str,
    client_order_id: str,
    passed_spread_check: bool,
    passed_depth_check: bool,
    would_place: bool,
    rejection_reason: Optional[str],
    config_hash: str,
    confidence: Optional[float] = None,
    conviction: Optional[float] = None,
    orderbook_depth_20bps_usd: Optional[float] = None,
) -> ShadowOrder:
    """
    Helper to create a ShadowOrder from execution parameters.
    """
    # Calculate quote age
    if hasattr(quote, 'timestamp') and quote.timestamp:
        if isinstance(quote.timestamp, datetime):
            quote_ts = quote.timestamp
        else:
            quote_ts = datetime.fromisoformat(str(quote.timestamp))
        
        if quote_ts.tzinfo is None:
            quote_ts = quote_ts.replace(tzinfo=timezone.utc)
        
        age = (datetime.now(timezone.utc) - quote_ts).total_seconds() * 1000
    else:
        age = 0.0
    
    return ShadowOrder(
        timestamp=datetime.now(timezone.utc).isoformat(),
        symbol=symbol,
        side=side,
        size_usd=size_usd,
        size_units=size_units,
        quote_mid=(quote.bid + quote.ask) / 2,
        quote_bid=quote.bid,
        quote_ask=quote.ask,
        quote_spread_bps=quote.spread_bps,
        quote_age_ms=age,
        intended_route=intended_route,
        intended_price=intended_price,
        expected_slippage_bps=expected_slippage_bps,
        expected_fees_usd=expected_fees_usd,
        tier=tier,
        confidence=confidence,
        conviction=conviction,
        passed_spread_check=passed_spread_check,
        passed_depth_check=passed_depth_check,
        orderbook_depth_20bps_usd=orderbook_depth_20bps_usd,
        client_order_id=client_order_id,
        would_place=would_place,
        rejection_reason=rejection_reason,
        mode="SHADOW_DRY_RUN",
        config_hash=config_hash
    )
