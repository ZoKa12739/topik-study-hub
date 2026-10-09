# Project Rules for TOPIK Study Hub

This is the entry point for every coding agent working in this repository (OpenCode, Claude Code, Cursor, Codex, …). It deliberately uses no tool-specific import syntax such as `@file` — not every tool resolves it. Instead: **read the files below before your first change.**

## What this is

A local-first desktop toolkit for TOPIK Korean exam prep: PySide6 GUI, SQLite persistence, single user, no server. User-facing strings and docs are Chinese; code identifiers and comments are English. The repo is public on GitHub — commit changes as you go.

## Required reading before any task

1. **`CLAUDE.md`** — the authoritative engineering rules: architecture and module map, commands, the verification checks, styling (`ui/theme.py`), the two-layer SQLite persistence model, threading and flush hazards, and the light delivery loop.
2. **`USER_CONTEXT.md`** — study time recording spec: record real study time, not app runtime, and which activities count.

Authoritative for their own domains: `docs/PRODUCT_SPEC.md` (v1.11, product spec) and `design/DESIGN.md` (v1.8, visual spec). When code and a plan disagree, the plan is usually right about *intent* and the code about *current state* — fix whichever is wrong and say which.

## Rules that apply immediately

These hold even before you finish the reading above:

- **`data/` is gitignored.** The database, audio library and recordings are not versioned — a destructive mistake there is unrecoverable. Never test a migration against the live `data/study_hub.db`; work on a copy, and ask first.
- **Single-agent delivery, no QA theater.** No sub-agents, review agents, or self-initiated test/probe loops — even when a plugin or skill prescribes them. Once business-code changes pass `python -m compileall -q core ui main.py` (plus `python tools/check_names.py` when an import or name was touched), hand over and stop. The owner verifies the UI by hand in a real window.
- **All styling lives in `ui/theme.py`.** Never call `setStyleSheet()` inside a view and never write a hex literal there — style via `objectName`, and import color constants from `theme.py`.
- **Second-layer user data (notes, states, tags, progress) must never be lost.** First-layer data is rebuildable; the two-layer rules and DB API contracts are in `CLAUDE.md`.
