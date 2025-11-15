#!/usr/bin/env python3
"""
Quick portfolio check - shows all non-zero balances
"""
import sys
import yaml
from pathlib import Path

# Add project to path
sys.path.insert(0, str(Path(__file__).parent))

from core.exchange_coinbase import CoinbaseExchange

def main():
    # Load config (uses same credential path as bot)
    with open('config/app.yaml') as f:
        config = yaml.safe_load(f)
    
    print("\n" + "="*70)
    print("COINBASE PORTFOLIO CHECK")
    print("="*70)
    print()
    
    # Initialize exchange (read-only, safe)
    exchange = CoinbaseExchange(config['exchange'])
    
    # Get accounts
    accounts = exchange.get_accounts()
    
    positions = []
    total_usd_value = 0
    
    for acc in accounts:
        currency = acc.get('currency', 'UNKNOWN')
        available = float(acc.get('available_balance', {}).get('value', 0))
        
        if available > 0.00001:  # Filter dust
            # Get USD value
            if currency in ['USD', 'USDC', 'USDT']:
                usd_value = available
            else:
                try:
                    quote = exchange.get_quote(f'{currency}-USD')
                    usd_value = available * quote.price
                except Exception as e:
                    print(f"⚠️  Could not price {currency}: {e}")
                    usd_value = 0
            
            positions.append({
                'currency': currency,
                'amount': available,
                'usd_value': usd_value
            })
            total_usd_value += usd_value
    
    # Display positions
    if not positions:
        print("❌ No positions found")
        return
    
    print(f"{'Currency':<12} {'Amount':>20} {'USD Value':>15}")
    print("-" * 70)
    
    # Sort by USD value (descending)
    for pos in sorted(positions, key=lambda x: x['usd_value'], reverse=True):
        print(f"{pos['currency']:<12} {pos['amount']:>20.8f} ${pos['usd_value']:>14.2f}")
    
    print("=" * 70)
    print(f"{'TOTAL':<12} {'':<20} ${total_usd_value:>14.2f}")
    print()
    
    # Analysis
    usd_equivalent = sum(p['usd_value'] for p in positions if p['currency'] in ['USD', 'USDC', 'USDT'])
    crypto_value = sum(p['usd_value'] for p in positions if p['currency'] not in ['USD', 'USDC', 'USDT'])
    
    print("Portfolio Breakdown:")
    print(f"  • USD-equivalents (USD/USDC/USDT): ${usd_equivalent:.2f} ({usd_equivalent/total_usd_value*100:.1f}%)")
    print(f"  • Crypto holdings:                   ${crypto_value:.2f} ({crypto_value/total_usd_value*100:.1f}%)")
    print()
    
    # Recommendation
    if crypto_value >= 1.0:
        print(f"💡 You could liquidate ${crypto_value:.2f} in crypto to increase trading capital")
        print(f"   This would give you ${usd_equivalent + crypto_value:.2f} total for LIVE deployment")
        print()
        print("   To liquidate: python liquidate_to_usdc.py")
    else:
        print(f"✅ Portfolio is already {usd_equivalent/total_usd_value*100:.0f}% in USD-equivalents")
        if usd_equivalent >= 100:
            print(f"   ${usd_equivalent:.2f} is sufficient for LIVE deployment")
        elif usd_equivalent >= 45:
            print(f"   ${usd_equivalent:.2f} is acceptable for ultra-conservative LIVE deployment")
            print("   Consider adding $55+ for more comfortable trading room")
        else:
            print(f"   ⚠️  ${usd_equivalent:.2f} is quite low - consider adding more capital")
    print()

if __name__ == '__main__':
    main()
