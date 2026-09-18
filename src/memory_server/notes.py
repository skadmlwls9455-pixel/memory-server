"""노트 형식 (SPEC 3.3): frontmatter, 제목, 본문, `## 관련`, `## 덧붙임`.

이 모듈은 문자열을 만들고 읽기만 한다. 파일 쓰기는 store가 맡는다.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import yaml

from .naming import is_valid_id

NOTE_TYPES = ("episode", "fact", "reflection")
SOURCES = ("user", "document", "inference")
STATUSES = ("active", "archived", "superseded")

RELATED_HEADING = "## 관련"
AMEND_HEADING = "## 덧붙임"
RESERVED_HEADINGS = (RELATED_HEADING, AMEND_HEADING)

_FRONTMATTER_KEYS = ("id", "created", "type", "source", "fact_key", "tags", "links", "supersedes", "status", "superseded_by")
_AMEND_LINE_RE = re.compile(r"^- (\d{4}-\d{2}-\d{2}): (.*)$")


class NoteFormatError(ValueError):
    """frontmatter가 깨졌거나 필수 필드가 없다."""


@dataclass
class Frontmatter:
    id: str
    created: str
    type: str
    source: str
    fact_key: str | None
    tags: list[str]
    links: list[str]
    supersedes: list[str]
    status: str = "active"
    superseded_by: str | None = None

    def validate(self) -> None:
        if not is_valid_id(self.id):
            raise NoteFormatError(f"id 형식이 틀렸다: {self.id}")
        if self.type not in NOTE_TYPES:
            raise NoteFormatError(f"type 이 틀렸다: {self.type}")
        if self.source not in SOURCES:
            raise NoteFormatError(f"source 가 틀렸다: {self.source}")
        if self.status not in STATUSES:
            raise NoteFormatError(f"status 가 틀렸다: {self.status}")
        try:
            datetime.fromisoformat(self.created)
        except ValueError as e:
            raise NoteFormatError(f"created 가 ISO 8601 이 아니다: {self.created}") from e
        for name in ("tags", "links", "supersedes"):
            value = getattr(self, name)
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise NoteFormatError(f"{name} 는 문자열 목록이어야 한다.")


@dataclass
class Amendment:
    date: str
    text: str


@dataclass
class Note:
    frontmatter: Frontmatter
    title: str
    body: str
    related: list[str] = field(default_factory=list)  # `## 관련` 아래 줄들 (- 로 시작하는 원문)
    amendments: list[Amendment] = field(default_factory=list)

    @property
    def id(self) -> str:
        return self.frontmatter.id


def _yaml_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    s = str(value)
    # 한글, 영숫자, 몇 가지 기호만 있으면 그대로 두고 나머지는 JSON 문자열로 감싼다 (YAML 호환).
    if re.fullmatch(r"[0-9A-Za-z가-힣_/\-.]+", s) and not re.fullmatch(r"[0-9.\-]+", s) and s not in ("null", "true", "false"):
        return s
    return json.dumps(s, ensure_ascii=False)


def _yaml_list(values: list[str]) -> str:
    return "[" + ", ".join(_yaml_scalar(v) for v in values) + "]"


def render_frontmatter(fm: Frontmatter) -> str:
    lines = [
        "---",
        f"id: {fm.id}",
        f"created: {json.dumps(fm.created)}",
        f"type: {fm.type}",
        f"source: {fm.source}",
        f"fact_key: {_yaml_scalar(fm.fact_key)}",
        f"tags: {_yaml_list(fm.tags)}",
        f"links: {_yaml_list(fm.links)}",
        f"supersedes: {_yaml_list(fm.supersedes)}",
        f"status: {fm.status}",
        f"superseded_by: {_yaml_scalar(fm.superseded_by)}",
        "---",
    ]
    return "\n".join(lines) + "\n"


def render_note(note: Note) -> str:
    """새 노트 전문을 만든다. 생성 때 한 번만 쓴다."""
    parts = [render_frontmatter(note.frontmatter), f"# {note.title}\n", "\n", note.body.rstrip("\n") + "\n"]
    if note.related:
        parts.append("\n" + RELATED_HEADING + "\n")
        parts.extend(line.rstrip("\n") + "\n" for line in note.related)
    if note.amendments:
        parts.append("\n" + AMEND_HEADING + "\n")
        parts.extend(f"- {a.date}: {a.text}\n" for a in note.amendments)
    return "".join(parts)


def body_has_reserved_heading(body: str) -> str | None:
    for line in body.splitlines():
        stripped = line.rstrip()
        if stripped in RESERVED_HEADINGS:
            return stripped
    return None


def split_frontmatter(text: str) -> tuple[str, str]:
    """(frontmatter yaml, 나머지)를 돌려준다. 없으면 NoteFormatError."""
    if not text.startswith("---\n"):
        raise NoteFormatError("frontmatter 시작(---)이 없다.")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise NoteFormatError("frontmatter 끝(---)이 없다.")
    return text[4:end + 1], text[end + 5:]


def parse_frontmatter(yaml_text: str) -> Frontmatter:
    try:
        data = yaml.safe_load(yaml_text)
    except yaml.YAMLError as e:
        raise NoteFormatError(f"frontmatter YAML 파싱 실패: {e}") from e
    if not isinstance(data, dict):
        raise NoteFormatError("frontmatter 가 매핑이 아니다.")
    missing = [k for k in ("id", "created", "type", "source") if k not in data]
    if missing:
        raise NoteFormatError(f"frontmatter 에 필수 필드가 없다: {missing}")
    created = data["created"]
    if isinstance(created, datetime):
        created = created.isoformat(timespec="seconds")
    fm = Frontmatter(
        id=str(data["id"]),
        created=str(created),
        type=str(data["type"]),
        source=str(data["source"]),
        fact_key=None if data.get("fact_key") is None else str(data["fact_key"]),
        tags=[str(t) for t in (data.get("tags") or [])],
        links=[str(t) for t in (data.get("links") or [])],
        supersedes=[str(t) for t in (data.get("supersedes") or [])],
        status=str(data.get("status", "active")),
        superseded_by=None if data.get("superseded_by") is None else str(data["superseded_by"]),
    )
    fm.validate()
    return fm


def parse_note(text: str) -> Note:
    yaml_text, rest = split_frontmatter(text)
    fm = parse_frontmatter(yaml_text)
    lines = rest.split("\n")
    # 제목: 첫 `# ` 줄
    title = ""
    idx = 0
    while idx < len(lines) and not lines[idx].startswith("# "):
        if lines[idx].strip():
            raise NoteFormatError("제목(# ) 앞에 다른 내용이 있다.")
        idx += 1
    if idx >= len(lines):
        raise NoteFormatError("제목(# ) 이 없다.")
    title = lines[idx][2:].strip()
    idx += 1

    body_lines: list[str] = []
    related: list[str] = []
    amendments: list[Amendment] = []
    section = "body"
    for line in lines[idx:]:
        stripped = line.rstrip()
        if stripped == RELATED_HEADING:
            section = "related"
            continue
        if stripped == AMEND_HEADING:
            section = "amend"
            continue
        if section == "body":
            body_lines.append(line)
        elif section == "related":
            if stripped:
                related.append(stripped)
        else:
            if not stripped:
                continue
            m = _AMEND_LINE_RE.match(stripped)
            if not m:
                raise NoteFormatError(f"덧붙임 줄 형식이 틀렸다: {stripped}")
            amendments.append(Amendment(date=m.group(1), text=m.group(2)))
    body = "\n".join(body_lines).strip("\n")
    return Note(frontmatter=fm, title=title, body=body, related=related, amendments=amendments)


def normalize_text(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def wikilink(title: str, note_id: str, filename_stem: str) -> str:
    """[[<제목> <id>|<제목>]] 형식."""
    return f"[[{filename_stem}|{title}]]"
