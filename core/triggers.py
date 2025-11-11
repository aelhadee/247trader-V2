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
    
    def __init__(self):
        self.exchange = get_exchange()
        logger.info("Initialized TriggerEngine")
    
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
    
    def _check_volume_spike(self, asset: UniverseAsset, 
                           candles: List[OHLCV]) -> Optional[TriggerSignal]:
        """
        Check for volume spike (current volume vs average).
        
        Signal strength = volume ratio - 1.0
        """
        if len(candles) < 24:
            return None
        
        # Current volume (last candle)
        current_volume = candles[-1].volume
        
        # Average volume (24 hours ago to 7 days ago)
        avg_volume = sum(c.volume for c in candles[-168:-24]) / 144
        
        if avg_volume == 0:
            return None
        
        volume_ratio = current_volume / avg_volume
        
        # Lowered threshold: 1.3x spike (aggressive to ensure we get triggers)
        if volume_ratio < 1.3:
            return None
        
        # Strength: 1.3x = 0.1, 2.0x = 0.4, 3.0x = 0.6, 4.0x+ = 1.0
        strength = min((volume_ratio - 1.0) / 3.0, 1.0)
        
        # Confidence: Higher for bigger spikes
        confidence = min(volume_ratio / 4.0, 1.0)
        
        return TriggerSignal(
            symbol=asset.symbol,
            trigger_type="volume_spike",
            strength=strength,
            confidence=confidence,
            reason=f"Volume {volume_ratio:.2f}x average (${current_volume:,.0f} vs ${avg_volume:,.0f})",
            timestamp=datetime.utcnow(),
            current_price=candles[-1].close,
            volume_ratio=volume_ratio
        )
    
    def _check_breakout(self, asset: UniverseAsset, 
                       candles: List[OHLCV]) -> Optional[TriggerSignal]:
        """
        Check for price breakout (new high or recovering from low).
        """
        # Changed to 24h lookback (was 7d - align with trading_parameters.md)
        if len(candles) < 24:
            return None
        
        current_price = candles[-1].close
        
        # Get 24-hour range (was 7-day)
        high_24h = max(c.high for c in candles[-24:])
        low_24h = min(c.low for c in candles[-24:])
        range_24h = high_24h - low_24h
        
        if range_24h == 0:
            return None
        
        # Check if breaking to new high
        if current_price >= high_24h * 0.995:  # Within 0.5% of high
            strength = 0.7
            confidence = 0.8
            reason = f"Breaking 24h high (${current_price:,.2f} near ${high_24h:,.2f})"
            
            return TriggerSignal(
                symbol=asset.symbol,
                trigger_type="breakout",
                strength=strength,
                confidence=confidence,
                reason=reason,
                timestamp=datetime.utcnow(),
                current_price=current_price,
                price_change_pct=(current_price - low_24h) / low_24h * 100
            )
        
        # Check if recovering from low (V-shape)
        if current_price <= low_24h * 1.10:  # Within 10% of low
            # Check if we bounced at least 5% from the low
            recovery_pct = (current_price - low_24h) / low_24h
            if recovery_pct > 0.05:
                strength = min(recovery_pct / 0.20, 1.0)  # 20% recovery = max strength
                confidence = 0.6
                reason = f"Recovering from 24h low (+{recovery_pct*100:.1f}% from ${low_24h:,.2f})"
                
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
