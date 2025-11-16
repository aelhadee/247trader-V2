"""
Tests for per-endpoint rate limiter.

Tests token bucket algorithm, endpoint tracking, and alerting.
"""

import time
import pytest
from core.rate_limiter import RateLimiter, EndpointQuota, RateLimitStats


def test_endpoint_quota_basic():
    """Test basic token bucket mechanics"""
    quota = EndpointQuota(name="test_endpoint", requests_per_second=10.0)
    
    # Should have full tokens initially
    assert quota.available_tokens == pytest.approx(10.0, rel=0.1)
    
    # Acquire 1 token
    assert quota.acquire(1.0) is True
    assert quota.available_tokens == pytest.approx(9.0, rel=0.1)
    
    # Acquire 5 more
    assert quota.acquire(5.0) is True
    assert quota.available_tokens == pytest.approx(4.0, rel=0.1)
    
    # Try to acquire 10 (should fail - insufficient)
    assert quota.acquire(10.0) is False
    
    # Utilization should reflect 6 calls in last second (60%)
    # Note: utilization is calls in window / requests_per_second
    assert quota.utilization >= 0.2  # At least 2 calls recorded


def test_endpoint_quota_refill():
    """Test token refill over time"""
    quota = EndpointQuota(name="test_endpoint", requests_per_second=10.0)
    
    # Exhaust tokens
    assert quota.acquire(10.0) is True
    assert quota.available_tokens == pytest.approx(0.0, abs=0.1)
    
    # Wait 0.5 seconds (should refill 5 tokens at 10/sec)
    time.sleep(0.5)
    assert quota.available_tokens == pytest.approx(5.0, rel=0.2)
    
    # Wait another 0.5 seconds (should be full)
    time.sleep(0.5)
    assert quota.available_tokens == pytest.approx(10.0, rel=0.1)


def test_endpoint_quota_utilization():
    """Test utilization tracking"""
    quota = EndpointQuota(name="test_endpoint", requests_per_second=10.0)
    
    # No calls = 0% utilization
    assert quota.utilization == 0.0
    
    # Make 5 calls (50% utilization)
    for _ in range(5):
        quota.acquire(1.0)
    
    assert quota.utilization == pytest.approx(0.5, rel=0.1)
    
    # Make 5 more (100% utilization)
    for _ in range(5):
        quota.acquire(1.0)
    
    assert quota.utilization == pytest.approx(1.0, rel=0.1)
    
    # Wait for window to clear
    time.sleep(1.1)
    assert quota.utilization == 0.0


def test_rate_limiter_configuration():
    """Test rate limiter configuration"""
    limiter = RateLimiter(alert_threshold=0.8)
    
    # Configure endpoint quotas
    limiter.configure({
        "get_quote": 10.0,
        "place_order": 5.0,
        "list_products": 3.0
    }, default_public=8.0, default_private=12.0)
    
    # Check configured endpoints
    stats = limiter.get_all_stats()
    assert "get_quote" in stats
    assert "place_order" in stats
    assert "list_products" in stats
    
    # Verify quotas
    assert limiter._quotas["get_quote"].requests_per_second == 10.0
    assert limiter._quotas["place_order"].requests_per_second == 5.0


def test_rate_limiter_acquire_wait():
    """Test acquire with waiting"""
    limiter = RateLimiter()
    limiter.configure({"test_endpoint": 5.0})
    
    # Acquire 5 tokens (should succeed immediately)
    start = time.time()
    assert limiter.acquire("test_endpoint", wait=True) is True
    duration1 = time.time() - start
    assert duration1 < 0.1  # Should be instant
    
    # Acquire 4 more tokens (5 total)
    for _ in range(4):
        assert limiter.acquire("test_endpoint", wait=True) is True
    
    # Next acquire should wait for refill
    start = time.time()
    assert limiter.acquire("test_endpoint", wait=True) is True
    duration_wait = time.time() - start
    assert duration_wait >= 0.15  # Should wait for at least 1 token to refill


def test_rate_limiter_acquire_no_wait():
    """Test acquire without waiting"""
    limiter = RateLimiter()
    limiter.configure({"test_endpoint": 5.0})
    
    # Acquire all tokens
    for _ in range(5):
        assert limiter.acquire("test_endpoint", wait=False) is True
    
    # Next acquire should fail
    assert limiter.acquire("test_endpoint", wait=False) is False
    
    # Should record violation
    stats = limiter.get_stats("test_endpoint")
    assert stats.violations == 1


