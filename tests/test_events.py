import json
from datetime import datetime, timedelta, timezone

import pytest

from memory_server.events import EVENT_TYPES, EventError, append_event, iter_events

KST = timezone(timedelta(hours=9))


def test_event_types_are_the_nine_from_spec():
    assert EVENT_TYPES == {
        "created", "amended", "used", "archived", "unarchived",
        "core_updated", "external_edit", "restored", "recovered",
    }


def test_append_writes_monthly_file_with_one_json_line(initialized):
    ts = datetime(2026, 9, 18, 15, 30, 0, tzinfo=KST)
    path = append_event(initialized, "created", ["20260918-153000-a3f2"], {"supersedes": []}, ts=ts)
    assert path == initialized.events_file(2026, 9)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == {
        "ts": "2026-09-18T15:30:00+09:00",
        "type": "created",
        "ids": ["20260918-153000-a3f2"],
        "data": {"supersedes": []},
    }


def test_append_only_never_rewrites_previous_lines(initialized):
    ts = datetime(2026, 9, 1, 0, 0, 0, tzinfo=KST)
    append_event(initialized, "created", ["20260901-000000-0001"], ts=ts)
    path = initialized.events_file(2026, 9)
    first = path.read_text(encoding="utf-8")
    append_event(initialized, "amended", ["20260901-000000-0001"], {"note": "덧붙임"}, ts=ts + timedelta(minutes=1))
    text = path.read_text(encoding="utf-8")
    assert text.startswith(first)
    assert text.count("\n") == 2


def test_month_boundary_splits_files(initialized):
    append_event(initialized, "used", ["20260930-235959-0001"], ts=datetime(2026, 9, 30, 23, 59, 59, tzinfo=KST))
    append_event(initialized, "used", ["20261001-000000-0001"], ts=datetime(2026, 10, 1, 0, 0, 0, tzinfo=KST))
    assert initialized.events_file(2026, 9).exists()
    assert initialized.events_file(2026, 10).exists()


def test_korean_is_not_escaped(initialized):
    ts = datetime(2026, 9, 18, tzinfo=KST)
    path = append_event(initialized, "archived", ["20260918-000000-0001"], {"reason": "이유"}, ts=ts)
    assert '"reason":"이유"' in path.read_text(encoding="utf-8")


def test_default_ts_is_local_with_offset(initialized):
    path = append_event(initialized, "created", ["20260918-000000-0001"])
    obj = json.loads(path.read_text(encoding="utf-8"))
    parsed = datetime.fromisoformat(obj["ts"])
    assert parsed.tzinfo is not None


@pytest.mark.parametrize("bad_type", ["deleted", "updated", ""])
def test_unknown_type_rejected(initialized, bad_type):
    with pytest.raises(EventError):
        append_event(initialized, bad_type, ["20260918-000000-0001"])
    assert not any(initialized.events_dir.iterdir())


def test_bad_ids_rejected(initialized):
    with pytest.raises(EventError):
        append_event(initialized, "created", "20260918-000000-0001")  # type: ignore[arg-type]
    with pytest.raises(EventError):
        append_event(initialized, "created", [""])


def test_naive_ts_rejected(initialized):
    with pytest.raises(EventError):
        append_event(initialized, "created", ["20260918-000000-0001"], ts=datetime(2026, 9, 18))


def test_iter_events_skips_truncated_tail(initialized):
    ts = datetime(2026, 9, 18, tzinfo=KST)
    path = append_event(initialized, "created", ["20260918-000000-0001"], ts=ts)
    append_event(initialized, "used", ["20260918-000000-0001"], ts=ts)
    with open(path, "a", encoding="utf-8") as f:
        f.write('{"ts":"2026-09-18T00:00:00+09:00","type":"used","ids":["2026')  # 잘린 줄
    events = list(iter_events(path))
    assert [e.type for e in events] == ["created", "used"]


def test_iter_events_missing_file(initialized):
    assert list(iter_events(initialized.events_file(2000, 1))) == []
