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
    from pathlib import Path
    
    # CRITICAL: Cleanup lock file before test (prevents "instance already running" errors)
    lock_file = Path("data/247trader-v2.pid")
    if lock_file.exists():
        try:
            lock_file.unlink()
        except Exception:
            pass
    
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
    
    # Cleanup lock file after test
    if lock_file.exists():
        try:
            lock_file.unlink()
        except Exception:
            pass
