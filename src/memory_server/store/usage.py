"""mark_used: 6시간 재신고 무시, used 이벤트, 커밋."""

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


class UsageMixin:
    """mark_used 와 열 점수 갱신 (SPEC 7.3)."""

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
        from ..recall.heat import reheat

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
