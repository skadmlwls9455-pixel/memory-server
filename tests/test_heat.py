"""T12 의 열 감쇠·가열·6시간 재신고, rebuild 재생 (SPEC 7.3)."""

from datetime import timedelta

import pytest

from memory_server.app import build_app
from memory_server.config import HeatConfig
from memory_server.events import iter_events
from memory_server.recall.heat import decayed_heat, reheat
from tests.helpers import T0, Clock


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def app(initialized, clock):
    a = build_app(initialized, now=clock)
    yield a
    a.close()


def test_t12_decay_and_reheat_math():
    cfg = HeatConfig()
    later = T0 + timedelta(days=30)
    assert decayed_heat(0.5, T0, later, cfg) == pytest.approx(0.25)
    assert reheat(0.5, T0, later, cfg) == pytest.approx(0.55)
    assert decayed_heat(0.5, T0, T0, cfg) == pytest.approx(0.5)
    assert reheat(1.0, T0, T0, cfg) == 1.0  # 상한
    assert decayed_heat(0.5, T0, T0 + timedelta(days=60), cfg) == pytest.approx(0.125)


def test_t12_mark_used_flow(app, clock):
    a = app.store.remember("자주 쓰는 기억", "본문")
    assert app.db.get(a.id).heat == 0.5
    clock.advance(days=30)
    r = app.store.mark_used([a.id])
    assert r["heated"] == [a.id] and r["committed"]
    row = app.db.get(a.id)
    assert row.heat == pytest.approx(0.55) and row.used_at == clock().isoformat(timespec="seconds")
    used = [e for f in app.paths.events_dir.glob("*.jsonl") for e in iter_events(f) if e.type == "used"]
    assert len(used) == 1 and used[0].ids == [a.id]

    # 6시간 안 재신고: 무시, 이벤트 없음
    clock.advance(hours=5, minutes=59)
    r2 = app.store.mark_used([a.id])
    assert r2["heated"] == [] and r2["skipped"] == {a.id: "RECENTLY_USED"}
    used = [e for f in app.paths.events_dir.glob("*.jsonl") for e in iter_events(f) if e.type == "used"]
    assert len(used) == 1
    assert app.db.get(a.id).heat == pytest.approx(0.55)

    # 6시간 뒤에는 다시 가열
    clock.advance(minutes=2)
    r3 = app.store.mark_used([a.id])
    assert r3["heated"] == [a.id]
    assert app.db.get(a.id).heat > 0.55


def test_mark_used_skips_inactive_and_unknown(app):
    a = app.store.remember("보관", "b")
    b = app.store.remember("대체 전", "b", type="fact", fact_key="k/x")
    app.store.archive(a.id, "r")
    c = app.store.remember("대체 후", "b", type="fact", fact_key="k/x", supersedes=[b.id], reason="r")
    r = app.store.mark_used([a.id, b.id, c.id, "20200101-000000-0000"])
    assert r["heated"] == [c.id]
    assert r["skipped"] == {a.id: "NOT_ACTIVE", b.id: "NOT_ACTIVE", "20200101-000000-0000": "NOT_FOUND"}
    # 읽거나 보충한 것만으로는 데워지지 않는다
    app.store.read_memory(c.id)
    app.store.amend(c.id, "보충")
    assert app.db.get(c.id).used_at == app.now().isoformat(timespec="seconds")
    assert len([e for f in app.paths.events_dir.glob("*.jsonl") for e in iter_events(f) if e.type == "used"]) == 1


def test_mark_used_validation(app):
    from memory_server.errors import MemoryError

    for bad in ([], [""], "id", ["x"] * 21):
        with pytest.raises(MemoryError):
            app.store.mark_used(bad)  # type: ignore[arg-type]


def test_rebuild_replays_used_events_to_same_heat(app, clock):
    from memory_server.rebuild import rebuild

    a = app.store.remember("재생", "본문")
    b = app.store.remember("재생 둘", "본문 둘")
    clock.advance(days=10)
    app.store.mark_used([a.id])
    clock.advance(days=20)
    app.store.mark_used([a.id, b.id])
    before = {r.id: (round(r.heat, 6), r.heat_at, r.used_at) for r in app.db.iter_rows()}
    app.close()
    report = rebuild(app.paths, app.config, app.embedder, now=clock)
    assert report.used_events == 2 and report.indexed == 2 and report.replaced
    app2 = build_app(app.paths, now=clock)
    try:
        after = {r.id: (round(r.heat, 6), r.heat_at, r.used_at) for r in app2.db.iter_rows()}
        assert after == before
    finally:
        app2.close()
