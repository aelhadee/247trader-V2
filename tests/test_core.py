"""
247trader-v2 Tests: Core Integration

Test that Phase 1 core skeleton works end-to-end.
"""

import sys
from pathlib import Path
import pytest

# Ensure project root is importable when running via pytest
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

@pytest.fixture(autouse=True)
def reset_metrics():
    """Reset Prometheus metrics between tests to avoid registry conflicts"""
    from infra.metrics import MetricsRecorder
    # Clean up BEFORE test (in case previous test didn't have fixture)
    MetricsRecorder._reset_for_testing()
    yield
    # Clean up AFTER test
    MetricsRecorder._reset_for_testing()

def test_config_loading():
    """Test that configs load"""
    from runner.main_loop import TradingLoop
    
    # Force DRY_RUN mode to avoid credential requirements in tests
    loop = TradingLoop(config_dir="config", mode_override="DRY_RUN")
    assert loop.mode in ["DRY_RUN", "PAPER", "LIVE"], f"Invalid mode: {loop.mode}"
    assert loop.policy_config is not None, "Policy config not loaded"
    assert loop.universe_config is not None, "Universe config not loaded"
    print("✅ Config loading: PASS")


def test_universe_building():
    """Test universe manager"""
    from core.universe import UniverseManager
    
    # Use classmethod for backward compatibility with config_path API
    mgr = UniverseManager.from_config_path("config/universe.yaml")
    snapshot = mgr.get_universe(regime="chop")
    
    assert snapshot.total_eligible > 0, "No eligible assets found"
    # Relaxed: allow fallback to work (offline mode may only have 3 core assets)
    assert len(snapshot.tier_1_assets) >= 1, "No tier 1 assets found"
    
    print(f"✅ Universe building: PASS - {snapshot.total_eligible} eligible assets")
    print(f"   Tier 1: {len(snapshot.tier_1_assets)} core")
    print(f"   Tier 2: {len(snapshot.tier_2_assets)} rotational")
    print(f"   Tier 3: {len(snapshot.tier_3_assets)} event-driven")


def test_trigger_scanning():
    """Test trigger engine"""
    from core.universe import UniverseManager
    from core.triggers import TriggerEngine
    
    # Use classmethod for backward compatibility with config_path API
    mgr = UniverseManager.from_config_path("config/universe.yaml")
    snapshot = mgr.get_universe(regime="chop")
    
    engine = TriggerEngine()
    triggers = engine.scan(snapshot.get_all_eligible(), regime="chop")
    
    # Triggers may be 0 if market is quiet or offline - that's okay
    assert triggers is not None, "Trigger scan returned None"
    assert isinstance(triggers, list), "Triggers should be a list"
    
    print(f"✅ Trigger scanning: PASS - {len(triggers)} triggers detected")
    
    if triggers:
        top = triggers[0]
        print(f"   Top: {top.symbol} ({top.trigger_type}) strength={top.strength:.2f} conf={top.confidence:.2f}")


def test_rules_engine():
    """Test rules engine"""
    from core.universe import UniverseManager
    from core.triggers import TriggerEngine
    from strategy.rules_engine import RulesEngine
    
    # Use classmethod for backward compatibility with config_path API
    mgr = UniverseManager.from_config_path("config/universe.yaml")
    snapshot = mgr.get_universe(regime="chop")
    
    trigger_engine = TriggerEngine()
    triggers = trigger_engine.scan(snapshot.get_all_eligible(), regime="chop")
    
    rules_engine = RulesEngine(config={})
    proposals = rules_engine.propose_trades(
        universe=snapshot,
        triggers=triggers,
        regime="chop"
    )
    
    # Proposals may be 0 if no triggers - that's okay
    assert proposals is not None, "Proposals returned None"
    assert isinstance(proposals, list), "Proposals should be a list"
    
    print(f"✅ Rules engine: PASS - {len(proposals)} proposals generated")
    
    if proposals:
        top = proposals[0]
        print(f"   Top: {top.side} {top.symbol} size={top.size_pct:.1f}% conf={top.confidence:.2f}")


def test_risk_checks():
    """Test risk engine"""
    from core.risk import RiskEngine, PortfolioState
    from strategy.rules_engine import TradeProposal
    import yaml
    
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
    
    assert result is not None, "Risk check returned None"
    assert hasattr(result, 'approved'), "Risk result missing 'approved' field"
    assert hasattr(result, 'approved_proposals'), "Risk result missing 'approved_proposals' field"
    
    if result.approved:
        print("✅ Risk checks: PASS - Proposal approved")
        assert len(result.approved_proposals) > 0, "Approved but no proposals in list"
    else:
        print(f"✅ Risk checks: PASS - Proposal rejected: {result.reason}")


def test_full_cycle():
    """Test complete trading cycle"""
    from runner.main_loop import TradingLoop
    
    # Clean up any stale test lock file
    import os
    test_lock = "data/247trader-v2.pid"
    if os.path.exists(test_lock):
        try:
            os.remove(test_lock)
        except:
            pass
    
    # Force DRY_RUN mode to avoid credential requirements in tests
    loop = TradingLoop(config_dir="config", mode_override="DRY_RUN")
    try:
        loop.run_cycle()  # Now returns None, logs to audit trail
    finally:
        # Release lock after test
        if hasattr(loop, 'instance_lock') and loop.instance_lock:
            loop.instance_lock.release()
    
    # Check audit log was created
    assert loop.audit.audit_file.exists(), "Audit log file not created"
    
    # Check that we can read recent cycles
    recent = loop.audit.get_recent_cycles(n=1)
    assert len(recent) > 0, "No cycles in audit log"
    
    # Verify cycle structure
    cycle = recent[0]
    assert "status" in cycle, "Cycle missing 'status' field"
    assert cycle["status"] in ["NO_TRADE", "EXECUTED", "NO_OPPORTUNITIES"], \
        f"Invalid status: {cycle['status']}"
    assert "proposals" in cycle, "Cycle missing 'proposals'"
    
    print("✅ Full cycle: PASS")
    print(f"   Status: {cycle['status']}")
    print(f"   Proposals: {cycle['proposals']}")
    
    return True


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
        try:
            passed = test_func()
            results.append((name, True))
        except AssertionError as e:
            print(f"❌ {name}: FAIL - Assertion failed: {e}")
            results.append((name, False))
        except Exception as e:
            print(f"❌ {name}: FAIL - Exception: {e}")
            import traceback
            traceback.print_exc()
            results.append((name, False))
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
