# Stages 2·3·4 implementation report (indexing, recall, core, rebuild, stats, backup, heat score, expansion)

_English translation of stage2-4_KO.md. If the two differ, the Korean original is authoritative._

2026-09-18 · Implementation environment: Linux x86_64 container, 4 CPU cores, 15GB RAM. MacBook measurement (T08) and physical B2·age trials (T09, T10) are the user's part.

## Test results summary

| ID | Test | Result | Notes |
| --- | --- | --- | --- |
| T05 | 20 correct / 10 irrelevant (bge-m3) | **Pass** 20/20, 10/10 | `scripts/run_t05.py`. Tuning range 0.435–0.50, adopted 0.45 (D-050). e5-small: passes at 0.83 with 19/20, 9/10 |
| T06 | 3 kinds of core rejection + evidence invalidation | Pass | `tests/test_core.py` |
| T07 | rebuild identity after deleting db | Pass | Same query → same IDs·order·similarity·heat, same state·core. `tests/test_rebuild_stats.py` |
| T08 | MacBook measurement·Claude Desktop | **Not run (user)** | Container reference values below. `scripts/measure_t08.py`, SETUP.md chapter 3 |
| T09 | Lock deletion rejection, key permissions | **Not run (user)** | Procedure in SETUP.md chapter 6 |
| T10 | Restore into an empty directory | Procedure only verified | Restore→rebuild→search passes with fake age·rclone (`test_t10_shape_...`). Physical run is the user's |
| T11 | 48h gap, failure retry, re-upload | Pass | `tests/test_backup.py`, fake provider |
| T12 | Decay·heating·6h·group ordering·expansion | Pass | `tests/test_heat.py`, `tests/test_recall.py` |
| T04 rerun | "Edits are reindexed" | Pass | `test_external_edit_is_reindexed_by_doctor` |

```
uv run pytest                      → pass (1 real-model test skipped)
MEMORY_REAL_MODEL=1 pytest tests/test_real_model.py → pass
```

## T05 details (bge-m3, 64 fictional notes)

| min_similarity | Tuning correct top3 | Tuning irrelevant empty |
| --- | --- | --- |
| 0.45 | 15/15 | 10/10 |
| 0.50 | 15/15 | 10/10 |
| 0.55 | 12/15 | 10/10 |
| 0.60 | 8/15 | 10/10 |

Evaluation set (separate): correct-answer similarity min 0.499 / median 0.648 / max 0.735, top-1 irrelevant similarity min 0.304 / median 0.376 / max 0.423. No failure cases. Median recall latency 135ms (CPU).

Caution: short queries without names sit on the boundary. "회사 어디 다녀?" ("Where do you work?") 0.45, "직장" ("workplace") 0.425, "어느 회사에서 일해?" ("Which company do you work at?") 0.582. That is why 0.45, at the low end of the range, was chosen. The notes and queries were made by the same person, so the scores may be generous; retune with failure cases after real use.

e5-small failure cases: "전 직장이 어디였고 왜 그만뒀어?" ("Where was your previous job and why did you quit?") correct-answer similarity 0.824 (below the 0.83 threshold), "농구 트래블링 규칙" ("basketball traveling rule") → "PR 리뷰 규칙" ("PR review rules") 0.841. e5 has a narrow similarity spread (irrelevant 0.77–0.84, correct 0.82–0.92), so the boundary is sharper.

## T08 container reference values (these are not MacBook values)

| Item | Value |
| --- | --- |
| Model loading | 9.5s (warm cache) / 20s (first run) |
| RSS after loading | about 2.0GB, peak 2.7GB |
| remember average | 178ms (including embedding) |
| recall median / p95 | 111ms / 121ms |
| sqlite-vec | v0.1.9 loading·cosine KNN pass |

## Not implemented·not verified

- T08, T09, T10 physical runs: run by the user. Procedures in SETUP.md, RESTORE.md.
- Remote usage in stats is updated from `rclone size` results when a backup runs. There is no separate periodic query.
- Stage 4 "rerun T05 and compare that it did not get worse": the results above are from running T05 with the expansion path in place (no change on the top-3 basis).

## Changes made on the test side

- Fixtures change the embedder in `config.toml` to `fake/ngram-hash` (D-036). Accordingly, the model-check locations in `test_load_after_init`, `test_init_files_are_loadable` were moved.
- `test_tools_via_stdio_roundtrip`: Stage 1's `indexed is False` changed to `indexed is True` (because indexing now exists).
- `test_no_secrets_in_default_config`: the "key" word check → a secret value pattern check (keychain service names contain key).
- `min_similarity` default check 0.45 → 0.47 → 0.45 (reflecting T05 tuning results).
- Adjusted example sentences in recall tests to fit the fake embedder's similarity characteristics (the purpose is structural verification).

## Paths that write to files (additions)

- `index/db.py`: `db/memory.db` (cache). `rebuild.py`: creates a temporary DB, then replaces with `os.replace` (cache).
- `core.py`: `core.json`, `core.md` via temporary file + `os.replace` (generated artifacts that are not notes, D-042).
- `backup/`: staging, pending ciphertext, `backup_state.json` under `runtime/backup/`. Does not write to the vault. A remote delete API was not implemented.
- `backup/restore.py`: extracts only into an empty target directory.

There is still no path that rewrites the body·title of memory notes or deletes notes.
