"""
Tests for credential enforcement (environment-only loading)

Verifies that:
1. Credentials must come from environment variables
2. Clear error messages when credentials missing
3. Format validation catches obviously invalid credentials
4. Helper function works correctly
"""

import pytest
import os
from unittest.mock import patch
from core.exchange_coinbase import CoinbaseExchange, validate_credentials_available


def test_missing_credentials_raises_error():
    """Test that missing credentials raise clear error"""
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(ValueError) as exc_info:
            CoinbaseExchange(read_only=False)
        
        error_msg = str(exc_info.value)
        assert "CB_API_KEY" in error_msg
        assert "CB_API_SECRET" in error_msg
        assert "environment variables" in error_msg
        print(f"✅ Clear error message: {error_msg[:100]}...")


def test_missing_api_key_only():
    """Test that missing API key is detected"""
    with patch.dict(os.environ, {"CB_API_SECRET": "test-secret-value-here-12345"}, clear=True):
        with pytest.raises(ValueError) as exc_info:
            CoinbaseExchange(read_only=False)
        
        error_msg = str(exc_info.value)
        assert "CB_API_KEY" in error_msg
        print("✅ Detected missing API key")


def test_missing_api_secret_only():
    """Test that missing API secret is detected"""
    with patch.dict(os.environ, {"CB_API_KEY": "test-key"}, clear=True):
        with pytest.raises(ValueError) as exc_info:
            CoinbaseExchange(read_only=False)
        
        error_msg = str(exc_info.value)
        assert "CB_API_SECRET" in error_msg
        print("✅ Detected missing API secret")


def test_read_only_mode_allows_missing_credentials():
    """Test that read_only=True allows missing credentials"""
    with patch.dict(os.environ, {}, clear=True):
        # Should not raise
        exchange = CoinbaseExchange(read_only=True)
        assert exchange.read_only is True
        print("✅ Read-only mode tolerates missing credentials")


def test_invalid_api_key_format():
    """Test that obviously invalid API key is rejected"""
    with patch.dict(os.environ, {"CB_API_KEY": "abc", "CB_API_SECRET": "test-secret-value-here-12345"}, clear=True):
        with pytest.raises(ValueError) as exc_info:
            CoinbaseExchange(read_only=False)
        
        error_msg = str(exc_info.value)
        assert "invalid" in error_msg.lower()
        assert "too short" in error_msg.lower()
        print("✅ Detected invalid API key format")


def test_invalid_api_secret_format():
    """Test that obviously invalid API secret is rejected"""
    with patch.dict(os.environ, {"CB_API_KEY": "test-key-12345", "CB_API_SECRET": "short"}, clear=True):
        with pytest.raises(ValueError) as exc_info:
            CoinbaseExchange(read_only=False)
        
        error_msg = str(exc_info.value)
        assert "invalid" in error_msg.lower()
        assert "too short" in error_msg.lower()
        print("✅ Detected invalid API secret format")


def test_valid_credentials_accepted():
    """Test that valid-looking credentials are accepted"""
    with patch.dict(os.environ, {
        "CB_API_KEY": "test-api-key-1234567890",
        "CB_API_SECRET": "test-api-secret-value-here-minimum-20-chars"
    }, clear=True):
        # Should not raise
        exchange = CoinbaseExchange(read_only=False)
        assert exchange.api_key == "test-api-key-1234567890"
        assert exchange.api_secret == "test-api-secret-value-here-minimum-20-chars"
        print("✅ Valid credentials accepted")


def test_alternate_env_var_names():
    """Test that COINBASE_API_KEY/SECRET also work"""
    with patch.dict(os.environ, {
        "COINBASE_API_KEY": "test-api-key-1234567890",
        "COINBASE_API_SECRET": "test-api-secret-value-here-minimum-20-chars"
    }, clear=True):
        exchange = CoinbaseExchange(read_only=False)
        assert exchange.api_key == "test-api-key-1234567890"
        print("✅ Alternate environment variable names work")


def test_cb_api_key_takes_precedence():
    """Test that CB_API_KEY takes precedence over COINBASE_API_KEY"""
    with patch.dict(os.environ, {
        "CB_API_KEY": "preferred-key-1234567890",
        "COINBASE_API_KEY": "fallback-key-1234567890",
        "CB_API_SECRET": "test-api-secret-value-here-minimum-20-chars"
    }, clear=True):
        exchange = CoinbaseExchange(read_only=False)
        assert exchange.api_key == "preferred-key-1234567890"
        print("✅ CB_API_KEY takes precedence")


def test_validate_credentials_helper():
    """Test the validate_credentials_available helper function"""
    # Test with missing credentials
    with patch.dict(os.environ, {}, clear=True):
        valid, error = validate_credentials_available(require_credentials=False)
        assert not valid
        assert "missing" in error.lower()
        print("✅ Helper detects missing credentials")
    
    # Test with valid credentials
    with patch.dict(os.environ, {
        "CB_API_KEY": "test-api-key-1234567890",
        "CB_API_SECRET": "test-api-secret-value-here-minimum-20-chars"
    }, clear=True):
        valid, error = validate_credentials_available(require_credentials=False)
        assert valid
        assert error == ""
        print("✅ Helper validates good credentials")


def test_validate_credentials_require_mode():
    """Test that validate_credentials_available raises when required"""
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(ValueError) as exc_info:
            validate_credentials_available(require_credentials=True)
        
        error_msg = str(exc_info.value)
        assert "missing" in error_msg.lower()
        print("✅ Helper raises when require_credentials=True")


def test_pem_key_detection():
    """Test that PEM keys are detected correctly"""
    pem_key = """-----BEGIN EC PRIVATE KEY-----
MHcCAQEEIBKKw12pLBn1D/jI7PyVcDLKvGDzXDrLs6xpSk7mXaHwoAoGCCqGSM49
AwEHoUQDQgAEG0JhE0SHUl4sO0H0I+6QKyFoUqKjvqpL7TkOg3VH8R9cL2hKqJBN
-----END EC PRIVATE KEY-----"""
    
    with patch.dict(os.environ, {
        "CB_API_KEY": "organizations/test/apiKeys/test",
        "CB_API_SECRET": pem_key
    }, clear=True):
        exchange = CoinbaseExchange(read_only=False)
        assert exchange._mode == "pem"
        assert exchange._pem == pem_key
        print("✅ PEM key detected for Cloud API authentication")


def test_hmac_key_detection():
    """Test that HMAC keys are detected correctly"""
    with patch.dict(os.environ, {
        "CB_API_KEY": "test-hmac-key-1234567890",
        "CB_API_SECRET": "test-hmac-secret-value-here-minimum-20-chars"
    }, clear=True):
        exchange = CoinbaseExchange(read_only=False)
        assert exchange._mode == "hmac"
        assert exchange._pem is None
        print("✅ HMAC key detected for legacy authentication")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
