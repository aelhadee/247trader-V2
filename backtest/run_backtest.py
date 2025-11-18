"""
247trader-v2 Backtest: Runner

Simple backtest runner script.
Tests rules-only strategy on historical data.
"""

import sys
import json
import logging
from pathlib import Path
from datetime import datetime

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest.engine import BacktestEngine, BacktestMetrics
from backtest.data_loader import HistoricalDataLoader


def run_simple_backtest(
    start_date: str = "2024-11-01",
    end_date: str = "2024-11-10",
    initial_capital: float = 10_000.0,
    interval_minutes: int = 60,
    seed: int = None
) -> BacktestMetrics:
    """
    Run a simple backtest.
    
    Args:
        start_date: Start date (YYYY-MM-DD)
        end_date: End date (YYYY-MM-DD)
        initial_capital: Starting capital in USD
        interval_minutes: Minutes between cycles
        
    Returns:
        BacktestMetrics
    """
    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    logger = logging.getLogger(__name__)
    
    # Parse dates
    start = datetime.fromisoformat(start_date)
    end = datetime.fromisoformat(end_date)
    
    logger.info("=" * 80)
    logger.info("247TRADER-V2 BACKTEST")
    logger.info("=" * 80)
    logger.info(f"Period: {start_date} to {end_date}")
    logger.info(f"Initial Capital: ${initial_capital:,.0f}")
    logger.info(f"Interval: {interval_minutes} minutes")
    logger.info("=" * 80)
    
    # Create data loader
    data_loader = HistoricalDataLoader()
    
    # Pre-load data for major symbols
    symbols = ["BTC-USD", "ETH-USD", "SOL-USD", "DOGE-USD", "XRP-USD"]
    
    logger.info(f"Pre-loading historical data for {len(symbols)} symbols...")
    historical_data = data_loader.load(
        symbols=symbols,
        start=start,
        end=end,
        granularity=interval_minutes * 60  # Convert to seconds
    )
    
    # Create backtest engine
    engine = BacktestEngine(
        config_dir="config",
        initial_capital=initial_capital,
        seed=seed
    )
    
    # Wrapper for data_loader that uses pre-loaded data
    def data_loader_func(syms, s, e):
        return {sym: historical_data.get(sym, []) for sym in syms}
    
    # Run backtest
    logger.info("Starting backtest...")
    metrics = engine.run(
        start_date=start,
        end_date=end,
        data_loader=data_loader_func,
        interval_minutes=interval_minutes
    )
    
    # Print results
    print("\n" + "=" * 80)
    print("BACKTEST RESULTS")
    print("=" * 80)
    print(json.dumps(metrics.to_dict(), indent=2))
    print("=" * 80)
    
    # Print detailed trades
    if metrics.trades:
        print("\nTOP 10 TRADES:")
        print("-" * 80)
        sorted_trades = sorted(metrics.trades, key=lambda t: t.pnl_pct, reverse=True)
        for i, trade in enumerate(sorted_trades[:10], 1):
            print(
                f"{i:2d}. {trade.symbol:10s} | "
                f"Entry: ${trade.entry_price:8,.2f} @ {trade.entry_time.strftime('%Y-%m-%d %H:%M')} | "
                f"Exit:  ${trade.exit_price:8,.2f} @ {trade.exit_time.strftime('%Y-%m-%d %H:%M')} | "
                f"PnL: {trade.pnl_pct:+6.2f}% ({trade.exit_reason})"
            )
        print("-" * 80)
    
    # Summary
    print("\nSUMMARY:")
    print(f"  Final Capital: ${engine.capital:,.2f} (from ${initial_capital:,.0f})")
    print(f"  Total Return: {((engine.capital - initial_capital) / initial_capital * 100):+.2f}%")
    print(f"  Win Rate: {metrics.win_rate * 100:.1f}%")
    print(f"  Profit Factor: {metrics.profit_factor:.2f}" if metrics.profit_factor else "  Profit Factor: N/A")
    print(f"  Max Consecutive Losses: {metrics.max_consecutive_losses}")
    
    # Loss Analysis
    losing_trades = [t for t in metrics.trades if t.pnl_pct < 0]
    if losing_trades:
        print("\n" + "=" * 80)
        print("LOSS ANALYSIS")
        print("=" * 80)
        
        from collections import defaultdict
        
        # Exit reason breakdown
        exit_reasons = defaultdict(list)
        for trade in losing_trades:
            exit_reasons[trade.exit_reason].append(trade)
        
        print(f"\nLosing Trades: {len(losing_trades)}/{metrics.total_trades} ({len(losing_trades)/metrics.total_trades*100:.1f}%)")
        print("\nLoss Breakdown by Exit Reason:")
        for reason, trades in sorted(exit_reasons.items(), key=lambda x: len(x[1]), reverse=True):
            avg_loss = sum(t.pnl_pct for t in trades) / len(trades)
            print(f"  {reason:15s}: {len(trades):3d} trades | Avg: {avg_loss:6.2f}%")
        
        # Average loss stats
        avg_loss_pct = sum(t.pnl_pct for t in losing_trades) / len(losing_trades)
        hold_times = [t.hold_time.total_seconds() / 3600 for t in losing_trades if t.hold_time]
        avg_hold = sum(hold_times) / len(hold_times) if hold_times else 0
        
        print(f"\nAverage Loss: {avg_loss_pct:.2f}% (stop loss threshold: -8.0%)")
        print(f"Average Hold Time: {avg_hold:.1f} hours (max hold: 48h)")
        
        # Worst losses
        print("\nWorst 5 Losing Trades:")
        worst = sorted(losing_trades, key=lambda t: t.pnl_pct)[:5]
        for i, trade in enumerate(worst, 1):
            hold_hours = trade.hold_time.total_seconds() / 3600 if trade.hold_time else 0
            print(f"  {i}. {trade.symbol:10s} | {trade.pnl_pct:+6.2f}% | Hold: {hold_hours:5.1f}h | Exit: {trade.exit_reason}")
    
    # Verdict
    print("\n" + "=" * 80)
    if engine.capital > initial_capital and metrics.win_rate > 0.4 and metrics.profit_factor and metrics.profit_factor > 1.2:
        print("✅ VERDICT: Strategy shows promise (profitable with decent metrics)")
    elif engine.capital > initial_capital:
        print("⚠️  VERDICT: Strategy is profitable but needs tuning (low win rate or profit factor)")
    else:
        print("❌ VERDICT: Strategy is not profitable - needs significant tuning or redesign")
    print("=" * 80)
    
    return metrics


def main():
    """Main entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Backtest 247trader-v2 rules")
    parser.add_argument("--start", default="2024-11-01", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", default="2024-11-10", help="End date (YYYY-MM-DD)")
    parser.add_argument("--capital", type=float, default=10_000.0, help="Initial capital")
    parser.add_argument("--interval", type=int, default=60, help="Minutes between cycles")
    parser.add_argument("--seed", type=int, default=None, help="Random seed for deterministic results")
    
    args = parser.parse_args()
    
    run_simple_backtest(
        start_date=args.start,
        end_date=args.end,
        initial_capital=args.capital,
        interval_minutes=args.interval,
        seed=args.seed
    )


if __name__ == "__main__":
    main()
