"""Start REST (port 8474) + MCP (port 8574)."""
import multiprocessing
import uvicorn

REST_PORT = 8474
MCP_PORT = 8574


def run_rest():
    uvicorn.run("app:app", host="127.0.0.1", port=REST_PORT, log_level="info")


def run_mcp():
    import mcp_server
    # Identity-enforcing streamable-HTTP app (replaces the bare server.run).
    uvicorn.run(mcp_server.create_mcp_app(), host="127.0.0.1",
                port=MCP_PORT, log_level="warning")


if __name__ == "__main__":
    rest = multiprocessing.Process(target=run_rest)
    mcp = multiprocessing.Process(target=run_mcp)
    rest.start()
    mcp.start()
    rest.join()
    mcp.join()
