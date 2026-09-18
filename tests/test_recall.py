"""recall (SPEC 7.1, 7.2) 와 T12 의 확장·묶음 정렬 부분. 가짜 임베더 기준의 구조 검증이다. 품질은 T05 스크립트."""

import pytest

from memory_server.app import build_app
from memory_server.errors import MemoryError
from memory_server.recall.ranking import bucket_sort
from tests.helpers import Clock


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def app(initialized, clock):
    a = build_app(initialized, now=clock)
    yield a
    a.close()


def _ids(res):
    return [r.id for r in res.results]


def test_recall_validation(app):
    for kw in (dict(query=""), dict(query="x" * 501), dict(query="q", k=0), dict(query="q", k=11), dict(query="q", type="memo")):
        with pytest.raises(MemoryError) as ei:
            app.recaller.recall(**kw)
        assert ei.value.code == "INVALID_INPUT"


def test_recall_empty_vs_results(app):
    res = app.recaller.recall("아무것도 없다")
    assert res.results == [] and res.to_dict()["empty"] is True
    a = app.store.remember("현재 근무 회사는 B 회사", "사용자는 현재 B 회사에 다닌다고 말했다.", tags=["주제/직장"])
    res = app.recaller.recall("현재 근무 회사")
    assert _ids(res) == [a.id]
    item = res.results[0]
    assert item.path == "vector" and item.text is not None and item.preview is None
    assert 0 < item.similarity <= 1 and item.heat == pytest.approx(0.5)


def test_recall_top3_full_text_rest_preview(app):
    ids = [app.store.remember(f"회의 메모 {i}", ("회의 메모 본문 " * 40) + f"번호 {i}").id for i in range(6)]
    res = app.recaller.recall("회의 메모", k=6)
    assert len(res.results) == 6
    for i, it in enumerate(res.results):
        if i < 3:
            assert it.text is not None and len(it.text) > 200
        else:
            assert it.text is None and len(it.preview) == 200 and it.truncated_preview is True
    assert set(_ids(res)) == set(ids)


def test_recall_filters_status_tags_type(app):
    a = app.store.remember("직장 이야기", "직장 이야기 본문", tags=["주제/직장", "사람/김철수"], type="fact", fact_key="k/a")
    b = app.store.remember("직장 이야기 둘", "직장 이야기 본문 둘", tags=["주제/직장"])
    c = app.store.remember("직장 이야기 셋", "직장 이야기 본문 셋", tags=["주제/직장"])
    app.store.archive(c.id, "r")
    res = app.recaller.recall("직장 이야기")
    assert c.id not in _ids(res)
    res = app.recaller.recall("직장 이야기", include_inactive=True)
    assert c.id in _ids(res) and next(r for r in res.results if r.id == c.id).status == "archived"
    res = app.recaller.recall("직장 이야기", tags=["주제/직장", "사람/김철수"])
    assert _ids(res) == [a.id]
    res = app.recaller.recall("직장 이야기", type="fact")
    assert _ids(res) == [a.id]
    assert b.id in _ids(app.recaller.recall("직장 이야기", type="episode"))


def test_recall_k_and_min_similarity(app):
    for i in range(8):
        app.store.remember(f"운동 기록 {i}", f"오늘 달리기 {i}km")
    assert len(app.recaller.recall("달리기 기록", k=3).results) == 3
    app.config = app.config.__class__(**{**app.config.__dict__, "recall": app.config.recall.__class__(**{**app.config.recall.__dict__, "min_similarity": 0.999})})
    app.recaller.config = app.config
    assert app.recaller.recall("달리기 기록").results == []


def test_bucket_sort_rules():
    items = [("a", 0.90, 0.1), ("b", 0.89, 0.9), ("c", 0.85, 1.0), ("d", 0.845, 0.2), ("e", 0.70, 1.0)]
    out = bucket_sort(items, lambda x: x[1], lambda x: x[2], lambda x: x[0], epsilon=0.02)
    assert [x[0] for x in out] == ["b", "a", "c", "d", "e"]
    # 묶음 안 동률: 유사도 내림차순, ID 오름차순
    tie = [("z", 0.5, 0.5), ("y", 0.5, 0.5), ("x", 0.49, 0.5)]
    assert [x[0] for x in bucket_sort(tie, lambda x: x[1], lambda x: x[2], lambda x: x[0], 0.02)] == ["y", "z", "x"]
    # 낮은 묶음이 높은 묶음을 넘지 않는다
    far = [("hot", 0.60, 1.0), ("cold", 0.90, 0.0)]
    assert [x[0] for x in bucket_sort(far, lambda x: x[1], lambda x: x[2], lambda x: x[0], 0.02)] == ["cold", "hot"]


