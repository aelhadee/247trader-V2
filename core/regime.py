"""
247trader-v2 Core: Regime Detection

Simple market regime classifier: bull / chop / bear / crash
Based on BTC trend and volatility.

No AI - just math on BTC-USD.
"""

from typing import List, Literal
from dataclasses import dataclass
from datetime import datetime, timezone
import logging

logger = logging.getLogger(__name__)

RegimeType = Literal["bull", "chop", "bear", "crash"]


@dataclass
class RegimeSignal:
    """Market regime signal"""
    regime: RegimeType
    confidence: float  # 0.0 to 1.0
    btc_trend_pct: float  # % change over lookback
    volatility_pct: float  # Realized volatility
    timestamp: datetime
    reason: str


class RegimeDetector:
    """
    Detect market regime from BTC price action.

    Rules (simple but effective):
    - Bull: BTC up 10%+ in 7d, vol < 60%
    - Chop: BTC -5% to +10% in 7d, vol < 80%
    - Bear: BTC down 5%+ in 7d, vol < 100%
    - Crash: BTC down 10%+ in 7d OR vol > 100%

    Inspired by traditional market regime filters.
    """

    def __init__(self):
        logger.info("Initialized RegimeDetector")

    def detect(self, btc_candles: List, lookback_days: int = 7) -> RegimeSignal:
        """
        Detect current market regime from BTC candles.

        Args:
            btc_candles: List of OHLCV candles (sorted by time)
            lookback_days: Days to look back

        Returns:
            RegimeSignal
        """
        if not btc_candles or len(btc_candles) < lookback_days * 24:
            # Not enough data, default to chop
            return RegimeSignal(
                regime="chop",
                confidence=0.5,
                btc_trend_pct=0.0,
                volatility_pct=0.0,
                timestamp=datetime.now(timezone.utc),
                reason="Insufficient data - defaulting to chop"
            )

        # Calculate trend (% change over lookback)
        lookback_hours = lookback_days * 24
        start_price = btc_candles[-lookback_hours].close
        current_price = btc_candles[-1].close
        trend_pct = ((current_price - start_price) / start_price) * 100

        # Calculate realized volatility (std dev of hourly returns)
        hourly_returns = []
        for i in range(-lookback_hours, 0):
            if i == -lookback_hours:
                continue
            prev_close = btc_candles[i-1].close
            curr_close = btc_candles[i].close
            ret = ((curr_close - prev_close) / prev_close) * 100
            hourly_returns.append(ret)

        # Annualized volatility (hourly std dev * sqrt(24*365))
        import statistics
        vol_hourly = statistics.stdev(hourly_returns) if len(hourly_returns) > 1 else 0.0
        vol_annual_pct = vol_hourly * (24 * 365) ** 0.5

        # Classify regime
        regime, confidence, reason = self._classify(trend_pct, vol_annual_pct)

        logger.info(
            f"Regime: {regime.upper()} (conf={confidence:.2f}) | "
            f"BTC trend: {trend_pct:+.1f}% | Vol: {vol_annual_pct:.0f}%"
        )

        return RegimeSignal(
            regime=regime,
            confidence=confidence,
            btc_trend_pct=trend_pct,
            volatility_pct=vol_annual_pct,
            timestamp=datetime.now(timezone.utc),
            reason=reason
        )

    def _classify(self, trend_pct: float, vol_pct: float) -> tuple[RegimeType, float, str]:
        """
        Classify regime based on trend and volatility.

        Returns:
            (regime, confidence, reason)
        """
        # Crash: extreme moves or extreme vol
        if trend_pct < -10 or vol_pct > 100:
            if trend_pct < -15 and vol_pct > 120:
                return "crash", 0.9, f"Severe drawdown ({trend_pct:.1f}%) + high vol ({vol_pct:.0f}%)"
            return "crash", 0.7, f"Crash conditions: trend={trend_pct:.1f}%, vol={vol_pct:.0f}%"

        # Bull: strong uptrend with manageable vol
        if trend_pct >= 10 and vol_pct < 60:
            confidence = min(0.9, 0.5 + (trend_pct - 10) / 50)  # More confidence for stronger trends
            return "bull", confidence, f"Strong uptrend ({trend_pct:+.1f}%) + low vol ({vol_pct:.0f}%)"

        # Bear: downtrend
        if trend_pct <= -5:
            confidence = min(0.8, 0.5 + abs(trend_pct + 5) / 20)
            return "bear", confidence, f"Downtrend ({trend_pct:.1f}%) + elevated vol ({vol_pct:.0f}%)"

        # Chop: everything else
        if abs(trend_pct) < 5:
            confidence = 0.8  # High confidence in ranging
            return "chop", confidence, f"Ranging market: trend={trend_pct:+.1f}%, vol={vol_pct:.0f}%"
        else:
            confidence = 0.6  # Mild trend
            return "chop", confidence, f"Mild trend ({trend_pct:+.1f}%), choppy conditions"

    def get_trigger_multipliers(self, regime: RegimeType) -> dict:
        """
        Get trigger threshold multipliers for each regime.

        In bull markets, we want to be more aggressive (lower thresholds).
        In bear/crash, we want to be more conservative (higher thresholds).

        Returns:
            Dict with multipliers for: volume_spike, momentum, breakout
        """
        multipliers = {
            "bull": {
                "volume_spike": 0.8,  # Lower threshold (1.6x instead of 2.0x)
                "momentum": 0.75,     # Lower threshold (3% instead of 4%)
                "breakout": 0.9,      # Slightly lower
            },
            "chop": {
                "volume_spike": 1.0,  # Baseline (2.0x)
                "momentum": 1.0,      # Baseline (4%)
                "breakout": 1.0,      # Baseline
            },
            "bear": {
                "volume_spike": 1.2,  # Higher threshold (2.4x)
                "momentum": 1.25,     # Higher threshold (5%)
                "breakout": 1.1,      # Higher
            },
            "crash": {
                "volume_spike": 1.5,  # Much higher (3.0x)
                "momentum": 1.5,      # Much higher (6%)
                "breakout": 1.3,      # Much higher
            }
        }

        return multipliers.get(regime, multipliers["chop"])


