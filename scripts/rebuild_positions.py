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
    buy_base = Decimal("0")
    buy_usd = Decimal("0")
    sell_base = Decimal("0")
    sell_usd = Decimal("0")
    fee_total = Decimal("0")
    last_price: Optional[Decimal] = None
    last_time: Optional[datetime] = None

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

        if size is None or size <= 0:
            continue

        side = (fill.get("side") or "BUY").upper()
        trade_dt = _parse_trade_time(fill)
        if trade_dt and (last_time is None or trade_dt > last_time):
            last_time = trade_dt
            last_price = price

        if side == "BUY":
            buy_base += size
            buy_usd += size * price
        else:
            sell_base += size
            sell_usd += size * price

        fee = _as_decimal(fill.get("commission") or fill.get("fee"))
        if fee:
            fee_total += fee

    net_base = buy_base - sell_base
    if net_base <= 0:
        return None

    avg_buy_price = (buy_usd / buy_base) if buy_base > 0 else Decimal("0")
    valuation_price = last_price or avg_buy_price
    if valuation_price <= 0:
        valuation_price = avg_buy_price or Decimal("0")

    net_usd_cost = avg_buy_price * net_base if avg_buy_price > 0 else Decimal("0")
    mark_to_market = net_base * valuation_price if valuation_price > 0 else net_usd_cost

    return {
        "net_base": net_base,
        "avg_buy_price": avg_buy_price,
        "usd_cost": net_usd_cost,
        "usd_mark": mark_to_market,
        "fees": fee_total,
        "last_price": valuation_price,
        "last_time": last_time,
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
        # Sort fills oldest->newest for deterministic processing and last price tracking
        symbol_fills_sorted = sorted(
            symbol_fills,
            key=lambda item: _parse_trade_time(item) or datetime.min.replace(tzinfo=timezone.utc),
        )
        summary = _aggregate_fills(symbol_fills_sorted)
        if not summary:
            if symbol in positions:
                removed += 1
                positions.pop(symbol, None)
            continue

        net_base = summary["net_base"]
        base_qty = float(net_base.copy_abs())
        avg_price = float(summary["avg_buy_price"]) if summary["avg_buy_price"] else 0.0
        usd_cost = float(summary["usd_cost"]) if summary["usd_cost"] else 0.0
        usd_mark = float(summary["usd_mark"]) if summary["usd_mark"] else usd_cost
        fees_paid = float(summary["fees"]) if summary["fees"] else 0.0
        last_price = float(summary["last_price"]) if summary["last_price"] else avg_price
        last_fill_time = summary["last_time"].isoformat() if summary["last_time"] else None

        entry = positions.get(symbol, {})
        now_iso = datetime.now(timezone.utc).isoformat()
        is_long = net_base > 0
        entry.update(
            {
                "side": "BUY" if is_long else "SELL",
                "quantity": base_qty,
                "units": base_qty,
                "base_qty": base_qty,
                "entry_price": avg_price,
                "entry_value_usd": usd_cost,
                "usd_value": usd_mark,
                "usd": usd_mark,
                "fees_paid": fees_paid,
                "last_updated": now_iso,
                "last_fill_price": last_price,
                "rebuild_source": "fills",
            }
        )
        if last_fill_time:
            entry["last_fill_time"] = last_fill_time
        if "entry_time" not in entry:
            entry["entry_time"] = now_iso
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
