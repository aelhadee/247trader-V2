"""
Rate Limiter with Token Bucket Algorithm

Implements pre-emptive rate limiting to prevent 429 errors from Coinbase API.
Uses token bucket algorithm with per-endpoint tracking and graceful degradation.

Coinbase Advanced Trade API Limits (as of 2024):
- Public endpoints: 10 requests/second
- Private endpoints: 15 requests/second
- Burst tolerance: Up to 2x limit for short durations

Reference: https://docs.cdp.coinbase.com/advanced-trade/docs/rest-api-rate-limits
"""
import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class TokenBucket:
    """
    Token bucket for rate limiting.
    
    Tokens replenish at a fixed rate. Each request consumes one token.
    If bucket is empty, request must wait until tokens replenish.
    """
    capacity: float  # Max tokens (burst capacity)
    refill_rate: float  # Tokens per second
    tokens: float = field(init=False)  # Current tokens
    last_refill: float = field(init=False)  # Last refill timestamp
    
    def __post_init__(self):
        self.tokens = self.capacity  # Start full
        self.last_refill = time.monotonic()
    
    def refill(self) -> None:
        """Refill tokens based on elapsed time"""
        now = time.monotonic()
        elapsed = now - self.last_refill
        
        # Add tokens based on elapsed time
        new_tokens = elapsed * self.refill_rate
        self.tokens = min(self.capacity, self.tokens + new_tokens)
        self.last_refill = now
    
    def consume(self, tokens: float = 1.0) -> bool:
        """
        Try to consume tokens.
        
        Returns:
            True if tokens available, False if bucket empty
        """
        self.refill()
        
        if self.tokens >= tokens:
            self.tokens -= tokens
            return True
        return False
    
    def wait_time(self, tokens: float = 1.0) -> float:
        """
        Calculate time to wait for tokens to be available.
        
        Returns:
            Seconds to wait (0 if tokens available now)
        """
        self.refill()
        
        if self.tokens >= tokens:
            return 0.0
        
        # Calculate time needed for tokens to refill
        tokens_needed = tokens - self.tokens
        wait_seconds = tokens_needed / self.refill_rate
        return wait_seconds


@dataclass
class RateLimitStats:
    """Statistics for rate limit monitoring"""
    total_requests: int = 0
    blocked_requests: int = 0
    total_wait_time_ms: float = 0.0
    max_wait_time_ms: float = 0.0
    throttle_events: int = 0
    
    def record_wait(self, wait_time_seconds: float) -> None:
        """Record a throttle event"""
        self.total_requests += 1
        if wait_time_seconds > 0:
            self.blocked_requests += 1
            self.throttle_events += 1
            wait_ms = wait_time_seconds * 1000.0
            self.total_wait_time_ms += wait_ms
            self.max_wait_time_ms = max(self.max_wait_time_ms, wait_ms)
        else:
            self.total_requests += 1
    
    def utilization_pct(self) -> float:
        """Calculate blocked request percentage"""
        if self.total_requests == 0:
            return 0.0
        return (self.blocked_requests / self.total_requests) * 100.0


