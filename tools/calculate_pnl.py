#!/usr/bin/env python3
"""
Calculate total PnL (realized + unrealized) from state store and current prices.
"""
import json
import sys
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple


def load_state(state_path: str = "data/.state.json") -> dict:
    """Load the state store JSON."""
    with open(state_path, "r") as f:
        return json.load(f)


def extract_fills_by_symbol(state: dict) -> Dict[str, List[dict]]:
    """Extract all fill events grouped by symbol."""
    fills_by_symbol = {}
    
    for event in state.get("events", []):
        if event.get("event") == "fill" and "symbol" in event:
            symbol = event["symbol"]
            # Only process fills with complete data
            if all(k in event for k in ["quantity", "price", "fees", "side"]):
                if symbol not in fills_by_symbol:
                    fills_by_symbol[symbol] = []
                fills_by_symbol[symbol].append(event)
    
    return fills_by_symbol


def calculate_position_cost_basis(fills: List[dict]) -> Tuple[float, float, float]:
    """
    Calculate cost basis for a position from its fills.
    Returns: (total_quantity, weighted_avg_cost, total_fees)
    """
    buy_quantity = 0.0
    buy_cost = 0.0
    buy_fees = 0.0
    sell_quantity = 0.0
    sell_proceeds = 0.0
    sell_fees = 0.0
    
    for fill in sorted(fills, key=lambda x: x.get("at", "")):
        qty = fill["quantity"]
        price = fill["price"]
        fees = fill["fees"]
        side = fill["side"]
        
        if side == "BUY":
            buy_quantity += qty
            buy_cost += qty * price
            buy_fees += fees
        elif side == "SELL":
            sell_quantity += qty
            sell_proceeds += qty * price
            sell_fees += fees
    
    net_quantity = buy_quantity - sell_quantity
    
    if net_quantity > 0 and buy_quantity > 0:
        # Still holding some position - calculate weighted average cost
        avg_cost = (buy_cost + buy_fees) / buy_quantity
    else:
        avg_cost = 0.0
    
    total_fees = buy_fees + sell_fees
    
    return net_quantity, avg_cost, total_fees


def calculate_realized_pnl(fills: List[dict]) -> float:
    """
    Calculate realized PnL from sells.
    Uses FIFO to match sells with buys.
    """
    buys = []
    realized_pnl = 0.0
    
    for fill in sorted(fills, key=lambda x: x.get("at", "")):
        qty = fill["quantity"]
        price = fill["price"]
        fees = fill["fees"]
        side = fill["side"]
        
        if side == "BUY":
            buys.append({"qty": qty, "price": price, "fees": fees})
        elif side == "SELL":
            sell_proceeds = qty * price - fees
            remaining_sell = qty
            cost_basis = 0.0
            
            # Match with FIFO buys
            while remaining_sell > 0 and buys:
                buy = buys[0]
                take_qty = min(remaining_sell, buy["qty"])
                
                # Cost including proportional fees
                cost = take_qty * buy["price"] + (take_qty / buy["qty"]) * buy["fees"]
                cost_basis += cost
                
                buy["qty"] -= take_qty
                remaining_sell -= take_qty
                
                if buy["qty"] <= 0:
                    buys.pop(0)
            
            # Realized PnL for this sell
            proportional_proceeds = sell_proceeds * (qty - remaining_sell) / qty if qty > 0 else 0
            realized_pnl += proportional_proceeds - cost_basis
    
    return realized_pnl


