"""
247trader-v2 Strategy: Signal Manager

Orchestrates modular signals with regime-aware filtering.
Bridges between new signal architecture and existing TriggerEngine.

Integration pattern:
1. Load signal config (config/signals.yaml)
2. Instantiate enabled signals from registry
3. Apply regime filtering from policy
4. Return filtered signals to RulesEngine
"""

from typing import Dict, List
import yaml
import logging

from core.exchange_coinbase import OHLCV
from core.universe import UniverseAsset
from core.triggers import TriggerSignal
from strategy.signals import BaseSignal, get_signal, SIGNAL_REGISTRY

logger = logging.getLogger(__name__)


class SignalManager:
    """
    Manages modular signal generation with regime filtering.

    Responsibilities:
    - Load signal configuration
    - Instantiate enabled signals
    - Apply regime-aware filtering
    - Coordinate signal scanning
    """

    def __init__(
        self,
        signals_config_path: str = "config/signals.yaml",
        policy_config_path: str = "config/policy.yaml"
    ):
        """
        Initialize signal manager.

        Args:
            signals_config_path: Path to signals.yaml
            policy_config_path: Path to policy.yaml (for regime filtering)
        """
        # Load configurations
        with open(signals_config_path) as f:
            self.signals_config = yaml.safe_load(f)

        with open(policy_config_path) as f:
            self.policy_config = yaml.safe_load(f)

        # Extract enabled signals
        enabled = self.signals_config.get("enabled_signals", ["price_move"])

        # Instantiate signals
        self.signals: Dict[str, BaseSignal] = {}
        for signal_name in enabled:
            if signal_name in SIGNAL_REGISTRY:
                config = self.signals_config.get("signals", {}).get(signal_name, {})
                self.signals[signal_name] = get_signal(signal_name, config)
                logger.info(f"Loaded signal: {signal_name}")
            else:
                logger.warning(f"Unknown signal: {signal_name} (skipping)")

        # Extract regime filters
        self.regime_config = self.policy_config.get("regime", {})

        logger.info(
            f"SignalManager initialized with {len(self.signals)} signals: "
            f"{list(self.signals.keys())}"
        )

    def scan(
        self,
        assets: List[UniverseAsset],
        candles_by_symbol: Dict[str, List[OHLCV]],
        regime: str = "chop"
    ) -> List[TriggerSignal]:
        """
        Scan assets for signals with regime filtering.

        Args:
            assets: Universe assets to scan
            candles_by_symbol: Historical OHLCV data per symbol
            regime: Current market regime

        Returns:
            List of TriggerSignals (regime-filtered)
        """
        # Get allowed signals for regime
        allowed_signals = self._get_allowed_signals(regime)

        if not allowed_signals:
            logger.debug(f"No signals allowed in {regime} regime")
            return []

        logger.debug(
            f"Scanning {len(assets)} assets with signals: {allowed_signals} "
            f"(regime={regime})"
        )

        # Scan each asset with allowed signals
        all_signals = []
        for asset in assets:
            candles = candles_by_symbol.get(asset.symbol)
            if not candles:
                continue

            # Try each allowed signal type
            for signal_name in allowed_signals:
                signal = self.signals.get(signal_name)
                if not signal:
                    continue

                try:
                    detected = signal.scan(asset, candles, regime)
                    if detected:
                        # Apply regime confidence adjustment
                        adjusted = self._apply_regime_adjustment(detected, regime)
                        all_signals.append(adjusted)

                        logger.debug(
                            f"{asset.symbol}: {signal_name} detected "
                            f"(strength={adjusted.strength:.2f}, "
                            f"conf={adjusted.confidence:.2f})"
                        )

                        # Take first matching signal per asset
                        break

                except Exception as e:
                    logger.warning(f"{asset.symbol}: {signal_name} scan failed: {e}")

        # Sort by strength * confidence
        all_signals.sort(key=lambda s: s.strength * s.confidence, reverse=True)

        logger.info(f"Found {len(all_signals)} signals across {len(assets)} assets")

        return all_signals

    def _get_allowed_signals(self, regime: str) -> List[str]:
        """
        Get allowed signals for regime.

        Args:
            regime: Market regime

        Returns:
            List of signal names allowed in this regime
        """
        regime_settings = self.regime_config.get(regime, {})
        allowed = regime_settings.get("allowed_signals", [])

        # Filter to only signals we have loaded
        return [s for s in allowed if s in self.signals]

    def _apply_regime_adjustment(
        self,
        signal: TriggerSignal,
        regime: str
    ) -> TriggerSignal:
        """
        Apply regime-specific confidence adjustments.

        Args:
            signal: Original signal
            regime: Current regime

        Returns:
            Adjusted signal (new instance)
        """
        regime_settings = self.regime_config.get(regime, {})

        # Get confidence boost/penalty
        boost = regime_settings.get("signal_confidence_boost", 0.0)
        penalty = regime_settings.get("signal_confidence_penalty", 0.0)

        adjustment = boost - penalty

        if adjustment != 0:
            # Create adjusted signal
            new_confidence = max(0.0, min(1.0, signal.confidence + adjustment))

            # Create new instance with adjusted confidence
            adjusted = TriggerSignal(
                symbol=signal.symbol,
                trigger_type=signal.trigger_type,
                strength=signal.strength,
                confidence=new_confidence,
                reason=signal.reason + f" (regime {regime}: {adjustment:+.2f})",
                timestamp=signal.timestamp,
                current_price=signal.current_price,
                volume_ratio=signal.volume_ratio,
                price_change_pct=signal.price_change_pct,
                volatility=signal.volatility,
                metrics=signal.metrics
            )

            return adjusted

        return signal

    def get_signal_stats(self) -> Dict:
        """
        Get statistics about loaded signals.

        Returns:
            Dict with signal counts and configurations
        """
        return {
            "loaded_signals": list(self.signals.keys()),
            "total_signals": len(self.signals),
            "regime_filters": {
                regime: settings.get("allowed_signals", [])
                for regime, settings in self.regime_config.items()
                if regime != "enabled"
            }
        }
