"""MCP stdio 서버 껍데기 (mcp 2.x의 MCPServer, 옛 이름 FastMCP). 아직 도구가 없다.

stdout은 MCP 메시지 전용이다. 진단은 stderr와 runtime/server.log로 보낸다.
"""

from __future__ import annotations

import sys

from mcp.server.mcpserver import MCPServer

from .logging_setup import get_logger, setup_logging
from .paths import MemoryHomeError, MemoryPaths, get_paths

SERVER_NAME = "memory"


def create_server(paths: MemoryPaths | None = None) -> MCPServer:
    """도구가 등록되지 않은 서버를 만든다. 로그 파일은 paths가 있을 때만 켠다."""
    setup_logging(paths.server_log if paths is not None else None)
    server = MCPServer(SERVER_NAME, instructions="개인 기억 시스템. 아직 도구가 등록되지 않았다.")
    get_logger().info("server created tools=%d", 0)
    return server


def run() -> None:
    try:
        paths = get_paths()
    except MemoryHomeError as e:
        print(f"오류: {e}", file=sys.stderr)
        sys.exit(2)
    server = create_server(paths)
    server.run(transport="stdio")


if __name__ == "__main__":  # pragma: no cover
    run()
