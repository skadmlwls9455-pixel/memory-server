"""입력 상한, 결과·입력 자료형, 정규화 해시."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

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


def _hash_payload(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
