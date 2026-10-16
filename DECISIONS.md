# DECISIONS.md

_English translation of DECISIONS_KO.md. If the two differ, the Korean original is authoritative._

When deciding on parts that are not in SPEC.md or that can be read two ways, the decision and its reason are written here.
The order of priority for judgment is: preserving the original, accuracy of current facts, recoverability.

## Stage 1, 1-1 Skeleton

### D-001 Use `MCPServer` from mcp 2.x
- Decision: Use the official `mcp` SDK 2.x and build the server with `mcp.server.mcpserver.MCPServer`.
- Reason: The `FastMCP` referred to in SPEC chapter 2 was only renamed to `MCPServer` in 2.x. Pinning a new project to 1.x would miss security fixes and Claude Desktop compatibility. It is the same class in the same SDK, so the intent of the SPEC (official SDK, stdio) is unchanged.

### D-002 `MEMORY_HOME` is required and has no default
- Decision: If the environment variable is missing or is a relative path, refuse to start. Do not fall back to `~/memory`.
- Reason: CLAUDE.md rule. Prevents, at the code level, accidents where a test or trial home (`~/memory-test`) gets mixed up with the real vault.

### D-003 The repository root is `server/` from SPEC 3.1
- Decision: This Git repository itself is the code directory placed at `~/memory/server/`. The package name is `memory_server`, the CLI name is `memory`.
- Reason: In SPEC chapter 2, the server code is a separate Git repository. The package name `memory` is too generic and could collide, so it was named `memory_server`. The CLI is `memory`, as the SPEC calls it.

### D-004 Times in IDs are in the local time zone
- Decision: `YYYYMMDD-HHMMSS` is local time based on `datetime.now().astimezone()`. Event `ts` and `created` are also local ISO 8601 with an offset.
- Reason: The SPEC 3.3 example is `+09:00`. If the ID and `created` use the same clock, the creation time can be known from the file name alone. Even if the time zone changes, collisions are prevented by the 4 random characters at the end of the ID and an existence check.

### D-005 If the title is empty after sanitizing, save it as `무제`
- Decision: After removing forbidden characters and whitespace and truncating to 60 characters, if nothing remains, the file-name title becomes `무제` ("Untitled"). Newlines and tabs are replaced with a single space.
- Reason: Keeps the file name from starting with a space, like ` <id>.md`. The `# 제목` (title) inside the note is left as the original text, so the original is preserved.

### D-006 `memory init` commits only the files created by this run
- Decision: init creates only folders and files that do not exist, and `git add`s and commits only those. It does not read or modify files that already exist.
- Reason: It must be safe to run twice. If init silently committed a `config.toml` the user had edited, its role would overlap with doctor's handling of "uncommitted changes".

### D-007 Fix the commit author of the vault repository
- Decision: If the vault repository has no `user.name`/`user.email`, set `memory-server` / `memory-server@localhost` as local config.
- Reason: The personal memory repository has no remote and is used by one person. Prevents the first save from ending in a commit failure on a Mac with no global Git config. If a setting already exists, it is not touched.

### D-008 init creates `core.json` and does not create `core.md`
- Decision: `core.json` is created as `{"entries": []}`. `core.md` is generated from `core.json` by `update_core` in Stage 2.
- Reason: SPEC 3.1 treats `core.json` as the source and `core.md` as a generated artifact. Placing an empty `core.md` without the generation logic would create a file that disagrees with the source.

### D-009 The `.lock` lock waits 30 seconds by default, then fails
- Decision: `write_lock` polls `flock(LOCK_EX|LOCK_NB)`, waiting up to 30 seconds by default, and raises `LockTimeout` if exceeded.
- Reason: The backup CLI may hold the lock while copying to staging. An infinite wait would stall MCP tool calls, so there is an upper bound. The caller can change the value.

