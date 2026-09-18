"""T06: core (SPEC 9)."""

import json

import pytest

from memory_server.app import build_app
from memory_server.core import MAX_CHARS, CoreService, load_core, total_chars
from memory_server.errors import MemoryError
from tests.helpers import Clock


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def app(initialized, clock):
    a = build_app(initialized, now=clock)
    yield a
    a.close()


@pytest.fixture
def core(app):
    return CoreService(app.paths, now=app.now)


def _err(fn, *a, **k):
    with pytest.raises(MemoryError) as ei:
        fn(*a, **k)
    return ei.value


def test_update_and_get_core(app, core):
    f = app.store.remember("현재 근무 회사는 B 회사", "B 회사에 다닌다", type="fact", fact_key="self/employer")
    e = app.store.remember("기억 시스템 시작", "9월에 시작했다")
    r = core.update_core(
        [
            {"section": "나에 대해", "text": "B 회사에 다닌다.", "sources": [f.id]},
            {"section": "진행 중인 일", "text": "기억 시스템을 만들고 있다.", "sources": [e.id, f.id]},
        ],
        reason="첫 작성",
    )
    assert r["saved"] and r["committed"] and r["entries"] == 2
    got = core.get_core()
    assert [x["text"] for x in got["entries"]] == ["B 회사에 다닌다.", "기억 시스템을 만들고 있다."]
    assert got["review"] == [] and got["updated"]
    # core.json 이 원본, core.md 는 생성물
    doc = json.loads(app.paths.core_json.read_text(encoding="utf-8"))
    assert doc["entries"][0]["sources"] == [f.id] and doc["reason"] == "첫 작성"
    md = app.paths.core_md.read_text(encoding="utf-8")
    assert md.startswith("# 핵심 요약") and "## 나에 대해" in md and "B 회사에 다닌다." in md and f.id in md
    # 커밋됨, 이벤트 남음
    import subprocess

    assert subprocess.run(["git", "-C", str(app.paths.vault), "status", "--porcelain"], capture_output=True, text=True).stdout == ""
    from memory_server.events import iter_events

    evs = [x for f_ in app.paths.events_dir.glob("*.jsonl") for x in iter_events(f_) if x.type == "core_updated"]
    assert len(evs) == 1 and set(evs[0].ids) == {f.id, e.id}


def test_t06_rejections(app, core):
    f = app.store.remember("사실", "b", type="fact", fact_key="k/x")
    refl = app.store.remember("해석", "추측", type="reflection", source="inference")
    # 근거 없는 항목
    e = _err(core.update_core, [{"section": "나에 대해", "text": "x", "sources": []}], "r")
    assert e.code == "INVALID_INPUT" and e.details["field"] == "sources"
    e = _err(core.update_core, [{"section": "나에 대해", "text": "x", "sources": ["20200101-000000-0000"]}], "r")
    assert e.code == "INVALID_SOURCE" and e.details["state"] == "missing"
    # reflection 근거
    e = _err(core.update_core, [{"section": "나에 대해", "text": "x", "sources": [refl.id]}], "r")
    assert e.code == "INVALID_SOURCE" and e.details["state"] == "invalid_type"
    # 1,500자 초과
    big = [{"section": "나에 대해", "text": "가" * 800, "sources": [f.id]}, {"section": "선호와 규칙", "text": "나" * 800, "sources": [f.id]}]
    e = _err(core.update_core, big, "r")
    assert e.code == "INVALID_INPUT" and e.details["limit"] == MAX_CHARS
    # 항목 수, 최근 맥락 수, 절 이름, reason
    e = _err(core.update_core, [{"section": "나에 대해", "text": "x", "sources": [f.id]}] * 13, "r")
    assert e.details["limit"] == 12
    e = _err(core.update_core, [{"section": "최근 맥락", "text": "x", "sources": [f.id]}] * 4, "r")
    assert e.details["limit"] == 3
    assert _err(core.update_core, [{"section": "기타", "text": "x", "sources": [f.id]}], "r").details["field"] == "section"
    assert _err(core.update_core, [{"section": "나에 대해", "text": "x", "sources": [f.id]}], "").details["field"] == "reason"
    assert _err(core.update_core, [{"section": "나에 대해", "text": "x", "sources": [f.id] * 4}], "r").details["field"] == "sources"
    # 아무것도 저장되지 않았다
    assert load_core(app.paths).entries == []


def test_t06_invalidated_source_is_hidden_and_reviewable(app, core, clock):
    a = app.store.remember("A 회사", "A 회사에 다닌다", type="fact", fact_key="self/employer")
    ep = app.store.remember("이사", "서울로 이사했다")
    keep = app.store.remember("취미", "등산을 좋아한다", type="fact", fact_key="self/hobby")
    core.update_core(
        [
            {"section": "나에 대해", "text": "A 회사에 다닌다.", "sources": [a.id]},
            {"section": "최근 맥락", "text": "서울로 이사했다.", "sources": [ep.id]},
            {"section": "선호와 규칙", "text": "등산을 좋아한다.", "sources": [keep.id]},
        ],
        reason="r",
    )
    clock.advance(minutes=1)
    b = app.store.remember("B 회사", "B 회사에 다닌다", type="fact", fact_key="self/employer", supersedes=[a.id], reason="이직")
    app.store.archive(ep.id, "옛날 일")
    got = core.get_core()
    assert [x["text"] for x in got["entries"]] == ["등산을 좋아한다."]
    assert [(x["index"], x["section"]) for x in got["review"]] == [(0, "나에 대해"), (1, "최근 맥락")]
    assert b.id in got["review"][0]["reasons"][0] and "대체" in got["review"][0]["reasons"][0]
    assert "보관" in got["review"][1]["reasons"][0]
    # 검토 필요 항목의 문장은 어디에도 없다
    assert "A 회사에 다닌다." not in json.dumps(got, ensure_ascii=False)
    # 새 근거로 고치면 다시 유효
    core.update_core([{"section": "나에 대해", "text": "B 회사에 다닌다.", "sources": [b.id]}], reason="정정 반영")
    assert core.get_core()["review"] == []


def test_empty_core_allowed_and_history_kept(app, core):
    f = app.store.remember("사실", "b", type="fact", fact_key="k/x")
    core.update_core([{"section": "나에 대해", "text": "x", "sources": [f.id]}], "r")
    core.update_core([], "비움")
    assert core.get_core()["entries"] == []
    import subprocess

    log = subprocess.run(["git", "-C", str(app.paths.vault), "log", "--format=%s", "--", "_system/core.json"], capture_output=True, text=True).stdout.splitlines()
    assert log.count("core: update") == 2


def test_total_chars_counts_title_sections_and_texts():
    from memory_server.core import CoreEntry

    entries = [CoreEntry("나에 대해", "가나다", ["x"]), CoreEntry("나에 대해", "라마", ["x"]), CoreEntry("최근 맥락", "바", ["x"])]
    assert total_chars(entries) == len("핵심 요약") + len("나에 대해") + len("최근 맥락") + 3 + 2 + 1


def test_get_core_includes_warnings(app):
    from memory_server.stats import collect_warnings

    w = collect_warnings(app)
    assert any(x.startswith("BACKUP_NOT_CONFIGURED") for x in w)
