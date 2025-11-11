#!/usr/bin/env python3
"""Smoke-test the fail-closed data gating behaviour.

This script exercises the execution engine with two mock exchanges:
1. ``FailingExchange`` consistently raises when balances are requested, and we
   assert that ``CriticalDataUnavailable`` propagates back to the caller.
2. ``HappyExchange`` supplies a minimal account snapshot so the normal sizing
   path succeeds, demonstrating the engine still functions when data is
   available.

Run: ``./scripts/fail_closed_smoke.py`` (with the project virtualenv active).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.execution import ExecutionEngine
from core.exceptions import CriticalDataUnavailable


@dataclass
class DummyProposal:
    """Minimal stand-in for a TradeProposal."""

    symbol: str
    size_pct: float
    confidence: float = 1.0


class FailingExchange:
    """Exchange stub that simulates Coinbase being unavailable."""

    def get_accounts(self):
        raise RuntimeError("coinbase: network timeout")


class HappyExchange:
    """Exchange stub that returns consistent account and quote data."""

    def __init__(self, balance: float = 1_000.0):
        self._balance = balance
        self._account_calls = 0

    def get_accounts(self):
        self._account_calls += 1
        return [
            {
                "currency": "USDC",
                "available_balance": {"value": str(self._balance)},
            }
        ]

    def get_quote(self, pair: str):
        # Always respond with a $1 mid-price; good enough for sizing.
        return SimpleNamespace(mid=1.0, last=1.0)


def exercise_fail_closed() -> None:
    """Verify that missing account data raises CriticalDataUnavailable."""

    engine = ExecutionEngine(mode="LIVE", exchange=FailingExchange(), policy={})
    proposal = DummyProposal(symbol="BTC-USD", size_pct=5.0)

    try:
        engine.adjust_proposals_to_capital([proposal], portfolio_value_usd=10_000.0)
    except CriticalDataUnavailable as exc:
        print("✅ fail-closed trigger working:", exc.source)
    else:
        raise AssertionError("CriticalDataUnavailable was not raised as expected")


def exercise_happy_path() -> None:
    """Ensure normal sizing still works when data is present."""

    exchange = HappyExchange(balance=2_500.0)
    engine = ExecutionEngine(mode="LIVE", exchange=exchange, policy={})
    proposal = DummyProposal(symbol="ETH-USD", size_pct=10.0)

    sized = engine.adjust_proposals_to_capital([proposal], portfolio_value_usd=10_000.0)
    assert sized, "Expected proposal sizing to succeed"
    symbol, size_usd = sized[0][0].symbol, sized[0][1]
    print(f"✅ happy path sizing: {symbol} sized to ${size_usd:.2f}")


if __name__ == "__main__":
    exercise_fail_closed()
    exercise_happy_path()
