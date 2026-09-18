"""저장소 공통: 잠금·Git 확인, 중복 방지, 새 파일 쓰기, 커밋, 색인 훅, 노트 읽기, fact_key 조회."""

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

from .. import gitutil, mutations
from ..config import Config, TagRules, load_config, load_tag_rules
from ..errors import MemoryError, invalid_input, not_found
from ..events import append_event, iter_events
from ..lock import LockTimeout, write_lock
from ..logging_setup import get_logger
from ..naming import find_note_by_id, id_exists, is_valid_id, new_id, note_path
from ..notes import (
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
from ..paths import MemoryPaths
from ..secrets_guard import find_secret
from ..timeutil import now_local

if TYPE_CHECKING:  # pragma: no cover
    from ..index.indexer import Indexer

# 입력 상한 (SPEC 4)
from .models import LIMITS, RememberInput, WriteResult, _collapse_ws, _hash_payload


class StoreBase:
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
            from ..doctor import NO_GIT_MESSAGE

            raise MemoryError("VAULT_NOT_GIT", NO_GIT_MESSAGE, retryable=False)


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
        from ..index.embedder import EmbeddingUnavailable
        from ..index.indexer import IndexError_

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
