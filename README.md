# memory-server

**A local MCP server that gives an AI assistant long-term memory you can audit,
correct and restore, and that it cannot quietly rewrite.**

The assistant (Claude Desktop) saves selected facts and episodes from a
conversation as Markdown notes. In later conversations it recalls what is still
*currently true*. Everything runs on one laptop at zero extra cost: local
embeddings, a SQLite vector index, local Git history, and an encrypted,
write-once backup.

Status: implemented to SPEC v0.3. All 12 acceptance tests (T01–T12) pass
(one sub-check of T08 still needs more notes to exercise), including a live run on a MacBook (Claude Desktop connection, Object Lock
deletion test, restore from ciphertext only). The suite has 266 offline tests
plus 1 real-model test.

> The specification, decision log and reports are written in Korean
> (`SPEC.md`, `DECISIONS.md`, `reports/`). The original Korean README is
> [`README_KO.md`](README_KO.md).

## Why I built it

Chat assistants forget everything between conversations. The usual fixes,
such as summarizing every chat automatically or letting the model edit its own
memory, introduce failure modes I cared more about than forgetting:

- the model's **guesses get stored as facts** about the user;
- an outdated fact (an old job, a past preference) keeps being recalled **as if
  still true**;
- instructions hidden inside a stored memory get **re-read as a new request**;
- one bad tool call can **silently overwrite or delete** what was there.

So the goal was memory that a person can trust: every change is visible, every
claim has a source, corrections replace facts without destroying history, and
nothing is lost even if the model, the server or I make a mistake.

## Design principles

From `SPEC.md` §1. Each one is enforced in code and covered by tests:

1. **No delete path.** There is no delete tool, and the server never rewrites
   a note body. It is allowed exactly three mutations on an existing note:
   append a dated line, change `status`, fill `superseded_by`. Each is
   byte-verified (`mutations.py`).
2. **Text is the source of truth.** Notes are Markdown in a Git repo you can
   open in Obsidian. The database is a cache; `rebuild` recreates it from
   scratch with identical recall order (T07).
3. **Amending is not correcting.** Adding detail uses `amend`. A changed fact
   gets a *new* note that `supersedes` the old one. The old note is kept but
   leaves default recall. Saving a second active note under the same
   `fact_key` returns `FACT_CONFLICT` with the existing note.
4. **Model interpretation never becomes a user fact.** Every note carries a
   source (`user` / `document` / `inference`). `reflection` notes must be
   `inference` and vice versa, and an inference cannot be stored as a fact.
   The always-loaded summary (`core`) must cite active, non-reflection
   evidence. If that evidence is later corrected or archived, the sentence is
   withheld and flagged for review.
5. **Memories are data, not instructions.** The server's MCP instructions and
   the client instructions tell the model that text inside a memory is never
   a current user request.
6. **Saved ≠ indexed ≠ committed ≠ backed up.** Each is reported separately, so
   the assistant cannot claim success it does not have.
7. **Ask the model for as little as possible.** Every required argument is
   another chance for a save to be skipped. So `request_id`, `session_id` and
   revision checks were dropped in favour of input hashing and time windows.

A secrets guard rejects known credential formats before anything is written.
Error messages never echo the matched value.

## Architecture

```text
Claude Desktop ──stdio MCP──▶ server.py (10 tools)
                                │
     remember / amend / archive │ recall / read_memory / get_core
                                ▼
   vault/ (Markdown + Git) ◀── store ──▶ db/memory.db (SQLite + sqlite-vec cache)
      │  events-YYYY-MM.jsonl (append-only)        ▲
      │                                            └── bge-m3 embeddings (local)
      ▼
   nightly snapshot ─ tar ─ age encryption ─ rclone ─▶ Backblaze B2 (Object Lock, 30 days)
```

- **Recall:** vector KNN, then a minimum-similarity cutoff calibrated on a
  held-out query set (0.45 for bge-m3: 20/20 relevant queries hit the top 3,
  10/10 irrelevant queries return nothing). Link and tag expansion run after
  that. A decaying "heat" score from actual use reorders results only *within*
  a similarity tie band (ε = 0.02), so usage can never push a less relevant
  memory above a more relevant one.
- **Recovery:** `doctor` checks the vault against Git `HEAD`. It restores
  externally deleted notes, re-indexes external edits, salvages truncated
  event logs and repairs half-finished corrections after a crash.
- **Backup:** the upload key has no `deleteFiles` permission, and the bucket is
  in compliance-mode Object Lock. A key that *does* have delete permission
  still gets `Access Denied` (T09). Restore was rehearsed from ciphertext
  alone, once with each copy of the private key (T10).

