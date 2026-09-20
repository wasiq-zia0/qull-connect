"""Browser-only SCA pages; no untrusted identifiers or redirect targets accepted."""
from pathlib import Path

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from .service import BillingClient

_HEADERS = {
    "Cache-Control": "no-store, private",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'none'; script-src 'self' https://js.stripe.com; frame-src https://js.stripe.com https://hooks.stripe.com https://*.stripe.com; connect-src 'self' https://api.stripe.com https://*.stripe.com; style-src 'none'; img-src https://*.stripe.com; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
}
_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>Authorize your Qull payment</title>
<script defer src="https://js.stripe.com/dahlia/stripe.js"></script><script defer src="authenticate.js"></script>
</head><body><main><h1>Authorize your payment</h1>
<p id="status" role="status">Checking your secure authorization link…</p>
<p>After completing your bank's verification, return to your connector to check payment status.</p>
<p>Qull never collects your card number on this page.</p></main></body></html>"""
_SCRIPT = """'use strict';
(async () => {
  const output = document.getElementById('status');
  const token = window.location.hash.slice(1);
  window.history.replaceState(null, '', window.location.pathname);
  if (!/^[A-Za-z0-9_-]{43}$/.test(token)) {
    output.textContent = 'This link is invalid. Return to your connector and request a new authorization link.';
    return;
  }
  try {
    const response = await fetch('authenticate/session', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({token}), credentials: 'omit', cache: 'no-store', referrerPolicy: 'no-referrer'
    });
    const data = await response.json();
    if (!response.ok || data.error) throw new Error(data.error || 'Authorization is unavailable.');
    if (data.status === 'succeeded') {
      output.textContent = 'Payment is complete. Return to your connector to update its status.';
      return;
    }
    const provider = window.Stripe(data.publishable_key);
    output.textContent = 'Complete your bank verification below.';
    const result = data.action === 'confirm_payment'
      ? await provider.confirmPayment({clientSecret: data.client_secret, confirmParams: {return_url: data.return_url}, redirect: 'if_required'})
      : await provider.handleNextAction({clientSecret: data.client_secret});
    if (result.error) throw new Error('Bank verification was not completed. Return to your connector for a new link.');
    output.textContent = 'Verification submitted. Return to your connector to check the final payment status.';
  } catch (error) {
    output.textContent = error.message || 'Authorization is unavailable. Return to your connector for a new link.';
  }
})();"""


def install_payment_routes(app, slug: str, data_dir=None):
    """Public pages use a scoped single-use capability, not customer identity."""
    service = BillingClient(slug, Path(data_dir) if data_dir else Path.cwd() / "data")

    @app.get("/billing/authenticate", include_in_schema=False)
    def authentication_page():
        return HTMLResponse(_PAGE, headers=_HEADERS)

    @app.get("/billing/authenticate.js", include_in_schema=False)
    def authentication_script():
        return Response(_SCRIPT, media_type="application/javascript", headers=_HEADERS)

    @app.post("/billing/authenticate/session", include_in_schema=False)
    async def authentication_session(request: Request):
        if request.headers.get("content-type", "").split(";", 1)[0] != "application/json":
            return JSONResponse({"error": "Use an authorization link from your connector."}, status_code=400, headers=_HEADERS)
        try:
            raw = await request.body()
            if len(raw) > 256:
                raise ValueError("Oversized token request")
            import json
            body = json.loads(raw)
            if not isinstance(body, dict) or set(body) != {"token"}:
                raise ValueError("Invalid token request")
        except (ValueError, TypeError):
            return JSONResponse({"error": "Invalid authorization request."}, status_code=400, headers=_HEADERS)
        # Synchronous SDK calls run outside the event loop.
        from starlette.concurrency import run_in_threadpool
        result = await run_in_threadpool(service.exchange_auth_token, body["token"])
        return JSONResponse(result, status_code=400 if result.get("error") else 200, headers=_HEADERS)
