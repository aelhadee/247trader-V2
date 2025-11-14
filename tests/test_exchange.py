import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, ANY

import pytest

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.exchange_coinbase import CB_BASE, CoinbaseExchange  # noqa: E402


def test_req_includes_query_string(monkeypatch):
    exchange = CoinbaseExchange(api_key="key", api_secret="secret", read_only=True)

    captured = {}

    def fake_headers(method, path, body):
        captured["method"] = method
        captured["path"] = path
        captured["body"] = body
        return {"X-Test": "ok"}

    def fake_request(method, url, headers, json, timeout):
        captured["request"] = {
            "method": method,
            "url": url,
            "headers": headers,
            "json": json,
            "timeout": timeout,
        }
        response = MagicMock()
        response.json.return_value = {"ok": True}
        response.raise_for_status.return_value = None
        return response

    monkeypatch.setattr(exchange, "_headers", fake_headers)
    monkeypatch.setattr("requests.request", fake_request)

    result = exchange._req(
        "GET",
        "/test/endpoint",
        authenticated=True,
        query={"foo": 1, "bar": "baz"},
        max_retries=1,
    )

    assert result == {"ok": True}
    # Query params may be reordered (sorted for stable signatures)
    assert "?bar=baz" in captured["path"] and "&foo=1" in captured["path"], \
        f"Expected query params in path, got: {captured['path']}"
    assert "?bar=baz" in captured["request"]["url"] and "&foo=1" in captured["request"]["url"], \
        f"Expected query params in URL, got: {captured['request']['url']}"
    assert captured["request"]["headers"].get("X-Test") == "ok"
    assert captured["request"]["json"] is None


def test_get_convert_trade_passes_query(monkeypatch):
    exchange = CoinbaseExchange(api_key="key", api_secret="secret", read_only=True)

    called = SimpleNamespace(query=None)

    def fake_req(method, endpoint, body=None, authenticated=True, max_retries=3, query=None):
        called.query = query
        return {"trade": {"id": "123", "status": "OK"}}

    monkeypatch.setattr(exchange, "_req", fake_req)

    result = exchange.get_convert_trade("abc", "from", "to")

    assert result == {"trade": {"id": "123", "status": "OK"}}
    assert called.query == {"from_account": "from", "to_account": "to"}


def test_req_records_metrics(monkeypatch):
    metrics = SimpleNamespace(
        record_rate_limit_usage=MagicMock(),
        record_api_call=MagicMock(),
    )
    exchange = CoinbaseExchange(api_key="key", api_secret="secret", read_only=True, metrics=metrics)
    exchange.configure_rate_limits({"private": 10})

    def fake_headers(method, path, body):
        return {"X-Test": "ok"}

    def fake_request(method, url, headers, json, timeout):
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"ok": True}
        return response

    monkeypatch.setattr(exchange, "_headers", fake_headers)
    monkeypatch.setattr("requests.request", fake_request)

    result = exchange._req("GET", "/metrics/test", authenticated=True, max_retries=1)

    assert result == {"ok": True}
    metrics.record_rate_limit_usage.assert_called()
    metrics.record_api_call.assert_called_with("metrics/test", "private", ANY, "success")