def test_t12_heat_reorders_only_within_bucket(app, clock):
    # 태그는 임베딩에 들어가지 않으므로 같은 제목·본문에 태그만 다르면 유사도가 같다 (같은 묶음)
    a = app.store.remember("독서 모임 기록", "독서 모임에서 소설을 읽었다", tags=["주제/독서"])
    b = app.store.remember("독서 모임 기록", "독서 모임에서 소설을 읽었다", tags=["주제/모임"])
    res = app.recaller.recall("독서 모임 기록")
    sims = {r.id: r.similarity for r in res.results}
    assert abs(sims[a.id] - sims[b.id]) <= 0.02  # 같은 묶음
    first = _ids(res)[0]
    second = b.id if first == a.id else a.id
    app.store.mark_used([second])
    assert _ids(app.recaller.recall("독서 모임 기록"))[0] == second


def test_t12_link_expansion_respects_active_and_threshold(app):
    a = app.store.remember("기억 시스템 설계 결정", "벡터 검색과 열 점수를 쓴다", tags=["프로젝트/기억시스템"])
    linked = app.store.remember("기억 시스템 설계 결정 보충", "벡터 검색과 열 점수를 쓴다. 백업도 한다", links=[a.id])
    archived = app.store.remember("기억 시스템 설계 결정 옛것", "벡터 검색과 열 점수를 쓴다 (옛 결정)", links=[a.id])
    app.store.archive(archived.id, "r")
    unrelated = app.store.remember("고양이 사료 브랜드", "고양이가 사료를 잘 먹는다", links=[a.id])
    res = app.recaller.recall("기억 시스템 설계 결정", k=1)
    paths = {r.id: r.path for r in res.results}
    assert sorted(paths[x] for x in (a.id, linked.id)) == ["link", "vector"]
    assert archived.id not in paths  # 보관 노트가 링크 경로로 새지 않는다
    # 관련 없는 링크 노트는 min_similarity 미만이면 빠진다 (가짜 임베더 특성상 유사도가 낮다)
    if unrelated.id in paths:
        assert next(r for r in res.results if r.id == unrelated.id).similarity >= app.config.recall.min_similarity


def test_t12_tag_expansion_only_topic_person_project(app):
    a = app.store.remember("북한산 등산 계획", "주말에 북한산 등산 계획", tags=["주제/등산", "상태/진행중", "시기/2026-09"])
    by_topic = app.store.remember("북한산 등산 계획 장비", "주말 북한산 등산 계획에 필요한 등산화", tags=["주제/등산"])
    by_status_only = app.store.remember("북한산 등산 계획 무관 진행 건", "주말 북한산 등산 계획과 상관없는 다른 일 진행", tags=["상태/진행중", "시기/2026-09"])
    archived = app.store.remember("북한산 등산 계획 옛 기록", "작년 주말 북한산 등산 계획", tags=["주제/등산"])
    app.store.archive(archived.id, "r")
    res = app.recaller.recall("주말 북한산 등산 계획", k=1)
    paths = {r.id: r.path for r in res.results}
    assert len([p for p in paths.values() if p == "vector"]) == 1
    assert paths.get(by_topic.id) in ("tag", "vector") and set(paths) >= {a.id, by_topic.id} or paths.get(by_topic.id) == "tag"
    assert by_status_only.id not in paths  # 상태·시기 태그만으로는 확장하지 않는다
    assert archived.id not in paths
    assert [r.path for r in res.results] == sorted([r.path for r in res.results], key=["vector", "link", "tag"].index)


def test_no_expansion_without_vector_results(app):
    app.store.remember("등산", "등산", tags=["주제/등산"])
    app.recaller.config = app.config.__class__(**{**app.config.__dict__, "recall": app.config.recall.__class__(**{**app.config.recall.__dict__, "min_similarity": 0.999})})
    assert app.recaller.recall("등산").results == []


def test_recall_reports_pending_index(initialized, clock):
    from memory_server.index import FakeEmbedder

    bad = build_app(initialized, now=clock, embedder=FakeEmbedder(dimension=64, fail=True))
    bad.store.remember("미색인", "본문")
    bad.close()
    good = build_app(initialized, now=clock)
    try:
        res = good.recaller.recall("아무거나")
        assert res.pending_index == 1 and any(w.startswith("INDEX_PENDING") for w in res.warnings)
    finally:
        good.close()


def test_search_unavailable_is_distinct_from_empty(initialized, clock):
    from memory_server.index import FakeEmbedder

    app = build_app(initialized, now=clock, embedder=FakeEmbedder(dimension=64, fail=True))
    try:
        with pytest.raises(MemoryError) as ei:
            app.recaller.recall("x")
        assert ei.value.code == "SEARCH_UNAVAILABLE" and ei.value.retryable is True
    finally:
        app.close()
