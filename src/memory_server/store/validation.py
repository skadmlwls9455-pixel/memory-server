"""입력 검증 (SPEC 4): 상한, type/source, fact_key, 태그, ID 목록, 비밀 형식."""

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
from .models import LIMITS, RememberInput, _collapse_ws


class ValidationMixin:
    """remember/amend/archive 입력 검증 (SPEC 4)."""

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


    @staticmethod
    def _require_reason(reason: str | None) -> str:
        if not isinstance(reason, str) or not reason.strip():
            raise invalid_input("reason 이 필요하다.", field="reason")
        text = _collapse_ws(normalize_text(reason))
        if len(text) > LIMITS["note"]:
            raise invalid_input(f"reason 은 {LIMITS['note']}자 이하여야 한다.", field="reason", limit=LIMITS["note"])
        return text
