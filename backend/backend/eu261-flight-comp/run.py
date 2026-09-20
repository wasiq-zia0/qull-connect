"""Start both servers: REST (uvicorn, :8472) and MCP (streamable-http, :8572)."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_PY = ROOT / ".venv" / "bin" / "python"

REST_PORT = 8472
MCP_PORT = 8572


def main() -> None:
    rest = subprocess.Popen(
        [str(VENV_PY), "-m", "uvicorn", "app:app", "--host", "127.0.0.1",
         "--port", str(REST_PORT)],
        cwd=str(ROOT))
    # The MCP app is wrapped with IdentityMiddleware so every tool call
    # resolves the platform user; tools read it via require_owner().
    mcp = subprocess.Popen(
        [str(VENV_PY), "-c",
         "import uvicorn, mcp_server; "
         "uvicorn.run(mcp_server.create_mcp_app(), host='127.0.0.1', "
         f"port={MCP_PORT}, log_level='warning')"],
        cwd=str(ROOT))
    print(f"REST API : http://127.0.0.1:{REST_PORT}  (docs: /docs, health: /health)")
    print(f"MCP      : http://127.0.0.1:{MCP_PORT}/mcp  (streamable-http)")
    print("Press Ctrl+C to stop both.")
    try:
        rest.wait()
    except KeyboardInterrupt:
        pass
    finally:
        rest.terminate()
        mcp.terminate()


if __name__ == "__main__":
    main()
