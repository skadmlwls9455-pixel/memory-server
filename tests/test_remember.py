"""T01 과 remember 입력 검증."""

import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from memory_server.errors import MemoryError
from memory_server.events import iter_events
from memory_server.notes import parse_note
from memory_server.store import LIMITS, MemoryStore

KST = timezone(timedelta(hours=9))
T0 = datetime(2026, 9, 18, 15, 30, 0, tzinfo=KST)


class Clock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs) -> None:
        self.now = self.now + timedelta(**kwargs)


@pytest.fixture
def clock() -> Clock:
    return Clock(T0)


@pytest.fixture
def store(initialized, clock) -> MemoryStore:
    return MemoryStore(initialized, now=clock)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


def _notes(paths) -> list[Path]:
    return sorted(paths.memories.rglob("*.md"))


def _events(paths, year=2026, month=9):
    return list(iter_events(paths.events_file(year, month)))


def _commit_count(paths) -> int:
    return int(_git(paths.vault, "rev-list", "--count", "HEAD"))


# ----------------------------------------------------------------------------- T01


def test_t01_single_remember_creates_one_note_event_commit(store, initialized):
    base_commits = _commit_count(initialized)
    r = store.remember("첫 기억", "본문이다.", tags=["주제/테스트"])
    assert r.saved is True and r.indexed is False and r.committed is True and r.warnings == []
    assert r.id.startswith("20260918-153000-")

    notes = _notes(initialized)
    assert len(notes) == 1
    assert notes[0].name == f"첫 기억 {r.id}.md"
    assert notes[0].parent == initialized.memories / "2026" / "09"

    events = _events(initialized)
    assert len(events) == 1
    assert events[0].type == "created" and events[0].ids == [r.id]
    assert events[0].ts == "2026-09-18T15:30:00+09:00"

    assert _commit_count(initialized) == base_commits + 1
    assert _git(initialized.vault, "status", "--porcelain") == ""
    tracked = set(_git(initialized.vault, "ls-files").splitlines())
    assert f"memories/2026/09/첫 기억 {r.id}.md" in tracked
    assert "_system/events/events-2026-09.jsonl" in tracked

    note = parse_note(notes[0].read_text(encoding="utf-8"))
    assert note.title == "첫 기억" and note.body == "본문이다."
    assert note.frontmatter.tags == ["주제/테스트"]
    assert note.frontmatter.status == "active"
    assert note.frontmatter.created == "2026-09-18T15:30:00+09:00"


def test_t01_two_different_inputs_in_same_second_get_different_ids(store, initialized):
    a = store.remember("같은 초 A", "본문 A")
    b = store.remember("같은 초 B", "본문 B")
    assert a.id != b.id
    assert a.id[:15] == b.id[:15] == "20260918-153000"
    notes = _notes(initialized)
    assert len(notes) == 2
    # 덮어쓰기 없음: 두 파일의 내용이 각자 것이다
    contents = {parse_note(p.read_text(encoding="utf-8")).title for p in notes}
    assert contents == {"같은 초 A", "같은 초 B"}
    assert len(_events(initialized)) == 2


def test_t01_same_input_within_10_minutes_returns_existing_id(store, initialized, clock):
    first = store.remember("중복 제목", "중복 본문", tags=["주제/테스트"])
    commits = _commit_count(initialized)
    clock.advance(minutes=9, seconds=59)
    again = store.remember("중복 제목", "중복 본문", tags=["주제/테스트"])
    assert again.id == first.id
    assert again.saved is True
    assert any(w.startswith("DUPLICATE_INPUT") for w in again.warnings)
    assert len(_notes(initialized)) == 1
    assert len(_events(initialized)) == 1
    assert _commit_count(initialized) == commits


def test_dedupe_is_on_normalized_input(store, initialized, clock):
    import unicodedata

    first = store.remember("  제목   공백 ", "본문\n", tags=["주제/테스트", "주제/테스트"])
    clock.advance(minutes=1)
    again = store.remember(unicodedata.normalize("NFD", "제목 공백"), "본문", tags=["주제/테스트"])
    assert again.id == first.id
    assert len(_notes(initialized)) == 1


