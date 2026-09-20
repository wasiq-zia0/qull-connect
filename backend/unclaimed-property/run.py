#!/usr/bin/env python3
"""Supervise both REST and MCP; stop both if either exits."""
import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.environ.get("HOST", os.environ.get("DEPOSIT_HOST", "127.0.0.1")))
    parser.add_argument("--rest-port", type=int, default=int(os.environ.get("REST_PORT", os.environ.get("DEPOSIT_REST_PORT", "8477"))))
    parser.add_argument("--mcp-port", type=int, default=int(os.environ.get("MCP_PORT", os.environ.get("DEPOSIT_MCP_PORT", "8577"))))
    args = parser.parse_args()
    children = []
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True
        for process in children:
            if process.poll() is None:
                process.terminate()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    env = dict(os.environ, QULL_RUN_HOST=args.host, QULL_RUN_MCP_PORT=str(args.mcp_port))
    try:
        children.append(subprocess.Popen([sys.executable, "-m", "uvicorn", "app:app", "--host", args.host,
                                           "--port", str(args.rest_port), "--forwarded-allow-ips", "127.0.0.1"], cwd=ROOT, env=env))
        children.append(subprocess.Popen([sys.executable, "-c", "import os,uvicorn,mcp_server; uvicorn.run(mcp_server.create_mcp_app(),host=os.environ['QULL_RUN_HOST'],port=int(os.environ['QULL_RUN_MCP_PORT']),forwarded_allow_ips='127.0.0.1')"], cwd=ROOT, env=env))
        while not stopping:
            for process in children:
                if process.poll() is not None:
                    # A clean child exit is still an unexpected partial service failure.
                    return process.returncode or 1
            time.sleep(0.2)
        return 0
    finally:
        stop()
        deadline = time.monotonic() + 10
        for process in children:
            try:
                process.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
