"""Rate limiting + request size limits (in-process, no extra dependencies).

Two middlewares, both framework-agnostic Starlette:
- ``RateLimitMiddleware``: per-IP token bucket; a tighter budget for money
  and PDF endpoints (hints in ``_MONEY_HINTS``).
- ``BodySizeLimitMiddleware``: rejects absurd Content-Length before reading.
"""
from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

_MONEY_HINTS = (
    "/billing/", "-confirmed", "/pay", "/demand-letter",
    "/claim-pack", "/pack", "/letter",
)


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, per_minute: int = 120, money_per_minute: int = 20):
        super().__init__(app)
        self.per_minute = per_minute
        self.money_per_minute = money_per_minute
        self._buckets: dict[tuple[str, str], list[float]] = {}

    def _is_money(self, path: str) -> bool:
        return any(h in path for h in _MONEY_HINTS)

    async def dispatch(self, request, call_next):
        ip = request.client.host if request.client else "unknown"
        money = self._is_money(request.url.path)
        budget = self.money_per_minute if money else self.per_minute
        now = time.monotonic()
        key = (ip, "money" if money else "std")
        if len(self._buckets) > 20000:  # crude memory guard
            self._buckets.clear()
        hits = [t for t in self._buckets.get(key, []) if now - t < 60]
        if len(hits) >= budget:
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many requests — slow down and try again.",
                         "user_message": "Too many requests — slow down and try again."},
            )
        hits.append(now)
        self._buckets[key] = hits
        return await call_next(request)


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, max_bytes: int = 1_000_000):
        super().__init__(app)
        self.max_bytes = max_bytes

    async def dispatch(self, request, call_next):
        clen = request.headers.get("content-length")
        if clen and clen.isdigit() and int(clen) > self.max_bytes:
            return JSONResponse(
                status_code=413,
                content={"detail": "Request body too large.",
                         "user_message": "That request is too large."},
            )
        return await call_next(request)
