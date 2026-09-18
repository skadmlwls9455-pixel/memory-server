"""DB 캐시 (SPEC 3.5). 원본은 노트와 이벤트다. 언제든 rebuild 로 다시 만든다.

| 테이블 | 열 |
| memories | id, path, title, type, source, fact_key, created, status, superseded_by, file_hash, heat, heat_at, used_at |
| vec_memories | id, embedding float[N] distance_metric=cosine |
| links | src, dst, kind |
| tags | memory_id, tag |
| index_meta | model_id, model_revision, dimension |

`used_at` 은 SPEC 표에 없지만 6시간 재신고 판정에 필요해 캐시에 둔다 (DECISIONS D-037).
"""

from __future__ import annotations

import sqlite3
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Sequence

import sqlite_vec


class IndexDBError(RuntimeError):
    pass


@dataclass(frozen=True)
class MemoryRow:
    id: str
    path: str
    title: str
    type: str
    source: str
    fact_key: str | None
    created: str
    status: str
    superseded_by: str | None
    file_hash: str
    heat: float
    heat_at: str
    used_at: str | None


@dataclass(frozen=True)
class IndexMeta:
    model_id: str
    model_revision: str
    dimension: int


def serialize_f32(vector: Sequence[float]) -> bytes:
    return struct.pack(f"{len(vector)}f", *vector)


