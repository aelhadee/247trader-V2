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
        
        # Quote currency preferences
        self.preferred_quotes = execution_config.get("preferred_quote_currencies", 
                                                     ["USDC", "USD", "USDT", "BTC", "ETH"])
        
        logger.info(f"Initialized ExecutionEngine (mode={self.mode}, min_notional=${self.min_notional_usd}, quotes={self.preferred_quotes})")
    
    def adjust_proposals_to_capital(self, proposals: List, portfolio_value_usd: float) -> List[Tuple]:
        """
        Adjust trade sizes based on available capital.
        
        Strategy:
        1. Get actual available balances (USDC, USD, etc.)
        2. Scale down position sizes to fit available capital
        3. Prioritize higher-conviction trades (by confidence score)
        4. Skip trades below minimum notional
        
        Args:
            proposals: List of TradeProposal objects
            portfolio_value_usd: Total portfolio value (for reference)
            
        Returns:
            List of (proposal, adjusted_size_usd) tuples
        """
        try:
            # Get fresh balances
            accounts = self.exchange.get_accounts()
            balances = {
                acc['currency']: float(acc.get('available_balance', {}).get('value', 0))
                for acc in accounts
            }
            
            # Convert all preferred quote currencies to USD
            available_capital = 0.0
            for quote in self.preferred_quotes:
                balance = balances.get(quote, 0)
                if balance == 0:
                    continue
                
                # USD/USDC/USDT are 1:1 with USD
                if quote in ['USD', 'USDC', 'USDT']:
                    available_capital += balance
                else:
                    # Convert crypto (BTC, ETH, etc.) to USD
                    try:
                        pair = f"{quote}-USD"
                        quote_obj = self.exchange.get_quote(pair)
                        usd_value = balance * quote_obj.mid
                        available_capital += usd_value
                        logger.debug(f"Converted {balance:.6f} {quote} to ${usd_value:.2f} USD")
                    except Exception as e:
                        logger.warning(f"Could not convert {quote} to USD: {e}")
                        # Skip this balance if conversion fails
                        continue
            
            logger.info(f"Available capital: ${available_capital:.2f} across {len([q for q in self.preferred_quotes if balances.get(q, 0) > 0])} currencies")
            
            if available_capital < self.min_notional_usd:
                logger.warning(f"Insufficient capital: ${available_capital:.2f} < ${self.min_notional_usd} minimum")
                return []
            
            # Calculate total requested size
            total_requested = sum(portfolio_value_usd * (p.size_pct / 100.0) for p in proposals)
            
            # If we have enough capital, no adjustment needed
            if total_requested <= available_capital:
                logger.info(f"Sufficient capital: ${total_requested:.2f} requested, ${available_capital:.2f} available")
                return [(p, portfolio_value_usd * (p.size_pct / 100.0)) for p in proposals]
            
            # Capital constrained - need to adjust
            logger.warning(f"Capital constrained: ${total_requested:.2f} requested > ${available_capital:.2f} available")
            
            # Sort proposals by confidence (highest first)
            sorted_proposals = sorted(proposals, key=lambda p: p.confidence, reverse=True)
            
            # Allocate capital proportionally, respecting minimums
            adjusted = []
            remaining_capital = available_capital
            
            for proposal in sorted_proposals:
                if remaining_capital < self.min_notional_usd:
                    logger.debug(f"Skipping {proposal.symbol}: insufficient remaining capital (${remaining_capital:.2f})")
                    break
                
                # Calculate proportional size
                requested_size = portfolio_value_usd * (proposal.size_pct / 100.0)
                scale_factor = available_capital / total_requested
                adjusted_size = min(requested_size * scale_factor, remaining_capital)
                
                # Respect minimum notional
                if adjusted_size < self.min_notional_usd:
                    logger.debug(f"Skipping {proposal.symbol}: adjusted size ${adjusted_size:.2f} < ${self.min_notional_usd} minimum")
                    continue
                
                adjusted.append((proposal, adjusted_size))
                remaining_capital -= adjusted_size
                
                logger.info(f"Adjusted {proposal.symbol}: ${requested_size:.2f} → ${adjusted_size:.2f} (confidence={proposal.confidence:.2f})")
            
            return adjusted
            
        except Exception as e:
            logger.error(f"Error adjusting proposals to capital: {e}")
            # Fallback: use original sizes
            return [(p, portfolio_value_usd * (p.size_pct / 100.0)) for p in proposals]
    
    def get_liquidation_candidates(self, min_value_usd: float = 10.0, 
                                  sort_by: str = "performance") -> List[Dict]:
        """
        Identify holdings that could be liquidated for capital.
        
        Strategy:
        - By default, prioritize worst-performing assets (largest 24h loss)
        - Can also sort by lowest value for dust cleanup
        
        Args:
            min_value_usd: Only consider holdings worth more than this
            sort_by: "performance" (default) or "value"
            
        Returns:
            List of holdings with value and performance, sorted accordingly
        """
        try:
            accounts = self.exchange.get_accounts()
            candidates = []
            
            for acc in accounts:
                currency = acc['currency']
                balance = float(acc.get('available_balance', {}).get('value', 0))
                account_uuid = acc.get('uuid', '')
                
                # Skip if balance too low or is a quote currency we prefer
                if balance == 0 or currency in self.preferred_quotes:
                    continue
                
                # Try to get USD value and performance
                try:
                    # Try direct USD pair first
                    pair = f"{currency}-USD"
                    quote = self.exchange.get_quote(pair)
                    value_usd = balance * quote.mid
                    
                    if value_usd >= min_value_usd:
                        # Get 24h performance
                        change_24h_pct = 0.0
                        try:
                            # Calculate from 24h volume and current price
                            # Note: This is approximate - actual historical data would be better
                            change_24h_pct = ((quote.last - quote.mid) / quote.mid) * 100 if quote.mid > 0 else 0.0
                        except:
                            pass
                        
                        candidates.append({
                            'currency': currency,
                            'account_uuid': account_uuid,
                            'balance': balance,
                            'value_usd': value_usd,
                            'price': quote.mid,
                            'pair': pair,
                            'change_24h_pct': change_24h_pct
                        })
                except:
                    # Try USDC pair as fallback
                    try:
                        pair = f"{currency}-USDC"
                        quote = self.exchange.get_quote(pair)
                        value_usd = balance * quote.mid
                        
                        if value_usd >= min_value_usd:
                            # Get 24h performance
                            change_24h_pct = 0.0
                            try:
                                change_24h_pct = ((quote.last - quote.mid) / quote.mid) * 100 if quote.mid > 0 else 0.0
                            except:
                                pass
                            
                            candidates.append({
                                'currency': currency,
                                'account_uuid': account_uuid,
                                'balance': balance,
                                'value_usd': value_usd,
                                'price': quote.mid,
                                'pair': pair,
                                'change_24h_pct': change_24h_pct
                            })
                    except:
                        pass
            
            # Sort based on strategy
            if sort_by == "performance":
                # Worst performers first (most negative change)
                candidates.sort(key=lambda x: x['change_24h_pct'])
            else:
                # Lowest value first (for dust cleanup)
                candidates.sort(key=lambda x: x['value_usd'])
            
            if candidates:
                total_value = sum(c['value_usd'] for c in candidates)
                logger.info(f"Found {len(candidates)} liquidation candidates worth ${total_value:.2f} (sorted by {sort_by})")
                if candidates:
                    worst = candidates[0]
                    logger.info(f"Top candidate: {worst['currency']} (${worst['value_usd']:.2f}, {worst['change_24h_pct']:+.2f}% 24h)")
            
            return candidates
            
        except Exception as e:
            logger.error(f"Error finding liquidation candidates: {e}")
            return []
    
    def convert_asset(self, from_currency: str, to_currency: str, amount: str,
                     from_account_uuid: str, to_account_uuid: str) -> Dict:
        """
        Convert one crypto asset to another using Coinbase Convert API.
        
        Flow:
        1. Get quote for conversion
        2. Review quote (exchange rate, fees)
        3. Commit if acceptable
        
        Args:
            from_currency: Source currency (e.g., "PEPE")
            to_currency: Target currency (e.g., "USDC")
            amount: Amount in source currency
            from_account_uuid: Source account UUID
            to_account_uuid: Target account UUID
            
        Returns:
            Dict with success status and details
        """
        try:
            logger.info(f"Converting {amount} {from_currency} → {to_currency}")
            
            # Step 1: Get quote
            quote_response = self.exchange.create_convert_quote(
                from_account=from_account_uuid,
                to_account=to_account_uuid,
                amount=amount
            )
            
            if 'trade' not in quote_response:
                logger.error(f"Convert quote failed: {quote_response}")
                return {'success': False, 'error': 'Quote failed'}
            
            trade = quote_response['trade']
            trade_id = trade.get('id')
            
            # Extract key quote details
            exchange_rate = trade.get('exchange_rate', {}).get('value', 0)
            total_fee = trade.get('total_fee', {}).get('amount', {}).get('value', 0)
            
            logger.info(f"Quote received: rate={exchange_rate}, fee={total_fee}, trade_id={trade_id}")
            
            # Step 2: Auto-commit (we accept all quotes for liquidation)
            # In production, you might want to add checks here (e.g., max slippage)
            commit_response = self.exchange.commit_convert_trade(
                trade_id=trade_id,
                from_account=from_account_uuid,
                to_account=to_account_uuid
            )
            
            if 'trade' not in commit_response:
                logger.error(f"Convert commit failed: {commit_response}")
                return {'success': False, 'error': 'Commit failed', 'trade_id': trade_id}
            
            final_trade = commit_response['trade']
            status = final_trade.get('status', 'UNKNOWN')
            
            logger.info(f"✅ Conversion executed: {from_currency}→{to_currency}, status={status}")
            
            return {
                'success': True,
                'trade_id': trade_id,
                'status': status,
                'exchange_rate': exchange_rate,
                'fee': total_fee,
                'from_currency': from_currency,
                'to_currency': to_currency,
                'amount': amount
            }
            
        except Exception as e:
            logger.error(f"Error converting {from_currency} to {to_currency}: {e}")
            return {'success': False, 'error': str(e)}
    
    def _find_best_trading_pair(self, base_symbol: str, size_usd: float) -> Optional[Tuple[str, str, float]]:
        """
        Find the best trading pair based on available balance.
        
        Strategy:
        1. First try preferred quote currencies (USDC, USD, USDT, BTC, ETH)
        2. If none have sufficient balance, try ANY coin we hold
        3. This allows trading portfolio holdings against each other
        
        Args:
            base_symbol: Base asset (e.g., "HBAR", "XRP")
            size_usd: USD-equivalent size needed
            
        Returns:
            Tuple of (trading_pair, quote_currency, available_balance) or None
        """
        try:
            # Get FRESH account balances (critical - balance changes after each trade)
            accounts = self.exchange.get_accounts()
            balances = {
                acc['currency']: float(acc.get('available_balance', {}).get('value', 0))
                for acc in accounts
            }
            
            logger.info(f"Looking for trading pair: {base_symbol} with ${size_usd:.2f} needed")
            logger.info(f"Current balances: {', '.join([f'{k}={v:.2f}' for k, v in balances.items() if v > 0])}")
            
            # Track best option even if insufficient
            best_option = None
            best_balance_usd = 0
            
            # Build list of quote currencies to try:
            # 1. Preferred quotes first (USDC, USD, USDT, BTC, ETH)
            # 2. Then any coin we hold (for cross-pair trading)
            all_quote_candidates = list(self.preferred_quotes)
            
            # Add all holdings as potential quote currencies
            for currency in balances.keys():
                if currency not in all_quote_candidates and currency != base_symbol and balances[currency] > 0:
                    all_quote_candidates.append(currency)
            
            logger.debug(f"Trying {len(all_quote_candidates)} quote candidates: {', '.join(all_quote_candidates[:10])}...")
            
            # Try each quote currency
            for quote in all_quote_candidates:
                balance = balances.get(quote, 0)
                logger.debug(f"Trying quote {quote}: balance={balance:.6f}")
                if balance == 0:
                    logger.debug(f"  Skipping {quote}: zero balance")
                    continue
                
                # Convert balance to USD equivalent for comparison
                balance_usd = balance
                if quote in ['USD', 'USDC', 'USDT']:
                    # Stablecoins are 1:1 with USD
                    balance_usd = balance
                    logger.debug(f"  {quote} balance: {balance:.2f} = ${balance_usd:.2f} USD (stablecoin)")
                else:
                    # Crypto holdings need USD conversion
                    try:
                        # Try direct USD pair first
                        quote_pair = f"{quote}-USD"
                        quote_obj = self.exchange.get_quote(quote_pair)
                        balance_usd = balance * quote_obj.mid
                        logger.debug(f"  {quote} balance: {balance:.6f} * ${quote_obj.mid:.2f} = ${balance_usd:.2f} USD")
                    except:
                        # Try USDC pair as fallback
                        try:
                            quote_pair = f"{quote}-USDC"
                            quote_obj = self.exchange.get_quote(quote_pair)
                            balance_usd = balance * quote_obj.mid
                            logger.debug(f"  {quote} balance: {balance:.6f} * ${quote_obj.mid:.2f} = ${balance_usd:.2f} USDC (≈USD)")
                        except Exception as e:
                            logger.debug(f"  Could not get USD value for {quote}: {e}")
                            continue
                
                # Check if trading pair exists
                pair = f"{base_symbol}-{quote}"
                try:
                    # Try to get a quote to verify pair exists
                    self.exchange.get_quote(pair)
                    
                    # Check if we have enough balance (prefer full balance, but track best option)
                    if balance_usd >= size_usd:
                        logger.info(f"✅ Selected trading pair: {pair} (balance: {balance:.6f} {quote} = ${balance_usd:.2f})")
                        return (pair, quote, balance)
                    elif balance_usd >= self.min_notional_usd and balance_usd > best_balance_usd:
                        # Track best partial option (above minimum)
                        best_option = (pair, quote, balance, balance_usd)
                        best_balance_usd = balance_usd
                        logger.debug(f"  {pair} is viable but insufficient (${balance_usd:.2f} < ${size_usd:.2f})")
                    else:
                        logger.debug(f"  {pair} balance ${balance_usd:.2f} below minimum ${self.min_notional_usd}")
                except Exception as e:
                    logger.warning(f"Pair {pair} not available or error: {type(e).__name__}: {str(e)[:100]}")
                    continue
            
            # If no quote has sufficient balance, use the best available (if above minimum)
            if best_option:
                pair, quote, balance, balance_usd = best_option
                logger.warning(f"Using best available: {pair} with ${balance_usd:.2f} (requested ${size_usd:.2f})")
                return (pair, quote, balance)
            
            # Last resort: suggest using Convert API for cross-pair trades
            # Find the largest holding that could be converted
            largest_holding = None
            largest_value = 0
            for currency, balance in balances.items():
                if currency == base_symbol or balance == 0:
                    continue
                # Try to get USD value
                try:
                    if currency in ['USD', 'USDC', 'USDT']:
                        value_usd = balance
                    else:
                        try:
                            pair = f"{currency}-USD"
                            quote_obj = self.exchange.get_quote(pair)
                            value_usd = balance * quote_obj.mid
                        except:
                            try:
                                pair = f"{currency}-USDC"
                                quote_obj = self.exchange.get_quote(pair)
                                value_usd = balance * quote_obj.mid
                            except:
                                continue
                    
                    if value_usd > largest_value and value_usd >= size_usd:
                        largest_holding = currency
                        largest_value = value_usd
                except:
                    continue
            
            if largest_holding:
                logger.info(f"💡 Suggestion: Convert {largest_holding} (${largest_value:.2f}) to USDC, then buy {base_symbol}")
                logger.info(f"   This requires implementing two-step conversion flow (Convert API + Buy)")
            
            logger.warning(f"No suitable trading pair found for {base_symbol} with size ${size_usd:.2f}")
            logger.warning(f"Available balances: {', '.join([f'{k}={v:.2f}' for k, v in balances.items() if v > 0])}")
            return None
            
        except Exception as e:
            logger.error(f"Error finding trading pair: {e}")
            return None
    
    def preview_order(self, symbol: str, side: str, size_usd: float, skip_liquidity_checks: bool = False) -> Dict:
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

            if not skip_liquidity_checks:
                # Check spread
                if quote.spread_bps > self.max_spread_bps:
                    return {
                        "success": False,
                        "error": f"Spread {quote.spread_bps:.1f}bps exceeds max {self.max_spread_bps}bps"
                    }

                # Check orderbook depth (critical for LIVE mode)
                try:
                    orderbook = self.exchange.get_orderbook(symbol, depth_levels=20)

                    if side.upper() == "BUY":
                        depth_available_usd = orderbook.ask_depth_usd
                    else:
                        depth_available_usd = orderbook.bid_depth_usd

                    min_depth_required = size_usd * self.min_depth_multiplier
                    if depth_available_usd < min_depth_required:
                        return {
                            "success": False,
                            "error": f"Insufficient depth: ${depth_available_usd:.0f} < ${min_depth_required:.0f} required"
                        }
                    logger.debug(f"Depth check passed: ${depth_available_usd:.0f} available for ${size_usd:.0f} order")
                except Exception as e:
                    logger.warning(f"Depth check failed (continuing): {e}")
                    if self.mode == "LIVE":
                        return {
                            "success": False,
                            "error": f"Cannot verify orderbook depth: {e}"
                        }
            else:
                logger.debug(f"Skipping liquidity checks for {symbol} {side} purge/forced execution.")
            
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
                max_slippage_bps: Optional[float] = None,
                force_order_type: Optional[str] = None,
                skip_liquidity_checks: bool = False) -> ExecutionResult:
        """
        Execute a trade.
        
        Args:
            symbol: Base asset symbol or trading pair (e.g., "HBAR", "BTC-USD")
            side: "BUY" or "SELL"
            size_usd: USD-equivalent amount to trade
            client_order_id: Optional idempotency key
            max_slippage_bps: Optional slippage limit (overrides default)
            
        Returns:
            ExecutionResult with fill details
        """
        # Extract base symbol if full pair provided (e.g., "BTC-USD" -> "BTC")
        base_symbol = symbol.split('-')[0] if '-' in symbol else symbol
        
        # For BUY orders, find best trading pair based on available balance
        if side.upper() == "BUY" and self.mode in ("LIVE", "PAPER"):
            pair_info = self._find_best_trading_pair(base_symbol, size_usd)
            if pair_info:
                symbol = pair_info[0]  # Use the found trading pair
                quote_currency = pair_info[0].split('-')[1]  # Extract quote (USDC, USD, etc.)
                available_balance = pair_info[2]  # Raw balance in quote currency
                
                # Adjust size if available balance is less than requested (for stablecoins)
                if quote_currency in ['USD', 'USDC', 'USDT']:
                    available_balance_usd = available_balance
                    if available_balance_usd < size_usd:
                        logger.warning(f"Adjusting trade size: ${size_usd:.2f} → ${available_balance_usd:.2f} (limited by {quote_currency} balance)")
                        size_usd = max(self.min_notional_usd, available_balance_usd * 0.99)  # Use 99% to leave room for fees
                
                logger.info(f"Using trading pair: {symbol} with ${size_usd:.2f}")
            else:
                # No direct pair found - try two-step conversion
                logger.warning(f"No direct trading pair found for {base_symbol}")
                logger.warning(f"Two-step conversion (holdings → USDC → {base_symbol}) not yet fully automated")
                logger.warning(f"For now, please liquidate holdings manually using examples/liquidate_worst_performers.py")
                return ExecutionResult(
                    success=False,
                    order_id=None,
                    filled_size=0.0,
                    filled_price=0.0,
                    fees=0.0,
                    slippage_bps=0.0,
                    route="failed",
                    error=f"No suitable trading pair found. Need to liquidate holdings to USDC first."
                )
        elif '-' not in symbol:
            # Default to USD if no pair specified and not buying
            symbol = f"{symbol}-USD"
        
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
            return self._execute_live(symbol, side, size_usd, client_order_id, max_slippage_bps, force_order_type, skip_liquidity_checks)
        
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
                     max_slippage_bps: Optional[float],
                     force_order_type: Optional[str] = None,
                     skip_liquidity_checks: bool = False) -> ExecutionResult:
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
            preview = self.preview_order(symbol, side, size_usd, skip_liquidity_checks=skip_liquidity_checks)
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
            
            # Place order (use post-only if configured), but allow explicit override
            order_type = force_order_type or ("limit_post_only" if self.limit_post_only else "market")
            result = self.exchange.place_order(
                product_id=symbol,
                side=side.lower(),
                quote_size_usd=size_usd,
                client_order_id=client_order_id,
                order_type=order_type
            )
            
            route = f"live_{order_type}"
            
            # Log full response for debugging
            logger.info(f"Order response: {result}")
            
            # Parse result
            order_id = result.get("order_id") or result.get("success_response", {}).get("order_id")
            
            # Check if order actually succeeded
            if not order_id and not result.get("success"):
                raise ValueError(f"Order placement failed: {result.get('error', 'Unknown error')}")
            
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
            order_type = force_order_type or ("limit_post_only" if self.limit_post_only else "market")
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


def get_executor(mode: str = "DRY_RUN", policy: Optional[Dict] = None, 
                 exchange: Optional[CoinbaseExchange] = None) -> ExecutionEngine:
    """Get singleton executor instance"""
    global _executor
    if _executor is None or _executor.mode != mode.upper():
        _executor = ExecutionEngine(mode=mode, policy=policy, exchange=exchange)
    return _executor
