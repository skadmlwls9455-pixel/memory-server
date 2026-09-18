"""T07: db 삭제 뒤 rebuild. stats (SPEC 8.4)."""

import shutil

import pytest

from memory_server.app import build_app
from memory_server.core import CoreService
from memory_server.rebuild import rebuild
from memory_server.stats import collect_stats
from tests.helpers import Clock


@pytest.fixture
def clock():
    return Clock()


def _snapshot(app, queries):
    out = {}
    for q in queries:
        res = app.recaller.recall(q, k=10)
        out[q] = [(r.id, r.path, round(r.similarity, 6), round(r.heat, 6)) for r in res.results]
    rows = {r.id: (r.status, r.superseded_by, round(r.heat, 6), r.heat_at, r.used_at, r.fact_key) for r in app.db.iter_rows()}
    core = CoreService(app.paths, now=app.now).get_core()
    return out, rows, core


def test_t07_rebuild_reproduces_state(initialized, clock):
    app = build_app(initialized, now=clock)
    a = app.store.remember("현재 근무 회사는 A 회사", "A 회사", type="fact", fact_key="self/employer", tags=["주제/직장"])
    b = app.store.remember("독서 모임", "소설을 읽었다", tags=["주제/독서"])
    c = app.store.remember("등산 계획", "북한산", tags=["주제/등산"], links=[b.id])
    clock.advance(minutes=1)
    d = app.store.remember("현재 근무 회사는 B 회사", "B 회사", type="fact", fact_key="self/employer", supersedes=[a.id], reason="이직", tags=["주제/직장"])
    app.store.archive(c.id, "r")
    app.store.amend(b.id, "다음 책은 시집")
    clock.advance(days=3)
    app.store.mark_used([b.id, d.id])
    clock.advance(hours=7)
    app.store.mark_used([d.id])
    CoreService(app.paths, now=app.now).update_core([{"section": "나에 대해", "text": "B 회사에 다닌다.", "sources": [d.id]}], "r")
    queries = ["현재 근무 회사", "독서 모임", "등산 계획", "아무 관계 없는 질의"]
    before = _snapshot(app, queries)
    app.close()

    shutil.rmtree(initialized.db_dir)
    assert not initialized.db_file.exists()
    report = rebuild(initialized, app.config, app.embedder, now=clock)
    assert report.replaced and report.indexed == 4 and report.used_events == 2 and report.skipped == []
    assert initialized.db_file.exists()

    app2 = build_app(initialized, now=clock)
    try:
        after = _snapshot(app2, queries)
    finally:
        app2.close()
    assert after == before


def test_rebuild_skips_broken_note_and_verifies(initialized, clock):
    app = build_app(initialized, now=clock)
    a = app.store.remember("정상", "b")
    b = app.store.remember("깨짐", "b")
    app.close()
    p = next(initialized.memories.rglob(f"* {b.id}.md"))
    p.write_text("---\nid: [\n---\n# x\nb\n", encoding="utf-8")
    report = rebuild(initialized, app.config, app.embedder, now=clock)
    assert report.indexed == 1 and len(report.skipped) == 1
    app2 = build_app(initialized, now=clock)
    try:
        assert app2.db.get(a.id) is not None and app2.db.get(b.id) is None
    finally:
        app2.close()


def test_rebuild_failure_leaves_old_db(initialized, clock):
    from memory_server.index import FakeEmbedder
    from memory_server.rebuild import RebuildError

    app = build_app(initialized, now=clock)
    app.store.remember("x", "b")
    app.close()
    size = initialized.db_file.stat().st_size
    with pytest.raises(RebuildError):
        rebuild(initialized, app.config, FakeEmbedder(dimension=64, fail=True), now=clock)
    assert initialized.db_file.stat().st_size == size
    assert not list(initialized.db_dir.glob("*.rebuild-*"))


def test_stats_shape(initialized, clock):
    app = build_app(initialized, now=clock)
    try:
        a = app.store.remember("x", "b", type="fact", fact_key="k/x")
        b = app.store.remember("y", "b")
        app.store.archive(b.id, "r")
        CoreService(app.paths, now=app.now).update_core([{"section": "나에 대해", "text": "x", "sources": [a.id]}], "r")
        s = collect_stats(app)
    finally:
        app.close()
    assert s["notes"] == {"active": 1, "archived": 1, "superseded": 0}
    assert s["core"] == {"valid": 1, "review": 0}
    assert s["index"]["pending"] == 0 and s["index"]["vectors"] == 2 and s["index"]["model"] == "fake/ngram-hash"
    assert s["relevance_calibrated"] is True and s["min_similarity"] == 0.45
    assert s["doctor"] == "미실행"
    b_ = s["backup"]
    assert b_["configured"] is False and b_["last_verified_captured_at"] == "미설정" and b_["last_restore_drill"] == "미설정"
    assert b_["remote_usage_bytes"] == "미설정" and b_["events_since_last_verified"] >= 3
    assert any(w.startswith("BACKUP_NOT_CONFIGURED") for w in s["warnings"])