def deserialize_f32(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


def load_sqlite_vec(conn: sqlite3.Connection) -> str:
    """확장을 올리고 버전을 돌려준다. 실패하면 IndexDBError."""
    try:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
    except (AttributeError, sqlite3.OperationalError) as e:
        raise IndexDBError(
            f"sqlite-vec 확장을 올리지 못했다: {e}. 확장 로딩이 막힌 파이썬(macOS 기본 등)이면 uv 가 받은 파이썬을 쓴다."
        ) from e
    return conn.execute("select vec_version()").fetchone()[0]


class IndexDB:
    def __init__(self, conn: sqlite3.Connection, path: Path | None) -> None:
        self.conn = conn
        self.path = path
        self.vec_version = load_sqlite_vec(conn)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")

    # ------------------------------------------------------------------ 스키마

    def init_schema(self, dimension: int) -> None:
        c = self.conn
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                path TEXT NOT NULL,
                title TEXT NOT NULL,
                type TEXT NOT NULL,
                source TEXT NOT NULL,
                fact_key TEXT,
                created TEXT NOT NULL,
                status TEXT NOT NULL,
                superseded_by TEXT,
                file_hash TEXT NOT NULL,
                heat REAL NOT NULL,
                heat_at TEXT NOT NULL,
                used_at TEXT
            );
            CREATE INDEX IF NOT EXISTS memories_status ON memories(status);
            CREATE INDEX IF NOT EXISTS memories_fact_key ON memories(fact_key);
            CREATE TABLE IF NOT EXISTS links (
                src TEXT NOT NULL,
                dst TEXT NOT NULL,
                kind TEXT NOT NULL,
                PRIMARY KEY (src, dst, kind)
            );
            CREATE INDEX IF NOT EXISTS links_dst ON links(dst);
            CREATE TABLE IF NOT EXISTS tags (
                memory_id TEXT NOT NULL,
                tag TEXT NOT NULL,
                PRIMARY KEY (memory_id, tag)
            );
            CREATE INDEX IF NOT EXISTS tags_tag ON tags(tag);
            CREATE TABLE IF NOT EXISTS index_meta (
                model_id TEXT NOT NULL,
                model_revision TEXT NOT NULL,
                dimension INTEGER NOT NULL
            );
            """
        )
        c.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_memories USING vec0("
            f"id TEXT PRIMARY KEY, embedding float[{int(dimension)}] distance_metric=cosine)"
        )
        c.commit()

    def get_meta(self) -> IndexMeta | None:
        row = self.conn.execute("SELECT model_id, model_revision, dimension FROM index_meta LIMIT 1").fetchone()
        return IndexMeta(row["model_id"], row["model_revision"], int(row["dimension"])) if row else None

    def set_meta(self, meta: IndexMeta) -> None:
        self.conn.execute("DELETE FROM index_meta")
        self.conn.execute(
            "INSERT INTO index_meta(model_id, model_revision, dimension) VALUES (?, ?, ?)",
            (meta.model_id, meta.model_revision, meta.dimension),
        )
        self.conn.commit()

    # ------------------------------------------------------------------ 메모리 행

    def get(self, note_id: str) -> MemoryRow | None:
        row = self.conn.execute("SELECT * FROM memories WHERE id = ?", (note_id,)).fetchone()
        return _row(row) if row else None

    def all_rows(self) -> list[MemoryRow]:
        return [_row(r) for r in self.conn.execute("SELECT * FROM memories ORDER BY id")]

    def hashes(self) -> dict[str, str]:
        return {r["id"]: r["file_hash"] for r in self.conn.execute("SELECT id, file_hash FROM memories")}

    def upsert(
        self,
        row: MemoryRow,
        embedding: Sequence[float] | None,
        tags: Iterable[str],
        links: Iterable[tuple[str, str]],
        keep_heat: bool = True,
    ) -> None:
        """행, 벡터, 태그, 링크를 함께 갱신한다. keep_heat 면 기존 열 점수를 유지한다."""
        c = self.conn
        existing = self.get(row.id)
        heat, heat_at, used_at = row.heat, row.heat_at, row.used_at
        if keep_heat and existing is not None:
            heat, heat_at, used_at = existing.heat, existing.heat_at, existing.used_at
        c.execute(
            """
            INSERT INTO memories(id, path, title, type, source, fact_key, created, status, superseded_by,
                                 file_hash, heat, heat_at, used_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                path=excluded.path, title=excluded.title, type=excluded.type, source=excluded.source,
                fact_key=excluded.fact_key, created=excluded.created, status=excluded.status,
                superseded_by=excluded.superseded_by, file_hash=excluded.file_hash,
                heat=excluded.heat, heat_at=excluded.heat_at, used_at=excluded.used_at
            """,
            (row.id, row.path, row.title, row.type, row.source, row.fact_key, row.created, row.status,
             row.superseded_by, row.file_hash, heat, heat_at, used_at),
        )
        if embedding is not None:
            c.execute("DELETE FROM vec_memories WHERE id = ?", (row.id,))
            c.execute("INSERT INTO vec_memories(id, embedding) VALUES (?, ?)", (row.id, serialize_f32(embedding)))
        c.execute("DELETE FROM tags WHERE memory_id = ?", (row.id,))
        c.executemany("INSERT OR IGNORE INTO tags(memory_id, tag) VALUES (?, ?)", [(row.id, t) for t in tags])
        c.execute("DELETE FROM links WHERE src = ?", (row.id,))
        c.executemany("INSERT OR IGNORE INTO links(src, dst, kind) VALUES (?, ?, ?)", [(row.id, d, k) for d, k in links])
        c.commit()

    def update_status(self, note_id: str, status: str, superseded_by: str | None, file_hash: str) -> None:
        self.conn.execute(
            "UPDATE memories SET status = ?, superseded_by = ?, file_hash = ? WHERE id = ?",
            (status, superseded_by, file_hash, note_id),
        )
        self.conn.commit()

    def set_heat(self, note_id: str, heat: float, heat_at: str, used_at: str | None) -> None:
        self.conn.execute(
            "UPDATE memories SET heat = ?, heat_at = ?, used_at = ? WHERE id = ?", (heat, heat_at, used_at, note_id)
        )
        self.conn.commit()

    def remove(self, note_id: str) -> None:
        """파일이 사라진 항목의 캐시만 지운다. 노트 원본은 건드리지 않는다 (doctor 가 먼저 복원한다)."""
        c = self.conn
        c.execute("DELETE FROM vec_memories WHERE id = ?", (note_id,))
        c.execute("DELETE FROM tags WHERE memory_id = ?", (note_id,))
        c.execute("DELETE FROM links WHERE src = ?", (note_id,))
        c.execute("DELETE FROM memories WHERE id = ?", (note_id,))
        c.commit()

    # ------------------------------------------------------------------ 조회

    def has_vector(self, note_id: str) -> bool:
        return self.conn.execute("SELECT 1 FROM vec_memories WHERE id = ?", (note_id,)).fetchone() is not None

    def vector(self, note_id: str) -> list[float] | None:
        row = self.conn.execute("SELECT embedding FROM vec_memories WHERE id = ?", (note_id,)).fetchone()
        return deserialize_f32(row[0]) if row else None

    def knn(self, query: Sequence[float], k: int) -> list[tuple[str, float]]:
        """(id, cosine distance) 를 거리 오름차순으로."""
        rows = self.conn.execute(
            "SELECT id, distance FROM vec_memories WHERE embedding MATCH ? AND k = ? ORDER BY distance",
            (serialize_f32(query), int(k)),
        ).fetchall()
        return [(r["id"], float(r["distance"])) for r in rows]

    def tags_of(self, note_id: str) -> list[str]:
        return [r["tag"] for r in self.conn.execute("SELECT tag FROM tags WHERE memory_id = ? ORDER BY tag", (note_id,))]

    def tags_map(self, ids: Iterable[str]) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for nid in ids:
            out[nid] = self.tags_of(nid)
        return out

    def ids_with_tag(self, tag: str) -> list[str]:
        return [r["memory_id"] for r in self.conn.execute("SELECT memory_id FROM tags WHERE tag = ?", (tag,))]

    def neighbors(self, note_id: str) -> list[str]:
        """위키링크 양방향 1단계 (SPEC 7.2)."""
        rows = self.conn.execute(
            "SELECT dst AS other FROM links WHERE src = ? UNION SELECT src AS other FROM links WHERE dst = ?",
            (note_id, note_id),
        ).fetchall()
        return [r["other"] for r in rows]

    def counts_by_status(self) -> dict[str, int]:
        out = {"active": 0, "archived": 0, "superseded": 0}
        for r in self.conn.execute("SELECT status, COUNT(*) AS n FROM memories GROUP BY status"):
            out[r["status"]] = int(r["n"])
        return out

    def count_vectors(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM vec_memories").fetchone()[0])

    def iter_rows(self) -> Iterator[MemoryRow]:
        for r in self.conn.execute("SELECT * FROM memories ORDER BY id"):
            yield _row(r)

    def close(self) -> None:
        self.conn.close()


def _row(r: sqlite3.Row) -> MemoryRow:
    return MemoryRow(
        id=r["id"], path=r["path"], title=r["title"], type=r["type"], source=r["source"], fact_key=r["fact_key"],
        created=r["created"], status=r["status"], superseded_by=r["superseded_by"], file_hash=r["file_hash"],
        heat=float(r["heat"]), heat_at=r["heat_at"], used_at=r["used_at"],
    )


def open_db(path: Path | None, dimension: int) -> IndexDB:
    """path=None 이면 메모리 DB (테스트, rebuild 검증용)."""
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path) if path is not None else ":memory:", check_same_thread=False)
    db = IndexDB(conn, path)
    db.init_schema(dimension)
    return db
