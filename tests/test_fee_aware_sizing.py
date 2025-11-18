"""
Test fee-aware sizing in ExecutionEngine.
"""
from core.execution import ExecutionEngine


def test_fee_estimation():
    """Test that fees are calculated correctly based on maker/taker rates."""
    
    policy = {
        "execution": {
            "maker_fee_bps": 40,  # 0.40%
            "taker_fee_bps": 60,  # 0.60%
        },
        "risk": {
            "min_trade_notional_usd": 10.0
        }
    }
    
    engine = ExecutionEngine(mode="DRY_RUN", policy=policy)
    
    # Test maker fee (0.40%)
    maker_fee = engine.estimate_fee(1000.0, is_maker=True)
    assert abs(maker_fee - 4.0) < 0.01, f"Expected $4.00 maker fee, got ${maker_fee:.2f}"
    
    # Test taker fee (0.60%)
    taker_fee = engine.estimate_fee(1000.0, is_maker=False)
    assert abs(taker_fee - 6.0) < 0.01, f"Expected $6.00 taker fee, got ${taker_fee:.2f}"
    
    print("✅ Fee estimation test passed")


def test_size_after_fees():
    """Test net position calculation after fees."""
    
    policy = {
        "execution": {
            "maker_fee_bps": 40,  # 0.40%
            "taker_fee_bps": 60,  # 0.60%
        },
        "risk": {
            "min_trade_notional_usd": 10.0
        }
    }
    
    engine = ExecutionEngine(mode="DRY_RUN", policy=policy)
    
    # $1000 order with maker fee = $1000 - $4 = $996 net
    net_maker = engine.size_after_fees(1000.0, is_maker=True)
    assert abs(net_maker - 996.0) < 0.01, f"Expected $996.00 net, got ${net_maker:.2f}"
    
    # $1000 order with taker fee = $1000 - $6 = $994 net
    net_taker = engine.size_after_fees(1000.0, is_maker=False)
    assert abs(net_taker - 994.0) < 0.01, f"Expected $994.00 net, got ${net_taker:.2f}"
    
    print("✅ Size after fees test passed")


def test_size_to_achieve_net():
    """Test reverse calculation: gross size needed for target net position."""
    
    policy = {
        "execution": {
            "maker_fee_bps": 40,  # 0.40%
            "taker_fee_bps": 60,  # 0.60%
        },
        "risk": {
            "min_trade_notional_usd": 10.0
        }
    }
    
    engine = ExecutionEngine(mode="DRY_RUN", policy=policy)
    
    # To get $1000 net with maker fees: need $1000 / 0.996 = $1004.016
    gross_maker = engine.size_to_achieve_net(1000.0, is_maker=True)
    expected_maker = 1000.0 / 0.996
    assert abs(gross_maker - expected_maker) < 0.01, \
        f"Expected ${expected_maker:.2f} gross for $1000 net, got ${gross_maker:.2f}"
    
    # Verify round-trip: gross → net → gross
    net = engine.size_after_fees(gross_maker, is_maker=True)
    assert abs(net - 1000.0) < 0.01, f"Round-trip failed: expected $1000 net, got ${net:.2f}"
    
    # To get $1000 net with taker fees: need $1000 / 0.994 = $1006.036
    gross_taker = engine.size_to_achieve_net(1000.0, is_maker=False)
    expected_taker = 1000.0 / 0.994
    assert abs(gross_taker - expected_taker) < 0.01, \
        f"Expected ${expected_taker:.2f} gross for $1000 net, got ${gross_taker:.2f}"
    
    print("✅ Size to achieve net test passed")


def test_min_gross_size():
    """Test minimum gross size calculation that accounts for fees."""
    
    policy = {
        "execution": {
            "maker_fee_bps": 40,  # 0.40%
            "default_order_type": "limit_post_only"
        },
        "risk": {
            "min_trade_notional_usd": 10.0
        }
    }
    
    engine = ExecutionEngine(mode="DRY_RUN", policy=policy)
    
    # With $10 min notional and 0.40% fee, need $10.04 gross to get $10 net
    min_gross = engine.get_min_gross_size(is_maker=True)
    expected = 10.0 / 0.996
    assert abs(min_gross - expected) < 0.01, \
        f"Expected ${expected:.2f} min gross, got ${min_gross:.2f}"
    
    # Verify: min_gross after fees = min_notional
    net = engine.size_after_fees(min_gross, is_maker=True)
    assert abs(net - 10.0) < 0.01, \
        f"Min gross ${min_gross:.2f} should yield $10.00 net, got ${net:.2f}"
    
    print("✅ Min gross size test passed")


def test_fee_impact_on_small_trades():
    """Test that fees are significant on small trades and properly accounted for."""
    
    policy = {
        "execution": {
            "maker_fee_bps": 40,  # 0.40%
            "taker_fee_bps": 60,  # 0.60%
        },
        "risk": {
            "min_trade_notional_usd": 10.0
        }
    }
    
    engine = ExecutionEngine(mode="DRY_RUN", policy=policy)
    
    # On a $10 trade, maker fee is $0.04 (0.4%)
    small_maker_fee = engine.estimate_fee(10.0, is_maker=True)
    assert abs(small_maker_fee - 0.04) < 0.001, \
        f"Expected $0.04 fee on $10 trade, got ${small_maker_fee:.3f}"
    
    # On a $10 trade, taker fee is $0.06 (0.6%)
    small_taker_fee = engine.estimate_fee(10.0, is_maker=False)
    assert abs(small_taker_fee - 0.06) < 0.001, \
        f"Expected $0.06 fee on $10 trade, got ${small_taker_fee:.3f}"
    
    # Fee as percentage of trade
    maker_pct = (small_maker_fee / 10.0) * 100
    taker_pct = (small_taker_fee / 10.0) * 100
    
    print(f"  $10 trade: maker fee ${small_maker_fee:.3f} ({maker_pct:.2f}%), "
          f"taker fee ${small_taker_fee:.3f} ({taker_pct:.2f}%)")
    
    assert maker_pct == 0.4, "Maker fee should be 0.4%"
    assert taker_pct == 0.6, "Taker fee should be 0.6%"
    
    print("✅ Fee impact on small trades test passed")


if __name__ == "__main__":
    test_fee_estimation()
    test_size_after_fees()
    test_size_to_achieve_net()
    test_min_gross_size()
    test_fee_impact_on_small_trades()
    print("\n✅ All fee-aware sizing tests passed!")
