#!/usr/bin/env python3
"""Test position exit logic without running full bot."""
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.position_manager import PositionManager
from core.exchange_coinbase import CoinbaseExchange
from infra.state_store import StateStore
import yaml
import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

def test_exits():
    """Test exit signal generation."""
    
    # Load policy
    with open("config/policy.yaml") as f:
        policy = yaml.safe_load(f)
    
    # Initialize components
    state_store = StateStore()
    position_manager = PositionManager(policy=policy, state_store=state_store)
    exchange = CoinbaseExchange(read_only=True)
    
    # Load current state
    state = state_store.load()
    positions = state.get("positions", {})
    managed_positions = state.get("managed_positions", {})
    
    print("\n" + "="*80)
    print("POSITION EXIT TEST")
    print("="*80 + "\n")
    
    # Get current prices
    current_prices = {}
    for symbol in positions.keys():
        pair = f"{symbol}-USD"
        try:
            quote = exchange.get_quote(pair)
            if quote and quote.ask > 0:
                current_prices[symbol] = quote.ask
        except Exception as e:
            logger.warning(f"Failed to get price for {symbol}: {e}")
    
    # Evaluate positions
    logger.info(f"Evaluating {len(positions)} positions for exits...")
    
    exit_proposals = position_manager.evaluate_positions(
        positions=positions,
        managed_positions=managed_positions,
        current_prices=current_prices,
    )
    
    print("\nRESULTS:")
    print("-" * 80)
    
    if exit_proposals:
        print(f"\n✅ Generated {len(exit_proposals)} SELL proposal(s):\n")
        for proposal in exit_proposals:
            notes = proposal.notes or {}
            print(f"  {proposal.symbol}")
            print(f"    Reason:        {notes.get('exit_reason', 'unknown')}")
            print(f"    Entry Price:   ${notes.get('entry_price', 0):.4f}")
            print(f"    Current Price: ${notes.get('current_price', 0):.4f}")
            print(f"    PnL:           {notes.get('pnl_pct', 0):+.2f}%")
            print(f"    Hold Time:     {notes.get('hold_hours', 0):.1f}h")
            print(f"    Size:          {proposal.base_size:.6f}")
            print(f"    Notional:      ${proposal.notional_usd:.2f}")
            print()
    else:
        print("\n❌ No positions met exit criteria")
        print("\nPosition Summary:")
        for symbol in positions.keys():
            managed = managed_positions.get(symbol, {})
            if not isinstance(managed, dict):
                continue
            
            entry_price = managed.get("entry_price", 0)
            current_price = current_prices.get(symbol, 0)
            
            if entry_price > 0 and current_price > 0:
                pnl_pct = ((current_price - entry_price) / entry_price) * 100
                sl_pct = managed.get("stop_loss_pct", 0)
                tp_pct = managed.get("take_profit_pct", 0)
                
                print(f"  {symbol:<10} Entry: ${entry_price:.4f} → Current: ${current_price:.4f} "
                      f"({pnl_pct:+.2f}%) [SL: -{sl_pct}%, TP: +{tp_pct}%]")
    
    print("\n" + "="*80)


if __name__ == "__main__":
    test_exits()