### D-010 `tag_rules.yaml` is a list of allowed names per category
- Decision: All five categories (`주제` (topic), `사람` (person), `프로젝트` (project), `상태` (status), `시기` (period)) must be present; if `values` is empty, names are free, and if filled, only that list is allowed.
- Reason: SPEC chapter 4 only says "validate with tag_rules.yaml". Enforcing only the categories at first, and tightening by filling the lists if names become scattered with use, produces fewer missed saves (design principle 7).

### D-011 CLI informational output also goes to stderr
- Decision: Human-facing messages from CLIs such as `memory init` are also emitted to stderr, not stdout.
- Reason: The same executable becomes the MCP server with `memory serve`. Keep the habit of writing nothing to stdout in any subcommand.

## Stage 1, 1-2 remember, read_memory

### D-012 Tool errors are returned as a `{error: {code, message, retryable, ...}}` dictionary
- Decision: Errors are emitted as a structured result value instead of MCP's `isError` string. Additional information (field name, ID and full text of the conflicting note) is carried in the same object.
- Reason: SPEC chapter 4's `FACT_CONFLICT` must carry the full text of the existing note in the error. A structured value suits the model's single retry better than a string error.

### D-013 10-minute duplicate prevention is judged by the `input_hash` of the `created` event
- Decision: The SHA-256 of the normalized input (title, body, tags, type, source, fact_key, links, supersedes) is put in the `created` event data, and if the same hash is found among events from the last 10 minutes, that ID is returned. No separate state file or in-memory cache is kept.
- Reason: It persists across restarts, and since the event log is already a source, no new storage location is added. Only the hash is stored, so the body does not go into the event.

### D-014 T01's "twice in the same second" is read as two calls with different inputs
- Decision: Even within the same second, if inputs differ, two notes with different IDs are created. If the inputs are also the same, the 10-minute rule takes precedence and the existing ID is returned.
- Reason: The only case where the two rules overlap is "same input", and that is the situation SPEC chapter 5's duplicate prevention is meant for.

### D-015 Reserved heading lines and a leading `---` are forbidden in the body
- Decision: If the body contains a line exactly equal to `## 관련` (related) or `## 덧붙임` (amendments), or if the body starts with `---`, it is `INVALID_INPUT`. Other lines such as `## 관련 사항` (related matters) are allowed.
- Reason: The two headings are markers the parser uses to divide sections. If they are mixed into the body, the position where `amend` appends and the body boundary of `read_memory` become unstable.

### D-016 `## 덧붙임` is not created at creation time; it is appended to the end of the file at the first `amend`
- Decision: New notes do not include `## 덧붙임`. `amend` in 1-3, if it is absent, appends it including the heading to the end of the file; if present, it adds just one line.
- Reason: Both are append-to-end-of-file operations, so the body is not rewritten. This is better than putting an empty heading in every note.

### D-017 `reflection` must be `source=inference` (bidirectional)
- Decision: If `source=inference`, then `type=reflection` is required, and if `type=reflection`, then `source=inference` is required.
- Reason: Applied "reflection: only when `source=inference`" from the SPEC chapter 4 table as is. What the user said is an episode or a fact.

### D-018 `links` targets must be existing notes and are written in `## 관련` as `- 관련: [[<제목> <id>|<제목>]]`
- Decision: Putting a nonexistent ID in links is `NOT_FOUND`. The wikilink display is built by reading the current title of the target note.
- Reason: Prevents broken links in the Obsidian graph. Titles do not change after creation, so the display will not go out of sync.

### D-019 Set `core.quotePath=false` on the vault repository
- Decision: init turns off `core.quotePath`.
- Reason: If Korean file names show up garbled as octal in `git status` or `ls-files`, both doctor's path comparison and human inspection become inconvenient. It has no effect on behavior.

## Stage 1, 1-3 Supersede, amend, archive

