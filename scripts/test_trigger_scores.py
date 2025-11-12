#!/usr/bin/env python3
"""
Quick diagnostic script to see trigger scores and why proposals are filtered.
"""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import logging
logging.basicConfig(level=logging.DEBUG, format='%(levelname)s %(name)s: %(message)s')

from core.universe import UniverseManager
from core.triggers import TriggerEngine
from strategy.rules_engine import RulesEngine

def main():
    print("=" * 80)
    print("TRIGGER SCORE DIAGNOSTIC")
    print("=" * 80)
    
    # Initialize components
    universe_mgr = UniverseManager("config/universe.yaml")
    trigger_engine = TriggerEngine()
    rules_engine = RulesEngine(config={})
    
    # Build universe
    print("\n1. Building universe...")
    universe = universe_mgr.get_universe(regime="chop")
    print(f"   ✓ {universe.total_eligible} eligible assets")
    
    # Get triggers
    print("\n2. Scanning for triggers...")
    all_assets = universe.get_all_eligible()
    triggers = trigger_engine.scan(all_assets, regime="chop")
    print(f"   ✓ {len(triggers)} triggers detected")
    
    # Show trigger scores
    print("\n3. Trigger Scores (strength × confidence):")
    print(f"{'Symbol':<12} {'Type':<15} {'Strength':<10} {'Conf':<10} {'Score':<10} {'Reason'}")
    print("-" * 90)
    
    for t in triggers[:20]:  # Show first 20
        score = t.strength * t.confidence
        print(f"{t.symbol:<12} {t.trigger_type:<15} {t.strength:<10.3f} {t.confidence:<10.3f} {score:<10.3f} {t.reason[:50]}")
    
    # Generate proposals
    print(f"\n4. Generating proposals (min_conviction={rules_engine.min_conviction_to_propose})...")
    proposals = rules_engine.propose_trades(
        universe=universe,
        triggers=triggers,
        regime="chop"
    )
    
    print(f"   ✓ {len(proposals)} proposals generated")
    
    if proposals:
        print("\n5. Approved Proposals:")
        for p in proposals:
            print(f"   • {p.side} {p.symbol} size={p.size_pct:.1f}% conf={p.confidence:.3f} - {p.reason}")
    else:
        print("\n5. No proposals met conviction threshold")
        print(f"\n   Analysis: All triggers have confidence < {rules_engine.min_conviction_to_propose}")
        print("\n   Solutions:")
        print("   a) Lower min_conviction_to_propose in policy.yaml (currently 0.45)")
        print("   b) Adjust trigger confidence scoring in triggers.py")
        print("   c) Wait for stronger market signals")

if __name__ == "__main__":
    main()
