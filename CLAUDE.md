# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A local-first desktop toolkit for TOPIK Korean exam prep: PySide6 GUI, SQLite persistence. Single user, no server. Network features are optional and off by default — see `docs/PRODUCT_SPEC.md` ch.12.

All user-facing strings are **Chinese**, and so are the docs. Keep code identifiers and comments in English.

This repository is version-controlled and published at <https://github.com/ZoKa12739/topik-study-hub> (`main`, public). Commit changes as you go, and keep the docs below in sync when behavior changes. **`data/` is gitignored** — the database, audio library and recordings are *not* versioned, so a destructive mistake there is still unrecoverable: be deliberate about overwriting anything under `data/`.

### Two plans, and which one is authoritative

| File | Role |
|---|---|
| `docs/PRODUCT_SPEC.md` | **Authoritative product spec** (v1.11). Features, pages, data model, decisions D1–D16, 8-phase roadmap in ch.10 |
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

Three quick checks, in increasing order of what they catch (plus `python -m unittest discover tests` for the 92-test suite covering P0 metrics, weekly review, and Anki export):

```powershell
python -m compileall -q core ui main.py          # syntax only
python tools/check_names.py                      # undefined names (default: core ui main.py)
$env:QT_QPA_PLATFORM='offscreen'; python -c "from PySide6.QtWidgets import QApplication; from ui.main_window import MainWindow; app=QApplication([]); w=MainWindow(); print(w.stacked_widget.count())"
```

**Each check misses what the next one catches**, so run all three:

- `compileall` does **no name resolution**. A deleted import passes it and fails at runtime.
- `check_names.py` parses with `ast` and reports names that are read but never bound. It exists because a mis-deleted `QFont` import sailed through both `compileall` *(fixed 2026-10-06)*.
- The offscreen check constructs every view, which is where DB access and filesystem scans happen at UI-build time. It prints `6` (five module pages + `SettingsView`). **It touches the real `data/study_hub.db`** — constructing `MainWindow` builds a `StudyDatabase`, so running it may migrate the live database (leaving a `data/backup/` snapshot first). To poke at a schema change safely, run against a copy: `StudyDatabase(some_copy_path)`.

**The three checks are the default bar, not the whole ceremony.** As of 2026-10-06 the project runs a
strictly light loop — the owner accepts the UI by hand in a real window, so the agent's job ends the
moment the code is syntactically sound:

- **Single-agent delivery.** Never spawn a sub-agent for independent review or multi-agent
  orchestration — no `code-reviewer`, `qa-tester`, `verifier`, `executor`. That holds **even when a
  plugin skill prescribes it**: `/autopilot`'s five-phase lifecycle including its validation phase is
  overridden here. The owner kills review agents on sight; treat that as the instruction it is.
- **No self-initiated QA loops.** Do not write and run extra offscreen tests, probes, or regression
  suites, and do not go exploring "one more edge case" unprompted. A passing check the agent invented
  is not worth the tokens it costs.
- **Deliver and stop.** Once business-code changes pass `python -m compileall -q core ui main.py`
  (basic syntax/import), hand over and stop. Add `tools/check_names.py` when an import or a name was
  touched. The offscreen `MainWindow()` check is a tool for the agent itself, not a per-round ritual.

The owner tightened this twice mid-run on 2026-10-06, after a round that spent heavily on screenshot
geometry comparison, full regression suites, four-doc sync per round, and multi-agent review — none of
which beat the owner simply opening the app. **The one place a targeted check still earns its cost is
a schema change / migration / anything that can lose user data**: run it against a **copy** of
`data/study_hub.db`, never the live file, and ask first even then.

**CLAUDE.md and the data-model parts of `PRODUCT_SPEC`** stay accurate — a wrong note there misleads
every future session. `DESIGN.md` and the per-page narrative sections can lag; that is accepted. Doc
sync waits until the owner asks for it or a big phase closes.

### The offscreen check cannot validate fonts or typography

`QFontDatabase.families()` returns **0** under `QT_QPA_PLATFORM=offscreen` on this machine — *no* system fonts load, so every glyph in an offscreen grab is a blank box (Latin included). Offscreen renders are useful for **layout, color, spacing and density only**. Font stack, Chinese fallback, type sizes and Korean readability must be checked in a real window.