### D-020 Changes to existing notes are made only in `mutations.py`, and the result is verified byte by byte
- Decision: `append_amendment` checks that the result is `existing content + appended part`, and `set_status` checks that not a single byte differs other than the frontmatter `status`·`superseded_by` lines; only then is the change applied via a temporary file and rename. If it does not match, it raises `ForbiddenChange` and does not write.
- Reason: Have the code, not review, guarantee SPEC 3.3's "three allowed changes". `store.py` has no path that writes to existing files.

### D-021 An already superseded note cannot be superseded again (`INVALID_SUPERSEDES`)
- Decision: If the status of the `supersedes` target is `superseded`, reject and report `superseded_by`. A note that is `archived` can be superseded.
- Reason: If the supersede chain forks, there would be two "currently valid facts". Archive only means exclusion from recall, so an archived note can be a target of correction.

### D-022 The `fact_key` of an archived fact can be used by a new active fact, and after that `unarchive` is `FACT_CONFLICT`
- Decision: Uniqueness is checked only among active notes. When reviving an archived note, if there is an active note with the same key, reject it, carrying that note's ID and full text.
- Reason: Followed SPEC chapter 4 "only one active note per key" and chapter 6 "`unarchive` rejects a fact_key conflict" as is.

### D-023 A supersede is recorded as a single `created` event
- Decision: `supersedes` and `reason` are put in the data of the new note's `created` event. No separate event is left for the status change of the old note.
- Reason: SPEC 3.4's nine event types do not include `superseded`, and the example carries `supersedes` in `created`. The new note, old note and event change within one lock and go into one commit.

### D-024 `archive` of a superseded note is rejected with `NOT_ACTIVE`
- Decision: `archive` accepts only active notes. If already archived, `no_change`; if superseded, reject.
- Reason: A superseded note is already excluded from default recall. Changing its status to `archived` would make the status disagree with the `superseded_by` information.

### D-025 `archive` and `unarchive` require `reason`
- Decision: If empty, `INVALID_INPUT`. The reason is kept in the event data and not written to the note.
- Reason: SPEC chapter 6 puts `reason` in the input of both tools. The event log handles the audit record without touching the note body.

### D-026 `amend` also applies 10-minute duplicate prevention
- Decision: If the same normalized sentence comes to the same note again within 10 minutes, no line is added and the same ID is returned with a `DUPLICATE_INPUT` warning. It is judged by the `input_hash` of the `amended` event, and the body is not put in the event.
- Reason: SPEC chapter 5's duplicate prevention is "same normalized input to the same tool", so it is not limited to `remember`.

## Stage 1, 1-4 doctor, input defense

### D-027 The "known state" baseline for Stage 1 doctor is the vault's Git HEAD
- Decision: While there is no DB, deletions (D), modifications (M) and untracked files (??) are found with `git status` and handled per the SPEC chapter 5 table. In Stage 2, "index notes not in the DB" and "reindex" are attached via an `indexer` hook.
- Reason: Every change the server makes is committed, so anything differing from HEAD is one of two things: "a server change that died before commit" or "an external change". These two can be told apart without a separate state file.

### D-028 If the change relative to HEAD is within the three allowed kinds, it is treated as a server change; otherwise as an external edit
- Decision: The HEAD content and current content of a modified note are compared, and if it is "only an amendment added" or "only the status/superseded_by lines changed", it is committed without an event. Otherwise, an `external_edit` event is left and it is committed.
- Reason: To avoid wrongly recording an `amend` or `archive` that died just before commit as an Obsidian edit. The judgment criterion is the same as the allowed changes in 3.3, so no separate rule is added.

### D-029 Put an `input_hash` recomputed from the note into the `recovered` event
- Decision: For a note found without a created event, the remember input hash is reconstructed from its title, body, tags, etc. and put in the `recovered` event, and 10-minute duplicate prevention looks at both `created` and `recovered`.
- Reason: If the process dies before writing the event, the model gets no response and retries with the same input. Without the hash, that retry would create a duplicate note (T04 "no duplicates after restart").

