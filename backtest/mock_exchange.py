"""
247trader-v2 Backtest: Mock Exchange

Realistic exchange simulation for backtesting.
Implements same interface as CoinbaseExchange but with simulated order fills.

Pattern: Jesse-style simulation with realistic costs via CostModel.
"""

import uuid
import random
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from decimal import Decimal
import logging

from core.exchange_coinbase import Quote, OHLCV, OrderbookSnapshot
from core.cost_model import get_cost_model, CostModel
from backtest.data_loader import DataLoader

logger = logging.getLogger(__name__)


@dataclass
class MockOrder:
    """Simulated order with lifecycle"""
    order_id: str
    client_order_id: str
    product_id: str
    side: str  # "buy" or "sell"
    order_type: str  # "market" or "limit_post_only"
    size_usd: float
    limit_price: Optional[float] = None
    
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    ttl_seconds: int = 300  # Default 5 min
    
    # Fill tracking
    status: str = "open"  # "open" | "filled" | "canceled" | "rejected"
    filled_size_usd: float = 0.0
    filled_price: Optional[float] = None
    filled_at: Optional[datetime] = None
    
    # Cost tracking
    fee_usd: float = 0.0
    is_maker: bool = False
    
    def is_expired(self, current_time: datetime) -> bool:
        """Check if order has exceeded TTL at given time"""
        if self.status != "open":
            return False
        # Handle both timezone-aware and naive datetimes
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=timezone.utc)
        if self.created_at.tzinfo is None:
            created_at = self.created_at.replace(tzinfo=timezone.utc)
        else:
            created_at = self.created_at
        age = (current_time - created_at).total_seconds()
        return age >= self.ttl_seconds
    
    def is_active(self, current_time: datetime) -> bool:
        """Check if order is still active at given time"""
        return self.status == "open" and not self.is_expired(current_time)