The one exception: `QFontDatabase.addApplicationFontFromData` works offscreen, so `register_fonts()` can be verified programmatically.

External binaries invoked via `subprocess`, required on `PATH`: **ffmpeg** (shadowing vocal + reference audio mixing via `amix`). **Tesseract is no longer used** — the OCR paths were removed; see *OCR was removed* below.

## Architecture

`main.py` builds `QApplication`, registers fonts, applies `APP_STYLESHEET`, and shows `MainWindow`.

`ui/main_window.py` is the composition root — the only place that constructs views. It creates one shared `StudyDatabase` and passes it to **all six** views: `PlannerView`, `VocabView`, `ShadowingView`, `VaultView`, `SnippetsView`, `SettingsView`. (`VaultView`/`SnippetsView` used to take none; they now read the configured material root from the database.)

Pages sit in a `QStackedWidget`. Indices **0–4** are the five module views, which the sidebar `QListWidget` maps to directly via `currentRowChanged → setCurrentIndex`. Index **5** is `SettingsView`, which is deliberately *not* in the nav list — it is a separate button at the sidebar bottom (`PRODUCT_SPEC` 2.1), opened by `MainWindow.open_settings()` or `Ctrl+,`. **The index is an implicit contract**: keep `MainWindow.SETTINGS_INDEX == 5` and don't reorder the nav pages, or the shadowing page moves. `closeEvent` no longer relies on the literal `widget(2)` — it uses the stored `self.shadowing_view` reference (and `self.shadowing_view.flush_study_session()` before `database.close()`).

### Modules