### D-030 doctor corrects supersede consistency (an item not in the SPEC chapter 5 table)
- Decision: If the note pointed to by some note's `supersedes` is not yet `superseded`, fill in `status=superseded` and `superseded_by`. If there are two or more active notes with the same fact_key, only warn.
- Reason: If the process dies after writing the new note but before changing the old note, there are two active notes with the same key. The new note's frontmatter carries the intent, so finishing applying it fits "accuracy of current facts". It fixes only within the allowed changes.

### D-031 Targets for restoring external deletions are notes, all of `_system/`, `.gitignore`, and `core.md`
- Decision: Other deletions are only warned about and not restored. Among untracked files, `_system/` and `core.md` are committed, and the rest (including `.tmp-*`) are left untouched with a warning.
- Reason: The SPEC chapter 5 table names "note or `_system/` files" as restore targets. If the server also revived other files the user placed in the vault, it would be ignoring their intent to delete.

### D-032 For a truncated event log, scan back from the last line and move the entire non-JSON tail to a fragment
- Decision: Not only the last fragment without a newline, but also final lines that have newlines yet are not JSON are moved together to `runtime/recovery/<파일명>.<시각>.fragment` (<file name>.<time>). Broken lines in the middle are skipped when reading and left untouched.
- Reason: The fragment is moved, not deleted, so the original is preserved. Fixing middle lines would violate "existing lines are not modified" (3.4).

### D-033 Secret detection uses only format patterns, and the value is not carried in the error
- Decision: Reject only known formats such as `sk-`, `AKIA`, `-----BEGIN`, `AGE-SECRET-KEY-1`, GitHub/GitLab/Slack/Google/Stripe tokens, JWT, B2 app keys. Keyword heuristics such as "비밀번호:" ("password:") are not included. Only the pattern name is carried in the error.
- Reason: SPEC chapter 4 is about rejecting known formats and does not require complete detection. Keyword heuristics would block normal memories like "I changed my password" and cause missed saves (design principle 7). The age private key was included specifically because it is this system's recovery key.

### D-034 If doctor fails at server startup, only a log is left and the server still starts
- Decision: `startup_doctor` swallows exceptions and records them to stderr·log. If `.git` is missing, doctor records `VAULT_NOT_GIT`, and write tools check `.git` on every call and reject with the same code. Reads work.
- Reason: A diagnostic failure must not block recall too. Write blocking is rechecked at tool-call time, so it still blocks even if `.git` disappears after startup.

## Stages 2·3·4 (indexing, recall, core, rebuild, stats, backup, heat score, expansion)

### D-035 Assemble everything in a single composition root, `app.py`
- Decision: `build_app()` assembles config, DB, embedder, indexer, recaller, and store. The server, CLI and tests use the same function. A DB open failure is kept as `db_error`, and saving continues.
- Reason: To report save success, index success and backup success as distinct states (principle 6), the storage layer must stand even without the index layer.

### D-036 Tests use a deterministic fake embedder
- Decision: Test fixtures put `fake/ngram-hash` (character 2-gram hash, 64 dimensions) into config. Real model trials are done with `MEMORY_REAL_MODEL=1` and `scripts/run_t05.py`, `scripts/measure_t08.py`.
- Reason: More than 200 tests cannot load a 3GB model every time. The fake embedder verifies structure (filters, expansion, group ordering, rebuild identity), and T05 verifies quality.

### D-037 Add a `used_at` column to DB `memories`
- Decision: Add `used_at` to the columns in the SPEC 3.5 table. It is used for the 6-hour re-report check. `heat_at` is the time of the last heating (or creation).
- Reason: Right after creation `heat_at = created`, so heat_at alone cannot distinguish "whether it was already reported within 6 hours". The DB is a cache, so it is regenerated by rebuild.

