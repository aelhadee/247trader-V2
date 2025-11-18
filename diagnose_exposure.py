#!/usr/bin/env python3
"""Diagnostic: Show what's causing the 49% exposure"""

import os
import sys
sys.path.insert(0, os.path.dirname(__file__))

from core.exchange_coinbase import CoinbaseExchange
from infra.state_store import StateStore

# Initialize
ex = CoinbaseExchange(read_only=True)
state = StateStore(state_file="data/state.db")

print("=" * 80)
print("EXPOSURE DIAGNOSTIC")
print("=" * 80)

# Get accounts
accounts = ex.get_accounts()
total_usd = 0
holdings = []

for acc in accounts:
    curr = acc['currency']
    bal = float(acc.get('available_balance', {}).get('value', 0))
    
    if bal > 0.001:
        # Get USD value
        usd_val = 0
        if curr in ['USD', 'USDC', 'USDT']:
            usd_val = bal
        else:
            try:
                pair = f"{curr}-USD"
                quote = ex.get_quote(pair)
                usd_val = bal * quote.mid
            except Exception as e:
                print(f"⚠️  Could not price {curr}: {e}")
        
        total_usd += usd_val
        holdings.append((curr, bal, usd_val))

print(f"\n📊 Account Value: ${total_usd:.2f}\n")
print("Holdings:")
for curr, bal, usd in holdings:
    pct = (usd / total_usd * 100) if total_usd > 0 else 0
    print(f"  {curr:8s}: {bal:12.6f} = ${usd:8.2f} ({pct:5.1f}%)")

# Check state
print("\n📝 State Store:")
data = state.load()
managed = data.get('managed_positions', {})
open_orders = data.get('open_orders', [])
positions = data.get('positions', {})

print(f"  Managed positions: {len(managed)}")
if managed:
    for symbol, pos in managed.items():
        print(f"    {symbol}: {pos}")

print(f"  Open orders: {len(open_orders)}")
if open_orders:
    for order in open_orders[:5]:
        print(f"    {order.get('product_id')}: {order.get('side')} {order.get('size')}")

print(f"  Positions: {len(positions)}")
if positions:
    for symbol, pos in positions.items():
        print(f"    {symbol}: {pos}")

# Calculate what the bot thinks is "at risk"
non_quote_value = sum(usd for curr, bal, usd in holdings if curr not in ['USD', 'USDC', 'USDT'])
at_risk_pct = (non_quote_value / total_usd * 100) if total_usd > 0 else 0

print("\n💰 Exposure Calculation:")
print(f"  Non-quote currency value: ${non_quote_value:.2f}")
print(f"  Exposure %: {at_risk_pct:.1f}%")
print("  Cap: 25.0%")
print(f"  Excess: ${max(0, non_quote_value - (total_usd * 0.25)):.2f}")

print("\n" + "=" * 80)
