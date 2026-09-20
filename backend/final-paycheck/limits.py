"""Bounded per-client rate limits and actual request-body size enforcement.

Use one ASGI worker per connector. These limits are in-memory; nginx supplies
an additional edge limit. Each socket client has a budget; claimed bearer tokens never increase it.
Proxy headers must be accepted only from the local trusted nginx proxy.
"""
from __future__ import annotations

import time
import asyncio
from collections import OrderedDict
from collections import deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

_MONEY_HINTS = ("/billing/", "-confirmed", "/pay", "/demand-letter", "/claim-pack", "/pack", "/letter")


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, per_minute: int = 120, money_per_minute: int = 20):
        super().__init__(app)
        self.per_minute = per_minute
        self.money_per_minute = money_per_minute
        self._buckets = OrderedDict()
        self._maximum_clients = 20_000

    async def dispatch(self, request, call_next):
        if request.url.path in {"/health", "/ready"}:
            return await call_next(request)
        identity = request.client.host if request.client else "unknown"
        kind = "money" if any(h in request.url.path for h in _MONEY_HINTS) else "standard"
        budget = self.money_per_minute if kind == "money" else self.per_minute
        now = time.monotonic()
        key = identity, kind
        # Never clear active limits under memory pressure: expire idle clients,
        # then reject unseen identities until capacity is available.
        while self._buckets:
            first, entries = next(iter(self._buckets.items()))
            if entries and now - entries[-1] < 60:
                break
            self._buckets.pop(first)
        hits = self._buckets.get(key)
        if hits is None:
            if len(self._buckets) >= self._maximum_clients:
                return self._limited(60)
            hits = deque()
            self._buckets[key] = hits
        while hits and now - hits[0] >= 60:
            hits.popleft()
        self._buckets.move_to_end(key)
        if len(hits) >= budget:
            return self._limited(max(1, int(60 - (now - hits[0])) + 1))
        hits.append(now)
        return await call_next(request)

    @staticmethod
    def _limited(retry_after):
        return JSONResponse(status_code=429,
                            content={"detail": "Too many requests.", "user_message": "Wait before trying again."},
                            headers={"Retry-After": str(retry_after), "Cache-Control": "no-store"})


class BodySizeLimitMiddleware:
    """Buffer at most max_bytes before calling code that could mutate data.

    Content-Length alone cannot protect chunked or deliberately understated
    bodies. Checking each received chunk also enforces the actual size.
    """

    def __init__(self, app, max_bytes: int = 1_000_000):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        lengths = [value for name, value in scope.get("headers", []) if name.lower() == b"content-length"]
        if len(lengths) > 1 or (lengths and (len(lengths[0]) > 20 or not lengths[0].isdigit())):
            return await self._error(400, "Invalid Content-Length.", scope, receive, send)
        if lengths and int(lengths[0]) > self.max_bytes:
            return await self._error(413, "Request body too large.", scope, receive, send)
        body = bytearray()
        deadline = time.monotonic() + 30
        while True:
            try:
                message = await asyncio.wait_for(receive(), timeout=max(0, deadline - time.monotonic()))
            except asyncio.TimeoutError:
                return await self._error(408, "Request body timed out.", scope, receive, send)
            if message["type"] == "http.disconnect":
                return
            if message["type"] != "http.request":
                continue
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > self.max_bytes:
                return await self._error(413, "Request body too large.", scope, receive, send)
            body.extend(chunk)
            if not message.get("more_body", False):
                break
        delivered = False
        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()
        await self.app(scope, replay, send)

    @staticmethod
    async def _error(code, text, scope, receive, send):
        response = JSONResponse(status_code=code, content={"detail": text, "user_message": text},
                                headers={"Cache-Control": "no-store"})
        await response(scope, receive, send)
