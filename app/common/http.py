"""Minimal HTTP server for the worker and producer: /metrics, health probes, admin hooks."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, generate_latest

PostHandler = Callable[[dict[str, str]], dict[str, Any]]


class ServiceHttpServer:
    def __init__(
        self,
        port: int,
        registry: CollectorRegistry,
        is_live: Callable[[], bool],
        post_handlers: dict[str, PostHandler] | None = None,
        host: str = "0.0.0.0",  # noqa: S104 - must listen on the pod IP
    ) -> None:
        handlers = post_handlers or {}

        class Handler(BaseHTTPRequestHandler):
            def _send(self, status: int, body: bytes, ctype: str = "application/json") -> None:
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                path = urlparse(self.path).path
                if path == "/metrics":
                    self._send(200, generate_latest(registry), CONTENT_TYPE_LATEST)
                elif path == "/health/ready":
                    self._send(200, b'{"status":"ready"}')
                elif path == "/health/live":
                    ok = is_live()
                    body = b'{"status":"ok"}' if ok else b'{"error":"NOT_LIVE","detail":"stalled"}'
                    self._send(200 if ok else 503, body)
                else:
                    self._send(404, b'{"error":"NOT_FOUND","detail":"not found"}')

            def do_POST(self) -> None:
                parsed = urlparse(self.path)
                handler = handlers.get(parsed.path)
                if handler is None:
                    self._send(404, b'{"error":"NOT_FOUND","detail":"not found"}')
                    return
                params = {k: v[-1] for k, v in parse_qs(parsed.query).items()}
                try:
                    result = handler(params)
                except ValueError as exc:
                    body = {"error": "BAD_REQUEST", "detail": str(exc)}
                    self._send(400, json.dumps(body).encode())
                    return
                self._send(200, json.dumps(result).encode())

            def log_message(self, format: str, *args: Any) -> None:
                return None

        self._server = ThreadingHTTPServer((host, port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
