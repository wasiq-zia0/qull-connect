"""Platform identity resolution and tenant isolation.

Every REST endpoint and MCP tool in this connector resolves the calling user
through this module. There is exactly one choke point: ``require_owner()``.

Identity sources (in priority order):
  1. ``PLATFORM_USER_HEADER`` (default ``X-Platform-User-Id``) — injected by
     the Muse platform for the authenticated user. Trusted only when the
     request arrives through the platform; the edge (nginx) MUST NOT forward
     a client-supplied value (see deploy.sh template: the header is forwarded
     only from ``PLATFORM_TRUSTED_CIDRS``).
  2. ``X-Dev-User-Id`` — honored ONLY when ``ALLOW_DEV_IDENTITY=1`` (local
     development and verification). Never enable in production.

No resolvable identity -> 401 with a plain-language message.

Service-to-service calls (``POST /api/life-events`` and other webhook-style
endpoints) authenticate with ``SERVICE_API_KEY`` as a Bearer token instead;
they carry no user identity.
"""
from __future__ import annotations

import os
from contextvars import ContextVar
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

_current_owner: ContextVar[Optional[str]] = ContextVar("qull_owner_id", default=None)

_UNAUTH_MSG = (
    "I couldn't tell who this request is for. Sign in and try again — "
    "if you're the developer, set ALLOW_DEV_IDENTITY=1 and send X-Dev-User-Id."
)


def platform_header() -> str:
    return os.environ.get("PLATFORM_USER_HEADER", "X-Platform-User-Id")


def dev_identity_allowed() -> bool:
    return os.environ.get("ALLOW_DEV_IDENTITY") == "1"


def resolve_owner(headers) -> Optional[str]:
    """Resolve the owner id from request headers. None when unresolvable."""
    name = platform_header().lower()
    val = headers.get(name)
    if val and val.strip():
        return val.strip()[:128]
    if dev_identity_allowed():
        dev = headers.get("x-dev-user-id")
        if dev and dev.strip():
            return "dev:" + dev.strip()[:120]
    return None


def current_owner() -> Optional[str]:
    """Owner id for the current request (set by IdentityMiddleware)."""
    return _current_owner.get()


class IdentityError(Exception):
    """Raised inside MCP tools when no identity could be resolved."""


def require_owner() -> str:
    """Return the current request's owner id or raise IdentityError."""
    owner = _current_owner.get()
    if not owner:
        raise IdentityError(_UNAUTH_MSG)
    return owner


class IdentityMiddleware(BaseHTTPMiddleware):
    """Enforces identity on every request.

    - ``/health`` stays public (orchestrator probes).
    - ``service_paths`` (default ``/api/life-events``) require
      ``Authorization: Bearer $SERVICE_API_KEY`` instead of user identity.
    - Everything else requires a resolvable user identity (401 otherwise).
    """

    def __init__(self, app, service_paths=("/api/life-events",)):
        super().__init__(app)
        self.service_paths = tuple(service_paths)

    def _is_service_path(self, path: str) -> bool:
        return any(
            path == sp or path.startswith(sp.rstrip("/") + "/")
            for sp in self.service_paths
        )

    async def dispatch(self, request, call_next):
        path = request.url.path
        if path == "/health" or path.startswith("/health/"):
            return await call_next(request)
        if self._is_service_path(path):
            expected = os.environ.get("SERVICE_API_KEY", "")
            got = request.headers.get("authorization", "")
            if not expected or got != f"Bearer {expected}":
                return JSONResponse(
                    status_code=401,
                    content={
                        "detail": "Service authentication required.",
                        "user_message": "This endpoint needs a service key.",
                    },
                )
            _current_owner.set(None)
            try:
                return await call_next(request)
            finally:
                _current_owner.set(None)
        owner = resolve_owner(request.headers)
        if not owner:
            return JSONResponse(
                status_code=401,
                content={"detail": "Authentication required.",
                         "user_message": _UNAUTH_MSG},
            )
        _current_owner.set(owner)
        try:
            return await call_next(request)
        finally:
            _current_owner.set(None)