def test_dedupe_expires_after_window(store, initialized, clock):
    first = store.remember("만료", "본문")
    clock.advance(minutes=10, seconds=1)
    again = store.remember("만료", "본문")
    assert again.id != first.id
    assert len(_notes(initialized)) == 2


def test_dedupe_survives_restart(initialized, clock):
    s1 = MemoryStore(initialized, now=clock)
    first = s1.remember("재시작", "본문")
    clock.advance(minutes=2)
    s2 = MemoryStore(initialized, now=clock)  # 새 프로세스 흉내
    assert s2.remember("재시작", "본문").id == first.id


def test_dedupe_across_month_boundary(initialized):
    clock = Clock(datetime(2026, 9, 30, 23, 58, 0, tzinfo=KST))
    s = MemoryStore(initialized, now=clock)
    first = s.remember("월말", "본문")
    clock.advance(minutes=5)  # 10월 1일 00:03
    assert s.remember("월말", "본문").id == first.id


def test_different_field_is_not_duplicate(store, initialized):
    a = store.remember("같은 제목", "같은 본문", source="user")
    b = store.remember("같은 제목", "같은 본문", source="document")
    assert a.id != b.id


# ----------------------------------------------------------------------------- 파일과 이벤트


def test_no_temp_files_left_and_event_has_hash(store, initialized):
    r = store.remember("임시", "본문")
    leftovers = [p for p in initialized.memories.rglob(".tmp-*")]
    assert leftovers == []
    ev = _events(initialized)[0]
    assert ev.data["type"] == "episode"
    assert len(ev.data["input_hash"]) == 64
    assert "본문" not in json.dumps(ev.data, ensure_ascii=False)  # 이벤트에 본문을 넣지 않는다
    assert r.id in ev.ids


def test_links_render_related_section(store, initialized):
    a = store.remember("대상 노트", "본문 A")
    b = store.remember("연결 노트", "본문 B", links=[a.id])
    note = parse_note((_notes(initialized)[1] if _notes(initialized)[1].name.startswith("연결") else _notes(initialized)[0]).read_text(encoding="utf-8"))
    assert note.frontmatter.links == [a.id]
    assert note.related == [f"- 관련: [[대상 노트 {a.id}|대상 노트]]"]
    assert b.id != a.id


def test_commit_failure_reports_committed_false(store, initialized, monkeypatch):
    from memory_server import gitutil

    def boom(*a, **k):
        raise gitutil.GitError("no git")

    monkeypatch.setattr(gitutil, "commit_paths", boom)
    r = store.remember("커밋 실패", "본문")
    assert r.saved is True and r.committed is False
    assert any(w.startswith("GIT_COMMIT_FAILED") for w in r.warnings)
    assert len(_notes(initialized)) == 1 and len(_events(initialized)) == 1


def test_lock_timeout_returns_locked_error(initialized, clock):
    from memory_server.lock import write_lock

    s = MemoryStore(initialized, now=clock)
    with write_lock(initialized.lock_file, timeout=1):
        s_fast = MemoryStore(initialized, now=clock)
        # 잠금 대기 시간을 줄이기 위해 내부 remember 를 짧은 timeout 으로 감싼다
        import memory_server.store.writes as store_mod

        original = store_mod.write_lock
        store_mod.write_lock = lambda p, timeout=30.0: original(p, timeout=0.2)
        try:
            with pytest.raises(MemoryError) as ei:
                s_fast.remember("잠금", "본문")
        finally:
            store_mod.write_lock = original
    assert ei.value.code == "LOCKED" and ei.value.retryable is True
    del s


# ----------------------------------------------------------------------------- 검증


def _err(store, **kwargs) -> MemoryError:
    with pytest.raises(MemoryError) as ei:
        store.remember(**kwargs)
    return ei.value


