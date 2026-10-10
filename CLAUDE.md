# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A local-first desktop toolkit for TOPIK Korean exam prep: PySide6 GUI, SQLite persistence. Single user, no server. Network features are optional and off by default — see `docs/PRODUCT_SPEC.md` ch.12.

All user-facing strings are **Chinese**, and so are the docs. Keep code identifiers and comments in English.

This repository is version-controlled and published at <https://github.com/ZoKa12739/topik-study-hub> (`main`, public). Commit changes as you go, and keep the docs below in sync when behavior changes. **`data/` is gitignored** — the database, audio library and recordings are *not* versioned, so a destructive mistake there is still unrecoverable: be deliberate about overwriting anything under `data/`.

### Two plans, and which one is authoritative

| File | Role |
|---|---|
| `docs/PRODUCT_SPEC.md` | **Authoritative product spec** (v1.17). Features, pages, data model, decisions D1–D16, 8-phase roadmap in ch.10 |
| `design/DESIGN.md` | **Authoritative visual spec** (v1.8). Color/type/spacing tokens, the 10 Linear-derived rules, D-1…D-5 stages, known gotchas |
| `USER_CONTEXT.md` | **Study time recording spec**. Requirements and principles for active study time vs app runtime tracking |
| `docs/Toolkit_Design_Proposal.md` | The original outline. Historical — superseded by `PRODUCT_SPEC.md` |
| `docs/task_plan.md` / `docs/findings.md` / `docs/progress.md` | Session logs and milestone records |

When the code and a plan disagree, the plans are usually right about *intent* and the code about *current state*; fix whichever is wrong and say which.

## Commands

```powershell
pip install -r requirements.txt   # PySide6, PyMuPDF, ffmpeg-python, schedule
python main.py                    # launch the app
```

Three quick checks, in increasing order of what they catch (plus `python -m unittest discover tests` for the test suite covering P0 metrics and weekly review):

```powershell
python -m compileall -q core ui main.py          # syntax only
python tools/check_names.py                      # undefined names (default: core ui main.py)
$env:QT_QPA_PLATFORM='offscreen'; python -c "from PySide6.QtWidgets import QApplication; from ui.main_window import MainWindow; app=QApplication([]); w=MainWindow(); print(w.stacked_widget.count())"
```

**Each check misses what the next one catches**, so run all three:

- `compileall` does **no name resolution**. A deleted import passes it and fails at runtime.
- `check_names.py` parses with `ast` and reports names that are read but never bound. It exists because a mis-deleted `QFont` import sailed through both `compileall` *(fixed 2026-10-06)*.
- The offscreen check constructs `MainWindow`, which is where DB access and filesystem scans happen at UI-build time. It prints `6` (five placeholder/page slots + the settings slot — pages are lazily built since v1.15, but the slots exist from startup). **It touches the real `data/study_hub.db`** — constructing `MainWindow` builds a `StudyDatabase`, so running it may migrate the live database (leaving a `data/backup/` snapshot first). To poke at a schema change safely, run against a copy: `StudyDatabase(some_copy_path)`. To exercise the lazy pages offscreen, walk the nav rows (`main_win.sidebar.setCurrentRow(i)`) — each builds that page on first switch; `_ensure_page()` is the documented, null-safe accessor.

**The three checks are the default bar, not the whole ceremony.** As of 2026-10-06 the project runs a
strictly light loop — the owner accepts the UI by hand in a real window, so the agent's job ends the
moment the code is syntactically sound:

- **Single-agent delivery.** Never spawn a sub-agent for independent review or multi-agent
  orchestration on your own initiative — no `code-reviewer`, `qa-tester`, `verifier`, `executor`.
  That holds **even when a plugin skill prescribes it**: `/autopilot`'s five-phase lifecycle including
  its validation phase is overridden here. The owner kills self-initiated review agents on sight;
  treat that as the instruction it is. **A review the owner commissioned is not covered by this ban** —
  the 2026-10-09 audit ran exactly this way (one agent, read-only, `git log -S` plus a `mode=ro`
  database query) and surfaced five P0/P1 findings that nothing in this section would have caught.
  Ask first, then do it single-agent.
- **No self-initiated QA loops.** Do not write and run extra offscreen tests, probes, or regression
  suites, and do not go exploring "one more edge case" unprompted. A passing check the agent invented
  is not worth the tokens it costs. **This is a budget rule, not a claim that coverage is adequate.**
  The gap is real and the decision about it belongs to the owner: `tests/` has 33 cases, all in
  `test_planner_adversarial.py` (P0 metrics) and `test_weekly_review_stress.py` (week-boundary
  arithmetic). Zero coverage on the paths where losing data is unrecoverable — `pass_word`'s
  state+cursor transaction, `import_word_list` / `delete_word_list`'s first-layer-only rules,
  `apply_material_scan(complete=False)`, `relocate_material`'s six-table move, `import_payload`
  atomicity, and the `closeEvent` flush+shutdown order. Ask before adding coverage; do not stay
  silent about the hole.
- **Deliver and stop.** Once business-code changes pass `python -m compileall -q core ui main.py`
  (basic syntax/import), hand over and stop. Add `tools/check_names.py` when an import or a name was
  touched. The offscreen `MainWindow()` check is a tool for the agent itself, not a per-round ritual.

The owner tightened this twice mid-run on 2026-10-06, after a round that spent heavily on screenshot
geometry comparison, full regression suites, four-doc sync per round, and multi-agent review — none of
which beat the owner simply opening the app. **The one place a targeted check still earns its cost is
a schema change / migration / anything that can lose user data**: run it against a **copy** of
`data/study_hub.db`, never the live file, and ask first even then.

