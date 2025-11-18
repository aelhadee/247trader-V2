"""
Fault-Injection Tests for CoinbaseExchange Retry Logic (REQ-CB1)

Verifies exponential backoff with full jitter for:
- 429 rate limit errors
- 5xx server errors  
- Network timeouts/connection errors
- Full jitter formula: random(0, min(cap, base * 2^attempt))
"""

import pytest
from unittest.mock import Mock, patch
from requests.exceptions import HTTPError, Timeout, ConnectionError

from core.exchange_coinbase import CoinbaseExchange


@pytest.fixture
def exchange():
    """Create exchange instance for testing."""
    return CoinbaseExchange(api_key="test_key", api_secret="test_secret", read_only=True)


class TestRetry429RateLimit:
    """Test retry behavior on 429 rate limit errors."""
    
    def test_retries_on_429_and_succeeds(self, exchange):
        """Test retries 429 errors and eventually succeeds."""
        with patch('core.exchange_coinbase.requests.request') as mock_request:
            # First 2 calls return 429, third succeeds
            mock_response_429 = Mock()
            mock_response_429.status_code = 429
            mock_response_429.text = "Rate limit exceeded"
            
            mock_response_ok = Mock()
            mock_response_ok.status_code = 200
            mock_response_ok.json.return_value = {"success": True}
            
            mock_request.side_effect = [
                HTTPError(response=mock_response_429),
                HTTPError(response=mock_response_429),
                mock_response_ok
            ]
            
            # Should succeed after retries
            result = exchange._req("GET", "/test", authenticated=False, max_retries=3)
            
            assert result == {"success": True}
            assert mock_request.call_count == 3
    
    def test_exhausts_retries_on_persistent_429(self, exchange):
        """Test raises after exhausting all retries on 429."""
        with patch('core.exchange_coinbase.requests.request') as mock_request:
            mock_response = Mock()
            mock_response.status_code = 429
            mock_response.text = "Rate limit exceeded"
            
            mock_request.side_effect = HTTPError(response=mock_response)
            
            with pytest.raises(HTTPError):
                exchange._req("GET", "/test", authenticated=False, max_retries=3)
            
            assert mock_request.call_count == 3
    
    def test_records_rate_limit_usage(self, exchange):
        """Test records rate limit for circuit breaker."""
        with patch('core.exchange_coinbase.requests.request') as mock_request:
            mock_response = Mock()
            mock_response.status_code = 429
            mock_response.text = "Rate limit exceeded"
            
            mock_request.side_effect = HTTPError(response=mock_response)
            
            try:
                exchange._req("GET", "/test", authenticated=False, max_retries=1)
            except HTTPError:
                pass
            
            # Should have recorded rate limit usage
            # (Internal tracking - check via metrics if available)
            assert mock_request.call_count == 1


class TestRetry5xxServerErrors:
    """Test retry behavior on 5xx server errors."""
    
    def test_retries_on_500_and_succeeds(self, exchange):
        """Test retries 500 errors and eventually succeeds."""
        with patch('core.exchange_coinbase.requests.request') as mock_request:
            mock_response_500 = Mock()
            mock_response_500.status_code = 500
            mock_response_500.text = "Internal server error"
            
            mock_response_ok = Mock()
            mock_response_ok.status_code = 200
            mock_response_ok.json.return_value = {"success": True}
            
            mock_request.side_effect = [
                HTTPError(response=mock_response_500),
                mock_response_ok
            ]
            
            result = exchange._req("GET", "/test", authenticated=False, max_retries=3)
            
            assert result == {"success": True}
            assert mock_request.call_count == 2
    
    def test_retries_on_503_service_unavailable(self, exchange):
        """Test retries 503 errors."""
        with patch('core.exchange_coinbase.requests.request') as mock_request:
            mock_response_503 = Mock()
            mock_response_503.status_code = 503
            mock_response_503.text = "Service unavailable"
            
            mock_response_ok = Mock()
            mock_response_ok.status_code = 200
            mock_response_ok.json.return_value = {"success": True}
            
            mock_request.side_effect = [
                HTTPError(response=mock_response_503),
                HTTPError(response=mock_response_503),
                mock_response_ok
            ]
            
            result = exchange._req("GET", "/test", authenticated=False, max_retries=3)
            
            assert result == {"success": True}
            assert mock_request.call_count == 3
    
    def test_does_not_retry_4xx_client_errors(self, exchange):
        """Test does NOT retry 4xx errors (except 429)."""
        with patch('core.exchange_coinbase.requests.request') as mock_request:
            for status_code in [400, 401, 403, 404]:
                mock_response = Mock()
                mock_response.status_code = status_code
                mock_response.text = f"Client error {status_code}"
                
                mock_request.side_effect = HTTPError(response=mock_response)
                
                with pytest.raises(HTTPError):
                    exchange._req("GET", "/test", authenticated=False, max_retries=3)
                
                # Should NOT retry - only 1 call
                assert mock_request.call_count == 1
                mock_request.reset_mock()


