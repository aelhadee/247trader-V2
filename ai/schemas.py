"""
AI Advisor schemas and data structures.

Defines the contract between the trading loop and AI advisory layer.
All inputs/outputs are strongly typed for safety and auditability.
"""

from dataclasses import dataclass
from typing import Dict, List, Literal, Optional

# Risk modes: AI can suggest these but cannot violate policy.yaml caps
RiskMode = Literal["OFF", "DEFENSIVE", "NORMAL", "AGGRESSIVE"]


@dataclass
class AIProposalIn:
    """Single trade proposal as seen by AI advisor."""
    symbol: str
    side: Literal["BUY", "SELL"]
    tier: str           # "T1" / "T2" / "T3"
    conviction: float   # 0.0–1.0 from strategy
    notional: float     # $ amount requested by rules_engine
    reason: str         # Human-readable justification from strategy


@dataclass
class AIMarketSnapshot:
    """High-level market context for AI reasoning."""
    regime: str                       # "trend", "chop", "crash", etc.
    nav: float                        # Total account value in USD
    exposure_pct: float               # Total exposure / NAV
    drawdown_24h_pct: Optional[float] = None  # NAV vs 24h ago
    realized_vol_24h: Optional[float] = None  # Simple realized vol proxy


@dataclass
class AIPortfolioSnapshot:
    """Current portfolio state for AI reasoning."""
    positions: Dict[str, float]   # symbol -> $ exposure
    realized_pnl_24h: float       # P&L over last 24h
    num_positions: int            # Current position count


@dataclass
class AIAdvisorInput:
    """Complete input payload for AI advisor call."""
    run_id: str                        # Unique cycle ID for audit trail
    timestamp_iso: str                 # ISO timestamp of decision point
    market: AIMarketSnapshot           # Market context
    portfolio: AIPortfolioSnapshot     # Portfolio state
    proposals: List[AIProposalIn]      # Proposals from rules_engine


@dataclass
class AIProposalDecision:
    """AI decision for a single proposal."""
    symbol: str
    side: Literal["BUY", "SELL"]
    decision: Literal["accept", "reduce", "skip"]
    size_factor: float          # 0–1, applied to notional (NEVER >1)
    comment: str = ""           # AI reasoning for audit


@dataclass
class AIAdvisorOutput:
    """AI advisor response with risk mode and per-proposal decisions."""
    risk_mode: Optional[RiskMode]                  # Suggested risk posture
    proposal_decisions: List[AIProposalDecision]   # Per-proposal guidance
    
    # Metadata for observability
    latency_ms: Optional[float] = None
    model_used: Optional[str] = None
    error: Optional[str] = None  # If fallback was triggered
