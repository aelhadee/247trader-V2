#!/usr/bin/env python3
"""
Check for and fix corrupted ETH-USD position from size_in_quote bug.

The bug caused the system to record 2.64 ETH instead of 0.000887 ETH
from the first live fill on 2025-11-17 23:29 UTC.

This script:
1. Checks current state for ETH-USD position
2. If size ~= 2.64, calculates correct size (2.64 / 2975.32 = 0.000887)
3. Offers to fix it automatically
"""

import json
import sys
from pathlib import Path
from decimal import Decimal

STATE_FILE = Path("data/.state.json")
BACKUP_FILE = Path("data/.state.json.before_size_in_quote_fix")

# Known buggy fill details
BUGGY_FILL_PRICE = Decimal("2975.32")
BUGGY_FILL_QUOTE = Decimal("2.6399716828")
CORRECT_BASE_SIZE = BUGGY_FILL_QUOTE / BUGGY_FILL_PRICE  # ~0.000887367


def check_eth_position():
    """Check if ETH-USD position is corrupted."""
    if not STATE_FILE.exists():
        print(f"❌ State file not found: {STATE_FILE}")
        return None
    
    with open(STATE_FILE) as f:
        state = json.load(f)
    
    # Check positions
    positions = state.get("positions", {})
    eth_position = positions.get("ETH-USD")
    
    if not eth_position:
        print("✅ No ETH-USD position found in state")
        return None
    
    size = Decimal(str(eth_position.get("size", 0)))
    entry_price = Decimal(str(eth_position.get("entry_price", 0)))
    
    print(f"\n📊 Current ETH-USD Position:")
    print(f"   Size: {size} ETH")
    print(f"   Entry price: ${entry_price}")
    print(f"   Notional: ${size * entry_price:.2f}")
    
    # Check if this looks like the buggy fill
    is_buggy = (
        abs(size - BUGGY_FILL_QUOTE) < Decimal("0.01") and  # Size matches quote amount
        abs(entry_price - BUGGY_FILL_PRICE) < Decimal("1.0")  # Price matches
    )
    
    if is_buggy:
        print(f"\n⚠️  CORRUPTION DETECTED!")
        print(f"   This position matches the buggy fill:")
        print(f"   - Size {size} matches quote amount (should be base units)")
        print(f"   - Price ${entry_price} matches the 2025-11-17 23:29 fill")
        print(f"\n   Correct size should be: {CORRECT_BASE_SIZE:.8f} ETH")
        print(f"   Correct notional: ${CORRECT_BASE_SIZE * entry_price:.2f}")
        return (size, entry_price)
    else:
        print(f"\n✅ Position looks normal (not the buggy fill)")
        return None


def fix_eth_position():
    """Fix the corrupted ETH position."""
    with open(STATE_FILE) as f:
        state = json.load(f)
    
    # Backup first
    print(f"\n💾 Creating backup: {BACKUP_FILE}")
    with open(BACKUP_FILE, 'w') as f:
        json.dump(state, f, indent=2)
    
    # Fix the position
    positions = state.get("positions", {})
    eth_position = positions.get("ETH-USD")
    
    if eth_position:
        old_size = eth_position["size"]
        eth_position["size"] = float(CORRECT_BASE_SIZE)
        
        print(f"\n✏️  Fixed ETH-USD position:")
        print(f"   Old size: {old_size} ETH")
        print(f"   New size: {eth_position['size']:.8f} ETH")
        
        # Update exposure if exists
        if "notional_usd" in eth_position:
            old_notional = eth_position["notional_usd"]
            eth_position["notional_usd"] = float(CORRECT_BASE_SIZE * Decimal(str(eth_position["entry_price"])))
            print(f"   Old notional: ${old_notional:.2f}")
            print(f"   New notional: ${eth_position['notional_usd']:.2f}")
    
    # Write fixed state
    print(f"\n💾 Writing fixed state to: {STATE_FILE}")
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2)
    
    print(f"\n✅ Position fixed!")
    print(f"   Backup saved to: {BACKUP_FILE}")
    print(f"   If something goes wrong, restore with:")
    print(f"   cp {BACKUP_FILE} {STATE_FILE}")


def main():
    print("="*70)
    print("ETH-USD Position Check (size_in_quote bug)")
    print("="*70)
    
    result = check_eth_position()
    
    if result is None:
        print("\n✅ No action needed")
        return 0
    
    size, price = result
    
    print("\n" + "="*70)
    print("FIX RECOMMENDED")
    print("="*70)
    
    response = input("\nFix this position? [y/N]: ").strip().lower()
    
    if response == 'y':
        fix_eth_position()
        print("\n" + "="*70)
        print("Verify the fix:")
        print("="*70)
        check_eth_position()
        return 0
    else:
        print("\n❌ Fix cancelled by user")
        return 1


if __name__ == "__main__":
    sys.exit(main())