class RateLimiter:
    """
    Pre-emptive rate limiter with per-endpoint tracking.
    
    Features:
    - Token bucket algorithm (smooth rate limiting)
    - Per-endpoint buckets (public vs private)
    - Burst tolerance (2x capacity for short duration)
    - Pre-emptive blocking (prevents 429 errors)
    - Graceful degradation (logs warnings when throttled)
    - Statistics tracking (monitor utilization)
    
    Usage:
        limiter = RateLimiter()
        
        # Before making API call
        limiter.acquire("private", endpoint="/orders")
        # ... make API call ...
    """
    
    def __init__(
        self,
        public_limit: float = 10.0,  # requests/second
        private_limit: float = 15.0,  # requests/second
        burst_multiplier: float = 2.0,  # Allow 2x burst
    ):
        """
        Initialize rate limiter.
        
        Args:
            public_limit: Public endpoint rate limit (req/s)
            private_limit: Private endpoint rate limit (req/s)
            burst_multiplier: Burst capacity multiplier
        """
        self._public_bucket = TokenBucket(
            capacity=public_limit * burst_multiplier,
            refill_rate=public_limit,
        )
        self._private_bucket = TokenBucket(
            capacity=private_limit * burst_multiplier,
            refill_rate=private_limit,
        )
        
        # Per-endpoint stats
        self._stats: Dict[str, RateLimitStats] = defaultdict(RateLimitStats)
        self._global_stats = {"public": RateLimitStats(), "private": RateLimitStats()}
        
        # Recent violations (for alerting)
        self._recent_violations: Dict[str, deque] = {
            "public": deque(maxlen=100),
            "private": deque(maxlen=100),
        }
        
        # Thread safety
        self._lock = Lock()
        
        logger.info(
            f"Initialized RateLimiter: public={public_limit}/s, private={private_limit}/s, "
            f"burst={burst_multiplier}x"
        )
    
    def acquire(
        self,
        channel: str,
        endpoint: str = "unknown",
        tokens: float = 1.0,
        block: bool = True,
    ) -> float:
        """
        Acquire tokens for API call.
        
        Args:
            channel: "public" or "private"
            endpoint: API endpoint name (for stats)
            tokens: Number of tokens to consume
            block: If True, wait for tokens. If False, return immediately.
        
        Returns:
            Wait time in seconds (0 if no wait needed)
        
        Raises:
            ValueError: If channel invalid or tokens not available (when block=False)
        """
        if channel not in ("public", "private"):
            raise ValueError(f"Invalid channel: {channel}. Must be 'public' or 'private'.")
        
        bucket = self._public_bucket if channel == "public" else self._private_bucket
        
        with self._lock:
            # Check if tokens available
            wait_time = bucket.wait_time(tokens)
            
            if wait_time == 0:
                # Tokens available immediately
                bucket.consume(tokens)
                self._stats[endpoint].record_wait(0.0)
                self._global_stats[channel].record_wait(0.0)
                return 0.0
            
            if not block:
                raise ValueError(
                    f"Rate limit exceeded for {channel}:{endpoint}. "
                    f"Need to wait {wait_time:.2f}s but block=False."
                )
            
            # Log throttle event
            if wait_time > 1.0:
                logger.warning(
                    f"Rate limit throttle: {channel}:{endpoint} waiting {wait_time:.2f}s "
                    f"(tokens={bucket.tokens:.1f}/{bucket.capacity:.1f})"
                )
            elif wait_time > 0.1:
                logger.info(
                    f"Rate limit pause: {channel}:{endpoint} waiting {wait_time:.3f}s"
                )
            
            # Record violation
            self._recent_violations[channel].append({
                "timestamp": datetime.now(timezone.utc),
                "endpoint": endpoint,
                "wait_time": wait_time,
            })
        
        # Wait outside lock to avoid blocking other threads
        time.sleep(wait_time)
        
        # Consume tokens after wait
        with self._lock:
            bucket.consume(tokens)
            self._stats[endpoint].record_wait(wait_time)
            self._global_stats[channel].record_wait(wait_time)
        
        return wait_time
    
    def check_available(self, channel: str, tokens: float = 1.0) -> Tuple[bool, float]:
        """
        Check if tokens available without consuming them.
        
        Returns:
            (available: bool, wait_time: float)
        """
        if channel not in ("public", "private"):
            raise ValueError(f"Invalid channel: {channel}")
        
        bucket = self._public_bucket if channel == "public" else self._private_bucket
        
        with self._lock:
            wait_time = bucket.wait_time(tokens)
            return (wait_time == 0, wait_time)
    
    def get_stats(self, channel: Optional[str] = None) -> Dict:
        """
        Get rate limiter statistics.
        
        Args:
            channel: If specified, return channel-specific stats.
                     If None, return global stats.
        
        Returns:
            Statistics dictionary
        """
        with self._lock:
            if channel:
                if channel not in ("public", "private"):
                    raise ValueError(f"Invalid channel: {channel}")
                
                stats = self._global_stats[channel]
                bucket = self._public_bucket if channel == "public" else self._private_bucket
                
                return {
                    "channel": channel,
                    "total_requests": stats.total_requests,
                    "blocked_requests": stats.blocked_requests,
                    "throttle_events": stats.throttle_events,
                    "utilization_pct": stats.utilization_pct(),
                    "total_wait_time_ms": stats.total_wait_time_ms,
                    "max_wait_time_ms": stats.max_wait_time_ms,
                    "avg_wait_time_ms": (
                        stats.total_wait_time_ms / stats.blocked_requests
                        if stats.blocked_requests > 0
                        else 0.0
                    ),
                    "current_tokens": bucket.tokens,
                    "capacity": bucket.capacity,
                    "refill_rate": bucket.refill_rate,
                    "recent_violations": len([
                        v for v in self._recent_violations[channel]
                        if (datetime.now(timezone.utc) - v["timestamp"]).total_seconds() < 60
                    ]),
                }
            else:
                # Global stats
                return {
                    "public": self.get_stats("public"),
                    "private": self.get_stats("private"),
                }
    
    def reset_stats(self) -> None:
        """Reset statistics (useful for testing)"""
        with self._lock:
            self._stats.clear()
            self._global_stats = {"public": RateLimitStats(), "private": RateLimitStats()}
            self._recent_violations["public"].clear()
            self._recent_violations["private"].clear()
    
    def get_endpoint_stats(self, endpoint: str) -> Dict:
        """Get statistics for specific endpoint"""
        with self._lock:
            stats = self._stats.get(endpoint, RateLimitStats())
            return {
                "endpoint": endpoint,
                "total_requests": stats.total_requests,
                "blocked_requests": stats.blocked_requests,
                "throttle_events": stats.throttle_events,
                "utilization_pct": stats.utilization_pct(),
                "total_wait_time_ms": stats.total_wait_time_ms,
                "max_wait_time_ms": stats.max_wait_time_ms,
                "avg_wait_time_ms": (
                    stats.total_wait_time_ms / stats.blocked_requests
                    if stats.blocked_requests > 0
                    else 0.0
                ),
            }
    
    def should_alert(self, channel: str, threshold_pct: float = 80.0) -> bool:
        """
        Check if rate limit utilization exceeds threshold.
        
        Args:
            channel: "public" or "private"
            threshold_pct: Alert if utilization > this percentage
        
        Returns:
            True if should alert
        """
        stats = self.get_stats(channel)
        return stats["utilization_pct"] > threshold_pct
