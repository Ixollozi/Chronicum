from __future__ import annotations

import argparse
import sys

import uvicorn

from chronicum_mcp.config import api_token, api_url
from chronicum_mcp.http_auth import BearerAuthMiddleware
from chronicum_mcp.tools import create_mcp


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Chronicum MCP server")
    parser.add_argument("--http", action="store_true", help="Serve Streamable HTTP instead of stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--path", default="/mcp", help="HTTP MCP path (default /mcp)")
    args = parser.parse_args(argv)

    mcp = create_mcp()
    if not args.http:
        mcp.run(transport="stdio")
        return

    token = api_token()
    try:
        app = mcp.streamable_http_app(streamable_http_path=args.path)
    except TypeError:
        app = mcp.streamable_http_app()

    if hasattr(app, "add_middleware"):
        app.add_middleware(BearerAuthMiddleware, token=token)
        asgi = app
    else:
        asgi = BearerAuthMiddleware(app, token)

    print(f"Chronicum MCP HTTP http://{args.host}:{args.port}{args.path} → API {api_url()}", file=sys.stderr)
    uvicorn.run(asgi, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
