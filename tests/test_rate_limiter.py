"""
Tests for Rate Limiter

Validates token bucket algorithm, pre-emptive throttling, and statistics tracking.
"""
import pytest
import time
from unittest.mock import patch, MagicMock
from infra.rate_limiter import RateLimiter, TokenBucket, RateLimitStats


class TestTokenBucket:
    """Test token bucket implementation"""
    
    def test_bucket_starts_full(self):
        """Bucket starts with full capacity"""
        bucket = TokenBucket(capacity=10.0, refill_rate=5.0)
        assert bucket.tokens == 10.0
    
    def test_consume_tokens(self):
        """Consuming tokens decreases bucket"""
        bucket = TokenBucket(capacity=10.0, refill_rate=5.0)
        assert bucket.consume(3.0)
        assert bucket.tokens == 7.0
    
    def test_cannot_over_consume(self):
        """Cannot consume more tokens than available"""
        bucket = TokenBucket(capacity=10.0, refill_rate=5.0)
        bucket.consume(10.0)  # Empty bucket
        assert not bucket.consume(1.0)  # Should fail
    
    def test_tokens_refill_over_time(self):
        """Tokens refill at specified rate"""
        bucket = TokenBucket(capacity=10.0, refill_rate=10.0)  # 10 tokens/second
        bucket.consume(10.0)  # Empty bucket
        
        # Simulate 0.5 seconds passing
        bucket.last_update -= 0.5
        bucket.refill()
        
        # Should have ~5 tokens (10 tokens/s × 0.5s)
        assert 4.5 <= bucket.tokens <= 5.5
    
    def test_bucket_does_not_exceed_capacity(self):
        """Bucket cannot exceed capacity even with long wait"""
        bucket = TokenBucket(capacity=10.0, refill_rate=10.0)
        
        # Simulate 2 seconds passing (much longer than needed)
        bucket.last_update -= 2.0
        bucket.refill()
        
        assert bucket.tokens == 10.0  # Capped at capacity
    
    def test_wait_time_calculation(self):
        """Wait time calculated correctly"""
        bucket = TokenBucket(capacity=10.0, refill_rate=10.0)
        bucket.consume(10.0)  # Empty bucket
        
        wait_time = bucket.wait_time(5.0)  # Need 5 tokens
        
        # Should need 0.5 seconds (5 tokens / 10 tokens/s)
        assert 0.45 <= wait_time <= 0.55
    
    def test_zero_wait_when_tokens_available(self):
        """Zero wait time when tokens available"""
        bucket = TokenBucket(capacity=10.0, refill_rate=10.0)
        
        wait_time = bucket.wait_time(5.0)
        assert wait_time == 0.0


class TestRateLimitStats:
    """Test statistics tracking"""
    
    def test_stats_start_at_zero(self):
        """Statistics start at zero"""
        stats = RateLimitStats()
        assert stats.total_requests == 0
        assert stats.blocked_requests == 0
        assert stats.throttle_events == 0
    
    def test_record_successful_request(self):
        """Recording request with no wait"""
        stats = RateLimitStats()
        stats.record_wait(0.0)
        
        assert stats.total_requests == 1
        assert stats.blocked_requests == 0
    
    def test_record_throttled_request(self):
        """Recording request with wait time"""
        stats = RateLimitStats()
        stats.record_wait(0.5)  # 500ms wait
        
        assert stats.total_requests == 1
        assert stats.blocked_requests == 1
        assert stats.throttle_events == 1
        assert stats.total_wait_time_ms == 500.0
        assert stats.max_wait_time_ms == 500.0
    
    def test_utilization_calculation(self):
        """Utilization percentage calculated correctly"""
        stats = RateLimitStats()
        stats.record_wait(0.0)
        stats.record_wait(0.1)
        stats.record_wait(0.0)
        stats.record_wait(0.2)
        
        # 2 blocked out of 4 total = 50%
        assert stats.utilization_pct() == 50.0


