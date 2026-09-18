"""runtime/backup/backup_state.json."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..paths import MemoryPaths


@dataclass
class Snapshot:
    name: str
    captured_at: str
    size: int
    sha256: str
    uploaded_at: str | None = None
    verified: bool = False


@dataclass
class RemoteUsage:
    bytes: int
    checked_at: str


@dataclass
class BackupState:
    last_verified: Snapshot | None = None
    pending: list[Snapshot] = field(default_factory=list)  # 업로드 실패로 runtime/backup/ 에 남은 암호문
    history: list[Snapshot] = field(default_factory=list)  # 최근 verified 기록 (최대 60)
    remote_usage: RemoteUsage | None = None
    last_restore_drill: str | None = None
    last_check_at: str | None = None
    last_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def state_path(paths: MemoryPaths) -> Path:
    return paths.backup / "backup_state.json"


def load_state(paths: MemoryPaths) -> BackupState | None:
    p = state_path(paths)
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    snap = lambda x: Snapshot(**x) if x else None  # noqa: E731
    return BackupState(
        last_verified=snap(d.get("last_verified")),
        pending=[Snapshot(**x) for x in d.get("pending", [])],
        history=[Snapshot(**x) for x in d.get("history", [])],
        remote_usage=RemoteUsage(**d["remote_usage"]) if d.get("remote_usage") else None,
        last_restore_drill=d.get("last_restore_drill"),
        last_check_at=d.get("last_check_at"),
        last_error=d.get("last_error"),
    )


def save_state(paths: MemoryPaths, state: BackupState) -> None:
    p = state_path(paths)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=p.parent)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(state.to_dict(), f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, p)
