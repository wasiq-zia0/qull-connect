"""Boot the final-paycheck connector: REST on :8475, MCP on :8575."""
import multiprocessing
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

REST_PORT = int(os.environ.get("REST_PORT", "8475"))
MCP_PORT = int(os.environ.get("MCP_PORT", "8575"))


def _rest():
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=REST_PORT, log_level="warning")


def _mcp():
    import os
    import uvicorn
    from mcp_server import create_mcp_app
    uvicorn.run(create_mcp_app(), host="127.0.0.1",
                port=int(os.environ.get("MCP_PORT", "8575")), log_level="warning")


if __name__ == "__main__":
    rest = multiprocessing.Process(target=_rest, name="rest-8475")
    mcp = multiprocessing.Process(target=_mcp, name="mcp-8575")
    rest.start()
    mcp.start()
    try:
        rest.join()
        mcp.join()
    except KeyboardInterrupt:
        rest.terminate()
        mcp.terminate()
