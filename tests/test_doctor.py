"""T04: 강제 종료 복구, 외부 삭제·수정, 잘린 이벤트 로그, 비밀 형식, 잘못된 출처, .git 없음."""

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from memory_server.doctor import load_last_report, run_doctor
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


class Crash(BaseException):
    """강제 종료 흉내. Exception 이 아니라 BaseException 이라 어떤 except 에도 잡히지 않는다."""


def crash_at(point: str):
    def hook(name: str) -> None:
        if name == point:
            raise Crash(name)

    return hook


@pytest.fixture
def clock():
    return Clock(datetime(2026, 9, 18, 15, 30, 0, tzinfo=KST))


@pytest.fixture
def store(initialized, clock):
    return MemoryStore(initialized, now=clock)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


def _notes(paths):
    return sorted(p for p in paths.memories.rglob("*.md") if not p.name.startswith("."))


def _events(paths):
    out = []
    for f in sorted(paths.events_dir.glob("events-*.jsonl")):
        out += list(iter_events(f))
    return out


def _head_files(paths) -> set[str]:
    return set(_git(paths.vault, "ls-files").splitlines())


def _clean(paths) -> bool:
    return _git(paths.vault, "status", "--porcelain") == ""


# ----------------------------------------------------------------------------- 강제 종료


def test_t04_crash_before_event(initialized, clock):
    s = MemoryStore(initialized, now=clock, crash_hook=crash_at("before_event"))
    with pytest.raises(Crash):
        s.remember("죽기 전", "본문", tags=["주제/테스트"])
    # 노트는 있고 이벤트와 커밋은 없다
    assert len(_notes(initialized)) == 1
    assert _events(initialized) == []
    assert not _clean(initialized)

    # 재시작: doctor 가 recovered 이벤트를 남기고 커밋한다
    clock.advance(seconds=30)
    report = run_doctor(initialized, now=clock)
    note_id = parse_note(_notes(initialized)[0].read_text(encoding="utf-8")).id
    assert report.recovered == [note_id]
    assert report.commit and _clean(initialized)
    evs = _events(initialized)
    assert [e.type for e in evs] == ["recovered"] and evs[0].ids == [note_id]
    assert "input_hash" in evs[0].data

    # 모델이 같은 입력으로 다시 시도해도 중복이 생기지 않는다
    s2 = MemoryStore(initialized, now=clock)
    r = s2.remember("죽기 전", "본문", tags=["주제/테스트"])
    assert r.id == note_id
    assert len(_notes(initialized)) == 1
    assert len(_events(initialized)) == 1

    # 두 번째 doctor 는 할 일이 없다
    report2 = run_doctor(initialized, now=clock)
    assert report2.recovered == [] and report2.commit is None


def test_t04_crash_before_index(initialized, clock):
    s = MemoryStore(initialized, now=clock, crash_hook=crash_at("before_index"))
    with pytest.raises(Crash):
        s.remember("이벤트 뒤", "본문")
    assert len(_notes(initialized)) == 1
    assert [e.type for e in _events(initialized)] == ["created"]
    assert not _clean(initialized)

    clock.advance(seconds=30)
    report = run_doctor(initialized, now=clock)
    note_id = _events(initialized)[0].ids[0]
    assert report.recovered == [] and report.external_edits == []
    assert report.commit and _clean(initialized)
    assert f"memories/2026/09/이벤트 뒤 {note_id}.md" in _head_files(initialized)
    # 이벤트가 이미 있으므로 새 이벤트 없음, 재시도는 기존 ID
    assert len(_events(initialized)) == 1
    assert MemoryStore(initialized, now=clock).remember("이벤트 뒤", "본문").id == note_id
    assert len(_notes(initialized)) == 1


