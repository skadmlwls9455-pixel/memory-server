import pytest

from memory_server.errors import MemoryError
from memory_server.store import MemoryStore


@pytest.fixture
def store(initialized) -> MemoryStore:
    return MemoryStore(initialized)


def test_read_memory_returns_full_note(store):
    a = store.remember("대상", "본문 A", tags=["주제/직장"])
    b = store.remember("읽기", "본문 B\n\n둘째 단락", type="fact", fact_key="self/employer", source="document", links=[a.id])
    r = store.read_memory(b.id)
    assert r["id"] == b.id
    assert r["title"] == "읽기"
    assert r["body"] == "본문 B\n\n둘째 단락"
    assert r["type"] == "fact" and r["source"] == "document" and r["fact_key"] == "self/employer"
    assert r["links"] == [a.id]
    assert r["status"] == "active" and r["superseded_by"] is None and r["supersedes"] == []
    assert r["related"] == [f"- 관련: [[대상 {a.id}|대상]]"]
    assert r["amendments"] == []
    assert r["path"].startswith("memories/")


def test_read_memory_not_found(store):
    with pytest.raises(MemoryError) as ei:
        store.read_memory("20260918-000000-0000")
    assert ei.value.code == "NOT_FOUND" and ei.value.retryable is False
    with pytest.raises(MemoryError):
        store.read_memory("")


def test_read_memory_reads_archived_and_superseded_status(store, initialized):
    r = store.remember("상태", "본문")
    path = next(initialized.memories.rglob(f"*{r.id}.md"))
    # 외부에서 상태만 바뀐 파일도 그대로 읽힌다 (보관 여부와 상관없이 읽힘)
    path.write_text(path.read_text(encoding="utf-8").replace("status: active", "status: archived"), encoding="utf-8")
    assert store.read_memory(r.id)["status"] == "archived"


def test_read_memory_corrupt_frontmatter(store, initialized):
    r = store.remember("깨짐", "본문")
    path = next(initialized.memories.rglob(f"*{r.id}.md"))
    path.write_text("---\nid: [broken\n---\n# 깨짐\n본문\n", encoding="utf-8")
    with pytest.raises(MemoryError) as ei:
        store.read_memory(r.id)
    assert ei.value.code == "NOTE_CORRUPT"


def test_read_memory_id_mismatch(store, initialized):
    r = store.remember("불일치", "본문")
    path = next(initialized.memories.rglob(f"*{r.id}.md"))
    path.write_text(path.read_text(encoding="utf-8").replace(f"id: {r.id}", "id: 20200101-000000-0000"), encoding="utf-8")
    with pytest.raises(MemoryError) as ei:
        store.read_memory(r.id)
    assert ei.value.code == "NOTE_CORRUPT"
