#!/usr/bin/env python3
"""
Test dynamic universe discovery
"""

import sys
sys.path.insert(0, '.')

from core.universe import UniverseManager
import yaml

# Load config and temporarily change to dynamic
config_path = 'config/universe.yaml'

with open(config_path, 'r') as f:
    config = yaml.safe_load(f)

# Save original method
original_method = config['universe']['method']

# Test with dynamic discovery
print("=" * 70)
print("DYNAMIC UNIVERSE DISCOVERY TEST")
print("=" * 70)
print()

config['universe']['method'] = 'dynamic_discovery'

# Write temp config
with open(config_path, 'w') as f:
    yaml.dump(config, f)

try:
    # Initialize manager (will trigger dynamic discovery)
    manager = UniverseManager(config_path)
    
    # Get universe
    universe = manager.get_universe(regime='chop')
    
    print(f"Total eligible assets: {universe.total_eligible}")
    print()
    
    print(f"Tier 1 (Core): {len(universe.tier_1_assets)} assets")
    for asset in universe.tier_1_assets:
        print(f"  - {asset.symbol:15} Vol: ${asset.volume_24h:>12,.0f}  Spread: {asset.spread_bps:.1f}bps")
    
    print()
    print(f"Tier 2 (Rotational): {len(universe.tier_2_assets)} assets")
    for asset in universe.tier_2_assets[:10]:  # Show first 10
        print(f"  - {asset.symbol:15} Vol: ${asset.volume_24h:>12,.0f}  Spread: {asset.spread_bps:.1f}bps")
    
    if len(universe.tier_2_assets) > 10:
        print(f"  ... and {len(universe.tier_2_assets) - 10} more")
    
    print()
    print(f"Tier 3 (Event-driven): {len(universe.tier_3_assets)} assets")
    for asset in universe.tier_3_assets[:5]:  # Show first 5
        print(f"  - {asset.symbol:15} Vol: ${asset.volume_24h:>12,.0f}  Spread: {asset.spread_bps:.1f}bps")
    
finally:
    # Restore original method
    config['universe']['method'] = original_method
    with open(config_path, 'w') as f:
        yaml.dump(config, f)
    
    print()
    print("=" * 70)
    print(f"Config restored to method: {original_method}")
    print("=" * 70)
