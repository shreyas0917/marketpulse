"""Quote API (FastAPI).

Degraded mode: Redis is the source of truth; when a Redis call fails or times
out, a quote no older than STALE_QUOTE_MAX_AGE_SECONDS may be served from the
per-process cache with `stale: true`, otherwise the API answers
503 QUOTE_UNAVAILABLE. Readiness deliberately ignores Redis.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, MutableMapping
from contextlib import asynccontextmanager
from typing import Any

import redis.asyncio as aioredis
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, generate_latest
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.cache import LocalQuoteCache
from app.api.metrics import ApiMetrics
from app.common.config import Settings
from app.common.faults import API_FAULTS, Faults, register_faults
from app.common.logging import request_id_var
from app.common.metrics import new_registry
from app.common.models import MarketEvent, Quote
from app.common.queue import QueueBackend, make_queue
from app.common.store import QuoteReader

log = logging.getLogger(__name__)

QUOTE_ROUTE = "/v1/quotes/{symbol}"
WARMUP_HEADER = "x-traffic-class"

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


def error(
    status: int, code: str, detail: str, headers: dict[str, str] | None = None
) -> JSONResponse:
    return JSONResponse({"error": code, "detail": detail}, status_code=status, headers=headers)


class MetricsMiddleware:
    """Records http_requests_total and http_request_duration_seconds per route template.

    Requests tagged `X-Traffic-Class: warmup` are recorded under route="warmup" so
    they stay visible but never match the SLI selector.
    """

    def __init__(self, app: ASGIApp, metrics: ApiMetrics) -> None:
        self.app = app
        self.metrics = metrics

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        start = time.perf_counter()
        status = 500
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        request_id = headers.get("x-request-id") or uuid.uuid4().hex
        token = request_id_var.set(request_id)

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message.setdefault("headers", []).append((b"x-request-id", request_id.encode()))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            route = scope.get("route")
            template = getattr(route, "path", None) or "unmatched"
            if headers.get(WARMUP_HEADER) == "warmup":
                template = "warmup"
            self.metrics.requests.labels(
                route=template, method=scope["method"], status=str(status)
            ).inc()
            self.metrics.duration.labels(route=template).observe(time.perf_counter() - start)
            request_id_var.reset(token)


def _redis_client(settings: Settings) -> aioredis.Redis:
    return aioredis.Redis.from_url(
        settings.redis_url,
        socket_timeout=settings.redis_timeout_seconds,
        socket_connect_timeout=settings.redis_timeout_seconds,
        retry=Retry(NoBackoff(), 0),
    )


def create_app(
    settings: Settings | None = None,
    reader: QuoteReader | None = None,
    queue: QueueBackend | None = None,
    registry: CollectorRegistry | None = None,
) -> FastAPI:
    settings = settings or Settings()
    registry = registry or new_registry()
    metrics = ApiMetrics(registry)
    faults = Faults.from_settings(settings)
    register_faults(faults, registry, API_FAULTS)
    symbols = set(settings.symbol_list)
    cache = LocalQuoteCache(len(symbols), settings.stale_quote_max_age_seconds)
    timeout = settings.redis_timeout_seconds
    state: dict[str, Any] = {"reader": reader, "queue": queue}

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if state["reader"] is None:
            state["reader"] = QuoteReader(_redis_client(settings))
        if state["queue"] is None:
            state["queue"] = make_queue(settings)
        log.info("api_started", extra={"symbols": len(symbols)})
        yield
        await state["reader"].close()

    app = FastAPI(title="MarketPulse Quote API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(MetricsMiddleware, metrics=metrics)
    app.state.cache = cache
    app.state.metrics = metrics
    app.state.registry = registry

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = "NOT_FOUND" if exc.status_code == 404 else "HTTP_ERROR"
        return error(exc.status_code, code, str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return error(422, "VALIDATION_ERROR", str(exc.errors()))

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.error("unhandled_exception", exc_info=exc)
        return error(500, "INTERNAL", "internal server error")

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    async def ready() -> Response:
        # Deliberately does not touch Redis: see spec section 5, "Why readiness ignores Redis".
        if faults.readiness_fail:
            return error(503, "NOT_READY", "readiness failure injected")
        return JSONResponse({"status": "ready"})

    @app.get("/metrics")
    async def metrics_endpoint() -> Response:
        return Response(generate_latest(registry), media_type=CONTENT_TYPE_LATEST)

    @app.get(QUOTE_ROUTE)
    async def get_quote(symbol: str, request: Request) -> Response:
        if faults.api_extra_latency_ms:
            await asyncio.sleep(faults.api_extra_latency_ms / 1000.0)
        if faults.api_error_rate and random.random() < faults.api_error_rate:
            return error(500, "FAULT_INJECTED", "injected server error")

        symbol = symbol.upper()
        if symbol not in symbols:
            return error(404, "UNKNOWN_SYMBOL", f"symbol {symbol} is not served")

        warmup = request.headers.get(WARMUP_HEADER) == "warmup"
        quote: Quote | None
        try:
            quote = await asyncio.wait_for(state["reader"].get(symbol), timeout)
            metrics.store_up.set(1)
        except (TimeoutError, aioredis.RedisError, OSError) as exc:
            metrics.store_up.set(0)
            now = time.time()
            cached = cache.get_fresh(symbol, now)
            if cached is None:
                metrics.responses.labels(source="unavailable").inc()
                log.warning(
                    "quote_unavailable", extra={"symbol": symbol, "reason": type(exc).__name__}
                )
                return error(
                    503,
                    "QUOTE_UNAVAILABLE",
                    "quote store unavailable and no fresh cached quote",
                    headers={"Retry-After": str(settings.retry_after_seconds)},
                )
            metrics.responses.labels(source="local_cache").inc()
            return _quote_response(cached, now, stale=True, source="local_cache", warmup=warmup)

        if quote is None:
            return error(404, "NO_QUOTE", f"no quote received yet for {symbol}")
        cache.put(quote)
        metrics.responses.labels(source="store").inc()
        return _quote_response(quote, time.time(), stale=False, source="store", warmup=warmup)

    def _quote_response(
        quote: Quote, now: float, *, stale: bool, source: str, warmup: bool
    ) -> JSONResponse:
        age = max(0.0, now - quote.event_ts)
        if not warmup:
            metrics.quote_age.observe(age)
        return JSONResponse(
            {
                "symbol": quote.symbol,
                "price": quote.price,
                "event_ts": quote.event_ts,
                "event_id": quote.event_id,
                "age_seconds": round(age, 6),
                "stale": stale,
                "source": source,
            }
        )

    @app.post("/v1/events", status_code=202)
    async def post_event(event: MarketEvent) -> Response:
        if event.symbol not in symbols:
            return error(400, "UNKNOWN_SYMBOL", f"symbol {event.symbol} is not served")
        try:
            await run_in_threadpool(state["queue"].publish, [event])
        except Exception as exc:
            log.warning("event_publish_failed", extra={"event_id": event.event_id}, exc_info=exc)
            return error(503, "QUEUE_UNAVAILABLE", "could not enqueue event")
        log.debug("event_accepted", extra={"event_id": event.event_id})
        return JSONResponse({"accepted": True, "event_id": event.event_id}, status_code=202)

    return app
