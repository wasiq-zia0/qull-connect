"""Start Subscription Slayer: REST API on :8473, MCP server on :8573."""
import subprocess
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
VENV_PY = BASE_DIR / ".venv" / "bin" / "python"


def main() -> None:
    rest = subprocess.Popen(
        [str(VENV_PY), "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", "8473"],
        cwd=str(BASE_DIR),
    )
    # The MCP app is wrapped with IdentityMiddleware so every tool call
    # resolves the platform user; tools read it via require_owner().
    mcp = subprocess.Popen(
        [str(VENV_PY), "-c",
         "import uvicorn, mcp_server; "
         "uvicorn.run(mcp_server.create_mcp_app(), host='127.0.0.1', "
         "port=8573, log_level='warning')"],
        cwd=str(BASE_DIR),
    )
    print(f"subscription-slayer: REST on :8473 (pid {rest.pid}), MCP on :8573 (pid {mcp.pid})",
          flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        rest.terminate()
        mcp.terminate()


if __name__ == "__main__":
    main()
