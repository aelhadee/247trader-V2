"""
Pytest configuration and fixtures for 247trader-v2 tests.

This conftest.py provides shared fixtures and hooks for all tests.
"""
import pytest


@pytest.fixture(autouse=True)
def reset_singletons():
    """
    Reset singleton instances between tests to ensure test isolation.
    
    This is applied automatically to all tests (autouse=True).
    """
    # CRITICAL: Reset BEFORE test (cleanup from previous test pollution)
    try:
        from infra.metrics import MetricsRecorder
        MetricsRecorder._reset_for_testing()
    except ImportError:
        pass  # Module not available in some test contexts
    
    # Run test
    yield
    
    # Cleanup after test
    try:
        from infra.metrics import MetricsRecorder
        MetricsRecorder._reset_for_testing()
    except ImportError:
        pass  # Module not available in some test contexts
