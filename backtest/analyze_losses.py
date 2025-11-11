"""
Analyze losing trades to identify patterns and improve exit timing.

This script runs backtests and analyzes:
- Why trades lose (stopped out vs max hold)
- Entry conditions that lead to losses
- Time-to-loss patterns
- Regime-specific loss patterns
"""

import json
from datetime import datetime
from pathlib import Path
from collections import defaultdict
import logging

from backtest.data_loader import HistoricalDataLoader
from backtest.engine import BacktestEngine

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)


def run_analysis_backtest(start_date: str, end_date: str, period_name: str):
    """Run backtest and collect detailed loss analysis"""
    
    start = datetime.fromisoformat(start_date)
    end = datetime.fromisoformat(end_date)
    
    # Initialize engine and data loader
    engine = BacktestEngine(initial_capital=10_000.0)
    data_loader = HistoricalDataLoader()
    
    # Run backtest
    print(f"\n{'='*80}")
    print(f"Analyzing {period_name}: {start_date} to {end_date}")
    print(f"{'='*80}\n")
    
    metrics = engine.run(
        start_date=start,
        end_date=end,
        data_loader=data_loader.load,
        interval_minutes=60
    )
    
    # Analyze losses
    losing_trades = [t for t in metrics.trades if t.pnl_pct < 0]
    
    print(f"\n{period_name} LOSS ANALYSIS:")
    print(f"{'-'*80}")
    print(f"Total Trades: {metrics.total_trades}")
    print(f"Losing Trades: {len(losing_trades)} ({len(losing_trades)/metrics.total_trades*100:.1f}%)")
    print(f"Max Consecutive Losses: {metrics.max_consecutive_losses}")
    
    if not losing_trades:
        print("No losing trades to analyze!")
        return
    
    # Group by exit reason
    exit_reasons = defaultdict(list)
    for trade in losing_trades:
        exit_reasons[trade.exit_reason].append(trade)
    
    print(f"\nLoss Breakdown by Exit Reason:")
    for reason, trades in sorted(exit_reasons.items(), key=lambda x: len(x[1]), reverse=True):
        avg_loss = sum(t.pnl_pct for t in trades) / len(trades)
        print(f"  {reason:15s}: {len(trades):3d} trades | Avg Loss: {avg_loss:6.2f}%")
    
    # Average loss magnitude
    avg_loss_pct = sum(t.pnl_pct for t in losing_trades) / len(losing_trades)
    print(f"\nAverage Loss: {avg_loss_pct:.2f}%")
    
    # Hold time analysis
    hold_times = [t.hold_time.total_seconds() / 3600 for t in losing_trades if t.hold_time]
    if hold_times:
        avg_hold = sum(hold_times) / len(hold_times)
        print(f"Average Hold Time for Losses: {avg_hold:.1f} hours")
    
    # Asset frequency
    asset_losses = defaultdict(int)
    for trade in losing_trades:
        asset_losses[trade.symbol] += 1
    
    print(f"\nMost Frequently Lost Assets:")
    for symbol, count in sorted(asset_losses.items(), key=lambda x: x[1], reverse=True)[:5]:
        print(f"  {symbol:12s}: {count} losses")
    
    # Detailed loss examples
    print(f"\nWorst 5 Losing Trades:")
    worst_losses = sorted(losing_trades, key=lambda t: t.pnl_pct)[:5]
    for i, trade in enumerate(worst_losses, 1):
        hold_hours = trade.hold_time.total_seconds() / 3600 if trade.hold_time else 0
        print(f"  {i}. {trade.symbol:12s} | Entry: ${trade.entry_price:10.2f} @ {trade.entry_time.strftime('%Y-%m-%d %H:%M')}")
        print(f"     Exit:  ${trade.exit_price:10.2f} @ {trade.exit_time.strftime('%Y-%m-%d %H:%M')} | "
              f"PnL: {trade.pnl_pct:6.2f}% | Hold: {hold_hours:.1f}h | Reason: {trade.exit_reason}")
    
    return metrics, losing_trades


def main():
    """Run analysis across all test periods"""
    
    periods = [
        ("2024-08-01T00:00:00", "2024-10-31T23:59:59", "BULL (Aug-Oct 2024)"),
        ("2024-09-01T00:00:00", "2024-09-30T23:59:59", "BEAR (Sep 2024)"),
        ("2024-11-01T00:00:00", "2024-11-10T23:59:59", "CHOP (Nov 2024)"),
    ]
    
    all_results = []
    
    for start, end, name in periods:
        try:
            metrics, losses = run_analysis_backtest(start, end, name)
            all_results.append({
                "period": name,
                "metrics": metrics,
                "losses": losses
            })
        except Exception as e:
            print(f"Error analyzing {name}: {e}")
            continue
    
    # Cross-period summary
    print(f"\n{'='*80}")
    print("CROSS-PERIOD LOSS ANALYSIS")
    print(f"{'='*80}\n")
    
    print("Max Consecutive Losses by Period:")
    for result in all_results:
        print(f"  {result['period']:25s}: {result['metrics'].max_consecutive_losses}")
    
    print("\nRecommendations:")
    print("-" * 80)
    
    # Analyze all losses across periods
    all_losses = []
    for result in all_results:
        all_losses.extend(result['losses'])
    
    if all_losses:
        # Most common exit reason for losses
        exit_reason_counts = defaultdict(int)
        for trade in all_losses:
            exit_reason_counts[trade.exit_reason] += 1
        
        most_common = max(exit_reason_counts.items(), key=lambda x: x[1])
        print(f"1. Most losses from: {most_common[0]} ({most_common[1]}/{len(all_losses)} trades)")
        
        # Average loss size
        avg_loss = sum(t.pnl_pct for t in all_losses) / len(all_losses)
        print(f"2. Average loss magnitude: {avg_loss:.2f}% (current stop: -8%)")
        
        # Hold time before loss
        loss_holds = [t.hold_time.total_seconds() / 3600 for t in all_losses if t.hold_time]
        if loss_holds:
            avg_hold = sum(loss_holds) / len(loss_holds)
            print(f"3. Average time to loss: {avg_hold:.1f} hours (current max_hold: 48h)")
    
    print("\nPotential Improvements:")
    print("-" * 80)
    print("• Consider tightening stop loss if average loss < -8%")
    print("• Consider shortening max_hold if most losses hit max_hold")
    print("• Consider stricter entry filters for frequently-losing assets")
    print("• Review trigger quality for assets with high loss frequency")


if __name__ == "__main__":
    main()