```text
src/memory_server/
  store/, mutations.py, notes.py, events.py   source-of-truth layer
  index/, recall/                              SQLite + sqlite-vec, embedders, ranking
  core.py                                      evidence-backed always-loaded summary
  doctor.py, secrets_guard.py                  recovery and input defence
  backup/, setup/                              snapshots, B2, setup wizard
  server.py, cli.py                            MCP tools and `memory` CLI
data/eval/      64 synthetic notes + tuning/eval query sets (no real personal data)
reports/        stage reports and live test results
```

## How the design changed

### v0.2 → v0.3: keep the decisions, remove the machinery

v0.2 got most decisions right but was over-built for one person using it a few
times a day. v0.3 kept every *semantic* safeguard and cut the operational ones
that Git or a single-writer lock already cover:

| | |
|---|---|
| **Kept** | amend vs. supersede, `fact_key`, source labels, reflections never promoted to facts, encrypted Object-Lock backup, no personal memories on GitHub, restore rehearsal before real use, heat only reorders within a similarity band, evidence-backed core |
| **Changed** | merged separate `store/` and `vault/` into one vault and limited the server to three byte-verified mutations · titled filenames so the Obsidian graph is readable · `recall` returns full text for the top 3 to save round trips · externally deleted notes are auto-restored from Git · two copies of the age private key, one offline |
| **Removed** | intent files, fsync durability protocol, event hash chain, `request_id`/`session_id`, `expected_revision`, core version conflicts, versioned config directories, per-upload retention checks |
| **Tests** | 27 planned acceptance tests cut to 12 |

Every removed item is listed in SPEC §10 with the **condition that would bring
it back**, for example "if duplicate saves actually accumulate" for
`request_id`. Cutting a safeguard was treated as a reversible decision with a
stated trigger, not as a deletion.

### v0.3 spec → implementation

53 implementation decisions are logged in `DECISIONS.md` with reasons. These
are the places where the code deliberately differs from the spec:

| Spec | Implementation | Why |
|---|---|---|
| `doctor` repairs 7 cases | adds correction-consistency repair | a crash between writing the new note and marking the old one leaves two active notes for one fact (D-030) |
| tag expansion needs 3 shared tags | any of the top 3, ranked by overlap | requiring all three almost never matched; ranking by overlap still puts full matches first (D-041) |
| model loads on first recall | preloads in background at startup | removes a ~20 s wait on the first conversation, noticed in the live T08 test, while load failure still never blocks a save (D-052) |
| user follows SETUP.md by hand | `memory setup` wizard drives B2, keys, launchd and T08–T10 | keeps secrets on the user's machine instead of passing them through chat, and removes error-prone manual steps (D-051) |

### Open questions

From `reports/final.md`:

- **Device scope.** The server only works in Claude Desktop on the laptop, but
  most real use happens on a phone. Fixing that means bringing back the
  "always-on server" item the spec deferred, which needs remote transport and
  authentication.
- **Threshold margin.** 0.45 sits only 0.027 above the best irrelevant match,
  so it needs recalibration as real failures accumulate.
- **Instruction-following.** The server cannot force the model to call
  `recall` or label sources correctly. It can only reject what is invalid.

## Tech stack

Python 3.11 · official `mcp` SDK (stdio) · sentence-transformers with
`BAAI/bge-m3` (or `multilingual-e5-small`) · SQLite + sqlite-vec · Git ·
age · rclone + Backblaze B2 Object Lock · launchd · uv · pytest

## Run it

```sh
uv sync
uv run pytest                                   # fake embedder; no model download
MEMORY_REAL_MODEL=1 uv run pytest tests/test_real_model.py   # real bge-m3

export MEMORY_HOME=~/memory-test                # never point tests at a real vault
uv run memory init
uv run memory serve                             # MCP stdio server
```

Connecting Claude Desktop, B2 backup and restore are covered in `SETUP.md`
and `RESTORE.md` (Korean). `scripts/run_t05.py` recalibrates the similarity
threshold against the evaluation set.

## How this was built

I wrote the specification and made the design calls. The implementation was
done step by step with an AI coding assistant against that spec, with each
stage gated on its acceptance tests and every deviation logged in
`DECISIONS.md`. The live tests (T08–T10) were run on my own machine.

## Copyright
Copyright (c) 2026 Katherine Nahm (남의진). All rights reserved. See [LICENSE](LICENSE).
