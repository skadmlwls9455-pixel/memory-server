"""저장소: remember, read_memory (SPEC 4, 5, 6).

쓰기 절차 (SPEC 5):
1. 잠금을 얻고 입력을 검증한다.
2. 임시 파일에 쓴 뒤 존재하지 않는 최종 경로로 rename 한다.
3. 이벤트 한 줄을 추가하고 flush 한다. 여기까지 끝나면 saved=true.
4. DB 갱신과 임베딩. (2단계 전이라 indexed=false)
5. 바뀐 경로만 git add 하고 커밋한다. 실패하면 committed=false.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from . import gitutil
from .config import Config, TagRules, load_config, load_tag_rules
from .errors import MemoryError, invalid_input, not_found
from .events import append_event, iter_events
from .lock import LockTimeout, write_lock
from .logging_setup import get_logger
from .naming import find_note_by_id, id_exists, new_id, note_filename, note_path
from .notes import (
    NOTE_TYPES,
    SOURCES,
    Frontmatter,
    Note,
    NoteFormatError,
    body_has_reserved_heading,
    normalize_text,
    parse_note,
    render_note,
)
from .paths import MemoryPaths
from .timeutil import now_local

# 입력 상한 (SPEC 4)
LIMITS = {
    "title": 100,
    "body": 4000,
    "note": 1000,
    "query": 500,
    "tags": 10,
    "links": 10,
    "supersedes": 5,
}


@dataclass
class WriteResult:
    id: str
    saved: bool
    indexed: bool
    committed: bool
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "saved": self.saved,
            "indexed": self.indexed,
            "committed": self.committed,
            "warnings": list(self.warnings),
        }


@dataclass
class RememberInput:
    title: str
    body: str
    tags: list[str]
    type: str
    source: str
    fact_key: str | None
    links: list[str]
    supersedes: list[str]
    reason: str | None

    def input_hash(self) -> str:
        """정규화한 입력의 해시. 10분 중복 방지에 쓴다 (SPEC 5)."""
        payload = {
            "tool": "remember",
            "title": self.title,
            "body": self.body,
            "tags": sorted(self.tags),
            "type": self.type,
            "source": self.source,
            "fact_key": self.fact_key,
            "links": sorted(self.links),
            "supersedes": sorted(self.supersedes),
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _collapse_ws(value: str) -> str:
    return " ".join(value.split())


class MemoryStore:
    def __init__(
        self,
        paths: MemoryPaths,
        config: Config | None = None,
        tag_rules: TagRules | None = None,
        now: Callable[[], datetime] = now_local,
    ) -> None:
        self.paths = paths
        self.config = config if config is not None else load_config(paths.config_toml)
        self.tag_rules = tag_rules if tag_rules is not None else load_tag_rules(paths.tag_rules_yaml)
        self.now = now
        self.log = get_logger()

    # ------------------------------------------------------------------ 검증

    def _validate_remember(
        self,
        title: str,
        body: str,
        tags: list[str] | None,
        type: str,
        source: str,
        fact_key: str | None,
        links: list[str] | None,
        supersedes: list[str] | None,
        reason: str | None,
    ) -> RememberInput:
        if not isinstance(title, str) or not isinstance(body, str):
            raise invalid_input("title 과 body 는 문자열이어야 한다.")
        title_n = _collapse_ws(normalize_text(title))
        body_n = normalize_text(body).strip()
        if not title_n:
            raise invalid_input("title 이 비어 있다.", field="title")
        if len(title_n) > LIMITS["title"]:
            raise invalid_input(f"title 은 {LIMITS['title']}자 이하여야 한다.", field="title", limit=LIMITS["title"])
        if "\n" in title.strip():
            raise invalid_input("title 에 줄바꿈이 있을 수 없다.", field="title")
        if not body_n:
            raise invalid_input("body 가 비어 있다.", field="body")
        if len(body_n) > LIMITS["body"]:
            raise invalid_input(f"body 는 {LIMITS['body']}자 이하여야 한다.", field="body", limit=LIMITS["body"])
        reserved = body_has_reserved_heading(body_n)
        if reserved:
            raise invalid_input(f"body 에 예약된 제목 '{reserved}' 줄을 넣을 수 없다.", field="body")
        if body_n.lstrip().startswith("---"):
            raise invalid_input("body 는 '---' 로 시작할 수 없다.", field="body")

        if type not in NOTE_TYPES:
            raise invalid_input(f"type 은 {', '.join(NOTE_TYPES)} 중 하나여야 한다.", field="type")
        if source not in SOURCES:
            raise invalid_input(f"source 는 {', '.join(SOURCES)} 중 하나여야 한다.", field="source")
        if source == "inference" and type != "reflection":
            raise MemoryError(
                "INVALID_SOURCE",
                "source=inference 는 type=reflection 에서만 쓸 수 있다. 사용자가 확인해 준 사실이면 source=user 로 다시 저장한다.",
                retryable=False,
            )
        if type == "reflection" and source != "inference":
            raise MemoryError(
                "INVALID_SOURCE",
                "type=reflection 은 source=inference 여야 한다. 사용자가 말한 내용이면 type=episode 나 fact 로 저장한다.",
                retryable=False,
            )

        fact_key_n = None
        if fact_key is not None:
            if not isinstance(fact_key, str) or not fact_key.strip():
                raise invalid_input("fact_key 가 비어 있다.", field="fact_key")
            fact_key_n = normalize_text(fact_key.strip())
            if len(fact_key_n) > 200 or any(ch.isspace() for ch in fact_key_n):
                raise invalid_input("fact_key 는 공백 없는 200자 이하 문자열이어야 한다 (예: self/employer).", field="fact_key")
        if type == "fact" and fact_key_n is None:
            raise invalid_input("type=fact 는 fact_key 가 필수다.", field="fact_key")
        if type != "fact" and fact_key_n is not None:
            raise invalid_input("fact_key 는 type=fact 일 때만 쓴다.", field="fact_key")

        tags_n = self._normalize_tags(tags)

        links_n = self._normalize_ids(links, "links")
        supersedes_n = self._normalize_ids(supersedes, "supersedes")
        if supersedes_n and not (isinstance(reason, str) and reason.strip()):
            raise invalid_input("supersedes 가 있으면 reason 이 필수다.", field="reason")
        if set(links_n) & set(supersedes_n):
            raise invalid_input("같은 ID 를 links 와 supersedes 에 동시에 넣을 수 없다.")

        return RememberInput(
            title=title_n,
            body=body_n,
            tags=tags_n,
            type=type,
            source=source,
            fact_key=fact_key_n,
            links=links_n,
            supersedes=supersedes_n,
            reason=reason.strip() if isinstance(reason, str) and reason.strip() else None,
        )

    def _normalize_tags(self, tags: list[str] | None) -> list[str]:
        if tags is None:
            return []
        if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
            raise invalid_input("tags 는 문자열 목록이어야 한다.", field="tags")
        seen: list[str] = []
        for raw in tags:
            tag = unicodedata.normalize("NFC", raw.strip())
            if not tag:
                continue
            if not self.tag_rules.is_valid(tag):
                raise MemoryError(
                    "INVALID_TAG",
                    f"태그 '{tag}' 는 규칙에 맞지 않는다. 형식은 '분류/이름' 이고 분류는 주제, 사람, 프로젝트, 상태, 시기 중 하나다.",
                    retryable=False,
                    tag=tag,
                )
            if tag not in seen:
                seen.append(tag)
        if len(seen) > LIMITS["tags"]:
            raise invalid_input(f"tags 는 {LIMITS['tags']}개 이하여야 한다.", field="tags", limit=LIMITS["tags"])
        return seen

    def _normalize_ids(self, ids: list[str] | None, field_name: str) -> list[str]:
        if ids is None:
            return []
        if not isinstance(ids, list) or not all(isinstance(t, str) for t in ids):
            raise invalid_input(f"{field_name} 는 ID 문자열 목록이어야 한다.", field=field_name)
        out: list[str] = []
        for raw in ids:
            nid = raw.strip()
            if nid and nid not in out:
                out.append(nid)
        limit = LIMITS[field_name]
        if len(out) > limit:
            raise invalid_input(f"{field_name} 는 {limit}개 이하여야 한다.", field=field_name, limit=limit)
        for nid in out:
            if find_note_by_id(self.paths, nid) is None:
                raise MemoryError("NOT_FOUND", f"{field_name} 의 ID {nid} 인 노트가 없다.", retryable=False, id=nid, field=field_name)
        return out

    # ------------------------------------------------------------------ 중복 방지

    def _find_recent_duplicate(self, input_hash: str, now: datetime) -> str | None:
        window = timedelta(minutes=self.config.write.dedupe_window_minutes)
        since = now - window
        months = {(now.year, now.month), (since.year, since.month)}
        for year, month in sorted(months):
            for ev in iter_events(self.paths.events_file(year, month)):
                if ev.type != "created" or ev.data.get("input_hash") != input_hash:
                    continue
                try:
                    ts = datetime.fromisoformat(ev.ts)
                except ValueError:
                    continue
                if since <= ts <= now and ev.ids and find_note_by_id(self.paths, ev.ids[0]) is not None:
                    return ev.ids[0]
        return None

    # ------------------------------------------------------------------ 파일 쓰기

    def _write_new_file(self, final_path: Path, content: str) -> None:
        """같은 디렉터리의 임시 파일에 쓴 뒤 존재하지 않는 최종 경로로 rename 한다."""
        final_path.parent.mkdir(parents=True, exist_ok=True)
        self.paths.ensure_inside_vault(final_path.parent)
        if final_path.exists():
            raise MemoryError("WRITE_FAILED", f"최종 경로가 이미 있다: {final_path.name}", retryable=True)
        fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".md", dir=final_path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
                f.flush()
            os.rename(tmp, final_path)
        except Exception:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            raise

    def _commit(self, changed: list[Path], message: str, warnings: list[str]) -> bool:
        try:
            gitutil.commit_paths(self.paths.vault, changed, message)
            return True
        except gitutil.GitError as e:
            self.log.warning("git commit failed code=GIT_COMMIT_FAILED")
            warnings.append(f"GIT_COMMIT_FAILED: 커밋에 실패했다. 다음 doctor 가 다시 시도한다. ({e.__class__.__name__})")
            return False

    # ------------------------------------------------------------------ 도구

    def remember(
        self,
        title: str,
        body: str,
        tags: list[str] | None = None,
        type: str = "episode",
        source: str = "user",
        fact_key: str | None = None,
        links: list[str] | None = None,
        supersedes: list[str] | None = None,
        reason: str | None = None,
    ) -> WriteResult:
        try:
            with write_lock(self.paths.lock_file):
                return self._remember_locked(title, body, tags, type, source, fact_key, links, supersedes, reason)
        except LockTimeout as e:
            raise MemoryError("LOCKED", "다른 쓰기가 진행 중이라 잠금을 얻지 못했다. 잠시 뒤 다시 시도한다.", retryable=True) from e

    def _remember_locked(self, title, body, tags, type, source, fact_key, links, supersedes, reason) -> WriteResult:
        inp = self._validate_remember(title, body, tags, type, source, fact_key, links, supersedes, reason)
        if inp.supersedes:
            # 1-3 에서 구현한다. 그 전에는 명확히 거부한다.
            raise MemoryError("NOT_IMPLEMENTED", "supersedes 정정은 아직 구현되지 않았다.", retryable=False)

        now = self.now()
        input_hash = inp.input_hash()
        existing = self._find_recent_duplicate(input_hash, now)
        if existing is not None:
            self.log.info("tool=remember dedupe id=%s", existing)
            return WriteResult(id=existing, saved=True, indexed=False, committed=True, warnings=["DUPLICATE_INPUT: 10분 안에 같은 입력이 있어 기존 ID 를 돌려준다."])

        note_id = new_id(id_exists(self.paths), now=now)
        fm = Frontmatter(
            id=note_id,
            created=now.isoformat(timespec="seconds"),
            type=inp.type,
            source=inp.source,
            fact_key=inp.fact_key,
            tags=inp.tags,
            links=inp.links,
            supersedes=[],
            status="active",
            superseded_by=None,
        )
        related = [f"- 관련: {self._wikilink(lid)}" for lid in inp.links]
        note = Note(frontmatter=fm, title=inp.title, body=inp.body, related=related)
        path = note_path(self.paths, inp.title, note_id, now)

        warnings: list[str] = []
        self._write_new_file(path, render_note(note))
        events_path = append_event(
            self.paths,
            "created",
            [note_id],
            {"supersedes": [], "type": inp.type, "input_hash": input_hash},
            ts=now,
        )
        # 여기까지 끝나면 saved=true
        committed = self._commit([path, events_path], f"remember: {note_id}", warnings)
        self.log.info("tool=remember id=%s committed=%s", note_id, committed)
        return WriteResult(id=note_id, saved=True, indexed=False, committed=committed, warnings=warnings)

    def _wikilink(self, note_id: str) -> str:
        path = find_note_by_id(self.paths, note_id)
        assert path is not None  # _normalize_ids 가 확인했다
        stem = path.stem
        try:
            title = parse_note(path.read_text(encoding="utf-8")).title
        except NoteFormatError:
            title = stem[: -len(note_id) - 1]
        return f"[[{stem}|{title}]]"

    def load_note(self, note_id: str) -> tuple[Path, Note]:
        if not isinstance(note_id, str) or not note_id.strip():
            raise invalid_input("id 가 비어 있다.", field="id")
        note_id = note_id.strip()
        path = find_note_by_id(self.paths, note_id)
        if path is None:
            raise not_found(note_id)
        try:
            note = parse_note(path.read_text(encoding="utf-8"))
        except NoteFormatError as e:
            raise MemoryError("NOTE_CORRUPT", f"노트 {note_id} 의 형식이 깨져 있다: {e}", retryable=False, id=note_id) from e
        if note.id != note_id:
            raise MemoryError("NOTE_CORRUPT", f"파일명 ID 와 frontmatter id 가 다르다: {note_id}", retryable=False, id=note_id)
        return path, note

    def read_memory(self, id: str) -> dict[str, Any]:
        path, note = self.load_note(id)
        fm = note.frontmatter
        self.log.info("tool=read_memory id=%s", fm.id)
        return {
            "id": fm.id,
            "title": note.title,
            "type": fm.type,
            "source": fm.source,
            "fact_key": fm.fact_key,
            "tags": list(fm.tags),
            "links": list(fm.links),
            "created": fm.created,
            "status": fm.status,
            "supersedes": list(fm.supersedes),
            "superseded_by": fm.superseded_by,
            "body": note.body,
            "related": list(note.related),
            "amendments": [{"date": a.date, "text": a.text} for a in note.amendments],
            "path": str(path.relative_to(self.paths.vault)),
        }
