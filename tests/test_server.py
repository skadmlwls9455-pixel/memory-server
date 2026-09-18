import os
import sys

import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters
from mcp.client.stdio import stdio_client

from memory_server.server import SERVER_NAME, create_server


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_server_registers_stage1_tools(initialized):
    server = create_server(initialized)
    assert server.name == SERVER_NAME
    assert sorted(t.name for t in await server.list_tools()) == ["read_memory", "remember"]
    assert await server.list_resources() == []
    assert await server.list_prompts() == []


@pytest.mark.anyio
async def test_stdio_handshake_and_logging(initialized):
    """실제 stdio로 붙어서 initialize와 tools/list가 되고, stdout이 MCP 전용인지 본다."""
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "memory_server.cli", "serve"],
        env={**os.environ, "MEMORY_HOME": str(initialized.home)},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            info = await session.initialize()
            assert info.server_info.name == SERVER_NAME
            tools = await session.list_tools()
            assert sorted(t.name for t in tools.tools) == ["read_memory", "remember"]
    log = initialized.server_log.read_text(encoding="utf-8")
    assert "server created tools=2" in log


def test_server_without_memory_home_exits_2_with_empty_stdout():
    import subprocess

    env = {k: v for k, v in os.environ.items() if k != "MEMORY_HOME"}
    proc = subprocess.run(
        [sys.executable, "-m", "memory_server.cli", "serve"],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert proc.returncode == 2
    assert proc.stdout == ""
    assert "MEMORY_HOME" in proc.stderr


@pytest.mark.anyio
async def test_tools_via_stdio_roundtrip(initialized):
    """remember 와 read_memory 를 실제 MCP 왕복으로 부른다."""
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "memory_server.cli", "serve"],
        env={**os.environ, "MEMORY_HOME": str(initialized.home)},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = {t.name: t for t in (await session.list_tools()).tools}
            # 도구 설명문에 4장 작성 규칙 요약이 있다
            assert "fact_key" in tools["remember"].description and "비밀번호" in tools["remember"].description
            res = await session.call_tool("remember", {"title": "왕복", "body": "본문", "tags": ["주제/테스트"]})
            data = res.structured_content or {}
            assert data.get("saved") is True and data.get("indexed") is False
            note_id = data["id"]
            res2 = await session.call_tool("read_memory", {"id": note_id})
            assert (res2.structured_content or {}).get("title") == "왕복"
            res3 = await session.call_tool("read_memory", {"id": "20200101-000000-0000"})
            assert (res3.structured_content or {}).get("error", {}).get("code") == "NOT_FOUND"
            res4 = await session.call_tool("remember", {"title": "", "body": "b"})
            assert (res4.structured_content or {}).get("error", {}).get("code") == "INVALID_INPUT"