### D-038 Vector search takes 200 KNN results, then filters to make the top 50
- Decision: Get 200 results with sqlite-vec KNN, apply status, tags, type to make up to 50, then cut by `min_similarity`.
- Reason: vec0's KNN ranks before filtering, so to guarantee 50 after filtering it must fetch generously. At personal scale (thousands of items), 200 is enough and the cost is small. If it grows, move to sqlite-vec's metadata filters.

### D-039 `SIMILAR_EXISTS` is judged at index time with 5 KNN results
- Decision: Right after embedding a new note, among the 5 nearest active notes, those with similarity 0.92 or higher are carried in the warning. If indexing fails, there is no warning either.
- Reason: The embedding is computed only once and used for both indexing and the similarity check.

### D-040 Input filters (tags, type) are applied identically to expansion paths
- Decision: Link·tag expansion candidates must also pass, in addition to active and `min_similarity`, the recall arguments' tags (AND) and type.
- Reason: If filters apply only to the vector path, expansion would mix in notes outside the filter and break the "AND condition that includes all".

### D-041 Tag expansion looks at notes that share at least one of the top 3 tags, ordered by share count
- Decision: From the vector results, pick the top 3 `주제`, `사람`, `프로젝트` tags by frequency, and take active notes having one or more of them, in descending order of number of shared tags and group-ordered within that, up to 5.
- Reason: Reading SPEC 7.2 "sharing 3" as "all three" yields almost no results. Using share count as the primary criterion naturally puts notes sharing all three first.

### D-042 core.json and core.md are overwritten (they are not notes)
- Decision: `update_core` replaces both files wholesale with a temporary file + rename and commits. The ids of the `core_updated` event contain the evidence IDs.
- Reason: SPEC 9 "previous versions remain in Git history". The prohibition on overwriting bodies is a rule for memory notes.

### D-043 The core character count is the document title + section headings that appear + the sum of entry text
- Decision: Add the lengths of `핵심 요약` (core summary; the document title), section headings that have entries, and each entry's text. Evidence IDs are not counted.
- Reason: The most natural reading of SPEC 9 "the total of titles and entry text is 1,500 characters or less".

### D-044 rebuild builds into a temporary DB, verifies it, then replaces with `os.replace`
- Decision: Index into `memory.db.rebuild-<난수>` (<random number>), confirm row count = vector count = index count and that index_meta exists, then replace. On failure, delete the temporary file and keep the existing DB. If rebuild is run while the server is on, the server must be restarted.
- Reason: The DB is a cache, so replacing it is allowed. Replacing before verification would make recall run on a half-built DB.

### D-045 `mark_used` commits a `used` event for each report
- Decision: Only when there are heated IDs, write one `used` event line and commit the event file. A re-report within 6 hours produces neither an event nor a commit.
- Reason: SPEC 8.1 "every change is committed to local Git". Thanks to the 6-hour rule, the number of commits is a few per day.

### D-046 Backup credentials are passed only via environment variables, not the rclone config file
- Decision: On macOS, read from the keychain with `security find-generic-password` and give them only to the rclone child process as `RCLONE_CONFIG_<REMOTE>_ACCOUNT/KEY` environment variables. Linux (CI) reads from environment variables.
- Reason: SPEC 8.3 "put the B2 key in the macOS keychain and only the reference name in the config file". rclone's own config file is merely obfuscated, so it is not created.

### D-047 Ciphertext that failed to upload is kept in `runtime/backup/pending/` and retried only when the hash matches
- Decision: Re-upload under the same name only when the file matches the sha256 in the state file. Even if a re-upload is verified, freshness changes only when `captured_at` is newer.
- Reason: SPEC 8.2 item 4 and T11 "re-upload does not update freshness".

### D-048 The restore drill date is kept in `backup_state.json`
- Decision: `memory restore-drill-done` writes `last_restore_drill` in `runtime/backup/backup_state.json`. It is not put in the vault.
- Reason: A drill is device·operational state, not a memory. Restoring to a new device requires drilling again, so it is right for it to stay in runtime.

