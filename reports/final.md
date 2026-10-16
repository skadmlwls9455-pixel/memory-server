# Final report (4-3)

_English translation of final_KO.md. If the two differ, the Korean original is authoritative._

2026-09-18. The implementation environment was a Linux container; physical trials were done on the user's MacBook (M3 Pro 18GB, macOS 14.6) with `memory setup`.

## Full rerun of T01–T12

```
uv run pytest                      267 passed (fake embedder; the 1 real-model test passes with MEMORY_REAL_MODEL=1)
uv run pytest -k "t01 ... t12"     27 passed
scripts/run_t05.py                 bge-m3 passed, e5-small passed
```

| ID | Result | Evidence |
| --- | --- | --- |
| T01 | Pass | tests/test_remember.py. 1 each of note·event·commit, different IDs in the same second, 10-minute duplicate returns the existing ID |
| T02 | Pass | tests/test_correction.py. After supersede, A is byte-identical except for two lines; FACT_CONFLICT carries the full text |
| T03 | Pass | amend is append-only, archive/unarchive, superseded note rejected |
| T04 | Pass | tests/test_doctor.py + test_index.py. No duplicates·omissions after forced termination at two points, deletion restored (not committed), external edit reindexed, secrets·source rejected |
| T05 | Pass | bge-m3: correct 20/20, irrelevant 10/10 @ 0.45 (range 0.435–0.50). reports/stage2-4.md |
| T06 | Pass | tests/test_core.py. Rejects no-evidence·reflection·over-1,500-character entries, hides the sentence when its evidence is invalidated |
| T07 | Pass | tests/test_rebuild_stats.py. Delete db, then rebuild → same IDs·order·similarity·heat·core |
| T08 | Pass (partly unverified) | reports/t08.md. In Desktop, 3 saves saved/indexed/committed; in a new conversation, recalled 3 items via recall (0.63/0.53/0.48). "Does not draw conclusions from the preview alone" is unverified because with 3 notes all were returned as full text |
| T09 | Pass | reports/t09.md. Access Denied even with the delete-permission key, no deleteFiles on the upload key, compliance 30 days |
| T10 | Pass | reports/t10.md. Once each with the password manager key and the paper-copy key. In the paper-copy run, search hit after rebuilding 3 notes |
| T11 | Pass | tests/test_backup.py. 48h warning, retry under the same name, re-upload does not change freshness |
| T12 | Pass | tests/test_heat.py, test_recall.py. 0.5→0.25→0.55, 6h re-report ignored, group ordering, expansion to archived notes blocked |

The MacBook measurement table (model loading·memory·recall latency) is in `reports/t08.md` on the user's Mac and has not yet been attached to this repository. Container reference values: loading 9.5s, RSS 2.0GB, recall 111ms.

## DECISIONS.md summary (D-001–D-053)

- **Environment**: mcp 2.x MCPServer (successor to FastMCP), MEMORY_HOME required, this repository is server/, IDs use local time, tests use a fake embedder.
- **Format·rules**: errors are a structured dict, reserved heading lines forbidden, `## 덧붙임` (amendments) at the first amend, reflection↔inference bidirectional, supersede is a single created event, reuse of an archived fact_key allowed.
- **Safeguards**: changes to existing notes are byte-verified in the single place mutations, doctor uses Git HEAD as baseline + supersede correction, input_hash in recovered, secret detection by format only.
- **Recall**: filter after KNN 200, input filters on expansion too, tag expansion ordered by share count, min_similarity 0.45 (low end of the range).
- **Backup**: credentials keychain→environment variables, pending retry when hash matches, drill date in runtime, restore is a two-step bootstrap.
- **Operations**: the setup wizard creates the bucket·keys via the B2 API and runs T08–T10, model preloading, store split into a package.

## What was implemented differently from the SPEC

| Item | SPEC | Implementation | Record |
| --- | --- | --- | --- |
| Server class | FastMCP | MCPServer (the 2.x name in the same SDK) | D-001 |
| doctor table | 7 rows | + supersede consistency correction (sets the active note pointed to by supersedes to superseded) | D-030 |
| DB memories columns | heat, heat_at | + used_at (6-hour check) | D-037 |
| Tag expansion | "sharing 3" | shares at least one of the top 3, ordered by share count | D-041 |
| Model loading | at first recall/indexing | background preloading right after startup (can be turned off) | D-052 |
| Event log | nine types | Unchanged. Added input_hash to `recovered`/`amended`, and path to `restored`, as data | D-013, D-029 |
| Installation | user follows SETUP.md | `memory setup` wizard does it on the user's behalf (except account sign-up·2FA·master key·printing) | D-051 |

Not added: delete tools, body overwriting, paid APIs, new upload targets, shortening the lock period, chapter 10 deferred items. Confirmed by search results (reports/stage1.md, stage2-4.md).

## Remaining risks

1. **Device scope.** This server is used only in Claude Desktop on the MacBook. Phone·web conversations are neither saved nor recalled. The user's actual usage is mostly on the phone, so the value is narrow. Solving this requires remote transport (streamable-http) + authentication + an always-on host, and that is a decision that revives the "always-on server" deferral in SPEC 10. The condition for reviving it ("if not being able to use it because the MacBook isn't there happens often") appears to already be met.
2. **Threshold margin.** 0.45 is 0.027 away from the maximum top-1 irrelevant value of 0.423, and in a real conversation a correct answer came out at 0.48. Retune if failure cases accumulate (one line of config).
3. **T05 bias.** The notes and queries were made by the same person. It needs to be corrected with failure cases from real use.
4. **Unverified items.** Whether it avoids drawing conclusions from the preview alone (needs 4 or more notes), reflecting the MacBook measurement table, running `memory setup golive` (not done yet).
5. **Token cost.** About 2 thousand tokens of tool descriptions per conversation. Reduced, but not 0. It is worth it only when memories are used.
6. **Operational habits.** Quarterly restore drills, deleting the master key (by the user in the console), Command Line Tools updates (to avoid brew building from source).
7. **Concurrent execution.** If `memory rebuild` is run while the server is on, the server must be restarted. Each mark_used creates a commit, so history gets long (no functional problem).

## Whether to switch to real use

The code and tests are ready. However, because of item 1 above, it is right to run `memory setup golive` only after the user decides whether to use it as a "MacBook study memory" or to attach remote transport first.
