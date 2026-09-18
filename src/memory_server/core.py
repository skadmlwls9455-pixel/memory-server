"""핵심 요약 core (SPEC 9).

core.json 이 원본이고 core.md 는 생성물이다. 근거는 active 인 fact 또는 episode 여야 한다.
get_core 는 호출될 때마다 근거 상태를 확인하고, 무효한 근거가 있는 항목의 문장은 돌려주지 않는다.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from . import gitutil
from .errors import MemoryError, invalid_input
from .events import append_event
from .lock import LockTimeout, write_lock
from .logging_setup import get_logger
from .naming import find_note_by_id, is_valid_id
from .notes import NoteFormatError, normalize_text, parse_note
from .paths import MemoryPaths
from .timeutil import now_local

SECTIONS = ("나에 대해", "진행 중인 일", "선호와 규칙", "최근 맥락")
MAX_ENTRIES = 12
MAX_RECENT = 3
MAX_CHARS = 1500
MAX_SOURCES = 3
CORE_TITLE = "핵심 요약"


@dataclass
class CoreEntry:
    section: str
    text: str
    sources: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {"section": self.section, "text": self.text, "sources": list(self.sources)}


@dataclass
class CoreDoc:
    entries: list[CoreEntry] = field(default_factory=list)
    updated: str | None = None
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"entries": [e.to_dict() for e in self.entries], "updated": self.updated, "reason": self.reason}


def total_chars(entries: list[CoreEntry]) -> int:
    """제목과 항목 텍스트 합계 (SPEC 9): 문서 제목 + 등장하는 절 제목 + 각 항목 텍스트."""
    sections = {e.section for e in entries}
    return len(CORE_TITLE) + sum(len(s) for s in sections) + sum(len(e.text) for e in entries)


def load_core(paths: MemoryPaths) -> CoreDoc:
    if not paths.core_json.exists():
        return CoreDoc()
    try:
        data = json.loads(paths.core_json.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise MemoryError("CORE_CORRUPT", f"core.json 을 읽을 수 없다: {e}", retryable=False) from e
    entries = [
        CoreEntry(section=str(e.get("section", "")), text=str(e.get("text", "")), sources=[str(s) for s in e.get("sources", [])])
        for e in data.get("entries", [])
    ]
    return CoreDoc(entries=entries, updated=data.get("updated"), reason=data.get("reason"))


def render_core_md(doc: CoreDoc) -> str:
    lines = [f"# {CORE_TITLE}", ""]
    if doc.updated:
        lines += [f"갱신: {doc.updated}", ""]
    for section in SECTIONS:
        items = [e for e in doc.entries if e.section == section]
        if not items:
            continue
        lines.append(f"## {section}")
        for e in items:
            refs = " ".join(f"[[{_stem_for(doc, s)}]]" if False else f"`{s}`" for s in e.sources)
            lines.append(f"- {e.text} ({refs})")
        lines.append("")
    if not doc.entries:
        lines += ["(비어 있음)", ""]
    return "\n".join(lines)


def _stem_for(_: CoreDoc, s: str) -> str:  # pragma: no cover - 위키링크 표기는 쓰지 않는다 (ID 만)
    return s


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
        os.replace(tmp, path)  # core.json/core.md 는 노트가 아니라 생성물이다. 이전 버전은 Git 이력에 남는다.
    except Exception:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


class CoreService:
    def __init__(self, paths: MemoryPaths, now: Callable[[], datetime] = now_local) -> None:
        self.paths = paths
        self.now = now
        self.log = get_logger()

    # ------------------------------------------------------------------ 근거 검사

    def source_state(self, note_id: str) -> tuple[str, str | None]:
        """(상태, 사유). 상태는 ok | missing | inactive | invalid_type | corrupt."""
        path = find_note_by_id(self.paths, note_id) if is_valid_id(note_id) else None
        if path is None:
            return "missing", f"근거 {note_id} 가 없다"
        try:
            fm = parse_note(path.read_text(encoding="utf-8")).frontmatter
        except (NoteFormatError, OSError) as e:
            return "corrupt", f"근거 {note_id} 의 형식이 깨졌다: {e}"
        if fm.type == "reflection":
            return "invalid_type", f"근거 {note_id} 는 reflection 이라 core 근거로 쓸 수 없다"
        if fm.status != "active":
            what = f"{fm.superseded_by} 로 대체됨" if fm.status == "superseded" else "보관됨"
            return "inactive", f"근거 {note_id} 가 {what}"
        return "ok", None

    # ------------------------------------------------------------------ 도구

    def get_core(self, extra_warnings: list[str] | None = None) -> dict[str, Any]:
        doc = load_core(self.paths)
        valid: list[dict[str, Any]] = []
        review: list[dict[str, Any]] = []
        for i, e in enumerate(doc.entries):
            reasons = [r for s in e.sources for st, r in [self.source_state(s)] if st != "ok"]
            if reasons:
                review.append({"index": i, "section": e.section, "sources": list(e.sources), "reasons": reasons})
            else:
                valid.append({"index": i, **e.to_dict()})
        self.log.info("tool=get_core valid=%d review=%d", len(valid), len(review))
        return {
            "entries": valid,
            "review": review,
            "updated": doc.updated,
            "warnings": list(extra_warnings or []),
        }

    def update_core(self, entries: list[dict[str, Any]], reason: str) -> dict[str, Any]:
        if not isinstance(reason, str) or not reason.strip():
            raise invalid_input("reason 이 필요하다.", field="reason")
        if not isinstance(entries, list):
            raise invalid_input("entries 는 목록이어야 한다.", field="entries")
        if len(entries) > MAX_ENTRIES:
            raise invalid_input(f"항목은 {MAX_ENTRIES}개 이하여야 한다. 넘치는 내용은 remember 로 보낸다.", field="entries", limit=MAX_ENTRIES)
        parsed: list[CoreEntry] = []
        for i, raw in enumerate(entries):
            if not isinstance(raw, dict):
                raise invalid_input(f"entries[{i}] 는 객체여야 한다.", field="entries")
            section = str(raw.get("section", "")).strip()
            text = " ".join(normalize_text(str(raw.get("text", ""))).split())
            sources = raw.get("sources")
            if section not in SECTIONS:
                raise invalid_input(f"entries[{i}].section 은 {', '.join(SECTIONS)} 중 하나여야 한다.", field="section", index=i)
            if not text:
                raise invalid_input(f"entries[{i}].text 가 비어 있다.", field="text", index=i)
            if not isinstance(sources, list) or not 1 <= len(sources) <= MAX_SOURCES or not all(isinstance(s, str) for s in sources):
                raise invalid_input(f"entries[{i}].sources 는 기억 ID 1~{MAX_SOURCES}개여야 한다.", field="sources", index=i)
            sources = [s.strip() for s in sources]
            for s in sources:
                state, why = self.source_state(s)
                if state != "ok":
                    raise MemoryError("INVALID_SOURCE", f"entries[{i}]: {why}. 근거는 active 인 fact 또는 episode 여야 한다.", retryable=False, index=i, source=s, state=state)
            parsed.append(CoreEntry(section=section, text=text, sources=sources))
        recent = [e for e in parsed if e.section == "최근 맥락"]
        if len(recent) > MAX_RECENT:
            raise invalid_input(f"'최근 맥락' 은 {MAX_RECENT}개 이하여야 한다.", field="entries", limit=MAX_RECENT)
        chars = total_chars(parsed)
        if chars > MAX_CHARS:
            raise invalid_input(f"제목과 항목 텍스트 합계가 {chars}자로 {MAX_CHARS}자를 넘는다. 넘치는 내용은 remember 로 보낸다.", field="entries", limit=MAX_CHARS, chars=chars)

        try:
            with write_lock(self.paths.lock_file):
                if not gitutil.is_repo(self.paths.vault):
                    from .doctor import NO_GIT_MESSAGE

                    raise MemoryError("VAULT_NOT_GIT", NO_GIT_MESSAGE, retryable=False)
                now = self.now()
                doc = CoreDoc(entries=parsed, updated=now.isoformat(timespec="seconds"), reason=reason.strip())
                _atomic_write(self.paths.core_json, json.dumps(doc.to_dict(), ensure_ascii=False, indent=2) + "\n")
                _atomic_write(self.paths.core_md, render_core_md(doc))
                events_path = append_event(
                    self.paths, "core_updated", sorted({s for e in parsed for s in e.sources}),
                    {"entries": len(parsed), "chars": chars, "reason": doc.reason}, ts=now,
                )
                warnings: list[str] = []
                committed = True
                try:
                    gitutil.commit_paths(self.paths.vault, [self.paths.core_json, self.paths.core_md, events_path], "core: update")
                except gitutil.GitError:
                    committed = False
                    warnings.append("GIT_COMMIT_FAILED: 커밋에 실패했다. 다음 doctor 가 다시 시도한다.")
        except LockTimeout as e:
            raise MemoryError("LOCKED", "다른 쓰기가 진행 중이라 잠금을 얻지 못했다.", retryable=True) from e
        self.log.info("tool=update_core entries=%d chars=%d committed=%s", len(parsed), chars, committed)
        return {"saved": True, "committed": committed, "entries": len(parsed), "chars": chars, "warnings": warnings}
