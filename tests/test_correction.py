"""T02: 정정과 fact_key 유일성. T03: amend, archive, unarchive."""

import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from memory_server.errors import MemoryError
from memory_server.events import iter_events
from memory_server.notes import parse_note
from memory_server.store import MemoryStore

KST = timezone(timedelta(hours=9))


class Clock:
    def __init__(self, start):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, **kw):
        self.now += timedelta(**kw)


@pytest.fixture
def clock():
    return Clock(datetime(2026, 9, 18, 15, 30, 0, tzinfo=KST))


@pytest.fixture
def store(initialized, clock):
    return MemoryStore(initialized, now=clock)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


def _path(store, note_id) -> Path:
    return next(store.paths.memories.rglob(f"* {note_id}.md"))


def _text(store, note_id) -> str:
    return _path(store, note_id).read_text(encoding="utf-8")


def _events(store):
    return list(iter_events(store.paths.events_file(2026, 9)))


def _err(fn, *a, **k) -> MemoryError:
    with pytest.raises(MemoryError) as ei:
        fn(*a, **k)
    return ei.value


# ----------------------------------------------------------------------------- T02


def test_t02_supersede_fact(store, clock):
    a = store.remember("이전 근무 회사는 A 회사", "사용자는 A 회사에 다닌다고 말했다.", type="fact", fact_key="self/employer", tags=["주제/직장"])
    a_before = _text(store, a.id)
    clock.advance(days=1)
    commits_before = int(_git(store.paths.vault, "rev-list", "--count", "HEAD"))

    b = store.remember(
        "현재 근무 회사는 B 회사", "사용자는 현재 B 회사에 다닌다고 말했다.",
        type="fact", fact_key="self/employer", tags=["주제/직장"], supersedes=[a.id], reason="이직",
    )
    assert b.saved and b.committed and b.warnings == []

    # A: 본문은 그대로, status 와 superseded_by 두 줄만 바뀜
    a_after = _text(store, a.id)
    assert a_after == a_before.replace("status: active\n", "status: superseded\n").replace("superseded_by: null\n", f"superseded_by: {b.id}\n")
    a_note = parse_note(a_after)
    assert a_note.body == "사용자는 A 회사에 다닌다고 말했다."
    assert a_note.frontmatter.status == "superseded" and a_note.frontmatter.superseded_by == b.id

    # B: supersedes 와 `## 관련` 의 대체 링크
    b_note = parse_note(_text(store, b.id))
    assert b_note.frontmatter.supersedes == [a.id]
    assert b_note.related == [f"- 대체함: [[이전 근무 회사는 A 회사 {a.id}|이전 근무 회사는 A 회사]]"]

    # 기본 회상(active 만) 에 A 없음: fact_key 로 찾으면 B 만 나온다
    assert store.find_active_by_fact_key("self/employer") == [b.id]

    # 이벤트: created 하나에 supersedes 가 실린다. 한 커밋에 새 노트, 옛 노트, 이벤트가 함께 들어간다.
    ev = _events(store)[-1]
    assert ev.type == "created" and ev.ids == [b.id] and ev.data["supersedes"] == [a.id] and ev.data["reason"] == "이직"
    assert int(_git(store.paths.vault, "rev-list", "--count", "HEAD")) == commits_before + 1
    assert _git(store.paths.vault, "status", "--porcelain") == ""
    changed = set(_git(store.paths.vault, "show", "--name-only", "--format=", "HEAD").splitlines())
    assert changed == {
        f"memories/2026/09/이전 근무 회사는 A 회사 {a.id}.md",
        f"memories/2026/09/현재 근무 회사는 B 회사 {b.id}.md",
        "_system/events/events-2026-09.jsonl",
    }

    # read_memory 로 대체 관계가 보인다
    assert store.read_memory(a.id)["superseded_by"] == b.id
    assert store.read_memory(b.id)["supersedes"] == [a.id]


