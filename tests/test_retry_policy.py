"""
Tests for Retry Policy with Fault Injection (REQ-CB1)

Verifies exponential backoff with full jitter for 429/5xx errors.
"""

import pytest
import time
from unittest.mock import Mock, patch, MagicMock
from requests.exceptions import HTTPError, Timeout, ConnectionError

from core.exchange_coinbase import CoinbaseExchange


@pytest.fixture
def exchange():
    """Create exchange with minimal config."""
    # Create exchange and mock _headers to avoid credential requirement
    exc = CoinbaseExchange(read_only=True)
    
    # Mock _headers to return dummy headers without requiring credentials
    from unittest.mock import patch
    patcher = patch.object(exc, '_headers', return_value={"Content-Type": "application/json"})
    patcher.start()
    
    yield exc
    
    patcher.stop()


class TestExponentialBackoff:
    """Test exponential backoff with full jitter (REQ-CB1)."""
    
    def test_backoff_increases_exponentially(self, exchange):
        """Test backoff delay increases exponentially with attempts."""
        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.raise_for_status.side_effect = HTTPError("Server error", response=mock_response)
        
        delays = []
        
        with patch('core.exchange_coinbase.requests.request') as mock_request:
            mock_request.return_value = mock_response
            
            with patch('time.sleep') as mock_sleep:
                with pytest.raises(HTTPError):
                    exchange._req("GET", "/test", max_retries=4)
                
                # Capture all sleep delays
                delays = [call.args[0] for call in mock_sleep.call_args_list]
        
        # Should have 3 retries (4 attempts total, sleep after first 3)
        assert len(delays) == 3
        
        # Delays should generally increase (allowing for jitter variance)
        # Attempt 0: 0 to 1s
        # Attempt 1: 0 to 2s
        # Attempt 2: 0 to 4s
        assert 0 <= delays[0] <= 1.0
        assert 0 <= delays[1] <= 2.0
        assert 0 <= delays[2] <= 4.0
    
    def test_full_jitter_formula_applied(self, exchange):
        """Test full jitter formula: random(0, min(cap, base * 2^attempt))."""
        mock_response = Mock()
        mock_response.status_code = 503
        mock_response.raise_for_status.side_effect = HTTPError("Service unavailable", response=mock_response)
        
        with patch('core.exchange_coinbase.requests.request') as mock_request:
            mock_request.return_value = mock_response
            
            with patch('time.sleep') as mock_sleep:
                with pytest.raises(HTTPError):
                    exchange._req("GET", "/test", max_retries=5)
                
                delays = [call.args[0] for call in mock_sleep.call_args_list]
        
        # Verify full jitter bounds for each attempt
        # Attempt 0: random(0, min(30, 1*2^0)) = random(0, 1)
        # Attempt 1: random(0, min(30, 1*2^1)) = random(0, 2)
        # Attempt 2: random(0, min(30, 1*2^2)) = random(0, 4)
        # Attempt 3: random(0, min(30, 1*2^3)) = random(0, 8)
        assert len(delays) == 4  # 5 attempts = 4 sleeps
        assert delays[0] >= 0 and delays[0] <= 1.0
        assert delays[1] >= 0 and delays[1] <= 2.0
        assert delays[2] >= 0 and delays[2] <= 4.0
        assert delays[3] >= 0 and delays[3] <= 8.0
    
    def test_backoff_capped_at_max_delay(self, exchange):
        """Test backoff delay capped at 30 seconds."""
        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.raise_for_status.side_effect = HTTPError("Server error", response=mock_response)
        
        with patch('core.exchange_coinbase.requests.request') as mock_request:
            mock_request.return_value = mock_response
            
            with patch('time.sleep') as mock_sleep:
                with pytest.raises(HTTPError):
                    exchange._req("GET", "/test", max_retries=10)  # Many retries
                
                delays = [call.args[0] for call in mock_sleep.call_args_list]
        
        # All delays should be <= 30 seconds (max cap)
        assert all(delay <= 30.0 for delay in delays)
        
        # Later delays should hit the cap
        # Attempt 5+: random(0, min(30, 1*2^5+)) = random(0, 30)
        if len(delays) >= 5:
            assert delays[4] <= 30.0  # Should be capped
    
    def test_jitter_provides_randomness(self, exchange):
        """Test jitter introduces randomness to prevent thundering herd."""
        mock_response = Mock()
        mock_response.status_code = 429
        mock_response.raise_for_status.side_effect = HTTPError("Rate limited", response=mock_response)
        
        all_delays = []
        
        # Run multiple times to capture jitter variance
        for _ in range(5):
            with patch('core.exchange_coinbase.requests.request') as mock_request:
                mock_request.return_value = mock_response
                
                with patch('time.sleep') as mock_sleep:
                    with pytest.raises(HTTPError):
                        exchange._req("GET", "/test", max_retries=3)
                    
                    delays = [call.args[0] for call in mock_sleep.call_args_list]
                    all_delays.append(delays)
        
        # Check that delays vary between runs (jitter is working)
        first_run_delays = all_delays[0]
        different_delays = [delays for delays in all_delays if delays != first_run_delays]
        
        # At least some runs should have different delays due to jitter
        assert len(different_delays) > 0, "Jitter should cause variance in delays"


