"""
247trader-v2 Strategy: Modular Signals

Extracted signal classes for clean separation of concerns.
Each signal type has its own class with scan()/strength()/confidence() methods.

Pattern: Strategy pattern + Builder pattern for signal composition
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime, timezone
import logging

from core.exchange_coinbase import OHLCV
from core.universe import UniverseAsset
from core.triggers import TriggerSignal

logger = logging.getLogger(__name__)


class BaseSignal(ABC):
    """
    Base class for all signal types.
    
    Each signal implements:
    - scan(): Check if signal is present
    - strength(): Signal strength (0.0-1.0)
    - confidence(): Confidence in signal (0.0-1.0)
    """
    
    def __init__(self, config: Dict):
        self.config = config
        self.name = self.__class__.__name__
    
    @abstractmethod
    def scan(
        self,
        asset: UniverseAsset,
        candles: List[OHLCV],
        regime: str
    ) -> Optional[TriggerSignal]:
        """
        Scan asset for signal.
        
        Args:
            asset: Asset to scan
            candles: Historical OHLCV data
            regime: Market regime (chop/bull/bear)
            
        Returns:
            TriggerSignal if detected, None otherwise
        """
        pass
    
    @abstractmethod
    def strength(self, candles: List[OHLCV], regime: str) -> float:
        """
        Calculate signal strength.
        
        Returns:
            Float 0.0 to 1.0 (1.0 = strongest)
        """
        pass
    
    @abstractmethod
    def confidence(self, candles: List[OHLCV], regime: str) -> float:
        """
        Calculate confidence in signal.
        
        Returns:
            Float 0.0 to 1.0 (1.0 = highest confidence)
        """
        pass


class PriceMoveSignal(BaseSignal):
    """
    Volume spike + price move signal.
    
    Detects:
    - Significant price movement (2-5% depending on regime)
    - Above-average volume confirmation
    - Short-term momentum
    
    This is the current primary signal in triggers.py.
    """
    
    def scan(
        self,
        asset: UniverseAsset,
        candles: List[OHLCV],
        regime: str
    ) -> Optional[TriggerSignal]:
        """Scan for volume spike + price move"""
        
        if len(candles) < 96:  # Need 24 hours of 15min data
            return None
        
        # Get regime thresholds
        thresholds = self._get_thresholds(regime)
        
        # Current price
        current = candles[-1]
        current_price = current.close
        
        # Price change over last 15 minutes (1 candle)
        price_15m_ago = candles[-2].close if len(candles) >= 2 else current.open
        pct_change_15m = ((current_price - price_15m_ago) / price_15m_ago) * 100
        
        # Price change over last 60 minutes (4 candles)
        price_60m_ago = candles[-5].close if len(candles) >= 5 else candles[0].open
        pct_change_60m = ((current_price - price_60m_ago) / price_60m_ago) * 100
        
        # Volume ratio (current vs 24h average)
        recent_volume = sum(c.volume for c in candles[-4:])  # Last hour
        avg_volume = sum(c.volume for c in candles[-96:]) / 96.0
        volume_ratio = recent_volume / (avg_volume * 4) if avg_volume > 0 else 1.0
        
        # Check thresholds
        price_move_threshold = thresholds["pct_change_15m"]
        volume_threshold = thresholds["volume_ratio_1h"]
        
        if abs(pct_change_15m) >= price_move_threshold and volume_ratio >= volume_threshold:
            strength = self.strength(candles, regime)
            confidence = self.confidence(candles, regime)
            
            return TriggerSignal(
                symbol=asset.symbol,
                trigger_type="price_move",
                strength=strength,
                confidence=confidence,
                reason=f"{pct_change_15m:+.1f}% move with {volume_ratio:.1f}x volume",
                timestamp=datetime.now(timezone.utc),
                current_price=current_price,
                volume_ratio=volume_ratio,
                price_change_pct=pct_change_15m,
                volatility=self._calculate_volatility(candles),
                metrics={
                    "pct_change_15m": pct_change_15m,
                    "pct_change_60m": pct_change_60m,
                    "volume_ratio": volume_ratio,
                }
            )
        
        return None
    
    def strength(self, candles: List[OHLCV], regime: str) -> float:
        """Calculate strength based on price momentum"""
        if len(candles) < 5:
            return 0.5
        
        current = candles[-1].close
        price_5_ago = candles[-6].close if len(candles) >= 6 else candles[0].open
        
        # Stronger signal = larger move
        pct_change = abs((current - price_5_ago) / price_5_ago) * 100
        
        # Normalize to 0-1 (5% move = 1.0 strength)
        strength = min(pct_change / 5.0, 1.0)
        
        return strength
    
    def confidence(self, candles: List[OHLCV], regime: str) -> float:
        """Calculate confidence based on volume and consistency"""
        if len(candles) < 96:
            return 0.5
        
        # Volume confirmation
        recent_volume = sum(c.volume for c in candles[-4:])
        avg_volume = sum(c.volume for c in candles[-96:]) / 96.0
        volume_score = min(recent_volume / (avg_volume * 4), 2.0) / 2.0 if avg_volume > 0 else 0.5
        
        # Price consistency (all recent candles moving same direction)
        recent_closes = [c.close for c in candles[-4:]]
        direction = 1 if recent_closes[-1] > recent_closes[0] else -1
        consistency = sum(
            1 for i in range(1, len(recent_closes))
            if (recent_closes[i] - recent_closes[i-1]) * direction > 0
        ) / (len(recent_closes) - 1)
        
        # Combine factors
        confidence = (volume_score * 0.6) + (consistency * 0.4)
        
        return min(confidence, 1.0)
    
    def _get_thresholds(self, regime: str) -> Dict:
        """Get regime-specific thresholds"""
        defaults = {
            "chop": {
                "pct_change_15m": 2.0,
                "pct_change_60m": 4.0,
                "volume_ratio_1h": 1.9,
            },
            "bull": {
                "pct_change_15m": 3.5,
                "pct_change_60m": 7.0,
                "volume_ratio_1h": 2.0,
            },
            "bear": {
                "pct_change_15m": 3.0,
                "pct_change_60m": 7.0,
                "volume_ratio_1h": 2.0,
            }
        }
        return defaults.get(regime, defaults["chop"])
    
    def _calculate_volatility(self, candles: List[OHLCV]) -> float:
        """Calculate annualized volatility from recent candles"""
        if len(candles) < 24:
            return 0.5
        
        # Use last 24 candles (6 hours at 15min)
        recent = candles[-24:]
        returns = []
        for i in range(1, len(recent)):
            ret = (recent[i].close - recent[i-1].close) / recent[i-1].close
            returns.append(ret)
        
        if not returns:
            return 0.5
        
        # Standard deviation of returns
        mean_ret = sum(returns) / len(returns)
        variance = sum((r - mean_ret) ** 2 for r in returns) / len(returns)
        std_dev = variance ** 0.5
        
        # Annualize (252 trading days, 96 periods per day at 15min)
        annual_vol = std_dev * (252 * 96) ** 0.5
        
        return annual_vol


class MomentumSignal(BaseSignal):
    """
    Momentum continuation signal.
    
    Detects:
    - Sustained price trend over multiple hours
    - Increasing volume on trend
    - No divergence (price + volume moving together)
    
    Best for: Trend-following in bull/bear regimes
    """
    
    def scan(
        self,
        asset: UniverseAsset,
        candles: List[OHLCV],
        regime: str
    ) -> Optional[TriggerSignal]:
        """Scan for momentum trend"""
        
        if len(candles) < 48:  # Need 12 hours of data
            return None
        
        # Trend over last 12 hours
        start_price = candles[-48].close
        current_price = candles[-1].close
        pct_change = ((current_price - start_price) / start_price) * 100
        
        # Momentum threshold (higher in trending regimes)
        threshold = 5.0 if regime in ["bull", "bear"] else 8.0
        
        if abs(pct_change) >= threshold:
            # Check volume trend
            first_half_vol = sum(c.volume for c in candles[-48:-24])
            second_half_vol = sum(c.volume for c in candles[-24:])
            volume_increasing = second_half_vol >= first_half_vol
            
            if volume_increasing:
                strength = self.strength(candles, regime)
                confidence = self.confidence(candles, regime)
                
                return TriggerSignal(
                    symbol=asset.symbol,
                    trigger_type="momentum",
                    strength=strength,
                    confidence=confidence,
                    reason=f"{pct_change:+.1f}% 12h trend with increasing volume",
                    timestamp=datetime.now(timezone.utc),
                    current_price=current_price,
                    price_change_pct=pct_change,
                    volatility=self._calculate_volatility(candles),
                    metrics={
                        "pct_change_12h": pct_change,
                        "volume_trend": "increasing" if volume_increasing else "flat",
                    }
                )
        
        return None
    
    def strength(self, candles: List[OHLCV], regime: str) -> float:
        """Strength based on trend magnitude"""
        if len(candles) < 48:
            return 0.5
        
        start = candles[-48].close
        current = candles[-1].close
        pct_change = abs((current - start) / start) * 100
        
        # Normalize (10% move = 1.0 strength)
        return min(pct_change / 10.0, 1.0)
    
    def confidence(self, candles: List[OHLCV], regime: str) -> float:
        """Confidence based on trend consistency"""
        if len(candles) < 48:
            return 0.5
        
        # Check how many intermediate points confirm the trend
        start = candles[-48].close
        end = candles[-1].close
        direction = 1 if end > start else -1
        
        # Sample every 6 candles (90 minutes)
        samples = [candles[-48 + i*6].close for i in range(8)]
        
        # Count confirmations
        confirmations = sum(
            1 for i in range(1, len(samples))
            if (samples[i] - samples[i-1]) * direction > 0
        )
        
        confidence = confirmations / (len(samples) - 1)
        
        return confidence
    
    def _calculate_volatility(self, candles: List[OHLCV]) -> float:
        """Calculate volatility - same as PriceMoveSignal"""
        if len(candles) < 24:
            return 0.5
        
        recent = candles[-24:]
        returns = [(recent[i].close - recent[i-1].close) / recent[i-1].close 
                   for i in range(1, len(recent))]
        
        if not returns:
            return 0.5
        
        mean_ret = sum(returns) / len(returns)
        variance = sum((r - mean_ret) ** 2 for r in returns) / len(returns)
        std_dev = variance ** 0.5
        annual_vol = std_dev * (252 * 96) ** 0.5
        
        return annual_vol


class MeanReversionSignal(BaseSignal):
    """
    Mean reversion signal.
    
    Detects:
    - Extreme moves away from average
    - Overextension indicators (RSI-like)
    - Signs of exhaustion
    
    Best for: Choppy/range-bound markets
    """
    
    def scan(
        self,
        asset: UniverseAsset,
        candles: List[OHLCV],
        regime: str
    ) -> Optional[TriggerSignal]:
        """Scan for mean reversion opportunity"""
        
        # Only active in chop regime
        if regime not in ["chop"]:
            return None
        
        if len(candles) < 96:
            return None
        
        # Calculate deviation from 24h average
        avg_price = sum(c.close for c in candles[-96:]) / 96.0
        current_price = candles[-1].close
        deviation_pct = ((current_price - avg_price) / avg_price) * 100
        
        # Look for 3%+ deviation
        if abs(deviation_pct) >= 3.0:
            # Check for exhaustion (slowing move)
            recent_move = abs(candles[-1].close - candles[-4].close)
            prior_move = abs(candles[-4].close - candles[-8].close)
            
            exhaustion = recent_move < prior_move
            
            if exhaustion:
                strength = self.strength(candles, regime)
                confidence = self.confidence(candles, regime)
                
                return TriggerSignal(
                    symbol=asset.symbol,
                    trigger_type="mean_reversion",
                    strength=strength,
                    confidence=confidence,
                    reason=f"{deviation_pct:+.1f}% from mean with exhaustion",
                    timestamp=datetime.now(timezone.utc),
                    current_price=current_price,
                    price_change_pct=deviation_pct,
                    volatility=0.3,  # Lower vol assumed for mean reversion
                    metrics={
                        "deviation_pct": deviation_pct,
                        "avg_price": avg_price,
                        "exhaustion": exhaustion,
                    }
                )
        
        return None
    
    def strength(self, candles: List[OHLCV], regime: str) -> float:
        """Strength based on deviation magnitude"""
        if len(candles) < 96:
            return 0.5
        
        avg_price = sum(c.close for c in candles[-96:]) / 96.0
        current_price = candles[-1].close
        deviation_pct = abs((current_price - avg_price) / avg_price) * 100
        
        # Normalize (5% deviation = 1.0 strength)
        return min(deviation_pct / 5.0, 1.0)
    
    def confidence(self, candles: List[OHLCV], regime: str) -> float:
        """Confidence based on exhaustion signals"""
        if len(candles) < 20:
            return 0.5
        
        # Volume declining (exhaustion)
        recent_vol = sum(c.volume for c in candles[-4:])
        prior_vol = sum(c.volume for c in candles[-8:-4])
        vol_declining = recent_vol < prior_vol
        
        # Price move slowing
        recent_range = abs(candles[-1].close - candles[-4].close)
        prior_range = abs(candles[-4].close - candles[-8].close)
        move_slowing = recent_range < prior_range
        
        # Both signals = high confidence
        if vol_declining and move_slowing:
            return 0.8
        elif vol_declining or move_slowing:
            return 0.6
        else:
            return 0.4


# Signal registry for dynamic loading
SIGNAL_REGISTRY: Dict[str, type] = {
    "price_move": PriceMoveSignal,
    "momentum": MomentumSignal,
    "mean_reversion": MeanReversionSignal,
}


def get_signal(signal_type: str, config: Dict) -> BaseSignal:
    """
    Factory function to get signal instance.
    
    Args:
        signal_type: Signal name from SIGNAL_REGISTRY
        config: Signal configuration
        
    Returns:
        Signal instance
    """
    signal_class = SIGNAL_REGISTRY.get(signal_type)
    if not signal_class:
        raise ValueError(f"Unknown signal type: {signal_type}")
    
    return signal_class(config)
