"""Start the 401k-match connector: REST API (port 8479) + MCP server (port 8579)."""
import multiprocessing
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)


def run_rest():
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=8479, log_level="info")


def run_mcp():
    import uvicorn
    from mcp_server import create_mcp_app
    uvicorn.run(create_mcp_app(), host="127.0.0.1", port=8579, log_level="warning")


if __name__ == "__main__":
    rest = multiprocessing.Process(target=run_rest, name="rest-8479")
    mcp = multiprocessing.Process(target=run_mcp, name="mcp-8579")
    rest.start()
    mcp.start()
    try:
        rest.join()
        mcp.join()
    except KeyboardInterrupt:
        rest.terminate()
        mcp.terminate()