class TestRetryOn429:
    """Test retry behavior for 429 (rate limit) errors."""
    
    def test_retries_on_429_rate_limit(self, exchange):
        """Test 429 (rate limit) triggers retry."""
        mock_response = Mock()
        mock_response.status_code = 429
        mock_response.raise_for_status.side_effect = HTTPError("Rate limited", response=mock_response)
        
        with patch('core.exchange_coinbase.requests.request') as mock_request:
            mock_request.return_value = mock_response
            
            with patch('time.sleep'):
                with pytest.raises(HTTPError):
                    exchange._req("GET", "/test", max_retries=3)
            
            # Should retry 3 times
            assert mock_request.call_count == 3
    
    def test_succeeds_after_429_recovery(self, exchange):
        """Test succeeds after 429 if subsequent attempt succeeds."""
        fail_response = Mock()
        fail_response.status_code = 429
        fail_response.raise_for_status.side_effect = HTTPError("Rate limited", response=fail_response)
        
        success_response = Mock()
        success_response.status_code = 200
        success_response.json.return_value = {"success": True}
        
        with patch('core.exchange_coinbase.requests.request') as mock_request:
            # First call fails with 429, second succeeds
            mock_request.side_effect = [fail_response, success_response]
            
            with patch('time.sleep'):
                result = exchange._req("GET", "/test", max_retries=3)
            
            assert result == {"success": True}
            assert mock_request.call_count == 2


class TestRetryOn5xx:
    """Test retry behavior for 5xx (server error) errors."""
    
    @pytest.mark.parametrize("status_code", [500, 502, 503, 504])
    def test_retries_on_5xx_errors(self, exchange, status_code):
        """Test 5xx errors trigger retry."""
        mock_response = Mock()
        mock_response.status_code = status_code
        mock_response.raise_for_status.side_effect = HTTPError(f"Server error {status_code}", response=mock_response)
        
        with patch('core.exchange_coinbase.requests.request') as mock_request:
            mock_request.return_value = mock_response
            
            with patch('time.sleep'):
                with pytest.raises(HTTPError):
                    exchange._req("GET", "/test", max_retries=3)
            
            # Should retry 3 times
            assert mock_request.call_count == 3
    
    def test_succeeds_after_5xx_recovery(self, exchange):
        """Test succeeds after transient 5xx error."""
        fail_response = Mock()
        fail_response.status_code = 503
        fail_response.raise_for_status.side_effect = HTTPError("Service unavailable", response=fail_response)
        
        success_response = Mock()
        success_response.status_code = 200
        success_response.json.return_value = {"data": "ok"}
        
        with patch('core.exchange_coinbase.requests.request') as mock_request:
            mock_request.side_effect = [fail_response, fail_response, success_response]
            
            with patch('time.sleep'):
                result = exchange._req("GET", "/test", max_retries=5)
            
            assert result == {"data": "ok"}
            assert mock_request.call_count == 3  # 2 failures + 1 success


class TestNoRetryOn4xx:
    """Test no retry for 4xx (client error) errors except 429."""
    
    @pytest.mark.parametrize("status_code", [400, 401, 403, 404, 422])
    def test_no_retry_on_4xx_client_errors(self, exchange, status_code):
        """Test 4xx errors (except 429) do NOT trigger retry."""
        mock_response = Mock()
        mock_response.status_code = status_code
        mock_response.raise_for_status.side_effect = HTTPError(f"Client error {status_code}", response=mock_response)
        
        with patch('core.exchange_coinbase.requests.request') as mock_request:
            mock_request.return_value = mock_response
            
            with pytest.raises(HTTPError):
                exchange._req("GET", "/test", max_retries=3)
            
            # Should only try once (no retries for 4xx except 429)
            assert mock_request.call_count == 1
    
    def test_429_is_exception_to_4xx_no_retry_rule(self, exchange):
        """Test 429 is the exception - it DOES retry despite being 4xx."""
        mock_response = Mock()
        mock_response.status_code = 429
        mock_response.raise_for_status.side_effect = HTTPError("Rate limited", response=mock_response)
        
        with patch('core.exchange_coinbase.requests.request') as mock_request:
            mock_request.return_value = mock_response
            
            with patch('time.sleep'):
                with pytest.raises(HTTPError):
                    exchange._req("GET", "/test", max_retries=3)
            
            # 429 should retry
            assert mock_request.call_count == 3


