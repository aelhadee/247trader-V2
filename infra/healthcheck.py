"""Lightweight HTTP health endpoint for operational monitoring."""

from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)


class HealthServer:
    """Simple JSON health server with pluggable status provider."""

    def __init__(self, port: int, status_provider: Callable[[], Dict[str, Any]]):
        self._port = int(port)
        self._status_provider = status_provider
        self._server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    @property
    def port(self) -> Optional[int]:
        if self._server is None:
            return None
        return self._server.server_port

    def start(self) -> None:
        if self._thread:
            return

        handler_cls = self._build_handler(self._status_provider)
        self._server = HTTPServer(("0.0.0.0", self._port), handler_cls)
        self._thread = threading.Thread(target=self._server.serve_forever, name="HealthServer", daemon=True)
        self._thread.start()
        logger.info("Health server listening on 0.0.0.0:%s", self._server.server_port)

    def stop(self) -> None:
        if not self._server:
            return
        try:
            self._server.shutdown()
            self._server.server_close()
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.warning("Failed shutting down health server: %s", exc)
        if self._thread:
            self._thread.join(timeout=3)
        self._thread = None
        self._server = None

    @staticmethod
    def _build_handler(status_provider: Callable[[], Dict[str, Any]]):
        provider = status_provider

        class HealthHandler(BaseHTTPRequestHandler):
            def do_GET(self):  # type: ignore[override]
                if self.path not in ("/", "/health", "/healthz"):
                    self.send_response(404)
                    self.end_headers()
                    return

                payload = provider() or {}
                ok = bool(payload.get("ok", True))
                body = json.dumps(payload).encode("utf-8")

                self.send_response(200 if ok else 503)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: Any) -> None:  # pragma: no cover - suppress noisy logs
                return

        return HealthHandler


__all__ = ["HealthServer"]