def test_regime_detector():
    """Test regime detection"""
    from dataclasses import dataclass as dc

    @dc
    class MockCandle:
        close: float

    # Create mock BTC data
    detector = RegimeDetector()

    # Bull market: +15% in 7 days
    print("\n=== BULL MARKET TEST ===")
    bull_candles = [MockCandle(close=50000 + i * 45) for i in range(168)]  # ~15% gain
    signal = detector.detect(bull_candles)
    print(f"Regime: {signal.regime} (confidence={signal.confidence:.2f})")
    print(f"Reason: {signal.reason}")

    # Chop market: flat
    print("\n=== CHOP MARKET TEST ===")
    chop_candles = [MockCandle(close=50000 + (i % 10) * 100) for i in range(168)]
    signal = detector.detect(chop_candles)
    print(f"Regime: {signal.regime} (confidence={signal.confidence:.2f})")
    print(f"Reason: {signal.reason}")

    # Bear market: -8% in 7 days
    print("\n=== BEAR MARKET TEST ===")
    bear_candles = [MockCandle(close=50000 - i * 25) for i in range(168)]  # ~8% loss
    signal = detector.detect(bear_candles)
    print(f"Regime: {signal.regime} (confidence={signal.confidence:.2f})")
    print(f"Reason: {signal.reason}")

    # Crash: -15% in 7 days
    print("\n=== CRASH TEST ===")
    crash_candles = [MockCandle(close=50000 - i * 50) for i in range(168)]  # ~15% loss
    signal = detector.detect(crash_candles)
    print(f"Regime: {signal.regime} (confidence={signal.confidence:.2f})")
    print(f"Reason: {signal.reason}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    test_regime_detector()
