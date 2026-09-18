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
from typing import TYPE_CHECKING, Any, Callable

from . import gitutil, mutations
from .config import Config, TagRules, load_config, load_tag_rules
from .errors import MemoryError, invalid_input, not_found
from .events import append_event, iter_events
from .lock import LockTimeout, write_lock
from .logging_setup import get_logger
from .naming import find_note_by_id, id_exists, is_valid_id, new_id, note_path
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
from .secrets_guard import find_secret
from .timeutil import now_local

if TYPE_CHECKING:  # pragma: no cover
    from .index.indexer import Indexer

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
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out = {
            "id": self.id,
            "saved": self.saved,
            "indexed": self.indexed,
            "committed": self.committed,
            "warnings": list(self.warnings),
        }
        out.update(self.extra)
        return out


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
        return _hash_payload(payload)


def _collapse_ws(value: str) -> str:
    return " ".join(value.split())


class MemoryStore:
    def __init__(
        self,
        paths: MemoryPaths,
        config: Config | None = None,
        tag_rules: TagRules | None = None,
        now: Callable[[], datetime] = now_local,
        crash_hook: Callable[[str], None] | None = None,
        indexer: "Indexer | None" = None,
    ) -> None:
        self.paths = paths
        self.config = config if config is not None else load_config(paths.config_toml)
        self.tag_rules = tag_rules if tag_rules is not None else load_tag_rules(paths.tag_rules_yaml)
        self.now = now
        self.log = get_logger()
        # 테스트 전용. 쓰기 절차의 지점 이름으로 불린다: after_note_written, before_supersede, before_event, before_index.
        self._crash_hook = crash_hook
        # 2단계: 색인기. 없으면 indexed=false 로 둔다.
        self.indexer = indexer

    def _checkpoint(self, name: str) -> None:
        if self._crash_hook is not None:
            self._crash_hook(name)

    def _require_git(self) -> None:
        """.git 이 없으면 쓰기를 멈추고 백업 복원을 안내한다 (SPEC 5 doctor 표)."""
        if not gitutil.is_repo(self.paths.vault):
            from .doctor import NO_GIT_MESSAGE

            raise MemoryError("VAULT_NOT_GIT", NO_GIT_MESSAGE, retryable=False)

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
        for label, value in (("title", title_n), ("body", body_n)):
            hit = find_secret(value)
            if hit:
                raise MemoryError(
                    "SECRET_DETECTED",
                    f"{label} 에 비밀 형식({hit})으로 보이는 문자열이 있다. 비밀번호, API 키, 토큰, 개인키는 저장하지 않는다.",
                    retryable=False,
                    field=label,
                    pattern=hit,
                )
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

    def _find_recent_duplicate(self, input_hash: str, now: datetime, event_types: tuple[str, ...] = ("created", "recovered")) -> str | None:
        window = timedelta(minutes=self.config.write.dedupe_window_minutes)
        since = now - window
        months = {(now.year, now.month), (since.year, since.month)}
        for year, month in sorted(months):
            for ev in iter_events(self.paths.events_file(year, month)):
                if ev.type not in event_types or ev.data.get("input_hash") != input_hash:
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

    # ------------------------------------------------------------------ 색인 (SPEC 5 4단계)

    def _index_after_write(self, path: Path, warnings: list[str], *, check_similar: bool = False) -> bool:
        """DB 를 갱신하고 임베딩한다. 실패하면 indexed=false 로 두고 다음 doctor 가 처리한다."""
        if self.indexer is None:
            return False
        from .index.embedder import EmbeddingUnavailable
        from .index.indexer import IndexError_

        try:
            r = self.indexer.index_path(path, check_similar=check_similar)
        except EmbeddingUnavailable as e:
            self.log.warning("index skipped code=EMBEDDING_UNAVAILABLE")
            warnings.append(f"INDEX_FAILED: 임베딩 모델을 쓸 수 없어 색인하지 못했다. 다음 doctor 가 다시 시도한다. ({e})")
            return False
        except (IndexError_, OSError, Exception) as e:  # noqa: BLE001
            self.log.warning("index failed type=%s", type(e).__name__)
            warnings.append(f"INDEX_FAILED: 색인에 실패했다 ({type(e).__name__}). 다음 doctor 가 다시 시도한다.")
            return False
        warnings.extend(r.warnings)
        for other_id, sim in r.similar:
            warnings.append(f"SIMILAR_EXISTS: 유사도 {sim:.2f} 인 active 노트 {other_id} 가 이미 있다. 같은 주제면 amend 나 supersedes 를 검토한다.")
        return True

    def _refresh_status_index(self, path: Path, warnings: list[str]) -> bool:
        """상태만 바뀐 노트의 DB 행을 갱신한다. 벡터가 없으면 전체 색인으로 넘어간다."""
        if self.indexer is None:
            return False
        try:
            if self.indexer.refresh_status(path):
                return True
        except Exception as e:  # noqa: BLE001
            self.log.warning("status refresh failed type=%s", type(e).__name__)
        return self._index_after_write(path, warnings)

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
                self._require_git()
                return self._remember_locked(title, body, tags, type, source, fact_key, links, supersedes, reason)
        except LockTimeout as e:
            raise MemoryError("LOCKED", "다른 쓰기가 진행 중이라 잠금을 얻지 못했다. 잠시 뒤 다시 시도한다.", retryable=True) from e

    def _remember_locked(self, title, body, tags, type, source, fact_key, links, supersedes, reason) -> WriteResult:
        inp = self._validate_remember(title, body, tags, type, source, fact_key, links, supersedes, reason)
        now = self.now()
        input_hash = inp.input_hash()
        existing = self._find_recent_duplicate(input_hash, now)
        if existing is not None:
            self.log.info("tool=remember dedupe id=%s", existing)
            return WriteResult(id=existing, saved=True, indexed=False, committed=True, warnings=["DUPLICATE_INPUT: 10분 안에 같은 입력이 있어 기존 ID 를 돌려준다."])

        # 정정 대상 확인 (SPEC 4): 이미 대체된 노트는 다시 대체할 수 없다.
        targets: list[tuple[Path, Note]] = []
        for old_id in inp.supersedes:
            old_path, old_note = self.load_note(old_id)
            if old_note.frontmatter.status == "superseded":
                raise MemoryError(
                    "INVALID_SUPERSEDES",
                    f"노트 {old_id} 는 이미 {old_note.frontmatter.superseded_by} 로 대체됐다. 최신 노트를 대상으로 정정한다.",
                    retryable=False,
                    id=old_id,
                    superseded_by=old_note.frontmatter.superseded_by,
                )
            targets.append((old_path, old_note))

        # fact_key 유일성 (SPEC 4): 같은 키의 active 노트는 하나뿐이다.
        if inp.type == "fact":
            self._check_fact_conflict(inp.fact_key, allowed=set(inp.supersedes))

        note_id = new_id(id_exists(self.paths), now=now)
        fm = Frontmatter(
            id=note_id,
            created=now.isoformat(timespec="seconds"),
            type=inp.type,
            source=inp.source,
            fact_key=inp.fact_key,
            tags=inp.tags,
            links=inp.links,
            supersedes=inp.supersedes,
            status="active",
            superseded_by=None,
        )
        related = [f"- 대체함: {self._wikilink(p.stem, n.title)}" for p, n in targets]
        related += [f"- 관련: {self._wikilink_by_id(lid)}" for lid in inp.links]
        note = Note(frontmatter=fm, title=inp.title, body=inp.body, related=related)
        path = note_path(self.paths, inp.title, note_id, now)

        warnings: list[str] = []
        changed: list[Path] = [path]
        self._write_new_file(path, render_note(note))
        self._checkpoint("after_note_written")
        # 정정: 기존 노트의 status 와 superseded_by 만 바꾼다. 같은 잠금 안에서 끝난다.
        if targets:
            self._checkpoint("before_supersede")
        for old_path, _ in targets:
            mutations.set_status(old_path, "superseded", superseded_by=note_id)
            changed.append(old_path)
        self._checkpoint("before_event")
        events_path = append_event(
            self.paths,
            "created",
            [note_id],
            {"supersedes": inp.supersedes, "type": inp.type, "input_hash": input_hash, **({"reason": inp.reason} if inp.reason else {})},
            ts=now,
        )
        changed.append(events_path)
        # 여기까지 끝나면 saved=true
        self._checkpoint("before_index")
        indexed = self._index_after_write(path, warnings, check_similar=True)
        for old_path, _ in targets:
            self._refresh_status_index(old_path, warnings)
        message = f"remember: {note_id}" + (f" supersedes {' '.join(inp.supersedes)}" if inp.supersedes else "")
        committed = self._commit(changed, message, warnings)
        self.log.info("tool=remember id=%s supersedes=%d indexed=%s committed=%s", note_id, len(targets), indexed, committed)
        return WriteResult(id=note_id, saved=True, indexed=indexed, committed=committed, warnings=warnings)

    def _check_fact_conflict(self, fact_key: str, allowed: set[str]) -> None:
        for path, fm in self.iter_frontmatters():
            if fm.type != "fact" or fm.status != "active" or fm.fact_key != fact_key or fm.id in allowed:
                continue
            content = path.read_text(encoding="utf-8")
            try:
                title = parse_note(content).title
            except NoteFormatError:
                title = path.stem
            raise MemoryError(
                "FACT_CONFLICT",
                f"fact_key '{fact_key}' 의 active 노트 {fm.id} 가 이미 있다. 사실이 바뀐 것이면 supersedes=[\"{fm.id}\"] 와 reason 을 넣어 다시 저장하고, 보충이면 amend 를 쓴다.",
                retryable=True,
                fact_key=fact_key,
                existing={"id": fm.id, "title": title, "path": str(path.relative_to(self.paths.vault)), "content": content},
            )

    def iter_frontmatters(self):
        """vault/memories 의 모든 노트 frontmatter. 깨진 노트는 건너뛴다 (doctor 가 다룬다)."""
        if not self.paths.memories.exists():
            return
        for path in sorted(self.paths.memories.rglob("*.md")):
            if path.name.startswith("."):
                continue
            try:
                yield path, parse_note(path.read_text(encoding="utf-8")).frontmatter
            except (NoteFormatError, OSError):
                continue

    def find_active_by_fact_key(self, fact_key: str) -> list[str]:
        return [fm.id for _, fm in self.iter_frontmatters() if fm.type == "fact" and fm.status == "active" and fm.fact_key == fact_key]

    @staticmethod
    def _wikilink(stem: str, title: str) -> str:
        return f"[[{stem}|{title}]]"

    def _wikilink_by_id(self, note_id: str) -> str:
        path = find_note_by_id(self.paths, note_id)
        assert path is not None  # _normalize_ids 가 확인했다
        stem = path.stem
        try:
            title = parse_note(path.read_text(encoding="utf-8")).title
        except NoteFormatError:
            title = stem[: -len(note_id) - 1]
        return self._wikilink(stem, title)

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

    # ------------------------------------------------------------------ amend

    def amend(self, id: str, note: str) -> WriteResult:
        try:
            with write_lock(self.paths.lock_file):
                self._require_git()
                return self._amend_locked(id, note)
        except LockTimeout as e:
            raise MemoryError("LOCKED", "다른 쓰기가 진행 중이라 잠금을 얻지 못했다. 잠시 뒤 다시 시도한다.", retryable=True) from e

    def _amend_locked(self, id: str, note: str) -> WriteResult:
        if not isinstance(note, str):
            raise invalid_input("note 는 문자열이어야 한다.", field="note")
        text = _collapse_ws(normalize_text(note))
        if not text:
            raise invalid_input("note 가 비어 있다.", field="note")
        if len(text) > LIMITS["note"]:
            raise invalid_input(f"note 는 {LIMITS['note']}자 이하여야 한다.", field="note", limit=LIMITS["note"])
        hit = find_secret(text)
        if hit:
            raise MemoryError(
                "SECRET_DETECTED",
                f"note 에 비밀 형식({hit})으로 보이는 문자열이 있다. 비밀번호, API 키, 토큰, 개인키는 저장하지 않는다.",
                retryable=False,
                field="note",
                pattern=hit,
            )
        path, existing = self.load_note(id)
        note_id = existing.id
        if existing.frontmatter.status != "active":
            raise MemoryError(
                "NOT_ACTIVE",
                f"노트 {note_id} 는 {existing.frontmatter.status} 상태라 보충할 수 없다. 대체된 노트면 최신 노트에, 보관된 노트면 unarchive 뒤에 보충한다.",
                retryable=False,
                id=note_id,
                status=existing.frontmatter.status,
                superseded_by=existing.frontmatter.superseded_by,
            )
        now = self.now()
        input_hash = _hash_payload({"tool": "amend", "id": note_id, "note": text})
        if self._find_recent_duplicate(input_hash, now, event_types=("amended",)) is not None:
            self.log.info("tool=amend dedupe id=%s", note_id)
            return WriteResult(id=note_id, saved=True, indexed=False, committed=True, warnings=["DUPLICATE_INPUT: 10분 안에 같은 보충이 있어 다시 더하지 않는다."])

        line = mutations.append_amendment(path, now.date().isoformat(), text)
        self._checkpoint("before_event")
        events_path = append_event(self.paths, "amended", [note_id], {"input_hash": input_hash}, ts=now)
        self._checkpoint("before_index")
        warnings: list[str] = []
        indexed = self._index_after_write(path, warnings)
        committed = self._commit([path, events_path], f"amend: {note_id}", warnings)
        self.log.info("tool=amend id=%s indexed=%s committed=%s", note_id, indexed, committed)
        return WriteResult(id=note_id, saved=True, indexed=indexed, committed=committed, warnings=warnings, extra={"line": line})

    # ------------------------------------------------------------------ archive / unarchive

    def archive(self, id: str, reason: str) -> WriteResult:
        try:
            with write_lock(self.paths.lock_file):
                self._require_git()
                return self._archive_locked(id, reason)
        except LockTimeout as e:
            raise MemoryError("LOCKED", "다른 쓰기가 진행 중이라 잠금을 얻지 못했다. 잠시 뒤 다시 시도한다.", retryable=True) from e

    def unarchive(self, id: str, reason: str) -> WriteResult:
        try:
            with write_lock(self.paths.lock_file):
                self._require_git()
                return self._unarchive_locked(id, reason)
        except LockTimeout as e:
            raise MemoryError("LOCKED", "다른 쓰기가 진행 중이라 잠금을 얻지 못했다. 잠시 뒤 다시 시도한다.", retryable=True) from e

    @staticmethod
    def _require_reason(reason: str | None) -> str:
        if not isinstance(reason, str) or not reason.strip():
            raise invalid_input("reason 이 필요하다.", field="reason")
        text = _collapse_ws(normalize_text(reason))
        if len(text) > LIMITS["note"]:
            raise invalid_input(f"reason 은 {LIMITS['note']}자 이하여야 한다.", field="reason", limit=LIMITS["note"])
        return text

    def _archive_locked(self, id: str, reason: str) -> WriteResult:
        reason_n = self._require_reason(reason)
        path, existing = self.load_note(id)
        note_id = existing.id
        status = existing.frontmatter.status
        if status == "archived":
            return WriteResult(id=note_id, saved=True, indexed=False, committed=True, extra={"status": status, "no_change": True})
        if status == "superseded":
            raise MemoryError(
                "NOT_ACTIVE",
                f"노트 {note_id} 는 이미 {existing.frontmatter.superseded_by} 로 대체돼 기본 회상에서 빠져 있다. 보관할 필요가 없다.",
                retryable=False,
                id=note_id,
                status=status,
                superseded_by=existing.frontmatter.superseded_by,
            )
        now = self.now()
        mutations.set_status(path, "archived")
        events_path = append_event(self.paths, "archived", [note_id], {"reason": reason_n}, ts=now)
        warnings: list[str] = []
        indexed = self._refresh_status_index(path, warnings)
        committed = self._commit([path, events_path], f"archive: {note_id}", warnings)
        self.log.info("tool=archive id=%s committed=%s", note_id, committed)
        return WriteResult(id=note_id, saved=True, indexed=indexed, committed=committed, warnings=warnings, extra={"status": "archived", "no_change": False})

    def _unarchive_locked(self, id: str, reason: str) -> WriteResult:
        reason_n = self._require_reason(reason)
        path, existing = self.load_note(id)
        note_id = existing.id
        fm = existing.frontmatter
        if fm.status == "active":
            return WriteResult(id=note_id, saved=True, indexed=False, committed=True, extra={"status": "active", "no_change": True})
        if fm.status == "superseded":
            raise MemoryError(
                "SUPERSEDED",
                f"노트 {note_id} 는 {fm.superseded_by} 로 대체된 노트라 되살릴 수 없다. unarchive 는 수동 보관만 되돌린다.",
                retryable=False,
                id=note_id,
                superseded_by=fm.superseded_by,
            )
        if fm.type == "fact" and fm.fact_key:
            self._check_fact_conflict(fm.fact_key, allowed={note_id})
        now = self.now()
        mutations.set_status(path, "active")
        events_path = append_event(self.paths, "unarchived", [note_id], {"reason": reason_n}, ts=now)
        warnings: list[str] = []
        indexed = self._refresh_status_index(path, warnings)
        committed = self._commit([path, events_path], f"unarchive: {note_id}", warnings)
        self.log.info("tool=unarchive id=%s committed=%s", note_id, committed)
        return WriteResult(id=note_id, saved=True, indexed=indexed, committed=committed, warnings=warnings, extra={"status": "active", "no_change": False})

    # ------------------------------------------------------------------ mark_used (SPEC 7.3)

    def mark_used(self, ids: list[str]) -> dict[str, Any]:
        if not isinstance(ids, list) or not ids or not all(isinstance(i, str) and i.strip() for i in ids):
            raise invalid_input("ids 는 비어 있지 않은 ID 목록이어야 한다.", field="ids")
        if len(ids) > 20:
            raise invalid_input("ids 는 20개 이하여야 한다.", field="ids", limit=20)
        if self.indexer is None:
            raise MemoryError("INDEX_UNAVAILABLE", "DB 를 열 수 없어 열 점수를 갱신하지 못한다.", retryable=True)
        try:
            with write_lock(self.paths.lock_file):
                self._require_git()
                return self._mark_used_locked([i.strip() for i in dict.fromkeys(ids)])
        except LockTimeout as e:
            raise MemoryError("LOCKED", "다른 쓰기가 진행 중이라 잠금을 얻지 못했다. 잠시 뒤 다시 시도한다.", retryable=True) from e

    def _mark_used_locked(self, ids: list[str]) -> dict[str, Any]:
        from .recall.heat import reheat

        db = self.indexer.db  # type: ignore[union-attr]
        now = self.now()
        cfg = self.config.heat
        window = timedelta(hours=cfg.reheat_window_hours)
        heated: list[str] = []
        skipped: dict[str, str] = {}
        for nid in ids:
            row = db.get(nid)
            if row is None:
                skipped[nid] = "NOT_INDEXED" if find_note_by_id(self.paths, nid) else "NOT_FOUND"
                continue
            if row.status != "active":
                skipped[nid] = "NOT_ACTIVE"
                continue
            if row.used_at:
                try:
                    if now - datetime.fromisoformat(row.used_at) < window:
                        skipped[nid] = "RECENTLY_USED"
                        continue
                except ValueError:
                    pass
            try:
                heat_at = datetime.fromisoformat(row.heat_at)
            except ValueError:
                heat_at = now
            new_heat = reheat(row.heat, heat_at, now, cfg)
            iso = now.isoformat(timespec="seconds")
            db.set_heat(nid, new_heat, iso, iso)
            heated.append(nid)
        committed = True
        warnings: list[str] = []
        if heated:
            events_path = append_event(self.paths, "used", heated, {}, ts=now)
            committed = self._commit([events_path], f"used: {len(heated)}", warnings)
        self.log.info("tool=mark_used heated=%d skipped=%d", len(heated), len(skipped))
        return {"heated": heated, "skipped": skipped, "committed": committed, "warnings": warnings}


def _hash_payload(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
