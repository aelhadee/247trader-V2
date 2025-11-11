"""
247trader-v2 Tests: Core Integration

Test that Phase 1 core skeleton works end-to-end.
"""

import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

def test_config_loading():
    """Test that configs load"""
    from runner.main_loop import TradingLoop
    
    try:
        loop = TradingLoop(config_dir="config")
        assert loop.mode in ["DRY_RUN", "PAPER", "LIVE"]
        assert loop.policy_config is not None
        assert loop.universe_config is not None
        print("✅ Config loading: PASS")
        return True
    except Exception as e:
        print(f"❌ Config loading: FAIL - {e}")
        return False


def test_universe_building():
    """Test universe manager"""
    from core.universe import UniverseManager
    
    try:
        mgr = UniverseManager(config_path="config/universe.yaml")
        snapshot = mgr.get_universe(regime="chop")
        
        assert snapshot.total_eligible > 0
        assert len(snapshot.tier_1_assets) >= 3  # At least BTC, ETH, SOL
        
        print(f"✅ Universe building: PASS - {snapshot.total_eligible} eligible assets")
        print(f"   Tier 1: {len(snapshot.tier_1_assets)} core")
        print(f"   Tier 2: {len(snapshot.tier_2_assets)} rotational")
        print(f"   Tier 3: {len(snapshot.tier_3_assets)} event-driven")
        return True
    except Exception as e:
        print(f"❌ Universe building: FAIL - {e}")
        return False


def test_trigger_scanning():
    """Test trigger engine"""
    from core.universe import UniverseManager
    from core.triggers import TriggerEngine
    
    try:
        mgr = UniverseManager(config_path="config/universe.yaml")
        snapshot = mgr.get_universe(regime="chop")
        
        engine = TriggerEngine()
        triggers = engine.scan(snapshot.get_all_eligible(), regime="chop")
        
        print(f"✅ Trigger scanning: PASS - {len(triggers)} triggers detected")
        
        if triggers:
            top = triggers[0]
            print(f"   Top: {top.symbol} ({top.trigger_type}) strength={top.strength:.2f} conf={top.confidence:.2f}")
        
        return True
    except Exception as e:
        print(f"❌ Trigger scanning: FAIL - {e}")
        return False


def test_rules_engine():
    """Test rules engine"""
    from core.universe import UniverseManager
    from core.triggers import TriggerEngine
    from strategy.rules_engine import RulesEngine
    
    try:
        mgr = UniverseManager(config_path="config/universe.yaml")
        snapshot = mgr.get_universe(regime="chop")
        
        trigger_engine = TriggerEngine()
        triggers = trigger_engine.scan(snapshot.get_all_eligible(), regime="chop")
        
        rules_engine = RulesEngine(config={})
        proposals = rules_engine.propose_trades(
            universe=snapshot,
            triggers=triggers,
            regime="chop"
        )
        
        print(f"✅ Rules engine: PASS - {len(proposals)} proposals generated")
        
        if proposals:
            top = proposals[0]
            print(f"   Top: {top.side} {top.symbol} size={top.size_pct:.1f}% conf={top.confidence:.2f}")
        
        return True
    except Exception as e:
        print(f"❌ Rules engine: FAIL - {e}")
        import traceback
        traceback.print_exc()
        return False


def test_risk_checks():
    """Test risk engine"""
    from core.risk import RiskEngine, PortfolioState
    from strategy.rules_engine import TradeProposal
    import yaml
    
    try:
        with open("config/policy.yaml") as f:
            policy = yaml.safe_load(f)
        
        risk_engine = RiskEngine(policy)
        
        # Mock proposal
        proposal = TradeProposal(
            symbol="BTC-USD",
            side="BUY",
            size_pct=3.0,
            reason="Test",
            confidence=0.8
        )
        
        # Mock portfolio
        portfolio = PortfolioState(
            account_value_usd=10_000.0,
            open_positions={},
            daily_pnl_pct=0.0,
            max_drawdown_pct=0.0,
            trades_today=0,
            trades_this_hour=0
        )
        
        result = risk_engine.check_all([proposal], portfolio, regime="chop")
        
        if result.approved:
            print("✅ Risk checks: PASS - Proposal approved")
        else:
            print(f"✅ Risk checks: PASS - Proposal rejected: {result.reason}")
        
        return True
    except Exception as e:
        print(f"❌ Risk checks: FAIL - {e}")
        import traceback
        traceback.print_exc()
        return False


def test_full_cycle():
    """Test complete trading cycle"""
    from runner.main_loop import TradingLoop
    
    try:
        loop = TradingLoop(config_dir="config")
        summary = loop.run_once()
        
        assert summary["status"] in ["NO_TRADE", "EXECUTED", "APPROVED_DRY_RUN", "NO_OPPORTUNITIES"]
        assert "universe_size" in summary
        assert "triggers_detected" in summary
        assert "proposals_generated" in summary
        
        print("✅ Full cycle: PASS")
        print(f"   Status: {summary['status']}")
        print(f"   Universe: {summary['universe_size']} assets")
        print(f"   Triggers: {summary['triggers_detected']}")
        print(f"   Proposals: {summary['proposals_generated']} → {summary['proposals_approved']} approved")
        
        if summary.get("base_trades"):
            print(f"   Top trade: {summary['base_trades'][0]}")
        
        return True
    except Exception as e:
        print(f"❌ Full cycle: FAIL - {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Run all tests"""
    print("=" * 80)
    print("247TRADER-V2 PHASE 1 TESTS")
    print("=" * 80)
    print()
    
    tests = [
        ("Config Loading", test_config_loading),
        ("Universe Building", test_universe_building),
        ("Trigger Scanning", test_trigger_scanning),
        ("Rules Engine", test_rules_engine),
        ("Risk Checks", test_risk_checks),
        ("Full Cycle", test_full_cycle),
    ]
    
    results = []
    for name, test_func in tests:
        print(f"\n{'='*80}")
        print(f"TEST: {name}")
        print('='*80)
        passed = test_func()
        results.append((name, passed))
        print()
    
    # Summary
    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)
    
    passed = sum(1 for _, p in results if p)
    total = len(results)
    
    for name, p in results:
        status = "✅ PASS" if p else "❌ FAIL"
        print(f"{status}: {name}")
    
    print()
    print(f"Total: {passed}/{total} tests passed")
    print("=" * 80)
    
    return passed == total


if __name__ == "__main__":
    import sys
    sys.exit(0 if main() else 1)
