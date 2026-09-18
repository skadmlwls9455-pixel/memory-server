"""허용된 세 가지 변경 (SPEC 3.3) 만 가능한지 바이트 단위로 확인한다."""

import pytest

from memory_server.mutations import ForbiddenChange, append_amendment, set_status
from memory_server.notes import Frontmatter, Note, parse_note, render_note

FM = Frontmatter(
    id="20260918-153000-a3f2", created="2026-09-18T15:30:00+09:00", type="fact", source="user",
    fact_key="self/employer", tags=["주제/직장"], links=[], supersedes=[],
)


@pytest.fixture
def note_file(tmp_path):
    p = tmp_path / "t 20260918-153000-a3f2.md"
    p.write_text(render_note(Note(frontmatter=FM, title="제목", body="본문 첫 줄\n\n둘째 단락")), encoding="utf-8")
    return p


def test_append_amendment_adds_heading_then_lines(note_file):
    before = note_file.read_text(encoding="utf-8")
    line = append_amendment(note_file, "2026-09-20", "첫 덧붙임")
    after1 = note_file.read_text(encoding="utf-8")
    assert line == "- 2026-09-20: 첫 덧붙임"
    assert after1 == before + "\n## 덧붙임\n- 2026-09-20: 첫 덧붙임\n"
    append_amendment(note_file, "2026-09-21", "둘째")
    after2 = note_file.read_text(encoding="utf-8")
    assert after2 == after1 + "- 2026-09-21: 둘째\n"
    parsed = parse_note(after2)
    assert parsed.body == "본문 첫 줄\n\n둘째 단락"
    assert [(a.date, a.text) for a in parsed.amendments] == [("2026-09-20", "첫 덧붙임"), ("2026-09-21", "둘째")]


def test_append_amendment_handles_missing_trailing_newline(note_file):
    text = note_file.read_text(encoding="utf-8").rstrip("\n")
    note_file.write_text(text, encoding="utf-8")
    append_amendment(note_file, "2026-09-20", "x")
    after = note_file.read_text(encoding="utf-8")
    assert after.startswith(text)
    assert parse_note(after).amendments[0].text == "x"


def test_append_amendment_rejects_multiline(note_file):
    with pytest.raises(ValueError):
        append_amendment(note_file, "2026-09-20", "a\nb")


def test_set_status_changes_only_status_line(note_file):
    before = note_file.read_text(encoding="utf-8")
    set_status(note_file, "archived")
    after = note_file.read_text(encoding="utf-8")
    assert after == before.replace("status: active\n", "status: archived\n")
    assert after.count("\n") == before.count("\n")


def test_set_status_with_superseded_by(note_file):
    before = note_file.read_text(encoding="utf-8")
    set_status(note_file, "superseded", superseded_by="20260919-000000-0001")
    after = note_file.read_text(encoding="utf-8")
    expected = before.replace("status: active\n", "status: superseded\n").replace("superseded_by: null\n", "superseded_by: 20260919-000000-0001\n")
    assert after == expected
    fm = parse_note(after).frontmatter
    assert fm.status == "superseded" and fm.superseded_by == "20260919-000000-0001"


def test_set_status_noop_when_same(note_file):
    before = note_file.read_text(encoding="utf-8")
    mtime = note_file.stat().st_mtime_ns
    set_status(note_file, "active")
    assert note_file.read_text(encoding="utf-8") == before
    assert note_file.stat().st_mtime_ns == mtime  # 다시 쓰지 않았다


def test_set_status_rejects_bad_status(note_file):
    with pytest.raises(ValueError):
        set_status(note_file, "deleted")


def test_set_status_refuses_when_status_line_missing(tmp_path):
    p = tmp_path / "x 20260918-153000-a3f2.md"
    p.write_text("---\nid: 20260918-153000-a3f2\n---\n# t\nb\n", encoding="utf-8")
    with pytest.raises(ForbiddenChange):
        set_status(p, "archived")


def test_no_temp_files_left(note_file):
    append_amendment(note_file, "2026-09-20", "x")
    set_status(note_file, "archived")
    assert [p.name for p in note_file.parent.iterdir()] == [note_file.name]
