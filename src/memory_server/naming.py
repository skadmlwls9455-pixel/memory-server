"""ID와 파일명 규칙 (SPEC 3.2)."""

from __future__ import annotations

import re
import secrets
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Callable

from .paths import MemoryPaths
from .timeutil import now_local

ID_RE = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{4}$")
FORBIDDEN_TITLE_CHARS = set('/\\:*?"<>|#^[]')
TITLE_MAX_LEN = 60
UNTITLED = "무제"
MAX_ID_ATTEMPTS = 100


class IdError(RuntimeError):
    pass


def is_valid_id(value: str) -> bool:
    return bool(ID_RE.match(value))


def new_id(exists: Callable[[str], bool], now: datetime | None = None) -> str:
    """`YYYYMMDD-HHMMSS-xxxx`를 만든다. exists(id)가 참이면 다시 뽑는다."""
    when = now if now is not None else now_local()
    prefix = when.strftime("%Y%m%d-%H%M%S")
    for _ in range(MAX_ID_ATTEMPTS):
        candidate = f"{prefix}-{secrets.token_hex(2)}"
        if not exists(candidate):
            return candidate
    raise IdError("ID 생성을 반복해도 겹친다.")  # pragma: no cover


def safe_title(title: str) -> str:
    """파일명용 제목. 금지 문자 제거, NFC 정규화, 60자 제한. 비면 '무제'."""
    cleaned = "".join(ch for ch in title if ch not in FORBIDDEN_TITLE_CHARS)
    cleaned = unicodedata.normalize("NFC", cleaned)
    cleaned = " ".join(cleaned.split())  # 개행·탭·연속 공백을 공백 하나로
    cleaned = cleaned[:TITLE_MAX_LEN].rstrip(" .")
    return cleaned or UNTITLED


def note_filename(title: str, note_id: str) -> str:
    if not is_valid_id(note_id):
        raise IdError(f"잘못된 ID 형식: {note_id}")
    return f"{safe_title(title)} {note_id}.md"


def note_path(paths: MemoryPaths, title: str, note_id: str, created: datetime) -> Path:
    """memories/YYYY/MM/<제목> <id>.md. vault 밖으로 나가지 않는지 확인한다."""
    path = paths.memories_dir_for(created.year, created.month) / note_filename(title, note_id)
    paths.ensure_inside_vault(path.parent if not path.exists() else path)
    return path


def find_note_by_id(paths: MemoryPaths, note_id: str) -> Path | None:
    """`* <id>.md` 패턴으로 vault/memories에서 파일을 찾는다. 없으면 None."""
    if not is_valid_id(note_id):
        return None
    if not paths.memories.exists():
        return None
    matches = sorted(paths.memories.rglob(f"* {note_id}.md"))
    if len(matches) > 1:
        raise IdError(f"같은 ID의 파일이 여러 개다: {note_id}")
    return matches[0] if matches else None


def id_exists(paths: MemoryPaths) -> Callable[[str], bool]:
    return lambda note_id: find_note_by_id(paths, note_id) is not None
