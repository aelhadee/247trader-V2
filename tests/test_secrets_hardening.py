"""
Tests for secrets/credential handling in CoinbaseExchange.

Validates that credentials are properly loaded from environment variables
and that file-based fallbacks are removed for security.
"""
import pytest
import os
from unittest.mock import patch
from core.exchange_coinbase import CoinbaseExchange


class TestCredentialLoading:
    """Test credential loading from environment variables"""
    
    def test_credentials_from_parameters(self):
        """Credentials can be passed as parameters"""
        exchange = CoinbaseExchange(
            api_key="test_key",
            api_secret="test_secret",
            read_only=True
        )
        
        assert exchange.api_key == "test_key"
        assert exchange.api_secret == "test_secret"
    
    @patch.dict(os.environ, {"CB_API_KEY": "env_key", "CB_API_SECRET": "env_secret"})
    def test_credentials_from_environment_cb_prefix(self):
        """Credentials loaded from CB_API_KEY and CB_API_SECRET env vars"""
        exchange = CoinbaseExchange(read_only=True)
        
        assert exchange.api_key == "env_key"
        assert exchange.api_secret == "env_secret"
    
    @patch.dict(os.environ, {"COINBASE_API_KEY": "cb_key", "COINBASE_API_SECRET": "cb_secret"})
    def test_credentials_from_environment_coinbase_prefix(self):
        """Credentials loaded from COINBASE_API_KEY and COINBASE_API_SECRET env vars"""
        exchange = CoinbaseExchange(read_only=True)
        
        assert exchange.api_key == "cb_key"
        assert exchange.api_secret == "cb_secret"
    
    @patch.dict(os.environ, {
        "CB_API_KEY": "cb_key",
        "CB_API_SECRET": "cb_secret",
        "COINBASE_API_KEY": "coinbase_key",
        "COINBASE_API_SECRET": "coinbase_secret"
    })
    def test_cb_prefix_takes_precedence(self):
        """CB_API_* prefix takes precedence over COINBASE_API_*"""
        exchange = CoinbaseExchange(read_only=True)
        
        assert exchange.api_key == "cb_key"
        assert exchange.api_secret == "cb_secret"
    
    def test_parameters_override_environment(self):
        """Parameters override environment variables"""
        with patch.dict(os.environ, {"CB_API_KEY": "env_key", "CB_API_SECRET": "env_secret"}):
            exchange = CoinbaseExchange(
                api_key="param_key",
                api_secret="param_secret",
                read_only=True
            )
            
            assert exchange.api_key == "param_key"
            assert exchange.api_secret == "param_secret"
    
    @patch.dict(os.environ, {}, clear=True)
    def test_missing_credentials_allowed_in_read_only(self):
        """Missing credentials allowed when read_only=True"""
        exchange = CoinbaseExchange(read_only=True)
        
        assert exchange.api_key == ""
        assert exchange.api_secret == ""
        assert exchange.read_only is True
    
    @patch.dict(os.environ, {}, clear=True)
    def test_missing_credentials_fails_in_live_mode(self):
        """Missing credentials raises ValueError when read_only=False"""
        with pytest.raises(ValueError, match="LIVE mode requires credentials"):
            CoinbaseExchange(read_only=False)
    
    @patch.dict(os.environ, {"CB_API_KEY": "key_only"}, clear=True)
    def test_missing_secret_fails_in_live_mode(self):
        """Missing secret raises ValueError when read_only=False"""
        with pytest.raises(ValueError, match="LIVE mode requires credentials"):
            CoinbaseExchange(read_only=False)
    
    @patch.dict(os.environ, {"CB_API_SECRET": "secret_only"}, clear=True)
    def test_missing_key_fails_in_live_mode(self):
        """Missing key raises ValueError when read_only=False"""
        with pytest.raises(ValueError, match="LIVE mode requires credentials"):
            CoinbaseExchange(read_only=False)
    
    @patch.dict(os.environ, {"CB_API_KEY": "valid_api_key_min_10chars", "CB_API_SECRET": "valid_secret_min_20_characters"})
    def test_valid_credentials_in_live_mode(self):
        """Valid credentials allow LIVE mode initialization"""
        exchange = CoinbaseExchange(read_only=False)
        
        assert exchange.api_key == "valid_api_key_min_10chars"
        assert exchange.api_secret == "valid_secret_min_20_characters"
        assert exchange.read_only is False


