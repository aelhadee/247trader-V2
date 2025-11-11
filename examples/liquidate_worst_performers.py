#!/usr/bin/env python3
"""
Example: Liquidate Worst Performing Assets

This script demonstrates how to:
1. Find worst-performing holdings (by 24h change)
2. Convert them to USDC using the Convert API
3. Free up capital for better opportunities
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.exchange_coinbase import CoinbaseExchange
from core.execution import ExecutionEngine
import yaml
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main():
    # Load config
    with open('config/policy.yaml') as f:
        policy = yaml.safe_load(f)
    
    # Initialize (read_only=True for dry run)
    exchange = CoinbaseExchange(read_only=True)
    executor = ExecutionEngine(exchange=exchange, policy=policy)
    
    print("\n" + "="*60)
    print("WORST PERFORMER LIQUIDATION TOOL")
    print("="*60 + "\n")
    
    # Step 1: Get liquidation candidates sorted by performance
    print("Step 1: Finding worst-performing holdings...")
    candidates = executor.get_liquidation_candidates(
        min_value_usd=10.0,  # Only consider holdings > $10
        sort_by="performance"  # Worst performers first
    )
    
    if not candidates:
        print("✅ No liquidation candidates found (all positions performing well)")
        return
    
    print(f"\nFound {len(candidates)} candidates:\n")
    print(f"{'Currency':<10} {'Value':<12} {'24h Change':<12} {'Balance':<15}")
    print("-" * 60)
    
    for c in candidates[:10]:  # Show top 10 worst
        print(f"{c['currency']:<10} ${c['value_usd']:<11.2f} {c['change_24h_pct']:>+10.2f}% {c['balance']:<15.8f}")
    
    print("\n" + "="*60)
    
    # Step 2: Choose target currency for conversion
    target_currency = "USDC"  # Could also be USD, BTC, etc.
    
    # Get target account UUID
    accounts = exchange.get_accounts()
    target_account = next((a for a in accounts if a['currency'] == target_currency), None)
    
    if not target_account:
        print(f"❌ No {target_currency} account found")
        return
    
    target_uuid = target_account['uuid']
    
    # Step 3: Demonstrate conversion for worst performer
    worst = candidates[0]
    
    print(f"\nWorst performer: {worst['currency']} ({worst['change_24h_pct']:+.2f}% 24h)")
    print(f"Current value: ${worst['value_usd']:.2f}")
    print(f"Balance: {worst['balance']:.8f} {worst['currency']}")
    print(f"\nConverting to {target_currency}...")
    
    # Convert
    result = executor.convert_asset(
        from_currency=worst['currency'],
        to_currency=target_currency,
        amount=str(worst['balance']),
        from_account_uuid=worst['account_uuid'],
        to_account_uuid=target_uuid
    )
    
    if result['success']:
        print(f"\n✅ Conversion successful!")
        print(f"   Trade ID: {result['trade_id']}")
        print(f"   Exchange Rate: {result['exchange_rate']}")
        print(f"   Fee: {result['fee']}")
        print(f"   Status: {result['status']}")
    else:
        print(f"\n❌ Conversion failed: {result.get('error')}")
    
    print("\n" + "="*60)
    print("\nNote: Set read_only=False in code to execute real conversions")
    print("="*60 + "\n")


if __name__ == "__main__":
    main()
