"""이벤트 로그 (SPEC 3.4).

월별 jsonl 파일에 한 줄씩 덧붙인다. 기존 줄은 고치지 않는다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from .paths import MemoryPaths
from .timeutil import now_local

EVENT_TYPES = frozenset(
    {
        "created",
        "amended",
        "used",
        "archived",
        "unarchived",
        "core_updated",
        "external_edit",
        "restored",
        "recovered",
    }
)


class EventError(ValueError):
    pass


@dataclass(frozen=True)
class Event:
    ts: str
    type: str
    ids: list[str]
    data: dict[str, Any]

    def to_json(self) -> str:
        return json.dumps(
            {"ts": self.ts, "type": self.type, "ids": self.ids, "data": self.data},
            ensure_ascii=False,
            separators=(",", ":"),
        )


def append_event(
    paths: MemoryPaths,
    type: str,
    ids: list[str],
    data: dict[str, Any] | None = None,
    ts: datetime | None = None,
) -> Path:
    """이벤트 한 줄을 그 달의 파일에 덧붙이고 flush한다. 쓴 파일 경로를 돌려준다.

    호출자는 쓰기 잠금 안에서 불러야 한다.
    """
    if type not in EVENT_TYPES:
        raise EventError(f"허용되지 않은 이벤트 type: {type}")
    if not isinstance(ids, list) or not all(isinstance(i, str) and i for i in ids):
        raise EventError("ids 는 비어 있지 않은 문자열 목록이어야 한다.")
    when = ts if ts is not None else now_local()
    if when.tzinfo is None:
        raise EventError("ts 는 시간대가 있어야 한다.")
    event = Event(ts=when.isoformat(timespec="seconds"), type=type, ids=list(ids), data=dict(data or {}))
    line = event.to_json()
    if "\n" in line:  # json.dumps는 개행을 이스케이프하므로 방어용
        raise EventError("이벤트 한 줄에 개행이 들어갈 수 없다.")
    path = paths.events_file(when.year, when.month)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")
        f.flush()
    return path


def iter_events(path: Path) -> Iterator[Event]:
    """한 파일의 온전한 이벤트를 순서대로 돌려준다. 잘린 마지막 줄은 doctor가 다룬다."""
    if not path.exists():
        return
    with open(path, encoding="utf-8") as f:
        for raw in f:
            if not raw.endswith("\n"):
                # 잘린 마지막 줄. 여기서는 건너뛴다 (SPEC 5 doctor 표).
                return
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                # 중간의 깨진 줄은 건너뛴다. doctor 가 경고한다.
                continue
            if not isinstance(obj, dict) or "type" not in obj or "ts" not in obj:
                continue
            yield Event(ts=obj["ts"], type=obj["type"], ids=list(obj.get("ids", [])), data=dict(obj.get("data", {})))
