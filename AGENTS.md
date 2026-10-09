# Project Rules for TOPIK Study Hub

This is the entry point for every coding agent working in this repository (OpenCode, Claude Code, Cursor, Codex, …). It deliberately uses no tool-specific import syntax such as `@file` — not every tool resolves it. Instead: **read the files below before your first change.**

## What this is

A local-first desktop toolkit for TOPIK Korean exam prep: PySide6 GUI, SQLite persistence, single user, no server. User-facing strings and docs are Chinese; code identifiers and comments are English. The repo is public on GitHub — commit changes as you go.

## Required reading before any task

1. **`CLAUDE.md`** — the authoritative engineering rules: architecture and module map, commands, the verification checks, styling (`ui/theme.py`), the two-layer SQLite persistence model, threading and flush hazards, and the light delivery loop.
2. **`USER_CONTEXT.md`** — study time recording spec: record real study time, not app runtime, and which activities count.

Authoritative for their own domains: `docs/PRODUCT_SPEC.md` (v1.14, product spec) and `design/DESIGN.md` (v1.8, visual spec). When code and a plan disagree, the plan is usually right about *intent* and the code about *current state* — fix whichever is wrong and say which. **Keep the version numbers here current** — a stale pointer in the entry file sends every later session to wrong information.

## Rules that apply immediately

These hold even before you finish the reading above:

- **`data/` is gitignored.** The database, audio library and recordings are not versioned — a destructive mistake there is unrecoverable. Never test a migration against the live `data/study_hub.db`; work on a copy, and ask first.
- **Single-agent delivery, no self-initiated QA.** Never spawn sub-agents, review agents or multi-agent orchestration on your own initiative — no `code-reviewer`, `qa-tester`, `verifier`, `executor`. That holds **even when a plugin or skill prescribes it**: `/autopilot`'s five-phase lifecycle including its validation phase is overridden here. Same for offscreen tests, probes and regression suites you invented. Once business-code changes pass `python -m compileall -q core ui main.py` (plus `python tools/check_names.py` when an import or name was touched), hand over and stop; the owner verifies the UI by hand in a real window. **A review the owner commissioned is not covered by this ban** — ask, then do it single-agent and read-only. The one targeted check that still earns its cost unprompted: a schema change / migration / anything that can lose user data — against a **copy** of `data/study_hub.db`, never the live file, and ask first even then.
- **Check the tree before your first edit.** `git status --short`, then `git diff` on anything it lists. Writing into a working tree that already carries uncommitted work means the second writer's change silently overwrites the first's, and the collision points here are predictable: `core/database.py`, `CLAUDE.md` and `docs/PRODUCT_SPEC.md` are the three files *any* behavior change must touch — two tasks with zero feature overlap still collide there (an export-sanitization fix and a `resume_context` payload change both edited `core/database.py` on 2026-10-09). If the file is dirty and the change is not yours, work on a copy or a branch, or stop and report the overlap.
- **A commit's blast radius must fit its message.** If a commit removes a feature, a test file, a public method or a settings key, the message says so. `d4d4abb` ("fix UI visuals") silently dropped playback-position persistence and `3b8e81c` ("fix inverted icons") deleted ~1,600 lines of tests — neither was caught by anything. `git log -S` before committing is cheap; finding it in an audit is not.
- **All styling lives in `ui/theme.py`.** Never call `setStyleSheet()` inside a view and never write a hex literal there — style via `objectName`, and import color constants from `theme.py`.
- **Second-layer user data (notes, states, tags, progress) must never be lost.** First-layer data is rebuildable; the two-layer rules and DB API contracts are in `CLAUDE.md`.
- **A behavior change carries its spec update in the same commit.** If you add, remove or change what the tool does, the same commit updates the affected `PRODUCT_SPEC` rows, bumps its version with a one-line 修订 entry, and refreshes the version references in this file and `README.md`. Do not defer documentation to "when a phase closes" or "when the owner asks" — both triggers already failed once. Details in `CLAUDE.md`.