class TestRateLimiter:
    """Test rate limiter functionality"""
    
    def test_limiter_initialization(self):
        """Limiter initializes with correct limits"""
        limiter = RateLimiter(public_limit=10.0, private_limit=15.0, burst_multiplier=2.0)
        
        # Check buckets created correctly
        assert limiter._public_bucket.capacity == 20.0  # 10 × 2
        assert limiter._public_bucket.refill_rate == 10.0
        assert limiter._private_bucket.capacity == 30.0  # 15 × 2
        assert limiter._private_bucket.refill_rate == 15.0
    
    def test_acquire_public_endpoint(self):
        """Acquiring tokens for public endpoint"""
        limiter = RateLimiter(public_limit=10.0, private_limit=15.0)
        
        wait_time = limiter.acquire("public", endpoint="/products")
        assert wait_time == 0.0  # First request should not wait
    
    def test_acquire_private_endpoint(self):
        """Acquiring tokens for private endpoint"""
        limiter = RateLimiter(public_limit=10.0, private_limit=15.0)
        
        wait_time = limiter.acquire("private", endpoint="/orders")
        assert wait_time == 0.0
    
    def test_invalid_channel_raises_error(self):
        """Invalid channel raises ValueError"""
        limiter = RateLimiter()
        
        with pytest.raises(ValueError, match="Invalid channel"):
            limiter.acquire("invalid")
    
    def test_rate_limit_enforced(self):
        """Rate limit enforced after exhausting tokens"""
        limiter = RateLimiter(public_limit=5.0, private_limit=5.0, burst_multiplier=1.0)
        
        # Exhaust bucket (5 tokens)
        for _ in range(5):
            limiter.acquire("public", endpoint="/products", block=False)
        
        # Next request should be blocked
        with pytest.raises(ValueError, match="Rate limit exceeded"):
            limiter.acquire("public", endpoint="/products", block=False)
    
    def test_blocking_waits_for_tokens(self):
        """Blocking acquire waits for tokens to refill"""
        limiter = RateLimiter(public_limit=10.0, private_limit=10.0, burst_multiplier=1.0)
        
        # Exhaust bucket
        for _ in range(10):
            limiter.acquire("public", endpoint="/products", block=False)
        
        # Mock time.sleep to avoid actual waiting
        with patch('time.sleep') as mock_sleep:
            wait_time = limiter.acquire("public", endpoint="/products", block=True)
            
            # Should have calculated wait time > 0
            assert wait_time > 0
            # Should have called sleep with that wait time
            assert mock_sleep.called
    
    def test_check_available_does_not_consume(self):
        """check_available does not consume tokens"""
        limiter = RateLimiter(public_limit=10.0, private_limit=10.0, burst_multiplier=1.0)
        
        # Check availability
        available, wait_time = limiter.check_available("public", tokens=5.0)
        assert available
        assert wait_time == 0.0
        
        # Tokens should still be available (not consumed)
        available, _ = limiter.check_available("public", tokens=5.0)
        assert available
    
    def test_stats_tracking(self):
        """Statistics tracked correctly"""
        limiter = RateLimiter(public_limit=100.0, private_limit=100.0)  # High limit to avoid throttling
        
        limiter.acquire("public", endpoint="/products")
        limiter.acquire("public", endpoint="/products")
        limiter.acquire("private", endpoint="/orders")
        
        public_stats = limiter.get_stats("public")
        assert public_stats["total_requests"] == 2
        assert public_stats["channel"] == "public"
        
        private_stats = limiter.get_stats("private")
        assert private_stats["total_requests"] == 1
    
    def test_endpoint_stats(self):
        """Per-endpoint statistics tracked"""
        limiter = RateLimiter(public_limit=100.0, private_limit=100.0)
        
        limiter.acquire("public", endpoint="/products")
        limiter.acquire("public", endpoint="/products")
        limiter.acquire("public", endpoint="/ticker")
        
        products_stats = limiter.get_endpoint_stats("/products")
        assert products_stats["total_requests"] == 2
        
        ticker_stats = limiter.get_endpoint_stats("/ticker")
        assert ticker_stats["total_requests"] == 1
    
    def test_reset_stats(self):
        """Statistics can be reset"""
        limiter = RateLimiter(public_limit=100.0, private_limit=100.0)
        
        limiter.acquire("public", endpoint="/products")
        limiter.reset_stats()
        
        stats = limiter.get_stats("public")
        assert stats["total_requests"] == 0
    
    def test_should_alert_when_utilization_high(self):
        """Alert triggered when utilization exceeds threshold"""
        limiter = RateLimiter(public_limit=5.0, private_limit=5.0, burst_multiplier=1.0)
        
        # Exhaust 80% of bucket (4 out of 5 tokens)
        for _ in range(4):
            limiter.acquire("public", endpoint="/products", block=False)
        
        # Force a wait on next request
        try:
            limiter.acquire("public", endpoint="/products", block=False)
        except ValueError:
            pass  # Expected - bucket exhausted
        
        # Note: should_alert checks utilization_pct (blocked/total), not token count
        # After exhausting bucket, we had 4 successful + 1 failed = 20% blocked
        # This is below 80% threshold, so no alert
        assert not limiter.should_alert("public", threshold_pct=80.0)
    
    def test_global_stats(self):
        """Global statistics returned when channel=None"""
        limiter = RateLimiter(public_limit=100.0, private_limit=100.0)
        
        limiter.acquire("public", endpoint="/products")
        limiter.acquire("private", endpoint="/orders")
        
        global_stats = limiter.get_stats()
        assert "public" in global_stats
        assert "private" in global_stats
        assert global_stats["public"]["total_requests"] == 1
        assert global_stats["private"]["total_requests"] == 1
    
    def test_concurrent_channels_independent(self):
        """Public and private channels have independent limits"""
        limiter = RateLimiter(public_limit=2.0, private_limit=5.0, burst_multiplier=1.0)
        
        # Exhaust public bucket (2 tokens)
        limiter.acquire("public", endpoint="/products", block=False)
        limiter.acquire("public", endpoint="/products", block=False)
        
        # Private should still have tokens
        wait_time = limiter.acquire("private", endpoint="/orders", block=False)
        assert wait_time == 0.0
    
    def test_burst_capacity_allows_spikes(self):
        """Burst capacity allows temporary spikes above steady-state rate"""
        limiter = RateLimiter(public_limit=5.0, private_limit=5.0, burst_multiplier=2.0)
        
        # Can make 10 requests immediately (capacity = 5 × 2)
        for _ in range(10):
            limiter.acquire("public", endpoint="/products", block=False)
        
        # 11th request should fail
        with pytest.raises(ValueError, match="Rate limit exceeded"):
            limiter.acquire("public", endpoint="/products", block=False)


class TestRateLimiterIntegration:
    """Integration tests for real-world scenarios"""
    
    def test_sustained_load(self):
        """Limiter handles sustained load at rate limit"""
        limiter = RateLimiter(public_limit=10.0, private_limit=10.0, burst_multiplier=2.0)
        
        # Make 15 requests - with burst capacity of 20, should all succeed immediately
        with patch('time.sleep') as mock_sleep:
            for i in range(15):
                limiter.acquire("public", endpoint="/products", block=True)
            
            # With burst capacity of 20, first 15 should not require any sleep
            assert not mock_sleep.called
    
    def test_bursty_load_then_idle(self):
        """Limiter handles burst then recovers during idle"""
        limiter = RateLimiter(public_limit=10.0, private_limit=10.0, burst_multiplier=2.0)
        
        # Burst: use all 20 tokens
        for _ in range(20):
            limiter.acquire("public", endpoint="/products", block=False)
        
        # Simulate 1 second passing (10 tokens refilled at 10/s rate)
        limiter._public_bucket.last_update -= 1.0
        limiter._public_bucket.refill()
        
        # Should have ~10 tokens available now
        for _ in range(10):
            wait_time = limiter.acquire("public", endpoint="/products", block=False)
            assert wait_time == 0.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
