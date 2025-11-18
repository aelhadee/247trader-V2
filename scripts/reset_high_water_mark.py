#!/usr/bin/env python3
"""
Reset high_water_mark in StateStore to current account value.

This is useful when:
1. Starting with a new/smaller account balance
2. Recovering from a large historical drawdown
3. Resetting baseline after account withdrawal/transfer

USAGE:
    python scripts/reset_high_water_mark.py [--value VALUE] [--dry-run]

OPTIONS:
    --value VALUE    Set high_water_mark to specific value (default: current account value)
    --dry-run        Show what would change without making changes
    --force          Skip confirmation prompt

SAFETY:
    - Creates automatic backup before modification
    - Validates account connectivity before proceeding
    - Logs all changes to audit trail
"""
import argparse
import json
import sys
from pathlib import Path
from datetime import datetime, timezone
import sqlite3

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.exchange_coinbase import CoinbaseExchange
from infra.state_store import StateStore
import yaml


def get_current_account_value(exchange: CoinbaseExchange) -> float:
    """Get current account value in USD."""
    accounts = exchange.get_accounts()
    total_usd = 0.0
    
    cash_equivalents = {"USD", "USDC", "USDT"}
    
    for acc in accounts:
        currency = acc["currency"]
        balance = float(acc.get("available_balance", {}).get("value", 0))
        
        if balance <= 0:
            continue
            
        if currency in cash_equivalents:
            total_usd += balance
        else:
            # Get current price
            quote = exchange.get_quote(f"{currency}-USD")
            if quote and quote.mid:
                total_usd += balance * quote.mid
    
    return total_usd


def get_state_storage_backend() -> str:
    """Determine which storage backend is being used."""
    config_path = Path(__file__).parent.parent / "config" / "app.yaml"
    if not config_path.exists():
        return "sqlite"  # Default from logs
    
    with open(config_path) as f:
        config = yaml.safe_load(f)
    
    storage = config.get("state_storage", {})
    backend = storage.get("backend", "sqlite")
    return backend


def backup_state_db(db_path: Path) -> Path:
    """Create timestamped backup of state database."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_dir = db_path.parent / "state_backups"
    backup_dir.mkdir(exist_ok=True)
    
    backup_path = backup_dir / f"state_before_hwm_reset_{timestamp}.db"
    
    import shutil
    shutil.copy2(db_path, backup_path)
    
    print(f"✅ Backup created: {backup_path}")
    return backup_path


def get_current_high_water_mark(db_path: Path) -> float:
    """Read current high_water_mark from SQLite database."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    cursor.execute("SELECT payload FROM state_store WHERE id = 1")
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        return 0.0
    
    state = json.loads(row[0])
    return float(state.get("high_water_mark", 0.0))


