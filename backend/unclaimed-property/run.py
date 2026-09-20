"""Start the unclaimed-property connector: REST (port 8477) + MCP (port 8577).

Usage:  .venv/bin/python run.py
"""
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).parent
PY = BASE / ".venv" / "bin" / "python"

procs = [
    subprocess.Popen([str(PY), "-m", "uvicorn", "app:app",
                      "--host", "127.0.0.1", "--port", "8477"], cwd=BASE),
    subprocess.Popen([str(PY), "-c",
                      "import uvicorn, mcp_server; "
                      "uvicorn.run(mcp_server.create_mcp_app(), host='127.0.0.1',"
                      " port=8577, log_level='warning')"], cwd=BASE),
]
print(f"REST  http://127.0.0.1:8477  (pid {procs[0].pid})")
print(f"MCP   http://127.0.0.1:8577/mcp  (pid {procs[1].pid})")
try:
    for p in procs:
        p.wait()
except KeyboardInterrupt:
    for p in procs:
        p.terminate()
    sys.exit(0)
