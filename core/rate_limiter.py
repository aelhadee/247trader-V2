"""
247trader-v2 Core: Per-Endpoint Rate Limiter

Track API quota usage per endpoint to prevent exhaustion.
Implements proactive pausing before hitting rate limits.

Coinbase Advanced Trade API Limits (as of 2024):
- Public endpoints: 10 requests/second
- Private endpoints: 15 requests/second
- Orders: 10 orders/second
- Candles: 10 requests/second

Pattern: Token bucket with endpoint-specific quotas
"""

import time
import logging
from collections import deque
from typing import Dict, Optional, Tuple
from dataclasses import dataclass, field
from threading import Lock

logger = logging.getLogger(__name__)


@dataclass
class EndpointQuota:
    """Rate limit quota for a specific endpoint"""
    name: str
    requests_per_second: float
    window_seconds: float = 1.0
    
    # Token bucket state
    tokens: float = field(init=False)
    last_refill: float = field(init=False, default_factory=time.monotonic)
    
    # Usage tracking
    calls: deque = field(init=False, default_factory=lambda: deque())
    violations: int = 0
    
    def __post_init__(self):
        self.tokens = self.requests_per_second
    
    @property
    def utilization(self) -> float:
        """Current utilization (0.0-1.0+)"""
        now = time.monotonic()
        cutoff = now - self.window_seconds
        
        # Clean old calls
        while self.calls and self.calls[0] < cutoff:
            self.calls.popleft()
        
        return len(self.calls) / self.requests_per_second if self.requests_per_second > 0 else 0.0
    
    @property
    def available_tokens(self) -> float:
        """Tokens available after refill"""
        self._refill()
        return self.tokens
    
    def _refill(self):
        """Refill tokens based on elapsed time"""
        now = time.monotonic()
        elapsed = now - self.last_refill
        
        # Refill at rate: requests_per_second tokens per second
        refill_amount = elapsed * self.requests_per_second
        self.tokens = min(self.requests_per_second, self.tokens + refill_amount)
        self.last_refill = now
    
    def acquire(self, tokens: float = 1.0) -> bool:
        """
        Try to acquire tokens.
        
        Returns:
            True if tokens acquired, False if insufficient
        """
        self._refill()
        
        if self.tokens >= tokens:
            self.tokens -= tokens
            self.calls.append(time.monotonic())
            return True
        
        return False
    
    def wait_time(self, tokens: float = 1.0) -> float:
        """
        Calculate wait time in seconds to acquire tokens.
        
        Returns:
            0.0 if tokens available now, otherwise seconds to wait
        """
        self._refill()
        
        if self.tokens >= tokens:
            return 0.0
        
        # Calculate time needed to refill required tokens
        tokens_needed = tokens - self.tokens
        return tokens_needed / self.requests_per_second


@dataclass
class RateLimitStats:
    """Statistics for a rate limiter"""
    endpoint: str
    utilization: float
    tokens_available: float
    calls_last_second: int
    violations: int
    wait_time_seconds: float


