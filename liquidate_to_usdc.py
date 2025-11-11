#!/usr/bin/env python3
"""
Quick Liquidation Script: Convert all holdings to USDC

This frees up capital for trading by converting everything to USDC.
"""

import sys
from core.exchange_coinbase import CoinbaseExchange
from core.execution import ExecutionEngine
import yaml
import logging

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

def main():
    # Load config
    with open('config/policy.yaml') as f:
        policy = yaml.safe_load(f)
    
    # Initialize (read_only=False for LIVE)
    READ_ONLY = input("\n⚠️  LIVE MODE - Type 'EXECUTE' to liquidate holdings, anything else for dry run: ").strip()
    read_only = (READ_ONLY != 'EXECUTE')
    
    exchange = CoinbaseExchange(read_only=read_only)
    executor = ExecutionEngine(exchange=exchange, policy=policy)
    
    print("\n" + "="*70)
    print("LIQUIDATION TO USDC")
    print("="*70)
    
    # Get current balances
    accounts = exchange.get_accounts()
    
    # Let user choose target currency
    print("\nAvailable accounts:")
    available_currencies = sorted([acc['currency'] for acc in accounts if float(acc.get('available_balance', {}).get('value', 0)) >= 0])
    
    print("\nPopular targets:")
    print("  1. USDC (stablecoin - recommended)")
    print("  2. USD (fiat)")
    print("  3. USDT (Tether stablecoin)")
    print("  4. BTC (Bitcoin)")
    print("  5. ETH (Ethereum)")
    print("  6. SOL (Solana)")
    
    target_currency = input("\nEnter target currency (or press Enter for USDC): ").strip().upper()
    if not target_currency:
        target_currency = 'USDC'
    
    target_account = next((a for a in accounts if a['currency'] == target_currency), None)
    
    if not target_account:
        print(f"❌ No {target_currency} account found!")
        print(f"   Available: {', '.join(available_currencies[:10])}")
        return
    
    target_uuid = target_account['uuid']
    print(f"\n✅ Target: {target_currency} account ({target_uuid[:16]}...)\n")
    
    # Filter holdings to convert
    holdings = []
    for acc in accounts:
        currency = acc['currency']
        balance = float(acc.get('available_balance', {}).get('value', 0))
        
        # Skip target currency, other stablecoins (if target is stablecoin), and very small balances
        if currency == target_currency or balance < 0.01:
            continue
        
        # If converting to stablecoin, skip other stablecoins too
        if target_currency in ['USDC', 'USD', 'USDT'] and currency in ['USDC', 'USD', 'USDT']:
            continue    if not holdings_to_sell:
        print("✅ No holdings to liquidate (or all below $1)")
        return
    
    # Display holdings
    total_value = sum(h['value_usd'] for h in holdings_to_sell)
    print(f"Found {len(holdings_to_sell)} holdings worth ${total_value:.2f}:\n")
    print(f"{'Currency':<10} {'Balance':<20} {'USD Value':<12}")
    print("-" * 50)
    
    for h in holdings_to_sell:
        print(f"{h['currency']:<10} {h['balance']:<20.8f} ${h['value_usd']:<11.2f}")
    
    print("\n" + "="*70)
    
    if read_only:
        print("\n🔒 DRY RUN - No actual conversions will be made")
        print("    To execute, run again and type 'EXECUTE' when prompted\n")
        return
    
    # Confirm
    print(f"\n⚠️  About to convert {len(holdings_to_sell)} holdings to USDC")
    confirm = input("Type 'YES' to proceed: ").strip()
    
    if confirm != 'YES':
        print("\n❌ Aborted")
        return
    
    # Execute conversions
    print(f"\n🔄 Converting {len(holdings_to_sell)} holdings to USDC...\n")
    
    successful = 0
    failed = 0
    
    for h in holdings_to_sell:
        print(f"Converting {h['currency']} (${h['value_usd']:.2f})...")
        
        result = executor.convert_asset(
            from_currency=h['currency'],
            to_currency='USDC',
            amount=str(h['balance']),
            from_account_uuid=h['account_uuid'],
            to_account_uuid=target_uuid
        )
        
        if result['success']:
            print(f"  ✅ Success - Trade ID: {result['trade_id']}")
            successful += 1
        else:
            print(f"  ❌ Failed: {result.get('error')}")
            failed += 1
    
    print("\n" + "="*70)
    print(f"Conversions complete: {successful} successful, {failed} failed")
    print(f"Check your Coinbase account for USDC balance")
    print("="*70 + "\n")


if __name__ == "__main__":
    main()
