import unicodedata
from datetime import datetime, timedelta, timezone

import pytest

from memory_server.naming import (
    TITLE_MAX_LEN,
    UNTITLED,
    IdError,
    find_note_by_id,
    id_exists,
    is_valid_id,
    new_id,
    note_filename,
    note_path,
    safe_title,
)

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 9, 18, 15, 30, 0, tzinfo=KST)


def test_new_id_format():
    nid = new_id(lambda _: False, now=NOW)
    assert nid.startswith("20260918-153000-")
    assert is_valid_id(nid)
    assert len(nid) == len("20260918-153000-a3f2")


def test_new_id_retries_when_exists():
    seen = set()
    first = new_id(lambda _: False, now=NOW)
    seen.add(first)
    calls = []

    def exists(candidate):
        calls.append(candidate)
        return candidate in seen

    second = new_id(exists, now=NOW)
    assert second != first
    assert second.startswith("20260918-153000-")


def test_new_id_gives_up_after_many_collisions():
    with pytest.raises(IdError):
        new_id(lambda _: True, now=NOW)


def test_same_second_ids_differ():
    ids = {new_id(lambda _: False, now=NOW) for _ in range(20)}
    assert len(ids) > 1


@pytest.mark.parametrize("bad", ["2026-09-18", "20260918-153000", "20260918-153000-A3F2", "20260918-153000-a3f2.md", ""])
def test_invalid_ids(bad):
    assert not is_valid_id(bad)


def test_safe_title_strips_forbidden_chars():
    assert safe_title('a/b\\c:d*e?f"g<h>i|j#k^l[m]n') == "abcdefghijklmn"


def test_safe_title_normalizes_nfc():
    nfd = unicodedata.normalize("NFD", "현재 근무 회사")
    assert safe_title(nfd) == "현재 근무 회사"
    assert unicodedata.is_normalized("NFC", safe_title(nfd))


def test_safe_title_truncates_to_60():
    long = "가" * 100
    assert safe_title(long) == "가" * TITLE_MAX_LEN


def test_safe_title_collapses_whitespace_and_newlines():
    assert safe_title("  제목   with\n줄바꿈\t탭 ") == "제목 with 줄바꿈 탭"


@pytest.mark.parametrize("empty", ["", "   ", "###", "[]/\\", "..."])
def test_safe_title_empty_falls_back(empty):
    assert safe_title(empty) == UNTITLED


def test_note_filename():
    assert note_filename("현재 근무 회사는 B 회사", "20260918-153000-a3f2") == "현재 근무 회사는 B 회사 20260918-153000-a3f2.md"


def test_note_filename_rejects_bad_id():
    with pytest.raises(IdError):
        note_filename("제목", "bad-id")


def test_note_path_under_year_month(initialized):
    p = note_path(initialized, "제목", "20260918-153000-a3f2", NOW)
    assert p == initialized.memories / "2026" / "09" / "제목 20260918-153000-a3f2.md"


def test_find_note_by_id(initialized):
    d = initialized.memories / "2026" / "09"
    d.mkdir(parents=True)
    target = d / "어떤 제목 20260918-153000-a3f2.md"
    target.write_text("x", encoding="utf-8")
    assert find_note_by_id(initialized, "20260918-153000-a3f2") == target
    assert find_note_by_id(initialized, "20260918-153000-ffff") is None
    assert find_note_by_id(initialized, "not-an-id") is None
    assert id_exists(initialized)("20260918-153000-a3f2") is True


def test_find_note_by_id_rejects_duplicates(initialized):
    d = initialized.memories / "2026" / "09"
    d.mkdir(parents=True)
    (d / "a 20260918-153000-a3f2.md").write_text("x", encoding="utf-8")
    (d / "b 20260918-153000-a3f2.md").write_text("y", encoding="utf-8")
    with pytest.raises(IdError):
        find_note_by_id(initialized, "20260918-153000-a3f2")
