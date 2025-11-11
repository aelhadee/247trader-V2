"""
247trader-v2 Core: Execution Engine

Order placement with preview, route selection, and idempotency.
Ported from v1 with simplified logic for rules-first strategy.
"""

import uuid
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime
import logging

from core.exchange_coinbase import CoinbaseExchange, get_exchange

logger = logging.getLogger(__name__)


@dataclass
class ExecutionResult:
    """Result of order execution"""
    success: bool
    order_id: Optional[str]
    filled_size: float
    filled_price: float
    fees: float
    slippage_bps: float
    route: str  # "market_ioc" | "limit_post" | "dry_run"
    error: Optional[str] = None
    timestamp: datetime = None
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.utcnow()


class ExecutionEngine:
    """
    Order execution engine.
    
    Responsibilities:
    - Preview orders before placement
    - Select best route (limit post-only vs market IOC)
    - Place orders with idempotency
    - Track fills and fees
    
    Safety:
    - DRY_RUN mode prevents real orders
    - Liquidity checks before placement
    - Slippage protection
    """
    
    def __init__(self, mode: str = "DRY_RUN", exchange: Optional[CoinbaseExchange] = None,
                 policy: Optional[Dict] = None):
        """
        Initialize execution engine.
        
        Args:
            mode: "DRY_RUN" | "PAPER" | "LIVE"
            exchange: Coinbase exchange instance
            policy: Policy configuration dict (optional, for reading limits)
        """
        self.mode = mode.upper()
        self.exchange = exchange or get_exchange()
        self.policy = policy or {}
        
        # Load limits from policy or use defaults
        execution_config = self.policy.get("execution", {})
        microstructure_config = self.policy.get("microstructure", {})
        risk_config = self.policy.get("risk", {})
        
        self.max_slippage_bps = microstructure_config.get("max_expected_slippage_bps", 50.0)
        self.max_spread_bps = microstructure_config.get("max_spread_bps", 100.0)
        self.min_notional_usd = risk_config.get("min_trade_notional_usd", 100.0)  # From policy.yaml
        self.min_depth_multiplier = 2.0  # Want 2x order size in depth
        
        # Order type preference
        self.default_order_type = execution_config.get("default_order_type", "limit")
        self.limit_post_only = (self.default_order_type == "limit_post_only")
        
        logger.info(f"Initialized ExecutionEngine (mode={self.mode}, min_notional=${self.min_notional_usd})")
    
    def preview_order(self, symbol: str, side: str, size_usd: float) -> Dict:
        """
        Preview an order without placing it.
        
        Args:
            symbol: e.g. "BTC-USD"
            side: "BUY" or "SELL"
            size_usd: USD amount to trade
            
        Returns:
            Preview result with estimated fills, fees, slippage
        """
        if size_usd < self.min_notional_usd:
            return {
                "success": False,
                "error": f"Size ${size_usd:.2f} below minimum ${self.min_notional_usd}"
            }
        
        try:
            # Get quote for slippage estimate
            quote = self.exchange.get_quote(symbol)
            
            # Check spread
            if quote.spread_bps > self.max_spread_bps:
                return {
                    "success": False,
                    "error": f"Spread {quote.spread_bps:.1f}bps exceeds max {self.max_spread_bps}bps"
                }
            
            # Check orderbook depth (critical for LIVE mode)
            try:
                orderbook = self.exchange.get_orderbook(symbol, depth_levels=20)
                
                # Calculate depth within 20bps of mid
                mid = quote.mid
                depth_20bps_usd = 0.0
                
                if side.upper() == "BUY":
                    # For buys, check ask side depth
                    max_price = mid * 1.002  # 20bps above mid
                    for level in orderbook.asks:
                        if level["price"] <= max_price:
                            depth_20bps_usd += level["price"] * level["size"]
                else:
                    # For sells, check bid side depth
                    min_price = mid * 0.998  # 20bps below mid
                    for level in orderbook.bids:
                        if level["price"] >= min_price:
                            depth_20bps_usd += level["price"] * level["size"]
                
                # Check if sufficient depth
                min_depth_required = size_usd * 2.0  # Want 2x our size available
                if depth_20bps_usd < min_depth_required:
                    return {
                        "success": False,
                        "error": f"Insufficient depth: ${depth_20bps_usd:.0f} < ${min_depth_required:.0f} required (20bps)"
                    }
                
                logger.debug(f"Depth check passed: ${depth_20bps_usd:.0f} available for ${size_usd:.0f} order")
                
            except Exception as e:
                logger.warning(f"Depth check failed (continuing): {e}")
                # In LIVE mode, depth check failure should block execution
                if self.mode == "LIVE":
                    return {
                        "success": False,
                        "error": f"Cannot verify orderbook depth: {e}"
                    }
            
            # Estimate fill
            if side.upper() == "BUY":
                estimated_price = quote.ask
            else:
                estimated_price = quote.bid
            
            estimated_size = size_usd / estimated_price
            estimated_fees = size_usd * 0.006  # Coinbase fee ~0.6%
            estimated_slippage_bps = quote.spread_bps / 2
            
            # If not DRY_RUN and auth available, call real preview API
            if self.mode != "DRY_RUN" and self.exchange.api_key:
                try:
                    api_preview = self.exchange.preview_order(symbol, side.lower(), size_usd)
                    # Parse API response if successful
                    if api_preview.get("success"):
                        # Update estimates from API
                        pass
                except Exception as e:
                    logger.warning(f"API preview failed, using estimates: {e}")
            
            return {
                "success": True,
                "symbol": symbol,
                "side": side,
                "size_usd": size_usd,
                "estimated_price": estimated_price,
                "estimated_size": estimated_size,
                "estimated_fees": estimated_fees,
                "estimated_slippage_bps": estimated_slippage_bps,
                "spread_bps": quote.spread_bps,
            }
            
        except Exception as e:
            logger.error(f"Preview failed for {symbol}: {e}")
            return {"success": False, "error": str(e)}
    
    def execute(self, symbol: str, side: str, size_usd: float,
                client_order_id: Optional[str] = None,
                max_slippage_bps: Optional[float] = None) -> ExecutionResult:
        """
        Execute a trade.
        
        Args:
            symbol: e.g. "BTC-USD"
            side: "BUY" or "SELL"
            size_usd: USD amount to trade
            client_order_id: Optional idempotency key
            max_slippage_bps: Optional slippage limit (overrides default)
            
        Returns:
            ExecutionResult with fill details
        """
        # Validate mode
        if self.mode == "DRY_RUN":
            logger.info(f"DRY_RUN: Would execute {side} ${size_usd:.2f} of {symbol}")
            return ExecutionResult(
                success=True,
                order_id=f"dry_run_{uuid.uuid4().hex[:8]}",
                filled_size=0.0,
                filled_price=0.0,
                fees=0.0,
                slippage_bps=0.0,
                route="dry_run"
            )
        
        if self.mode == "PAPER":
            # Simulate execution with live quotes
            return self._execute_paper(symbol, side, size_usd, client_order_id)
        
        if self.mode == "LIVE":
            # Real execution
            return self._execute_live(symbol, side, size_usd, client_order_id, max_slippage_bps)
        
        raise ValueError(f"Invalid mode: {self.mode}")
    
    def _execute_paper(self, symbol: str, side: str, size_usd: float,
                      client_order_id: Optional[str]) -> ExecutionResult:
        """
        Simulate execution with live quotes (paper trading).
        """
        logger.info(f"PAPER: Simulating {side} ${size_usd:.2f} of {symbol}")
        
        try:
            # Get live quote
            quote = self.exchange.get_quote(symbol)
            
            # Simulate fill at ask (buy) or bid (sell)
            if side.upper() == "BUY":
                fill_price = quote.ask
            else:
                fill_price = quote.bid
            
            filled_size = size_usd / fill_price
            fees = size_usd * 0.006  # Estimate 0.6%
            slippage_bps = quote.spread_bps / 2
            
            return ExecutionResult(
                success=True,
                order_id=f"paper_{uuid.uuid4().hex[:8]}",
                filled_size=filled_size,
                filled_price=fill_price,
                fees=fees,
                slippage_bps=slippage_bps,
                route="paper_simulated"
            )
            
        except Exception as e:
            logger.error(f"Paper execution failed: {e}")
            return ExecutionResult(
                success=False,
                order_id=None,
                filled_size=0.0,
                filled_price=0.0,
                fees=0.0,
                slippage_bps=0.0,
                route="paper_simulated",
                error=str(e)
            )
    
    def _execute_live(self, symbol: str, side: str, size_usd: float,
                     client_order_id: Optional[str],
                     max_slippage_bps: Optional[float]) -> ExecutionResult:
        """
        Execute real order on Coinbase.
        """
        if self.exchange.read_only:
            raise ValueError("Cannot execute LIVE orders with read_only exchange")
        
        logger.warning(f"LIVE: Executing {side} ${size_usd:.2f} of {symbol}")
        
        # Generate client order ID for idempotency
        if not client_order_id:
            client_order_id = str(uuid.uuid4())
        
        try:
            # Preview first
            preview = self.preview_order(symbol, side, size_usd)
            if not preview.get("success"):
                return ExecutionResult(
                    success=False,
                    order_id=None,
                    filled_size=0.0,
                    filled_price=0.0,
                    fees=0.0,
                    slippage_bps=0.0,
                    route="live_market_ioc",
                    error=preview.get("error", "Preview failed")
                )
            
            # Check slippage
            max_slip = max_slippage_bps or self.max_slippage_bps
            if preview.get("estimated_slippage_bps", 0) > max_slip:
                return ExecutionResult(
                    success=False,
                    order_id=None,
                    filled_size=0.0,
                    filled_price=0.0,
                    fees=0.0,
                    slippage_bps=preview["estimated_slippage_bps"],
                    route="live_market_ioc",
                    error=f"Slippage {preview['estimated_slippage_bps']:.1f}bps exceeds max {max_slip}bps"
                )
            
            # Place order (use post-only if configured)
            order_type = "limit_post_only" if self.limit_post_only else "market"
            result = self.exchange.place_order(
                product_id=symbol,
                side=side.lower(),
                quote_size_usd=size_usd,
                client_order_id=client_order_id,
                order_type=order_type
            )
            
            route = f"live_{order_type}"
            
            # Parse result
            order_id = result.get("order_id") or result.get("success_response", {}).get("order_id")
            
            # Extract fill details
            filled_size = 0.0
            filled_price = 0.0
            fees = 0.0
            
            # Coinbase response structure varies; best-effort parsing
            fills = result.get("fills", [])
            if fills:
                for fill in fills:
                    filled_size += float(fill.get("size", 0))
                    filled_price += float(fill.get("price", 0)) * float(fill.get("size", 0))
                    fees += float(fill.get("fee", 0))
                if filled_size > 0:
                    filled_price /= filled_size
            
            actual_slippage = preview.get("estimated_slippage_bps", 0)
            
            return ExecutionResult(
                success=True,
                order_id=order_id,
                filled_size=filled_size,
                filled_price=filled_price,
                fees=fees,
                slippage_bps=actual_slippage,
                route=route
            )
            
        except Exception as e:
            logger.error(f"Live execution failed: {e}")
            order_type = "limit_post_only" if self.limit_post_only else "market"
            return ExecutionResult(
                success=False,
                order_id=None,
                filled_size=0.0,
                filled_price=0.0,
                fees=0.0,
                slippage_bps=0.0,
                route=f"live_{order_type}",
                error=str(e)
            )
    
    def execute_batch(self, orders: List[Dict]) -> List[ExecutionResult]:
        """
        Execute multiple orders sequentially.
        
        Args:
            orders: List of order dicts with keys: symbol, side, size_usd
            
        Returns:
            List of ExecutionResults
        """
        results = []
        for order in orders:
            result = self.execute(
                symbol=order["symbol"],
                side=order["side"],
                size_usd=order["size_usd"],
                client_order_id=order.get("client_order_id"),
                max_slippage_bps=order.get("max_slippage_bps")
            )
            results.append(result)
            
            # Stop on first failure if critical
            if not result.success and order.get("critical", False):
                logger.warning("Critical order failed, stopping batch execution")
                break
        
        return results


# Singleton instance
_executor = None


def get_executor(mode: str = "DRY_RUN", policy: Optional[Dict] = None) -> ExecutionEngine:
    """Get singleton executor instance"""
    global _executor
    if _executor is None or _executor.mode != mode.upper():
        _executor = ExecutionEngine(mode=mode, policy=policy)
    return _executor
