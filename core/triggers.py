"""
247trader-v2 Core: Triggers

Deterministic signal detection (NO AI).
Decides which assets warrant deeper analysis.

Inspired by Jesse's clean strategy lifecycle and Freqtrade's indicator patterns.
"""

from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime
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
        except Exception as e:
            logger.warning(f"Could not load policy triggers: {e}, using defaults")
            self.policy_triggers = {}
        
        # Extract spec-compliant parameters (from policy.yaml)
        price_move_config = self.policy_triggers.get("price_move", {})
        self.pct_15m = price_move_config.get("pct_15m", 3.5)
        self.pct_60m = price_move_config.get("pct_60m", 6.0)
        
        volume_spike_config = self.policy_triggers.get("volume_spike", {})
        self.ratio_1h_vs_24h = volume_spike_config.get("ratio_1h_vs_24h", 1.8)
        
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
        self.max_triggers_per_cycle = self.config.get("max_triggers_per_cycle", 10)
        self.regime_multipliers = self.config.get("regime_multipliers", {
            "bull": 1.2, "chop": 1.0, "bear": 0.8, "crash": 0.0
        })
        
        logger.info(f"Initialized TriggerEngine (pct_15m={self.pct_15m}%, pct_60m={self.pct_60m}%, "
                   f"vol_ratio_1h={self.ratio_1h_vs_24h}x, lookback={self.lookback_hours}h)")
    
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
        
        for asset in assets:
            try:
                # Get OHLCV data
                candles = self.exchange.get_ohlcv(asset.symbol, interval="1h", limit=168)  # 7 days
                
                if not candles:
                    continue
                
                # Check various trigger types
                triggers = []
                
                # Price move (spec-compliant: 15m/60m thresholds)
                price_move_trigger = self._check_price_move(asset, candles)
                if price_move_trigger:
                    triggers.append(price_move_trigger)
                
                # Volume spike
                vol_trigger = self._check_volume_spike(asset, candles)
                if vol_trigger:
                    triggers.append(vol_trigger)
                
                # Breakout
                breakout_trigger = self._check_breakout(asset, candles)
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
        
        # Sort by strength × confidence
        signals.sort(key=lambda s: s.strength * s.confidence, reverse=True)
        
        logger.info(f"Found {len(signals)} triggers")
        return signals
    
    def _check_price_move(self, asset: UniverseAsset, 
                          candles: List[OHLCV]) -> Optional[TriggerSignal]:
        """
        Check for significant price moves (spec-compliant: 15m/60m thresholds).
        
        Triggers on:
        - |Δprice_15m| >= pct_15m (default 3.5%)
        - |Δprice_60m| >= pct_60m (default 6.0%)
        """
        if len(candles) < 60:
            return None
        
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
        triggered_15m = max_1h_move >= self.pct_15m
        
        # Check 60m threshold  
        triggered_60m = move_60m >= self.pct_60m
        
        if not (triggered_15m or triggered_60m):
            return None
        
        # Determine which triggered and build reason
        if triggered_15m and triggered_60m:
            reason = f"Price move {max_1h_move:.1f}% (1h) - exceeds both thresholds"
            strength = 0.8
            confidence = 0.85
            move_pct = max_1h_move
        elif triggered_15m:
            reason = f"Sharp price move {max_1h_move:.1f}% (1h) - exceeds {self.pct_15m}% threshold"
            strength = 0.6
            confidence = 0.7
            move_pct = max_1h_move
        else:  # triggered_60m
            reason = f"Sustained price move {move_60m:.1f}% (60m) - exceeds {self.pct_60m}% threshold"
            strength = 0.7
            confidence = 0.75
            move_pct = move_60m
        
        return TriggerSignal(
            symbol=asset.symbol,
            trigger_type="price_move",
            strength=strength,
            confidence=confidence,
            reason=reason,
            timestamp=datetime.utcnow(),
            current_price=current_price,
            price_change_pct=move_pct
        )
    
    def _check_volume_spike(self, asset: UniverseAsset, 
                           candles: List[OHLCV]) -> Optional[TriggerSignal]:
        """
        Check for volume spike (spec: 1h volume vs 24h average).
        
        Uses policy.yaml triggers.volume_spike.ratio_1h_vs_24h (default 1.8x)
        """
        if len(candles) < 24:
            return None
        
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
        
        # Calculate ratio: 1h / avg_hourly (spec: ratio_1h_vs_24h)
        volume_ratio = current_volume / avg_hourly
        
        # Use spec-compliant threshold from policy.yaml (default 1.8x)
        if volume_ratio < self.ratio_1h_vs_24h:
            return None
        
        # Strength: 1.8x = 0.27, 2.0x = 0.33, 3.0x = 0.67, 4.0x+ = 1.0
        strength = min((volume_ratio - 1.0) / 3.0, 1.0)
        
        # Confidence: Higher for bigger spikes
        confidence = min(volume_ratio / 4.0, 1.0)
        
        return TriggerSignal(
            symbol=asset.symbol,
            trigger_type="volume_spike",
            strength=strength,
            confidence=confidence,
            reason=f"Volume {volume_ratio:.2f}x avg hourly (1h: ${current_volume:,.0f} vs 24h avg: ${avg_hourly:,.0f})",
            timestamp=datetime.utcnow(),
            current_price=candles[-1].close,
            volume_ratio=volume_ratio
        )
    
    def _check_breakout(self, asset: UniverseAsset, 
                       candles: List[OHLCV]) -> Optional[TriggerSignal]:
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
                timestamp=datetime.utcnow(),
                current_price=current_price,
                price_change_pct=(current_price - low_lookback) / low_lookback * 100
            )
        
        # Check if recovering from low (V-shape)
        if current_price <= low_lookback * 1.10:  # Within 10% of low
            # Check if we bounced at least 5% from the low
            recovery_pct = (current_price - low_lookback) / low_lookback
            if recovery_pct > 0.05:
                strength = min(recovery_pct / 0.20, 1.0)  # 20% recovery = max strength
                confidence = 0.6
                reason = f"Recovering from {lookback}h low (+{recovery_pct*100:.1f}% from ${low_lookback:,.2f})"
                
                return TriggerSignal(
                    symbol=asset.symbol,
                    trigger_type="reversal",
                    strength=strength,
                    confidence=confidence,
                    reason=reason,
                    timestamp=datetime.utcnow(),
                    current_price=current_price,
                    price_change_pct=recovery_pct * 100
                )
        
        return None
    
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
        
        return TriggerSignal(
            symbol=asset.symbol,
            trigger_type="momentum",
            strength=strength,
            confidence=confidence,
            reason=f"Momentum {direction} ({return_24h*100:+.1f}% in 24h)",
            timestamp=datetime.utcnow(),
            current_price=current_price,
            price_change_pct=return_24h * 100,
            technical_score=strength * confidence
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
