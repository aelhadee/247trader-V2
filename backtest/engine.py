"""
247trader-v2 Backtest: Engine

Simple backtesting harness for rules-only strategy.
Goal: Tune parameters until profitable WITHOUT AI.

Pattern: Jesse-style backtest + Freqtrade-style metrics
"""

import json
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
import logging

from core.universe import UniverseManager, UniverseAsset
from core.triggers import TriggerEngine, TriggerSignal
from core.regime import RegimeDetector
from strategy.rules_engine import RulesEngine, TradeProposal
from core.risk import RiskEngine, PortfolioState

logger = logging.getLogger(__name__)


@dataclass
class Trade:
    """Executed trade"""
    symbol: str
    side: str
    entry_price: float
    entry_time: datetime
    size_usd: float
    
    exit_price: Optional[float] = None
    exit_time: Optional[datetime] = None
    exit_reason: Optional[str] = None  # "stop_loss" | "take_profit" | "max_hold" | "manual"
    
    pnl_usd: float = 0.0
    pnl_pct: float = 0.0
    
    # Risk parameters
    stop_loss_price: Optional[float] = None
    take_profit_price: Optional[float] = None
    max_hold_hours: Optional[int] = None
    
    @property
    def is_open(self) -> bool:
        return self.exit_price is None
    
    @property
    def hold_time(self) -> Optional[timedelta]:
        if self.exit_time:
            return self.exit_time - self.entry_time
        return None


@dataclass
class BacktestMetrics:
    """Backtest performance metrics"""
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    
    total_pnl_usd: float = 0.0
    total_pnl_pct: float = 0.0
    
    win_rate: float = 0.0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    
    max_drawdown_pct: float = 0.0
    max_consecutive_losses: int = 0
    
    sharpe_ratio: Optional[float] = None
    profit_factor: Optional[float] = None
    
    trades: List[Trade] = field(default_factory=list)
    
    def update(self, trade: Trade):
        """Update metrics with new closed trade"""
        if trade.is_open:
            return
        
        self.total_trades += 1
        self.total_pnl_usd += trade.pnl_usd
        self.total_pnl_pct += trade.pnl_pct
        
        if trade.pnl_usd > 0:
            self.winning_trades += 1
        else:
            self.losing_trades += 1
        
        self.trades.append(trade)
        
        # Recalculate derived metrics
        self._calculate_derived()
    
    def _calculate_derived(self):
        """Calculate derived metrics"""
        if self.total_trades > 0:
            self.win_rate = self.winning_trades / self.total_trades
        
        # Average win/loss
        wins = [t.pnl_pct for t in self.trades if t.pnl_pct > 0]
        losses = [t.pnl_pct for t in self.trades if t.pnl_pct < 0]
        
        self.avg_win_pct = sum(wins) / len(wins) if wins else 0.0
        self.avg_loss_pct = sum(losses) / len(losses) if losses else 0.0
        
        # Profit factor
        total_wins = sum(wins)
        total_losses = abs(sum(losses))
        if total_losses > 0:
            self.profit_factor = total_wins / total_losses
        
        # Max consecutive losses
        consecutive = 0
        max_consecutive = 0
        for trade in self.trades:
            if trade.pnl_usd < 0:
                consecutive += 1
                max_consecutive = max(max_consecutive, consecutive)
            else:
                consecutive = 0
        self.max_consecutive_losses = max_consecutive
    
    def to_dict(self) -> Dict:
        """Export to dict"""
        return {
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "win_rate": round(self.win_rate, 3),
            "total_pnl_usd": round(self.total_pnl_usd, 2),
            "total_pnl_pct": round(self.total_pnl_pct, 2),
            "avg_win_pct": round(self.avg_win_pct, 2),
            "avg_loss_pct": round(self.avg_loss_pct, 2),
            "max_drawdown_pct": round(self.max_drawdown_pct, 2),
            "max_consecutive_losses": self.max_consecutive_losses,
            "profit_factor": round(self.profit_factor, 2) if self.profit_factor else None,
        }