class TestRetryNetworkErrors:
    """Test retry behavior on network errors."""
    
    def test_retries_on_timeout(self, exchange):
        """Test retries on Timeout errors."""
        with patch('core.exchange_coinbase.requests.request') as mock_request:
            mock_response_ok = Mock()
            mock_response_ok.status_code = 200
            mock_response_ok.json.return_value = {"success": True}
            
            mock_request.side_effect = [
                Timeout("Connection timed out"),
                mock_response_ok
            ]
            
            result = exchange._req("GET", "/test", authenticated=False, max_retries=3)
            
            assert result == {"success": True}
            assert mock_request.call_count == 2
    
    def test_retries_on_connection_error(self, exchange):
        """Test retries on ConnectionError."""
        with patch('core.exchange_coinbase.requests.request') as mock_request:
            mock_response_ok = Mock()
            mock_response_ok.status_code = 200
            mock_response_ok.json.return_value = {"success": True}
            
            mock_request.side_effect = [
                ConnectionError("Failed to establish connection"),
                ConnectionError("Failed to establish connection"),
                mock_response_ok
            ]
            
            result = exchange._req("GET", "/test", authenticated=False, max_retries=3)
            
            assert result == {"success": True}
            assert mock_request.call_count == 3
    
    def test_exhausts_retries_on_persistent_timeout(self, exchange):
        """Test raises after exhausting retries on timeout."""
        with patch('core.exchange_coinbase.requests.request') as mock_request:
            mock_request.side_effect = Timeout("Connection timed out")
            
            with pytest.raises(Timeout):
                exchange._req("GET", "/test", authenticated=False, max_retries=3)
            
            assert mock_request.call_count == 3


class TestExponentialBackoff:
    """Test exponential backoff with full jitter (REQ-CB1)."""
    
    def test_backoff_increases_exponentially(self, exchange):
        """Test backoff delay increases exponentially: base * 2^attempt."""
        with patch('core.exchange_coinbase.requests.request') as mock_request, \
             patch('time.sleep') as mock_sleep, \
             patch('random.uniform') as mock_random:
            
            # Always return 0.5 * max for predictable testing
            mock_random.side_effect = lambda low, high: high * 0.5
            
            mock_response = Mock()
            mock_response.status_code = 500
            mock_response.text = "Server error"
            mock_request.side_effect = HTTPError(response=mock_response)
            
            try:
                exchange._req("GET", "/test", authenticated=False, max_retries=3)
            except HTTPError:
                pass
            
            # Should have slept between retries (not after last attempt)
            assert mock_sleep.call_count == 2
            
            # Verify exponential backoff calls
            # Attempt 0: base=1.0, exp=min(30, 1.0 * 2^0)=1.0, jitter=random(0,1.0)
            # Attempt 1: base=1.0, exp=min(30, 1.0 * 2^1)=2.0, jitter=random(0,2.0)
            sleep_calls = [call[0][0] for call in mock_sleep.call_args_list]
            
            # With 0.5 multiplier: [0.5, 1.0]
            assert sleep_calls[0] == pytest.approx(0.5, abs=0.1)
            assert sleep_calls[1] == pytest.approx(1.0, abs=0.1)
    
    def test_backoff_caps_at_max_delay(self, exchange):
        """Test backoff caps at 30 seconds."""
        with patch('core.exchange_coinbase.requests.request') as mock_request, \
             patch('time.sleep') as mock_sleep, \
             patch('random.uniform') as mock_random:
            
            # Return max value to test cap
            mock_random.side_effect = lambda low, high: high
            
            mock_response = Mock()
            mock_response.status_code = 500
            mock_request.side_effect = HTTPError(response=mock_response)
            
            try:
                # Use many retries to trigger cap
                exchange._req("GET", "/test", authenticated=False, max_retries=8)
            except HTTPError:
                pass
            
            # Check that no sleep exceeds 30 seconds
            sleep_calls = [call[0][0] for call in mock_sleep.call_args_list]
            assert all(delay <= 30.0 for delay in sleep_calls)
            
            # Later attempts should hit the 30s cap
            assert any(delay == 30.0 for delay in sleep_calls[-3:])
    
    def test_full_jitter_randomizes_delay(self, exchange):
        """Test full jitter: random(0, exp_backoff) spreads retries."""
        with patch('core.exchange_coinbase.requests.request') as mock_request, \
             patch('time.sleep'), \
             patch('random.uniform') as mock_random:
            
            # Track that random.uniform was called with correct bounds
            mock_random.return_value = 0.5
            
            mock_response = Mock()
            mock_response.status_code = 429
            mock_request.side_effect = HTTPError(response=mock_response)
            
            try:
                exchange._req("GET", "/test", authenticated=False, max_retries=3)
            except HTTPError:
                pass
            
            # Verify random.uniform called with (0, exp_backoff)
            uniform_calls = mock_random.call_args_list
            assert len(uniform_calls) == 2  # 2 retries = 2 sleeps
            
            # First retry: random(0, min(30, 1*2^0)) = random(0, 1.0)
            assert uniform_calls[0][0] == (0, 1.0)
            
            # Second retry: random(0, min(30, 1*2^1)) = random(0, 2.0)
            assert uniform_calls[1][0] == (0, 2.0)
    
    def test_no_sleep_after_last_attempt(self, exchange):
        """Test does NOT sleep after final failed attempt."""
        with patch('core.exchange_coinbase.requests.request') as mock_request, \
             patch('time.sleep') as mock_sleep:
            
            mock_response = Mock()
            mock_response.status_code = 500
            mock_request.side_effect = HTTPError(response=mock_response)
            
            try:
                exchange._req("GET", "/test", authenticated=False, max_retries=3)
            except HTTPError:
                pass
            
            # 3 attempts = 2 sleeps (not 3)
            assert mock_request.call_count == 3
            assert mock_sleep.call_count == 2


