"""Regression tests for execution fill aggregation math."""

from decimal import Decimal

import pytest

from core.execution import ExecutionEngine


@pytest.mark.parametrize(
    "fill, expected_base, expected_quote",
    [
        (
            {
                "size": "4.970300",
                "size_in_quote": "5.030200",
                "price": "0.55",
                "commission": "0.003",  # USD
                "liquidity_indicator": "TAKER",
            },
            Decimal("9.14581818181818181818"),
            Decimal("5.030200"),
        ),
        (
            {
                "size": "0.00012299867404",
                "size_in_quote": "5.030182",
                "price": "0.28",
                "commission": "0.0028",
                "liquidity_indicator": "TAKER",
            },
            Decimal("17.96529285714285714286"),
            Decimal("5.030182"),
        ),
    ],
)
def test_summarize_fills_uses_quote_when_base_mismatch(fill, expected_base, expected_quote):
    """Fill aggregation should reconcile mismatched size vs quote amounts."""

    total_size, avg_price, total_fees = ExecutionEngine._summarize_fills([fill])

    assert total_size == pytest.approx(expected_base)
    assert avg_price == pytest.approx(expected_quote / expected_base)
    assert total_fees == pytest.approx(float(fill["commission"]))


def test_summarize_fills_accumulates_multiple_rows():
    fills = [
        {
            "size": "0.5",
            "size_in_quote": "50.0",
            "price": "100",
            "commission": "0.05",
        },
        {
            "size": "0.25",
            "size_in_quote": "25.0",
            "price": "100",
            "commission": "0.025",
        },
    ]

    total_size, avg_price, total_fees = ExecutionEngine._summarize_fills(fills)

    assert total_size == pytest.approx(0.75)
    assert avg_price == pytest.approx(100.0)
    assert total_fees == pytest.approx(0.075)
