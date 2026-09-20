"""Start REST (port 8480) + MCP (port 8580) for medical-bill-fighter."""
import os
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_PY = ROOT / ".venv" / "bin" / "python"


def _run_rest() -> None:
    subprocess.run([str(VENV_PY), "-m", "uvicorn", "app:app",
                    "--host", "127.0.0.1", "--port", "8480"], cwd=str(ROOT))


def _run_mcp() -> None:
    env = {**os.environ, "MCP_PORT": "8580"}
    subprocess.run([str(VENV_PY), "mcp_server.py"], cwd=str(ROOT), env=env)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--rest-only":
        _run_rest()
    elif len(sys.argv) > 1 and sys.argv[1] == "--mcp-only":
        _run_mcp()
    else:
        threading.Thread(target=_run_rest, daemon=True).start()
        _run_mcp()
