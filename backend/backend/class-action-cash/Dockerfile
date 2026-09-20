# syntax=docker/dockerfile:1
# class-action-cash — Qull Muse connector (REST + MCP streamable-HTTP)
#
# Build (needs BuildKit — default on modern Docker, Railway, Fly):
#   docker build -t qull/class-action-cash .
# Run:
#   docker run -p 8476:8476 -p 8576:8576 qull/class-action-cash
#
# Ports are overridable at runtime: -e REST_PORT=8476 -e MCP_PORT=8576
# NOTE: run.py in this connector binds 127.0.0.1 (only deposit-recovery
# and final-paycheck honor env overrides there), so this image does NOT
# use run.py. start.py (generated below) launches uvicorn and the MCP
# server directly, both on 0.0.0.0 — no connector source was modified.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    REST_PORT=8476 \
    MCP_PORT=8576

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app code only: skip the local dev venv, caches, sqlite data.
COPY --exclude=.venv --exclude=__pycache__ --exclude=data --exclude=*.db . .

EXPOSE 8476 8576

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('REST_PORT','8476')+'/health',timeout=4)"

# Container entrypoint: REST (uvicorn) + MCP (streamable-http) on 0.0.0.0.
# Exits if either child dies so the orchestrator restarts the container.
RUN cat > /app/start.py <<'STARTEOF'
import os
import signal
import subprocess
import sys
import time

REST_PORT = os.environ.get("REST_PORT", "8476")
MCP_PORT = os.environ.get("MCP_PORT", "8576")


def main() -> int:
    os.makedirs("data", exist_ok=True)
    env = dict(os.environ)
    children = [
        subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app:app",
             "--host", "0.0.0.0", "--port", REST_PORT],
            env=env,
        ),
        subprocess.Popen(
            [sys.executable, "-c",
             "import os, uvicorn, mcp_server; "
             "uvicorn.run(mcp_server.create_mcp_app(), host='0.0.0.0', "
             "port=int(os.environ['MCP_PORT']), log_level='warning')"],
            env=env,
        ),
    ]

    def _stop(*_):
        for p in children:
            try:
                p.terminate()
            except Exception:
                pass

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    while True:
        for p in children:
            rc = p.poll()
            if rc is not None:
                print(f"child exited (rc={rc}); shutting down", flush=True)
                _stop()
                return rc
        time.sleep(1)


if __name__ == "__main__":
    sys.exit(main())
STARTEOF

CMD ["python", "/app/start.py"]