def test_crash_between_new_note_and_supersede(initialized, clock):
    s = MemoryStore(initialized, now=clock)
    a = s.remember("v1", "b", type="fact", fact_key="k/x")
    clock.advance(minutes=1)
    s_crash = MemoryStore(initialized, now=clock, crash_hook=crash_at("before_supersede"))
    with pytest.raises(Crash):
        s_crash.remember("v2", "b2", type="fact", fact_key="k/x", supersedes=[a.id], reason="r")
    # 새 노트는 있고 옛 노트는 아직 active: 같은 키의 active 가 둘
    assert len(s.find_active_by_fact_key("k/x")) == 2

    report = run_doctor(initialized, now=clock)
    assert len(report.supersede_fixes) == 1 and report.supersede_fixes[0]["old"] == a.id
    assert len(report.recovered) == 1
    assert s.find_active_by_fact_key("k/x") == [report.supersede_fixes[0]["new"]]
    assert parse_note(next(initialized.memories.rglob(f"* {a.id}.md")).read_text(encoding="utf-8")).frontmatter.superseded_by == report.recovered[0]
    assert _clean(initialized)


def test_crash_in_amend_before_event(initialized, clock):
    s = MemoryStore(initialized, now=clock)
    a = s.remember("보충", "본문")
    clock.advance(minutes=1)
    s_crash = MemoryStore(initialized, now=clock, crash_hook=crash_at("before_event"))
    with pytest.raises(Crash):
        s_crash.amend(a.id, "덧붙임")
    assert not _clean(initialized)
    report = run_doctor(initialized, now=clock)
    # 허용된 변경(덧붙임 추가)이라 외부 수정이 아니라 미커밋 서버 변경으로 본다
    assert report.external_edits == [] and report.commit
    assert _clean(initialized)
    assert [e.type for e in _events(initialized)] == ["created"]
    assert len(parse_note(next(initialized.memories.rglob(f"* {a.id}.md")).read_text(encoding="utf-8")).amendments) == 1


# ----------------------------------------------------------------------------- 외부 삭제


def test_t04_external_delete_is_restored_not_committed(store, initialized, clock):
    a = store.remember("지워질 노트", "본문")
    path = next(initialized.memories.rglob(f"* {a.id}.md"))
    original = path.read_text(encoding="utf-8")
    head_before = _git(initialized.vault, "rev-parse", "HEAD")

    path.unlink()
    assert not path.exists()
    clock.advance(minutes=1)
    report = run_doctor(initialized, now=clock)

    assert path.exists() and path.read_text(encoding="utf-8") == original
    assert report.restored == [f"memories/2026/09/지워질 노트 {a.id}.md"]
    evs = _events(initialized)
    assert evs[-1].type == "restored" and evs[-1].ids == [a.id]
    # 삭제는 커밋되지 않았다: HEAD 이후 커밋들의 변경 파일에 노트가 없고, 노트는 여전히 추적된다
    assert f"memories/2026/09/지워질 노트 {a.id}.md" in _head_files(initialized)
    changed_since = _git(initialized.vault, "diff", "--name-status", head_before, "HEAD")
    assert "지워질 노트" not in changed_since
    assert _clean(initialized)
    assert store.read_memory(a.id)["title"] == "지워질 노트"


def test_system_file_delete_is_restored(store, initialized, clock):
    store.remember("x", "b")
    cfg = initialized.config_toml.read_text(encoding="utf-8")
    initialized.config_toml.unlink()
    initialized.tag_rules_yaml.unlink()
    report = run_doctor(initialized, now=clock)
    assert initialized.config_toml.read_text(encoding="utf-8") == cfg
    assert initialized.tag_rules_yaml.exists()
    assert set(report.restored) == {"_system/config.toml", "_system/tag_rules.yaml"}
    assert _clean(initialized)


def test_delete_whole_month_directory(store, initialized, clock):
    ids = [store.remember(f"n{i}", "b").id for i in range(3)]
    shutil.rmtree(initialized.memories / "2026")
    report = run_doctor(initialized, now=clock)
    assert len(report.restored) == 3
    assert {parse_note(p.read_text(encoding="utf-8")).id for p in _notes(initialized)} == set(ids)


# ----------------------------------------------------------------------------- 외부 수정