def test_rate_limiter_default_quotas():
    """Test default quota creation"""
    limiter = RateLimiter()
    limiter.configure({}, default_public=10.0, default_private=15.0)
    
    # Acquire from unconfigured endpoint (should use default)
    assert limiter.acquire("unknown_public", is_private=False, wait=False) is True
    assert limiter.acquire("unknown_private", is_private=True, wait=False) is True
    
    # Check that defaults were applied
    stats_pub = limiter.get_stats("unknown_public", is_private=False)
    stats_priv = limiter.get_stats("unknown_private", is_private=True)
    
    # Should have used defaults (10 and 15 respectively)
    # Check via available tokens after 1 acquire
    assert limiter._quotas["unknown_public"].requests_per_second == 10.0
    assert limiter._quotas["unknown_private"].requests_per_second == 15.0


def test_rate_limiter_record():
    """Test manual recording"""
    limiter = RateLimiter()
    limiter.configure({"test_endpoint": 10.0})
    
    # Record some calls
    for _ in range(5):
        limiter.record("test_endpoint", is_private=False)
    
    # Check stats
    stats = limiter.get_stats("test_endpoint")
    assert stats.calls_last_second == 5
    assert stats.utilization == pytest.approx(0.5, rel=0.1)
    
    # Record a violation
    limiter.record("test_endpoint", violated=True)
    stats = limiter.get_stats("test_endpoint")
    assert stats.violations == 1


def test_rate_limiter_stats():
    """Test statistics collection"""
    limiter = RateLimiter(alert_threshold=0.7)
    limiter.configure({
        "endpoint_a": 10.0,
        "endpoint_b": 5.0
    })
    
    # Use endpoints
    for _ in range(7):
        limiter.record("endpoint_a")
    
    for _ in range(3):
        limiter.record("endpoint_b")
    
    # Get all stats
    all_stats = limiter.get_all_stats()
    assert len(all_stats) == 2
    
    # Check endpoint_a stats
    stats_a = all_stats["endpoint_a"]
    assert stats_a.endpoint == "endpoint_a"
    assert stats_a.calls_last_second == 7
    assert stats_a.utilization == pytest.approx(0.7, rel=0.1)
    
    # Check endpoint_b stats
    stats_b = all_stats["endpoint_b"]
    assert stats_b.calls_last_second == 3
    assert stats_b.utilization == pytest.approx(0.6, rel=0.1)


def test_rate_limiter_wait_time():
    """Test wait time calculation"""
    limiter = RateLimiter()
    limiter.configure({"test_endpoint": 10.0})
    
    # Exhaust tokens
    for _ in range(10):
        limiter.acquire("test_endpoint", wait=False)
    
    # Check wait time for 1 token
    wait_time = limiter.get_wait_time("test_endpoint")
    assert wait_time > 0
    assert wait_time <= 0.2  # At most 0.1s per token at 10/sec
    
    # Wait and check again
    time.sleep(0.5)
    wait_time = limiter.get_wait_time("test_endpoint")
    assert wait_time == 0  # Should have refilled ~5 tokens


def test_rate_limiter_reset():
    """Test reset functionality"""
    limiter = RateLimiter()
    limiter.configure({"test_endpoint": 10.0})
    
    # Use up some quota
    for _ in range(5):
        limiter.acquire("test_endpoint", wait=False)
    limiter.record("test_endpoint", violated=True)
    
    # Check state
    stats = limiter.get_stats("test_endpoint")
    assert stats.calls_last_second == 5
    assert stats.violations == 1
    
    # Reset
    limiter.reset("test_endpoint")
    
    # Check cleared
    stats = limiter.get_stats("test_endpoint")
    assert stats.calls_last_second == 0
    assert stats.violations == 0
    assert stats.tokens_available == pytest.approx(10.0, rel=0.1)


def test_rate_limiter_high_utilization_warning(caplog):
    """Test high utilization warnings"""
    import logging
    caplog.set_level(logging.WARNING)
    
    limiter = RateLimiter(alert_threshold=0.8)
    limiter.configure({"test_endpoint": 10.0})
    
    # Push utilization above threshold
    for _ in range(9):
        limiter.acquire("test_endpoint", wait=False)
    
    # Check for warning
    assert any("High rate limit utilization" in record.message for record in caplog.records)
    assert any("test_endpoint" in record.message for record in caplog.records)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
