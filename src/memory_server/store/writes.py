"""쓰기 도구: 잠금 → 검증 → 임시 파일+rename → 이벤트 → 색인 → 경로 지정 커밋."""

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
from .models import LIMITS, WriteResult, _collapse_ws, _hash_payload


class WriteMixin:
    """remember(정정 포함), amend, archive, unarchive (SPEC 4, 5)."""

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
