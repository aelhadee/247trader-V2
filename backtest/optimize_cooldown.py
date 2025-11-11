"""
Test different cooldown parameter combinations to find optimal settings.

Tests:
- Loss thresholds: 2, 3, 4 consecutive losses
- Cooldown durations: 30, 60, 90, 120 minutes
- Measure impact on return, max consecutive losses, total trades
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import datetime
import yaml
import logging

from backtest.engine import BacktestEngine
from backtest.data_loader import HistoricalDataLoader

logging.basicConfig(level=logging.WARNING)

def test_cooldown_params(loss_threshold: int, cooldown_minutes: int, 
                         start_date: str, end_date: str, period_name: str):
    """Test specific cooldown parameters"""
    
    # Modify policy config
    config_path = Path("config/policy.yaml")
    with open(config_path) as f:
        policy = yaml.safe_load(f)
    
    # Save original values
    original_threshold = policy['risk']['cooldown_after_loss_trades']
    original_cooldown = policy['risk']['cooldown_minutes']
    
    # Update with test values
    policy['risk']['cooldown_after_loss_trades'] = loss_threshold
    policy['risk']['cooldown_minutes'] = cooldown_minutes
    
    # Write to temp file (don't modify original)
    temp_config = Path("config/policy_test.yaml")
    with open(temp_config, 'w') as f:
        yaml.dump(policy, f)
    
    # Run backtest
    start = datetime.fromisoformat(start_date)
    end = datetime.fromisoformat(end_date)
    
    data_loader = HistoricalDataLoader()
    symbols = ["BTC-USD", "ETH-USD", "SOL-USD", "DOGE-USD", "XRP-USD"]
    
    historical_data = data_loader.load(
        symbols=symbols,
        start=start,
        end=end,
        granularity=3600
    )
    
    # Create engine with test config
    engine = BacktestEngine(config_dir="config", initial_capital=10_000.0)
    engine.policy_config = policy  # Override with test config
    
    def data_loader_func(syms, s, e):
        return {sym: historical_data.get(sym, []) for sym in syms}
    
    metrics = engine.run(
        start_date=start,
        end_date=end,
        data_loader=data_loader_func,
        interval_minutes=60
    )
    
    # Restore original config
    policy['risk']['cooldown_after_loss_trades'] = original_threshold
    policy['risk']['cooldown_minutes'] = original_cooldown
    
    return {
        'loss_threshold': loss_threshold,
        'cooldown_minutes': cooldown_minutes,
        'period': period_name,
        'return_pct': ((engine.capital - 10_000) / 10_000) * 100,
        'total_trades': metrics.total_trades,
        'win_rate': metrics.win_rate,
        'profit_factor': metrics.profit_factor,
        'max_consecutive_losses': metrics.max_consecutive_losses,
        'final_capital': engine.capital
    }


def main():
    """Run systematic cooldown parameter optimization"""
    
    # Test periods
    periods = [
        ("2024-08-01T00:00:00", "2024-10-31T23:59:59", "BULL"),
        ("2024-09-01T00:00:00", "2024-09-30T23:59:59", "BEAR"),
    ]
    
    # Parameter combinations to test
    loss_thresholds = [2, 3, 4]
    cooldown_durations = [30, 60, 90, 120]
    
    print("=" * 100)
    print("COOLDOWN PARAMETER OPTIMIZATION")
    print("=" * 100)
    print(f"\nTesting {len(loss_thresholds)} thresholds × {len(cooldown_durations)} durations × {len(periods)} periods")
    print(f"Total tests: {len(loss_thresholds) * len(cooldown_durations) * len(periods)}\n")
    
    results = []
    
    for start, end, period_name in periods:
        print(f"\n{'='*100}")
        print(f"TESTING PERIOD: {period_name}")
        print(f"{'='*100}\n")
        
        for loss_threshold in loss_thresholds:
            for cooldown_minutes in cooldown_durations:
                print(f"Testing: {loss_threshold} losses → {cooldown_minutes}min cooldown... ", end='', flush=True)
                
                try:
                    result = test_cooldown_params(
                        loss_threshold, cooldown_minutes, start, end, period_name
                    )
                    results.append(result)
                    
                    print(f"✓ Return: {result['return_pct']:+.2f}% | "
                          f"Max Losses: {result['max_consecutive_losses']} | "
                          f"Trades: {result['total_trades']}")
                    
                except Exception as e:
                    print(f"✗ Error: {e}")
                    continue
    
    # Analyze results
    print("\n" + "=" * 100)
    print("OPTIMIZATION RESULTS")
    print("=" * 100)
    
    # Group by period
    for period_name in ["BULL", "BEAR"]:
        period_results = [r for r in results if r['period'] == period_name]
        
        if not period_results:
            continue
        
        print(f"\n{period_name} PERIOD:")
        print("-" * 100)
        print(f"{'Threshold':<12} {'Cooldown':<12} {'Return':<12} {'Trades':<10} {'Win Rate':<12} {'PF':<8} {'Max Losses':<12}")
        print("-" * 100)
        
        # Sort by return descending
        sorted_results = sorted(period_results, key=lambda x: x['return_pct'], reverse=True)
        
        for r in sorted_results:
            print(f"{r['loss_threshold']:<12} {r['cooldown_minutes']:>3}min{'':<6} "
                  f"{r['return_pct']:>+6.2f}%{'':<4} {r['total_trades']:<10} "
                  f"{r['win_rate']*100:>5.1f}%{'':<5} {r['profit_factor']:>5.2f}{'':<3} "
                  f"{r['max_consecutive_losses']:<12}")
    
    # Find best overall settings
    print("\n" + "=" * 100)
    print("RECOMMENDATIONS")
    print("=" * 100)
    
    # Best by period
    for period_name in ["BULL", "BEAR"]:
        period_results = [r for r in results if r['period'] == period_name]
        if period_results:
            best = max(period_results, key=lambda x: x['return_pct'])
            print(f"\nBest for {period_name}: {best['loss_threshold']} losses, {best['cooldown_minutes']}min cooldown")
            print(f"  Return: {best['return_pct']:+.2f}% | Max Losses: {best['max_consecutive_losses']} | Trades: {best['total_trades']}")
    
    # Best average across periods
    from collections import defaultdict
    param_averages = defaultdict(list)
    
    for r in results:
        key = (r['loss_threshold'], r['cooldown_minutes'])
        param_averages[key].append(r['return_pct'])
    
    avg_returns = {k: sum(v) / len(v) for k, v in param_averages.items()}
    best_combo = max(avg_returns.items(), key=lambda x: x[1])
    
    print(f"\nBest Average Across Periods: {best_combo[0][0]} losses, {best_combo[0][1]}min cooldown")
    print(f"  Average Return: {best_combo[1]:+.2f}%")
    
    # Current baseline (3 losses, 60min)
    baseline_results = [r for r in results if r['loss_threshold'] == 3 and r['cooldown_minutes'] == 60]
    if baseline_results:
        baseline_avg = sum(r['return_pct'] for r in baseline_results) / len(baseline_results)
        improvement = best_combo[1] - baseline_avg
        print(f"\nImprovement vs Baseline (3 losses, 60min): {improvement:+.2f}%")


if __name__ == "__main__":
    main()
