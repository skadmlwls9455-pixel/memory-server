"""Claude Desktop 설정 병합과 launchd plist 렌더링. 순수 함수라 리눅스에서도 테스트한다."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DESKTOP_CONFIG_REL = "Library/Application Support/Claude/claude_desktop_config.json"
LAUNCHD_LABEL = "com.memory.backup-check"


def desktop_config_path(home: Path) -> Path:
    return home / DESKTOP_CONFIG_REL


def merged_desktop_config(existing: dict[str, Any] | None, uv: Path, server_dir: Path, memory_home: Path) -> dict[str, Any]:
    cfg = dict(existing or {})
    servers = dict(cfg.get("mcpServers") or {})
    servers["memory"] = {
        "command": str(uv),
        "args": ["run", "--directory", str(server_dir), "memory", "serve"],
        "env": {"MEMORY_HOME": str(memory_home)},
    }
    cfg["mcpServers"] = servers
    return cfg


def write_desktop_config(path: Path, uv: Path, server_dir: Path, memory_home: Path) -> Path | None:
    """기존 파일은 .bak 으로 남기고 병합해 쓴다. 백업 경로를 돌려준다."""
    existing = None
    backup = None
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8") or "{}")
        backup = path.with_suffix(".json.bak")
        backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(merged_desktop_config(existing, uv, server_dir, memory_home), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return backup


def render_plist(template: str, home: Path, memory_home: Path, server_dir: Path, uv: Path) -> str:
    return (
        template.replace("__HOME__", str(home))
        .replace("__MEMORY_HOME__", str(memory_home))
        .replace("__SERVER_DIR__", str(server_dir))
        .replace("__UV__", str(uv))
    )


def rclone_env(remote: str, key_id: str, key: str) -> dict[str, str]:
    name = remote.split(":", 1)[0].upper()
    return {
        f"RCLONE_CONFIG_{name}_TYPE": "b2",
        f"RCLONE_CONFIG_{name}_ACCOUNT": key_id,
        f"RCLONE_CONFIG_{name}_KEY": key,
    }
