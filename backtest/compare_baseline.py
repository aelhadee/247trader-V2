#!/usr/bin/env python3
"""
Backtest Regression Gate (REQ-BT3)

Compares current backtest results against a baseline and fails if
key metrics deviate beyond ±2% tolerance.

Usage:
    python backtest/compare_baseline.py --baseline baseline.json --current results.json
    
Exit codes:
    0 = Pass (within tolerance)
    1 = Fail (deviation exceeds ±2%)
    2 = Error (missing files, invalid JSON, etc.)
"""

import json
import sys
import argparse
from pathlib import Path
from typing import Dict, Tuple
import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


# REQ-BT3: Metrics to compare with ±2% tolerance
REGRESSION_METRICS = [
    "total_trades",
    "win_rate",
    "total_pnl_pct",
    "max_drawdown_pct",
    "profit_factor",
]

# Tolerance: ±2% as per REQ-BT3
TOLERANCE_PCT = 2.0


def load_report(path: str) -> Dict:
    """Load JSON backtest report."""
    try:
        with open(path, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        logger.error(f"File not found: {path}")
        sys.exit(2)
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in {path}: {e}")
        sys.exit(2)


def calculate_deviation_pct(baseline: float, current: float) -> float:
    """
    Calculate percentage deviation from baseline.
    
    Returns:
        Percentage deviation (positive = increase, negative = decrease)
    """
    if baseline == 0:
        # Handle zero baseline (e.g., profit_factor could be None -> 0)
        if current == 0:
            return 0.0
        else:
            # Non-zero current vs zero baseline = infinite deviation
            return 100.0 if current > 0 else -100.0
    
    return ((current - baseline) / abs(baseline)) * 100.0


def compare_metrics(baseline: Dict, current: Dict) -> Tuple[bool, Dict]:
    """
    Compare regression metrics between baseline and current.
    
    Args:
        baseline: Baseline backtest report
        current: Current backtest report
        
    Returns:
        (passed, deviations_dict)
    """
    baseline_keys = baseline.get("regression_keys", {})
    current_keys = current.get("regression_keys", {})
    
    if not baseline_keys:
        logger.error("Baseline report missing 'regression_keys' section")
        sys.exit(2)
    if not current_keys:
        logger.error("Current report missing 'regression_keys' section")
        sys.exit(2)
    
    deviations = {}
    passed = True
    
    for metric in REGRESSION_METRICS:
        baseline_val = baseline_keys.get(metric)
        current_val = current_keys.get(metric)
        
        # Handle None values (e.g., profit_factor could be None)
        if baseline_val is None:
            baseline_val = 0.0
        if current_val is None:
            current_val = 0.0
        
        deviation_pct = calculate_deviation_pct(baseline_val, current_val)
        
        within_tolerance = abs(deviation_pct) <= TOLERANCE_PCT
        
        deviations[metric] = {
            "baseline": baseline_val,
            "current": current_val,
            "deviation_pct": round(deviation_pct, 2),
            "tolerance_pct": TOLERANCE_PCT,
            "passed": within_tolerance,
        }
        
        if not within_tolerance:
            passed = False
    
    return passed, deviations


def print_comparison(deviations: Dict, passed: bool):
    """Print formatted comparison results."""
    print("\n" + "=" * 80)
    print("BACKTEST REGRESSION COMPARISON (REQ-BT3)")
    print("=" * 80)
    print(f"Tolerance: ±{TOLERANCE_PCT}%\n")
    
    for metric, data in deviations.items():
        status = "✅ PASS" if data["passed"] else "❌ FAIL"
        print(f"{metric:20s} {status}")
        print(f"  Baseline:  {data['baseline']:12.4f}")
        print(f"  Current:   {data['current']:12.4f}")
        print(f"  Deviation: {data['deviation_pct']:+12.2f}% (limit: ±{data['tolerance_pct']}%)")
        print()
    
    print("=" * 80)
    if passed:
        print("✅ REGRESSION TEST PASSED")
    else:
        print("❌ REGRESSION TEST FAILED")
        print("\nOne or more metrics deviated beyond ±2% tolerance.")
        print("Review changes that may have affected backtest results.")
    print("=" * 80 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="Compare backtest results against baseline (REQ-BT3)"
    )
    parser.add_argument(
        "--baseline",
        required=True,
        help="Path to baseline backtest JSON report"
    )
    parser.add_argument(
        "--current",
        required=True,
        help="Path to current backtest JSON report"
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=TOLERANCE_PCT,
        help=f"Tolerance percentage (default: {TOLERANCE_PCT}%)"
    )
    
    args = parser.parse_args()
    
    # Override global tolerance if specified
    global TOLERANCE_PCT
    TOLERANCE_PCT = args.tolerance
    
    # Load reports
    logger.info(f"Loading baseline: {args.baseline}")
    baseline = load_report(args.baseline)
    
    logger.info(f"Loading current: {args.current}")
    current = load_report(args.current)
    
    # Compare
    passed, deviations = compare_metrics(baseline, current)
    
    # Print results
    print_comparison(deviations, passed)
    
    # Exit with appropriate code
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
