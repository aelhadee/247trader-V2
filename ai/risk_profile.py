"""
AI risk profile mappings.

Maps risk modes to concrete multipliers and constraints.
ALL values are WITHIN existing policy.yaml caps - AI cannot exceed them.
"""

from typing import Dict, Any
from .schemas import RiskMode

# Risk profiles: AI suggests mode, but all values are clamped by policy.yaml
RISK_PROFILE: Dict[str, Dict[str, Any]] = {
    "OFF": {
        "trade_size_multiplier": 0.0,    # No trades
        "max_at_risk_pct": 0.0,          # No exposure
        "description": "Kill switch - no new trades",
    },
    "DEFENSIVE": {
        "trade_size_multiplier": 0.5,    # Half size
        "max_at_risk_pct": 10.0,         # Lower exposure cap
        "max_positions": 3,              # Fewer positions
        "description": "Conservative mode - reduced sizing and exposure",
    },
    "NORMAL": {
        "trade_size_multiplier": 1.0,    # Full size per strategy
        "max_at_risk_pct": 15.0,         # Standard exposure
        "max_positions": 5,              # Standard position limit
        "description": "Standard operation - strategy defaults",
    },
    "AGGRESSIVE": {
        "trade_size_multiplier": 1.0,    # Still NOT >1.0 in v1
        "max_at_risk_pct": 15.0,         # Same as NORMAL (safety first)
        "max_positions": 5,              # Same as NORMAL
        "description": "Future: allow slightly higher sizing (v2)",
    },
}


def get_risk_profile(mode: RiskMode) -> Dict[str, Any]:
    """
    Get risk profile for a given mode.
    
    Args:
        mode: Risk mode string
        
    Returns:
        Dict with multipliers and constraints
    """
    return RISK_PROFILE.get(mode, RISK_PROFILE["NORMAL"])


def apply_risk_profile_to_caps(
    mode: RiskMode,
    policy_max_at_risk_pct: float,
    policy_max_positions: int,
) -> Dict[str, float]:
    """
    Apply risk profile but never exceed policy.yaml caps.
    
    Args:
        mode: AI-suggested risk mode
        policy_max_at_risk_pct: Hard cap from policy.yaml
        policy_max_positions: Hard cap from policy.yaml
        
    Returns:
        Dict with runtime multipliers clamped to policy limits
    """
    profile = get_risk_profile(mode)
    
    return {
        "trade_size_multiplier": min(
            profile["trade_size_multiplier"],
            1.0,  # NEVER exceed 1.0 in v1
        ),
        "max_at_risk_pct": min(
            profile["max_at_risk_pct"],
            policy_max_at_risk_pct,  # Policy is ceiling
        ),
        "max_positions": min(
            profile.get("max_positions", policy_max_positions),
            policy_max_positions,  # Policy is ceiling
        ),
    }
