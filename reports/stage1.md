# Stage 1 (storage) completion report

_English translation of stage1_KO.md. If the two differ, the Korean original is authoritative._

2026-09-18 · Commit reference: the commit that contains this file

## Test results

```
uv run pytest -k "t01 or t02 or t03 or t04"   → 14 passed
uv run pytest                                  → 200 passed
```

| ID | Test | Result | Notes |
| --- | --- | --- | --- |
| T01 | remember once / twice in the same second / same input within 10 minutes | Pass | 1 each of note·event·commit. Same second is two calls with different inputs (D-014). Repeat call returns the existing ID |
| T02 | A→B supersede / same fact_key without supersedes | Pass | A is byte-identical except for the two lines status·superseded_by. FACT_CONFLICT carries the full text of the existing note |
| T03 | amend / archive / unarchive / unarchive of a superseded note | Pass | Only the amendments grow, body unchanged. Superseded note rejected with SUPERSEDED |
| T04 | Forced termination (before event, before DB) / external deletion / external edit / secret format / inference fact | Pass | No duplicates·omissions after restart. Deletion is not committed. **"Edits are reindexed" unverified because there is no DB** (rerun after 2-1) |

Items that failed or could not be run: one item, the reindex check in T04 (depends on Stage 2).

## Implementation scope

- 5 MCP tools: remember, read_memory, amend, archive, unarchive
- CLI: memory init, memory doctor, memory serve
- Write procedure (SPEC 5): flock → validation → temporary file+rename → event flush (saved) → indexed=false → path-specified commit (committed)
- doctor (entire SPEC 5 table, Git HEAD baseline) + supersede consistency correction (D-030)
- Error codes: INVALID_INPUT, INVALID_TAG, INVALID_SOURCE, SECRET_DETECTED, NOT_FOUND, NOTE_CORRUPT, FACT_CONFLICT, INVALID_SUPERSEDES, NOT_ACTIVE, SUPERSEDED, LOCKED, VAULT_NOT_GIT, CONFIG_ERROR, INTERNAL

## Not implemented (next stages, as planned)

| Item | Stage |
| --- | --- |
| DB indexing, reindexing, pending-index count, SIMILAR_EXISTS warning | 2-1 |
| recall, min_similarity tuning | 2-2 |
| get_core, update_core, core.md generation | 2-3 |
| rebuild, stats | 2-4 |
| All of backup | 3 |
| mark_used, heat score, link·tag expansion | 4 |

## Search for overwrite·delete paths

All calls in `src/memory_server/*.py` that write or delete files:

| Location | Behavior | Effect on existing notes |
| --- | --- | --- |
| store.py `_write_new_file` | Temporary file → rename to a final path that does not exist. On failure, unlink the temporary file | None (rejected if it exists) |
| mutations.py `_atomic_replace` | Only adding an amendment or changing the status/superseded_by lines, renamed after byte verification | Only the three allowed kinds |
| events.py `append_event` | append to jsonl | None |
| doctor.py `_repair_event_logs` | Moves the truncated tail to recovery and renames up to the intact lines | None (event log) |
| doctor.py deletion restore | `git checkout -- <경로>` (<path>) | Restore only |
| doctor.py `_persist` | runtime/doctor_last.json | None |
| init_cmd.py | Creates only files that do not exist | None |

Paths that rewrite note titles·bodies, paths that delete note files, delete tools, remote calls, paid APIs, hardcoded `~/memory`: none.

## DECISIONS.md summary (D-001–D-034)

- Environment: mcp 2.x MCPServer (D-001), MEMORY_HOME required (D-002), repository root=server/ (D-003), IDs use local time (D-004)
- init: commit only created files (D-006), fixed commit author (D-007), core.md in Stage 2 (D-008), quotePath=false (D-019)
- Format: errors are {error:{code,message,retryable}} (D-012), reserved heading lines forbidden (D-015), amendments heading at the first amend (D-016), reflection↔inference bidirectional (D-017)
- Duplicate prevention: input_hash of created/recovered/amended events (D-013, D-026, D-029)
- Supersede: an already superseded note cannot be superseded again (D-021), reuse of an archived fact_key allowed and then unarchive is FACT_CONFLICT (D-022), a single created event (D-023)
- doctor: Git HEAD baseline (D-027), server change if within allowed changes (D-028), supersede correction (D-030), restore targets limited (D-031), secrets by format only (D-033)

## Can we move on to Stage 2?

Yes. Evidence:
- T01–T04 all passed except the one reindex check, and that one can only be verified once 2-1 exists.
- Changes to existing notes are gathered in one place, `mutations.py`, with byte verification.
- doctor has an `indexer` hook, so 2-1's indexing·reindexing attach directly to the remaining two cells of the table.

Things to know before moving on:
- SPEC chapter 2's sqlite-vec loading and bge-m3 execution need confirmation on Apple Silicon, but the implementation environment is a Linux container. Here only loading and cosine KNN are verified, and the MacBook check is done in T08.
- The model download (bge-m3, about 2.3GB) may be blocked by the container's network policy. Check before starting 2-1.