def test_t02_fact_conflict_without_supersedes(store, clock):
    a = store.remember("A 회사", "A 회사에 다닌다.", type="fact", fact_key="self/employer")
    clock.advance(minutes=11)
    e = _err(store.remember, "B 회사", "B 회사에 다닌다.", type="fact", fact_key="self/employer")
    assert e.code == "FACT_CONFLICT" and e.retryable is True
    assert e.details["fact_key"] == "self/employer"
    assert e.details["existing"]["id"] == a.id
    assert e.details["existing"]["title"] == "A 회사"
    assert e.details["existing"]["content"] == _text(store, a.id)  # 기존 노트 전문
    assert a.id in e.message
    # 거부됐으니 파일, 이벤트 모두 그대로
    assert len(list(store.paths.memories.rglob("*.md"))) == 1
    assert len(_events(store)) == 1

    # 오류가 알려준 ID 로 한 번 재시도하면 정정이 끝난다
    b = store.remember("B 회사", "B 회사에 다닌다.", type="fact", fact_key="self/employer", supersedes=[a.id], reason="이직")
    assert store.find_active_by_fact_key("self/employer") == [b.id]


def test_fact_conflict_dict_carries_existing(store):
    a = store.remember("A", "b", type="fact", fact_key="k/x")
    e = _err(store.remember, "B", "c", type="fact", fact_key="k/x")
    d = e.to_dict()["error"]
    assert d["code"] == "FACT_CONFLICT" and d["existing"]["id"] == a.id and "content" in d["existing"]


def test_cannot_supersede_already_superseded(store, clock):
    a = store.remember("v1", "b1", type="fact", fact_key="k/x")
    clock.advance(minutes=1)
    b = store.remember("v2", "b2", type="fact", fact_key="k/x", supersedes=[a.id], reason="r")
    clock.advance(minutes=1)
    e = _err(store.remember, "v3", "b3", type="fact", fact_key="k/x", supersedes=[a.id], reason="r")
    assert e.code == "INVALID_SUPERSEDES" and e.details["superseded_by"] == b.id
    # 최신 노트를 대상으로 하면 된다
    c = store.remember("v3", "b3", type="fact", fact_key="k/x", supersedes=[b.id], reason="r")
    assert store.find_active_by_fact_key("k/x") == [c.id]
    assert parse_note(_text(store, b.id)).frontmatter.superseded_by == c.id


def test_supersede_multiple_and_episode(store, clock):
    a = store.remember("결정 1", "b")
    b = store.remember("결정 2", "b2")
    clock.advance(minutes=1)
    c = store.remember("합친 결정", "둘을 합침", supersedes=[a.id, b.id], reason="통합")
    for old in (a, b):
        fm = parse_note(_text(store, old.id)).frontmatter
        assert fm.status == "superseded" and fm.superseded_by == c.id
    note = parse_note(_text(store, c.id))
    assert len(note.related) == 2 and all(line.startswith("- 대체함: [[") for line in note.related)


def test_supersede_archived_note_is_allowed(store, clock):
    a = store.remember("A", "b", type="fact", fact_key="k/x")
    store.archive(a.id, "잠시 제외")
    clock.advance(minutes=1)
    b = store.remember("B", "b", type="fact", fact_key="k/x", supersedes=[a.id], reason="정정")
    fm = parse_note(_text(store, a.id)).frontmatter
    assert fm.status == "superseded" and fm.superseded_by == b.id


def test_archived_fact_key_can_be_reused(store, clock):
    a = store.remember("A", "b", type="fact", fact_key="k/x")
    store.archive(a.id, "제외")
    clock.advance(minutes=1)
    b = store.remember("B", "b2", type="fact", fact_key="k/x")  # supersedes 없이 허용
    assert store.find_active_by_fact_key("k/x") == [b.id]
    e = _err(store.unarchive, a.id, "되살림")  # 이제 되살리면 충돌
    assert e.code == "FACT_CONFLICT" and e.details["existing"]["id"] == b.id


def test_fact_conflict_check_happens_before_writing(store, clock):
    store.remember("A", "b", type="fact", fact_key="k/x")
    clock.advance(minutes=1)
    before = sorted(p.name for p in store.paths.memories.rglob("*"))
    _err(store.remember, "B", "b2", type="fact", fact_key="k/x")
    assert sorted(p.name for p in store.paths.memories.rglob("*")) == before


# ----------------------------------------------------------------------------- T03


