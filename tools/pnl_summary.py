#!/usr/bin/env python3
"""Simple PnL summary from state store."""
import json
from pathlib import Path


state_path = Path(__file__).parent.parent / "data" / ".state.json"
with open(state_path) as f:
    state = json.load(f)

# Extract fills with complete data
fills = [e for e in state.get("events", []) 
         if e.get("event") == "fill" and all(k in e for k in ["quantity", "price", "fees", "side"])]

# Calculate totals
buy_value = sum(f["quantity"] * f["price"] + f["fees"] for f in fills if f["side"] == "BUY")
buy_fees = sum(f["fees"] for f in fills if f["side"] == "BUY")
sell_value = sum(f["quantity"] * f["price"] - f["fees"] for f in fills if f["side"] == "SELL")

# Current positions
positions = state.get("positions", {})
current_value = sum(p.get("usd_value", 0) for p in positions.values())

# Cash
cash = sum(state.get("cash_balances", {}).values())

# Calculate PnL
cost_basis = buy_value
unrealized_pnl = current_value - cost_basis
realized_pnl = sell_value - 0  # No sells yet
total_pnl = unrealized_pnl + realized_pnl

print("\n" + "="*60)
print("247TRADER-V2 PNL SUMMARY")
print("="*60)
print(f"\nBUYS:")
print(f"  Total spent (incl fees): ${buy_value:.2f}")
print(f"  Number of buy fills:     {sum(1 for f in fills if f['side'] == 'BUY')}")

print(f"\nSELLS:")
print(f"  Total proceeds:          ${sell_value:.2f}")
print(f"  Number of sell fills:    {sum(1 for f in fills if f['side'] == 'SELL')}")

print(f"\nCURRENT POSITIONS:")
print(f"  Position count:          {len([p for p in positions.values() if p.get('total', 0) > 0])}")
print(f"  Current market value:    ${current_value:.2f}")

print(f"\nPNL:")
print(f"  Cost basis:              ${cost_basis:.2f}")
print(f"  Unrealized PnL:          ${unrealized_pnl:.2f}  ({unrealized_pnl/cost_basis*100:.1f}%)")
print(f"  Realized PnL:            ${realized_pnl:.2f}")
print(f"  Total PnL:               ${total_pnl:.2f}  ({total_pnl/cost_basis*100:.1f}%)")
print(f"  Fees paid:               ${buy_fees:.2f}")

print(f"\nACCOUNT:")
print(f"  Cash:                    ${cash:.2f}")
print(f"  Positions:               ${current_value:.2f}")
print(f"  Total value:             ${cash + current_value:.2f}")

print("="*60 + "\n")