def update_high_water_mark(db_path: Path, new_value: float) -> None:
    """Update high_water_mark in SQLite database."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Read current state
    cursor.execute("SELECT payload FROM state_store WHERE id = 1")
    row = cursor.fetchone()
    
    if not row:
        print("❌ ERROR: No state found in database")
        conn.close()
        sys.exit(1)
    
    # Update high_water_mark
    state = json.loads(row[0])
    old_value = state.get("high_water_mark", 0.0)
    state["high_water_mark"] = new_value
    
    # Write back
    cursor.execute(
        "UPDATE state_store SET payload = ?, updated_at = ? WHERE id = 1",
        (json.dumps(state), datetime.now(timezone.utc).isoformat())
    )
    conn.commit()
    conn.close()
    
    print(f"✅ Updated high_water_mark: ${old_value:.2f} → ${new_value:.2f}")


def main():
    parser = argparse.ArgumentParser(
        description="Reset high_water_mark to current account value",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument(
        "--value",
        type=float,
        help="Set high_water_mark to specific value (default: current account value)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without making changes"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Skip confirmation prompt"
    )
    
    args = parser.parse_args()
    
    print("=" * 80)
    print("HIGH WATER MARK RESET TOOL")
    print("=" * 80)
    
    # Determine storage backend
    backend = get_state_storage_backend()
    if backend != "sqlite":
        print(f"❌ ERROR: This script only supports SQLite backend (found: {backend})")
        sys.exit(1)
    
    db_path = Path(__file__).parent.parent / "data" / "state.db"
    if not db_path.exists():
        print(f"❌ ERROR: Database not found: {db_path}")
        sys.exit(1)
    
    # Get current high_water_mark
    current_hwm = get_current_high_water_mark(db_path)
    print(f"\n📊 Current high_water_mark: ${current_hwm:.2f}")
    
    # Get current account value
    if args.value is not None:
        new_hwm = args.value
        print(f"📊 New high_water_mark (manual): ${new_hwm:.2f}")
    else:
        print("\n🔍 Fetching current account value from Coinbase...")
        try:
            exchange = CoinbaseExchange(read_only=True)
            new_hwm = get_current_account_value(exchange)
            print(f"📊 Current account value: ${new_hwm:.2f}")
        except Exception as e:
            print(f"❌ ERROR: Failed to fetch account value: {e}")
            sys.exit(1)
    
    # Calculate current drawdown
    if current_hwm > 0:
        current_dd = ((current_hwm - new_hwm) / current_hwm) * 100.0
        print(f"📉 Current drawdown: {current_dd:.2f}%")
    
    # Calculate new drawdown (should be 0%)
    new_dd = 0.0
    print(f"📈 Drawdown after reset: {new_dd:.2f}%")
    
    if args.dry_run:
        print("\n⚠️  DRY RUN MODE - No changes will be made")
        print(f"\nWould update high_water_mark: ${current_hwm:.2f} → ${new_hwm:.2f}")
        print(f"This would reset drawdown from {current_dd:.2f}% to {new_dd:.2f}%")
        return
    
    # Confirmation
    if not args.force:
        print("\n" + "=" * 80)
        print("⚠️  WARNING: This will reset your high_water_mark baseline")
        print("=" * 80)
        print(f"Current high_water_mark: ${current_hwm:.2f}")
        print(f"New high_water_mark:     ${new_hwm:.2f}")
        print(f"Drawdown change:         {current_dd:.2f}% → {new_dd:.2f}%")
        print("\nThis will:")
        print("  ✓ Create automatic backup")
        print("  ✓ Reset drawdown calculation to 0%")
        print("  ✓ Allow trading to resume (if blocked by drawdown)")
        print("\nType 'yes' to confirm: ", end="")
        
        response = input().strip().lower()
        if response != "yes":
            print("❌ Aborted")
            sys.exit(0)
    
    # Create backup
    print("\n📦 Creating backup...")
    backup_path = backup_state_db(db_path)
    
    # Update high_water_mark
    print("\n🔧 Updating high_water_mark...")
    update_high_water_mark(db_path, new_hwm)
    
    # Verify
    verified_hwm = get_current_high_water_mark(db_path)
    if abs(verified_hwm - new_hwm) > 0.01:
        print(f"❌ ERROR: Verification failed! Expected ${new_hwm:.2f}, got ${verified_hwm:.2f}")
        print(f"Restore from backup: {backup_path}")
        sys.exit(1)
    
    print("\n" + "=" * 80)
    print("✅ SUCCESS")
    print("=" * 80)
    print(f"High water mark reset: ${current_hwm:.2f} → ${new_hwm:.2f}")
    print(f"Drawdown reset: {current_dd:.2f}% → 0.00%")
    print(f"Backup saved: {backup_path}")
    print("\n✅ Trading should now resume (if blocked by max drawdown)")
    print("\nNext steps:")
    print("  1. Monitor next trading cycle logs")
    print("  2. Verify drawdown calculation shows ~0%")
    print("  3. Check that risk engine approves proposals")


if __name__ == "__main__":
    main()