def test_t03_amend_only_appends(store, clock):
    a = store.remember("보충 대상", "원래 본문", tags=["주제/테스트"])
    before = _text(store, a.id)
    clock.advance(days=2)
    r = store.amend(a.id, "입사일은 9월 1일.")
    assert r.saved and r.committed and r.indexed is False
    assert r.extra["line"] == "- 2026-09-20: 입사일은 9월 1일."
    after = _text(store, a.id)
    assert after == before + "\n## 덧붙임\n- 2026-09-20: 입사일은 9월 1일.\n"
    note = parse_note(after)
    assert note.body == "원래 본문" and note.title == "보충 대상"
    assert [(x.date, x.text) for x in note.amendments] == [("2026-09-20", "입사일은 9월 1일.")]

    clock.advance(days=1)
    store.amend(a.id, "둘째 보충")
    after2 = _text(store, a.id)
    assert after2 == after + "- 2026-09-21: 둘째 보충\n"
    rm = store.read_memory(a.id)
    assert rm["body"] == "원래 본문"
    assert [x["text"] for x in rm["amendments"]] == ["입사일은 9월 1일.", "둘째 보충"]
    ev = [e for e in _events(store) if e.type == "amended"]
    assert len(ev) == 2 and ev[0].ids == [a.id] and "note" not in ev[0].data


def test_amend_dedupe_within_window(store, clock):
    a = store.remember("d", "b")
    store.amend(a.id, "같은 보충")
    clock.advance(minutes=5)
    r = store.amend(a.id, "같은  보충")
    assert any(w.startswith("DUPLICATE_INPUT") for w in r.warnings)
    assert len(parse_note(_text(store, a.id)).amendments) == 1
    clock.advance(minutes=6)
    store.amend(a.id, "같은 보충")
    assert len(parse_note(_text(store, a.id)).amendments) == 2


def test_amend_validation(store):
    a = store.remember("v", "b")
    assert _err(store.amend, a.id, "").code == "INVALID_INPUT"
    assert _err(store.amend, a.id, "x" * 1001).code == "INVALID_INPUT"
    assert _err(store.amend, "20200101-000000-0000", "x").code == "NOT_FOUND"
    r = store.amend(a.id, "여러\n줄은\n한 줄로")
    assert r.extra["line"].endswith("여러 줄은 한 줄로")


def test_t03_archive_and_unarchive(store, clock):
    a = store.remember("보관 대상", "본문")
    before = _text(store, a.id)
    r = store.archive(a.id, "당분간 필요 없음")
    assert r.extra == {"status": "archived", "no_change": False} and r.committed
    after = _text(store, a.id)
    assert after == before.replace("status: active\n", "status: archived\n")
    assert store.read_memory(a.id)["status"] == "archived"  # 보관 여부와 상관없이 읽힌다

    # 이미 보관 상태면 no_change, 이벤트도 없음
    n_events = len(_events(store))
    r2 = store.archive(a.id, "다시")
    assert r2.extra["no_change"] is True and len(_events(store)) == n_events

    # 보관된 노트는 amend 거부
    assert _err(store.amend, a.id, "x").code == "NOT_ACTIVE"

    # 수동 보관은 복구된다
    r3 = store.unarchive(a.id, "다시 필요")
    assert r3.extra == {"status": "active", "no_change": False}
    assert _text(store, a.id) == before
    assert store.unarchive(a.id, "또").extra["no_change"] is True

    types = [e.type for e in _events(store)]
    assert types == ["created", "archived", "unarchived"]
    assert _events(store)[1].data == {"reason": "당분간 필요 없음"}


def test_t03_superseded_note_cannot_be_unarchived_or_archived(store, clock):
    a = store.remember("v1", "b", type="fact", fact_key="k/x")
    clock.advance(minutes=1)
    b = store.remember("v2", "b", type="fact", fact_key="k/x", supersedes=[a.id], reason="r")
    e = _err(store.unarchive, a.id, "되살리기")
    assert e.code == "SUPERSEDED" and e.details["superseded_by"] == b.id
    assert _err(store.archive, a.id, "보관").code == "NOT_ACTIVE"
    assert _err(store.amend, a.id, "보충").code == "NOT_ACTIVE"
    assert parse_note(_text(store, a.id)).frontmatter.status == "superseded"


def test_reason_required(store):
    a = store.remember("r", "b")
    assert _err(store.archive, a.id, "").code == "INVALID_INPUT"
    assert _err(store.unarchive, a.id, "  ").code == "INVALID_INPUT"


def test_git_history_keeps_every_version(store, clock):
    a = store.remember("이력", "본문")
    clock.advance(minutes=1)
    store.amend(a.id, "보충")
    store.archive(a.id, "보관")
    rel = f"memories/2026/09/이력 {a.id}.md"
    log = _git(store.paths.vault, "log", "--format=%s", "--", rel).splitlines()
    assert log == [f"archive: {a.id}", f"amend: {a.id}", f"remember: {a.id}"]
