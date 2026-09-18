"""색인기: 노트 파일 → DB 행 + 벡터 + 태그 + 링크 (SPEC 3.5, 5).

임베딩 대상은 제목 + 본문 + 덧붙임이다. frontmatter 와 `## 관련` 은 뺀다.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from ..config import Config
from ..logging_setup import get_logger
from ..notes import Note, NoteFormatError, parse_note
from ..paths import MemoryPaths
from .db import IndexDB, IndexMeta, MemoryRow
from .embedder import Embedder, EmbeddingUnavailable


class IndexError_(RuntimeError):
    pass


@dataclass
class IndexResult:
    id: str
    indexed: bool
    warnings: list[str] = field(default_factory=list)
    similar: list[tuple[str, float]] = field(default_factory=list)  # SIMILAR_EXISTS 후보 (id, 유사도)


def file_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def embedding_text(note: Note) -> str:
    parts = [note.title, note.body]
    parts += [a.text for a in note.amendments]
    return "\n".join(p for p in parts if p)


def _links_of(note: Note) -> list[tuple[str, str]]:
    out = [(dst, "related") for dst in note.frontmatter.links]
    out += [(dst, "supersedes") for dst in note.frontmatter.supersedes]
    return out


class Indexer:
    def __init__(
        self,
        paths: MemoryPaths,
        config: Config,
        db: IndexDB,
        embedder: Embedder,
        now: Callable[[], datetime],
    ) -> None:
        self.paths = paths
        self.config = config
        self.db = db
        self.embedder = embedder
        self.now = now
        self.log = get_logger()

    # ------------------------------------------------------------------ 메타

    def meta_matches(self) -> bool:
        """index_meta 가 설정의 모델과 같은지. 비어 있으면 참(첫 색인 때 채운다)."""
        meta = self.db.get_meta()
        if meta is None:
            return True
        return meta.model_id == self.config.embedding.model_id and meta.dimension == self.config.embedding.dimension

    def _ensure_meta(self) -> None:
        if self.db.get_meta() is None:
            self.db.set_meta(IndexMeta(self.embedder.model_id, self.embedder.revision, self.embedder.dimension))

    # ------------------------------------------------------------------ 색인

    def index_path(self, path: Path, *, check_similar: bool = False, keep_heat: bool = True) -> IndexResult:
        """노트 하나를 색인한다. 임베딩 실패는 EmbeddingUnavailable 로 올린다."""
        text = path.read_text(encoding="utf-8")
        try:
            note = parse_note(text)
        except NoteFormatError as e:
            raise IndexError_(f"노트 형식이 깨져 색인할 수 없다: {e}") from e
        if not self.meta_matches():
            raise IndexError_("index_meta 가 설정의 모델과 다르다. rebuild 가 필요하다.")

        result = self.embedder.embed_documents([embedding_text(note)])
        vec = result.vectors[0]
        warnings: list[str] = []
        if result.truncated[0]:
            warnings.append("TRUNCATED: 노트가 모델 최대 길이를 넘어 뒷부분은 색인되지 않았다.")
        self._ensure_meta()

        similar: list[tuple[str, float]] = []
        if check_similar and note.frontmatter.status == "active":
            threshold = self.config.recall.similar_exists_threshold
            for other_id, dist in self.db.knn(vec, 5):
                if other_id == note.id:
                    continue
                sim = 1.0 - dist
                row = self.db.get(other_id)
                if row is not None and row.status == "active" and sim >= threshold:
                    similar.append((other_id, round(sim, 4)))

        fm = note.frontmatter
        created = fm.created
        row = MemoryRow(
            id=note.id,
            path=str(path.relative_to(self.paths.vault)),
            title=note.title,
            type=fm.type,
            source=fm.source,
            fact_key=fm.fact_key,
            created=created,
            status=fm.status,
            superseded_by=fm.superseded_by,
            file_hash=file_hash(text),
            heat=self.config.heat.initial,
            heat_at=created,
            used_at=None,
        )
        self.db.upsert(row, vec, fm.tags, _links_of(note), keep_heat=keep_heat)
        return IndexResult(id=note.id, indexed=True, warnings=warnings, similar=similar)

    def refresh_status(self, path: Path) -> bool:
        """상태만 바뀐 노트(archive, supersede)는 임베딩 없이 행만 갱신한다. 없으면 False."""
        text = path.read_text(encoding="utf-8")
        note = parse_note(text)
        if self.db.get(note.id) is None or not self.db.has_vector(note.id):
            return False
        self.db.update_status(note.id, note.frontmatter.status, note.frontmatter.superseded_by, file_hash(text))
        return True

    # ------------------------------------------------------------------ 대기 목록

    def pending(self) -> list[Path]:
        """DB 에 없거나 파일 해시가 다른 노트. 깨진 노트는 뺀다."""
        known = self.db.hashes()
        out: list[Path] = []
        if not self.paths.memories.exists():
            return out
        for path in sorted(self.paths.memories.rglob("*.md")):
            if path.name.startswith("."):
                continue
            try:
                text = path.read_text(encoding="utf-8")
                note = parse_note(text)
            except (NoteFormatError, OSError, UnicodeDecodeError):
                continue
            if known.get(note.id) != file_hash(text) or not self.db.has_vector(note.id):
                out.append(path)
        return out

    def stale_rows(self) -> list[str]:
        """DB 에는 있는데 파일이 없는 ID. doctor 가 파일을 먼저 복원하므로 보통 비어 있다."""
        from ..naming import find_note_by_id

        return [r.id for r in self.db.iter_rows() if find_note_by_id(self.paths, r.id) is None]

    def index_pending(self, limit: int | None = None) -> tuple[list[str], list[str]]:
        """대기 노트를 색인한다. (성공 ID, 실패 ID)."""
        ok: list[str] = []
        failed: list[str] = []
        for path in self.pending()[: limit or None]:
            try:
                r = self.index_path(path)
                ok.append(r.id)
            except (EmbeddingUnavailable, IndexError_, OSError) as e:
                self.log.warning("index failed type=%s", type(e).__name__)
                failed.append(path.stem.rsplit(" ", 1)[-1])
                if isinstance(e, EmbeddingUnavailable):
                    break  # 모델이 없으면 나머지도 실패한다
        return ok, failed