def main():
    # Load state
    state_path = Path(__file__).parent.parent / "data" / ".state.json"
    state = load_state(state_path)
    
    # Extract positions and fills
    positions = state.get("positions", {})
    fills_by_symbol = extract_fills_by_symbol(state)
    cash_balances = state.get("cash_balances", {})
    
    print("\n" + "=" * 80)
    print("247TRADER-V2 PNL REPORT")
    print("=" * 80)
    print(f"Report Time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print()
    
    # Cash summary
    print("CASH BALANCES:")
    total_cash_usd = 0.0
    for currency, amount in cash_balances.items():
        print(f"  {currency:8s}: ${amount:12.2f}")
        if currency in ["USD", "USDC", "USDT"]:
            total_cash_usd += amount
    print(f"  {'TOTAL':8s}: ${total_cash_usd:12.2f}")
    print()
    
    # Position analysis
    print("OPEN POSITIONS:")
    print(f"{'Symbol':<12} {'Quantity':>15} {'Avg Cost':>12} {'Current':>12} {'Value':>12} {'Unrealized':>12} {'%':>8}")
    print("-" * 100)
    
    total_cost_basis = 0.0
    total_current_value = 0.0
    total_unrealized_pnl = 0.0
    total_fees_paid = 0.0
    total_realized_pnl = 0.0
    
    for symbol, pos_data in sorted(positions.items()):
        quantity = pos_data.get("total", 0.0)
        current_value = pos_data.get("usd_value", 0.0)
        
        if quantity <= 0:
            continue
        
        current_price = current_value / quantity if quantity > 0 else 0.0
        
        # Get fill history for this symbol
        symbol_key = f"{symbol}-USD"
        fills = fills_by_symbol.get(symbol_key, [])
        
        if fills:
            net_qty, avg_cost, fees_paid = calculate_position_cost_basis(fills)
            realized_pnl = calculate_realized_pnl(fills)
            
            cost_basis = net_qty * avg_cost
            unrealized_pnl = current_value - cost_basis
            pnl_pct = (unrealized_pnl / cost_basis * 100) if cost_basis > 0 else 0.0
            
            total_cost_basis += cost_basis
            total_current_value += current_value
            total_unrealized_pnl += unrealized_pnl
            total_fees_paid += fees_paid
            total_realized_pnl += realized_pnl
            
            print(f"{symbol:<12} {quantity:>15.4f} ${avg_cost:>11.4f} ${current_price:>11.4f} "
                  f"${current_value:>11.2f} ${unrealized_pnl:>11.2f} {pnl_pct:>7.2f}%")
        else:
            # Position exists but no fill data found
            print(f"{symbol:<12} {quantity:>15.4f} {'N/A':>12} ${current_price:>11.4f} "
                  f"${current_value:>11.2f} {'N/A':>12} {'N/A':>8}")
    
    print("-" * 100)
    print(f"{'TOTALS':<12} {'':<15} {'':<12} {'':<12} ${total_current_value:>11.2f} "
          f"${total_unrealized_pnl:>11.2f} {(total_unrealized_pnl/total_cost_basis*100 if total_cost_basis > 0 else 0):>7.2f}%")
    print()
    
    # Summary
    print("=" * 80)
    print("SUMMARY:")
    print(f"  Total Cost Basis:      ${total_cost_basis:>12.2f}")
    print(f"  Current Position Value: ${total_current_value:>12.2f}")
    print(f"  Unrealized PnL:        ${total_unrealized_pnl:>12.2f}")
    print(f"  Realized PnL:          ${total_realized_pnl:>12.2f}")
    print(f"  Total PnL:             ${(total_unrealized_pnl + total_realized_pnl):>12.2f}")
    print(f"  Total Fees Paid:       ${total_fees_paid:>12.2f}")
    print()
    print(f"  Cash on Hand:          ${total_cash_usd:>12.2f}")
    print(f"  Total Account Value:   ${(total_cash_usd + total_current_value):>12.2f}")
    print()
    
    if total_cost_basis > 0:
        total_return_pct = (total_unrealized_pnl + total_realized_pnl) / total_cost_basis * 100
        print(f"  Return on Capital:      {total_return_pct:>11.2f}%")
    
    # Trade statistics
    total_fills = sum(len(fills) for fills in fills_by_symbol.values())
    buy_fills = sum(1 for fills in fills_by_symbol.values() 
                   for f in fills if f.get("side") == "BUY")
    sell_fills = sum(1 for fills in fills_by_symbol.values() 
                    for f in fills if f.get("side") == "SELL")
    
    print()
    print(f"  Total Fills:           {total_fills:>12}")
    print(f"  Buy Orders:            {buy_fills:>12}")
    print(f"  Sell Orders:           {sell_fills:>12}")
    print("=" * 80)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