class BacktestEngine:
    """
    Backtest harness for rules-only strategy.
    
    Simulates trading loop with historical data.
    No AI - pure rules tuning.
    
    REQ-BT1: Deterministic with fixed seed.
    """
    
    def __init__(self, config_dir: str = "config", initial_capital: float = 10_000.0, seed: Optional[int] = None):
        self.config_dir = Path(config_dir)
        self.initial_capital = initial_capital
        self.seed = seed
        
        # REQ-BT1: Set random seed for deterministic behavior
        if seed is not None:
            import random
            random.seed(seed)
            logger.info(f"Backtest seed set to {seed} for deterministic results")
        
        # Load modules (same as live)
        import yaml
        with open(self.config_dir / "policy.yaml") as f:
            self.policy_config = yaml.safe_load(f)
        
        self.universe_mgr = UniverseManager(self.config_dir / "universe.yaml")
        self.trigger_engine = TriggerEngine()
        self.regime_detector = RegimeDetector()
        self.rules_engine = RulesEngine(config={})
        self.risk_engine = RiskEngine(self.policy_config, universe_manager=self.universe_mgr)
        
        # Backtest state
        self.capital = initial_capital
        self.open_trades: List[Trade] = []
        self.closed_trades: List[Trade] = []
        self.metrics = BacktestMetrics()
        
        # Daily tracking
        self.daily_pnl = 0.0
        self.daily_trade_count = 0
        self.last_date = None
        
        # Loss streak tracking
        self.consecutive_losses = 0
        self.last_loss_time = None
        
        logger.info(f"Initialized BacktestEngine with ${initial_capital:,.0f} capital")
    
    def run(self, 
            start_date: datetime,
            end_date: datetime,
            data_loader,  # Function that returns OHLCV data
            interval_minutes: int = 15) -> BacktestMetrics:
        """
        Run backtest over date range.
        
        Args:
            start_date: Start datetime
            end_date: End datetime
            data_loader: Function(symbols, start, end) -> dict of OHLCV data
            interval_minutes: Minutes between cycles
            
        Returns:
            BacktestMetrics
        """
        logger.info(f"Starting backtest: {start_date} to {end_date}")
        
        current_time = start_date
        cycle_count = 0
        
        while current_time <= end_date:
            # Check if new day
            current_date = current_time.date()
            if self.last_date and current_date != self.last_date:
                self._reset_daily_counters()
            self.last_date = current_date
            
            # Run cycle
            self._run_cycle(current_time, data_loader)
            
            # Advance time
            current_time += timedelta(minutes=interval_minutes)
            cycle_count += 1
            
            if cycle_count % 100 == 0:
                logger.info(
                    f"Cycle {cycle_count}: {current_time.isoformat()} | "
                    f"Open: {len(self.open_trades)} | Closed: {len(self.closed_trades)} | "
                    f"PnL: ${self.metrics.total_pnl_usd:,.2f}"
                )
        
        # Close any remaining open trades
        for trade in self.open_trades:
            self._close_trade(trade, current_time, "backtest_end", data_loader)
        
        logger.info(f"Backtest complete: {cycle_count} cycles")
        return self.metrics
    
    def _run_cycle(self, current_time: datetime, data_loader):
        """Run one backtest cycle"""
        
        # 1. Update open positions (check stops, max hold)
        self._update_open_positions(current_time, data_loader)
        
        # 2. Detect market regime from BTC
        regime = self._detect_regime(current_time, data_loader)
        
        # 3. Build universe
        universe = self.universe_mgr.get_universe(regime=regime)
        
        # 4. Scan for triggers
        # For backtest, we simulate triggers using historical data
        triggers = self._simulate_triggers(universe, current_time, data_loader, regime)
        
        # 5. Generate proposals
        proposals = self.rules_engine.propose_trades(
            universe=universe,
            triggers=triggers,
            regime=regime
        )
        
        # 6. Build portfolio state
        portfolio = self._build_portfolio_state(current_time)
        
        # 7. Risk checks
        risk_result = self.risk_engine.check_all(proposals, portfolio, regime=regime)
        
        if not risk_result.approved:
            return
        
        # 8. Execute approved trades
        for proposal in proposals:
            self._execute_proposal(proposal, current_time, data_loader)
    
    def _detect_regime(self, current_time: datetime, data_loader) -> str:
        """Detect market regime from BTC"""
        # Get BTC data for last 7 days
        lookback_start = current_time - timedelta(days=7)
        btc_data = data_loader(["BTC-USD"], lookback_start, current_time)
        btc_candles = btc_data.get("BTC-USD", [])
        
        if not btc_candles or len(btc_candles) < 24:
            logger.warning("Insufficient BTC data for regime detection, defaulting to chop")
            return "chop"
        
        regime_signal = self.regime_detector.detect(btc_candles, lookback_days=7)
        return regime_signal.regime
    
    def _simulate_triggers(self, universe, current_time: datetime, data_loader, regime: str) -> List[TriggerSignal]:
        """Simulate trigger detection with historical data"""
        from core.exchange_coinbase import OHLCV
        
        triggers = []
        
        for asset in universe.get_all_eligible():
            # Get historical candles for this asset
            # Need 7 days (168 hours) of data for trigger calculations
            lookback_start = current_time - timedelta(days=7)
            
            # Get candles from data_loader
            # data_loader returns dict[symbol] -> list[Candle]
            all_data = data_loader([asset.symbol], lookback_start, current_time)
            candles_data = all_data.get(asset.symbol, [])
            
            if not candles_data or len(candles_data) < 24:
                continue
            
            # Convert to OHLCV format expected by trigger engine
            candles = [
                OHLCV(
                    symbol=asset.symbol,
                    timestamp=c.timestamp,
                    open=c.open,
                    high=c.high,
                    low=c.low,
                    close=c.close,
                    volume=c.volume
                )
                for c in candles_data
                if c.timestamp <= current_time  # Only past data
            ]
            
            if not candles:
                continue
            
            # Check volume spike
            vol_trigger = self.trigger_engine._check_volume_spike(asset, candles)
            if vol_trigger:
                triggers.append(vol_trigger)
                logger.debug(
                    f"{asset.symbol}: VOLUME_SPIKE str={vol_trigger.strength:.2f} "
                    f"conf={vol_trigger.confidence:.2f} ratio={vol_trigger.volume_ratio:.2f}"
                )
                continue
            
            # Check breakout
            breakout_trigger = self.trigger_engine._check_breakout(asset, candles, regime)
            if breakout_trigger:
                triggers.append(breakout_trigger)
                logger.debug(
                    f"{asset.symbol}: BREAKOUT str={breakout_trigger.strength:.2f} "
                    f"conf={breakout_trigger.confidence:.2f} price_chg={breakout_trigger.price_change_pct:.2f}%"
                )
                continue
            
            # Check momentum
            momentum_trigger = self.trigger_engine._check_momentum(asset, candles, regime)
            if momentum_trigger:
                triggers.append(momentum_trigger)
                logger.debug(
                    f"{asset.symbol}: MOMENTUM str={momentum_trigger.strength:.2f} "
                    f"conf={momentum_trigger.confidence:.2f} price_chg={momentum_trigger.price_change_pct:.2f}%"
                )
                continue
        
        return triggers
    
    def _execute_proposal(self, proposal: TradeProposal, current_time: datetime, data_loader):
        """Execute a trade proposal"""
        # Get current price from data_loader
        entry_price = self._get_current_price(proposal.symbol, current_time, data_loader)
        if entry_price is None:
            logger.warning(f"Could not get price for {proposal.symbol}, skipping trade")
            return
        
        # Calculate position size in USD
        size_usd = (proposal.size_pct / 100.0) * self.capital
        
        # Create trade
        trade = Trade(
            symbol=proposal.symbol,
            side=proposal.side,
            entry_price=entry_price,
            entry_time=current_time,
            size_usd=size_usd,
            max_hold_hours=proposal.max_hold_hours
        )
        
        # Set stops
        if proposal.stop_loss_pct:
            trade.stop_loss_price = entry_price * (1 - proposal.stop_loss_pct / 100.0)
        if proposal.take_profit_pct:
            trade.take_profit_price = entry_price * (1 + proposal.take_profit_pct / 100.0)
        
        self.open_trades.append(trade)
        self.daily_trade_count += 1
        
        logger.debug(
            f"OPEN {trade.symbol} @ ${entry_price:,.2f} | "
            f"Size: ${size_usd:,.0f} | Stop: ${trade.stop_loss_price:,.2f} | "
            f"Target: ${trade.take_profit_price:,.2f}"
        )
    
    def _update_open_positions(self, current_time: datetime, data_loader):
        """Update open positions and close if stops hit or max hold exceeded"""
        to_close = []
        
        for trade in self.open_trades:
            # Get current price
            current_price = self._get_current_price(trade.symbol, current_time, data_loader)
            if current_price is None:
                continue
            
            # Check stop loss
            if trade.stop_loss_price and current_price <= trade.stop_loss_price:
                to_close.append((trade, current_time, "stop_loss"))
                continue
            
            # Check take profit
            if trade.take_profit_price and current_price >= trade.take_profit_price:
                to_close.append((trade, current_time, "take_profit"))
                continue
            
            # Progressive exit checks at key intervals
            hold_hours = (current_time - trade.entry_time).total_seconds() / 3600
            current_pnl_pct = ((current_price - trade.entry_price) / trade.entry_price) * 100
            
            # Check if we should exit early based on poor performance
            should_exit_early, exit_reason = self._check_progressive_exit(
                trade, hold_hours, current_pnl_pct, current_time, data_loader
            )
            if should_exit_early:
                to_close.append((trade, current_time, exit_reason))
                continue
            
            # Check max hold time (last resort)
            if trade.max_hold_hours and hold_hours >= trade.max_hold_hours:
                to_close.append((trade, current_time, "max_hold"))
                continue
        
        # Close trades
        for trade, exit_time, reason in to_close:
            self._close_trade(trade, exit_time, reason, data_loader)
    
    def _close_trade(self, trade: Trade, exit_time: datetime, reason: str, data_loader):
        """Close a trade"""
        exit_price = self._get_current_price(trade.symbol, exit_time, data_loader)
        if exit_price is None:
            exit_price = trade.entry_price  # Fallback
        
        trade.exit_price = exit_price
        trade.exit_time = exit_time
        trade.exit_reason = reason
        
        # Calculate PnL
        if trade.side == "BUY":
            trade.pnl_pct = ((exit_price - trade.entry_price) / trade.entry_price) * 100
        else:  # SELL (short)
            trade.pnl_pct = ((trade.entry_price - exit_price) / trade.entry_price) * 100
        
        trade.pnl_usd = (trade.pnl_pct / 100.0) * trade.size_usd
        
        # Update capital
        self.capital += trade.pnl_usd
        self.daily_pnl += trade.pnl_usd
        
        # Update loss streak
        if trade.pnl_usd < 0:
            self.consecutive_losses += 1
            self.last_loss_time = exit_time
            if self.consecutive_losses >= 3:
                logger.warning(f"Consecutive losses: {self.consecutive_losses}")
        else:
            if self.consecutive_losses > 0:
                logger.info(f"Win streak! Reset consecutive losses from {self.consecutive_losses} to 0")
            self.consecutive_losses = 0
            self.last_loss_time = None
        
        # Move to closed
        self.open_trades.remove(trade)
        self.closed_trades.append(trade)
        self.metrics.update(trade)
        
        logger.debug(
            f"CLOSE {trade.symbol} @ ${exit_price:,.2f} | "
            f"PnL: ${trade.pnl_usd:+,.2f} ({trade.pnl_pct:+.2f}%) | "
            f"Reason: {reason} | Hold: {trade.hold_time}"
        )
    
    def _check_progressive_exit(self, trade: Trade, hold_hours: float, 
                                current_pnl_pct: float, current_time: datetime,
                                data_loader) -> Tuple[bool, str]:
        """
        Check if trade should exit early based on progressive criteria.
        
        Strategy:
        - 12h mark: Exit if losing AND momentum negative
        - 24h mark: Exit if losing AND no recovery signs
        - 36h mark: Exit if marginal profit or loss with weak momentum
        
        Returns:
            (should_exit, exit_reason)
        """
        # Get recent price action (last 6 hours)
        lookback_start = current_time - timedelta(hours=6)
        all_data = data_loader([trade.symbol], lookback_start, current_time)
        candles = all_data.get(trade.symbol, [])
        
        if len(candles) < 3:  # Need minimum data
            return False, ""
        
        # Calculate momentum indicators
        prices = [c.close for c in candles[-6:]]  # Last 6 candles
        recent_change = ((prices[-1] - prices[0]) / prices[0]) * 100 if prices else 0
        
        # Volume trend (declining = bad)
        volumes = [c.volume for c in candles[-6:]]
        avg_volume_recent = sum(volumes[-3:]) / 3 if len(volumes) >= 3 else 0
        avg_volume_older = sum(volumes[:3]) / 3 if len(volumes) >= 3 else 1
        volume_declining = avg_volume_recent < (avg_volume_older * 0.7)  # 30% drop
        
        # 12-hour checkpoint: Exit only if significantly losing with strong negative momentum
        if 11.5 <= hold_hours < 12.5:
            if current_pnl_pct < -2.5 and recent_change < -3.0:
                logger.info(f"Early exit (12h): {trade.symbol} | PnL: {current_pnl_pct:.2f}% | Momentum: {recent_change:.2f}%")
                return True, "momentum_check_12h"
        
        # 24-hour checkpoint: Exit clear losers with no recovery signs
        elif 23.5 <= hold_hours < 24.5:
            # Strong exit signal: losing + negative momentum
            if current_pnl_pct < -3.0 and recent_change < -1.0:
                logger.info(f"Early exit (24h): {trade.symbol} | PnL: {current_pnl_pct:.2f}% | No recovery")
                return True, "no_recovery_24h"
            # Moderate signal: losing + volume decline (less active interest)
            if current_pnl_pct < -2.0 and volume_declining and recent_change < 0:
                logger.info(f"Early exit (24h): {trade.symbol} | PnL: {current_pnl_pct:.2f}% | Volume declining")
                return True, "volume_decline_24h"
        
        # 36-hour checkpoint: Exit stalled positions (don't let max_hold)
        elif 35.5 <= hold_hours < 36.5:
            # Exit clear losers before max_hold
            if current_pnl_pct < -2.0 and recent_change < -1.5:
                logger.info(f"Early exit (36h): {trade.symbol} | PnL: {current_pnl_pct:.2f}% | Weak momentum")
                return True, "weak_momentum_36h"
            # Exit stalled positions (no movement, let capital rotate)
            if -1.5 < current_pnl_pct < 1.5 and abs(recent_change) < 0.5 and volume_declining:
                logger.info(f"Early exit (36h): {trade.symbol} | PnL: {current_pnl_pct:.2f}% | Stalled")
                return True, "stalled_36h"
        
        return False, ""
    
    def _get_current_price(self, symbol: str, timestamp: datetime, data_loader) -> Optional[float]:
        """Get price at timestamp from data_loader"""
        # Get data from loader
        all_data = data_loader([symbol], timestamp - timedelta(hours=1), timestamp + timedelta(hours=1))
        candles = all_data.get(symbol, [])
        
        if not candles:
            return None
        
        # Find closest candle
        closest = min(candles, key=lambda c: abs((c.timestamp - timestamp).total_seconds()))
        return closest.close
    
    def _build_portfolio_state(self, current_time: datetime) -> PortfolioState:
        """Build current portfolio state"""
        open_positions = {
            trade.symbol: trade.size_usd
            for trade in self.open_trades
        }
        
        return PortfolioState(
            account_value_usd=self.capital,
            open_positions=open_positions,
            daily_pnl_pct=(self.daily_pnl / self.initial_capital) * 100,
            max_drawdown_pct=self.metrics.max_drawdown_pct,
            trades_today=self.daily_trade_count,
            trades_this_hour=0,  # TODO: Track hourly
            consecutive_losses=self.consecutive_losses,
            last_loss_time=self.last_loss_time,
            current_time=current_time
        )
    
    def _reset_daily_counters(self):
        """Reset daily counters"""
        self.daily_pnl = 0.0
        self.daily_trade_count = 0
        # Note: consecutive_losses NOT reset daily - only on wins


def main():
    """Simple backtest example"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Backtest 247trader-v2 rules")
    parser.add_argument("--start", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument("--capital", type=float, default=10_000.0, help="Initial capital")
    parser.add_argument("--interval", type=int, default=15, help="Minutes between cycles")
    
    args = parser.parse_args()
    
    # Setup logging
    logging.basicConfig(level=logging.INFO)
    
    # Parse dates
    start = datetime.fromisoformat(args.start)
    end = datetime.fromisoformat(args.end)
    
    # Create backtest engine
    engine = BacktestEngine(initial_capital=args.capital)
    
    # Mock data loader (replace with real data)
    def mock_data_loader(symbols, start, end):
        return {}
    
    # Run backtest
    metrics = engine.run(
        start_date=start,
        end_date=end,
        data_loader=mock_data_loader,
        interval_minutes=args.interval
    )
    
    # Print results
    print("\n" + "=" * 80)
    print("BACKTEST RESULTS")
    print("=" * 80)
    print(json.dumps(metrics.to_dict(), indent=2))
    print("=" * 80)


if __name__ == "__main__":
    main()
