"""
247trader-v2 Core: Triggers

Deterministic signal detection (NO AI).
Decides which assets warrant deeper analysis.

Inspired by Jesse's clean strategy lifecycle and Freqtrade's indicator patterns.
"""

from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging

from core.exchange_coinbase import get_exchange, OHLCV
from core.universe import UniverseAsset

logger = logging.getLogger(__name__)


@dataclass
class TriggerSignal:
    """A trigger signal for an asset"""
    symbol: str
    trigger_type: str  # "volume_spike" | "breakout" | "reversal" | "momentum"
    strength: float  # 0.0 to 1.0
    confidence: float  # 0.0 to 1.0
    reason: str
    timestamp: datetime
    
    # Supporting data
    current_price: float
    volume_ratio: Optional[float] = None
    price_change_pct: Optional[float] = None
    technical_score: Optional[float] = None
    volatility: Optional[float] = None  # Annualized volatility for sizing
    qualifiers: Dict[str, bool] = field(default_factory=dict)
    metrics: Dict[str, float] = field(default_factory=dict)


class TriggerEngine:
    """
    Deterministic trigger detection.
    
    Rules-based only. No AI. No magic.
    Pattern: Jesse-style pure functions + Freqtrade-style indicators
    
    Responsibilities:
    - Detect volume spikes
    - Detect breakouts (price action)
    - Detect reversals
    - Compute momentum scores
    
    Output: Ranked list of assets with trigger signals
    """
    
    def __init__(self, config_path: str = "config/signals.yaml", policy_path: str = "config/policy.yaml"):
        self.exchange = get_exchange()
        
        import yaml
        from pathlib import Path
        
        # Load legacy signals.yaml configuration
        try:
            with open(Path(config_path)) as f:
                self.config = yaml.safe_load(f).get("triggers", {})
        except Exception as e:
            logger.warning(f"Could not load signals config: {e}, using defaults")
            self.config = {}
        
        # Load policy.yaml triggers section (spec-compliant)
        try:
            with open(Path(policy_path)) as f:
                policy_config = yaml.safe_load(f)
                self.policy_triggers = policy_config.get("triggers", {})
                self.circuit_breakers = policy_config.get("circuit_breakers", {})
        except Exception as e:
            logger.warning(f"Could not load policy triggers: {e}, using defaults")
            self.policy_triggers = {}
            self.circuit_breakers = {}
        
        # Extract regime-aware thresholds (from signals.yaml)
        self.regime_thresholds = self.config.get("regime_thresholds", {
            "chop": {
                "pct_change_15m": 2.0,
                "pct_change_60m": 4.0,
                "volume_ratio_1h": 1.9,
                "atr_filter_min_mult": 1.1
            },
            "bull": {
                "pct_change_15m": 3.5,
                "pct_change_60m": 7.0,
                "volume_ratio_1h": 2.0,
                "atr_filter_min_mult": 1.2
            },
            "bear": {
                "pct_change_15m": 3.0,
                "pct_change_60m": 7.0,
                "volume_ratio_1h": 2.0,
                "atr_filter_min_mult": 1.2
            }
        })

        # Confirmation rules for reversal triggers (Option A tuning)
        self.reversal_confirm_config = self.config.get("reversal_confirm", {})
        self.trend_filter_config = self.config.get("trend_filter", {})
        
        # Fallback to policy.yaml if signals.yaml doesn't have regime thresholds (backward compat)
        price_move_config = self.policy_triggers.get("price_move", {})
        self.pct_15m = price_move_config.get("pct_15m", 2.5)  # Fallback default
        self.pct_60m = price_move_config.get("pct_60m", 4.5)  # Fallback default
        
        volume_spike_config = self.policy_triggers.get("volume_spike", {})
        self.ratio_1h_vs_24h = volume_spike_config.get("ratio_1h_vs_24h", 1.9)
        
        breakout_config = self.policy_triggers.get("breakout", {})
        self.lookback_hours = breakout_config.get("lookback_hours", 24)
        
        self.min_score = self.policy_triggers.get("min_score", 0.2)
        
        # Legacy parameters (backward compatibility with signals.yaml)
        self.volume_spike_min_ratio = self.config.get("volume_spike_min_ratio", 1.5)
        self.volume_lookback_periods = self.config.get("volume_lookback_periods", 24)
        self.breakout_lookback_bars = self.config.get("breakout_lookback_bars", 24)
        self.breakout_threshold_pct = self.config.get("breakout_threshold_pct", 2.0)
        self.min_trigger_score = self.config.get("min_trigger_score", 0.2)
        self.min_trigger_confidence = self.config.get("min_trigger_confidence", 0.5)
        self.max_triggers_per_cycle = self.config.get("max_triggers_per_cycle", 5)
        self.regime_multipliers = self.config.get("regime_multipliers", {
            "bull": 1.2, "chop": 1.0, "bear": 0.8, "crash": 0.0
        })
        
        # ATR filter parameters
        self.enable_atr_filter = self.circuit_breakers.get("enable_atr_filter", True)
        self.atr_lookback = self.circuit_breakers.get("atr_lookback_periods", 14)
        self.atr_min_multiplier = self.circuit_breakers.get("atr_min_multiplier", 1.2)  # Must be 1.2x median

        # Direction filter (for long-only strategies)
        self.only_upside = self.config.get("only_upside", self.policy_triggers.get("only_upside", False))

        # Fallback configuration (policy overrides signals.yaml defaults)
        self.fallback_config = self.policy_triggers.get("fallback", {}) or {}
        self._no_trigger_streak = 0
        
        logger.info(f"Initialized TriggerEngine (regime_aware={bool(self.regime_thresholds)}, "
                   f"lookback={self.lookback_hours}h, atr_filter={self.enable_atr_filter}, "
                   f"only_upside={self.only_upside}, max_triggers={self.max_triggers_per_cycle})")
        logger.info(f"  Chop thresholds: 15m={self.regime_thresholds['chop']['pct_change_15m']}%, "
                   f"60m={self.regime_thresholds['chop']['pct_change_60m']}%, "
                   f"vol={self.regime_thresholds['chop']['volume_ratio_1h']}x, "
                   f"atr={self.regime_thresholds['chop']['atr_filter_min_mult']}x")
    
    def scan(self, assets: List[UniverseAsset], 
             regime: str = "chop") -> List[TriggerSignal]:
        """
        Scan eligible assets for triggers.
        
        Args:
            assets: List of eligible universe assets
            regime: Current market regime
            
        Returns:
            List of TriggerSignals, sorted by strength
        """
        logger.info(f"Scanning {len(assets)} assets for triggers (regime={regime})")
        
        signals = []
        asset_contexts: List[Tuple[UniverseAsset, List[OHLCV]]] = []
        
        for asset in assets:
            try:
                # Get OHLCV data
                candles = self.exchange.get_ohlcv(asset.symbol, interval="1h", limit=168)  # 7 days
                
                if not candles:
                    continue
                
                # Validate price data for outliers (bad ticks, flash crashes/spikes)
                outlier_reason = self._validate_price_outlier(asset.symbol, candles)
                if outlier_reason:
                    logger.warning(f"{asset.symbol}: {outlier_reason}")
                    continue  # Skip this asset for this cycle
                
                # Check ATR volatility filter (skip low-volatility chop)
                atr_reason = self._check_atr_filter(asset.symbol, candles, regime)
                if atr_reason:
                    logger.debug(f"{asset.symbol}: {atr_reason}")
                    continue  # Skip this asset for this cycle
                
                asset_contexts.append((asset, candles))

                # Check various trigger types
                triggers = []
                
                # Price move (regime-aware thresholds)
                price_move_trigger = self._check_price_move(asset, candles, regime)
                if price_move_trigger:
                    triggers.append(price_move_trigger)
                
                # Volume spike (regime-aware threshold)
                vol_trigger = self._check_volume_spike(asset, candles, regime)
                if vol_trigger:
                    triggers.append(vol_trigger)
                
                # Breakout
                breakout_trigger = self._check_breakout(asset, candles, regime)
                if breakout_trigger:
                    triggers.append(breakout_trigger)
                
                # Momentum
                momentum_trigger = self._check_momentum(asset, candles, regime)
                if momentum_trigger:
                    triggers.append(momentum_trigger)
                
                # Take strongest trigger
                if triggers:
                    strongest = max(triggers, key=lambda t: t.strength * t.confidence)
                    signals.append(strongest)
                    logger.debug(
                        f"{asset.symbol}: {strongest.trigger_type} "
                        f"(strength={strongest.strength:.2f}, conf={strongest.confidence:.2f})"
                    )
                
            except Exception as e:
                logger.warning(f"Failed to scan {asset.symbol}: {e}")
        
        if not signals:
            signals = self._maybe_run_fallback_scan(asset_contexts, regime)
        else:
            self._no_trigger_streak = 0

        # Sort by strength × confidence
        signals.sort(key=lambda s: s.strength * s.confidence, reverse=True)
        
        logger.info(f"Found {len(signals)} triggers")
        
        # Log top 5 triggers for visibility
        for i, sig in enumerate(signals[:5]):
            logger.info(
                f"  Trigger #{i+1}: {sig.symbol} {sig.trigger_type} "
                f"strength={sig.strength:.2f} conf={sig.confidence:.2f} "
                f"price_chg={sig.price_change_pct:.2f}% vol_ratio={sig.volume_ratio:.2f}x"
                if sig.volume_ratio else
                f"  Trigger #{i+1}: {sig.symbol} {sig.trigger_type} "
                f"strength={sig.strength:.2f} conf={sig.confidence:.2f} "
                f"price_chg={sig.price_change_pct or 0.0:.2f}%"
            )
        
        return signals

    def _maybe_run_fallback_scan(
        self,
        asset_contexts: List[Tuple[UniverseAsset, List[OHLCV]]],
        regime: str,
    ) -> List[TriggerSignal]:
        """Optionally run relaxed scan when primary pass returns nothing."""

        if not asset_contexts:
            self._no_trigger_streak = 0
            return []

        fallback_cfg = self.fallback_config
        enabled = bool(fallback_cfg.get("enabled", True))
        if not enabled:
            self._no_trigger_streak = 0
            return []

        consecutive = self._no_trigger_streak
        min_streak = int(fallback_cfg.get("min_no_trigger_streak", 1) or 0)

        if consecutive < min_streak:
            self._no_trigger_streak = consecutive + 1
            return []

        relax_pct = float(fallback_cfg.get("relax_pct", 0.30) or 0.0)
        relax_pct = min(max(relax_pct, 0.0), 0.9)
        max_new = int(fallback_cfg.get("max_new_positions_per_cycle", 1) or 1)
        allow_downside = bool(fallback_cfg.get("allow_downside", True))

        fallback_signals: List[TriggerSignal] = []
        prev_only_upside = self.only_upside
        if allow_downside:
            self.only_upside = False

        try:
            regime_key = regime if regime in self.regime_thresholds else "chop"
            base_15m = self.regime_thresholds[regime_key].get("pct_change_15m", self.pct_15m)
            base_60m = self.regime_thresholds[regime_key].get("pct_change_60m", self.pct_60m)
            relaxed_thresholds = (
                max(base_15m * (1.0 - relax_pct), 0.0),
                max(base_60m * (1.0 - relax_pct), 0.0),
            )

            for asset, candles in asset_contexts:
                signal = self._check_price_move(
                    asset,
                    candles,
                    regime,
                    threshold_override=relaxed_thresholds,
                    reason_suffix="[fallback relaxed scan]",
                )
                if signal:
                    fallback_signals.append(signal)

        finally:
            self.only_upside = prev_only_upside

        if not fallback_signals:
            self._no_trigger_streak = consecutive + 1
            return []

        fallback_signals.sort(key=lambda s: s.strength * s.confidence, reverse=True)
        limited = fallback_signals[:max_new]
        logger.info(
            "Fallback scan enabled: relaxed thresholds produced %d trigger(s) after %d empty cycle(s)",
            len(limited),
            consecutive,
        )
        self._no_trigger_streak = 0
        return limited
    
    def _validate_price_outlier(self, symbol: str, candles: List[OHLCV]) -> Optional[str]:
        """
        Validate price data for outliers (bad ticks, flash crashes/spikes).
        
        Returns rejection reason if outlier detected, None if valid.
        
        Guards against:
        - Price deviation >max_price_deviation_pct from moving average (default 10%)
        - Without volume confirmation (min_volume_ratio, default 0.1 = 10%)
        
        Per policy.yaml circuit_breakers:
        - check_price_outliers: enable/disable
        - max_price_deviation_pct: maximum allowed deviation from MA
        - min_volume_ratio: required volume ratio for extreme moves
        - outlier_lookback_periods: periods for moving average (default 20)
        """
        # Check if outlier detection is enabled
        if not self.circuit_breakers.get("check_price_outliers", True):
            return None  # Outlier detection disabled
        
        max_dev_pct = self.circuit_breakers.get("max_price_deviation_pct", 10.0)
        min_vol_ratio = self.circuit_breakers.get("min_volume_ratio", 0.1)
        lookback = self.circuit_breakers.get("outlier_lookback_periods", 20)
        
        # Need at least lookback periods + 1 for validation
        if len(candles) < lookback + 1:
            return None  # Insufficient data, skip validation
        
        current = candles[-1]
        historical = candles[-(lookback+1):-1]  # Last N periods, excluding current
        
        # Calculate moving average of close prices
        avg_price = sum(c.close for c in historical) / len(historical)
        
        # Calculate deviation
        if avg_price <= 0:
            return f"Invalid average price: {avg_price}"
        
        deviation_pct = abs(current.close - avg_price) / avg_price * 100.0
        
        # If deviation exceeds threshold, check volume confirmation
        if deviation_pct > max_dev_pct:
            # Calculate average volume
            avg_volume = sum(c.volume for c in historical) / len(historical)
            
            if avg_volume <= 0:
                return f"Invalid average volume: {avg_volume}"
            
            volume_ratio = current.volume / avg_volume
            
            # Extreme move without volume confirmation = outlier
            if volume_ratio < min_vol_ratio:
                return (
                    f"Price outlier: {deviation_pct:.1f}% deviation "
                    f"(>{max_dev_pct}%) with low volume "
                    f"({volume_ratio:.2f}x < {min_vol_ratio}x)"
                )
        
        return None  # Valid price data
    
    def _calculate_atr_pct(self, candles: List[OHLCV], period: int = 14) -> float:
        """
        Calculate Average True Range as percentage of price.
        
        ATR measures volatility. Low ATR = tight range = chop.
        
        Args:
            candles: OHLCV data
            period: Lookback period (default 14)
            
        Returns:
            ATR as percentage of current price
        """
        if len(candles) < period + 1:
            return 0.0
        
        true_ranges = []
        for i in range(-period, 0):
            high = candles[i].high
            low = candles[i].low
            prev_close = candles[i-1].close
            
            # True Range = max of:
            # 1) Current High - Current Low
            # 2) |Current High - Previous Close|
            # 3) |Current Low - Previous Close|
            tr = max(
                high - low,
                abs(high - prev_close),
                abs(low - prev_close)
            )
            true_ranges.append(tr)
        
        # Average True Range
        atr = sum(true_ranges) / len(true_ranges)
        
        # Express as percentage of current price
        current_price = candles[-1].close
        if current_price <= 0:
            return 0.0
        
        atr_pct = (atr / current_price) * 100.0
        return atr_pct
    
    def _check_atr_filter(self, symbol: str, candles: List[OHLCV], regime: str = "chop") -> Optional[str]:
        """
        Check if asset passes ATR volatility filter (regime-aware).
        
        Returns rejection reason if fails, None if passes.
        
        Filters out low-volatility chop/tight ranges where signals
        tend to be false positives.
        
        Uses regime-specific atr_filter_min_mult from signals.yaml regime_thresholds.
        """
        if not self.enable_atr_filter:
            return None  # Filter disabled
        
        # Need sufficient data
        if len(candles) < self.atr_lookback * 2:
            return None  # Insufficient data, skip filter
        
        # Get regime-specific ATR multiplier
        regime_key = regime if regime in self.regime_thresholds else "chop"
        atr_min_mult = self.regime_thresholds[regime_key].get("atr_filter_min_mult", 1.1)
        
        # Calculate current ATR
        current_atr_pct = self._calculate_atr_pct(candles, self.atr_lookback)
        
        # Calculate median ATR over last 7 days (168 hours)
        atr_samples = []
        for i in range(-168, -self.atr_lookback):
            if i + self.atr_lookback < 0:
                # Calculate ATR for this window
                window = candles[i:i+self.atr_lookback+1]
                if len(window) >= self.atr_lookback + 1:
                    atr_pct = self._calculate_atr_pct(window, self.atr_lookback)
                    atr_samples.append(atr_pct)
        
        if not atr_samples:
            return None  # Can't calculate median
        
        # Calculate median
        sorted_samples = sorted(atr_samples)
        median_atr_pct = sorted_samples[len(sorted_samples) // 2]
        
        # Check if current ATR meets minimum threshold
        if median_atr_pct <= 0:
            return None  # Invalid median
        
        atr_ratio = current_atr_pct / median_atr_pct
        
        if atr_ratio < atr_min_mult:
            return (
                f"Low volatility: ATR {current_atr_pct:.2f}% "
                f"({atr_ratio:.2f}x median {median_atr_pct:.2f}%, "
                f"need {atr_min_mult:.1f}x for {regime})"
            )
        
        return None  # Passes filter
    
    def _calculate_volatility(self, candles: List[OHLCV]) -> float:
        """
        Calculate annualized volatility from hourly returns.
        
        Returns volatility as percentage (e.g., 50.0 for 50% annualized).
        """
        if len(candles) < 24:
            return 50.0  # Default moderate volatility
        
        # Calculate hourly returns for last 7 days (168 hours)
        returns = []
        lookback = min(168, len(candles) - 1)
        for i in range(len(candles) - lookback, len(candles)):
            if candles[i-1].close > 0:
                ret = (candles[i].close - candles[i-1].close) / candles[i-1].close
                returns.append(ret)
        
        if not returns:
            return 50.0
        
        # Standard deviation of returns
        import math
        mean_ret = sum(returns) / len(returns)
        variance = sum((r - mean_ret) ** 2 for r in returns) / len(returns)
        hourly_vol = math.sqrt(variance)
        
        # Annualize: hourly vol × sqrt(24 × 365)
        annualized_vol = hourly_vol * math.sqrt(24 * 365) * 100  # Convert to percentage
        
        return min(annualized_vol, 200.0)  # Cap at 200% to avoid extreme outliers
    
    def _check_price_move(
        self,
        asset: UniverseAsset,
        candles: List[OHLCV],
        regime: str = "chop",
        threshold_override: Optional[Tuple[float, float]] = None,
        reason_suffix: str = "",
    ) -> Optional[TriggerSignal]:
        """
        Check for significant price moves (regime-aware thresholds).
        
        Uses regime-specific pct_change_15m and pct_change_60m from signals.yaml.
        """
        if len(candles) < 60:
            return None
        
        # Get regime-specific thresholds
        regime_key = regime if regime in self.regime_thresholds else "chop"
        pct_15m = self.regime_thresholds[regime_key].get("pct_change_15m", 2.0)
        pct_60m = self.regime_thresholds[regime_key].get("pct_change_60m", 4.0)

        if threshold_override:
            override_15, override_60 = threshold_override
            pct_15m = max(override_15, 0.0)
            pct_60m = max(override_60, 0.0)
        
        current_price = candles[-1].close
        
        # 15-minute move (15 bars ago with 1h candles)
        # Note: With 1h candles, we can't get true 15m moves, so we use closest approximation
        # For now, use 1h candle as proxy for short-term move
        price_1h_ago = candles[-2].close if len(candles) >= 2 else current_price
        move_1h = abs((current_price - price_1h_ago) / price_1h_ago * 100)
        
        # 60-minute move is just the 1h candle move
        price_60m_ago = candles[-2].close if len(candles) >= 2 else current_price
        move_60m = abs((current_price - price_60m_ago) / price_60m_ago * 100)
        
        # For better 15m detection, check last 4h for any large single-hour move
        max_1h_move = 0.0
        if len(candles) >= 5:
            for i in range(1, 5):  # Check last 4 hours
                prev_price = candles[-(i+1)].close
                curr_price = candles[-i].close
                move = abs((curr_price - prev_price) / prev_price * 100)
                max_1h_move = max(max_1h_move, move)
        
        # Check 15m threshold (using max 1h move as proxy)
        triggered_15m = max_1h_move >= pct_15m
        
        # Check 60m threshold  
        triggered_60m = move_60m >= pct_60m
        
        if not (triggered_15m or triggered_60m):
            return None
        
        # Determine which triggered and build reason
        if triggered_15m and triggered_60m:
            reason = f"Price move {max_1h_move:.1f}% (1h) - exceeds both {regime} thresholds"
            strength = 0.8
            confidence = 0.85
            move_pct = max_1h_move
        elif triggered_15m:
            reason = f"Sharp price move {max_1h_move:.1f}% (1h) - exceeds {pct_15m}% ({regime})"
            strength = 0.6
            confidence = 0.7
            move_pct = max_1h_move
        else:  # triggered_60m
            reason = f"Sustained price move {move_60m:.1f}% (60m) - exceeds {pct_60m}% ({regime})"
            strength = 0.7
            confidence = 0.75
            move_pct = move_60m
        
        if reason_suffix:
            reason = f"{reason} {reason_suffix}".strip()

        # Calculate volatility for sizing
        volatility = self._calculate_volatility(candles)
        
        return TriggerSignal(
            symbol=asset.symbol,
            trigger_type="price_move",
            strength=strength,
            confidence=confidence,
            reason=reason,
            timestamp=datetime.now(timezone.utc),
            current_price=current_price,
            price_change_pct=move_pct,
            volatility=volatility
        )
    
    def _check_volume_spike(self, asset: UniverseAsset, 
                           candles: List[OHLCV], regime: str = "chop") -> Optional[TriggerSignal]:
        """
        Check for volume spike (regime-aware threshold).
        
        Uses regime-specific volume_ratio_1h from signals.yaml regime_thresholds.
        
        Also calculates volatility for downstream sizing.
        """
        if len(candles) < 24:
            return None
        
        # Get regime-specific volume threshold
        regime_key = regime if regime in self.regime_thresholds else "chop"
        volume_threshold = self.regime_thresholds[regime_key].get("volume_ratio_1h", 1.9)
        
        # Current 1h volume (last candle)
        current_volume = candles[-1].volume
        
        # Calculate 24h average hourly volume (spec-compliant)
        if len(candles) >= 24:
            # Use last 24 hours for average
            volume_24h = sum(c.volume for c in candles[-24:])
            avg_hourly = volume_24h / 24
        else:
            # Fallback for insufficient data
            avg_hourly = sum(c.volume for c in candles) / len(candles)
        
        if avg_hourly == 0:
            return None
        
        # Calculate ratio: 1h / avg_hourly
        volume_ratio = current_volume / avg_hourly
        
        # Use regime-specific threshold
        if volume_ratio < volume_threshold:
            return None
        
        # Strength: 1.8x = 0.27, 2.0x = 0.33, 3.0x = 0.67, 4.0x+ = 1.0
        strength = min((volume_ratio - 1.0) / 3.0, 1.0)
        
        # Confidence: Higher for bigger spikes
        confidence = min(volume_ratio / 4.0, 1.0)
        
        # Calculate volatility for sizing
        volatility = self._calculate_volatility(candles)
        
        # CRITICAL FIX: Calculate price change for rules engine
        # _rule_volume_spike requires price_change_pct to determine trade direction
        price_change_pct = None
        if len(candles) >= 2:
            prev_close = candles[-2].close
            current_close = candles[-1].close
            if prev_close > 0:
                price_change_pct = ((current_close - prev_close) / prev_close) * 100.0
        
        return TriggerSignal(
            symbol=asset.symbol,
            trigger_type="volume_spike",
            strength=strength,
            confidence=confidence,
            reason=f"Volume {volume_ratio:.2f}x avg hourly (1h: ${current_volume:,.0f} vs 24h avg: ${avg_hourly:,.0f})",
            timestamp=datetime.now(timezone.utc),
            current_price=candles[-1].close,
            volume_ratio=volume_ratio,
            volatility=volatility,
            price_change_pct=price_change_pct
        )
    
    def _check_breakout(self, asset: UniverseAsset,
                       candles: List[OHLCV],
                       regime: str = "chop") -> Optional[TriggerSignal]:
        """
        Check for price breakout (spec: new high/low within lookback period).
        
        Uses policy.yaml triggers.breakout.lookback_hours (default 24)
        """
        # Use spec-compliant lookback from policy.yaml
        lookback = self.lookback_hours
        if len(candles) < lookback:
            return None
        
        current_price = candles[-1].close
        
        # Get high/low over lookback period
        high_lookback = max(c.high for c in candles[-lookback:])
        low_lookback = min(c.low for c in candles[-lookback:])
        range_lookback = high_lookback - low_lookback
        
        if range_lookback == 0:
            return None
        
        # Calculate volatility for sizing
        volatility = self._calculate_volatility(candles)
        
        # Check if breaking to new high
        if current_price >= high_lookback * 0.995:  # Within 0.5% of high
            strength = 0.7
            confidence = 0.8
            reason = f"Breaking {lookback}h high (${current_price:,.2f} near ${high_lookback:,.2f})"
            
            return TriggerSignal(
                symbol=asset.symbol,
                trigger_type="breakout",
                strength=strength,
                confidence=confidence,
                reason=reason,
                timestamp=datetime.now(timezone.utc),
                current_price=current_price,
                price_change_pct=(current_price - low_lookback) / low_lookback * 100,
                volatility=volatility
            )
        
        # Check if recovering from low (V-shape)
        if current_price <= low_lookback * 1.10:  # Within 10% of low
            # Check if we bounced at least 5% from the low
            recovery_pct = (current_price - low_lookback) / low_lookback
            if recovery_pct > 0.05:
                strength = min(recovery_pct / 0.20, 1.0)  # 20% recovery = max strength
                confidence = 0.6
                reason = f"Recovering from {lookback}h low (+{recovery_pct*100:.1f}% from ${low_lookback:,.2f})"
                qualifiers, metrics = self._compute_reversal_confirmations(asset.symbol, candles)
                metrics.setdefault("reversal_recovery_pct", recovery_pct * 100)
                trend_ok, trend_reason, trend_metrics = self._passes_trend_filter(candles, regime)
                metrics.update(trend_metrics)
                if not trend_ok:
                    logger.debug(f"{asset.symbol}: {trend_reason}")
                    return None
                if self.trend_filter_config.get("enabled", False):
                    qualifiers["trend_filter_passed"] = True
                
                return TriggerSignal(
                    symbol=asset.symbol,
                    trigger_type="reversal",
                    strength=strength,
                    confidence=confidence,
                    reason=reason,
                    timestamp=datetime.now(timezone.utc),
                    current_price=current_price,
                    price_change_pct=recovery_pct * 100,
                    volatility=volatility,
                    qualifiers=qualifiers,
                    metrics=metrics
                )
        
        return None
    
    def _compute_reversal_confirmations(self, symbol: str,
                                        candles_1h: List[OHLCV]) -> Tuple[Dict[str, bool], Dict[str, float]]:
        """Evaluate configured reversal confirmation rules for conviction boosts."""
        qualifiers: Dict[str, bool] = {}
        metrics: Dict[str, float] = {}

        if not self.reversal_confirm_config:
            return qualifiers, metrics

        config = self.reversal_confirm_config
        candles_5m: List[OHLCV] = []

        if any(config.get(key) for key in [
            "close_above_vwap_5m",
            "rsi_cross_up_50",
            "min_bounce_from_low_pct"
        ]):
            try:
                candles_5m = self.exchange.get_ohlcv(symbol, interval="5m", limit=60)
            except Exception as exc:  # pragma: no cover - network noise handled upstream
                logger.debug(
                    f"{symbol}: reversal confirmation 5m candles unavailable: {exc}"
                )
                candles_5m = []

        last_close = 0.0
        if candles_5m:
            last_close = candles_5m[-1].close
        elif candles_1h:
            last_close = candles_1h[-1].close

        if config.get("close_above_vwap_5m"):
            vwap = self._calculate_vwap(candles_5m[-12:]) if candles_5m else None
            if vwap:
                qualifiers["reversal_close_above_vwap_5m"] = last_close > vwap
                metrics["reversal_vwap_5m"] = vwap
            else:
                qualifiers["reversal_close_above_vwap_5m"] = False

        if config.get("higher_low_vs_prev"):
            pivot_lows = self._find_recent_pivot_lows(candles_1h)
            if len(pivot_lows) >= 2:
                last_low = pivot_lows[-1]
                prev_low = pivot_lows[-2]
                qualifiers["reversal_higher_low"] = last_low.low > prev_low.low
                metrics["reversal_last_pivot_low"] = last_low.low
                metrics["reversal_prev_pivot_low"] = prev_low.low
            else:
                qualifiers["reversal_higher_low"] = False

        if config.get("rsi_cross_up_50"):
            closes = [c.close for c in candles_5m] if candles_5m else [c.close for c in candles_1h]
            rsi_series = self._calculate_rsi_series(closes, period=14)
            if len(rsi_series) >= 2:
                prev_rsi, curr_rsi = rsi_series[-2], rsi_series[-1]
                qualifiers["reversal_rsi_cross_50"] = prev_rsi <= 50.0 and curr_rsi > 50.0
                metrics["reversal_rsi"] = curr_rsi
                metrics["reversal_rsi_prev"] = prev_rsi
            else:
                qualifiers["reversal_rsi_cross_50"] = False

        if config.get("min_bounce_from_low_pct"):
            threshold = float(config.get("min_bounce_from_low_pct", 0.0))
            recent_candles = candles_5m[-36:] if candles_5m else candles_1h[-12:]
            if recent_candles:
                recent_low = min((c.low for c in recent_candles if c.low > 0), default=0.0)
                if recent_low > 0 and last_close > 0:
                    bounce_pct = (last_close - recent_low) / recent_low * 100.0
                    qualifiers["reversal_bounce_confirmed"] = bounce_pct >= threshold
                    metrics["reversal_bounce_pct"] = bounce_pct
                else:
                    qualifiers["reversal_bounce_confirmed"] = False
            else:
                qualifiers["reversal_bounce_confirmed"] = False

        return qualifiers, metrics

    def _calculate_ema_series(self, values: List[float], period: int) -> List[Optional[float]]:
        if period <= 0:
            return []
        if len(values) < period:
            return []

        multiplier = 2.0 / (period + 1)
        ema_values: List[Optional[float]] = [None] * max(period - 1, 0)
        ema = sum(values[:period]) / period
        ema_values.append(ema)

        for price in values[period:]:
            ema = (price - ema) * multiplier + ema
            ema_values.append(ema)

        return ema_values

    def _passes_trend_filter(self, candles: List[OHLCV], regime: str) -> Tuple[bool, str, Dict[str, float]]:
        config = self.trend_filter_config or {}
        metrics: Dict[str, float] = {}

        if not config.get("enabled", False):
            return True, "", metrics

        period = int(config.get("ema_period_hours", 21))
        slope_lookback = max(1, int(config.get("slope_lookback_hours", 3)))
        slope_cfg = config.get("min_slope_pct_per_hour", 0.0)

        if isinstance(slope_cfg, dict):
            min_slope = float(slope_cfg.get(regime, slope_cfg.get("default", 0.0)))
        else:
            min_slope = float(slope_cfg)

        closes = [c.close for c in candles]
        if len(closes) < period + slope_lookback:
            metrics["trend_filter_passed"] = 0.0
            return False, (
                f"Trend filter: insufficient data for EMA period {period}"
            ), metrics

        ema_series = self._calculate_ema_series(closes, period)
        ema_values = [value for value in ema_series if value is not None]

        if len(ema_values) < slope_lookback + 1:
            metrics["trend_filter_passed"] = 0.0
            return False, (
                f"Trend filter: insufficient EMA samples for slope lookback {slope_lookback}"
            ), metrics

        current_ema = ema_values[-1]
        prior_ema = ema_values[-(slope_lookback + 1)]

        if prior_ema <= 0:
            metrics["trend_filter_passed"] = 0.0
            return False, "Trend filter: invalid prior EMA value", metrics

        slope_pct = ((current_ema - prior_ema) / prior_ema) * 100.0 / slope_lookback

        metrics["trend_filter_ema_period"] = float(period)
        metrics["trend_filter_slope_pct_per_hr"] = slope_pct
        metrics["trend_filter_ema_current"] = current_ema
        metrics["trend_filter_ema_prev"] = prior_ema
        metrics["trend_filter_slope_lookback"] = float(slope_lookback)

        if slope_pct < min_slope:
            reason = (
                f"Trend filter: EMA slope {slope_pct:.3f}%/h < {min_slope:.3f}%/h requirement"
            )
            metrics["trend_filter_passed"] = 0.0
            return False, reason, metrics

        metrics["trend_filter_passed"] = 1.0
        return True, "", metrics

    def _calculate_vwap(self, candles: List[OHLCV]) -> Optional[float]:
        if not candles:
            return None
        numerator = 0.0
        denominator = 0.0
        for candle in candles:
            typical_price = (candle.high + candle.low + candle.close) / 3.0
            numerator += typical_price * candle.volume
            denominator += candle.volume
        if denominator <= 0:
            return None
        return numerator / denominator

    def _find_recent_pivot_lows(self, candles: List[OHLCV], lookback: int = 48,
                                 window: int = 2) -> List[OHLCV]:
        if not candles:
            return []
        pivots: List[OHLCV] = []
        start = max(0, len(candles) - lookback)
        for idx in range(start + window, len(candles) - window):
            low_value = candles[idx].low
            if all(low_value <= candles[idx - offset].low for offset in range(1, window + 1)) and \
               all(low_value < candles[idx + offset].low for offset in range(1, window + 1)):
                pivots.append(candles[idx])
        return pivots[-3:]

    def _calculate_rsi_series(self, closes: List[float], period: int = 14) -> List[float]:
        if len(closes) < period + 1:
            return []

        gains = []
        losses = []
        for i in range(1, len(closes)):
            delta = closes[i] - closes[i - 1]
            gains.append(max(delta, 0.0))
            losses.append(abs(min(delta, 0.0)))

        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period

        rsis: List[float] = []
        if avg_loss == 0:
            rsis.append(100.0)
        else:
            rs = avg_gain / avg_loss if avg_loss else float("inf")
            rsis.append(100.0 - (100.0 / (1.0 + rs)))

        for idx in range(period, len(gains)):
            avg_gain = ((avg_gain * (period - 1)) + gains[idx]) / period
            avg_loss = ((avg_loss * (period - 1)) + losses[idx]) / period
            if avg_loss == 0:
                rsis.append(100.0)
            else:
                rs = avg_gain / avg_loss if avg_loss else float("inf")
                rsis.append(100.0 - (100.0 / (1.0 + rs)))

        return rsis

    def _check_momentum(self, asset: UniverseAsset, candles: List[OHLCV],
                       regime: str) -> Optional[TriggerSignal]:
        """
        Check for sustained momentum (trending price).
        
        Uses simple slope of 24h returns.
        """
        if len(candles) < 24:
            return None
        
        # Calculate 24h return
        price_24h_ago = candles[-24].close
        current_price = candles[-1].close
        return_24h = (current_price - price_24h_ago) / price_24h_ago
        
        # Lowered threshold: 2% move (aggressive to ensure triggers fire)
        if abs(return_24h) < 0.02:
            return None
        
        # Direction filter for long-only strategies
        if self.only_upside and return_24h < 0:
            return None  # Skip downward momentum if only_upside=true
        
        # In bear/crash, only flag downward momentum
        if regime in ["bear", "crash"] and return_24h > 0:
            return None
        
        # Strength = magnitude of return
        strength = min(abs(return_24h) / 0.10, 1.0)  # 10% return = max strength
        
        # Confidence = consistency (check if all recent candles moved same direction)
        recent_returns = [
            (candles[i].close - candles[i-1].close) / candles[i-1].close
            for i in range(-12, 0)  # Last 12 hours
        ]
        same_direction = sum(1 for r in recent_returns if (r > 0) == (return_24h > 0))
        confidence = same_direction / len(recent_returns)
        
        direction = "up" if return_24h > 0 else "down"
        
        # Calculate volatility for sizing
        volatility = self._calculate_volatility(candles)
        
        return TriggerSignal(
            symbol=asset.symbol,
            trigger_type="momentum",
            strength=strength,
            confidence=confidence,
            reason=f"Momentum {direction} ({return_24h*100:+.1f}% in 24h)",
            timestamp=datetime.now(timezone.utc),
            current_price=current_price,
            price_change_pct=return_24h * 100,
            technical_score=strength * confidence,
            volatility=volatility
        )
    
    def filter_by_threshold(self, signals: List[TriggerSignal],
                           min_strength: float = 0.3,
                           min_confidence: float = 0.5) -> List[TriggerSignal]:
        """
        Filter signals by minimum thresholds.
        
        Args:
            signals: List of trigger signals
            min_strength: Minimum signal strength
            min_confidence: Minimum confidence
            
        Returns:
            Filtered list
        """
        filtered = [
            s for s in signals
            if s.strength >= min_strength and s.confidence >= min_confidence
        ]
        
        logger.info(
            f"Filtered {len(signals)} signals -> {len(filtered)} "
            f"(min_strength={min_strength}, min_confidence={min_confidence})"
        )
        
        return filtered