class TestNetworkErrors:
    """Test retry behavior for network errors."""
    
    def test_retries_on_timeout(self, exchange):
        """Test timeout triggers retry."""
        with patch('core.exchange_coinbase.requests.request') as mock_request:
            mock_request.side_effect = Timeout("Connection timeout")
            
            with patch('time.sleep'):
                with pytest.raises(Timeout):
                    exchange._req("GET", "/test", max_retries=3)
            
            # Should retry on timeout
            assert mock_request.call_count == 3
    
    def test_retries_on_connection_error(self, exchange):
        """Test connection error triggers retry."""
        with patch('core.exchange_coinbase.requests.request') as mock_request:
            mock_request.side_effect = ConnectionError("Network unreachable")
            
            with patch('time.sleep'):
                with pytest.raises(ConnectionError):
                    exchange._req("GET", "/test", max_retries=3)
            
            # Should retry on connection error
            assert mock_request.call_count == 3
    
    def test_succeeds_after_network_recovery(self, exchange):
        """Test succeeds after transient network error."""
        success_response = Mock()
        success_response.status_code = 200
        success_response.json.return_value = {"recovered": True}
        
        with patch('core.exchange_coinbase.requests.request') as mock_request:
            mock_request.side_effect = [
                Timeout("Timeout"),
                ConnectionError("Connection failed"),
                success_response
            ]
            
            with patch('time.sleep'):
                result = exchange._req("GET", "/test", max_retries=5)
            
            assert result == {"recovered": True}
            assert mock_request.call_count == 3


class TestMaxRetries:
    """Test max retries behavior."""
    
    def test_respects_max_retries_limit(self, exchange):
        """Test stops after max_retries attempts."""
        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.raise_for_status.side_effect = HTTPError("Server error", response=mock_response)
        
        max_retries = 5
        
        with patch('core.exchange_coinbase.requests.request') as mock_request:
            mock_request.return_value = mock_response
            
            with patch('time.sleep'):
                with pytest.raises(HTTPError):
                    exchange._req("GET", "/test", max_retries=max_retries)
            
            # Should try exactly max_retries times
            assert mock_request.call_count == max_retries
    
    def test_different_max_retries_values(self, exchange):
        """Test max_retries parameter is respected."""
        mock_response = Mock()
        mock_response.status_code = 503
        mock_response.raise_for_status.side_effect = HTTPError("Unavailable", response=mock_response)
        
        for max_retries in [1, 3, 5, 10]:
            with patch('core.exchange_coinbase.requests.request') as mock_request:
                mock_request.return_value = mock_response
                
                with patch('time.sleep'):
                    with pytest.raises(HTTPError):
                        exchange._req("GET", "/test", max_retries=max_retries)
                
                assert mock_request.call_count == max_retries


