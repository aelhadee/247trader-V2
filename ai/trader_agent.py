"""AI Trader Agent - Portfolio allocator powered by LLM decisions.

This module turns rich universe/portfolio context into a JSON payload for
an LLM (AiTraderClient) and converts the model's target allocations back into
`TradeProposal` objects that flow through the normal risk + execution pipeline.

Design goals:
- Deterministic guardrails (no position > max_position_pct)
- Delta sizing (trades represent movement toward target allocation)
- Halal guardrails enforced downstream by RiskEngine, but agent errs on safe side
- Fail closed: any model error → zero proposals
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional
import logging

from core.risk import PortfolioState
from core.triggers import TriggerSignal
from core.universe import UniverseSnapshot, UniverseAsset
from strategy.rules_engine import TradeProposal
from ai.llm_client import AiTradeDecision, AiTraderClient
from ai.snapshot_builder import build_ai_snapshot

logger = logging.getLogger(__name__)


@dataclass
class TraderAgentSettings:
    """Runtime knobs for translating AI allocations into trades."""

    max_decisions: int = 5
    min_confidence: float = 0.55
    min_rebalance_delta_pct: float = 0.35
    max_position_pct: float = 7.0
    max_single_trade_pct: float = 4.0
    tag: str = "ai_trader_agent"


class AiTraderAgent:
    """Portfolio allocator that lets AI propose target weights safely."""

    def __init__(
        self,
        enabled: bool,
        client: Optional[AiTraderClient],
        settings: TraderAgentSettings,
    ) -> None:
        self.enabled = bool(enabled)
        self._client = client
        self.settings = settings

        if self.enabled and self._client is None:
            raise ValueError("AiTraderAgent enabled but no AI client provided")

    def generate_proposals(
        self,
        universe: UniverseSnapshot,
        triggers: List[TriggerSignal],
        portfolio: PortfolioState,
        guardrails: Dict[str, Any],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> List[TradeProposal]:
        """Call the LLM and convert allocations to TradeProposals."""

        if not self.enabled or not self._client:
            return []

        guardrails = guardrails or {}
        nav = float(portfolio.account_value_usd or 0.0)
        if nav <= 0:
            logger.warning("AiTraderAgent skipped: NAV<=0")
            return []

        snapshot = self._build_snapshot(universe, portfolio, triggers, guardrails, metadata)

        try:
            decisions = self._client.get_decisions(
                snapshot=snapshot,
                max_decisions=min(self.settings.max_decisions, int(guardrails.get("max_trades_per_cycle", self.settings.max_decisions) or self.settings.max_decisions)),
            )
        except Exception as exc:  # pragma: no cover - network errors
            logger.error("AiTraderAgent failed to get decisions: %s", exc, exc_info=True)
            return []

        if not decisions:
            logger.info("AiTraderAgent returned 0 decisions")
            return []

        proposals = self._decisions_to_proposals(decisions, universe, portfolio, nav, guardrails)
        logger.info(
            "AiTraderAgent converted %d decision(s) into %d proposal(s)",
            len(decisions),
            len(proposals),
        )
        return proposals

    # ------------------------------------------------------------------
    # Snapshot Helpers
    # ------------------------------------------------------------------

    def _build_snapshot(
        self,
        universe: UniverseSnapshot,
        portfolio: PortfolioState,
        triggers: List[TriggerSignal],
        guardrails: Dict[str, Any],
        metadata: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        available_capital = max(
            0.0,
            float(portfolio.account_value_usd or 0.0) - float(portfolio.get_total_exposure_usd()),
        )
        return build_ai_snapshot(
            universe_snapshot=self._format_universe(universe),
            positions=self._format_positions(portfolio),
            available_capital_usd=available_capital,
            regime=universe.regime,
            guardrails=guardrails,
            triggers=self._format_triggers(triggers),
            metadata=metadata or {},
        )

    def _format_universe(self, universe: UniverseSnapshot) -> List[Dict[str, Any]]:
        assets = []
        for asset in universe.get_all_eligible():
            assets.append(
                {
                    "symbol": asset.symbol,
                    "tier": asset.tier,
                    "volume_24h": getattr(asset, "volume_24h", 0.0),
                    "spread_pct": getattr(asset, "spread_pct", asset.spread_bps / 100.0 if getattr(asset, "spread_bps", None) is not None else 0.0),
                    "change_1h_pct": getattr(asset, "change_1h_pct", 0.0),
                    "change_24h_pct": getattr(asset, "change_24h_pct", 0.0),
                    "volatility": getattr(asset, "volatility", 0.0),
                    "allocation_min_pct": getattr(asset, "allocation_min_pct", 0.0),
                    "allocation_max_pct": getattr(asset, "allocation_max_pct", 100.0),
                }
            )
        return assets

    def _format_positions(self, portfolio: PortfolioState) -> List[Dict[str, Any]]:
        formatted = []
        for symbol, payload in (portfolio.open_positions or {}).items():
            usd_value = portfolio.get_position_usd(symbol)
            formatted.append(
                {
                    "product_id": symbol,
                    "symbol": symbol,
                    "usd": usd_value,
                    "size": float(payload.get("units", 0.0)) if isinstance(payload, dict) else 0.0,
                    "average_price": (payload.get("average_price") if isinstance(payload, dict) else None),
                    "unrealized_pnl_usd": (payload.get("unrealized_pnl") if isinstance(payload, dict) else None),
                    "unrealized_pnl_pct": (payload.get("unrealized_pnl_pct") if isinstance(payload, dict) else None),
                }
            )
        return formatted

    def _format_triggers(self, triggers: List[TriggerSignal]) -> List[Dict[str, Any]]:
        formatted = []
        for trigger in triggers or []:
            formatted.append(
                {
                    "symbol": trigger.symbol,
                    "type": trigger.trigger_type,
                    "strength": trigger.strength,
                    "confidence": trigger.confidence,
                    "volatility": getattr(trigger, "volatility", 0.0),
                }
            )
        return formatted

    # ------------------------------------------------------------------
    # Decision Conversion
    # ------------------------------------------------------------------

    def _decisions_to_proposals(
        self,
        decisions: List[AiTradeDecision],
        universe: UniverseSnapshot,
        portfolio: PortfolioState,
        nav: float,
        guardrails: Dict[str, Any],
    ) -> List[TradeProposal]:
        proposals: List[TradeProposal] = []

        for decision in decisions:
            if decision.confidence < self.settings.min_confidence:
                continue

            action = decision.action.upper()
            if action not in {"BUY", "SELL"}:
                continue

            asset = universe.get_asset(decision.symbol)
            if not asset:
                logger.debug("AiTraderAgent ignored %s: not in universe", decision.symbol)
                continue

            target_pct = self._clamp_target_pct(decision.target_weight_pct, asset, guardrails)
            current_usd = portfolio.get_position_usd(decision.symbol)
            current_pct = (current_usd / nav * 100.0) if nav > 0 else 0.0
            delta_pct = target_pct - current_pct

            if action == "SELL" and current_pct <= 0:
                continue

            if abs(delta_pct) < self.settings.min_rebalance_delta_pct:
                continue

            side = "BUY" if delta_pct > 0 else "SELL"
            size_pct = min(abs(delta_pct), self.settings.max_single_trade_pct)
            if size_pct <= 0:
                continue

            proposal = TradeProposal(
                symbol=decision.symbol,
                side=side,
                size_pct=size_pct,
                reason=self._build_reason(decision, target_pct, current_pct),
                confidence=min(1.0, decision.confidence),
                asset=asset,
            )
            proposal.tags.append(self.settings.tag)
            proposal.metadata.update(
                {
                    "strategy_source": self.settings.tag,
                    "ai_target_pct": round(target_pct, 4),
                    "ai_current_pct": round(current_pct, 4),
                    "ai_delta_pct": round(delta_pct, 4),
                    "ai_time_horizon_minutes": decision.time_horizon_minutes,
                }
            )
            proposals.append(proposal)

        return proposals

    def _clamp_target_pct(
        self,
        requested_pct: float,
        asset: UniverseAsset,
        guardrails: Dict[str, Any],
    ) -> float:
        max_pct = min(
            self.settings.max_position_pct,
            guardrails.get("max_position_size_pct", self.settings.max_position_pct) or self.settings.max_position_pct,
            getattr(asset, "allocation_max_pct", self.settings.max_position_pct) or self.settings.max_position_pct,
        )
        return max(0.0, min(float(requested_pct), max_pct))

    def _build_reason(
        self,
        decision: AiTradeDecision,
        target_pct: float,
        current_pct: float,
    ) -> str:
        rationale = decision.rationale.strip() if decision.rationale else "No rationale provided"
        return (
            f"[AI] target {target_pct:.2f}% (current {current_pct:.2f}%) "
            f"action={decision.action} conf={decision.confidence:.2f}: {rationale[:240]}"
        )