def test_t04_external_body_edit(store, initialized, clock):
    a = store.remember("옵시디언 편집", "원래 본문", tags=["주제/테스트"])
    path = next(initialized.memories.rglob(f"* {a.id}.md"))
    edited = path.read_text(encoding="utf-8").replace("원래 본문", "사람이 고친 본문\n\n새 단락")
    path.write_text(edited, encoding="utf-8")
    clock.advance(minutes=1)

    report = run_doctor(initialized, now=clock)
    assert report.external_edits == [a.id]
    assert path.read_text(encoding="utf-8") == edited  # 되돌리지 않는다
    evs = _events(initialized)
    assert evs[-1].type == "external_edit" and evs[-1].ids == [a.id]
    assert "본문" not in json.dumps(evs[-1].data, ensure_ascii=False)
    assert _clean(initialized)
    assert _git(initialized.vault, "show", f"HEAD:memories/2026/09/옵시디언 편집 {a.id}.md") == edited.rstrip("\n")
    assert store.read_memory(a.id)["body"] == "사람이 고친 본문\n\n새 단락"


def test_external_edit_that_breaks_frontmatter_is_committed_and_flagged(store, initialized, clock):
    a = store.remember("깨뜨림", "본문")
    path = next(initialized.memories.rglob(f"* {a.id}.md"))
    path.write_text("---\nid: [\n---\n# 깨뜨림\n본문\n", encoding="utf-8")
    report = run_doctor(initialized, now=clock)
    assert report.broken_notes and report.broken_notes[0]["path"].endswith(f"{a.id}.md")
    assert path.read_text(encoding="utf-8") == "---\nid: [\n---\n# 깨뜨림\n본문\n"  # 자동으로 고치지 않는다
    assert report.external_edits == [a.id]
    assert _clean(initialized)
    # stats/get_core 용으로 마지막 보고에 남는다
    last = load_last_report(initialized)
    assert last and last["broken_notes"]


def test_config_edit_is_committed_without_event(store, initialized, clock):
    initialized.config_toml.write_text("[recall]\nmin_similarity = 0.55\n", encoding="utf-8")
    report = run_doctor(initialized, now=clock)
    assert "_system/config.toml" in report.committed_paths
    assert _events(initialized) == [] and _clean(initialized)


def test_externally_created_note_is_recovered(store, initialized, clock):
    d = initialized.memories / "2026" / "09"
    d.mkdir(parents=True, exist_ok=True)
    nid = "20260918-120000-beef"
    (d / f"손으로 만든 노트 {nid}.md").write_text(
        "---\nid: 20260918-120000-beef\ncreated: \"2026-09-18T12:00:00+09:00\"\ntype: episode\nsource: user\n"
        "fact_key: null\ntags: []\nlinks: []\nsupersedes: []\nstatus: active\nsuperseded_by: null\n---\n# 손으로 만든 노트\n\n본문\n",
        encoding="utf-8",
    )
    report = run_doctor(initialized, now=clock)
    assert report.recovered == [nid]
    assert _clean(initialized) and store.read_memory(nid)["title"] == "손으로 만든 노트"


# ----------------------------------------------------------------------------- 이벤트 로그


def test_truncated_event_log_tail_moved_to_recovery(store, initialized, clock):
    a = store.remember("로그", "본문")
    f = initialized.events_file(2026, 9)
    good = f.read_bytes()
    with open(f, "ab") as fh:
        fh.write('{"ts":"2026-09-18T15:31:00+09:00","type":"used","ids":["2026'.encode("utf-8"))
    report = run_doctor(initialized, now=clock)
    assert report.truncated_logs == ["_system/events/events-2026-09.jsonl"]
    frags = list(initialized.recovery.glob("events-2026-09.jsonl.*.fragment"))
    assert len(frags) == 1 and frags[0].read_bytes().startswith(b'{"ts":"2026-09-18T15:31:00+09:00"')
    assert f.read_bytes() == good  # 온전한 줄까지만 남고, doctor 자신은 이벤트를 더하지 않았다
    assert [e.ids for e in _events(initialized)] == [[a.id]]
    assert _clean(initialized)


def test_iter_events_skips_garbage_middle_line(store, initialized):
    a = store.remember("중간", "본문")
    f = initialized.events_file(2026, 9)
    with open(f, "a", encoding="utf-8") as fh:
        fh.write("not json\n")
    b = store.remember("다음", "본문")
    assert [e.ids[0] for e in _events(initialized)] == [a.id, b.id]


# ----------------------------------------------------------------------------- 입력 방어


