"""구성 루트. 설정, DB, 임베더, 색인기, 회상기, 저장소를 한 번에 조립한다.

서버, CLI, 테스트가 모두 이 함수로 같은 조립을 얻는다. DB 나 모델이 없어도 저장은 되어야 하므로
DB 열기 실패는 index=None 으로 두고 경고만 남긴다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from .config import Config, TagRules, load_config, load_tag_rules
from .index import Embedder, IndexDB, Indexer, make_embedder, open_db
from .index.db import IndexDBError
from .logging_setup import get_logger
from .paths import MemoryPaths
from .recall import Recaller
from .store import MemoryStore
from .timeutil import now_local


@dataclass
class App:
    paths: MemoryPaths
    config: Config
    tag_rules: TagRules
    db: IndexDB | None
    embedder: Embedder
    indexer: Indexer | None
    recaller: Recaller | None
    store: MemoryStore
    now: Callable[[], datetime]
    db_error: str | None = None

    def pending_index_count(self) -> int:
        return len(self.indexer.pending()) if self.indexer else -1

    def close(self) -> None:
        if self.db is not None:
            self.db.close()


def build_app(
    paths: MemoryPaths,
    *,
    now: Callable[[], datetime] = now_local,
    embedder: Embedder | None = None,
    db_path: Path | None | str = "default",
    crash_hook: Callable[[str], None] | None = None,
) -> App:
    """db_path="default" 면 MEMORY_HOME/db/memory.db, None 이면 메모리 DB."""
    config = load_config(paths.config_toml)
    tag_rules = load_tag_rules(paths.tag_rules_yaml)
    emb = embedder if embedder is not None else make_embedder(config.embedding)
    path = paths.db_file if db_path == "default" else db_path
    db: IndexDB | None = None
    db_error: str | None = None
    try:
        db = open_db(path, emb.dimension)  # type: ignore[arg-type]
    except (IndexDBError, Exception) as e:  # noqa: BLE001
        db_error = f"{type(e).__name__}: {e}"
        get_logger().error("db open failed type=%s", type(e).__name__)
    indexer = Indexer(paths, config, db, emb, now) if db is not None else None
    recaller = (
        Recaller(paths, config, db, emb, now, lambda: len(indexer.pending()), indexer.meta_matches)
        if db is not None and indexer is not None
        else None
    )
    store = MemoryStore(paths, config=config, tag_rules=tag_rules, now=now, crash_hook=crash_hook, indexer=indexer)
    return App(paths, config, tag_rules, db, emb, indexer, recaller, store, now, db_error)