class RateLimiter:
    """
    Per-endpoint rate limiter with token bucket algorithm.
    
    Features:
    - Endpoint-specific quotas
    - Proactive waiting before exhaustion
    - Thread-safe token acquisition
    - Utilization tracking and alerting
    
    Usage:
        limiter = RateLimiter()
        limiter.configure({
            "list_products": 5.0,  # 5 req/sec
            "get_quote": 10.0,     # 10 req/sec
        })
        
        # Before API call
        if limiter.should_wait("get_quote"):
            time.sleep(limiter.get_wait_time("get_quote"))
        limiter.record("get_quote")
    """
    
    def __init__(self, alert_threshold: float = 0.8):
        """
        Initialize rate limiter.
        
        Args:
            alert_threshold: Utilization threshold (0.0-1.0) to trigger alerts
        """
        self.alert_threshold = alert_threshold
        self._quotas: Dict[str, EndpointQuota] = {}
        self._lock = Lock()
        
        # Global fallbacks
        self._default_public_quota = 10.0   # Coinbase: 10 req/sec
        self._default_private_quota = 15.0  # Coinbase: 15 req/sec
        
        logger.info(f"Initialized RateLimiter (alert_threshold={alert_threshold:.1%})")
    
    def configure(self, endpoint_quotas: Dict[str, float], 
                  default_public: Optional[float] = None,
                  default_private: Optional[float] = None):
        """
        Configure per-endpoint quotas.
        
        Args:
            endpoint_quotas: Dict mapping endpoint name -> requests/second
            default_public: Default quota for public endpoints
            default_private: Default quota for private endpoints
        """
        with self._lock:
            if default_public is not None:
                self._default_public_quota = default_public
            if default_private is not None:
                self._default_private_quota = default_private
            
            for endpoint, quota in endpoint_quotas.items():
                if quota <= 0:
                    logger.warning(f"Invalid quota for {endpoint}: {quota}, skipping")
                    continue
                
                self._quotas[endpoint] = EndpointQuota(
                    name=endpoint,
                    requests_per_second=quota
                )
            
            logger.info(f"Configured {len(self._quotas)} endpoint quotas")
    
    def _get_or_create_quota(self, endpoint: str, is_private: bool = False) -> EndpointQuota:
        """Get quota for endpoint, creating with defaults if missing"""
        with self._lock:
            if endpoint not in self._quotas:
                # Create with default quota
                default_quota = self._default_private_quota if is_private else self._default_public_quota
                self._quotas[endpoint] = EndpointQuota(
                    name=endpoint,
                    requests_per_second=default_quota
                )
                logger.debug(f"Created default quota for {endpoint}: {default_quota} req/sec")
            
            return self._quotas[endpoint]
    
    def should_wait(self, endpoint: str, is_private: bool = False, tokens: float = 1.0) -> bool:
        """
        Check if should wait before making request.
        
        Args:
            endpoint: Endpoint name
            is_private: Whether endpoint is private (authenticated)
            tokens: Number of tokens to acquire (default 1.0)
        
        Returns:
            True if should wait, False if can proceed
        """
        quota = self._get_or_create_quota(endpoint, is_private)
        return quota.available_tokens < tokens
    
    def get_wait_time(self, endpoint: str, is_private: bool = False, tokens: float = 1.0) -> float:
        """
        Get wait time in seconds before request can proceed.
        
        Args:
            endpoint: Endpoint name
            is_private: Whether endpoint is private
            tokens: Number of tokens to acquire
        
        Returns:
            Seconds to wait (0.0 if can proceed now)
        """
        quota = self._get_or_create_quota(endpoint, is_private)
        return quota.wait_time(tokens)
    
    def acquire(self, endpoint: str, is_private: bool = False, tokens: float = 1.0, 
                wait: bool = True) -> bool:
        """
        Acquire tokens for endpoint, optionally waiting if insufficient.
        
        Args:
            endpoint: Endpoint name
            is_private: Whether endpoint is private
            tokens: Number of tokens to acquire
            wait: If True, wait for tokens; if False, return immediately
        
        Returns:
            True if tokens acquired, False if insufficient (only when wait=False)
        """
        quota = self._get_or_create_quota(endpoint, is_private)
        
        if wait:
            wait_time = quota.wait_time(tokens)
            if wait_time > 0:
                logger.debug(f"Rate limiting {endpoint}: waiting {wait_time:.3f}s")
                time.sleep(wait_time)
        
        acquired = quota.acquire(tokens)
        
        if not acquired and not wait:
            quota.violations += 1
        
        # Check for high utilization
        if acquired and quota.utilization >= self.alert_threshold:
            logger.warning(
                f"High rate limit utilization for {endpoint}: "
                f"{quota.utilization:.1%} (threshold: {self.alert_threshold:.1%})"
            )
        
        return acquired
    
    def record(self, endpoint: str, is_private: bool = False, violated: bool = False):
        """
        Record API call for tracking (use when not using acquire()).
        
        Args:
            endpoint: Endpoint name
            is_private: Whether endpoint is private
            violated: Whether this call resulted in 429 response
        """
        quota = self._get_or_create_quota(endpoint, is_private)
        quota.calls.append(time.monotonic())
        
        if violated:
            quota.violations += 1
            logger.warning(f"Rate limit violation for {endpoint} (total: {quota.violations})")
    
    def get_stats(self, endpoint: str, is_private: bool = False) -> RateLimitStats:
        """Get statistics for endpoint"""
        quota = self._get_or_create_quota(endpoint, is_private)
        
        return RateLimitStats(
            endpoint=endpoint,
            utilization=quota.utilization,
            tokens_available=quota.available_tokens,
            calls_last_second=len(quota.calls),
            violations=quota.violations,
            wait_time_seconds=quota.wait_time()
        )
    
    def get_all_stats(self) -> Dict[str, RateLimitStats]:
        """Get statistics for all configured endpoints"""
        with self._lock:
            return {
                name: RateLimitStats(
                    endpoint=name,
                    utilization=quota.utilization,
                    tokens_available=quota.available_tokens,
                    calls_last_second=len(quota.calls),
                    violations=quota.violations,
                    wait_time_seconds=quota.wait_time()
                )
                for name, quota in self._quotas.items()
            }
    
    def reset(self, endpoint: Optional[str] = None):
        """Reset rate limiter state (for testing)"""
        with self._lock:
            if endpoint:
                if endpoint in self._quotas:
                    quota = self._quotas[endpoint]
                    quota.tokens = quota.requests_per_second
                    quota.calls.clear()
                    quota.violations = 0
            else:
                # Reset all
                for quota in self._quotas.values():
                    quota.tokens = quota.requests_per_second
                    quota.calls.clear()
                    quota.violations = 0