### D-049 Because the server code is inside the snapshot, restore bootstraps in two steps
- Decision: RESTORE.md first extracts only `server/` with `age` and `tar` and runs `uv sync`, then unpacks the whole thing with that code's `memory restore`, going through safety checks (rejecting absolute paths, parent traversal, links; empty directory required).
- Reason: In a situation where the MacBook is lost, the server code must also come out of the snapshot. Safe extraction is guaranteed by the server code, not by standard tar.

### D-050 Based on T05 results, the default `min_similarity` is set to 0.45 and `relevance_calibrated = true`
- Decision: For bge-m3, the tunable range is 0.435–0.50, and the midpoint chosen by the script was 0.468. However, short queries without names ("회사 어디 다녀?" ("Where do you work?") = 0.45, "직장" ("workplace") = 0.425) fell below the middle of the range, so 0.45, at the low end of the range, was chosen. Evaluation queries are still correct 20/20 and irrelevant 10/10 at 0.45 (maximum top-1 irrelevant 0.423). e5-small is 0.83. If the model is changed, rerun `run_t05.py`.
- Reason: SPEC 7.1 "tune with the T05 queries and write it in config.toml". Missing a correct answer (a silent failure) is more harmful than mixing in one irrelevant note at similarity 0.43, and the model receives the similarity value along with it. Evaluation queries (20 correct, 10 irrelevant) were kept separate from tuning queries, but the notes and queries were made by the same person (the implementer), so the scores may be generous. If failure cases accumulate after real use, retune.

### D-051 Automate the installation·trial procedure with a `memory setup` wizard
- Decision: `memory setup` executes all of the human procedures in SETUP.md except B2 account sign-up·2FA·master key issuance, paper printing, and Claude Desktop conversations. The B2 bucket·lock·lifecycle·keys are created by calling the B2 native API directly (no external CLI dependency). The master key and age private key are taken with `getpass` and kept only in memory, and only the upload key is written to the keychain with `security -i` (stdin). `golive` runs only when its name is given explicitly.
- Reason: The user said they do not know this part, and the implementer is a cloud container so it cannot touch the user's MacBook·accounts. Receiving secret values through chat creates the risk that they remain in conversation storage and the repository commit path. The wizard handles secret values only inside the user's machine. The pure parts of the wizard (B2 API, config merging, plist, config editing) were tested, and the execution parts are verified only on the Mac.

### D-052 Preload the embedding model in the background right after server startup (`embedding.preload`, default true)
- Decision: After `memory serve` assembles the App, it calls `embedder.preload()` in a daemon thread. Even if it fails, it only logs and retries on the first call. Loading and encode are serialized with one lock. It can be turned off with `preload = false`.
- Reason: SPEC 2 said "load at the first recall or first indexing", and its intent is that a model loading failure must not block saving. Background loading keeps that property while removing the 20-second wait in the first conversation. This was the delay the user felt in T08. The difference from the SPEC wording is recorded in this entry.

### D-053 Split `store.py` into a package and reduce recall response fields to the list in SPEC 6
- Decision: `store/` = models (data types·limits), validation (validation), base (common·reads), writes (write tools), usage (mark_used). `MemoryStore` is a mixin composition, and the external import path (`memory_server.store.MemoryStore`) is unchanged. From recall items, `heat` and `created` are removed, and empty `tags`, `fact_key`, `warnings`, `pending_index` keys are omitted. The summary of duplicate rules in the recall·remember descriptions is kept only in remember.
- Reason: A single 700-line file is hard to read, and padding in responses and descriptions eats tokens in every conversation. `heat` is used only for ordering, and there is no reason for the model to see it. Only the fields set by SPEC 6 (id, title, tags, type, source, fact_key, status, similarity, path) were kept.
