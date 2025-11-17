"""
Snapshot Builder - Construct complete market context for AI trader.

Builds structured snapshots containing:
- Universe data (price, vol, spread, momentum)
- Current positions & PnL
- Regime state
- Guardrails summary
- Recent trigger signals
"""

import logging
from typing import Any
from datetime import datetime

logger = logging.getLogger(__name__)


# ─── Snapshot Builder ──────────────────────────────────────────────────────

def build_ai_snapshot(
    universe_snapshot: list[dict[str, Any]],
    positions: list[dict[str, Any]],
    available_capital_usd: float,
    regime: str,
    guardrails: dict[str, Any],
    triggers: list[dict[str, Any]],
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Build complete market snapshot for AI trader.
    
    Args:
        universe_snapshot: List of universe symbols with price/vol/spread
        positions: Current open positions
        available_capital_usd: Available trading capital
        regime: Market regime (CHOP/TREND/etc.)
        guardrails: Risk guardrails summary
        triggers: Recent trigger signals
        metadata: Optional metadata (cycle_num, timestamp, etc.)
        
    Returns:
        Structured snapshot dict ready for LLM
    """
    snapshot = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "regime": regime,
        "universe": _format_universe(universe_snapshot),
        "positions": _format_positions(positions),
        "positions_count": len(positions),
        "available_capital_usd": available_capital_usd,
        "guardrails": _format_guardrails(guardrails),
        "triggers": _format_triggers(triggers),
    }
    
    if metadata:
        snapshot["metadata"] = metadata
    
    return snapshot


def _format_universe(universe: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Format universe data for AI consumption.
    
    Extract essential fields:
    - symbol, price, volume, spread
    - short-term changes (1h, 24h)
    - volatility metrics
    """
    formatted = []
    
    for u in universe:
        formatted.append({
            "symbol": u.get("symbol", "UNKNOWN"),
            "price": u.get("price", 0.0),
            "volume_24h": u.get("volume_24h", 0.0),
            "spread_pct": u.get("spread_pct", 0.0),
            "change_1h_pct": u.get("change_1h_pct", 0.0),
            "change_24h_pct": u.get("change_24h_pct", 0.0),
            "volatility": u.get("volatility", 0.0),
            "tier": u.get("tier", "UNKNOWN"),
        })
    
    return formatted


def _format_positions(positions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Format current positions for AI.
    
    Include:
    - symbol, size, avg_price
    - unrealized PnL ($ and %)
    - entry_time for duration tracking
    """
    formatted = []
    
    for p in positions:
        formatted.append({
            "symbol": p.get("product_id", "UNKNOWN"),
            "size": p.get("size", 0.0),
            "avg_price": p.get("average_price", 0.0),
            "current_price": p.get("current_price", 0.0),
            "unrealized_pnl_usd": p.get("unrealized_pnl", 0.0),
            "unrealized_pnl_pct": p.get("unrealized_pnl_pct", 0.0),
            "entry_time": p.get("entry_time", ""),
        })
    
    return formatted


def _format_guardrails(guardrails: dict[str, Any]) -> dict[str, Any]:
    """
    Extract high-level guardrails for AI context.
    
    AI needs to know:
    - Max total at risk (%)
    - Max position size (%)
    - Min trade notional ($)
    - Max trades per cycle/day
    """
    return {
        "max_total_at_risk_pct": guardrails.get("max_total_at_risk_pct", 25.0),
        "max_position_size_pct": guardrails.get("max_position_size_pct", 7.0),
        "min_trade_notional": guardrails.get("min_trade_notional_usd", 5.0),
        "max_trades_per_cycle": guardrails.get("max_trades_per_cycle", 3),
        "max_trades_per_day": guardrails.get("max_trades_per_day", 10),
    }


def _format_triggers(triggers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Format recent triggers for AI context.
    
    Include:
    - symbol, trigger type
    - strength, confidence
    - volatility (for sizing hints)
    """
    formatted = []
    
    for t in triggers:
        formatted.append({
            "symbol": t.get("symbol", "UNKNOWN"),
            "type": t.get("type", "UNKNOWN"),
            "strength": t.get("strength", 0.0),
            "confidence": t.get("confidence", 0.0),
            "volatility": t.get("volatility", 0.0),
        })
    
    return formatted
