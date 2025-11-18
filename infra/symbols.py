"""Symbol normalization and alias utilities for trading symbols.

Provides a single source of truth for canonical symbol handling so modules can
accept `BTC`, `BTCUSD`, `btc-usdc`, or other common variants and still operate on
`BTC-USD` consistently. This is critical for risk checks, exposure limits, and
state reconciliations.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Tuple

DEFAULT_QUOTE = "USD"

# Common quote suffixes that exchanges or market data vendors append without a dash.
QUOTE_SUFFIXES: Tuple[str, ...] = (
    "USD",
    "USDC",
    "USDT",
    "USDP",
    "USDS",
    "DAI",
    "EUR",
    "GBP",
    "BTC",
    "ETH",
    "SOL",
)

# Known aliases for bases that should collapse into a single canonical asset key.
_BASE_ALIAS_MAP: Dict[str, str] = {
    "XBT": "BTC",
    "WBTC": "BTC",
    "RENBTC": "BTC",
}


def canonical_base(base: str) -> str:
    """Return the canonical base asset ticker (e.g., WBTC -> BTC)."""

    if not base:
        return ""
    ticker = base.upper()
    return _BASE_ALIAS_MAP.get(ticker, ticker)


def normalize_symbol(symbol: Optional[str], *, default_quote: str = DEFAULT_QUOTE) -> str:
    """Normalize a symbol to the canonical `BASE-QUOTE` format.

    Args:
        symbol: Input symbol from configs, exchange, or state.
        default_quote: Quote currency to append when missing (default: USD).

    Returns:
        Canonicalized symbol or an empty string if the input is falsy.
    """

    if not symbol:
        return ""

    token = str(symbol).strip().upper().replace(" ", "")
    if not token:
        return ""

    for delim in ("/", "_", ":"):
        token = token.replace(delim, "-")
    while "--" in token:
        token = token.replace("--", "-")
    if token.endswith("-"):
        token = token[:-1]

    if not token:
        return ""

    if "-" in token:
        base, quote = token.split("-", 1)
        base = canonical_base(base or default_quote)
        quote = quote or default_quote
        return f"{base}-{quote}"

    for quote in QUOTE_SUFFIXES:
        if token.endswith(quote):
            base = token[: -len(quote)] or quote
            base = canonical_base(base)
            return f"{base}-{quote}"

    base = canonical_base(token)
    return f"{base}-{default_quote}"


def extract_base_quote(symbol: Optional[str], *, default_quote: str = DEFAULT_QUOTE) -> Tuple[str, str]:
    """Return (base, quote) tuple for any symbol variant."""

    normalized = normalize_symbol(symbol, default_quote=default_quote)
    if not normalized:
        return "", default_quote
    if "-" not in normalized:
        return normalized, default_quote
    base, quote = normalized.split("-", 1)
    return base, quote


def equivalent_symbols(lhs: Optional[str], rhs: Optional[str]) -> bool:
    """Return True if two symbols resolve to the same canonical key."""

    return normalize_symbol(lhs) == normalize_symbol(rhs)


def merge_symbol_value_map(values: Optional[Mapping[str, Any]]) -> Dict[str, float]:
    """Aggregate a mapping of symbol -> numeric value using canonical keys."""

    merged: Dict[str, float] = {}
    if not values:
        return merged

    for raw_symbol, value in values.items():
        symbol = normalize_symbol(raw_symbol)
        if not symbol:
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        merged[symbol] = merged.get(symbol, 0.0) + numeric
    return merged


def canonicalize_symbol_keys(payload: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Return a shallow copy of a dict with canonical symbol keys."""

    canonical: Dict[str, Any] = {}
    if not payload:
        return canonical
    for raw_symbol, value in payload.items():
        symbol = normalize_symbol(raw_symbol)
        if not symbol:
            continue
        canonical[symbol] = value
    return canonical


__all__ = [
    "DEFAULT_QUOTE",
    "QUOTE_SUFFIXES",
    "canonical_base",
    "normalize_symbol",
    "extract_base_quote",
    "equivalent_symbols",
    "merge_symbol_value_map",
    "canonicalize_symbol_keys",
]