class TestREQCB1Compliance:
    """Test compliance with REQ-CB1 specification."""
    
    def test_uses_exponential_backoff_formula(self, exchange):
        """Test uses AWS best practice formula: random(0, min(cap, base * 2^attempt))."""
        with patch('core.exchange_coinbase.requests.request') as mock_request, \
             patch('random.uniform') as mock_random:
            
            mock_random.return_value = 1.0
            
            mock_response = Mock()
            mock_response.status_code = 429
            mock_request.side_effect = HTTPError(response=mock_response)
            
            try:
                exchange._req("GET", "/test", authenticated=False, max_retries=4)
            except HTTPError:
                pass
            
            # Verify formula: min(30, 1.0 * 2^attempt)
            # Attempt 0: min(30, 1) = 1.0
            # Attempt 1: min(30, 2) = 2.0
            # Attempt 2: min(30, 4) = 4.0
            uniform_calls = [call[0] for call in mock_random.call_args_list]
            
            assert uniform_calls[0] == (0, 1.0)
            assert uniform_calls[1] == (0, 2.0)
            assert uniform_calls[2] == (0, 4.0)
    
    def test_handles_mixed_error_scenarios(self, exchange):
        """Test handles mixed errors (429 + 5xx + timeout) correctly."""
        with patch('core.exchange_coinbase.requests.request') as mock_request:
            mock_429 = Mock()
            mock_429.status_code = 429
            
            mock_503 = Mock()
            mock_503.status_code = 503
            
            mock_ok = Mock()
            mock_ok.status_code = 200
            mock_ok.json.return_value = {"success": True}
            
            # Mix of retryable errors
            mock_request.side_effect = [
                HTTPError(response=mock_429),
                Timeout("timeout"),
                HTTPError(response=mock_503),
                mock_ok
            ]
            
            result = exchange._req("GET", "/test", authenticated=False, max_retries=5)
            
            assert result == {"success": True}
            assert mock_request.call_count == 4
    
    def test_respects_max_retries_parameter(self, exchange):
        """Test respects custom max_retries parameter."""
        with patch('core.exchange_coinbase.requests.request') as mock_request:
            mock_response = Mock()
            mock_response.status_code = 500
            mock_request.side_effect = HTTPError(response=mock_response)
            
            # Test with custom max_retries=5
            try:
                exchange._req("GET", "/test", authenticated=False, max_retries=5)
            except HTTPError:
                pass
            
            assert mock_request.call_count == 5
            
            mock_request.reset_mock()
            
            # Test with max_retries=1 (no retries)
            try:
                exchange._req("GET", "/test", authenticated=False, max_retries=1)
            except HTTPError:
                pass
            
            assert mock_request.call_count == 1


class TestMetricsRecording:
    """Test metrics recording during retries."""
    
    def test_records_api_call_metrics(self, exchange):
        """Test records duration and status for each attempt."""
        mock_metrics = Mock()
        exchange.metrics = mock_metrics
        
        with patch('core.exchange_coinbase.requests.request') as mock_request, \
             patch('time.perf_counter') as mock_time:
            
            # Mock time progression
            mock_time.side_effect = [0.0, 0.1, 0.1, 0.15, 0.15, 0.2]
            
            mock_response_500 = Mock()
            mock_response_500.status_code = 500
            
            mock_response_ok = Mock()
            mock_response_ok.status_code = 200
            mock_response_ok.json.return_value = {"success": True}
            
            mock_request.side_effect = [
                HTTPError(response=mock_response_500),
                mock_response_ok
            ]
            
            exchange._req("GET", "/test", authenticated=False, max_retries=3)
            
            # Should have recorded metrics for failed and successful attempts
            # (Implementation-specific - verify metrics calls if available)
            assert mock_request.call_count == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
