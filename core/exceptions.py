"""Shared exception types for core trading logic."""

from typing import Optional


class CriticalDataUnavailable(RuntimeError):
    """Raised when required market or account data cannot be fetched safely."""

    def __init__(self, source: str, original: Optional[Exception] = None):
        super().__init__(source)
        self.source = source
        self.original = original
