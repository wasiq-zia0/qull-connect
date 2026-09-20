"""Shared HTTP contract metadata and the non-sensitive payment return page."""

import os
from urllib.parse import urlsplit

from fastapi.openapi.utils import get_openapi
from fastapi.responses import HTMLResponse


def install_api_contract(app, slug: str, title: str):
    """Document the actual per-user bearer contract without changing routes."""
    from payment_support import install_payment_routes

    install_payment_routes(app, slug)
    base = os.environ.get("PUBLIC_BASE_URL", f"https://5.78.152.6.nip.io/{slug}").rstrip("/")
    if urlsplit(base).scheme not in ("https", "http"):
        raise ValueError("PUBLIC_BASE_URL must be an absolute HTTP(S) URL")

    @app.get("/billing/return", include_in_schema=False, response_class=HTMLResponse)
    def billing_return():
        return HTMLResponse(
            "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<meta name='robots' content='noindex,nofollow'>"
            "<title>Return to Qull Connect</title></head>"
            "<body><main><h1>Return to your connector</h1>"
            "<p>Return to the conversation where you started payment setup. "
            "The connector will check Stripe to confirm whether your payment method was saved.</p>"
            "<p>Saving a payment method does not pay a service fee. "
            "Review the amount and authorize any fee in your connector.</p>"
            "<p><a href='https://qull.io/connect/'>Qull Connect</a></p>"
            "</main></body></html>",
            headers={
                "Cache-Control": "no-store",
                "Content-Security-Policy": "default-src 'none'; base-uri 'none'; frame-ancestors 'none'",
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
            },
        )

    def openapi():
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=title,
            version="1.0.0",
            description=(
                "Qull Connect API. Supply a per-user API key in Authorization: Bearer <key>. "
                "Keys identify one customer; arbitrary platform/user-id headers are not accepted. "
                "Payment setup opens hosted Stripe Checkout and does not charge a service fee. "
                "Use the record's billing/status operation to verify setup, then explicitly "
                "authorize the applicable fee. See the linked guide for workflow and service limits."
            ),
            routes=app.routes,
            servers=[{"url": base}],
        )
        schema["info"]["contact"] = {"name": "Qull, Inc.", "email": "wasiq@qull.io"}
        schema["components"].setdefault("securitySchemes", {})["UserApiKey"] = {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "Qull per-user API key",
            "description": "An operator-provisioned opaque API key belonging to one customer. Never use a Stripe secret key here.",
        }
        schema["security"] = [{"UserApiKey": []}]
        for path, item in schema["paths"].items():
            for method, operation in item.items():
                if method not in {"get", "post", "put", "patch", "delete", "options", "head"}:
                    continue
                if path in {"/health", "/ready"}:
                    operation["security"] = []
                    continue
                responses = operation.setdefault("responses", {})
                for status, description in {
                    "401": "Missing, invalid, expired or disabled per-user API key.",
                    "413": "Request body exceeds the configured limit.",
                    "429": "Request rate limit exceeded. Wait for the Retry-After interval.",
                }.items():
                    responses.setdefault(status, {"description": description})
        app.openapi_schema = schema
        return schema

    app.openapi = openapi