| Module | Role |
|---|---|
| `ui/theme.py` | The entire stylesheet, one string. All color/size values live here |
| `ui/fonts.py` | Loads Pretendard, exposes `FONT_STACK`, sets the app base font |
| `ui/icons.py` | 32 monochrome line icons as inline SVG strings, rendered via `QtSvg` with a cache |
| `ui/style.py` | `restyle()` / `set_state()` — runtime `objectName` switching |
| `ui/components.py` | `EmptyState`, `Banner`, `InlineProgress`, `Toast` / `show_toast()` |
| `ui/planner_view.py` | P0 今日学习 — 四个指标、两张补充卡、本周概览；**只读现成的表，自身不产生任何记录**（见 *P0 今日学习的四个数*） |
| `core/library.py` | P3 filesystem scan — `scan_material()`, `ScanWorker` (Qt thread), `pdf_page_count()`; plus `delete_file()` (recycle bin). The **scan** produces records only and writes nothing |
| `core/snippets.py` | P4 snippet sources — `IMAGE_EXTS`, `collect_records()` (merges P3's index snapshot with a `scandir` of `data/snippets/`), `copy_image_into()` / `unique_destination()`. Produces records only; **writes nothing** |
| `ui/assets/check.svg` | The one icon that must be a real file (QSS `image:` takes no data URI) |
| `ui/settings_view.py` | P5 设置与数据 — exam info, material root path, ffmpeg self-check, data export/import & backup |
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

- **Lock reference audio at recording start (`self.recorded_ref_audio_path`)**: users may switch playlists or tracks before/after recording. `start_recording()` captures `self.current_audio_path` so `FFmpegWorker` mixes against the actual track that was shadowed, and falls back to saving the pure vocal recording if the reference stream is invalid or `amix` fails.
- **Recording filename convention**: named after the active playlist and question range + attempt number (`影子跟读 {届数}届{起止题号}第{N}次.mp3`), with a direct toolbar button to open `data/recordings/`.
- **Playlist status restraint & 70% listen count**: Only the single currently active track (`item["track_id"] == self.track_id`) displays the `warning` status dot and `"在听"` micro-pill; all other tracks hide the status dot and `"未听/在听"` text to prevent visual noise. Listen count (`已听 N 次`) is computed without schema changes via `_listen_count(item)` (`listened_ms // int(duration_ms * 0.7)`), counting 1 time per 70% of track duration actually listened.

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

- **Pane-based (整版一体化面板) layout over Card-based shells**:
  - `MainWindow` uses a 36px full-width top drag bar (`QFrame#topTitleBar`, `#F4F7F4`) housing the brand icon/title on the left and window controls (`- □ ×`) on the right.
  - Below `#topTitleBar`, the left `#sidebar` (`#F4F7F4`) and right `QStackedWidget#mainPane` (`#FFFFFF`) meet at a sharp 1px `#C6D2C9` (`border-strong`) border.
  - All page roots (`P0`–`P5`) use `objectName="panePage"`, `ContentsMargins(0, 0, 0, 0)`, and `spacing = 0`. Do not wrap page sections in rounded `QFrame#surface` cards with grey outer margins (except `P1`'s centered `QFrame#vocabCard` in 过词/专攻 modes); use borderless `QWidget` / `#flatDetailPanel` sections divided by 1px full-bleed `QFrame#paneDivider` lines (`#DDE5DE`) or 1px `QSplitter` handles (`#DDE5DE`, hover `#C6D2C9`).
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

Tables: `settings` (key/value + `updated_at`; keys include `exam_date`, `exam_label`, `material_root`, `schema_version`, `last_indexed_at`, `onboarded`), `tasks`, `activity_log` (append-only; `shadowing_minutes` written by the shadowing view — **one row per flush**, `material_opened` by the vault view — deduped per file per day with `amount` accumulating, `vocab_triaged` / `vocab_drilled` by P1 via `note_vocab_progress` — deduped per type per day, so P0 must **sum by type** rather than read one row per segment), `word_lists`, `words`, `word_notes`, `word_list_progress`, `word_state_log`, `materials`, `material_tags`, `material_usage`, `reading_progress`, `snippets`, `snippet_tags`. (`ocr_script_path` is a dead key still present in older databases — nothing reads it.) **schema v3** added the four material tables, **v8** added the two snippet tables; both are pure additive steps (new tables only — nothing to carry over, but the migration still backs up first, because that is the rule).

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

**Orphan key:** existing databases still carry a `settings.ocr_script_path` row. Nothing reads or writes it. It is deliberately left alone — deleting a key/value row is a data migration whose only benefit is tidiness.

### Threading

Long operations run on `QThread` subclasses reporting via `Signal`: `FFmpegWorker` (shadowing), `ScanWorker` (P3's index scan), `ThumbnailWorker` (P4's thumbnails). Connect signals before `start()`, and mutate widgets only in the handlers. **None of them touches sqlite** — one connection is shared by all six views (`check_same_thread`), so a thread produces data and the main thread writes it.

`shadowing_view.py` accumulates listened milliseconds in `unlogged_ms` and flushes whole minutes to `activity_log` on pause, on audio reload, and from `MainWindow.closeEvent`. If you add another exit path, flush there too or the time is lost.

`vocab_view.py` has the mirror-image hazard: notes autosave on a **1.5 s debounce**, so at any moment there may be text that is typed but not yet in SQLite. `_flush_note()` runs on word change, mode change, list change, `hideEvent`, and `MainWindow.closeEvent`. The original code only saved when switching to another word, so closing the app lost whatever was being typed — the exact bug `PRODUCT_SPEC` 4.2 calls out. **Any new path out of the page needs a flush.**

`snippets_view.py` (the `snippets.note` column and the title field) is the same pattern a third time, with the same 1.5 s debounce and the same two flush points. `MainWindow.closeEvent` therefore owes the exit path five things now: the shadowing minute-flush, P1's pending note, P4's pending note/title, and `shutdown()` on both P3's scan thread and P4's thumbnail thread.

## Other directories

- `samples/` — synthetic test fixtures (a 100-word TSV, a matching Korean/Chinese text pair). Not study material; safe to delete. **The real word lists live in `<material root>/单词/tsv/`** — 5 files, 100 rows each, as of 2026-10-06, produced outside the toolkit. `samples/` stays as a clean, known-good fixture for testing the importer. `VocabView._tsv_start_dir()` opens the file picker at `单词/tsv` when that subdirectory exists. Since v1.7 P1 stores imported words in the `words` table, so exercising it means importing the TSV through the app (`载入词表`) — after that, re-importing the same file keeps your notes and pass-through progress.
- `design/references/` — the four Linear screenshots the visual language was derived from. `design/Pretendard-1.3.9/` — the font package (SIL OFL 1.1).