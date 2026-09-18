"""DB 캐시, 가짜 임베더, 색인기, 색인 대기, 쓰기 시 색인."""

import pytest

from memory_server.app import build_app
from memory_server.index import FakeEmbedder, open_db
from memory_server.index.db import IndexMeta, load_sqlite_vec
from memory_server.index.embedder import with_prefix
from memory_server.index.indexer import embedding_text
from memory_server.notes import parse_note
from tests.helpers import Clock


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def app(initialized, clock):
    a = build_app(initialized, now=clock)
    yield a
    a.close()


def test_sqlite_vec_loads_and_cosine_knn(tmp_path):
    db = open_db(tmp_path / "t.db", 4)
    assert db.vec_version.startswith("v")
    from memory_server.index.db import MemoryRow

    def row(i):
        return MemoryRow(i, f"p{i}", i, "episode", "user", None, "2026-09-18T00:00:00+09:00", "active", None, "h", 0.5, "2026-09-18T00:00:00+09:00", None)

    db.upsert(row("20260918-000000-0001"), [1, 0, 0, 0], [], [])
    db.upsert(row("20260918-000000-0002"), [0.9, 0.1, 0, 0], [], [])
    db.upsert(row("20260918-000000-0003"), [0, 1, 0, 0], [], [])
    res = db.knn([1, 0, 0, 0], 3)
    assert [r[0] for r in res] == ["20260918-000000-0001", "20260918-000000-0002", "20260918-000000-0003"]
    assert res[0][1] == pytest.approx(0.0, abs=1e-6)  # 코사인 거리
    assert 1 - res[1][1] == pytest.approx(0.9 / (0.9**2 + 0.1**2) ** 0.5, abs=1e-5)
    db.close()


def test_fake_embedder_is_deterministic_and_similar_for_similar_text():
    e = FakeEmbedder(dimension=64)
    a = e.embed_query("현재 근무 회사는 B 회사")
    b = e.embed_query("현재 근무 회사는 B 회사")
    c = e.embed_query("현재 근무 회사는 B 회사입니다")
    d = e.embed_query("오늘 점심 김치찌개")
    dot = lambda x, y: sum(p * q for p, q in zip(x, y))  # noqa: E731
    assert a == b
    assert dot(a, c) > dot(a, d)


def test_e5_prefixes_and_bge_none():
    assert with_prefix("intfloat/multilingual-e5-small", "q", "query") == "query: q"
    assert with_prefix("intfloat/multilingual-e5-small", "d", "passage") == "passage: d"
    assert with_prefix("BAAI/bge-m3", "q", "query") == "q"


def test_embedding_text_excludes_frontmatter_and_related(app):
    a = app.store.remember("대상", "본문 A")
    b = app.store.remember("제목", "본문 B", links=[a.id], tags=["주제/직장"])
    app.store.amend(b.id, "덧붙임 하나")
    note = parse_note(next(app.paths.memories.rglob(f"* {b.id}.md")).read_text(encoding="utf-8"))
    text = embedding_text(note)
    assert text == "제목\n본문 B\n덧붙임 하나"
    assert "관련" not in text and "fact_key" not in text and a.id not in text


def test_remember_indexes_and_sets_meta(app):
    r = app.store.remember("색인", "본문", tags=["주제/테스트"])
    assert r.indexed is True
    row = app.db.get(r.id)
    assert row is not None and row.status == "active" and row.title == "색인" and row.heat == 0.5
    assert app.db.has_vector(r.id) and app.db.tags_of(r.id) == ["주제/테스트"]
    meta = app.db.get_meta()
    assert meta.model_id == "fake/ngram-hash" and meta.dimension == 64
    assert app.pending_index_count() == 0


def test_status_changes_update_row_without_reembedding(app):
    a = app.store.remember("v1", "b", type="fact", fact_key="k/x")
    vec = app.db.vector(a.id)
    app.store.archive(a.id, "r")
    assert app.db.get(a.id).status == "archived" and app.db.vector(a.id) == vec
    app.store.unarchive(a.id, "r")
    assert app.db.get(a.id).status == "active"
    b = app.store.remember("v2", "b2", type="fact", fact_key="k/x", supersedes=[a.id], reason="r")
    row = app.db.get(a.id)
    assert row.status == "superseded" and row.superseded_by == b.id
    assert app.db.neighbors(b.id) == [a.id] and app.db.neighbors(a.id) == [b.id]


