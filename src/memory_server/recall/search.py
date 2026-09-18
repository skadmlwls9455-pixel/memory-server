"""recall (SPEC 6, 7.1, 7.2).

1. active 만 대상 (include_inactive=true 면 전부, 상태 표시).
2. tags(AND), type 필터.
3. 코사인 유사도 상위 50 에서 min_similarity 미만을 버린다.
4. 상위 k. 없으면 빈 결과. 억지로 채우지 않는다.
5. 벡터 결과가 있을 때만 링크·태그 확장(각 최대 5).
6. 각 경로 안에서 epsilon 묶음 정렬. 반환 순서는 벡터, 링크, 태그. 최대 20건. 상위 3건 전문, 나머지 200자.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Sequence

from ..config import EXPANDABLE_TAG_CATEGORIES, Config
from ..errors import MemoryError, invalid_input
from ..index.db import IndexDB, MemoryRow
from ..index.embedder import Embedder, EmbeddingUnavailable
from ..logging_setup import get_logger
from ..notes import NoteFormatError, parse_note
from ..paths import MemoryPaths
from .heat import decayed_heat
from .ranking import bucket_sort

VECTOR_POOL = 50
KNN_FETCH = 200
MAX_EXPANSION = 5
MAX_RESULTS = 20
FULL_TEXT_TOP = 3
PREVIEW_CHARS = 200
QUERY_MAX = 500


@dataclass
class RecallItem:
    id: str
    title: str
    tags: list[str]
    type: str
    source: str
    fact_key: str | None
    status: str
    similarity: float
    path: str  # vector | link | tag
    heat: float
    created: str
    text: str | None = None  # 상위 3건 전문
    preview: str | None = None  # 나머지 200자
    truncated_preview: bool = False

    def to_dict(self) -> dict[str, Any]:
        """모델에게 주는 항목. SPEC 6 의 필드만: id, 제목, 태그, type, source, fact_key, status, 유사도, 경로.
        heat 와 created 는 정렬에만 쓰고 응답에서 뺀다 (토큰 절약)."""
        d: dict[str, Any] = {
            "id": self.id, "title": self.title, "type": self.type, "source": self.source, "status": self.status,
            "similarity": round(self.similarity, 3), "path": self.path,
        }
        if self.tags:
            d["tags"] = self.tags
        if self.fact_key:
            d["fact_key"] = self.fact_key
        if self.text is not None:
            d["text"] = self.text
        else:
            d["preview"] = self.preview
            if self.truncated_preview:
                d["preview_truncated"] = True
        return d


@dataclass
class RecallResult:
    results: list[RecallItem]
    pending_index: int
    calibrated: bool
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"results": [r.to_dict() for r in self.results], "count": len(self.results), "empty": not self.results}
        if self.pending_index:
            d["pending_index"] = self.pending_index
        if not self.calibrated:
            d["relevance_calibrated"] = False
        if self.warnings:
            d["warnings"] = list(self.warnings)
        return d


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (na * nb)


class Recaller:
    def __init__(
        self,
        paths: MemoryPaths,
        config: Config,
        db: IndexDB,
        embedder: Embedder,
        now: Callable[[], datetime],
        pending_count: Callable[[], int],
        meta_matches: Callable[[], bool],
    ) -> None:
        self.paths = paths
        self.config = config
        self.db = db
        self.embedder = embedder
        self.now = now
        self.pending_count = pending_count
        self.meta_matches = meta_matches
        self.log = get_logger()

    # ------------------------------------------------------------------ 공개

    def recall(
        self,
        query: str,
        k: int = 10,
        tags: list[str] | None = None,
        type: str | None = None,
        include_inactive: bool = False,
    ) -> RecallResult:
        if not isinstance(query, str) or not query.strip():
            raise invalid_input("query 가 비어 있다.", field="query")
        query = query.strip()
        if len(query) > QUERY_MAX:
            raise invalid_input(f"query 는 {QUERY_MAX}자 이하여야 한다.", field="query", limit=QUERY_MAX)
        if not isinstance(k, int) or not 1 <= k <= 10:
            raise invalid_input("k 는 1~10 이어야 한다.", field="k")
        if type is not None and type not in ("episode", "fact", "reflection"):
            raise invalid_input("type 은 episode, fact, reflection 중 하나다.", field="type")
        tags = [t.strip() for t in (tags or []) if isinstance(t, str) and t.strip()]

        if not self.meta_matches():
            raise MemoryError(
                "REBUILD_REQUIRED",
                "색인의 모델이 설정과 다르다. `memory rebuild` 를 실행해야 한다. 다른 모델의 벡터를 섞지 않는다.",
                retryable=False,
            )
        try:
            qvec = self.embedder.embed_query(query)
        except EmbeddingUnavailable as e:
            raise MemoryError("SEARCH_UNAVAILABLE", f"검색을 수행하지 못했다: {e}", retryable=True) from e

        now = self.now()
        rc = self.config.recall
        pending = self.pending_count()
        warnings: list[str] = []
        if pending:
            warnings.append(f"INDEX_PENDING: 색인되지 않은 노트가 {pending}개 있다. 최근 저장분이 결과에 없을 수 있다.")

        # 7.1 벡터 검색
        candidates = self.db.knn(qvec, KNN_FETCH)
        pool: list[tuple[MemoryRow, float]] = []
        for nid, dist in candidates:
            row = self.db.get(nid)
            if row is None:
                continue
            if not include_inactive and row.status != "active":
                continue
            if type is not None and row.type != type:
                continue
            if tags and not set(tags) <= set(self.db.tags_of(nid)):
                continue
            pool.append((row, 1.0 - dist))
            if len(pool) >= VECTOR_POOL:
                break
        pool = [(r, s) for r, s in pool if s >= rc.min_similarity]
        vector_items = [self._item(r, s, "vector", now) for r, s in pool]
        vector_items = bucket_sort(vector_items, lambda x: x.similarity, lambda x: x.heat, lambda x: x.id, rc.epsilon)[:k]

        if not vector_items:
            self.log.info("tool=recall empty=True pending=%d", pending)
            return RecallResult(results=[], pending_index=pending, calibrated=rc.relevance_calibrated, warnings=warnings)

        seen = {it.id for it in vector_items}
        filt = (tuple(tags), type)  # 입력 필터는 확장 경로에도 똑같이 적용한다 (D-040)
        link_items = self._expand_links(vector_items, qvec, seen, now, filt)
        seen |= {it.id for it in link_items}
        tag_items = self._expand_tags(vector_items, qvec, seen, now, filt)

        results = (vector_items + link_items + tag_items)[:MAX_RESULTS]
        self._attach_text(results)
        self.log.info("tool=recall vector=%d link=%d tag=%d pending=%d", len(vector_items), len(link_items), len(tag_items), pending)
        return RecallResult(results=results, pending_index=pending, calibrated=rc.relevance_calibrated, warnings=warnings)

    # ------------------------------------------------------------------ 확장 (7.2)

    def _candidate(self, nid: str, qvec: Sequence[float], seen: set[str], filt: tuple[tuple[str, ...], str | None]) -> tuple[MemoryRow, float] | None:
        if nid in seen:
            return None
        row = self.db.get(nid)
        if row is None or row.status != "active":
            return None
        want_tags, want_type = filt
        if want_type is not None and row.type != want_type:
            return None
        if want_tags and not set(want_tags) <= set(self.db.tags_of(nid)):
            return None
        vec = self.db.vector(nid)
        if vec is None:
            return None
        sim = cosine(qvec, vec)
        if sim < self.config.recall.min_similarity:
            return None
        return row, sim

    def _expand_links(self, base: list[RecallItem], qvec: Sequence[float], seen: set[str], now: datetime, filt) -> list[RecallItem]:
        found: dict[str, tuple[MemoryRow, float]] = {}
        for it in base:
            for nid in self.db.neighbors(it.id):
                if nid in found:
                    continue
                c = self._candidate(nid, qvec, seen, filt)
                if c:
                    found[nid] = c
        items = [self._item(r, s, "link", now) for r, s in found.values()]
        return bucket_sort(items, lambda x: x.similarity, lambda x: x.heat, lambda x: x.id, self.config.recall.epsilon)[:MAX_EXPANSION]

    def _expand_tags(self, base: list[RecallItem], qvec: Sequence[float], seen: set[str], now: datetime, filt) -> list[RecallItem]:
        counter: Counter[str] = Counter()
        for it in base:
            for t in it.tags:
                if t.split("/", 1)[0] in EXPANDABLE_TAG_CATEGORIES:
                    counter[t] += 1
        top_tags = [t for t, _ in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:3]]
        if not top_tags:
            return []
        shared: Counter[str] = Counter()
        for t in top_tags:
            for nid in self.db.ids_with_tag(t):
                shared[nid] += 1
        found: list[tuple[RecallItem, int]] = []
        for nid, n in shared.items():
            c = self._candidate(nid, qvec, seen, filt)
            if c:
                found.append((self._item(c[0], c[1], "tag", now), n))
        # 공유 태그 수가 많은 순, 그 안에서 묶음 정렬
        found.sort(key=lambda x: (-x[1], -x[0].similarity, x[0].id))
        items = [it for it, _ in found]
        return bucket_sort(items, lambda x: x.similarity, lambda x: x.heat, lambda x: x.id, self.config.recall.epsilon)[:MAX_EXPANSION]

    # ------------------------------------------------------------------ 항목

    def _item(self, row: MemoryRow, sim: float, path: str, now: datetime) -> RecallItem:
        try:
            heat_at = datetime.fromisoformat(row.heat_at)
            heat = decayed_heat(row.heat, heat_at, now, self.config.heat)
        except ValueError:
            heat = row.heat
        return RecallItem(
            id=row.id, title=row.title, tags=self.db.tags_of(row.id), type=row.type, source=row.source,
            fact_key=row.fact_key, status=row.status, similarity=sim, path=path, heat=heat, created=row.created,
        )

    def _attach_text(self, items: list[RecallItem]) -> None:
        from ..naming import find_note_by_id

        for i, it in enumerate(items):
            p = find_note_by_id(self.paths, it.id)
            if p is None:
                it.preview, it.truncated_preview = "", False
                continue
            try:
                note = parse_note(p.read_text(encoding="utf-8"))
            except (NoteFormatError, OSError):
                it.preview, it.truncated_preview = "(노트를 읽을 수 없다)", False
                continue
            body = note.body
            if note.amendments:
                body += "\n\n" + "\n".join(f"- {a.date}: {a.text}" for a in note.amendments)
            if i < FULL_TEXT_TOP:
                it.text = body
            else:
                it.preview = body[:PREVIEW_CHARS]
                it.truncated_preview = len(body) > PREVIEW_CHARS