class TestSuccessNoRetry:
    """Test successful requests don't retry."""
    
    def test_success_on_first_attempt_no_retry(self, exchange):
        """Test successful first attempt doesn't trigger retry."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "success"}
        
        with patch('core.exchange_coinbase.requests.request') as mock_request:
            mock_request.return_value = mock_response
            
            with patch('time.sleep') as mock_sleep:
                result = exchange._req("GET", "/test", max_retries=5)
            
            assert result == {"status": "success"}
            assert mock_request.call_count == 1
            assert mock_sleep.call_count == 0  # No sleep on success


class TestREQCB1Compliance:
    """Test compliance with REQ-CB1 specification."""
    
    def test_exponential_backoff_implemented(self, exchange):
        """Test exponential backoff is implemented."""
        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.raise_for_status.side_effect = HTTPError("Error", response=mock_response)
        
        with patch('core.exchange_coinbase.requests.request') as mock_request:
            mock_request.return_value = mock_response
            
            with patch('time.sleep') as mock_sleep:
                with pytest.raises(HTTPError):
                    exchange._req("GET", "/test", max_retries=4)
                
                delays = [call.args[0] for call in mock_sleep.call_args_list]
        
        # Verify exponential growth pattern (with jitter tolerance)
        assert len(delays) == 3
        # Each delay should be within expected exponential range
        for i, delay in enumerate(delays):
            max_expected = min(30.0, 1.0 * (2 ** i))
            assert 0 <= delay <= max_expected
    
    def test_jitter_prevents_thundering_herd(self, exchange):
        """Test jitter adds randomness to prevent synchronized retries."""
        mock_response = Mock()
        mock_response.status_code = 503
        mock_response.raise_for_status.side_effect = HTTPError("Error", response=mock_response)
        
        # Collect delays from multiple runs
        delay_sets = []
        for _ in range(10):
            with patch('core.exchange_coinbase.requests.request') as mock_request:
                mock_request.return_value = mock_response
                
                with patch('time.sleep') as mock_sleep:
                    with pytest.raises(HTTPError):
                        exchange._req("GET", "/test", max_retries=2)
                    
                    delays = [call.args[0] for call in mock_sleep.call_args_list]
                    delay_sets.append(tuple(delays))
        
        # Verify not all runs have identical delays (jitter is working)
        unique_delays = len(set(delay_sets))
        assert unique_delays > 1, "Jitter should produce varying delays across runs"
    
    def test_full_jitter_formula_per_aws_best_practice(self, exchange):
        """
        Test full jitter formula matches AWS best practice:
        sleep = random(0, min(cap, base * 2^attempt))
        
        REQ-CB1: Exponential backoff with full jitter to prevent thundering herd.
        """
        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.raise_for_status.side_effect = HTTPError("Error", response=mock_response)
        
        with patch('core.exchange_coinbase.requests.request') as mock_request:
            mock_request.return_value = mock_response
            
            with patch('time.sleep') as mock_sleep:
                with pytest.raises(HTTPError):
                    exchange._req("GET", "/test", max_retries=6)
                
                delays = [call.args[0] for call in mock_sleep.call_args_list]
        
        # Verify full jitter formula for each attempt
        base = 1.0
        cap = 30.0
        
        for attempt, delay in enumerate(delays):
            expected_max = min(cap, base * (2 ** attempt))
            assert 0 <= delay <= expected_max, (
                f"Attempt {attempt}: delay {delay} not in range [0, {expected_max}]"
            )
    
    def test_meets_req_cb1_specification(self, exchange):
        """
        Comprehensive test that REQ-CB1 is fully implemented.
        
        REQ-CB1: Exponential backoff with full jitter for 429/5xx errors.
        """
        # 1. Retries on 429
        mock_429 = Mock()
        mock_429.status_code = 429
        mock_429.raise_for_status.side_effect = HTTPError("429", response=mock_429)
        
        with patch('core.exchange_coinbase.requests.request') as mock_request:
            mock_request.return_value = mock_429
            with patch('time.sleep'):
                with pytest.raises(HTTPError):
                    exchange._req("GET", "/test", max_retries=3)
            assert mock_request.call_count == 3
        
        # 2. Retries on 5xx
        mock_5xx = Mock()
        mock_5xx.status_code = 503
        mock_5xx.raise_for_status.side_effect = HTTPError("503", response=mock_5xx)
        
        with patch('core.exchange_coinbase.requests.request') as mock_request:
            mock_request.return_value = mock_5xx
            with patch('time.sleep'):
                with pytest.raises(HTTPError):
                    exchange._req("GET", "/test", max_retries=3)
            assert mock_request.call_count == 3
        
        # 3. Exponential backoff with jitter
        with patch('core.exchange_coinbase.requests.request') as mock_request:
            mock_request.return_value = mock_5xx
            with patch('time.sleep') as mock_sleep:
                with pytest.raises(HTTPError):
                    exchange._req("GET", "/test", max_retries=4)
                delays = [call.args[0] for call in mock_sleep.call_args_list]
        
        # Verify exponential growth
        assert len(delays) == 3
        for i, delay in enumerate(delays):
            assert 0 <= delay <= min(30.0, 1.0 * (2 ** i))
        
        # 4. No retry on 4xx (except 429)
        mock_4xx = Mock()
        mock_4xx.status_code = 400
        mock_4xx.raise_for_status.side_effect = HTTPError("400", response=mock_4xx)
        
        with patch('core.exchange_coinbase.requests.request') as mock_request:
            mock_request.return_value = mock_4xx
            with pytest.raises(HTTPError):
                exchange._req("GET", "/test", max_retries=3)
            assert mock_request.call_count == 1  # No retries