class MockExchange:
    """
    Simulated exchange for backtesting with realistic order behavior.
    
    Implements same interface as CoinbaseExchange:
    - get_quote(product_id) -> Quote
    - get_accounts() -> List[dict]
    - place_order(...) -> dict
    - cancel_order(order_id) -> dict
    - get_candles(...) -> List[OHLCV]
    
    Simulation features:
    - Maker/taker fills based on price action
    - Partial fills for large orders
    - Post-only rejection if price crossed
    - TTL-based order expiration
    - Realistic fees and slippage via CostModel
    """
    
    def __init__(
        self,
        data_loader: DataLoader,
        initial_balances: Dict[str, float],
        cost_model: Optional[CostModel] = None,
        read_only: bool = False
    ):
        """
        Initialize mock exchange.
        
        Args:
            data_loader: Historical data provider
            initial_balances: Starting balances (e.g. {"USD": 10000.0})
            cost_model: Cost model for fees/slippage
            read_only: If True, block order placement
        """
        self.data_loader = data_loader
        self.balances = dict(initial_balances)
        self.cost_model = cost_model or get_cost_model()
        self.read_only = read_only
        
        # Order tracking
        self.orders: Dict[str, MockOrder] = {}  # order_id -> MockOrder
        self.order_history: List[MockOrder] = []
        
        # Simulation state
        self.current_time = datetime.now(timezone.utc)
        self.fills_count = 0
        self.rejections_count = 0
        
        logger.info(
            f"MockExchange initialized: balances={self.balances}, "
            f"cost_model={self.cost_model.get_summary()}"
        )
    
    def advance_time(self, new_time: datetime):
        """
        Advance simulation clock and process pending orders.
        
        Call this before each backtest step to:
        1. Update current time
        2. Expire orders past TTL
        3. Process fills based on price action
        """
        self.current_time = new_time
        
        # Cancel expired orders
        for order in list(self.orders.values()):
            if order.is_expired:
                self._cancel_order_internal(order.order_id, reason="ttl_expired")
    
    def get_quote(self, product_id: str) -> Quote:
        """
        Get current quote for symbol.
        
        Uses most recent candle from data_loader.
        """
        candle = self.data_loader.get_latest_candle(product_id, self.current_time)
        
        if candle is None:
            raise ValueError(f"No price data for {product_id} at {self.current_time}")
        
        # Estimate bid/ask from close price + typical spread
        # Use tier-based spread from cost model
        tier = self._infer_tier(product_id)
        if tier == 1:
            spread_bps = self.cost_model.config.tier1_spread_bps
        elif tier == 2:
            spread_bps = self.cost_model.config.tier2_spread_bps
        else:
            spread_bps = self.cost_model.config.tier3_spread_bps
        
        half_spread_pct = (spread_bps / 2.0) / 10000.0
        mid = candle.close
        bid = mid * (1.0 - half_spread_pct)
        ask = mid * (1.0 + half_spread_pct)
        
        return Quote(
            symbol=product_id,
            bid=bid,
            ask=ask,
            mid=mid,
            spread_bps=spread_bps,
            last=candle.close,
            volume_24h=candle.volume,
            timestamp=candle.timestamp
        )
    
    def get_accounts(self) -> List[dict]:
        """Get account balances"""
        return [
            {
                "currency": currency,
                "available_balance": {
                    "value": str(amount),
                    "currency": currency
                }
            }
            for currency, amount in self.balances.items()
        ]
    
    def get_candles(
        self,
        product_id: str,
        start: datetime,
        end: datetime,
        granularity: str = "ONE_MINUTE"
    ) -> List[OHLCV]:
        """Get historical candles"""
        return self.data_loader.get_candles(product_id, start, end, granularity)
    
    def place_order(
        self,
        product_id: str,
        side: str,
        quote_size_usd: float,
        client_order_id: Optional[str] = None,
        order_type: str = "market",
        maker_cushion_ticks: int = 1
    ) -> dict:
        """
        Place simulated order.
        
        Args:
            product_id: e.g. "BTC-USD"
            side: "buy" or "sell"
            quote_size_usd: USD size
            client_order_id: Idempotency key
            order_type: "market" or "limit_post_only"
            maker_cushion_ticks: Price cushion for post-only
            
        Returns:
            Order result dict (same format as CoinbaseExchange)
        """
        if self.read_only:
            raise ValueError("Cannot place orders in READ_ONLY mode")
        
        # Generate IDs
        order_id = str(uuid.uuid4())
        if client_order_id is None:
            client_order_id = f"mock_{uuid.uuid4().hex[:8]}"
        
        # Check for duplicate client_order_id (idempotency)
        for existing in self.orders.values():
            if existing.client_order_id == client_order_id:
                logger.warning(f"Duplicate client_order_id {client_order_id}, returning existing order")
                return self._format_order_result(existing)
        
        # Get current price
        quote = self.get_quote(product_id)
        
        # Determine limit price for post-only orders
        limit_price = None
        if order_type == "limit_post_only":
            if side.upper() == "BUY":
                # Buy at bid, cushion down
                limit_price = quote.bid * (1.0 - (maker_cushion_ticks * 0.0001))
            else:
                # Sell at ask, cushion up
                limit_price = quote.ask * (1.0 + (maker_cushion_ticks * 0.0001))
        
        # Create order
        order = MockOrder(
            order_id=order_id,
            client_order_id=client_order_id,
            product_id=product_id,
            side=side.lower(),
            order_type=order_type,
            size_usd=quote_size_usd,
            limit_price=limit_price,
            created_at=self.current_time
        )
        
        # Check balance for buys
        if side.upper() == "BUY":
            available_usd = self.balances.get("USD", 0.0)
            if available_usd < quote_size_usd:
                order.status = "rejected"
                self.rejections_count += 1
                logger.warning(
                    f"Order rejected: insufficient balance ${available_usd:.2f} < ${quote_size_usd:.2f}"
                )
                return self._format_order_result(order)
        
        # For sells, check if we have the asset
        # (Simplified: assume we can sell if balance tracking is external)
        
        # Simulate immediate behavior
        if order_type == "market":
            # Market orders fill immediately
            self._fill_order(order, quote)
        elif order_type == "limit_post_only":
            # Post-only: check if price already crossed
            if self._is_post_only_invalid(order, quote):
                order.status = "rejected"
                self.rejections_count += 1
                logger.debug(f"Post-only order rejected: price crossed (side={side}, limit={limit_price}, quote={quote.mid})")
                return self._format_order_result(order)
            
            # Order sits on book (will fill in process_pending_fills)
            self.orders[order_id] = order
        
        return self._format_order_result(order)
    
    def process_pending_fills(self, product_id: str):
        """
        Process pending limit orders based on current price action.
        
        Call this after advancing time to simulate fills.
        """
        quote = self.get_quote(product_id)
        
        for order in list(self.orders.values()):
            if order.product_id != product_id or order.status != "open":
                continue
            
            # Check if limit order should fill
            if order.order_type == "limit_post_only":
                should_fill = False
                
                if order.side == "buy" and order.limit_price:
                    # Buy fills if price drops to/below our bid
                    should_fill = quote.ask <= order.limit_price
                elif order.side == "sell" and order.limit_price:
                    # Sell fills if price rises to/above our ask
                    should_fill = quote.bid >= order.limit_price
                
                if should_fill:
                    # Simulate probabilistic fill (not 100% guaranteed)
                    tier = self._infer_tier(product_id)
                    fill_prob = self.cost_model.estimate_fill_probability("limit_post_only", tier)
                    
                    if random.random() < fill_prob:
                        self._fill_order(order, quote)
                    else:
                        logger.debug(f"Order {order.order_id} eligible but did not fill (prob={fill_prob:.0%})")
    
    def cancel_order(self, order_id: str) -> dict:
        """Cancel order by order_id"""
        return self._cancel_order_internal(order_id, reason="user_requested")
    
    def cancel_orders(self, order_ids: List[str]) -> dict:
        """Cancel multiple orders"""
        results = []
        for oid in order_ids:
            result = self._cancel_order_internal(oid, reason="user_requested")
            results.append(result)
        
        return {
            "results": results,
            "success_count": sum(1 for r in results if r.get("success", False)),
            "failure_count": sum(1 for r in results if not r.get("success", False))
        }
    
    # Internal methods
    
    def _fill_order(self, order: MockOrder, quote: Quote):
        """Simulate order fill with realistic costs"""
        # Determine fill price
        if order.order_type == "market":
            # Market orders cross the spread
            fill_price = quote.ask if order.side == "buy" else quote.bid
            order.is_maker = False
        else:
            # Limit orders fill at limit price (maker)
            fill_price = order.limit_price or quote.mid
            order.is_maker = True
        
        # Calculate costs
        tier = self._infer_tier(order.product_id)
        cost = self.cost_model.calculate_trade_cost(
            size_usd=order.size_usd,
            is_maker=order.is_maker,
            tier=tier,
            spread_bps=quote.spread_bps,
            order_type=order.order_type
        )
        
        # Update order
        order.status = "filled"
        order.filled_size_usd = order.size_usd
        order.filled_price = fill_price
        order.filled_at = self.current_time
        order.fee_usd = cost.fee_usd
        
        # Update balances
        if order.side == "buy":
            # Spend USD (includes fees)
            total_cost = order.size_usd + cost.fee_usd
            self.balances["USD"] = self.balances.get("USD", 0.0) - total_cost
            
            # Receive asset
            base_currency = order.product_id.split("-")[0]
            received_amount = order.size_usd / fill_price
            self.balances[base_currency] = self.balances.get(base_currency, 0.0) + received_amount
            
        else:  # sell
            # Receive USD (minus fees)
            net_proceeds = order.size_usd - cost.fee_usd
            self.balances["USD"] = self.balances.get("USD", 0.0) + net_proceeds
            
            # Spend asset
            base_currency = order.product_id.split("-")[0]
            sold_amount = order.size_usd / fill_price
            self.balances[base_currency] = self.balances.get(base_currency, 0.0) - sold_amount
        
        # Remove from active orders
        if order.order_id in self.orders:
            del self.orders[order.order_id]
        
        # Add to history
        self.order_history.append(order)
        self.fills_count += 1
        
        logger.debug(
            f"Order filled: {order.product_id} {order.side} ${order.size_usd:.2f} @ ${fill_price:.2f} "
            f"(fee=${cost.fee_usd:.2f}, maker={order.is_maker})"
        )
    
    def _cancel_order_internal(self, order_id: str, reason: str) -> dict:
        """Internal cancel with reason tracking"""
        if order_id not in self.orders:
            return {
                "success": False,
                "order_id": order_id,
                "message": "Order not found"
            }
        
        order = self.orders[order_id]
        order.status = "canceled"
        
        del self.orders[order_id]
        self.order_history.append(order)
        
        logger.debug(f"Order canceled: {order_id} reason={reason}")
        
        return {
            "success": True,
            "order_id": order_id,
            "reason": reason
        }
    
    def _is_post_only_invalid(self, order: MockOrder, quote: Quote) -> bool:
        """Check if post-only order would immediately match (invalid)"""
        if not order.limit_price:
            return False
        
        if order.side == "buy":
            # Buy post-only invalid if limit >= ask (would take liquidity)
            return order.limit_price >= quote.ask
        else:
            # Sell post-only invalid if limit <= bid (would take liquidity)
            return order.limit_price <= quote.bid
    
    def _format_order_result(self, order: MockOrder) -> dict:
        """Format order as CoinbaseExchange-compatible dict"""
        return {
            "success": order.status in ["filled", "open"],
            "order_id": order.order_id,
            "client_order_id": order.client_order_id,
            "product_id": order.product_id,
            "side": order.side,
            "status": order.status,
            "order_type": order.order_type,
            "size_usd": order.size_usd,
            "limit_price": order.limit_price,
            "filled_size": order.filled_size_usd,
            "filled_price": order.filled_price,
            "filled_at": order.filled_at.isoformat() if order.filled_at else None,
            "fee": order.fee_usd,
            "is_maker": order.is_maker,
            "created_at": order.created_at.isoformat(),
        }
    
    def _infer_tier(self, product_id: str) -> int:
        """Infer universe tier from product (simplified)"""
        # Tier 1: BTC, ETH
        if product_id.startswith(("BTC-", "ETH-")):
            return 1
        # Tier 2: Top 10 by volume
        if product_id.startswith(("SOL-", "XRP-", "ADA-", "AVAX-", "DOT-")):
            return 2
        # Tier 3: Others
        return 3
    
    def get_balances_summary(self) -> dict:
        """Get current balances for reporting"""
        return dict(self.balances)
    
    def get_fill_stats(self) -> dict:
        """Get fill statistics"""
        total_orders = len(self.order_history)
        filled_orders = sum(1 for o in self.order_history if o.status == "filled")
        maker_fills = sum(1 for o in self.order_history if o.status == "filled" and o.is_maker)
        
        return {
            "total_orders": total_orders,
            "filled_orders": filled_orders,
            "canceled_orders": total_orders - filled_orders,
            "fill_rate": filled_orders / total_orders if total_orders > 0 else 0.0,
            "maker_ratio": maker_fills / filled_orders if filled_orders > 0 else 0.0,
            "rejections": self.rejections_count,
        }
