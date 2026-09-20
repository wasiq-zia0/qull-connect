"""Start Moving Concierge: REST on :8478, MCP streamable-HTTP on :8578."""
import threading
import uvicorn

import mcp_server


def run_rest():
    uvicorn.run("app:app", host="0.0.0.0", port=8478, log_level="warning")


def _run_mcp():
    uvicorn.run(mcp_server.create_mcp_app(), host="127.0.0.1", port=8578,
                log_level="warning")


if __name__ == "__main__":
    t = threading.Thread(target=_run_mcp, daemon=True)
    t.start()
    print("moving-concierge: REST http://localhost:8478 | MCP streamable-http http://localhost:8578")
    run_rest()