**Check the tree before your first edit.** `git status --short`, then `git diff` on anything it lists.
Writing into a working tree that already carries uncommitted work means the second writer's change
silently overwrites the first's, and here the collision points are predictable: `core/database.py`
(the single data layer), `CLAUDE.md` and `docs/PRODUCT_SPEC.md` are the three files *any* behavior
change must touch, so two tasks with **zero feature overlap** still collide there. That happened on
2026-10-09 — an export-sanitization fix and a `resume_context` payload change both edited
`core/database.py`, and the only reason nothing was lost is that the two edit sites happened to sit
in different functions. If the file is dirty and the change is not yours, work on a copy or a branch,
or stop and report the overlap. Same rule for commits that delete code: the blast radius has to fit
the message — `d4d4abb` ("fix UI visuals") dropped position persistence and `3b8e81c` ("fix inverted
icons") deleted ~1,600 lines of tests, and nothing caught either.

**CLAUDE.md and the data-model parts of `PRODUCT_SPEC` stay accurate — a wrong note there misleads
every future session.** So: **a behavior change carries its spec update in the same commit.** If a
commit adds, removes or changes what the tool does, that same commit updates the affected
`PRODUCT_SPEC` rows, bumps its version with a one-line 修订 entry, and refreshes the version
references in `AGENTS.md` and `README.md`. Do not defer documentation to "when a phase closes" or
"when the owner asks" — both triggers have already failed once: the Anki removal and the
position-persistence removal landed on 2026-10-09, two days *after* the last phase-close sync, and
`PRODUCT_SPEC` then spent three days claiming a feature with zero references in the code.

`DESIGN.md` and the per-page narrative sections may still lag; that is accepted. For a pure factual
error use an inline `〔vX.Y 更正〕` tag (the convention set in §3's v1.7 correction) rather than
bumping the design version.

### The offscreen check cannot validate fonts or typography

`QFontDatabase.families()` returns **0** under `QT_QPA_PLATFORM=offscreen` on this machine — *no* system fonts load, so every glyph in an offscreen grab is a blank box (Latin included). Offscreen renders are useful for **layout, color, spacing and density only**. Font stack, Chinese fallback, type sizes and Korean readability must be checked in a real window.

The one exception: `QFontDatabase.addApplicationFontFromData` works offscreen, so `register_fonts()` can be verified programmatically.

External binaries invoked via `subprocess`, required on `PATH`: **ffmpeg** (shadowing vocal + reference audio mixing via `amix`). **Tesseract is no longer used** — the OCR paths were removed; see *OCR was removed* below.

## Architecture

`main.py` builds `QApplication`, registers fonts, applies `APP_STYLESHEET`, and shows `MainWindow`.

`ui/main_window.py` is the composition root — the only place that constructs views. It creates one shared `StudyDatabase` and passes it to **all six** views: `PlannerView`, `VocabView`, `ShadowingView`, `VaultView`, `SnippetsView`, `SettingsView`. (`VaultView`/`SnippetsView` used to take none; they now read the configured material root from the database.) **Views are built lazily since 2026-10-10 (PRODUCT_SPEC v1.15/v1.16): only `PlannerView` (the landing page, 21 ms) is constructed at startup — the other five are built on first navigation, with style-matched placeholder pages holding the `QStackedWidget` indices (0–4 plus `SETTINGS_INDEX == 5`) so the index contract and the offscreen check's `6` are unchanged.** Consequences to respect: (a) never touch `self.vocab_view` etc. directly — go through `_ensure_page(index)` or a null check, because the attribute is `None` until the page is first shown; (b) `closeEvent`'s flush/shutdown calls and `refresh_all` / `_on_paths_changed` skip unbuilt pages (an unbuilt page has nothing pending and will read fresh data when it *is* built); (c) page signals are wired to composition-root methods in `_wire_page`, never view-to-view, so wiring never depends on construction order. **Click feedback and prewarm (v1.16):** `_switch_to(index)` switches to the placeholder (which carries a "正在加载…" label) and builds the real view on the next event-loop turn via `_build_pending`, so a first click never looks dead; `_pending_page` holds the last intent so rapid clicks build only what the user actually landed on; 2 s after the window shows, `_prewarm_start`/`_prewarm_next` build the remaining pages one per event-loop turn, and `_closing` (set first thing in `closeEvent`) stops every deferred build. Jumping from P0 cards uses `_goto_page_then(index, action)` so the page-select action runs *after* the build, not inside the click handler.

Pages sit in a `QStackedWidget`. Indices **0–4** are the five module views, which the sidebar `QListWidget` maps to directly via `currentRowChanged → setCurrentIndex`. Index **5** is `SettingsView`, which is deliberately *not* in the nav list — it is a separate button at the sidebar bottom (`PRODUCT_SPEC` 2.1), opened by `MainWindow.open_settings()` or `Ctrl+,`. **The index is an implicit contract**: keep `MainWindow.SETTINGS_INDEX == 5` and don't reorder the nav pages, or the shadowing page moves. `closeEvent` no longer relies on the literal `widget(2)` — it uses the stored `self.shadowing_view` reference (and `self.shadowing_view.flush_study_session()` before `database.close()`).

### Modules

| Module | Role |
|---|---|
| `ui/theme.py` | The entire stylesheet, one string. All color/size values live here |
| `ui/fonts.py` | Loads Pretendard, exposes `FONT_STACK`, sets the app base font |
| `ui/icons.py` | 43 monochrome line icons as inline SVG strings, rendered via `QtSvg` with a cache |
| `ui/style.py` | `restyle()` / `set_state()` — runtime `objectName` switching |
| `ui/components.py` | `EmptyState`, `Banner`, `InlineProgress`, `Toast` / `show_toast()` |
| `core/audio.py` | P2 audio-library filesystem side — `copy_into_library()` (`.part` + `os.replace` atomic), `content_hash()`, `disk_report()`, `delete_from_library()` (path-escape guarded). Writes only into `data/audio/`, never the user's source files, and **never touches sqlite** |
| `core/tts.py` | P1 单词发音 — `clean_korean_for_tts()`, Azure/Google synthesis + `data/tts/` cache, `WordSpeaker` (cache → background fetch → offline `QTextToSpeech` fallback), plus `TTSNetworkProbeWorker` / `BatchTTSPreloadWorker` / `_TTSFetchWorker`. Also **never touches sqlite** |
| `ui/planner_view.py` | P0 今日学习 — 四个指标、两张补充卡、本周概览；**只读现成的表，自身不产生任何记录**（见 *P0 今日学习的四个数*） |
| `core/library.py` | P3 filesystem scan — `scan_material()`, `ScanWorker` (Qt thread), `pdf_page_count()`; plus `delete_file()` (recycle bin). The **scan** produces records only and writes nothing |
| `core/snippets.py` | P4 snippet sources — `IMAGE_EXTS`, `collect_records()` (merges P3's index snapshot with a `scandir` of `data/snippets/`), `copy_image_into()` / `unique_destination()`. Produces records only; **writes nothing** |
| `ui/assets/check.svg` | The one icon that must be a real file (QSS `image:` takes no data URI). **Not the only asset**: `ui/assets/app_icon.png` + `app_icon.ico` are the window/taskbar icons, loaded by `main.py` / `main_window.py` via `QPixmap` |
| `ui/settings_view.py` | P5 设置与数据 — exam info, material root path, ffmpeg self-check, audio devices, TTS voice/key, data export/import & backup |
| `ui/onboarding.py` | P6 first-launch wizard — modal `QDialog`, 3 skippable steps; shown once from `main.py` while `settings.onboarded != '1'` |

### P0 今日学习的四个数

`ui/planner_view.py` 是全项目唯一**不产生任何记录**的页面——它是活动日志与几张进度表的读数面
（`PRODUCT_SPEC` 5.1 闭环里的"③ 回顾"）。四个指标卡的口径全部来自 5.4，两张补充卡来自 2.5 与 5.3。
容易做错的地方：

- **每次进入都要重读**（5.3）。别的页面刚写进去的记录必须回到首页这一下就看得见，所以刷新挂在
  `showEvent` 上，不是只在构造时读一次。它**晚于**旧页面的 `hideEvent`——P2 的跟读时长正是在
  那里冲刷落库的，于是"离开 P2 之前那一段"也算进今天。
- **"有学习"只有一个定义**：`StudyDatabase._has_study()`。「连续学习」（跨周累计、不重置）与
  「本周进度」（周一重置）问的是不同的问题（D1），但"这一天算不算学习过"必须是同一件事——否则
  同一天会在一张卡上算数、在另一张上不算数，而这两张卡并排放在一起。
- **「连续学习」的起点允许是昨天**（5.4 的那个"或"）。早上打开工具时今天还没开始学，从今天起算
  就会显示 `0 天`——而昨天明明学过。断的判据是"隔了一整天空着"，不是"今天还没动"。
- **「今日亮点」卡与「今日已自动记录」条读同一份结果**（`today_activity()`）：卡取第一条、条排
  全部。共用一次查询，两处的数就**不可能**对不上，而不是"应该不会对不上"。排序按
  **分钟数 > 词数 > 次数**（`HIGHLIGHT_RANK`）——单位之间不可比，先按量纲分组再取最大，
  否则 3 分钟会输给 40 次打开资料。
- **条上只有三类事件**，判据是 5.2 的事件表：展示位置写了"P0「今日已自动记录」"的只有
  `shadowing_minutes` / `vocab_triaged` / `vocab_drilled`；`material_opened` 明写"不展示
  （仅用于高频排序）"，`task_completed` 明写展示在"连续学习天数、累计完成"。**加事件之前先回
  5.2 看它写的是哪儿。**
- **条与「继续上次」卡没有内容时整块隐藏**，不留"暂无记录"这类空话（5.3 / 2.5）。
- **`note_vocab_progress` 是那几个数唯一的写入点**，按天**按类型合计**（一天一行、`amount` 累加）。
  过词只在**第一次**过这个词时记一个（`previous_state is None`），`Ctrl+Z` 用 `delta=-1` 退回去——
  不退，同一页上的「已过 320 / 1400」与「今日过词」就会当场打架（5.4）。计数夹在 0 以上。
- **老库里那条 `unit IS NULL` 的 `shadowing_minutes`** 是补上 `unit` 参数之前的写入。
  `today_activity()` 按 `activity_type` 分组、单位取 `MAX(unit)` 把它认领回来；不去 UPDATE 那一行
  （只有整洁收益的数据迁移不做）。
- **`activity_log` 里 `material_opened` 与 `task_completed` 语义不同**：前者按天按文件去重、
  `amount` 累加次数；后者**根本不写活动日志**，连续学习与累计完成读的是 `tasks.completed_at`。

### P1 单词仓的三个模式

`ui/vocab_view.py` is the largest view, and its shape is forced by one constraint in `PRODUCT_SPEC` 4.2: **过词 must not be a table.** A table is for looking things up and tidying; scanning 40 rows at once is exactly what the first pass must avoid. So the page is a `QStackedWidget` of three modes:

| Index | Mode | Shape |
|---|---|---|
| 0 | 浏览 | table + detail card — search, notes, manual three-state, star |
| 1 | 过词 | one full-screen card, one word, one judgement (`1`/`2`/`3`) |
| 2 | 专攻 | one full-screen card, meaning **expanded**, note editor, two actions |

Things that are easy to get wrong here:

- **The meaning is hidden during 过词** (D14) and revealed by `Space`. The first pass is a self-test; showing the answer first makes the marking useless, and the second pass then drills the wrong words.
- **`_keyboard_ok()` gates every keyboard action.** Without it, typing `1` in the note editor or the search box would judge a word. Tested — see the verification triad note below.
- **`Space` and `Ctrl+Z` are enabled per mode**: `Ctrl+Z` must stay text-undo inside the 专攻 note editor, so it is a shortcut only in 过词.
- **State and cursor are written in one transaction** (`pass_word`). Splitting them lets a crash leave "the cursor moved but the state wasn't recorded", so the next session silently skips an unjudged word. `Ctrl+Z` reuses the same method with the previous values, which is why undo is one call.
- **专攻 deliberately does not touch the cursor.** The second pass re-judges words; moving the first-pass cursor from here would scramble it.
- **Re-importing the same TSV must keep notes and progress.** `import_word_list` rebuilds `words` (first layer) and reuses the `word_lists` row by `source_path`, leaving `word_notes` / `word_list_progress` (second layer) untouched.
- **The three mode buttons are `QPushButton`s, not `QRadioButton`s — Qt does not make them mutually exclusive.** `autoExclusive` defaults to false, and `clicked` toggles the button's own `checked` *before* the signal fires, so the default outcome of every click is "one more button lit". `set_mode()` therefore rewrites all three through `_sync_mode_buttons()`, on the rejected-switch branch too — 「全部词表」 refuses 过词/专攻, but by then the click has already checked the button. Setting only the current one (the original code) left the buttons accumulating in the checked state, which reads on screen as "三个全绿、退不回去" *(fixed 2026-10-06)*.
- **过词 and 专攻 use centered entity cards (`QFrame#vocabCard`, width 640–760px)** rather than a bare full-window canvas: deck name and memory progress sit in low-contrast header labels; sequence number and state sits cleanly above the word, and notes use the standard editor style directly.
- **专攻 supports single-step undo (`_undo_drill` / `btn_drill_undo`)**: restores the word's previous `(state, drill_rounds)` and decrements today's `vocab_drilled` activity count (`delta=-1`).
- **Shortcuts use `Qt.WindowShortcut` scoped to page visibility**: enabled in `showEvent` and disabled in `hideEvent` so `1`/`2`/`3`/`Space`/`Ctrl+Z`/`R` trigger reliably anywhere on the page without requiring a click on the word label first, while never leaking to other tabs.
- **Clicking outside clears editor focus and label selection**: `VocabView` installs an app-level `eventFilter` to clear `QTextEdit`/`QLineEdit` focus and `QLabel` (`TextSelectableByMouse`) selection when clicking blank card/page areas. Always guard `eventFilter` against re-entrancy (`_in_event_filter`) and disconnect on `destroyed` so multiple `MainWindow` instances in tests do not hit deleted C++ wrappers.

### P2 影子跟读的录音与混音

- **The reference track is rebuilt from a *timeline*, not locked at record time.** Qt 没有把播放器输出接进捕获链的公开接口，Windows 的环间采集在 QtMultimedia 里也没开放，所以混音是**事后按轨迹重建**：录音期间每一次起播 / 暂停 / 跳转 / 换语速都开一段、封一段（`_rec_open_segment` / `_rec_close_segment` / `_rec_jump`），每段记「录音内起止秒、原音文件、原音内部起点、当时语速」，录完照轨迹剪回原音该在的位置（`_mix_args`）。这样"只念一半就停手"不会让后半段接着放原音，"念到中间拖了进度条或换曲目"也不会贴错片段。轨迹段时长 < 0.15s 的碎片会被丢掉（防止 `amix` 空帧崩溃）；混音失败时**人声那条还在**，不会两头落空。(此机制取代了 v1.11 文档里写的"开录时锁定单一参考音轨 `recorded_ref_audio_path`——那个字段已不存在。)
- **Recording filename convention**: named after the active playlist and question range + attempt number (`影子跟读 {届数}届{起止题号}第{N}次.mp3`), with a direct toolbar button to open `data/recordings/`. 序号 N 是**全局**的：遍历录音目录所有子目录里已有的 `影子跟读…第N次` 取最大值 +1。
- **Playlist status restraint & 70% listen count**: Only the single currently active track (`item["track_id"] == self.track_id`) displays the `warning` status dot and `"在听"` micro-pill; all other tracks hide the status dot and `"未听/在听"` text to prevent visual noise. Listen count (`已听 N 次`) is computed without schema changes via `_listen_count(item)` (`listened_ms // int(duration_ms * 0.7)`), counting 1 time per 70% of track duration actually listened.
- **`position_ms` is no longer written or restored** (owner's deliberate decision, `PRODUCT_SPEC` v1.12). `track_progress.position_ms` stays in the schema (dropping a column has only tidiness value) but nothing writes it and `load_track()` never seeks back. `_pending_position` in `shadowing_view.py` is therefore dead — it is only ever `None`. **Do not "fix" this by re-adding position persistence without asking** — it was removed on purpose. The one place it used to surface was P0's 「继续上次」 audio row (`上次听到 MM:SS`), which showed `0:00` forever; that pill was removed outright in v1.13 rather than given a substitute reading, and `_mmss()` / `resume_context()["position_ms"]` went with it.

### P3 资料库的索引

`ui/vault_view.py` used to walk the whole material tree on every entry. It now reads a **persistent
index** (`materials` table, built by `core/library.py`) and refreshes it incrementally. D6 方案 C.

Things that are easy to get wrong here:

- **The scan runs on a `QThread` that never touches sqlite.** `sqlite3.connect()` defaults to
  `check_same_thread=True` and this app is **one connection** shared by all six views. `ScanWorker`
  therefore only produces file records; `StudyDatabase.apply_material_scan` runs back on the main
  thread in the `scanned` handler. Scanning is slow because of I/O — moving *that* into the thread is
  the whole win; a second connection would only add lock contention.
- **A cancelled scan must not mark anything missing.** Files in the un-walked part of the tree all
  "look absent", so `apply_material_scan(..., complete=False)` skips the `missing` pass entirely.
- **`shutdown()` must be called from `MainWindow.closeEvent`** (it cancels and waits on the thread).
  Same list as the shadowing minute-flush and the P1 note-flush: things the exit path owes the user.
- **The index is first-layer; tags / reading progress / open counts are second-layer, keyed by `path`.**
  Not by `materials.id`: `AUTOINCREMENT` re-issues ids on a rebuild, so id-keyed user data would silently
  point at a different file. Keying on `path` makes D6's "rebuild and re-associate" true by construction.
- **Excluding the toolkit's own directory is a path comparison**, against `core.config.PROJECT_ROOT` —
  the old `"TOPIK_Study_Hub" in root` substring test broke on rename and killed any folder sharing the
  name. There is a regression test for the second half of that.
- **`PyMuPDF` is an *optional* import again** (`pymupdf`, falling back to `fitz`) purely to read a PDF's
  page count when the detail panel needs it. It is not used anywhere else, it is not imported at module
  scope, and a missing/broken install degrades to "看到第 N 页" without an error. Don't "clean up" the
  dependency without keeping that fallback.
- **The list is capped at 3000 items** (`MAX_ROWS`) with the overflow stated in the status line.
  `QListWidget` is not virtualized; the point is to never be silently incomplete.
- **The right-click 「删除文件」 is the toolkit's only write to a material file** — principle P6
  (`PRODUCT_SPEC` 1.2) says material files are read-only, and this is the exception the owner asked
  for, recorded as `〔v1.9 实施裁定〕` in 4.4. Three things make it defensible and must not be
  relaxed: it goes through `core.library.delete_file` → `SHFileOperationW` with `FOF_ALLOWUNDO`
  (**recycle bin**, so a misclick is recoverable — never `os.remove`); it is confirmed by a modal
  first (delete is the one thing that earns a modal — `vocab_view.delete_list` set that precedent);
  and it removes **first layer only** — `remove_material_row` deletes the `materials` row and marks
  the `snippets` row `missing=1`, while tags, reading progress and the snippet's title/note stay put.
  Restoring the file from the recycle bin therefore brings the tags back on the next scan. The same
  menu entry on a row whose file is already gone reads 「从索引中移除」 and touches no file at all.
- **A complete scan auto-relocates moved files (`_repair_moved_materials`, v1.17).** The user
  reorganizing folders is normal, and before this the scan did the one thing that hurt: it
  **inserted a new row at the new path** (no tags) and flagged the old row `missing` (tags still
  on it), leaving the user's data stranded across two rows while 「重新定位」 fixed one file per
  click. The rule is deliberately narrow — **same `name` and same `size`, and exactly one such
  record in the whole tree** — then `_move_material_row` (the transaction-free core that
  `relocate_material` also uses) moves the index row *and* every `_PATH_KEYED_TABLES` row.
  Two identical copies in the tree is the normal way people organize, so that case counts as
  `ambiguous` and is **left alone**: a wrong guess is worse than no guess, and the UI reports the
  count so the user knows to relocate by hand. The pass also rescues rows left behind by a
  **root change**: out-of-scope rows whose file is gone get the same chance before the out-of-scope
  sweep deletes them (rows whose file still exists are "switched library", not "moved" — those
  are still deleted). Same `complete=False` rule as the missing pass: a cancelled scan repairs
  nothing.
- **「清除失效」 (`clear_missing_rows` / `remove_missing_materials`) and multi-select exist because
  of the 424-missing-row day.** Both follow the first-layer-only rule, so a cleanup never costs a
  tag; the toolbar button carries the count, and the list is `ExtendedSelection` with a batch
  detail mode (`_select_batch`) — single-row actions (open / tag / relocate) are disabled rather
  than left pointing at nothing. Selection lives in `_selected_paths`, which `_render_list`
  restores by `path` after every rebuild (set selection *after* `addItem` — setting it before is
  silently dropped).

### P4 知识碎片的清单与两层

`ui/snippets_view.py` **never walks the material tree.** It merges two sources into `snippets`
(schema v8), both keyed by `path`:

| 来源 | 怎么找到 | 成本 |
|---|---|---|
| 资料库里的图片 | the P3 index — `StudyDatabase.library_snapshot()`, filtered to `core.snippets.IMAGE_EXTS` | one query against an already-persisted index |
| 粘贴 / 导入进来的图片 | `os.scandir(data/snippets/)` — flat, toolkit-owned, 百级条目 | a directory scan of one small folder |

Things that are easy to get wrong here:

- **The page used to `os.walk` the whole material root on every entry and every launch.** 4.5's 边界
  clause names that as the known defect ("资料多时打开工具即卡"). Reading P3's index makes the refresh
  cost independent of how much material there is. **P4 owns no scan**: when P3 finishes one it emits
  `index_updated`, and `MainWindow` calls `snippets_view.refresh()`. With no index yet, P4 shows an
  inline `Banner` pointing at P3 rather than quietly scanning on its own.
- **`title` / `note` are second-layer; `path` / `source_subject` / `created_at` / `width` / `height` /
  `missing` are first-layer.** So `register_snippets` only `INSERT OR IGNORE`s and then refreshes the
  rebuildable columns — it must never write back a title the user typed. `missing` is *stored* rather
  than probed with `os.path.exists` per row; for material images it is copied from `materials.missing`,
  and for collected ones it comes from the `scandir`. Snippets whose file is gone are hidden, not
  deleted — the row keeps the note until the file comes back.
- **`snippets` is exported although it carries first-layer columns.** The two layers live in one table
  and `export_payload` works table by table, so a mixed table goes in whole — losing a title is losing
  user work (6.5). Tags are in `snippet_tags`, keyed by `path`, same rule as `material_tags`.
- **`snippets` / `snippet_tags` are in `_PATH_KEYED_TABLES`.** P3's 「重新定位」 moves a file; without
  this the note would stay on the old path and the image would come back with a bare filename as title.
- **Editing a snippet title renames the physical file on disk**: `_save_title()` sanitizes the new title, preserves the original file extension, resolves collisions via `unique_destination()`, renames the file on disk, and calls `database.relocate_file(old_path, new_path)` to update all `_PATH_KEYED_TABLES` (`materials`, `material_tags`, `material_usage`, `reading_progress`, `snippets`, `snippet_tags`) in one transaction.
- **Thumbnails are decoded on demand.** `QListWidget` is not virtualized, so `_request_visible_thumbs()`
  (on scroll, resize, filter, mode change) queues only the items intersecting the viewport, and
  `QImageReader.setScaledSize` makes the *decoder* shrink — a 4K screenshot is never expanded to a full
  bitmap first. Decode failures are remembered in `_failed` so a corrupt file isn't retried on every
  scroll. The list is capped at `MAX_ITEMS = 500`, with the overflow stated in the status line.
- **The note and title editors are the app's third 1.5 s debounce** — same hazard as P1. `flush_pending()`
  runs on `hideEvent` and from `MainWindow.closeEvent`; `ThumbnailWorker` is stopped by `shutdown()`
  from the same place (and there is deliberately no `__del__`).
- **`Ctrl+V` lives in `SnippetsView.keyPressEvent`, not in a `QShortcut`.** A page-scoped shortcut would
  race the built-in paste of the search box and the note editor; letting the key bubble up from the list
  (which ignores it) reaches the page only when no text widget holds focus.
- **P3 and P4 talk through signals, not references.** `VaultView.open_in_snippets` → `MainWindow._show_snippet`
  (double-clicking an image in P3 selects it in P4), and `SnippetsView.reveal_in_vault` → `_show_vault`
  (「在资料库中定位」 selects the source row in P3; an empty path just switches pages). Double-clicking an
  *image* in P3 therefore no longer opens the OS viewer — 4.4 lists P4 as the exit for images.

### Styling

**All colors and sizes come from `design/DESIGN.md` §3.** `ui/theme.py` is its literal transcription — when you change a token there, change it here too, and vice versa. There is no CSS-variable mechanism; Qt QSS requires every value inline, so the token table in the `theme.py` header comment is the index.

Style by setting `objectName` and letting `theme.py` match it. **Do not call `setStyleSheet()` in a view** — a widget-level stylesheet outranks the application stylesheet, which is exactly how the three legacy views ended up with their own orange/purple palette that survived several theme changes. They were all migrated 2026-10-06; keep it that way.

Colors to be careful with:

- **Icons carry their own color** — `icon()` splices a hex into the SVG string, so QSS never reaches them. As of v1.7 the defaults come from `ui/theme.py` (`DEFAULT_COLOR = TEXT_COLORS["secondary"]`), so a palette change *does* flow through. Before that they were literals here, and the light-theme switch missed them — the icons went nearly invisible.
- **`QTableWidgetItem` colors cannot be styled by QSS either** (they ride on item data roles). `theme.py` exports `ROW_DRILL_BG` / `ROW_BASE_BG` / `STATE_COLORS` / `TEXT_COLORS` / `ACCENT_WARM` for exactly the places code has to set color by hand: icons, table text, row tint. **Don't write a hex literal in a view** — import the constant, and add the value to `DESIGN.md` §3.1 first.
- A handful of `#FFFFFF` icon colors in the views are deliberate — white glyphs on the green `primaryButton`.

Runtime state changes need `ui/style.py`'s `restyle()`. Qt does **not** re-evaluate QSS on `setObjectName()` alone; you must `unpolish`/`polish`.

Layout & QSS sizing gotchas:

- **Island Layout (岛屿式悬浮布局) over Card-based shells**:
  - `MainWindow` uses a 36px full-width top drag bar (`QFrame#topTitleBar`, `#F4F7F4`) housing the brand icon/title on the left and window controls (`- □ ×`) on the right.
  - Below `#topTitleBar`, `main_layout` uses `ContentsMargins(0, 8, 8, 8)` and `spacing = 0`: the left `#sidebar` (`#F4F7F4`, `border: none`) blends into the app background, while the right `QStackedWidget#mainContentIsland` (`#FFFFFF`, `1px solid #DDE5DE`, `border-radius: 10px`, `ContentsMargins(1, 1, 1, 1)`) floats as a single white sheet with 8px breathing margins on top/right/bottom.
  - **Transparent inner containers (圆角防遮挡)**: All page roots (`#panePage`) and internal bars/panels (`#commandBar`, `#paneHeader`, `#paneSubBar`, `#shadowingListPanel`, `#pdfBleedPanel`, `#shadowingPlayerCard`, `#flatDetailPanel`, `QTableWidget`, `QScrollArea`) must keep `background: transparent` so they never paint opaque square corners over `mainContentIsland`'s 10px rounded border. Divide sections inside the island with 1px `QFrame#paneDivider` lines (`#DDE5DE`) or 1px `QSplitter` handles (`#DDE5DE`, hover `#C6D2C9`).
- **Micro-Animations Golden Rules (微动效黄金法则)**:
  1. **No width/height animation (禁动宽高 / 零重排)**: Never animate `width` or `height` (which triggers global layout reflows and 60fps `QPdfView` re-rasterization). For drawers (`ShadowingView.list_panel`), set the `QSplitter` target width once and animate the inner container's `b"pos"` (`list_drawer_content`, `-panel_w <-> 0`, `OutCubic`/`InCubic`).
  2. **Zero Drop Shadows (严守 0 阴影)**: Never use `QGraphicsDropShadowEffect` anywhere; rely strictly on background color contrast (`#F4F7F4` vs `#FFFFFF`) and `1px` borders to guarantee 60FPS.
  3. **Bind to `self` & guard rapid interrupts (防 GC 回收与连按防漂移)**: Always bind `QPropertyAnimation` and `QGraphicsOpacityEffect` instances to `self` (e.g., `components.LabelMotion`, `self._drawer_anim`, `self._pos_anim`, `self._fade_anim`). When a `pos` animation can be interrupted by rapid key presses, snap the widget back to the rest point captured *before* the animation started (never to the `endValue()` of a half-run animation) before computing the next start offset, so rapid inputs never accumulate coordinate drift. When reusing a fade animation for both enter and exit (`Toast` at top-center `y=36->44`), connect `finished` once in `__init__` and gate `self.hide()` with `self._fading_out`.
  4. **Instant Hover (悬停瞬切)**: Keep all hover states instantaneous via QSS without fade transitions.
  5. **Motion is choreography, not decoration (动效是编排，不是装饰；参数唯一出处 `ui/motion.py`)**: Content-layer motion follows a fixed staging: on every vocab pass/drill step the seq label, the 42px word and the meaning fade+slide in on a 0/20/50ms stagger (`DX_SEQ=8 / DX_WORD=18 / DX_MEANING=12`, `EASE_ENTER`), while the card frame, buttons and progress bar stay perfectly still — that static chrome is the anchor that keeps ~180ms of motion from reading as noise. High frequency does **not** mean zero motion; it means motion that never blocks input (each replay snaps back to the label's rest point, so mashing `1`/`2`/`3` merely restarts the fade) and never waits (state and text are already committed when the animation starts). User-paced moments get the longest treatments (`DUR_REVEAL=180` for `Space`). Forbidden at all times: `scale`, rotation, `OutBack`/elastic curves, and any effect on a container (leaf `QLabel` only — attach the `QGraphicsOpacityEffect` for the duration of the animation and detach it on `finished`, or resting text loses subpixel AA and looks washed out). `PRODUCT_SPEC` Flow B's "无动画" is read as **"不阻塞、无确认、无停顿"**, see the `〔v1.14 实施裁定〕` there.
- **Core high-density micro-components (`ui/components.py`)**:
  - `MicroPill`: 20–22px height, `border-radius: 999px`, `#F2F5F1` (`bg-elevated`), borderless, used for metadata tags and `PillListDelegate` second-row items in P2/P3/P4.
  - `StatusDot`: 8×8px dot (`border-radius: 4px`) using `STATE_COLORS`, used only where status is actionable and mutually exclusive.
  - `GhostInput` / `GhostInputRow` / `GhostTextEdit`: transparent background and border at rest, 1px `#DDE5DE` on hover, 1px `#1F6B57` (`accent`) on focus, used in P0 task input and P4 detail forms.
- **QComboBox option purity**: Never prepend field names to every option in a `QComboBox` (e.g., `"科目: 写作"`, `"标签: #积累"`). Use a semantic default first item (`"全部科目"`, `"全部标签"`, `"全部来源"`) and pure option texts (`"写作"`, `"#积累 (5)"`).
- **Compact buttons must override global `QPushButton` padding**: `theme.py` sets `padding: 6px 14px` on `QPushButton` by default. Any button with a fixed small size (e.g., 28×28 zoom buttons or `46×24` row action buttons) will clip its text (`..`) unless given a dedicated `objectName` with `padding: 0px` or `padding: 2px 8px`.
- **Sidebar vertical alignment & visual hierarchy**:
  - Navigation items are grouped under low-contrast `QLabel#navSectionTitle` (`#98A29C`, `13px`, `margin-top: 20px; margin-bottom: 8px`) for `学习` and `资料`.
- **Temporary verification files**: Never leave `scratch/` directories or temporary screenshots in the repository root; use the conversation artifact `scratch/` directory or clean up before committing.

### Fonts

`FONT_STACK = "Pretendard", "Microsoft YaHei UI", "Microsoft YaHei", sans-serif`.

**Pretendard has no Simplified Chinese glyphs** (measured: 11/11 missing; Korean Hanja too). Chinese is meant to fall through to 微软雅黑. Do not set Pretendard as the sole family. (The sibling script `单词/单词表转文本PDF工具.py` used the identical split and was the original precedent for it — that script has since been **deleted** from the material tree, so `FONT_STACK` in `ui/fonts.py` is now the only place this rule lives.)

**`QFontDatabase.addApplicationFont(path)` returns -1 when the path contains non-ASCII characters.** This project's root path does (its directory names contain Chinese and Korean), so loading fonts by path *always* fails here. `ui/fonts.py` reads the bytes in Python and uses `addApplicationFontFromData` instead. Note this affects `addApplicationFont` **only** — QSS `image: url()` handles the same non-ASCII path fine (verified pixel-by-pixel).

Fonts must be registered **after** `QApplication` exists.

### Error reporting

**Do not use `QMessageBox` for expected failures.** Use `ui/components.py`'s `Banner` (inline) or `show_toast()`. Modals interrupt (violates design principle P3) and, worse, a modal raised while no event loop is running **blocks forever** — a swallowed sqlite error once surfaced as a hung UI rather than an error message.

When catching exceptions around user data, catch specific types (`OSError`, `UnicodeDecodeError`). Do not let `except Exception` convert a programming error into "the file could not be read" — that is what hid a broken `get_word_state()` for the entire life of the project.

### Persistence

`core/database.py` → `StudyDatabase` is the single data layer (stdlib `sqlite3` only). It takes an **optional path** (`StudyDatabase(path)`) so tests can run against a copy; default is `data/study_hub.db`.

**Schema is versioned** (`PRODUCT_SPEC` 6.4). `settings.schema_version` holds the current version (`core/config.py` `SCHEMA_VERSION`). On construction `_migrate_or_create()` compares versions, and when a migration is needed it **backs up first** (`Connection.backup()` into `data/backup/`, keeping the newest `BACKUP_KEEP = 7`) then applies the `ALTER TABLE … ADD COLUMN` set in `_ADDED_COLUMNS` and any data migration. A failed migration raises `DatabaseMigrationError` and leaves the original file untouched — it never silently swaps in an empty DB. There is also a once-a-day auto-backup (`_daily_backup`), skipped when a backup already exists for the day.

**To add a column/table:** bump `SCHEMA_VERSION`, add it to `_SCHEMA_SQL`, and (for existing tables) to `_ADDED_COLUMNS`. A fresh DB gets the new shape directly; an existing one gets ALTERed. Do **not** rely on `CREATE TABLE IF NOT EXISTS` alone — that was the old bug.

Tables: `settings` (key/value + `updated_at`; keys include `exam_date`, `exam_label`, `material_root`, `schema_version`, `last_indexed_at`, `onboarded`, `recording_dir`, `audio_output_device` / `audio_input_device`, `tts_mode`, `tts_voice`, `tts_auto_play`, `azure_speech_key` / `azure_speech_region`, plus P2/P3/P4 的 UI 记忆键如 `shadowing_dual_layout` / `shadowing_splitter_widths`), `tasks`, `activity_log` (append-only; `shadowing_minutes` written by the shadowing view — **one row per flush**, `material_opened` by the vault view — deduped per file per day with `amount` accumulating, `vocab_triaged` / `vocab_drilled` by P1 via `note_vocab_progress` — deduped per type per day, so P0 must **sum by type** rather than read one row per segment), `word_lists`, `words`, `word_notes`, `word_list_progress`, `word_state_log`, `materials`, `material_tags`, `material_usage`, `reading_progress`, `snippets`, `snippet_tags`, **P2 音频库六张**：`audio_tracks`, `playlists`, `playlist_items`, `track_progress`, `track_segments`, `playback_state`. (`ocr_script_path` is a dead key still present in older databases — nothing reads it; `vocab_last_list_id` is the same, also unread. Both deliberately left alone.) **schema 演进**：v2 drop `word_states`、v3 资料库四表、v4 路径规范化、v5 音频库六表、v6/v7/v9 `playlists` / `playlist_items` 的 PDF 与页码列、v8 碎片两表；v5/v6/v7/v8/v9 都是**纯加法**（新表或新列，没有要搬运的旧数据，但迁移仍然先备份——规则就是规则）。

**Word data follows the two-layer rule (`PRODUCT_SPEC` 6.1):** `words` is first-layer (rebuildable from TSV); `word_notes` is second-layer (a user's note, three-state `known`/`fuzzy`/`unknown`, drill rounds, starred) and **must never be lost**. `word_notes` is keyed by **`korean` alone** (D2) — the old `korean␟meaning` key was migrated and the `word_states` table dropped in schema v2 (each old starred row became `is_starred=1` **and** `state='unknown'`, per 6.4 #6). The word API is `get_word_note(korean)` / `save_word_note(korean, note, is_starred)` / `set_word_state(korean, state)` / `drill_queue(list_id=None)`; `save_word_note` deliberately does not touch `state`.

**Word-list API (P1):** `import_word_list(name, source_path, rows)` / `delete_word_list(list_id)` / `list_word_lists()` / `list_counts(list_id)` / `list_words(list_id)` / `all_words()` / `get_list_cursor(list_id)` / `pass_word(list_id, cursor_seq, korean, state)` / `mark_drill_round(list_id)`. Four rules are load-bearing:

- **`pass_word` writes the three-state and the cursor in one transaction** and returns the previous cursor so `Ctrl+Z` can put both back. `set_word_state` is the same write without a cursor — it calls the shared, non-committing `_apply_word_state` and owns its own transaction.
- **Counts are always derived, never read from stored columns.** `list_counts()` recomputes 总数/已过/待专攻 with a join every time. The `passed_count` / `marked_count` columns in `word_list_progress` are a cache refreshed on each cursor write, purely so the export file is self-describing — the same Korean word can live in several lists and share one `word_notes` row (6.2), so a stored per-list counter would contradict the others.
- **`import_word_list` rebuilds only the first layer.** It reuses the `word_lists` row by `source_path`, deletes and re-inserts that list's `words`, and touches nothing in `word_notes` / `word_list_progress` — that is what "re-importing keeps your notes" means concretely. `source_path` is coerced to `str`: sqlite3 refuses to bind a `Path`.
- **`delete_word_list` is the same rule in reverse — first layer only.** It deletes the `word_lists` row plus that list's `words` and `word_list_progress`, and leaves `word_notes` / `word_state_log` untouched. `word_notes` is keyed by `word_key` and **shared across lists** (D2), so deleting it by `list_id` would destroy notes belonging to other lists. The cost is orphan note rows for words no longer in any list; that is accepted, because the re-import promise above *depends* on notes outliving their list. Child rows are deleted explicitly rather than relying on `ON DELETE CASCADE` — `CREATE TABLE IF NOT EXISTS` means an older database's `words` table may predate the CASCADE clause.

Second-layer data can be exported/imported as one JSON (`export_payload`/`import_payload`, `SECOND_LAYER_TABLES`). **Import is atomic** — it validates `kind` + `schema_version`, then wipes and rewrites inside one transaction, so a bad file leaves existing data intact. A backup written by an older version simply has no `material_tags` / `material_usage` / `reading_progress` section, and `import_payload` skips absent tables rather than failing — verified, because that is the path a real restore takes.

**Material data follows the same two-layer rule**, with `path` as the join key instead of `word_key`: `materials` is rebuildable from the filesystem, while `material_tags` / `material_usage` / `reading_progress` are things the user made and **must never be lost**. `materials` is deliberately *not* in `SECOND_LAYER_TABLES` (6.5: don't export rebuildable first-layer data), and the three user tables are, even though their rows are useless without the files — losing someone's tags is losing their work.

**sqlite3 parameter binding gotcha:** the second argument must be a tuple. Passing a bare string makes sqlite3 iterate it as a character sequence and raise `Incorrect number of bindings supplied`. The old `get_word_state()` had this bug from day one, which silently broke word marking and notes until 2026-10-06.

`core/config.py` resolves `PROJECT_ROOT`/`DATA_DIR`/`DATABASE_PATH`/`BACKUP_DIR` from `__file__`, holds `DEFAULT_EXAM_DATE`/`DEFAULT_EXAM_LABEL` and `SUBJECT_FOLDERS` (all only seed a fresh DB), and provides `detect_material_root()` — the D10-C "auto-probe, then store" default. See **Filesystem coupling** below.

`tasks.json` at the repo root is a leftover from the pre-SQLite planner and is read by nothing.

### Filesystem coupling

`vault_view.py`, `snippets_view.py` and `vocab_view.py` **no longer hardcode paths** — they read the configured material root from the database:

- `VaultView.root_dir` is a property returning `database.get_material_root()`, and vocab's file dialogs start from `<root>/单词`. Each view has a `refresh()` that re-reads the setting, wired through `MainWindow._on_paths_changed` when P5 changes the path. `VaultView.refresh()` re-runs the scan rather than re-walking the tree inline — the index is what the page reads. **`SnippetsView` reads no root at all**: it takes its material images from that same index (see *P4 知识碎片的清单与两层* below).
- `get_material_root()` falls back to live detection (`core/config.py`) when the setting is empty, so a fresh DB still "just works".
- The material root is whatever directory contains the `写作 / 听力 / 阅读 / 单词` subfolders (`SUBJECT_FOLDERS`). A bounded BFS (`detect_material_root`) finds the shallowest match. The old "three `dirname()` hops from `__file__`" approach was abandoned because it **already broke** when the material tree moved one level deeper under `课程资料/` — the hardcoded hop landed on `课程资料/` itself, which has no subject folders. This is `PRODUCT_SPEC` D10-C.

### OCR was removed (2026-10-06) — TSV import is the only word-list entry point

P1's 「从 PDF 提取」 button and its `OCRWorker`, P4's 「提取图片文字」 and its `ImageOCRWorker`, the 词表转换脚本 setting field, and the Tesseract branch of P5's self-check are **all gone**. Read this before re-adding any of them:

- The external script `单词表转文本PDF工具.py` hardcoded `--psm 6` (assume a single uniform text block) for a **two-column table** layout. Measured against a hand-checked 50-word ground truth it scored **0/50 at 200, 300 and 400 DPI**; `--psm 3`/`4` scored 64–72%. The parser never had a usable input. **That script has since been deleted** from the material tree, so this evidence can no longer be re-checked against a live file — it is recorded here because it is the reason the OCR path was dropped.
- Half the source word-list PDFs are **digital, not scanned**, and carry a perfect text layer — `fitz.get_text()` returns all 50 rows exactly. OCR destroys that and can return 0 rows.
- Recognition now happens **outside the toolkit** (the user supplies the TSVs), so there is no second recognition path to keep in sync and no Tesseract dependency to detect.

The interchange format is what must now be preserved. `vocab_view.load_tsv` reads it and is the contract: **3 tab-separated columns — `编号 / 韩语 / 中文` — no header row, `utf-8-sig`, `\t` separator**. It silently drops rows with fewer than 3 columns or an empty 韩语 column, and silently substitutes the line number when 编号 isn't an integer. A header row is imported as a real word and its fallback seq collides with row 1. `core/database.py:import_word_list` then `strip()`s both text columns. See `samples/README.md`.

**Orphan key:** existing databases still carry a `settings.ocr_script_path` row. Nothing reads or writes it. It is deliberately left alone — deleting a key/value row is a data migration whose only benefit is tidiness. (`vocab_last_list_id` is the same story.)

### Anki export was removed (2026-10-09) — read this before re-adding it

P1's 「Anki 导出」 button, P5's 「Anki 导出」 button, `database.anki_rows()` / `export_anki_file()` / `count_anki_export_words()`, and the three test files that covered them (`tests/test_anki_export_stress.py`, `test_anki_forensic.py`, `test_settings_anki_adversarial.py` — ~1,600 lines) are **all gone**. `PRODUCT_SPEC` was at v1.11 claiming it was done; v1.12 corrects every mention.

Why it went, for the record: the export duplicated `PRODUCT_SPEC` §4.2 专攻模式 already being the drill queue (both answer "which words don't I know yet"), nothing in the repo or `docs/progress.md` shows it was ever used against a real Anki deck, and it carried ~1,600 lines of test surface for a one-shot TSV writer. The data it needed (`word_notes.state`) is still there and still exported by the JSON backup, so re-adding it is a ~60-line job, not a migration.

The cost of removing it: **`PRODUCT_SPEC` §10 第 7 期 item 8, §4.2 扩展方向, §6.5, §1.2 边界, and 附录 B all claimed it existed.** That is how a feature ends up documented as ✅ while the code has zero references to it. `docs/universal_exam_hub_refactoring_plan.md` also still lists Anki 导出 as an existing asset — it is a plan, not a record, and it is wrong on that point.

### Playback-position persistence was removed (2026-10-09)

`track_progress.position_ms` is in the schema but **nothing writes it and `load_track()` never seeks back**. This is deliberate, not a regression — the column survives only because dropping a column has no benefit beyond tidiness. `shadowing_view._pending_position` is now dead code (only ever `None`).

The UI tail was cut off on the same day: P0's 「继续上次」 audio row used to end with `上次听到 MM:SS`, which became `0:00` the moment position stopped being persisted. **v1.13 removed the pill instead of substituting a reading** — `已听 N 次` and cumulative listen time already live on P2's list row and track meta line, and repeating them on the home screen just spends a second card slot on the same fact. `ui/planner_view._mmss()` and `resume_context()["position_ms"]` were deleted along with it.

If a position reading is ever wanted on the home screen again, the prerequisite is restoring position *persistence* — not picking a number that looks plausible. `position_ms` can be reset to zero (restart from the top), `listened_ms` only ever grows; they are not interchangeable (see `PRODUCT_SPEC` 4.1).

### Threading

Long operations run on `QThread` subclasses reporting via `Signal`: `FFmpegWorker` (shadowing mix), `ScanWorker` (P3's index scan), `ThumbnailWorker` (P4's thumbnails), and the three in `core/tts.py` — `TTSNetworkProbeWorker` (P5 network probe), `BatchTTSPreloadWorker` (P1 batch cache warm-up), `_TTSFetchWorker` (P1 single-word fetch). Connect signals before `start()`, and mutate widgets only in the handlers. **None of them touches sqlite** — one connection is shared by all six views (`check_same_thread`), so a thread produces data and the main thread writes it.

The TTS workers are the ones that can outlive a timeout: `_TTSFetchWorker._fetch_google_tts` tries **three endpoints at 4.5 s each** and `TTSNetworkProbeWorker` three at 4 s, while `cancel()` only flips a flag that is checked *between* requests. `WordSpeaker.shutdown()` and `SettingsView.shutdown()` both `wait(1500)` — shorter than that worst case. Expect a "Destroyed while thread is still running" on exit if the network is slow.

`shadowing_view.py` accumulates listened milliseconds in `unlogged_ms` and flushes whole minutes to `activity_log` on pause, on audio reload, and from `MainWindow.closeEvent`. If you add another exit path, flush there too or the time is lost.

`vocab_view.py` has the mirror-image hazard: notes autosave on a **1.5 s debounce**, so at any moment there may be text that is typed but not yet in SQLite. `_flush_note()` runs on word change, mode change, list change, `hideEvent`, and `MainWindow.closeEvent`. The original code only saved when switching to another word, so closing the app lost whatever was being typed — the exact bug `PRODUCT_SPEC` 4.2 calls out. **Any new path out of the page needs a flush.**

`snippets_view.py` (the `snippets.note` column and the title field) is the same pattern a third time, with the same 1.5 s debounce and the same two flush points.

`MainWindow.closeEvent` therefore owes the exit path **eight things, in this order** — 三次冲刷 + 五次收线程，然后才关库：

| # | 调用 | 不做的后果 |
|---|---|---|
| 1 | `shadowing_view.flush_study_session()` | 丢掉尚未凑满整分钟的跟读时长、AB 点、语速、曲目状态 |
| 2 | `vocab_view.flush_pending()` | 丢掉 P1 防抖窗口里正在编辑的笔记 |
| 3 | `snippets_view.flush_pending()` | 丢掉 P4 防抖窗口里的说明与标题 |
| 4 | `vocab_view.shutdown()` | 批量预下载线程与发音线程还在跑；P1 的发音播放不收干净 |
| 5 | `settings_view.shutdown()` | `TTSNetworkProbeWorker` 还在跑（**这一条最早不在清单里，是后补的**） |
| 6 | `vault_view.shutdown()` | `ScanWorker` 被销毁时 Qt 打印 "Destroyed while thread is still running" |
| 7 | `shadowing_view.shutdown()` | `FFmpegWorker` 跑完只剩半截合成产物；录音中的会话要收干净 |
| 8 | `snippets_view.shutdown()` | `ThumbnailWorker` 同上 |

顺序有意义：冲刷必须全部早于收线程与 `database.close()`。`flush_study_session()` 自己有 `_closed` 守卫——关库之后 Qt 还会发一次 `hideEvent`，那时再写库就是 `Cannot operate on a closed database`，用户看到的是"关窗口报错"。新增任何出口（新页面、新线程、新的防抖字段）都要在这里补一行。

## Other directories

- `samples/` — synthetic test fixtures (a 100-word TSV, a matching Korean/Chinese text pair). Not study material; safe to delete. **The real word lists live in `<material root>/单词/tsv/`** — 5 files, 100 rows each, as of 2026-10-06, produced outside the toolkit. `samples/` stays as a clean, known-good fixture for testing the importer. `VocabView._tsv_start_dir()` opens the file picker at `单词/tsv` when that subdirectory exists. Since v1.7 P1 stores imported words in the `words` table, so exercising it means importing the TSV through the app (`载入词表`) — after that, re-importing the same file keeps your notes and pass-through progress.
- `design/references/` — the four Linear screenshots the visual language was derived from. `design/Pretendard-1.3.9/` — the font package (SIL OFL 1.1).