def test_limits(store):
    assert _err(store, title="x" * (LIMITS["title"] + 1), body="b").code == "INVALID_INPUT"
    assert _err(store, title="t", body="x" * (LIMITS["body"] + 1)).code == "INVALID_INPUT"
    assert _err(store, title="t", body="b", tags=[f"주제/{i}" for i in range(LIMITS["tags"] + 1)]).code == "INVALID_INPUT"
    assert store.remember(title="x" * LIMITS["title"], body="y" * LIMITS["body"]).saved


def test_links_limit_and_missing(store):
    e = _err(store, title="t", body="b", links=["20260918-000000-0000"])
    assert e.code == "NOT_FOUND" and e.details["id"] == "20260918-000000-0000"
    ids = [store.remember(f"n{i}", "b").id for i in range(LIMITS["links"] + 1)]
    assert _err(store, title="t", body="b", links=ids).code == "INVALID_INPUT"


def test_empty_title_or_body(store):
    assert _err(store, title="   ", body="b").code == "INVALID_INPUT"
    assert _err(store, title="t", body="\n\n").code == "INVALID_INPUT"


def test_type_and_source_rules(store):
    assert _err(store, title="t", body="b", type="memo").code == "INVALID_INPUT"
    assert _err(store, title="t", body="b", source="web").code == "INVALID_INPUT"
    e = _err(store, title="t", body="b", type="fact", source="inference", fact_key="self/x")
    assert e.code == "INVALID_SOURCE" and e.retryable is False
    assert _err(store, title="t", body="b", type="episode", source="inference").code == "INVALID_SOURCE"
    assert _err(store, title="t", body="b", type="reflection", source="user").code == "INVALID_SOURCE"
    assert store.remember("해석", "모델의 추측", type="reflection", source="inference").saved


def test_fact_key_rules(store):
    e = _err(store, title="t", body="b", type="fact")
    assert e.code == "INVALID_INPUT" and e.details["field"] == "fact_key"
    assert _err(store, title="t", body="b", type="episode", fact_key="self/x").code == "INVALID_INPUT"
    assert _err(store, title="t", body="b", type="fact", fact_key="self employer").code == "INVALID_INPUT"
    assert store.remember("직장", "B 회사", type="fact", fact_key="self/employer").saved


def test_tag_rules(store):
    e = _err(store, title="t", body="b", tags=["취미/독서"])
    assert e.code == "INVALID_TAG" and e.details["tag"] == "취미/독서"
    assert _err(store, title="t", body="b", tags=["직장"]).code == "INVALID_TAG"
    r = store.remember("태그", "b", tags=[" 주제/직장 ", "주제/직장", "사람/김철수"])
    assert r.saved
    note = parse_note(next(p for p in store.paths.memories.rglob(f"*{r.id}.md")).read_text(encoding="utf-8"))
    assert note.frontmatter.tags == ["주제/직장", "사람/김철수"]


def test_reserved_headings_in_body_rejected(store):
    assert _err(store, title="t", body="a\n## 덧붙임\nb").code == "INVALID_INPUT"
    assert _err(store, title="t", body="---\nid: x\n---").code == "INVALID_INPUT"


def test_supersedes_requires_reason(store):
    a = store.remember("A", "본문")
    e = _err(store, title="B", body="본문", supersedes=[a.id])
    assert e.code == "INVALID_INPUT" and e.details["field"] == "reason"
    assert _err(store, title="B", body="본문", supersedes=["20200101-000000-0000"], reason="r").code == "NOT_FOUND"


def test_error_dict_shape(store):
    e = _err(store, title="", body="b")
    d = e.to_dict()
    assert set(d) == {"error"}
    assert d["error"]["code"] == "INVALID_INPUT"
    assert isinstance(d["error"]["message"], str) and d["error"]["retryable"] is False


def test_title_with_forbidden_chars_keeps_original_in_note(store, initialized):
    r = store.remember('제목: "따옴표"/슬래시?', "본문")
    path = _notes(initialized)[0]
    assert path.name == f'제목 따옴표슬래시 {r.id}.md'
    assert parse_note(path.read_text(encoding="utf-8")).title == '제목: "따옴표"/슬래시?'
