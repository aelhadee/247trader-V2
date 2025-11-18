#!/usr/bin/env python3
"""
Migrate existing state to add managed_position metadata for exit logic.

For existing positions without managed_position metadata, this script:
1. Extracts entry_price from positions
2. Sets default stop-loss/take-profit targets from policy
3. Populates managed_positions dict for exit evaluation
"""
import json
import yaml
from pathlib import Path
from datetime import datetime, timezone


def migrate_state():
    """Migrate state.json to include managed_position metadata."""
    state_path = Path("data/.state.json")
    policy_path = Path("config/policy.yaml")
    
    # Load current state
    with open(state_path) as f:
        state = json.load(f)
    
    # Load policy for default targets
    with open(policy_path) as f:
        policy = yaml.safe_load(f)
    
    default_stop_loss = policy.get("risk", {}).get("stop_loss_pct", 8.0)
    default_take_profit = policy.get("risk", {}).get("take_profit_pct", 15.0)
    default_max_hold = 48.0  # 48 hours default
    
    positions = state.get("positions", {})
    managed_positions = state.get("managed_positions", {})
    
    print("\n" + "="*60)
    print("MANAGED POSITION MIGRATION")
    print("="*60 + "\n")
    
    migrated_count = 0
    
    for symbol, pos_data in positions.items():
        quantity = pos_data.get("total", 0.0)
        if quantity <= 0:
            continue
        
        # Check if already has metadata
        if symbol in managed_positions and isinstance(managed_positions[symbol], dict):
            print(f"✓ {symbol:<10} Already migrated")
            continue
        
        # Extract entry data from position metadata (NOT current prices)
        entry_price = pos_data.get("entry_price")  # This is weighted average from fills
        entry_time = pos_data.get("entry_time")
        
        if not entry_price:
            print(f"⚠ {symbol:<10} No entry_price in position data, skipping")
            continue
        
        if not entry_time:
            # Use last_updated or current time
            entry_time = pos_data.get("last_updated")
            if not entry_time:
                entry_time = datetime.now(timezone.utc).isoformat()
        
        # Create managed position metadata
        managed_positions[symbol] = {
            "entry_price": float(entry_price),
            "entry_time": entry_time,
            "stop_loss_pct": default_stop_loss,
            "take_profit_pct": default_take_profit,
            "max_hold_hours": default_max_hold,
        }
        
        print(f"✓ {symbol:<10} Migrated: entry=${entry_price:.4f}, "
              f"SL={default_stop_loss}%, TP={default_take_profit}%, max_hold={default_max_hold}h")
        migrated_count += 1
    
    # Update state
    state["managed_positions"] = managed_positions
    
    # Backup original
    backup_path = state_path.with_suffix(".json.bak")
    with open(backup_path, "w") as f:
        json.dump(state, f, indent=2)
    print(f"\n✓ Backup saved to {backup_path}")
    
    # Save migrated state
    with open(state_path, "w") as f:
        json.dump(state, f, indent=2)
    
    print("\n" + "="*60)
    print(f"MIGRATION COMPLETE: {migrated_count} positions migrated")
    print("="*60 + "\n")
    print("Next steps:")
    print("1. Run bot in DRY_RUN mode: ./app_run_live.sh")
    print("2. Check logs for EXIT SIGNAL messages")
    print("3. Verify SELL proposals generated for profitable positions")
    print()


if __name__ == "__main__":
    migrate_state()