def test_t04_secret_formats_rejected(store, initialized):
    cases = [
        dict(title="API 키", body="sk-abcdefghijklmnopqrstuvwxyz123456"),
        dict(title="AWS", body="access key AKIAIOSFODNN7EXAMPLE"),
        dict(title="키", body="-----BEGIN OPENSSH PRIVATE KEY-----\nabc"),
        dict(title="age", body="AGE-SECRET-KEY-1QYQSZQGPQYQSZQGPQYQSZQGPQYQSZQGPQYQSZQGPQYQSZQGPQYQS3PTVJA"),
        dict(title="ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef0123456789", body="제목에 토큰"),
    ]
    for kw in cases:
        with pytest.raises(MemoryError) as ei:
            store.remember(**kw)
        assert ei.value.code == "SECRET_DETECTED" and ei.value.retryable is False
        # 값은 오류에 실리지 않는다
        assert "AKIA" not in ei.value.message and "sk-" not in ei.value.message
    assert _notes(initialized) == [] and _events(initialized) == []

    a = store.remember("정상", "본문")
    with pytest.raises(MemoryError) as ei:
        store.amend(a.id, "토큰은 xoxb-1234567890-abcdefghijklmnop")
    assert ei.value.code == "SECRET_DETECTED"
    assert store.read_memory(a.id)["amendments"] == []


def test_t04_inference_fact_rejected(store, initialized):
    with pytest.raises(MemoryError) as ei:
        store.remember("추측", "아마 B 회사일 것", type="fact", source="inference", fact_key="self/employer")
    assert ei.value.code == "INVALID_SOURCE"
    with pytest.raises(MemoryError):
        store.remember("추측", "아마", type="episode", source="inference")
    assert _notes(initialized) == []
    r = store.remember("추측", "아마 B 회사일 것", type="reflection", source="inference")
    assert r.saved


# ----------------------------------------------------------------------------- .git 없음


def test_missing_git_stops_writes(store, initialized, clock):
    a = store.remember("있던 노트", "본문")
    shutil.rmtree(initialized.vault_git)
    report = run_doctor(initialized, now=clock)
    assert report.fatal and report.fatal.startswith("VAULT_NOT_GIT")
    assert "RESTORE.md" in report.fatal

    s = MemoryStore(initialized, now=clock)
    for fn, args in (
        (s.remember, ("새 노트", "본문")),
        (s.amend, (a.id, "보충")),
        (s.archive, (a.id, "이유")),
        (s.unarchive, (a.id, "이유")),
    ):
        with pytest.raises(MemoryError) as ei:
            fn(*args)
        assert ei.value.code == "VAULT_NOT_GIT"
    assert len(_notes(initialized)) == 1 and len(_events(initialized)) == 1
    assert s.read_memory(a.id)["title"] == "있던 노트"  # 읽기는 된다


# ----------------------------------------------------------------------------- CLI 와 서버 시작


def test_cli_doctor(store, initialized, capsys):
    from memory_server.cli import main

    a = store.remember("cli", "본문")
    next(initialized.memories.rglob(f"* {a.id}.md")).unlink()
    assert main(["doctor"]) == 0
    out, err = capsys.readouterr()
    assert out == ""
    assert "복원: memories/2026/09/cli" in err
    assert _clean(initialized)


def test_cli_doctor_fatal_exit_code(store, initialized, capsys):
    from memory_server.cli import main

    shutil.rmtree(initialized.vault_git)
    assert main(["doctor"]) == 1
    assert "VAULT_NOT_GIT" in capsys.readouterr().err


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_server_startup_runs_light_doctor(store, initialized):
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    a = store.remember("서버 시작 복원", "본문")
    path = next(initialized.memories.rglob(f"* {a.id}.md"))
    path.unlink()
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "memory_server.cli", "serve"],
        env={**os.environ, "MEMORY_HOME": str(initialized.home)},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            res = await session.call_tool("read_memory", {"id": a.id})
            assert (res.structured_content or {}).get("title") == "서버 시작 복원"
    assert path.exists()
    last = load_last_report(initialized)
    assert last["mode"] == "light" and last["restored"]
    assert "startup doctor 완료" in initialized.server_log.read_text(encoding="utf-8")
