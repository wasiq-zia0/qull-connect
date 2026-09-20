#!/usr/bin/env python3
"""Start the deposit-recovery connector: REST (FastAPI) + MCP (streamable HTTP).

REST on 8471, MCP on 8571 by default. Override with flags or env:
    python run.py --rest-port 8471 --mcp-port 8571
    DEPOSIT_REST_PORT=8471 DEPOSIT_MCP_PORT=8571 DEPOSIT_HOST=127.0.0.1 python run.py
"""
import argparse
import os
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)

import uvicorn  # noqa: E402

from src import db  # noqa: E402
import mcp_server  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Run the deposit-recovery connector (REST + MCP)")
    ap.add_argument("--rest-port", type=int,
                    default=int(os.environ.get("DEPOSIT_REST_PORT", "8471")))
    ap.add_argument("--mcp-port", type=int,
                    default=int(os.environ.get("DEPOSIT_MCP_PORT", "8571")))
    ap.add_argument("--host", default=os.environ.get("DEPOSIT_HOST", "127.0.0.1"))
    args = ap.parse_args()

    db.init_db()

    def _run_mcp() -> None:
        # The MCP app is wrapped with IdentityMiddleware so every tool call
        # resolves the platform user; tools read it via require_owner().
        uvicorn.run(mcp_server.create_mcp_app(), host=args.host,
                    port=args.mcp_port, log_level="warning")

    mcp_thread = threading.Thread(
        target=_run_mcp,
        daemon=True,
        name="mcp-server",
    )
    mcp_thread.start()
    print(f"deposit-recovery up: REST http://{args.host}:{args.rest_port}  "
          f"MCP http://{args.host}:{args.mcp_port}/mcp", flush=True)
    uvicorn.run("app:app", host=args.host, port=args.rest_port, log_level="info")


if __name__ == "__main__":
    main()