class TestPEMKeyHandling:
    """Test PEM key detection for Cloud API authentication"""
    
    @patch.dict(os.environ, {
        "CB_API_KEY": "cloud_key",
        "CB_API_SECRET": "-----BEGIN EC PRIVATE KEY-----\nMIHc...\n-----END EC PRIVATE KEY-----"
    })
    def test_pem_key_detected(self):
        """PEM key format detected and sets authentication mode to 'pem'"""
        exchange = CoinbaseExchange(read_only=True)
        
        assert exchange._mode == "pem"
        assert exchange._pem is not None
        assert "-----BEGIN" in exchange._pem
    
    @patch.dict(os.environ, {
        "CB_API_KEY": "hmac_key",
        "CB_API_SECRET": "base64encodedhmackey"
    })
    def test_hmac_key_mode(self):
        """Non-PEM key uses HMAC authentication mode"""
        exchange = CoinbaseExchange(read_only=True)
        
        assert exchange._mode == "hmac"
        assert exchange._pem is None
    
    def test_pem_key_with_escaped_newlines(self):
        """PEM key with escaped newlines (\\n) properly converted"""
        pem_with_escapes = "-----BEGIN EC PRIVATE KEY-----\\nMIHc...\\n-----END EC PRIVATE KEY-----"
        
        exchange = CoinbaseExchange(
            api_key="test_key",
            api_secret=pem_with_escapes,
            read_only=True
        )
        
        # Escaped \n should be converted to actual newlines
        assert "\n" in exchange.api_secret
        assert "\\n" not in exchange.api_secret


class TestSecurityHardening:
    """Test that file-based credential loading is removed"""
    
    @patch.dict(os.environ, {"CB_API_SECRET_FILE": "/tmp/fake_creds.json"}, clear=True)
    def test_file_based_loading_removed(self):
        """CB_API_SECRET_FILE environment variable no longer loads credentials"""
        # Even if CB_API_SECRET_FILE is set, it should be ignored
        exchange = CoinbaseExchange(read_only=True)
        
        # Credentials should be empty (not loaded from file)
        assert exchange.api_key == ""
        assert exchange.api_secret == ""
    
    @patch.dict(os.environ, {
        "CB_API_SECRET_FILE": "/tmp/fake_creds.json",
        "CB_API_KEY": "env_key",
        "CB_API_SECRET": "env_secret"
    })
    def test_environment_variables_still_work_with_file_var_set(self):
        """Environment variables work even if CB_API_SECRET_FILE is set"""
        exchange = CoinbaseExchange(read_only=True)
        
        # Should load from CB_API_KEY/SECRET, not from file
        assert exchange.api_key == "env_key"
        assert exchange.api_secret == "env_secret"


class TestReadOnlyMode:
    """Test read-only mode behavior"""
    
    @patch.dict(os.environ, {}, clear=True)
    def test_read_only_true_allows_missing_creds(self):
        """read_only=True allows initialization without credentials"""
        exchange = CoinbaseExchange(read_only=True)
        
        assert exchange.read_only is True
        assert exchange.api_key == ""
        assert exchange.api_secret == ""
    
    @patch.dict(os.environ, {"CB_API_KEY": "key", "CB_API_SECRET": "secret"})
    def test_read_only_false_requires_valid_creds(self):
        """read_only=False requires valid credentials"""
        exchange = CoinbaseExchange(read_only=False)
        
        assert exchange.read_only is False
        assert exchange.api_key == "key"
        assert exchange.api_secret == "secret"


class TestErrorMessages:
    """Test error message clarity"""
    
    @patch.dict(os.environ, {}, clear=True)
    def test_missing_credentials_error_message(self):
        """Error message provides clear guidance for missing credentials"""
        with pytest.raises(ValueError) as exc_info:
            CoinbaseExchange(read_only=False)
        
        error_msg = str(exc_info.value)
        assert "LIVE mode requires credentials" in error_msg
        assert "CB_API_KEY" in error_msg
        assert "CB_API_SECRET" in error_msg
        assert "read_only=True" in error_msg


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
