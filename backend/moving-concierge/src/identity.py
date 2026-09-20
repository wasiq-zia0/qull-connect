"""Per-user bearer authentication for REST and MCP.

The trusted owner is looked up in an operator-managed, hash-only registry.
Client-supplied platform/owner headers and the retired shared service key never
identify a user. This is Qull's API-key protocol, not an assumed Muse protocol.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

_current_owner: ContextVar[Optional[str]] = ContextVar("qull_owner_id", default=None)
_UNAUTH_MSG = "A valid user API key is required. Provide your Qull API key and try again."
_UNSAFE_FLAGS = ("ALLOW_DEV_IDENTITY", "FINAL_PAYCHECK_STRIPE_MOCK", "CLASS_ACTION_CASH_DRY_RUN")


def dev_identity_allowed() -> bool:
    return (os.environ.get("ENV") in {"development", "test"}
            and os.environ.get("ALLOW_DEV_IDENTITY") == "1")


def _production_config_safe() -> bool:
    if os.environ.get("ENV") != "production":
        return True
    return (not any(os.environ.get(name) not in (None, "", "0") for name in _UNSAFE_FLAGS)
            and not os.environ.get("FINAL_PAYCHECK_TODAY"))


def _registry() -> dict[str, str]:
    """Reload on each request so revocation does not wait for a restart."""
    filename = os.environ.get("QULL_API_KEYS_FILE")
    if not filename:
        return {}
    try:
        path = Path(filename)
        info = path.stat()
        # A registry is sensitive authorization configuration, even without raw keys.
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o027 or info.st_size > 2_000_000:
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != 1 or not isinstance(data.get("keys"), list):
            return {}
        result: dict[str, str] = {}
        seen = set()
        now = datetime.now(timezone.utc)
        for item in data["keys"]:
            digest, owner = item["sha256"], item["owner_id"]
            if (not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
                    or digest in seen or not isinstance(owner, str) or not owner.strip()
                    or owner != owner.strip() or len(owner) > 128
                    or any(ord(char) < 32 for char in owner)):
                return {}
            seen.add(digest)
            if not isinstance(item.get("disabled", False), bool):
                return {}
            if item.get("disabled", False):
                continue
            expires = item.get("expires_at")
            if expires:
                deadline = datetime.fromisoformat(expires.replace("Z", "+00:00"))
                if deadline.tzinfo is None:
                    return {}
                if deadline <= now:
                    continue
            result[digest] = owner
        return result
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return {}


def resolve_owner(headers) -> Optional[str]:
    """Return only an owner assigned to a verified secret by the operator."""
    if not _production_config_safe():
        return None
    if hasattr(headers, "getlist") and len(headers.getlist("authorization")) > 1:
        return None
    authorization = headers.get("authorization", "")
    if authorization:
        parts = authorization.split()
        if len(parts) != 2 or parts[0].lower() != "bearer" or not 32 <= len(parts[1]) <= 512:
            return None
        digest = hashlib.sha256(parts[1].encode("utf-8")).hexdigest()
        return _registry().get(digest)
    if dev_identity_allowed():
        value = headers.get("x-dev-user-id", "").strip()
        if value and len(value) <= 120 and not any(ord(char) < 32 for char in value):
            return "dev:" + value
    return None


def current_owner() -> Optional[str]:
    return _current_owner.get()


class IdentityError(Exception):
    """The request has no authenticated owner."""


def require_owner() -> str:
    owner = current_owner()
    if not owner:
        raise IdentityError(_UNAUTH_MSG)
    return owner


def readiness() -> tuple[bool, dict]:
    """Configuration probe; does not charge, call Stripe, or attest approval."""
    auth = bool(_registry()) and _production_config_safe()
    key = os.environ.get("STRIPE_SECRET_KEY", "")
    mode = os.environ.get("STRIPE_MODE", "test")
    prefixes = (f"sk_{mode}_", f"rk_{mode}_") + (("rkcs_test_",) if mode == "test" else ())
    payments = mode in {"test", "live"} and key.startswith(prefixes)
    data = Path(os.environ.get("DATA_DIR", ""))
    storage = data.is_absolute() and data.is_dir() and os.access(data, os.W_OK | os.X_OK)
    public_key = os.environ.get("STRIPE_PUBLISHABLE_KEY", "")
    payments = payments and public_key.startswith(f"pk_{mode}_")
    try:
        base = urlsplit(os.environ.get("PUBLIC_BASE_URL", ""))
        public_url = (base.scheme == "https" and bool(base.hostname) and not base.username
                      and not base.password and not base.query and not base.fragment)
    except ValueError:
        public_url = False
    ready = auth and payments and storage and public_url
    return ready, {"status": "ready" if ready else "not_ready", "authentication_configured": auth,
                   "payments_configured": payments, "durable_storage_configured": storage, "public_url_configured": bool(public_url),
                   "scope": "Configuration only; end-to-end service and payment verification required."}


class IdentityMiddleware(BaseHTTPMiddleware):
    """Authenticate each HTTP request, including MCP and life events."""

    def __init__(self, app, service_paths=()):
        super().__init__(app)
        if service_paths:
            raise ValueError("Shared service-key identity is unsupported; use a user-scoped bearer key.")

    async def dispatch(self, request, call_next):
        path = request.url.path
        if ((path == "/health" and request.method in {"GET", "HEAD"})
                or (path in {"/billing/return", "/billing/authenticate", "/billing/authenticate.js"} and request.method == "GET")
                or (path == "/billing/authenticate/session" and request.method == "POST")):
            return await call_next(request)
        if path == "/ready" and request.method in {"GET", "HEAD"}:
            ready, result = readiness()
            return JSONResponse(result, status_code=200 if ready else 503,
                                headers={"Cache-Control": "no-store"})
        owner = resolve_owner(request.headers)
        if not owner:
            return JSONResponse(status_code=401,
                                content={"detail": "Authentication required.", "user_message": _UNAUTH_MSG},
                                headers={"WWW-Authenticate": "Bearer", "Cache-Control": "no-store"})
        token = _current_owner.set(owner)
        try:
            response = await call_next(request)
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            return response
        finally:
            _current_owner.reset(token)
