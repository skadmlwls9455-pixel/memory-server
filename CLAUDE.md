# CLAUDE.md

_English translation of CLAUDE_KO.md. If the two differ, the Korean original is authoritative._

This repository implements SPEC.md (Personal AI Memory System Specification v0.3). SPEC.md is the source of truth, and this file records only working rules.

## Working rules

- Following the order in SPEC.md chapter 11, implement only the part the user instructed. Do not build the next stage ahead of time without instruction.
- All paths are relative to the environment variable `MEMORY_HOME`. Do not hardcode `~/memory` in the code.
- Tests use only temporary directories. Never touch the real `~/memory/vault`.
- Do not add delete tools, body overwriting, paid APIs, or the deferred items in SPEC chapter 10.
- When a decision is needed, write the decision and its reason in `DECISIONS.md`. The order of priority for judgment is: preserving the original, accuracy of current facts, recoverability.
- When a part is finished, report the tests run, failing items, and unimplemented items.
- stdout is for MCP only. Logs go to stderr and a file (`runtime/server.log`). Do not leave note bodies or search queries in logs.
