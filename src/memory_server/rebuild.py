"""`memory rebuild` (SPEC 3.5, T07).

노트의 frontmatter 와 `used` 이벤트만 읽어서 DB 전체를 임시 파일에 다시 만들고, 검증한 뒤 교체한다.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from .config import Config
from .events import iter_events
from .index import Embedder, Indexer, open_db
from .index.embedder import EmbeddingUnavailable
from .index.indexer import IndexError_
from .lock import write_lock
from .logging_setup import get_logger
from .paths import MemoryPaths
from .recall.heat import reheat
from .timeutil import now_local


class RebuildError(RuntimeError):
    pass


@dataclass
class RebuildReport:
    indexed: int = 0
    skipped: list[str] = field(default_factory=list)  # 깨진 노트 경로
    used_events: int = 0
    replaced: bool = False
    db_path: str = ""


def _used_events(paths: MemoryPaths):
    out = []
    if paths.events_dir.exists():
        for f in sorted(paths.events_dir.glob("events-*.jsonl")):
            out += [ev for ev in iter_events(f) if ev.type == "used"]
    out.sort(key=lambda e: e.ts)
    return out


def rebuild(
    paths: MemoryPaths,
    config: Config,
    embedder: Embedder,
    *,
    now: Callable[[], datetime] = now_local,
    target: Path | None = None,
) -> RebuildReport:
    """target 이 None 이면 MEMORY_HOME/db/memory.db 를 교체한다."""
    log = get_logger()
    target = target if target is not None else paths.db_file
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{target.name}.rebuild-{secrets.token_hex(3)}")
    report = RebuildReport(db_path=str(target))

    with write_lock(paths.lock_file, timeout=None):
        db = open_db(tmp, embedder.dimension)
        try:
            _build(paths, config, embedder, now, db, report)
        except BaseException:
            db.close()
            _cleanup(tmp)
            raise
        db.close()

        # 교체: 캐시 파일이라 덮어써도 된다. WAL 부속 파일도 정리한다.
        for suffix in ("-wal", "-shm"):
            side = Path(str(target) + suffix)
            if side.exists():
                side.unlink()
        os.replace(tmp, target)
        _cleanup(tmp, keep_main=True)
        report.replaced = True
    log.info("rebuild indexed=%d skipped=%d used=%d", report.indexed, len(report.skipped), report.used_events)
    return report


def _cleanup(tmp: Path, keep_main: bool = False) -> None:
    for suffix in ("", "-wal", "-shm"):
        if keep_main and suffix == "":
            continue
        side = Path(str(tmp) + suffix)
        if side.exists():
            side.unlink()


def _build(paths: MemoryPaths, config: Config, embedder: Embedder, now, db, report: RebuildReport) -> None:
    if True:
        if True:
            indexer = Indexer(paths, config, db, embedder, now)
            note_paths = sorted(p for p in paths.memories.rglob("*.md") if not p.name.startswith(".")) if paths.memories.exists() else []
            for p in note_paths:
                try:
                    indexer.index_path(p, keep_heat=False)
                    report.indexed += 1
                except IndexError_:
                    report.skipped.append(str(p.relative_to(paths.vault)))
                except EmbeddingUnavailable as e:
                    raise RebuildError(f"임베딩을 쓸 수 없어 rebuild 를 중단한다: {e}") from e

            # used 이벤트 재생 (6시간 안 재신고는 애초에 기록되지 않았다)
            for ev in _used_events(paths):
                ts = datetime.fromisoformat(ev.ts)
                for nid in ev.ids:
                    row = db.get(nid)
                    if row is None:
                        continue
                    heat_at = datetime.fromisoformat(row.heat_at)
                    new_heat = reheat(row.heat, heat_at, ts, config.heat)
                    db.set_heat(nid, new_heat, ev.ts, ev.ts)
                report.used_events += 1

            # 검증
            rows = db.counts_by_status()
            total = sum(rows.values())
            if total != report.indexed:
                raise RebuildError(f"검증 실패: 행 {total} ≠ 색인 {report.indexed}")
            if db.count_vectors() != report.indexed:
                raise RebuildError(f"검증 실패: 벡터 {db.count_vectors()} ≠ 색인 {report.indexed}")
            if report.indexed and db.get_meta() is None:
                raise RebuildError("검증 실패: index_meta 가 비어 있다")
            db.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
