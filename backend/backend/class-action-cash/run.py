"""Start Class Action Cash: REST on 8476 + MCP (streamable-http) on 8576."""
import signal
import subprocess
import sys
import time
from pathlib import Path

DIR = Path(__file__).resolve().parent
PY = DIR / ".venv" / "bin" / "python"


def main() -> None:
    env = dict(__import__("os").environ)
    rest = subprocess.Popen(
        [str(PY), "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", "8476"],
        cwd=str(DIR), env=env)
    mcp = subprocess.Popen(
        [str(PY), "-c",
         "import os, uvicorn, mcp_server; "
         "uvicorn.run(mcp_server.create_mcp_app(), host='127.0.0.1', "
         "port=int(os.environ.get('MCP_PORT', '8576')), log_level='warning')"],
        cwd=str(DIR), env=env)

    def stop(*_):
        for p in (rest, mcp):
            try:
                p.terminate()
            except Exception:
                pass
        sys.exit(0)

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    print("class-action-cash: REST http://127.0.0.1:8476  MCP http://127.0.0.1:8576/mcp",
          flush=True)
    try:
        while True:
            time.sleep(1)
            for name, p in (("rest", rest), ("mcp", mcp)):
                if p.poll() is not None:
                    print(f"{name} exited with code {p.returncode}", flush=True)
                    stop()
    except KeyboardInterrupt:
        stop()


if __name__ == "__main__":
    main()
