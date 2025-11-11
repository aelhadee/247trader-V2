import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

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
    assert captured["path"].endswith("/test/endpoint?foo=1&bar=baz")
    assert captured["request"]["url"] == f"{CB_BASE}/test/endpoint?foo=1&bar=baz"
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