def test_amend_reindexes_and_keeps_heat(app):
    a = app.store.remember("보충", "본문")
    app.db.set_heat(a.id, 0.9, "2026-09-18T15:30:00+09:00", None)
    old_hash = app.db.get(a.id).file_hash
    r = app.store.amend(a.id, "새 정보")
    assert r.indexed is True
    row = app.db.get(a.id)
    assert row.file_hash != old_hash and row.heat == 0.9


def test_index_failure_keeps_note_saved(initialized, clock):
    app = build_app(initialized, now=clock, embedder=FakeEmbedder(dimension=64, fail=True))
    try:
        r = app.store.remember("실패", "본문")
        assert r.saved and r.committed and r.indexed is False
        assert any(w.startswith("INDEX_FAILED") for w in r.warnings)
        assert app.pending_index_count() == 1
    finally:
        app.close()
    # 모델이 살아나면 doctor 가 색인한다
    app2 = build_app(initialized, now=clock)
    try:
        from memory_server.doctor import indexer_from_app, run_doctor

        idx, pend = indexer_from_app(app2, light=False)
        report = run_doctor(initialized, indexer=idx, pending_paths=pend, now=clock)
        assert len(report.indexed) == 1 and app2.pending_index_count() == 0
    finally:
        app2.close()


def test_light_doctor_does_not_load_model(app):
    from memory_server.doctor import indexer_from_app

    assert app.embedder.loaded() is False
    assert indexer_from_app(app, light=True) == (None, None)
    app.store.remember("x", "b")  # 모델(가짜)이 올라온다
    assert app.embedder.loaded() is True
    idx, _ = indexer_from_app(app, light=True)
    assert idx is not None


def test_external_edit_is_reindexed_by_doctor(app, clock):
    """T04 의 '수정은 재색인됨'."""
    from memory_server.doctor import indexer_from_app, run_doctor

    a = app.store.remember("옵시디언", "원래 본문")
    old_vec = app.db.vector(a.id)
    path = next(app.paths.memories.rglob(f"* {a.id}.md"))
    path.write_text(path.read_text(encoding="utf-8").replace("원래 본문", "완전히 다른 내용으로 바뀐 본문"), encoding="utf-8")
    assert app.pending_index_count() == 1
    idx, pend = indexer_from_app(app, light=False)
    report = run_doctor(initialized := app.paths, indexer=idx, pending_paths=pend, now=clock)
    assert report.external_edits == [a.id] and report.indexed == [a.id]
    assert app.db.vector(a.id) != old_vec and app.pending_index_count() == 0
    assert app.db.get(a.id).title == "옵시디언"
    del initialized


def test_similar_exists_warning(app):
    app.store.remember("현재 근무 회사는 B 회사", "사용자는 현재 B 회사에 다닌다고 말했다.")
    r = app.store.remember("현재 근무 회사는 B 회사다", "사용자는 현재 B 회사에 다닌다고 말했다!")
    assert any(w.startswith("SIMILAR_EXISTS") for w in r.warnings)
    r2 = app.store.remember("주말 등산 계획", "북한산에 간다")
    assert not any(w.startswith("SIMILAR_EXISTS") for w in r2.warnings)


def test_meta_mismatch_blocks_recall_and_index(app):
    app.store.remember("x", "b")
    app.db.set_meta(IndexMeta("other/model", "r", 64))
    from memory_server.errors import MemoryError

    with pytest.raises(MemoryError) as ei:
        app.recaller.recall("x")
    assert ei.value.code == "REBUILD_REQUIRED"
    r = app.store.remember("y", "b")
    assert r.saved and r.indexed is False


def test_truncated_warning(initialized, clock):
    app = build_app(initialized, now=clock, embedder=FakeEmbedder(dimension=64, max_chars=50))
    try:
        r = app.store.remember("긴 노트", "가" * 200)
        assert any(w.startswith("TRUNCATED") for w in r.warnings) and r.indexed
    finally:
        app.close()
