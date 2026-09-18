import pytest

from memory_server.notes import (
    Amendment,
    Frontmatter,
    Note,
    NoteFormatError,
    body_has_reserved_heading,
    parse_note,
    render_note,
)

FM = Frontmatter(
    id="20260918-153000-a3f2",
    created="2026-09-18T15:30:00+09:00",
    type="fact",
    source="user",
    fact_key="self/employer",
    tags=["주제/직장"],
    links=[],
    supersedes=["20260801-101500-9c1e"],
)


def test_render_matches_spec_shape():
    note = Note(
        frontmatter=FM,
        title="현재 근무 회사는 B 회사",
        body="사용자는 현재 B 회사에 다닌다고 말했다.",
        related=["- 대체함: [[이전 근무 회사는 A 회사 20260801-101500-9c1e|이전 근무 회사는 A 회사]]"],
        amendments=[Amendment("2026-09-20", "입사일은 9월 1일.")],
    )
    text = render_note(note)
    assert text == (
        "---\n"
        "id: 20260918-153000-a3f2\n"
        'created: "2026-09-18T15:30:00+09:00"\n'
        "type: fact\n"
        "source: user\n"
        "fact_key: self/employer\n"
        "tags: [주제/직장]\n"
        "links: []\n"
        "supersedes: [20260801-101500-9c1e]\n"
        "status: active\n"
        "superseded_by: null\n"
        "---\n"
        "# 현재 근무 회사는 B 회사\n"
        "\n"
        "사용자는 현재 B 회사에 다닌다고 말했다.\n"
        "\n"
        "## 관련\n"
        "- 대체함: [[이전 근무 회사는 A 회사 20260801-101500-9c1e|이전 근무 회사는 A 회사]]\n"
        "\n"
        "## 덧붙임\n"
        "- 2026-09-20: 입사일은 9월 1일.\n"
    )


def test_roundtrip():
    note = Note(
        frontmatter=FM,
        title="제목: 콜론 포함",
        body="첫 줄\n\n## 본문 안의 소제목\n- 목록\n\n마지막 줄",
        related=["- 관련: [[x 20260801-101500-9c1e|x]]"],
        amendments=[Amendment("2026-09-20", "덧붙임 하나"), Amendment("2026-09-21", "둘: 콜론")],
    )
    parsed = parse_note(render_note(note))
    assert parsed.frontmatter == FM
    assert parsed.title == note.title
    assert parsed.body == note.body
    assert parsed.related == note.related
    assert parsed.amendments == note.amendments


def test_roundtrip_without_sections():
    note = Note(frontmatter=FM, title="t", body="b")
    parsed = parse_note(render_note(note))
    assert parsed.related == [] and parsed.amendments == []
    assert parsed.body == "b"


def test_special_values_quoted_and_parsed():
    fm = Frontmatter(
        id="20260918-153000-a3f2", created="2026-09-18T15:30:00+09:00", type="fact", source="document",
        fact_key='project/기억 시스템: "상태"', tags=["주제/a b", "사람/김철수"], links=[], supersedes=[],
    )
    parsed = parse_note(render_note(Note(frontmatter=fm, title="t", body="b")))
    assert parsed.frontmatter.fact_key == fm.fact_key
    assert parsed.frontmatter.tags == fm.tags


def test_created_stays_string_even_if_unquoted():
    text = render_note(Note(frontmatter=FM, title="t", body="b")).replace('created: "2026-09-18T15:30:00+09:00"', "created: 2026-09-18T15:30:00+09:00")
    assert parse_note(text).frontmatter.created == "2026-09-18T15:30:00+09:00"


@pytest.mark.parametrize(
    "text",
    [
        "no frontmatter",
        "---\nid: x\n",  # 끝 없음
        "---\nid: 20260918-153000-a3f2\ncreated: 2026-09-18T15:30:00+09:00\n---\n# t\nb\n",  # type/source 없음
        "---\nid: bad\ncreated: 2026-09-18T15:30:00+09:00\ntype: fact\nsource: user\n---\n# t\nb\n",
        "---\nid: 20260918-153000-a3f2\ncreated: 2026-09-18T15:30:00+09:00\ntype: memo\nsource: user\n---\n# t\nb\n",
        "---\nid: 20260918-153000-a3f2\ncreated: 2026-09-18T15:30:00+09:00\ntype: fact\nsource: user\nstatus: deleted\n---\n# t\nb\n",
        "---\nid: 20260918-153000-a3f2\ncreated: 2026-09-18T15:30:00+09:00\ntype: fact\nsource: user\n---\nb\n",  # 제목 없음
        "---\nid: 20260918-153000-a3f2\ncreated: 2026-09-18T15:30:00+09:00\ntype: fact\nsource: user\n---\n# t\nb\n## 덧붙임\n- 형식 틀림\n",
        "---\n[unclosed\n---\n# t\nb\n",
    ],
)
def test_broken_notes_rejected(text):
    with pytest.raises(NoteFormatError):
        parse_note(text)


def test_reserved_heading_detection():
    assert body_has_reserved_heading("a\n## 관련\nb") == "## 관련"
    assert body_has_reserved_heading("a\n## 덧붙임  \nb") == "## 덧붙임"
    assert body_has_reserved_heading("## 관련 사항\n### 덧붙임") is None
