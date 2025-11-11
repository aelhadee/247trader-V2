"""
247trader-v2 Core: Execution Engine

Order placement with preview, route selection, and idempotency.
Ported from v1 with simplified logic for rules-first strategy.
"""

import uuid
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime, timezone
import logging

from core.exchange_coinbase import CoinbaseExchange, get_exchange
from core.exceptions import CriticalDataUnavailable
from infra.state_store import StateStore

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
                 policy: Optional[Dict] = None, state_store: Optional[StateStore] = None):
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
        self.state_store = state_store
        
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
        self.preferred_quotes = execution_config.get(
            "preferred_quote_currencies",
            ["USDC", "USD", "USDT", "BTC", "ETH"]
        )
        # Optional behavior flags
        self.auto_convert_preferred_quote = execution_config.get(
            "auto_convert_preferred_quote", False
        )
        self.clamp_small_trades = execution_config.get(
            "clamp_small_trades", True
        )
        self.small_order_market_threshold_usd = float(execution_config.get(
            "small_order_market_threshold_usd", 0.0
        ))
        self.failed_order_cooldown_seconds = int(execution_config.get(
            "failed_order_cooldown_seconds", 0
        ))

        # Track last failure by symbol to avoid retry spam
        self._last_fail = {}

        logger.info(
            f"Initialized ExecutionEngine (mode={self.mode}, min_notional=${self.min_notional_usd}, "
            f"quotes={self.preferred_quotes}, auto_convert={self.auto_convert_preferred_quote}, "
            f"clamp_small_trades={self.clamp_small_trades})"
        )
    
    def _require_accounts(self, context: str) -> List[Dict]:
        try:
            return self.exchange.get_accounts()
        except CriticalDataUnavailable:
            raise
        except Exception as exc:
            raise CriticalDataUnavailable(f"accounts:{context}", exc) from exc

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
            accounts = self._require_accounts("adjust_proposals")
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
                sized = []
                for p in proposals:
                    raw_size = portfolio_value_usd * (p.size_pct / 100.0)
                    if self.clamp_small_trades and raw_size < self.min_notional_usd:
                        logger.debug(
                            f"Clamping {p.symbol} raw size ${raw_size:.2f} → ${self.min_notional_usd:.2f} (min_notional)"
                        )
                        raw_size = self.min_notional_usd
                    if raw_size < self.min_notional_usd:
                        logger.debug(f"Skipping {p.symbol}: size ${raw_size:.2f} below minimum after clamp")
                        continue
                    sized.append((p, raw_size))
                return sized
            
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
                if self.clamp_small_trades and requested_size < self.min_notional_usd:
                    logger.debug(
                        f"Clamping {proposal.symbol} requested size ${requested_size:.2f} → ${self.min_notional_usd:.2f}"
                    )
                    requested_size = self.min_notional_usd
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
            
        except CriticalDataUnavailable:
            raise
        except Exception as e:
            logger.error(f"Error adjusting proposals to capital: {e}")
            raise CriticalDataUnavailable("capital_adjustment", e) from e
    
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
            accounts = self._require_accounts("liquidation_candidates")
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
            
        except CriticalDataUnavailable:
            raise
        except Exception as e:
            logger.error(f"Error finding liquidation candidates: {e}")
            raise CriticalDataUnavailable("accounts:liquidation_candidates", e) from e
    
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
            accounts = self._require_accounts("find_best_pair")
            balances = {
                acc['currency']: float(acc.get('available_balance', {}).get('value', 0))
                for acc in accounts
            }
            
            logger.info(f"Looking for trading pair: {base_symbol} with ${size_usd:.2f} needed")
            logger.info(f"Current balances: {', '.join([f'{k}={v:.2f}' for k, v in balances.items() if v > 0])}")

            stable_currencies = {"USD", "USDC", "USDT"}
            total_stable = sum(balances.get(cur, 0.0) for cur in stable_currencies)
            
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

                    if (
                        balance_usd + 1e-6 < size_usd
                        and total_stable >= size_usd
                        and self.mode == "LIVE"
                        and self.auto_convert_preferred_quote
                    ):
                        logger.info(
                            f"Attempting to top up {quote}: need ${size_usd:.2f}, have ${balance_usd:.2f}"
                        )
                        if self._top_up_stable_quote(quote, size_usd):
                            accounts = self._require_accounts("find_best_pair_refresh")
                            balances = {
                                acc['currency']: float(acc.get('available_balance', {}).get('value', 0))
                                for acc in accounts
                            }
                            balance = balances.get(quote, 0.0)
                            balance_usd = balance
                            total_stable = sum(balances.get(cur, 0.0) for cur in stable_currencies)
                            logger.info(
                                f"Top-up complete: {quote} balance now ${balance_usd:.2f}"
                            )
                        else:
                            logger.info(
                                f"Top-up for {quote} unavailable; proceeding with ${balance_usd:.2f}"
                            )
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
            
        except CriticalDataUnavailable:
            raise
        except Exception as e:
            logger.error(f"Error finding trading pair: {e}")
            raise CriticalDataUnavailable("accounts:find_best_pair", e) from e
    
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
        
        # Cooldown: skip if this symbol recently failed
        if self.failed_order_cooldown_seconds > 0:
            now = datetime.utcnow().timestamp()
            last = self._last_fail.get(symbol.split('-')[0], 0)
            if last and (now - last) < self.failed_order_cooldown_seconds:
                return ExecutionResult(
                    success=False,
                    order_id=None,
                    filled_size=0.0,
                    filled_price=0.0,
                    fees=0.0,
                    slippage_bps=0.0,
                    route="skipped_cooldown",
                    error=f"Cooldown active for {symbol}"
                )

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
                
                # If we ended up using a non-top preferred quote and auto-convert is enabled, 
                # try to acquire the preferred quote (e.g., convert USD → USDC) and re-select pair
                top_pref = self.preferred_quotes[0] if self.preferred_quotes else quote_currency
                if (
                    self.auto_convert_preferred_quote 
                    and quote_currency != top_pref 
                    and self.mode == "LIVE"
                ):
                    try:
                        if self._ensure_preferred_quote_liquidity(required_usd=size_usd, preferred_quote=top_pref):
                            logger.info(f"Acquired {top_pref} liquidity; re-selecting pair for {base_symbol}")
                            reselect = self._find_best_trading_pair(base_symbol, size_usd)
                            if reselect and reselect[0].split('-')[1] == top_pref:
                                symbol = reselect[0]
                                quote_currency = top_pref
                                available_balance = reselect[2]
                    except Exception as e:
                        logger.warning(f"Auto-convert to {top_pref} skipped/failed: {e}")

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
        generated_client_order_id = False

        # Generate client order ID for idempotency
        if not client_order_id:
            client_order_id = str(uuid.uuid4())
            generated_client_order_id = True

        # Abort duplicate submissions when we already track an open order
        if (
            self.state_store
            and not generated_client_order_id
            and client_order_id
            and self.state_store.has_open_order(client_order_id)
        ):
            logger.warning(
                "Duplicate submission detected for client_order_id=%s; skipping execution",
                client_order_id,
            )
            return ExecutionResult(
                success=False,
                order_id=None,
                filled_size=0.0,
                filled_price=0.0,
                fees=0.0,
                slippage_bps=0.0,
                route="skipped_duplicate",
                error="duplicate_client_order",
            )

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
            # Route very small orders via market to avoid precision-limit failures
            if force_order_type:
                order_type = force_order_type
            else:
                if self.small_order_market_threshold_usd and size_usd <= self.small_order_market_threshold_usd:
                    order_type = "market"
                else:
                    order_type = "limit_post_only" if self.limit_post_only else "market"
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
            status = (
                result.get("status")
                or result.get("success_response", {}).get("status")
                or "open"
            )
            
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

            self._update_state_store_after_execution(
                symbol=symbol,
                side=side,
                size_usd=size_usd,
                client_order_id=client_order_id,
                order_id=order_id,
                status=status,
                route=route,
                result_payload=result,
                fills=fills,
                filled_size=filled_size,
                filled_price=filled_price,
                fees=fees,
            )
            
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
            # Record failure for cooldown
            try:
                base_sym = symbol.split('-')[0] if '-' in symbol else symbol
                self._last_fail[base_sym] = datetime.utcnow().timestamp()
            except Exception:
                pass
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

    def _ensure_preferred_quote_liquidity(self, required_usd: float, preferred_quote: str = "USDC") -> bool:
        """
        Ensure we have at least required_usd in the preferred quote currency by doing a quick
        market conversion from USD if available.

        Currently supports USD → USDC via buying USDC-USD.

        Returns True if preferred liquidity is available or was acquired; False otherwise.
        """
        try:
            if self.mode != "LIVE":
                return False

            return self._top_up_stable_quote(preferred_quote, required_usd)
        except CriticalDataUnavailable:
            raise
        except Exception as e:
            logger.warning(f"Auto-convert to {preferred_quote} failed: {e}")
            return False

    def _top_up_stable_quote(self, target_quote: str, required_usd: float) -> bool:
        """Attempt to ensure target stable balance meets the required USD size."""
        stable_currencies = {"USD", "USDC", "USDT"}
        if target_quote not in stable_currencies or required_usd <= 0:
            return False

        try:
            accounts = self._require_accounts(f"top_up:{target_quote}")
            balances = {
                acc['currency']: float(acc.get('available_balance', {}).get('value', 0))
                for acc in accounts
            }

            current = balances.get(target_quote, 0.0)
            if current >= required_usd:
                return True

            deficit = required_usd - current
            donors = [c for c in stable_currencies if c != target_quote and balances.get(c, 0.0) > 0]
            donors.sort(key=lambda cur: balances.get(cur, 0.0), reverse=True)

            for donor in donors:
                available = balances.get(donor, 0.0)
                if available <= 0:
                    continue

                transfer = min(available, max(deficit * 1.05, self.min_notional_usd))
                if transfer < self.min_notional_usd:
                    continue

                logger.info(
                    f"Top-up: attempting convert {donor} → {target_quote} (~${transfer:.2f})"
                )

                if not self.exchange.convert_currency(donor, target_quote, transfer):
                    logger.debug("Convert %s → %s skipped or failed", donor, target_quote)
                    continue

                try:
                    accounts = self._require_accounts(f"top_up_refresh:{target_quote}")
                    balances = {
                        acc['currency']: float(acc.get('available_balance', {}).get('value', 0))
                        for acc in accounts
                    }
                except Exception as refresh_exc:
                    logger.warning("Failed to refresh balances after convert: %s", refresh_exc)
                    return True

                current = balances.get(target_quote, 0.0)
                deficit = required_usd - current
                if current >= required_usd:
                    return True

            return balances.get(target_quote, 0.0) >= required_usd
        except CriticalDataUnavailable:
            raise
        except Exception as exc:
            logger.warning(f"Top-up for {target_quote} failed: {exc}")
            raise CriticalDataUnavailable(f"accounts:top_up:{target_quote}", exc) from exc

    # ===== State store integration =====

    @staticmethod
    def _safe_float(value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _order_key(client_order_id: Optional[str], order_id: Optional[str]) -> Optional[str]:
        if client_order_id:
            return client_order_id
        if order_id:
            return order_id
        return None

    @classmethod
    def build_state_store_order_payload(cls, order: Dict[str, Any]) -> Optional[Tuple[str, Dict[str, Any]]]:
        order_id = order.get("order_id") or order.get("id")
        client_id = order.get("client_order_id") or order.get("client_order_id_v2")
        key = cls._order_key(client_id, order_id)
        if not key:
            return None

        status = (order.get("status") or "open").lower()
        product_id = order.get("product_id") or order.get("symbol")

        quote_size = cls._safe_float(
            order.get("quote_size")
            or order.get("quote_value")
            or order.get("notional")
            or order.get("filled_value")
        )
        base_size = cls._safe_float(order.get("base_size") or order.get("size") or order.get("filled_size"))

        config = order.get("order_configuration") or {}
        if config:
            limit_conf = (
                config.get("limit_limit_gtc")
                or config.get("limit_limit_gtc_post_only")
                or config.get("limit_limit_gtd")
            )
            market_conf = config.get("market_market_ioc")
            if limit_conf:
                base_conf = cls._safe_float(limit_conf.get("base_size"))
                price_conf = cls._safe_float(limit_conf.get("limit_price"))
                if base_conf:
                    base_size = max(base_size, base_conf)
                if base_conf and price_conf:
                    quote_size = max(quote_size, base_conf * price_conf)
            elif market_conf:
                quote_conf = cls._safe_float(market_conf.get("quote_size"))
                base_conf = cls._safe_float(market_conf.get("base_size"))
                if quote_conf:
                    quote_size = max(quote_size, quote_conf)
                if base_conf:
                    base_size = max(base_size, base_conf)

        payload: Dict[str, Any] = {
            "order_id": order_id,
            "client_order_id": client_id,
            "product_id": product_id,
            "side": (order.get("side") or "").lower(),
            "status": status,
            "quote_size_usd": quote_size,
            "base_size": base_size,
            "filled_size": cls._safe_float(order.get("filled_size")),
            "filled_value": cls._safe_float(order.get("filled_value")),
        }

        created_time = order.get("created_time") or order.get("submitted_at")
        if created_time:
            payload["created_time"] = created_time

        return key, payload

    def sync_open_orders_snapshot(self, orders: List[Dict[str, Any]]) -> None:
        if not self.state_store:
            return
        try:
            snapshot: Dict[str, Dict[str, Any]] = {}
            for order in orders:
                built = self.build_state_store_order_payload(order)
                if not built:
                    continue
                key, data = built
                snapshot[key] = data
            timestamp = datetime.now(timezone.utc)
            self.state_store.sync_open_orders(snapshot, timestamp)
        except Exception as exc:
            logger.warning("Failed to sync open orders into state store: %s", exc)

    def _close_order_in_state_store(
        self,
        key: Optional[str],
        status: str,
        details: Dict[str, Any],
    ) -> None:
        if not self.state_store:
            return

        candidates = []
        if details.get("client_order_id"):
            candidates.append(details["client_order_id"])
        if key and key not in candidates:
            candidates.append(key)
        if details.get("order_id"):
            oid = details["order_id"]
            if oid not in candidates:
                candidates.append(oid)

        for candidate in candidates:
            try:
                closed, _ = self.state_store.close_order(candidate, status=status, details=details)
                if closed:
                    return
            except Exception as exc:
                logger.warning("State store close_order failed for %s: %s", candidate, exc)

    def _update_state_store_after_execution(
        self,
        *,
        symbol: str,
        side: str,
        size_usd: float,
        client_order_id: Optional[str],
        order_id: Optional[str],
        status: str,
        route: str,
        result_payload: Dict[str, Any],
        fills: List[Dict[str, Any]],
        filled_size: float,
        filled_price: float,
        fees: float,
    ) -> None:
        if not self.state_store:
            return

        try:
            key = self._order_key(client_order_id, order_id)
            if not key:
                return

            status_lower = (status or "open").lower()
            payload: Dict[str, Any] = {
                "order_id": order_id,
                "client_order_id": client_order_id,
                "product_id": symbol,
                "side": side.lower(),
                "quote_size_usd": size_usd,
                "status": status_lower,
                "route": route,
                "filled_size": filled_size,
                "filled_price": filled_price,
                "fees": fees,
                "fills": fills,
                "result_snapshot": result_payload,
            }

            terminal_statuses = {
                "done",
                "filled",
                "canceled",
                "cancelled",
                "expired",
                "rejected",
                "failed",
                "error",
            }

            if status_lower in terminal_statuses:
                self._close_order_in_state_store(key, status_lower, payload)
            else:
                self.state_store.record_open_order(key, payload)
        except Exception as exc:
            logger.warning("State store update failed after execution: %s", exc)
    
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

    # ===== Open order management =====
    def manage_open_orders(self) -> None:
        """Cancel stale open limit orders based on policy timings.

        Uses execution.cancel_after_seconds to decide when to cancel.
        """
        try:
            execution_config = self.policy.get("execution", {})
            cancel_after = int(execution_config.get("cancel_after_seconds", 60))
            if self.mode == "DRY_RUN":
                return

            open_orders = self.exchange.list_open_orders()
            self.sync_open_orders_snapshot(open_orders)
            if not open_orders:
                return

            now = datetime.utcnow().timestamp()
            to_cancel = []
            for o in open_orders:
                # Best-effort parse created_time; if unknown, skip cancel
                created = o.get("created_time") or o.get("time_in_force", {}).get("start_time")
                age = None
                if created:
                    try:
                        # Coinbase returns RFC3339; try stdlib then dateutil
                        ts = None
                        try:
                            iso = created.replace("Z", "+00:00")
                            ts = datetime.fromisoformat(iso).timestamp()
                        except Exception:
                            try:
                                from dateutil import parser as dtp  # type: ignore
                                ts = dtp.parse(created).timestamp()
                            except Exception:
                                ts = None
                        if ts is not None:
                            age = now - ts
                    except Exception:
                        age = None
                if age is not None and age >= cancel_after:
                    to_cancel.append(o.get("order_id") or o.get("id"))

            to_cancel = [oid for oid in to_cancel if oid]
            if to_cancel:
                # Prefer batch cancel, fallback to single
                try:
                    self.exchange.cancel_orders(to_cancel)
                    logger.info(f"Canceled {len(to_cancel)} stale orders (batch)")
                except Exception:
                    for oid in to_cancel:
                        self.exchange.cancel_order(oid)
                    logger.info(f"Canceled {len(to_cancel)} stale orders (single)")
                finally:
                    try:
                        remaining = self.exchange.list_open_orders()
                    except Exception as refresh_exc:
                        logger.warning("Failed to refresh open orders after cancel: %s", refresh_exc)
                    else:
                        self.sync_open_orders_snapshot(remaining)
        except Exception as e:
            logger.warning(f"manage_open_orders failed: {e}")


# Singleton instance
_executor = None


def get_executor(
    mode: str = "DRY_RUN",
    policy: Optional[Dict] = None,
    exchange: Optional[CoinbaseExchange] = None,
    state_store: Optional[StateStore] = None,
) -> ExecutionEngine:
    """Get singleton executor instance"""
    global _executor
    if _executor is None or _executor.mode != mode.upper():
        _executor = ExecutionEngine(
            mode=mode,
            policy=policy,
            exchange=exchange,
            state_store=state_store,
        )
    else:
        if exchange and _executor.exchange is not exchange:
            _executor.exchange = exchange
        if policy is not None:
            _executor.policy = policy
        if state_store and _executor.state_store is not state_store:
            _executor.state_store = state_store
    return _executor
