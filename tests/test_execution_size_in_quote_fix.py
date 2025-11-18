"""
Test for CRITICAL size_in_quote parsing bug fix.

Bug: When Coinbase returns size_in_quote=True, the 'size' field contains
quote currency (USD), not base currency (ETH/BTC). The old code treated
it as base units, causing massive accounting errors.

Example:
- ETH-USD market buy of $2.68
- Coinbase returns: size=2.6399716828, size_in_quote=True, price=2975.32
- OLD CODE: 2.64 ETH position → $7,854 notional (WRONG!)
- FIXED CODE: 2.64 USD notional, 0.000887 ETH position (CORRECT)
"""

from decimal import Decimal
from core.execution import ExecutionEngine


def test_size_in_quote_true_parses_correctly():
    """
    Test ETH-USD fill with size_in_quote=True from real LIVE trade.
    
    This is the exact fill that triggered the bug discovery on 2025-11-17.
    """
    # Real fill from logs/live_20251117_232915.log
    fills = [
        {
            "entry_id": "08883b5eb05869736e3212e4f0215ac957a4c78a23702a7119c70d7cab951690",
            "trade_id": "18d2b893-f45f-4925-989b-e4ef074b6b21",
            "order_id": "7a83ee72-6bfb-4481-86d1-2617bf2a42f7",
            "trade_time": "2025-11-18T04:29:43.786756Z",
            "trade_type": "FILL",
            "price": "2975.32",  # ETH price in USD
            "size": "2.6399716828",  # ← This is USD, NOT ETH (because size_in_quote=True)
            "commission": "0.0316796601936",
            "product_id": "ETH-USD",
            "sequence_timestamp": "2025-11-18T04:29:43.789372Z",
            "liquidity_indicator": "TAKER",
            "size_in_quote": True,  # ← CRITICAL: size field is in quote currency (USD)
            "user_id": "b2329b9b-def2-57b8-b52e-f2666695ef0c",
            "side": "BUY",
            "retail_portfolio_id": "b2329b9b-def2-57b8-b52e-f2666695ef0c",
            "fillSource": "FILL_SOURCE_CLOB",
        }
    ]

    # Call _summarize_fills (static method)
    base_size, avg_price, fees, quote_notional = ExecutionEngine._summarize_fills(fills, None)

    # Expected results
    expected_quote = 2.6399716828  # USD notional
    expected_base = 2.6399716828 / 2975.32  # ≈ 0.000887367 ETH
    expected_price = 2975.32
    expected_fees = 0.0316796601936

    # Assertions
    assert abs(quote_notional - expected_quote) < 0.01, (
        f"Quote notional should be ~$2.64 USD, got {quote_notional}"
    )
    assert abs(avg_price - expected_price) < 0.01, (
        f"Price should be ~$2975.32, got {avg_price}"
    )
    assert abs(base_size - expected_base) < 0.0001, (
        f"Base size should be ~0.000887 ETH, got {base_size}. "
        f"If this is 2.64, the bug is NOT fixed!"
    )
    assert abs(fees - expected_fees) < 0.01, (
        f"Fees should be ~$0.032, got {fees}"
    )

    # Critical check: ensure base_size is NOT the raw size field value
    assert abs(base_size - 2.6399716828) > 0.1, (
        f"CRITICAL BUG STILL PRESENT: base_size={base_size} matches raw size field! "
        f"Should be ~0.000887 ETH, not 2.64 ETH. The size_in_quote fix is not working."
    )

    print("✅ size_in_quote=True parsing is CORRECT")
    print(f"   Quote notional: ${quote_notional:.6f} (requested ~$2.68)")
    print(f"   Base size: {base_size:.8f} ETH (NOT 2.64!)")
    print(f"   Avg price: ${avg_price:.2f}")
    print(f"   Fees: ${fees:.6f}")


def test_size_in_quote_false_standard_behavior():
    """
    Test that size_in_quote=False (or missing) uses standard base-unit parsing.
    
    This is the normal case for most orders.
    """
    fills = [
        {
            "price": "50.00",
            "size": "2.5",  # 2.5 SOL in base units
            "commission": "0.15",
            "product_id": "SOL-USD",
            "size_in_quote": False,  # Standard behavior
            "side": "BUY",
        }
    ]

    base_size, avg_price, fees, quote_notional = ExecutionEngine._summarize_fills(fills, None)

    # With size_in_quote=False, size is base units
    assert abs(base_size - 2.5) < 0.01, f"Base should be 2.5 SOL, got {base_size}"
    assert abs(quote_notional - 125.0) < 0.01, f"Quote should be $125, got {quote_notional}"
    assert abs(avg_price - 50.0) < 0.01, f"Price should be $50, got {avg_price}"

    print("✅ size_in_quote=False (standard) parsing is CORRECT")


def test_size_in_quote_missing_defaults_to_false():
    """
    Test that missing size_in_quote flag defaults to standard base-unit behavior.
    """
    fills = [
        {
            "price": "100.00",
            "size": "1.0",
            "commission": "0.06",
            "product_id": "XRP-USD",
            # size_in_quote missing - should default to False
            "side": "BUY",
        }
    ]

    base_size, avg_price, fees, quote_notional = ExecutionEngine._summarize_fills(fills, None)

    assert abs(base_size - 1.0) < 0.01
    assert abs(quote_notional - 100.0) < 0.01
    assert abs(avg_price - 100.0) < 0.01

    print("✅ Missing size_in_quote defaults to standard behavior")


if __name__ == "__main__":
    print("\n" + "="*70)
    print("CRITICAL BUG FIX TEST: size_in_quote parsing")
    print("="*70 + "\n")
    
    test_size_in_quote_true_parses_correctly()
    print()
    test_size_in_quote_false_standard_behavior()
    print()
    test_size_in_quote_missing_defaults_to_false()
    
    print("\n" + "="*70)
    print("ALL TESTS PASSED ✅")
    print("="*70 + "\n")
