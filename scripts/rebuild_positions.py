"""Utility to rebuild position quantities from recent Coinbase fills.

Usage:
    python scripts/rebuild_positions.py --hours 48 --state data/.state.json

The script fetches recent fills, aggregates base quantities per symbol, and
updates the state store so that `quantity`/`base_qty` reflect base units while
`usd` mirrors the notional exposure at average fill price.

The exchange client runs in read-only mode. API credentials (if required) are
loaded via the existing environment variables/secret file flow.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, getcontext
from typing import Dict, Iterable, List, Optional

from core.exchange_coinbase import CoinbaseExchange
from infra.state_store import StateStore, get_state_store

getcontext().prec = 28


def _as_decimal(value: object) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _parse_trade_time(payload: dict) -> Optional[datetime]:
    raw = payload.get("trade_time") or payload.get("executed_at")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _aggregate_fills(fills: Iterable[dict]) -> Optional[Dict[str, Decimal]]:
    base_total = Decimal("0")
    notional_total = Decimal("0")
    fee_total = Decimal("0")

    for fill in fills:
        price = _as_decimal(fill.get("price"))
        if price is None or price <= 0:
            continue

        size = _as_decimal(fill.get("size"))
        if size is None or size <= 0:
            # As a fallback, convert quote amounts to base units
            quote_size = _as_decimal(fill.get("size_in_quote") or fill.get("quote_size"))
            if quote_size is not None and quote_size > 0:
                size = quote_size / price

        if size is None or size == 0:
            continue

        side = (fill.get("side") or "BUY").upper()
        signed_size = size if side == "BUY" else -size

        base_total += signed_size
        notional_total += signed_size * price

        fee = _as_decimal(fill.get("commission") or fill.get("fee"))
        if fee:
            fee_total += fee

    if base_total == 0:
        return None

    avg_price = notional_total / base_total
    exposure = base_total * avg_price

    return {
        "base_qty": base_total.copy_abs(),
        "avg_price": avg_price,
        "usd": exposure,
        "fees": fee_total,
    }


def _load_state(state_file: Optional[str]) -> StateStore:
    return get_state_store(state_file=state_file)


def rebuild_positions(
    *,
    hours: int,
    state_file: Optional[str] = None,
    dry_run: bool = False,
) -> Dict[str, object]:
    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    exchange = CoinbaseExchange(read_only=True)
    fills = exchange.list_fills(limit=1000, start_time=since)

    fills_by_symbol: Dict[str, List[dict]] = defaultdict(list)
    for fill in fills:
        product_id = fill.get("product_id")
        if not product_id:
            continue

        trade_time = _parse_trade_time(fill)
        if trade_time and trade_time < since:
            continue

        fills_by_symbol[product_id].append(fill)

    store = _load_state(state_file)
    state = store.load()
    positions = state.setdefault("positions", {})

    updated = 0
    removed = 0

    for symbol, symbol_fills in fills_by_symbol.items():
        summary = _aggregate_fills(symbol_fills)
        if not summary:
            if symbol in positions:
                removed += 1
                positions.pop(symbol, None)
            continue

        base_qty = float(summary["base_qty"])
        avg_price = float(summary["avg_price"])
        usd_value = float(summary["usd"].copy_abs())
        fees_paid = float(summary["fees"])

        entry = positions.get(symbol, {})
        entry.update(
            {
                "side": "BUY" if base_qty >= 0 else "SELL",
                "quantity": abs(base_qty),
                "units": abs(base_qty),
                "base_qty": abs(base_qty),
                "entry_price": abs(avg_price),
                "entry_value_usd": usd_value,
                "usd_value": usd_value,
                "usd": usd_value,
                "fees_paid": fees_paid,
                "last_updated": datetime.now(timezone.utc).isoformat(),
                "rebuild_source": "fills",
            }
        )
        if "entry_time" not in entry:
            entry["entry_time"] = entry["last_updated"]
        positions[symbol] = entry
        updated += 1

    if dry_run:
        return {
            "symbols_seen": len(fills_by_symbol),
            "positions_updated": updated,
            "positions_removed": removed,
            "state_preview": json.dumps({"positions": positions}, indent=2)[:2048],
        }

    store.save(state)
    return {
        "symbols_seen": len(fills_by_symbol),
        "positions_updated": updated,
        "positions_removed": removed,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rebuild position quantities from recent fills")
    parser.add_argument("--hours", type=int, default=48, help="Lookback window in hours (default: 48)")
    parser.add_argument("--state", type=str, default=None, help="Path to state file (optional)")
    parser.add_argument("--dry-run", action="store_true", help="Show changes without persisting state")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    summary = rebuild_positions(hours=args.hours, state_file=args.state, dry_run=args.dry_run)
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
