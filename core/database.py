"""单一数据层（stdlib `sqlite3`，无 ORM）。

## 二层真相原则（PRODUCT_SPEC 6.1）

| 层 | 内容 | 能否重建 | 策略 |
|---|---|---|---|
| 第一层 | 词条、文件索引、截图元数据 | 能（从源文件重扫/重导） | 可随时清空重建 |
| 第二层 | 笔记、词的三态、过词断点、任务、活动记录、配置 | **不能** | 必须持久、必须可导出、迁移前必须备份 |

原实现把两层混在一起（`word_states` 同时承担"词条身份"和"用户笔记"），
本版按 D2 方案 B 拆开：`words` 是词条（第一层），`word_notes` 是用户数据（第二层）。

## schema 演进（PRODUCT_SPEC 6.4）

原实现用 `CREATE TABLE IF NOT EXISTS` 建表、**没有任何迁移机制**，字段变更对已存在的库
静默失效。现在：`settings.schema_version` 记录版本 → 启动时比对 → 需要时**先备份再迁移**。

三条硬约束（6.4）：
  1. 迁移前必须自动备份数据库文件到 `data/backup/`
  2. 迁移失败必须保留原库，绝不能用空库静默覆盖用户数据
  3. `word_states` 里的 note / is_starred 是第二层数据，迁移时一条都不能丢

## `word_key` 的取值（D2 子决策）

原实现是 `korean + "␟" + meaning`，本版改为**仅 `korean`**——用户写笔记时想的是
"这个词"，不是"这个词的这个释义"。多义词的歧义在笔记里自然能写清楚。
迁移时把旧的复合键收敛为 `korean`（当前数据 49 条 korean 互不重复，无碰撞）。
"""

import csv
import html
import io
import json
import os
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

from core.config import (
    AUDIO_DIR,
    BACKUP_DIR,
    DATABASE_PATH,
    DATA_DIR,
    DEFAULT_EXAM_DATE,
    DEFAULT_EXAM_LABEL,
    RECORDINGS_DIR,
    SCHEMA_VERSION,
    SNIPPETS_DIR,
    detect_material_root,
    dir_size,
    normalize_path,
    path_key,
    subject_of,
)

# 词的三态（D14 方案 B）。未过词为 NULL，不在这张枚举里。
WORD_STATES = ("known", "fuzzy", "unknown")  # 认识 / 模糊 / 不认识

# 曲目状态点（4.3）。NULL = 未听，同样不在这张枚举里。
# 显示名在视图层（`ui/shadowing_view.py` 的 TRACK_STATUS_LABELS）。
TRACK_STATES = ("listening", "done")  # 在听 / 已磕完

# 第二层数据表（6.1）。导出/导入只处理这些；第一层（words/word_lists/materials）
# 可从源文件重建，不进导出（6.5："不导出可重建的第一层数据"）。
SECOND_LAYER_TABLES = (
    "settings",
    "tasks",
    "activity_log",
    "word_notes",
    "word_list_progress",
    "word_state_log",
    "material_tags",
    "material_usage",
    "reading_progress",
    "snippets",
    "snippet_tags",
)

# 单词仓往活动日志里写的两个事件（5.2）。过词与专攻通过**分开计**——5.4 说它们
# 代表"不同性质的努力"，合成一条等于把两次不同的努力相加。
VOCAB_ACTIVITY_TYPES = ("vocab_triaged", "vocab_drilled")  # 过词 / 专攻通过

# 「今日亮点」的排序权重（5.4）：**分钟数 > 词数 > 次数**。
# 单位之间本来不可比，"投入量最大"必须先按可比量纲分组、再在组内取最大；
# 否则 3 分钟会输给 40 次打开资料。
HIGHLIGHT_RANK = {
    "shadowing_minutes": 0,
    "vocab_triaged": 1,
    "vocab_drilled": 1,
}
HIGHLIGHT_OTHER_RANK = 2

# 备份保留份数（PRODUCT_SPEC 6.5 / Q5：保留最近 7 份）
BACKUP_KEEP = 7


class DatabaseMigrationError(RuntimeError):
    """迁移失败。原库保持不变，调用方应以只读/降级模式启动并提示用户（6.4 第 4 条）。"""


class StudyDatabase:
    """所有学习模块共享的持久层。"""

    def __init__(self, path=None):
        self.path = Path(path) if path else DATABASE_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # 记录连接前的状态：决定是否需要备份、是否要跑每日备份
        pre_existing = self.path.exists() and self.path.stat().st_size > 0

        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")

        self._migrate_or_create(pre_existing)
        self._seed_defaults()
        if pre_existing:
            self._daily_backup()

    # ==================================================================
    # schema：创建与迁移
    # ==================================================================

    _SCHEMA_SQL = """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            completed INTEGER NOT NULL DEFAULT 0,
            due_date TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            completed_at TEXT,
            sort_order INTEGER,
            source_module TEXT
        );

        CREATE TABLE IF NOT EXISTS activity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            activity_type TEXT NOT NULL,
            amount INTEGER NOT NULL DEFAULT 1,
            unit TEXT,
            logged_on TEXT NOT NULL,
            created_at TEXT,
            ref_type TEXT,
            ref_id INTEGER,
            note TEXT
        );

        CREATE TABLE IF NOT EXISTS word_lists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            source_path TEXT,
            imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            item_count INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS words (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            list_id INTEGER NOT NULL REFERENCES word_lists(id) ON DELETE CASCADE,
            seq INTEGER NOT NULL,
            korean TEXT NOT NULL,
            meaning TEXT,
            word_key TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS word_notes (
            word_key TEXT PRIMARY KEY,
            note TEXT NOT NULL DEFAULT '',
            state TEXT,
            passed_at TEXT,
            state_updated_at TEXT,
            drill_rounds INTEGER NOT NULL DEFAULT 0,
            is_starred INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS word_list_progress (
            list_id INTEGER PRIMARY KEY REFERENCES word_lists(id) ON DELETE CASCADE,
            cursor_seq INTEGER NOT NULL DEFAULT 0,
            passed_count INTEGER NOT NULL DEFAULT 0,
            marked_count INTEGER NOT NULL DEFAULT 0,
            last_drilled_at TEXT,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS word_state_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            word_key TEXT NOT NULL,
            from_state TEXT,
            to_state TEXT,
            changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        /* ---- P3 资料库（第 4 期）。第一层只有 materials 一张表，见下方"资料库索引"一节 ---- */

        CREATE TABLE IF NOT EXISTS materials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            subject TEXT,
            ext TEXT,
            size INTEGER,
            mtime INTEGER,
            missing INTEGER NOT NULL DEFAULT 0,
            indexed_at TEXT
        );

        /* 第二层：标签、打开统计、阅读进度。**主键都是 path，不是 material_id**——
           索引表可以整个删掉重建（D6），而用户花时间产生的这三样数据不能跟着没，
           所以它们挂在稳定的 path 上，重建后自动重新关联。见本文件 library 一节 */

        CREATE TABLE IF NOT EXISTS material_tags (
            path TEXT NOT NULL,
            tag TEXT NOT NULL,
            created_at TEXT,
            PRIMARY KEY (path, tag)
        );

        CREATE TABLE IF NOT EXISTS material_usage (
            path TEXT PRIMARY KEY,
            last_opened_at TEXT,
            open_count INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS reading_progress (
            path TEXT PRIMARY KEY,
            last_page INTEGER NOT NULL DEFAULT 0,
            total_pages INTEGER,
            updated_at TEXT
        );

        /* ---- P4 知识碎片（第 6 期）。结构见 PRODUCT_SPEC 6.2 ----

           `snippets` 与 `audio_tracks` 是同一形态：**两层混在一张表里**。
           path / source_subject / created_at / width / height 是第一层（可从文件系统与
           P3 索引重建），title / note 是第二层（用户给这张图起的名字与写的说明，
           重建不出来）。

           因此这一张表是**整张**进 `SECOND_LAYER_TABLES` 的。导出是按表走的
           （`export_payload` 逐表 `SELECT *`），一张表里没法只导出后半截；而丢掉 title
           就是丢掉用户花时间写的东西，正是 6.5 要防的事。代价是导出里多带一份可重建的
           路径元数据——比丢用户数据便宜得多。

           两条由此而来的纪律：
             * `title` 默认取文件名，用户改过之后它就是第二层——所以登记走
               `INSERT OR IGNORE`，**永远不覆盖 title / note**；
             * `missing` 存一份是第一层的事实（资料库图片抄自 `materials.missing`，
               收集目录里的由 scandir 得出）。存下来 P4 就不必对每一行做
               `os.path.exists`。 */

        CREATE TABLE IF NOT EXISTS snippets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL DEFAULT '',
            source_subject TEXT DEFAULT '',
            created_at TEXT,
            width INTEGER NOT NULL DEFAULT 0,
            height INTEGER NOT NULL DEFAULT 0,
            missing INTEGER NOT NULL DEFAULT 0,
            note TEXT DEFAULT ''
        );

        /* 第二层：碎片标签。挂在 path 上，与 `material_tags` 同理——`snippets.id` 是
           AUTOINCREMENT，索引重建一次就重新发号，挂在它上面的标签会指向另一张图。 */

        CREATE TABLE IF NOT EXISTS snippet_tags (
            path TEXT NOT NULL,
            tag TEXT NOT NULL,
            created_at TEXT,
            PRIMARY KEY (path, tag)
        );

        /* ---- P2 音频库（第 5 期）。结构见 PRODUCT_SPEC 6.2 ----

           `audio_tracks` 是**两层混在一张表里**的（6.2 明确这么定的）：
           path/title/duration_ms/size/content_hash/imported_at 是第一层（从音频库目录可重建），
           source_path/kr_text/cn_text 是第二层（用户手打或校对的文本，不能重建）。

           ⚠️ 这几张表**故意没有进 `SECOND_LAYER_TABLES`**（即暂不参与导出/导入）：
           导出/导入排在 §10 的第 7 期，而 `playlist_items.track_id` 指向未导出的
           `audio_tracks.id`，恢复时必须按 6.2 说的"通过 content_hash 重新关联"做一次
           重映射。现在只做一半，恢复后就是一堆悬空引用——那比暂不支持更糟。 */

        CREATE TABLE IF NOT EXISTS audio_tracks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            duration_ms INTEGER,
            size INTEGER,
            content_hash TEXT,
            imported_at TEXT,
            source_path TEXT,
            kr_text TEXT,
            cn_text TEXT
        );

        CREATE TABLE IF NOT EXISTS playlists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            created_at TEXT,
            kr_pdf TEXT,
            cn_pdf TEXT,
            kr_page INTEGER,
            cn_page INTEGER
        );

        CREATE TABLE IF NOT EXISTS playlist_items (
            playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
            track_id INTEGER NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (playlist_id, track_id)
        );

        CREATE TABLE IF NOT EXISTS track_progress (
            track_id INTEGER PRIMARY KEY,
            position_ms INTEGER NOT NULL DEFAULT 0,
            pos_a_ms INTEGER,
            pos_b_ms INTEGER,
            speed REAL NOT NULL DEFAULT 1.0,
            status TEXT,
            listened_ms INTEGER NOT NULL DEFAULT 0,
            loop_count INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT
        );

        /* 分段标记（D4 方案 B）。§6.2 未列这张表，是本期的落点——
           没有它，"手动标记第 1 段 00:00-00:45" 无处可存。

           2026-10-06 验收后 UI 已移除（用户判定"没用"），表与下面的 API 留着：
           删表是一次没有收益的迁移，而字段与写入逻辑都是现成的，
           哪天想用它回来时不用重写。 */

        CREATE TABLE IF NOT EXISTS track_segments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            track_id INTEGER NOT NULL,
            seq INTEGER NOT NULL DEFAULT 0,
            title TEXT NOT NULL,
            start_ms INTEGER NOT NULL,
            end_ms INTEGER,
            created_at TEXT
        );

        /* 当前播放上下文，**单行**（id 恒为 1，靠 CHECK 约束）。P0「继续上次」读它。 */

        CREATE TABLE IF NOT EXISTS playback_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            playlist_id INTEGER,
            track_id INTEGER,
            updated_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_tasks_due_date ON tasks(due_date);
        CREATE INDEX IF NOT EXISTS idx_tasks_completed_at ON tasks(completed_at);
        CREATE INDEX IF NOT EXISTS idx_activity_logged_on ON activity_log(logged_on);
        CREATE INDEX IF NOT EXISTS idx_activity_type_day ON activity_log(activity_type, logged_on);
        CREATE INDEX IF NOT EXISTS idx_words_word_key ON words(word_key);
        CREATE INDEX IF NOT EXISTS idx_words_list_seq ON words(list_id, seq);
        CREATE INDEX IF NOT EXISTS idx_word_notes_state ON word_notes(state);
        CREATE INDEX IF NOT EXISTS idx_materials_subject ON materials(subject);
        CREATE INDEX IF NOT EXISTS idx_materials_name ON materials(name);
        CREATE INDEX IF NOT EXISTS idx_material_tags_tag ON material_tags(tag);
        CREATE INDEX IF NOT EXISTS idx_audio_tracks_hash ON audio_tracks(content_hash);
        CREATE INDEX IF NOT EXISTS idx_playlist_items_order ON playlist_items(playlist_id, sort_order);
        CREATE INDEX IF NOT EXISTS idx_track_segments_track ON track_segments(track_id, start_ms);
        CREATE INDEX IF NOT EXISTS idx_snippets_subject ON snippets(source_subject);
        CREATE INDEX IF NOT EXISTS idx_snippet_tags_tag ON snippet_tags(tag);
    """

    # v1 → v2 给已存在表补的列（值 = 列定义）。迁移时逐列检查，已存在的跳过。
    # 这张表在**每次启动**都会走一遍（自愈），所以它同时承担"给老库补新列"的职责。
    _ADDED_COLUMNS = {
        "settings": {"updated_at": "TEXT"},
        "tasks": {"sort_order": "INTEGER", "source_module": "TEXT"},
        "activity_log": {
            "unit": "TEXT",
            "created_at": "TEXT",
            "ref_type": "TEXT",
            "ref_id": "INTEGER",
        },
        # v6：每份播放列表配两份 PDF（原文 / 解析），见 PRODUCT_SPEC 4.3
        # v7：两份 PDF 各自记住读到第几页（"接着上次读"）
        "playlists": {
            "kr_pdf": "TEXT",
            "cn_pdf": "TEXT",
            "kr_page": "INTEGER",
            "cn_page": "INTEGER",
        },
    }

    def _migrate_or_create(self, pre_existing):
        current = self._read_schema_version()
        if current >= SCHEMA_VERSION:
            # 已是最新版：仍然跑一遍 CREATE IF NOT EXISTS / 补列，作为轻量自愈
            self.connection.executescript(self._SCHEMA_SQL)
            self._add_missing_columns()
            self.connection.commit()
            return

        legacy = self._table_exists("word_states")
        # 已有内容的库才值得备份；空文件（刚创建）不需要
        if pre_existing and (legacy or current > 0 or self._table_exists("settings")):
            self._backup(reason="migrate")

        try:
            self.connection.executescript(self._SCHEMA_SQL)
            self._add_missing_columns()
            if legacy:
                self._migrate_word_states()
            if current < 4:
                # 表已由上面那句建好；v2 及更早的库里这些表本来就不存在，
                # 函数内部先判存在性，空库上就是一次空操作
                self._normalize_material_paths()
            self._set_schema_version(SCHEMA_VERSION)
            self.connection.commit()
        except Exception as error:  # noqa: BLE001 —— 迁移失败必须显式转成可读错误
            self.connection.rollback()
            raise DatabaseMigrationError(
                f"数据库迁移失败，原库未被修改：{error}"
            ) from error

    def _read_schema_version(self):
        try:
            row = self.connection.execute(
                "SELECT value FROM settings WHERE key = 'schema_version'"
            ).fetchone()
        except sqlite3.OperationalError:
            return 0  # settings 表还不存在
        if not row or not row["value"]:
            return 0
        try:
            return int(row["value"])
        except (TypeError, ValueError):
            return 0

    def _table_exists(self, name):
        return (
            self.connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
            ).fetchone()
            is not None
        )

    def _column_names(self, table):
        return {row["name"] for row in self.connection.execute(f"PRAGMA table_info({table})")}

    def _add_missing_columns(self):
        for table, columns in self._ADDED_COLUMNS.items():
            if not self._table_exists(table):
                continue
            existing = self._column_names(table)
            for name, decl in columns.items():
                if name not in existing:
                    self.connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")

    def _set_schema_version(self, version):
        self.connection.execute(
            """
            INSERT INTO settings (key, value, updated_at) VALUES ('schema_version', ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP
            """,
            (str(version),),
        )

    def _migrate_word_states(self):
        """把 v1 的 `word_states` 迁进 `word_notes`，然后丢弃旧表。

        规则（6.4 第 5、6 条）：
          * note / is_starred 是第二层数据，**一条都不能丢**
          * 旧的 `is_starred = 1` 同时写入 `is_starred=1` 与 `state='unknown'`——
            原实现的"重点标记"语义上就是"想再看一遍"，直接落进专攻队列，
            用户的既有数据才有意义
          * `word_key` 由 `korean␟meaning` 收敛为 `korean`（D2）；碰撞时合并
        """
        rows = self.connection.execute(
            "SELECT korean, note, is_starred, updated_at FROM word_states"
        ).fetchall()

        migrated = {}  # word_key -> merged fields
        for row in rows:
            key = (row["korean"] or "").strip()
            if not key:
                continue
            note = row["note"] or ""
            starred = int(row["is_starred"] or 0)
            entry = migrated.setdefault(
                key, {"note": "", "is_starred": 0, "state": None, "state_updated_at": None}
            )
            if note.strip():
                # 碰撞时把两份笔记都留下，而不是丢掉其一
                entry["note"] = (entry["note"] + "\n" + note).strip() if entry["note"] else note
            if starred:
                entry["is_starred"] = 1
                entry["state"] = "unknown"
                entry["state_updated_at"] = row["updated_at"]

        for key, entry in migrated.items():
            self.connection.execute(
                """
                INSERT INTO word_notes
                    (word_key, note, state, state_updated_at, is_starred, updated_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(word_key) DO UPDATE SET
                    note=excluded.note,
                    state=COALESCE(excluded.state, word_notes.state),
                    state_updated_at=COALESCE(excluded.state_updated_at, word_notes.state_updated_at),
                    is_starred=excluded.is_starred,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (key, entry["note"], entry["state"], entry["state_updated_at"], entry["is_starred"]),
            )

        self.connection.execute("DROP TABLE word_states")

    def _normalize_material_paths(self):
        """v3 → v4：把资料路径统一成 `os.path.normpath` 形态，并合并由此产生的重复行。

        这是"看起来一样、比出来不等"的第二次发作（第一次是 D2 的 `word_key`）。
        Windows 上 `QFileDialog` 给的是正斜杠、`os.scandir` 给的是反斜杠，于是
        「重新定位」过的文件在索引里有了**两行**：一行是扫描新插的（科目对、没标签），
        一行是旧的（`missing=1`、带着用户标签）。规范化之后必须**按 path 合并**，
        并把第二层数据搬到留下的那一行上——否则标签与阅读进度会跟着被删的那行一起消失。
        """
        if not self._table_exists("materials"):
            return

        groups = {}
        for row in self.connection.execute("SELECT id, path, missing FROM materials"):
            groups.setdefault(path_key(row["path"]), []).append(dict(row))

        for group in groups.values():
            # 留下"文件还在"的那一行；都还在就留 id 最小的
            group.sort(key=lambda row: (row["missing"], row["id"]))
            survivor = group[0]
            for loser in group[1:]:
                self._merge_material_row(loser, survivor)
            canonical = normalize_path(survivor["path"])
            if canonical != survivor["path"]:
                # 删除在前、改名在后：否则 survivor 的新 path 会撞上还没删掉的 loser
                self.connection.execute(
                    "UPDATE materials SET path = ? WHERE id = ?", (canonical, survivor["id"])
                )

    def _merge_material_row(self, loser, survivor):
        """把 `loser` 这一行的第二层数据并到 `survivor` 上，再删掉 `loser`。

        合并规则按数据本身的语义定，不图省事：
          * **标签**取并集（`INSERT OR IGNORE`，survivor 已有的不重复加）
          * **阅读进度** survivor 优先，它没有才落到 loser 的那条上
          * **打开次数是一个计数器**，必须相加、时间取较晚的——丢掉一半就是丢数据
          * 活动流水里指向 loser 的 `ref_id` 改指 survivor，否则那些记录变成悬空引用
        """
        for table in ("material_tags", "reading_progress"):
            columns = self._PATH_KEYED_TABLES[table]
            moved = self.connection.execute(
                f"SELECT * FROM {table} WHERE path = ?", (loser["path"],)
            ).fetchall()
            if moved:
                self.connection.executemany(
                    f"INSERT OR IGNORE INTO {table} ({', '.join(columns)}) "
                    f"VALUES ({', '.join('?' for _ in columns)})",
                    [
                        tuple(
                            survivor["path"] if column == "path" else record[column]
                            for column in columns
                        )
                        for record in moved
                    ],
                )
            self.connection.execute(f"DELETE FROM {table} WHERE path = ?", (loser["path"],))

        loser_usage = self.connection.execute(
            "SELECT last_opened_at, open_count FROM material_usage WHERE path = ?",
            (loser["path"],),
        ).fetchone()
        if loser_usage is not None:
            survivor_usage = self.connection.execute(
                "SELECT open_count FROM material_usage WHERE path = ?", (survivor["path"],)
            ).fetchone()
            if survivor_usage is None:
                self.connection.execute(
                    "UPDATE material_usage SET path = ? WHERE path = ?",
                    (survivor["path"], loser["path"]),
                )
            else:
                # MAX() 在这里是标量的两参形式；ISO 时间戳按字典序比较即时间序
                self.connection.execute(
                    "UPDATE material_usage SET open_count = ?, "
                    "last_opened_at = MAX(COALESCE(last_opened_at, ''), COALESCE(?, '')), "
                    "updated_at = CURRENT_TIMESTAMP WHERE path = ?",
                    (
                        survivor_usage["open_count"] + loser_usage["open_count"],
                        loser_usage["last_opened_at"],
                        survivor["path"],
                    ),
                )
                self.connection.execute(
                    "DELETE FROM material_usage WHERE path = ?", (loser["path"],)
                )

        self.connection.execute(
            "UPDATE activity_log SET ref_id = ? WHERE ref_type = 'material' AND ref_id = ?",
            (survivor["id"], loser["id"]),
        )
        self.connection.execute("DELETE FROM materials WHERE id = ?", (loser["id"],))

    # ==================================================================
    # 备份（6.4 第 3 条 / 6.5 Q5）
    # ==================================================================

    def _backup(self, reason="manual"):
        """用 sqlite 在线备份 API 落一份一致快照到 `data/backup/`。

        不用 `shutil.copy`：直接复制一个可能正在写的数据库文件不保证一致。
        """
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        destination = BACKUP_DIR / f"study_hub_{reason}_{stamp}.db"
        target = sqlite3.connect(destination)
        try:
            with target:
                self.connection.backup(target)
        finally:
            target.close()
        self._prune_backups()
        return destination

    def _prune_backups(self):
        backups = sorted(
            BACKUP_DIR.glob("study_hub_*.db"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for stale in backups[BACKUP_KEEP:]:
            try:
                stale.unlink()
            except OSError:
                continue

    def _daily_backup(self):
        """每天一次自动备份（Q5）。当天已有备份则跳过，避免同一天多份。"""
        today = date.today().strftime("%Y%m%d")
        if BACKUP_DIR.is_dir() and any(BACKUP_DIR.glob(f"study_hub_*_{today}_*.db")):
            return None
        return self._backup(reason="daily")

    def backup_now(self):
        """手动备份（P5「打开数据目录」旁的手动入口）。"""
        return self._backup(reason="manual")

    # ==================================================================
    # settings
    # ==================================================================

    def _seed_defaults(self):
        for key, value in {
            "exam_date": DEFAULT_EXAM_DATE,
            "exam_label": DEFAULT_EXAM_LABEL,
            "last_indexed_at": "",
            "onboarded": "0",
        }.items():
            self.connection.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value)
            )
        # 探测类默认值只在**缺失时**计算，避免每次启动都扫一遍目录
        if not self._setting_exists("material_root"):
            self.set_setting("material_root", detect_material_root() or "")
        self.connection.commit()

    def _setting_exists(self, key):
        return (
            self.connection.execute("SELECT 1 FROM settings WHERE key=?", (key,)).fetchone()
            is not None
        )

    def get_setting(self, key, default=""):
        row = self.connection.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key, value):
        self.connection.execute(
            """
            INSERT INTO settings (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP
            """,
            (key, "" if value is None else str(value)),
        )
        self.connection.commit()

    def get_exam(self):
        return self.get_setting("exam_label", DEFAULT_EXAM_LABEL), self.get_setting(
            "exam_date", DEFAULT_EXAM_DATE
        )

    def set_exam(self, exam_label, exam_date):
        self.set_setting("exam_label", exam_label)
        self.set_setting("exam_date", exam_date)

    def get_material_root(self):
        """资料根目录。配置为空时回退到实时探测，仍然没有则返回空串。"""
        return self.get_setting("material_root") or (detect_material_root() or "")

    def set_material_root(self, path):
        self.set_setting("material_root", path or "")

    def get_recording_dir(self):
        """跟读录音存到哪。没配过就用 `data/recordings/`（P5 里可改）。

        与资料根目录不同，这里**不做实时探测**：麦克风录音是工具自己产生的东西，
        没有"用户已有的目录"可探，给一个默认值比给空串有用。
        """
        return self.get_setting("recording_dir") or str(RECORDINGS_DIR)

    def set_recording_dir(self, path):
        self.set_setting("recording_dir", path or "")

    def get_audio_device(self, kind):
        """指定的播放/录音设备**描述名**，没指定过返回空串（= 跟随系统默认）。

        存描述名而不是 `QAudioDevice.id()`：id 是后端给的字节串，插拔一次耳机就变，
        存下来下次一定对不上；描述名是用户自己在设置页看到的那一行字，
        设备不在了就退回系统默认，行为可预期。
        """
        return self.get_setting(f"audio_{kind}_device") or ""

    def set_audio_device(self, kind, description):
        self.set_setting(f"audio_{kind}_device", description or "")

    def is_onboarded(self):
        return self.get_setting("onboarded", "0") == "1"

    def mark_onboarded(self):
        self.set_setting("onboarded", "1")

    def schema_version(self):
        return self._read_schema_version()

    # ==================================================================
    # 任务与活动
    # ==================================================================

    def today_tasks(self):
        today = date.today().isoformat()
        return self.connection.execute(
            "SELECT id, title, completed FROM tasks WHERE due_date = ? "
            "ORDER BY completed, COALESCE(sort_order, id), id DESC",
            (today,),
        ).fetchall()

    def add_task(self, title):
        cursor = self.connection.execute(
            "INSERT INTO tasks (title, due_date) VALUES (?, ?)",
            (title.strip(), date.today().isoformat()),
        )
        self.connection.commit()
        return cursor.lastrowid

    def set_task_completed(self, task_id, completed):
        completed_at = date.today().isoformat() if completed else None
        self.connection.execute(
            "UPDATE tasks SET completed = ?, completed_at = ? WHERE id = ?",
            (int(completed), completed_at, task_id),
        )
        self.connection.commit()

    def delete_task(self, task_id):
        self.connection.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        self.connection.commit()

    def record_activity(self, activity_type, amount=1, note=None, unit=None,
                        ref_type=None, ref_id=None):
        self.connection.execute(
            """
            INSERT INTO activity_log
                (activity_type, amount, unit, logged_on, created_at, ref_type, ref_id, note)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                activity_type,
                amount,
                unit,
                date.today().isoformat(),
                datetime.now().isoformat(timespec="seconds"),
                ref_type,
                ref_id,
                note,
            ),
        )
        self.connection.commit()

    def note_vocab_progress(self, activity_type, list_id=None, list_name=None, delta=1):
        """给单词仓的两条事件记账（5.2）：`vocab_triaged` 过词 / `vocab_drilled` 专攻通过。

        与 `note_material_opened` 同一套**按天去重**：一天一个类型一行、`amount`
        累加。这一页的写入频率是全项目最高的——过一次千词的词表就是上千次——
        逐次插行会把活动日志冲成流水账，而 5.4 要的是"今天过了多少个"这一个数。

        `delta` 让过词的 `Ctrl+Z` 能把计数退回去。**不退这一步，同一页上的
        「已过 320 / 1400」与「今日过词」会当场打架**——前者读 `list_counts()` 现算，
        撤销会让它减一，而活动日志不减就是两张嘴上说两个数（5.4：口径必须统一）。
        计数夹在 0 以上：跨零点撤销时那次减法落不到昨天那一行上，不能让今天出现负数。
        """
        if activity_type not in VOCAB_ACTIVITY_TYPES:
            raise ValueError(
                f"未知的单词活动：{activity_type!r}；应为 {VOCAB_ACTIVITY_TYPES} 之一"
            )
        today = date.today().isoformat()
        with self.connection:
            row = self.connection.execute(
                "SELECT id FROM activity_log WHERE activity_type = ? AND logged_on = ?",
                (activity_type, today),
            ).fetchone()
            if row is None:
                if delta <= 0:
                    return   # 没有可退的行（跨零点的撤销），不必凭空造一条负数
                self.connection.execute(
                    "INSERT INTO activity_log "
                    "(activity_type, amount, unit, logged_on, created_at, ref_type, ref_id, note) "
                    "VALUES (?, ?, '个', ?, ?, 'word_list', ?, ?)",
                    (
                        activity_type,
                        delta,
                        today,
                        datetime.now().isoformat(timespec="seconds"),
                        list_id,
                        list_name,
                    ),
                )
                return
            # ref 只在这次带了词表时才改写（COALESCE）：「全部词表」范围下 list_id 是
            # None，那不该把上一次记下的具体词表抹掉——「继续上次」要跳到那份词表。
            self.connection.execute(
                "UPDATE activity_log SET amount = MAX(amount + ?, 0), "
                "ref_type = 'word_list', ref_id = COALESCE(?, ref_id), "
                "note = COALESCE(?, note) WHERE id = ?",
                (delta, list_id, list_name, row["id"]),
            )

    # ==================================================================
    # 单词：笔记、三态、专攻队列（D2 / D14）
    # ==================================================================

    @staticmethod
    def word_key(korean):
        return (korean or "").strip()

    def get_word_note(self, korean):
        """取一个词的笔记行（note / is_starred / state / …）。没有则返回 None。"""
        return self.connection.execute(
            "SELECT word_key, note, state, passed_at, state_updated_at, drill_rounds, is_starred "
            "FROM word_notes WHERE word_key = ?",
            (self.word_key(korean),),
        ).fetchone()

    def save_word_note(self, korean, note, is_starred):
        """保存笔记与重点标记。**不动** state —— 两者独立（6.4 第 6 条末句）。

        星标只表示"想再看一眼"，不参与专攻队列；专攻队列由三态决定。
        """
        self.connection.execute(
            """
            INSERT INTO word_notes (word_key, note, is_starred, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(word_key) DO UPDATE SET
                note=excluded.note,
                is_starred=excluded.is_starred,
                updated_at=CURRENT_TIMESTAMP
            """,
            (self.word_key(korean), note or "", int(is_starred)),
        )
        self.connection.commit()

    def _apply_word_state(self, key, state):
        """写入三态并记一条流水。**不提交**——事务边界由调用方掌握。

        拆出这个方法是因为第 3 期的过词要把"写状态"和"推进断点"放进同一个事务
        （见 `pass_word`）。若这里自行 commit，两步之间断电就会留下
        "断点过了但状态没记"的错位，用户重进时看到一个没标过的词被跳过。
        """
        if state is not None and state not in WORD_STATES:
            raise ValueError(f"未知的词状态：{state!r}；应为 {WORD_STATES} 之一或 None")
        existing = self.get_word_note(key)
        from_state = existing["state"] if existing else None
        self.connection.execute(
            """
            INSERT INTO word_notes (word_key, state, passed_at, state_updated_at, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT(word_key) DO UPDATE SET
                state=excluded.state,
                passed_at=COALESCE(word_notes.passed_at, CURRENT_TIMESTAMP),
                state_updated_at=CURRENT_TIMESTAMP,
                updated_at=CURRENT_TIMESTAMP
            """,
            (key, state),
        )
        if from_state != state:
            self.connection.execute(
                "INSERT INTO word_state_log (word_key, from_state, to_state) VALUES (?, ?, ?)",
                (key, from_state, state),
            )

    def set_word_state(self, korean, state):
        """设置词的三态（`known` / `fuzzy` / `unknown`）。"""
        key = self.word_key(korean)
        if not key:
            return
        with self.connection:
            self._apply_word_state(key, state)

    def drill_queue(self, list_id=None):
        """待专攻队列：`state IN ('fuzzy','unknown')`，不认识的排在前面（D14）。

        传 `list_id` → 该词表内的词，带序号与释义（专攻卡要显示释义，见流程 C）；
        不传 → 跨全部词表，只返回词与状态（第 7 期的"待专攻总视图"要用）。
        """
        if list_id is None:
            return self.connection.execute(
                "SELECT word_key, note, state, drill_rounds, is_starred FROM word_notes "
                "WHERE state IN ('fuzzy','unknown') "
                "ORDER BY CASE state WHEN 'unknown' THEN 0 ELSE 1 END, word_key"
            ).fetchall()
        return self.connection.execute(
            """
            SELECT w.seq, w.korean, w.meaning, w.word_key,
                   n.note, n.state, n.drill_rounds, n.is_starred
            FROM words w JOIN word_notes n ON n.word_key = w.word_key
            WHERE w.list_id = ? AND n.state IN ('fuzzy','unknown')
            ORDER BY CASE n.state WHEN 'unknown' THEN 0 ELSE 1 END, w.seq, w.id
            """,
            (list_id,),
        ).fetchall()

    # ---------------------------------------------------------------- Anki 导出（第 7 期 · M2）

    def get_anki_export_words(self, list_id=None):
        """拉取待专攻词 (`state IN ('fuzzy','unknown')`) 供 Anki 导出。

        不认识 (unknown) 排在前面，其次为模糊 (fuzzy)。
        使用 LEFT JOIN 保留孤儿笔记词，并通过 GROUP BY 去重跨表同词。
        """
        if list_id is not None:
            query = """
                SELECT w.korean,
                       COALESCE(w.meaning, '') AS meaning,
                       n.state,
                       COALESCE(n.note, '') AS note,
                       COALESCE(wl.name, '') AS list_names
                FROM words w
                JOIN word_notes n ON n.word_key = w.word_key
                LEFT JOIN word_lists wl ON wl.id = w.list_id
                WHERE w.list_id = ? AND n.state IN ('fuzzy', 'unknown')
                ORDER BY CASE n.state WHEN 'unknown' THEN 0 ELSE 1 END, w.seq, w.id
            """
            rows = self.connection.execute(query, (list_id,)).fetchall()
        else:
            query = """
                SELECT n.word_key AS korean,
                       COALESCE(MAX(w.meaning), '') AS meaning,
                       n.state,
                       COALESCE(n.note, '') AS note,
                       COALESCE(GROUP_CONCAT(DISTINCT wl.name), '') AS list_names
                FROM word_notes n
                LEFT JOIN words w ON w.word_key = n.word_key
                LEFT JOIN word_lists wl ON wl.id = w.list_id
                WHERE n.state IN ('fuzzy', 'unknown')
                GROUP BY n.word_key
                ORDER BY CASE n.state WHEN 'unknown' THEN 0 ELSE 1 END, n.word_key
            """
            rows = self.connection.execute(query).fetchall()
        return [dict(r) for r in rows]

    def count_anki_export_words(self, list_id=None):
        """统计待专攻词数量。"""
        if list_id is not None:
            row = self.connection.execute(
                """
                SELECT COUNT(DISTINCT w.word_key) AS n
                FROM words w JOIN word_notes n ON n.word_key = w.word_key
                WHERE w.list_id = ? AND n.state IN ('fuzzy', 'unknown')
                """,
                (list_id,),
            ).fetchone()
        else:
            row = self.connection.execute(
                "SELECT COUNT(*) AS n FROM word_notes WHERE state IN ('fuzzy', 'unknown')"
            ).fetchone()
        return row["n"] if row else 0

    @staticmethod
    def format_anki_export(words, delimiter="\t"):
        """将词条列表格式化为 Anki 导入文本 (TSV/CSV)。"""
        out = io.StringIO()
        sep_name = "tab" if delimiter == "\t" else "Comma"
        col_sep = "\t" if delimiter == "\t" else ","
        out.write(f"#separator:{sep_name}\n#html:true\n#tags column:4\n")
        out.write(f"#columns:韩语{col_sep}背面{col_sep}笔记{col_sep}标签\n")

        writer = csv.writer(out, delimiter=delimiter, lineterminator="\n")
        for r in words:
            korean = (r.get("korean") or "").strip()
            meaning = (r.get("meaning") or "").strip().replace("\r\n", "\n").replace("\r", "\n")
            note = (r.get("note") or "").strip().replace("\r\n", "\n").replace("\r", "\n")
            state = r.get("state", "")
            list_names = r.get("list_names", "")

            meaning_esc = html.escape(meaning).replace("\n", "<br>")
            note_esc = html.escape(note).replace("\n", "<br>")

            if meaning_esc and note_esc:
                back = f'{meaning_esc}<br><br><small style="color:#666"><b>[笔记]</b> {note_esc}</small>'
            elif meaning_esc:
                back = meaning_esc
            elif note_esc:
                back = f'<small style="color:#666"><b>[笔记]</b> {note_esc}</small>'
            else:
                back = '<small style="color:#999">（暂无释义）</small>'

            state_tag = "待专攻::不认识" if state == "unknown" else "待专攻::模糊"
            tags = ["TOPIK", "待专攻", state_tag]
            if list_names:
                for name in list_names.split(","):
                    clean = name.strip().replace(" ", "_")
                    if clean and clean not in tags:
                        tags.append(clean)
            tag_str = " ".join(tags)

            writer.writerow([korean, back, note, tag_str])
        return out.getvalue()

    def export_anki_file(self, path, list_id=None, delimiter=None):
        """执行导出写入文件。返回导出的词数。"""
        words = self.get_anki_export_words(list_id=list_id)
        if delimiter is None:
            delimiter = "," if str(path).lower().endswith(".csv") else "\t"
        content = self.format_anki_export(words, delimiter=delimiter)
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8-sig")
        return len(words)

    # ==================================================================
    # 词表：导入、浏览、过词进度（第 3 期 · P1）
    # ==================================================================

    def import_word_list(self, name, source_path, rows):
        """导入（或重导）一份词表。`rows` 是 `(seq, korean, meaning)` 的序列。

        **第一层整体重建，第二层一个字段都不碰**——这就是"重新导入不丢笔记"的
        实现方式（6.1 / 流程 A 第 6 条）：`words` 是"可从 TSV 重建"的表，整表换掉即可；
        笔记、三态、断点都在 `word_notes` / `word_list_progress` 里，按 `word_key` 关联。

        按 `source_path` 复用已有的 `word_lists` 行：同一份 TSV 再导一次应当得到
        同一个词表（并保住它的断点），而不是多出第二份。
        """
        cleaned = [
            (int(seq), (korean or "").strip(), (meaning or "").strip())
            for seq, korean, meaning in rows
            if (korean or "").strip()
        ]
        # 规范化成 str：sqlite3 不接受 Path（"Error binding parameter …: type
        # 'WindowsPath' is not supported"），而调用方传 Path 是很自然的写法。
        source_path = "" if source_path is None else str(source_path)
        with self.connection:
            existing = self.connection.execute(
                "SELECT id FROM word_lists WHERE source_path = ?", (source_path,)
            ).fetchone()
            reused = existing is not None
            if reused:
                list_id = existing["id"]
                self.connection.execute(
                    "UPDATE word_lists SET name = ?, item_count = ?, "
                    "imported_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (name, len(cleaned), list_id),
                )
                self.connection.execute("DELETE FROM words WHERE list_id = ?", (list_id,))
            else:
                cursor = self.connection.execute(
                    "INSERT INTO word_lists (name, source_path, item_count) VALUES (?, ?, ?)",
                    (name, source_path, len(cleaned)),
                )
                list_id = cursor.lastrowid

            self.connection.executemany(
                "INSERT INTO words (list_id, seq, korean, meaning, word_key) "
                "VALUES (?, ?, ?, ?, ?)",
                [
                    (list_id, seq, korean, meaning, self.word_key(korean))
                    for seq, korean, meaning in cleaned
                ],
            )
            self.connection.execute(
                "INSERT OR IGNORE INTO word_list_progress (list_id) VALUES (?)", (list_id,)
            )
        return {"list_id": list_id, "count": len(cleaned), "reused": reused}

    def delete_word_list(self, list_id):
        """删除一份词表：**第一层全删，第二层一个字段都不碰**。

        删的是 `word_lists` 行 + 它的 `words` + 它的 `word_list_progress`；
        `word_notes` / `word_state_log` 原样保留。这和 `import_word_list` 是同一条
        规则的两面（6.1）：`word_notes` 按 `word_key` 关联、**跨词表共享**（D2），
        所以这里既不能按 `list_id` 去删它，也不能"顺手"清掉只出现在这份词表里的词——
        那会连带删掉同一个词在别的词表里的笔记，而且重新导入同一份 TSV 本该把笔记
        带回来（那正是 import 侧的承诺）。

        代价是词永久消失后留下的笔记行没有界面可达（孤儿）。`word_notes` 是第二层
        用户数据，宁可留孤儿也不删。

        返回 `{"name", "count"}`；`list_id` 不存在时返回 None。
        """
        if list_id is None:
            return None
        with self.connection:
            row = self.connection.execute(
                "SELECT name FROM word_lists WHERE id = ?", (list_id,)
            ).fetchone()
            if row is None:
                return None
            # 显式删两张子表，不靠 `ON DELETE CASCADE`：建表语句是
            # `CREATE TABLE IF NOT EXISTS`，老库里 `words` 可能是更早的版本建的，
            # 那种表没有 CASCADE，全靠级联会留下指向空词表的词条。
            removed = self.connection.execute(
                "SELECT COUNT(*) AS n FROM words WHERE list_id = ?", (list_id,)
            ).fetchone()["n"]
            self.connection.execute("DELETE FROM words WHERE list_id = ?", (list_id,))
            self.connection.execute(
                "DELETE FROM word_list_progress WHERE list_id = ?", (list_id,)
            )
            self.connection.execute("DELETE FROM word_lists WHERE id = ?", (list_id,))
        return {"name": row["name"], "count": removed}

    def list_word_lists(self):
        """全部词表 + 进度摘要。供词表选择器与元信息条使用。"""
        return [
            dict(row)
            for row in self.connection.execute(
                """
                SELECT wl.id, wl.name, wl.source_path, wl.imported_at, wl.item_count,
                       COALESCE(p.cursor_seq, 0) AS cursor_seq, p.last_drilled_at,
                       p.updated_at AS progress_updated_at
                FROM word_lists wl
                LEFT JOIN word_list_progress p ON p.list_id = wl.id
                ORDER BY wl.id
                """
            ).fetchall()
        ]

    def list_counts(self, list_id):
        """一份词表的计数：总词数 / 已过 / 待专攻。

        **一律实时统计，不读存量列。** 同一个词可能同时出现在多份词表里
        （6.2 边界：状态按"词"而非"词表+行"），存量计数在多表共享同一个词时
        必然互相矛盾。`word_list_progress` 的计数列只是导出时的快照。
        """
        row = self.connection.execute(
            """
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN n.state IS NOT NULL THEN 1 ELSE 0 END) AS passed,
                   SUM(CASE WHEN n.state IN ('fuzzy','unknown') THEN 1 ELSE 0 END) AS to_drill
            FROM words w LEFT JOIN word_notes n ON n.word_key = w.word_key
            WHERE w.list_id = ?
            """,
            (list_id,),
        ).fetchone()
        return {
            "total": row["total"] or 0,
            "passed": row["passed"] or 0,
            "to_drill": row["to_drill"] or 0,
        }

    def list_words(self, list_id):
        """词表内的词条 + 各自的第二层状态，按序号。浏览表格与过词/专攻都读它。"""
        return [
            dict(row)
            for row in self.connection.execute(
                """
                SELECT w.seq, w.korean, w.meaning, w.word_key,
                       COALESCE(n.state, '') AS state, n.note,
                       COALESCE(n.is_starred, 0) AS is_starred
                FROM words w LEFT JOIN word_notes n ON n.word_key = w.word_key
                WHERE w.list_id = ?
                ORDER BY w.seq, w.id
                """,
                (list_id,),
            ).fetchall()
        ]

    def all_words(self):
        """全部词表的所有词条，带来源词表名。用于"全部词表"范围的浏览与检索
        （流程 F 跨词表查找）。"""
        return [
            dict(row)
            for row in self.connection.execute(
                """
                SELECT wl.id AS list_id, wl.name AS list_name, w.seq, w.korean, w.meaning,
                       w.word_key, COALESCE(n.state, '') AS state,
                       COALESCE(n.is_starred, 0) AS is_starred
                FROM words w
                JOIN word_lists wl ON wl.id = w.list_id
                LEFT JOIN word_notes n ON n.word_key = w.word_key
                ORDER BY wl.id, w.seq, w.id
                """
            ).fetchall()
        ]

    def get_list_cursor(self, list_id):
        """过词断点：本表最后一次判断所在的序号（0 = 还没开始过）。

        存"判到哪儿"而不是"下一词是谁"——序号来自 TSV 首列，未必从 1 连续，
        存位置一旦遇到空洞就会指向不存在的词。
        """
        row = self.connection.execute(
            "SELECT cursor_seq FROM word_list_progress WHERE list_id = ?", (list_id,)
        ).fetchone()
        return row["cursor_seq"] if row else 0

    def pass_word(self, list_id, cursor_seq, korean, state):
        """一次判断：写三态 + 把词表断点设到 `cursor_seq`，**单事务**。

        返回设置前的断点值——调用方据此实现 `Ctrl+Z` 回退（流程 B 第 7 条）。
        回退复用同一个方法：把状态写回旧值、断点写回旧位置。

        `state` 传 None 表示"撤销这次判断，回到未过词"；笔记不受影响
        （`_apply_word_state` 只动 state 三列）。
        """
        key = self.word_key(korean)
        if not key:
            return self.get_list_cursor(list_id)
        previous = self.get_list_cursor(list_id)
        with self.connection:
            self._apply_word_state(key, state)
            self.connection.execute(
                """
                INSERT INTO word_list_progress (list_id, cursor_seq, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(list_id) DO UPDATE SET
                    cursor_seq=excluded.cursor_seq, updated_at=CURRENT_TIMESTAMP
                """,
                (list_id, int(cursor_seq or 0)),
            )
            self._refresh_list_counts(list_id)
        return previous

    def _refresh_list_counts(self, list_id):
        """把 `word_list_progress` 的计数列刷成当前值。

        列是**缓存**，读取一律走 `list_counts()` 现算；这里只在写入断点时顺手刷新，
        让导出文件里的进度数字自洽。
        """
        counts = self.list_counts(list_id)
        self.connection.execute(
            "UPDATE word_list_progress SET passed_count = ?, marked_count = ?, "
            "updated_at = CURRENT_TIMESTAMP WHERE list_id = ?",
            (counts["passed"], counts["to_drill"], list_id),
        )

    def mark_drill_round(self, list_id):
        """一轮专攻结束：本表仍在队列里的词累加轮次，并记下时间（流程 C 第 6 条）。

        轮次是"这个词被反复攻了几轮"的痕迹，也是将来接记忆曲线的唯一存量信号
        （`word_state_log` 记的是单次变更，`drill_rounds` 记的是整轮）。
        """
        with self.connection:
            self.connection.execute(
                """
                UPDATE word_notes SET drill_rounds = drill_rounds + 1
                WHERE state IN ('fuzzy','unknown') AND word_key IN (
                    SELECT word_key FROM words WHERE list_id = ?
                )
                """,
                (list_id,),
            )
            self.connection.execute(
                "INSERT INTO word_list_progress (list_id, last_drilled_at, updated_at) "
                "VALUES (?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP) "
                "ON CONFLICT(list_id) DO UPDATE SET "
                "last_drilled_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP",
                (list_id,),
            )

    def word_note_counts(self):
        """词状态统计，供 P0/P1 徽标使用。"""
        rows = self.connection.execute(
            "SELECT state, COUNT(*) AS n FROM word_notes GROUP BY state"
        ).fetchall()
        counts = {"total": 0, "known": 0, "fuzzy": 0, "unknown": 0, "unpassed": 0,
                  "starred": 0}
        for row in rows:
            n = row["n"]
            counts["total"] += n
            if row["state"] in counts:
                counts[row["state"]] += n
            else:
                counts["unpassed"] += n
        counts["starred"] = self.connection.execute(
            "SELECT COUNT(*) FROM word_notes WHERE is_starred = 1"
        ).fetchone()[0]
        counts["to_drill"] = counts["fuzzy"] + counts["unknown"]
        return counts

    # ==================================================================
    # 仪表盘（P0 的四个指标、两张卡）
    #
    # 几个数的口径全部来自 5.4，而且**只有一个定义**：什么算「这一天有学习」由
    # `_has_study` 说了算，「连续学习」与「本周进度」都问它。这两张卡问的是不同的
    # 问题（坚持了多久 / 这周状态怎么样，见 D1），但「有学习」三个字必须是同一件事——
    # 否则同一天会在一张卡上算数、在另一张上不算数，而两张卡就并排放在一起。
    # ==================================================================

    _WEEK_DAYS = 7

    def _has_study(self, day):
        """这一天算不算「有学习」（5.4）：完成过任务，或产生过任何一条自动记录。

        `activity_log` 里什么类型都算——包括 `material_opened`。口径写的是「任何自动
        记录」，不是「任何学习时长」：这里判的是**工具被用过**，而「用了多久」是另一
        回事（今日亮点那张卡在管）。
        """
        day = day.isoformat()
        return bool(
            self.connection.execute(
                "SELECT 1 FROM tasks WHERE completed_at = ? LIMIT 1", (day,)
            ).fetchone()
            or self.connection.execute(
                "SELECT 1 FROM activity_log WHERE logged_on = ? LIMIT 1", (day,)
            ).fetchone()
        )

    def _current_streak(self):
        """连续有学习的自然日数（5.4）：从今天**或昨天**往回数，跨周累计、不重置。

        起点允许是昨天，是那个「或」字的落点：早上打开工具时今天还没开始学，若从今天
        起算就会显示 `0 天`——而昨天明明学过。**断的判据是「隔了一整天空着」，
        不是「今天还没动」。**
        """
        cursor_date = date.today()
        if not self._has_study(cursor_date):
            cursor_date -= timedelta(days=1)
        streak = 0
        while self._has_study(cursor_date):
            streak += 1
            cursor_date -= timedelta(days=1)
        return streak

    def week_progress(self):
        """本周（周一至周日）有学习的自然日数（5.4）。周一自然重置，不需要清理任务。

        尚未到来的那几天必然返回 False（还没到），所以周一早上看到的 `1 / 7` 是对的，
        而不是「这周才过了一天」这种需要解释的东西。
        """
        today = date.today()
        monday = today - timedelta(days=today.weekday())
        return sum(
            1 for offset in range(self._WEEK_DAYS)
            if self._has_study(monday + timedelta(days=offset))
        )

    def weekly_review_data(self, target_date=None):
        """本周（周一至周日）学习活动按天汇总与全周统计（PRODUCT_SPEC 4.1 / 5.4 / R1）。

        严格与 `_has_study()` 及 `week_progress()` 保持相同口径：
        周一为起点，周日为终点；有已完成任务或有活动日志即视为有学习。
        """
        if target_date is None:
            target_date = date.today()
        elif isinstance(target_date, str):
            target_date = date.fromisoformat(target_date)

        monday = target_date - timedelta(days=target_date.weekday())
        sunday = monday + timedelta(days=self._WEEK_DAYS - 1)

        # 1. 查询本周活动日志按天按类型合计
        rows = self.connection.execute(
            """
            SELECT logged_on, activity_type, SUM(amount) AS amount, MAX(unit) AS unit
            FROM activity_log
            WHERE logged_on BETWEEN ? AND ?
            GROUP BY logged_on, activity_type
            """,
            (monday.isoformat(), sunday.isoformat()),
        ).fetchall()

        default_units = {
            "shadowing_minutes": "分钟",
            "vocab_triaged": "个",
            "vocab_drilled": "个",
            "material_opened": "次",
        }

        activities_by_day = {}
        for r in rows:
            u = r["unit"] or default_units.get(r["activity_type"], "")
            if u == "times":
                u = "次"
            activities_by_day.setdefault(r["logged_on"], {})[r["activity_type"]] = {
                "amount": r["amount"],
                "unit": u,
            }

        # 2. 查询本周每天完成的任务数
        task_rows = self.connection.execute(
            """
            SELECT completed_at, COUNT(*) AS count
            FROM tasks
            WHERE completed = 1 AND completed_at BETWEEN ? AND ?
            GROUP BY completed_at
            """,
            (monday.isoformat(), sunday.isoformat()),
        ).fetchall()
        tasks_by_day = {r["completed_at"]: r["count"] for r in task_rows}

        # 3. 构造 7 天明细（周一至周日）
        weekdays_zh = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
        days = []
        week_totals = {
            "shadowing_minutes": 0,
            "vocab_triaged": 0,
            "vocab_drilled": 0,
            "tasks": 0,
        }

        real_today = date.today()
        for offset in range(self._WEEK_DAYS):
            d = monday + timedelta(days=offset)
            iso = d.isoformat()
            acts = activities_by_day.get(iso, {})
            task_count = tasks_by_day.get(iso, 0)
            # 有学习的判据严格与 _has_study 保持一致
            has_study = bool(acts or task_count > 0)

            # 累加周合计
            if "shadowing_minutes" in acts:
                week_totals["shadowing_minutes"] += acts["shadowing_minutes"]["amount"]
            if "vocab_triaged" in acts:
                week_totals["vocab_triaged"] += acts["vocab_triaged"]["amount"]
            if "vocab_drilled" in acts:
                week_totals["vocab_drilled"] += acts["vocab_drilled"]["amount"]
            week_totals["tasks"] += task_count

            days.append({
                "date": iso,
                "short_date": d.strftime("%m/%d"),
                "weekday": weekdays_zh[offset],
                "is_today": (d == target_date) if target_date != real_today else (d == real_today),
                "is_future": (d > target_date) if target_date != real_today else (d > real_today),
                "has_study": has_study,
                "activities": acts,
                "tasks_completed": task_count,
            })

        total_completed = self.connection.execute(
            "SELECT COUNT(*) FROM tasks WHERE completed = 1"
        ).fetchone()[0]

        return {
            "monday": monday.isoformat(),
            "sunday": sunday.isoformat(),
            "days": days,
            "week_days_studied": sum(1 for day in days if day["has_study"]),
            "week_total_days": self._WEEK_DAYS,
            "streak": self._current_streak(),
            "total_completed": total_completed,
            "totals": week_totals,
        }


    def today_activity(self):
        """今日的活动**按类型合计**，从大到小排（5.4 的排序口径）。

        P0 的「今日已自动记录」条与「今日亮点」卡读的是**同一份**结果：条要完整清单，
        卡要第一条。共用一次查询，卡上那个数就必然是条里那一行——两处对不上是不可能
        发生的，而不是「应该不会发生」。

        **合计是必须的**：`shadowing_minutes` 每一次冲刷都落一行（暂停、切曲、离开页面
        各算一次），一天下来是好几行，而 5.3 的条上要的是「跟读 24 分钟」这一个数。
        词的两条事件本来就按天去重（见 `note_vocab_progress`），合计对它们是恒等变换——
        同一条规则套两类数据，不必分情况写。

        单位取 `MAX(unit)` 而不是跟着 GROUP BY：**老库里有一条 `unit IS NULL` 的
        `shadowing_minutes`**（那是补上 `unit` 参数之前的写入）。按 `(类型, 单位)` 分组会
        把它拆成第二条「跟读」，而 `MAX` 会忽略 NULL —— 这里就把它认领回来。
        不去 UPDATE 那一行：改一个历史字段是一次只有整洁收益的数据迁移，
        而按类型分组本来就要求"一个类型一个单位"，NULL 恰好在读取层就能消化掉。
        """
        rows = self.connection.execute(
            "SELECT activity_type, SUM(amount) AS amount, MAX(unit) AS unit, "
            "MAX(ref_id) AS ref_id, MAX(note) AS note, COUNT(*) AS entries "
            "FROM activity_log WHERE logged_on = ? GROUP BY activity_type",
            (date.today().isoformat(),),
        ).fetchall()
        return sorted(
            (dict(row) for row in rows),
            key=lambda row: (
                HIGHLIGHT_RANK.get(row["activity_type"], HIGHLIGHT_OTHER_RANK),
                -row["amount"],
            ),
        )

    def resume_context(self):
        """P0「继续上次」卡的候选上下文（2.5），**最多两条，各自独立成立**。

        2.5 把两个「未完成的上下文」都定义成这张卡的内容：未听完的音频、有断点的词表。
        所以这里返回列表——两者都在时卡上就是两行。都没有时返回空列表，调用方据此
        **整张卡隐藏**（与 5.3 同一条纪律：没有就说没有，不写「暂无可继续的内容」）。
        """
        contexts = []

        state = self.playback_state()
        if state and state["track_id"] is not None:
            track = self.get_track(state["track_id"])
            if track is not None:
                progress = self.track_progress(state["track_id"])
                # 「未听完」的判据是**真的听过**（`listened_ms > 0`）且没标成已磕完。
                #
                # 两头都不能省：
                #   * 只看"存在 `playback_state`"不行——它在切曲那一刻就写，仅仅点开
                #     看一眼的曲目会立刻出现在「继续跟读」里，而它一次都没听过。
                #   * 用 `position_ms > 0` 代替 `listened_ms` 也不行——位置是**可回零**
                #     的（从头重听、或者冲刷时播放器还没有有效 source），而"听过多久"
                #     只增不减，它才是 4.3 说的「时长归曲目」那个字段。
                if progress["listened_ms"] > 0 and progress["status"] != "done":
                    playlist_name = None
                    segment = None
                    if state["playlist_id"] is not None:
                        playlist = next(
                            (
                                item
                                for item in self.list_playlists()
                                if item["id"] == state["playlist_id"]
                            ),
                            None,
                        )
                        if playlist is not None:
                            playlist_name = playlist["name"]
                            for index, item in enumerate(
                                self.playlist_items(state["playlist_id"]), start=1
                            ):
                                if item["track_id"] == state["track_id"]:
                                    segment = index
                                    break
                    contexts.append(
                        {
                            "kind": "audio",
                            "title": track["title"],
                            "playlist": playlist_name,
                            "segment": segment,
                            "position_ms": progress["position_ms"],
                            "listened_ms": progress["listened_ms"],
                            "duration_ms": track["duration_ms"],
                        }
                    )

        # 词表取**最近动过的那一份**（`word_list_progress.updated_at`），而不是第一份：
        # 手上有好几份词表时，「继续」要接的是刚才在做的那一份。
        # 计数复用 `list_counts()` 而不是在这里再写一遍那三个 SUM——同一页上的两个数
        # 必须由同一段代码算出来（5.4），复制一份表达式正是两个数开始不一致的起点。
        candidates = sorted(
            self.list_word_lists(),
            key=lambda row: (row["progress_updated_at"] or row["imported_at"] or ""),
            reverse=True,
        )
        for row in candidates:
            counts = self.list_counts(row["id"])
            # 「有断点」本身不算数：断点停在最后一个词时那一份其实已经过完了。
            # 真正没做完的是「还有待专攻的词」，或者「过了但没到头」。
            unfinished = counts["to_drill"] > 0 or (
                row["cursor_seq"] and counts["passed"] < counts["total"]
            )
            if unfinished:
                contexts.append(
                    {
                        "kind": "vocab",
                        "list_id": row["id"],
                        "name": row["name"],
                        "passed": counts["passed"],
                        "total": counts["total"],
                        "to_drill": counts["to_drill"],
                    }
                )
                break

        return contexts

    def dashboard_stats(self):
        """P0 首屏要的数字（4.1 的 H2 / H5）。

        「累计完成」仍在这一份里，但它**不再占首屏卡位**——D1 把它降到了 H5 本周概览区。
        """
        today = date.today().isoformat()
        completed_today = self.connection.execute(
            "SELECT COUNT(*) FROM tasks WHERE due_date = ? AND completed = 1", (today,)
        ).fetchone()[0]
        total_today = self.connection.execute(
            "SELECT COUNT(*) FROM tasks WHERE due_date = ?", (today,)
        ).fetchone()[0]
        total_completed = self.connection.execute(
            "SELECT COUNT(*) FROM tasks WHERE completed = 1"
        ).fetchone()[0]
        return {
            "completed_today": completed_today,
            "total_today": total_today,
            "total_completed": total_completed,
            "streak": self._current_streak(),
            "week_days": self.week_progress(),
            "week_total": self._WEEK_DAYS,
        }

    # ==================================================================
    # 资料库索引（P3 / D6 方案 C）
    #
    # 分层与词表完全同构，但**关联键不同**：词的第二层挂在 `word_key` 上（D2），
    # 资料的第二层挂在 `path` 上。理由是同一条：`materials` 是第一层，随时可以整个
    # 删掉重建；而标签、打开次数、阅读进度是用户花时间产生的，重建后必须还能认领回去。
    # 所以它们的主键是 `path`（D6 明写"重建后标签按 path 重新关联"），
    # 而不是 `material_id`——自增 id 在一次重建后就指向别的文件了。
    # ==================================================================

    # 搬家时每张第二层表要搬的列（顺序即插入顺序，别用 _column_names，那是集合）
    # 按 path 关联的第二层表。`relocate_material` 与 `_merge_material_row` 都靠它把
    # 用户数据搬到新路径——**P4 的碎片数据也在其中**：一张截图在 P3 里「重新定位」之后，
    # 它的标题、说明、标签必须跟着走。漏掉这一步，P4 那边会为同一个文件另起一行
    # （标题退回文件名），旧行带着笔记变成孤儿——正是 6.1 说的"用户产出丢失"。
    _PATH_KEYED_TABLES = {
        "material_tags": ("path", "tag", "created_at"),
        "material_usage": ("path", "last_opened_at", "open_count", "updated_at"),
        "reading_progress": ("path", "last_page", "total_pages", "updated_at"),
        "snippets": (
            "path", "title", "source_subject", "created_at", "width", "height", "missing", "note",
        ),
        "snippet_tags": ("path", "tag", "created_at"),
    }

    def library_snapshot(self):
        """列表页的**唯一数据来源**：索引行 + 三张第二层表，按 path 合并。

        一次取全量再在内存里筛选/排序，而不是每敲一个字就去查一次库：资料条目数
        在千级，一次全量读（四条 SELECT）比"每次过滤一条带子查询的 SQL"更快也更好读。
        """
        items = [
            dict(row)
            for row in self.connection.execute(
                "SELECT id, path, name, subject, ext, size, mtime, missing FROM materials"
            )
        ]
        by_path = {item["path"]: item for item in items}
        for item in items:
            item.update(tags=[], last_opened_at=None, open_count=0, last_page=0, total_pages=None)

        for row in self.connection.execute("SELECT path, tag FROM material_tags ORDER BY tag"):
            item = by_path.get(row["path"])
            if item is not None:
                item["tags"].append(row["tag"])
        for row in self.connection.execute(
            "SELECT path, last_opened_at, open_count FROM material_usage"
        ):
            item = by_path.get(row["path"])
            if item is not None:
                item["last_opened_at"] = row["last_opened_at"]
                item["open_count"] = row["open_count"]
        for row in self.connection.execute(
            "SELECT path, last_page, total_pages FROM reading_progress"
        ):
            item = by_path.get(row["path"])
            if item is not None:
                item["last_page"] = row["last_page"]
                item["total_pages"] = row["total_pages"]
        return items

    def apply_material_scan(self, records, complete=True):
        """把一次扫描结果落库（增量）。**只动 `materials`**。

        第二层三张表一个字段都不碰——它们按 path 关联，所以"重新扫描"对用户数据
        天然是无害的，这也是 D6 敢说"索引可以随时重建"的前提。

        `(size, mtime)` 都没变就是 unchanged，不写库；这样日常启动只更新变化的那几行。

        `complete=False`（扫描被取消）时**禁止标记缺失**：没走完的那棵树里每一个文件
        都会"看起来不在"，把它们全标成丢失，是取消一次索引就毁掉整页状态的做法。
        """
        added = updated = unchanged = 0
        with self.connection:
            # **按 `path_key` 比对，不按原字符串**：Windows 上正斜杠与反斜杠指向同一个
            # 文件却比不相等，这一条差异曾经让"重新定位过的文件"每次扫描都多出一行。
            # 命中之后写库一律用 `id`，于是行的形态再怎么变都不会错位。
            existing = {
                path_key(row["path"]): row
                for row in self.connection.execute(
                    "SELECT id, path, size, mtime, missing FROM materials"
                )
            }
            seen = set()
            inserts = []
            updates = []
            for record in records:
                stored = normalize_path(record["path"])
                key = path_key(stored)
                seen.add(key)
                row = existing.get(key)
                if row is None:
                    inserts.append(
                        (
                            stored,
                            record["name"],
                            record["subject"],
                            record["ext"],
                            record["size"],
                            record["mtime"],
                        )
                    )
                    added += 1
                elif row["size"] != record["size"] or row["mtime"] != record["mtime"] or row["missing"]:
                    # missing 也走这条路：文件回来了，一行 UPDATE 就把它复原
                    updates.append(
                        (
                            record["name"],
                            record["subject"],
                            record["ext"],
                            record["size"],
                            record["mtime"],
                            row["id"],
                        )
                    )
                    updated += 1
                else:
                    unchanged += 1

            if inserts:
                self.connection.executemany(
                    "INSERT INTO materials (path, name, subject, ext, size, mtime, indexed_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
                    inserts,
                )
            if updates:
                self.connection.executemany(
                    "UPDATE materials SET name = ?, subject = ?, ext = ?, size = ?, mtime = ?, "
                    "missing = 0 WHERE id = ?",
                    updates,
                )

            missing_ids = [
                row["id"] for key, row in existing.items() if key not in seen and not row["missing"]
            ]
            if complete and missing_ids:
                self.connection.executemany(
                    "UPDATE materials SET missing = 1 WHERE id = ?",
                    [(row_id,) for row_id in missing_ids],
                )

            self.connection.execute(
                """
                INSERT INTO settings (key, value, updated_at)
                VALUES ('last_indexed_at', ?, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP
                """,
                (datetime.now().isoformat(timespec="seconds"),),
            )

        return {
            "added": added,
            "updated": updated,
            "unchanged": unchanged,
            "missing": len(missing_ids) if complete else 0,
        }

    def last_indexed_at(self):
        return self.get_setting("last_indexed_at", "")

    def material_row(self, path):
        row = self.connection.execute(
            "SELECT id, path, name, subject, ext, size, mtime, missing FROM materials WHERE path = ?",
            (normalize_path(path),),
        ).fetchone()
        return dict(row) if row else None

    # ---- 标签（第二层） ----

    def all_material_tags(self):
        """[(tag, 用了几个文件)]，按用量降序——过滤下拉里常点的一定排在前面。"""
        return self.connection.execute(
            "SELECT tag, COUNT(*) AS n FROM material_tags GROUP BY tag ORDER BY n DESC, tag"
        ).fetchall()

    def add_material_tag(self, path, tag):
        tag = (tag or "").strip().lstrip("#").strip()
        if not path or not tag:
            return False
        path = normalize_path(path)
        self.connection.execute(
            "INSERT OR IGNORE INTO material_tags (path, tag, created_at) VALUES (?, ?, ?)",
            (path, tag, datetime.now().isoformat(timespec="seconds")),
        )
        self.connection.commit()
        return True

    def remove_material_tag(self, path, tag):
        self.connection.execute(
            "DELETE FROM material_tags WHERE path = ? AND tag = ?", (normalize_path(path), tag)
        )
        self.connection.commit()

    # ---- 阅读进度（第二层） ----

    def set_reading_progress(self, path, last_page, total_pages=None):
        self.connection.execute(
            """
            INSERT INTO reading_progress (path, last_page, total_pages, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(path) DO UPDATE SET
                last_page = excluded.last_page,
                total_pages = COALESCE(excluded.total_pages, reading_progress.total_pages),
                updated_at = CURRENT_TIMESTAMP
            """,
            (normalize_path(path), max(0, int(last_page or 0)), total_pages),
        )
        self.connection.commit()

    # ---- 打开统计（第二层） ----

    def note_material_opened(self, path):
        """记一次打开：`material_usage` 累加，并往 `activity_log` 落一条流水。

        流水**按天去重**（同一天同一个文件只留一行，amount 累加）：这一页的打开频率
        远高于其他模块，逐次插入会让活动日志被同一个文件刷屏，而第 7 期的"本周回顾"
        要的是"哪天动过它、动了多少次"，不是每一次点击的流水账。
        """
        row = self.material_row(path)
        if row is None:
            return None
        path = row["path"]   # 用规范化之后的那份，别把用户给的形态写进第二层表
        today = date.today().isoformat()
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO material_usage (path, last_opened_at, open_count, updated_at)
                VALUES (?, ?, 1, CURRENT_TIMESTAMP)
                ON CONFLICT(path) DO UPDATE SET
                    last_opened_at = excluded.last_opened_at,
                    open_count = material_usage.open_count + 1,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (path, datetime.now().isoformat(timespec="seconds")),
            )
            existing = self.connection.execute(
                "SELECT id FROM activity_log WHERE activity_type = 'material_opened' "
                "AND ref_type = 'material' AND ref_id = ? AND logged_on = ?",
                (row["id"], today),
            ).fetchone()
            if existing is None:
                self.connection.execute(
                    "INSERT INTO activity_log "
                    "(activity_type, amount, unit, logged_on, created_at, ref_type, ref_id, note) "
                    "VALUES ('material_opened', 1, 'times', ?, ?, 'material', ?, ?)",
                    (today, datetime.now().isoformat(timespec="seconds"), row["id"], row["name"]),
                )
            else:
                self.connection.execute(
                    "UPDATE activity_log SET amount = amount + 1 WHERE id = ?", (existing["id"],)
                )
        # 返回新的计数，让调用方**就地**更新那一行，不必为了刷新"上次打开"重建整张列表
        usage = self.connection.execute(
            "SELECT last_opened_at, open_count FROM material_usage WHERE path = ?", (path,)
        ).fetchone()
        return {
            "id": row["id"],
            "name": row["name"],
            "path": row["path"],
            "last_opened_at": usage["last_opened_at"] if usage else None,
            "open_count": usage["open_count"] if usage else 1,
        }

    def mark_material_missing(self, path, missing=True):
        """打开时发现文件不在了（4.4 状态表）。索引行留着，只翻 missing 标记——
        删掉它等于把标签与阅读进度一起变成孤儿，而"重新定位"正是为了让它们能接上。

        `missing=False` 是反方向的同一件事：文件在原位又出现了。少了这一条，
        标记就只会单向累积——把文件放回去之后那一行会永远灰着。
        """
        self.connection.execute(
            "UPDATE materials SET missing = ? WHERE path = ?",
            (1 if missing else 0, normalize_path(path)),
        )
        self.connection.commit()

    def remove_material_row(self, path):
        """把一份资料从索引里删掉（用户在 P3 右键删除文件时调用）。

        **只删索引行，第二层三张表一个字段都不碰**——与 `delete_word_list` 同一条规则。
        理由在那里写得很清楚，这里再补一条：文件是送进**回收站**的，"还原之后标签与
        '看到第 12 页'自动跟回来"正是第二层挂在 `path` 上换来的能力。代价是文件不还原时
        留下几行无主的第二层数据，可接受（6.2 已认可这个代价）。

        P4 的碎片行同理保留，只标 `missing=1`：它的标题与说明是用户写的，不该跟着文件
        一起消失。标了之后碎片页会把它隐去，文件还原回来它又出现。
        """
        path = normalize_path(path)
        with self.connection:
            self.connection.execute("DELETE FROM materials WHERE path = ?", (path,))
            self.connection.execute("UPDATE snippets SET missing = 1 WHERE path = ?", (path,))

    def relocate_material(self, old_path, new_path):
        """文件被移动/改名后，把索引行与**第二层数据一起**搬到新路径。

        返回新的索引行；`old_path` 不存在时返回 None。
        这正是把第二层挂在 path 上换来的能力：用户自己整理过文件夹之后，
        标签和"看到第 12 页"应该跟着走，而不是重新标一遍。

        **科目要从新路径重新推**，不能沿用旧值：把一个文件从 `单词/` 挪到 `阅读/`，
        最直观的期望就是它换到阅读去了。漏掉这一步的后果是它按新位置显示、
        却永远留在旧的科目筛选里——"分类不对"。
        """
        row = self.material_row(old_path)
        if row is None:
            return None
        new_path = normalize_path(new_path)
        name = os.path.basename(new_path)
        ext = os.path.splitext(name)[1].lower()
        subject = subject_of(new_path, self.get_material_root()) or row["subject"]
        try:
            stat = os.stat(new_path)
            size, mtime = stat.st_size, int(stat.st_mtime)
        except OSError:
            size, mtime = row["size"], row["mtime"]

        with self.connection:
            clash = self.connection.execute(
                "SELECT id, path FROM materials WHERE path = ?", (new_path,)
            ).fetchone()
            if clash is not None:
                # 新路径已经在索引里（重名，或扫描已经先一步发现了新位置）：
                # 把这一行的第二层数据**并过去**再删它，不要连同标签一起丢掉
                self._merge_material_row(row, dict(clash))
            else:
                self.connection.execute(
                    "UPDATE materials SET path = ?, name = ?, subject = ?, ext = ?, size = ?, "
                    "mtime = ?, missing = 0 WHERE id = ?",
                    (new_path, name, subject, ext, size, mtime, row["id"]),
                )
            for table, columns in self._PATH_KEYED_TABLES.items():
                moved = self.connection.execute(
                    f"SELECT * FROM {table} WHERE path = ?", (row["path"],)
                ).fetchall()
                if moved:
                    self.connection.executemany(
                        f"INSERT OR IGNORE INTO {table} ({', '.join(columns)}) "
                        f"VALUES ({', '.join('?' for _ in columns)})",
                        [
                            tuple(new_path if column == "path" else record[column] for column in columns)
                            for record in moved
                        ],
                    )
                    self.connection.execute(f"DELETE FROM {table} WHERE path = ?", (row["path"],))
        return self.material_row(new_path)

    # ==================================================================
    # 知识碎片（P4，第 6 期）
    # ==================================================================

    def list_snippets(self):
        """碎片清单的**唯一数据来源**：`snippets` 行 + `snippet_tags`，按 path 合并。

        与 `library_snapshot` 同一套做法（一次取全量、在内存里筛），理由也一样：
        条目在百到千级，一次全量读比"每敲一个字查一次带子查询的 SQL"更快也更好读。
        """
        rows = [
            dict(row)
            for row in self.connection.execute(
                "SELECT path, title, source_subject, created_at, width, height, missing, note "
                "FROM snippets"
            )
        ]
        by_path = {row["path"]: row for row in rows}
        for row in rows:
            row["tags"] = []
        for tag_row in self.connection.execute("SELECT path, tag FROM snippet_tags ORDER BY tag"):
            row = by_path.get(tag_row["path"])
            if row is not None:
                row["tags"].append(tag_row["tag"])
        return rows

    def register_snippets(self, records):
        """把 `core/snippets.py` 产出的第一层清单落库。**title 与 note 一个字段都不碰。**

        新行才写 `INSERT`（`OR IGNORE`，于是用户改过的标题不会被文件名覆盖回去），
        已存在的行只刷新"来源"与"文件还在不在"这两个可重建字段。返回新增的碎片数。
        """
        if not records:
            return 0
        stamp = datetime.now().isoformat(timespec="seconds")
        cursor = self.connection.executemany(
            "INSERT OR IGNORE INTO snippets (path, title, source_subject, created_at, missing) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                (row["path"], row["name"], row["source_subject"], stamp, int(bool(row["missing"])))
                for row in records
            ],
        )
        added = max(0, cursor.rowcount)
        self.connection.executemany(
            "UPDATE snippets SET source_subject = ?, missing = ? WHERE path = ?",
            [
                (row["source_subject"], int(bool(row["missing"])), row["path"])
                for row in records
            ],
        )
        self.connection.commit()
        return added

    def get_snippet(self, path):
        row = self.connection.execute(
            "SELECT path, title, source_subject, created_at, width, height, missing, note "
            "FROM snippets WHERE path = ?",
            (normalize_path(path),),
        ).fetchone()
        return dict(row) if row else None

    def rename_snippet(self, path, title):
        """改碎片的标题。**第二层**：默认是文件名，改过之后就是用户自己起的名。"""
        self.connection.execute(
            "UPDATE snippets SET title = ? WHERE path = ?",
            ((title or "").strip(), normalize_path(path)),
        )
        self.connection.commit()

    def save_snippet_note(self, path, note):
        self.connection.execute(
            "UPDATE snippets SET note = ? WHERE path = ?", (note or "", normalize_path(path))
        )
        self.connection.commit()

    def set_snippet_size(self, path, width, height):
        """记下原图尺寸。**选中时才写**——为了一个显示用的数字去读遍所有图片的头部，
        是把"打开这一页"的成本抬成"解析整个碎片目录"（与 `pdf_page_count` 同一条理由）。"""
        self.connection.execute(
            "UPDATE snippets SET width = ?, height = ? WHERE path = ? AND (width = 0 OR height = 0)",
            (int(width or 0), int(height or 0), normalize_path(path)),
        )
        self.connection.commit()

    # ---- 碎片标签（第二层） ----

    def snippet_tags(self, path):
        """某一张碎片的标签。加/删标签之后就地刷新一行时用它——比 `list_snippets()`
        把全量重读一遍便宜得多，也免得视图自己去拼 SQL。"""
        return [
            row["tag"]
            for row in self.connection.execute(
                "SELECT tag FROM snippet_tags WHERE path = ? ORDER BY tag", (normalize_path(path),)
            )
        ]

    def all_snippet_tags(self):
        """[(tag, 用了几张图)]，按用量降序——过滤下拉里常点的一定排在前面。"""
        return self.connection.execute(
            "SELECT tag, COUNT(*) AS n FROM snippet_tags GROUP BY tag ORDER BY n DESC, tag"
        ).fetchall()

    def add_snippet_tag(self, path, tag):
        tag = (tag or "").strip().lstrip("#").strip()
        if not path or not tag:
            return False
        self.connection.execute(
            "INSERT OR IGNORE INTO snippet_tags (path, tag, created_at) VALUES (?, ?, ?)",
            (normalize_path(path), tag, datetime.now().isoformat(timespec="seconds")),
        )
        self.connection.commit()
        return True

    def remove_snippet_tag(self, path, tag):
        self.connection.execute(
            "DELETE FROM snippet_tags WHERE path = ? AND tag = ?", (normalize_path(path), tag)
        )
        self.connection.commit()

    # ==================================================================
    # 音频库与播放列表（P2，第 5 期）
    # ==================================================================

    # ---- 曲目 ----

    def add_audio_track(self, path, title, size=None, content_hash=None,
                        source_path=None, duration_ms=None):
        """登记一个**已经复制进音频库**的文件，返回 `track_id`。

        `path` 是音频库内的相对路径（已经是 `normalize_path` 过的形态）：
        存相对路径而不是绝对路径，音频库跟着项目一起搬走时不会整片失效——
        这正是 P3 那次的教训（D:/ 与 D:\\ 是同一条路径的两种写法）。

        同一个 `path` 重复登记时返回已有行，不报错：复制阶段已经去过重，
        这里再撞上只可能是"同一文件被导入了两次"，复用比抛异常有用。
        """
        existing = self.connection.execute(
            "SELECT id FROM audio_tracks WHERE path = ?", (path,)
        ).fetchone()
        if existing:
            return existing["id"]
        cursor = self.connection.execute(
            """
            INSERT INTO audio_tracks
                (path, title, size, content_hash, imported_at, source_path, duration_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                path,
                title,
                size,
                content_hash,
                datetime.now().isoformat(timespec="seconds"),
                source_path,
                duration_ms,
            ),
        )
        self.connection.commit()
        return cursor.lastrowid

    def find_track_by_hash(self, content_hash):
        """按内容哈希找已入库的曲目——导入去重的**唯一**依据（D15）。

        没有哈希（老数据或算不出来）时不猜：返回 `None`，让调用方正常复制一份。
        """
        if not content_hash:
            return None
        row = self.connection.execute(
            "SELECT * FROM audio_tracks WHERE content_hash = ? ORDER BY id LIMIT 1",
            (content_hash,),
        ).fetchone()
        return dict(row) if row else None

    def get_track(self, track_id):
        row = self.connection.execute(
            "SELECT * FROM audio_tracks WHERE id = ?", (track_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_tracks(self):
        return [
            dict(row)
            for row in self.connection.execute(
                "SELECT * FROM audio_tracks ORDER BY title COLLATE NOCASE, id"
            )
        ]

    def set_track_duration(self, track_id, duration_ms):
        """回填时长。**只写第一次拿到的那次**——播放器给的是毫秒整数，
        但不同解码后端可能给出略有出入的值，来回覆盖没有意义。"""
        if not duration_ms or duration_ms <= 0:
            return
        self.connection.execute(
            "UPDATE audio_tracks SET duration_ms = ? WHERE id = ? AND "
            "(duration_ms IS NULL OR duration_ms <= 0)",
            (int(duration_ms), track_id),
        )
        self.connection.commit()

    def save_track_text(self, track_id, kr_text=None, cn_text=None):
        """按曲目保存原文 / 译文（第二层，不能从音频重建，见 6.2）。

        `None` 表示"这次不改这一栏"，空串是真值（用户把内容删空了，就该存空）。
        """
        assignments, values = [], []
        if kr_text is not None:
            assignments.append("kr_text = ?")
            values.append(kr_text)
        if cn_text is not None:
            assignments.append("cn_text = ?")
            values.append(cn_text)
        if not assignments:
            return
        values.append(track_id)
        self.connection.execute(
            f"UPDATE audio_tracks SET {', '.join(assignments)} WHERE id = ?", tuple(values)
        )
        self.connection.commit()

    def delete_track(self, track_id):
        """把曲目移出音频库（**只删登记与它的进度，不删磁盘上的文件**）。

        真要清磁盘得由调用方做，并且要说清楚——删用户的音频文件不是数据层该悄悄干的事。
        """
        with self.connection:
            self.connection.execute("DELETE FROM playlist_items WHERE track_id = ?", (track_id,))
            self.connection.execute("DELETE FROM track_progress WHERE track_id = ?", (track_id,))
            self.connection.execute("DELETE FROM track_segments WHERE track_id = ?", (track_id,))
            self.connection.execute("DELETE FROM audio_tracks WHERE id = ?", (track_id,))
            self.connection.execute(
                "UPDATE playback_state SET track_id = NULL WHERE track_id = ?", (track_id,)
            )

    # ---- 播放列表 ----

    def create_playlist(self, name):
        cursor = self.connection.execute(
            "INSERT INTO playlists (name, created_at) VALUES (?, ?)",
            (name, datetime.now().isoformat(timespec="seconds")),
        )
        self.connection.commit()
        return cursor.lastrowid

    def rename_playlist(self, playlist_id, name):
        self.connection.execute("UPDATE playlists SET name = ? WHERE id = ?", (name, playlist_id))
        self.connection.commit()

    def delete_playlist(self, playlist_id):
        """删列表**连带删它的条目**（`playlist_items` 是列表的一部分），
        但**曲目本身、文本、进度一律不动**——换一个列表继续听不该丢跟读记录。"""
        with self.connection:
            self.connection.execute("DELETE FROM playlist_items WHERE playlist_id = ?", (playlist_id,))
            self.connection.execute("DELETE FROM playlists WHERE id = ?", (playlist_id,))
            self.connection.execute(
                "UPDATE playback_state SET playlist_id = NULL WHERE playlist_id = ?",
                (playlist_id,),
            )

    def list_playlists(self):
        """列表 + 进度摘要 + 挂的两份 PDF。计数**每次现算**，与 P1 的 `list_counts`
        同一个纪律：存下来的计数器迟早会和事实不一致。"""
        return [
            dict(row)
            for row in self.connection.execute(
                """
                SELECT p.id, p.name, p.created_at, p.kr_pdf, p.cn_pdf, p.kr_page, p.cn_page,
                       COUNT(i.track_id) AS track_count,
                       SUM(CASE WHEN g.status = 'done' THEN 1 ELSE 0 END) AS done_count
                FROM playlists p
                LEFT JOIN playlist_items i ON i.playlist_id = p.id
                LEFT JOIN track_progress g ON g.track_id = i.track_id
                GROUP BY p.id
                ORDER BY p.id
                """
            )
        ]

    def set_playlist_documents(self, playlist_id, kr_pdf="keep", cn_pdf="keep",
                               kr_page="keep", cn_page="keep"):
        """挂上/换掉这份列表的两份 PDF，以及它们各自读到第几页。

        存的是**路径**，不复制文件：PDF 可能有几十上百 MB，而它们本来就躺在用户的资料树里
        （P3 也是按路径索引 PDF 的）。`"keep"` 哨兵与 `save_track_progress` 同理——
        `None` 在这里是合法值（表示"清掉这一份"），不能拿它当"不修改"。

        换一份 PDF 时页码由调用方一并重置（`kr_page=1`）：新文件的页数和旧的对不上，
        留着旧页码只会让"接着上次读"跳到一个毫无关系的位置。
        """
        fields = {}
        if kr_pdf != "keep":
            fields["kr_pdf"] = normalize_path(kr_pdf) if kr_pdf else None
        if cn_pdf != "keep":
            fields["cn_pdf"] = normalize_path(cn_pdf) if cn_pdf else None
        if kr_page != "keep":
            fields["kr_page"] = max(1, int(kr_page)) if kr_page else None
        if cn_page != "keep":
            fields["cn_page"] = max(1, int(cn_page)) if cn_page else None
        if not fields:
            return
        self.connection.execute(
            f"UPDATE playlists SET {', '.join(f'{name} = ?' for name in fields)} WHERE id = ?",
            (*fields.values(), playlist_id),
        )
        self.connection.commit()

    def add_track_to_playlist(self, playlist_id, track_id):
        """把曲目追加到列表末尾。已经在列表里就返回 `False`（不重复加、也不报错）。"""
        exists = self.connection.execute(
            "SELECT 1 FROM playlist_items WHERE playlist_id = ? AND track_id = ?",
            (playlist_id, track_id),
        ).fetchone()
        if exists:
            return False
        cursor = self.connection.execute(
            "SELECT COALESCE(MAX(sort_order), -1) AS last FROM playlist_items WHERE playlist_id = ?",
            (playlist_id,),
        ).fetchone()
        self.connection.execute(
            "INSERT INTO playlist_items (playlist_id, track_id, sort_order) VALUES (?, ?, ?)",
            (playlist_id, track_id, (cursor["last"] or -1) + 1),
        )
        self.connection.commit()
        return True

    def remove_track_from_playlist(self, playlist_id, track_id):
        self.connection.execute(
            "DELETE FROM playlist_items WHERE playlist_id = ? AND track_id = ?",
            (playlist_id, track_id),
        )
        self.connection.commit()

    def playlist_items(self, playlist_id):
        """列表里的一段段曲目，连着它自己的进度一起取出来。

        进度用 LEFT JOIN：没听过的曲目**没有** `track_progress` 行，
        那是"未听"而不是"没有这一段"——用内连接会让新导入的曲目从列表里消失。
        """
        if playlist_id is None:
            return []
        return [
            dict(row)
            for row in self.connection.execute(
                """
                SELECT t.id AS track_id, t.path, t.title, t.duration_ms, t.size,
                       t.content_hash, t.kr_text, t.cn_text,
                       i.sort_order,
                       g.position_ms, g.pos_a_ms, g.pos_b_ms, g.speed, g.status,
                       g.listened_ms, g.loop_count
                FROM playlist_items i
                JOIN audio_tracks t ON t.id = i.track_id
                LEFT JOIN track_progress g ON g.track_id = t.id
                WHERE i.playlist_id = ?
                ORDER BY i.sort_order, t.id
                """,
                (playlist_id,),
            )
        ]

    def reorder_playlist(self, playlist_id, ordered_track_ids):
        """按给定顺序重写 `sort_order`（拖拽排序的落库方式）。

        整体重写而不是"算出移动的那一对"：顺序是**一个**事实，只改局部迟早会
        出现两段相同的 `sort_order`，那样的列表顺序取决于查询计划而不是用户。
        """
        with self.connection:
            for order, track_id in enumerate(ordered_track_ids):
                self.connection.execute(
                    "UPDATE playlist_items SET sort_order = ? WHERE playlist_id = ? AND track_id = ?",
                    (order, playlist_id, track_id),
                )

    # ---- 曲目进度 ----

    def track_progress(self, track_id):
        """曲目进度；没有行时返回**默认值**而不是 `None`。

        调用方拿到的永远是一份可用的上下文（未听、位置 0、1.0 倍速），
        省掉每个使用点各写一遍 `if is None`——那种写法漏一处就是崩溃。
        """
        row = self.connection.execute(
            "SELECT * FROM track_progress WHERE track_id = ?", (track_id,)
        ).fetchone()
        if row:
            return dict(row)
        return {
            "track_id": track_id,
            "position_ms": 0,
            "pos_a_ms": None,
            "pos_b_ms": None,
            "speed": 1.0,
            "status": None,
            "listened_ms": 0,
            "loop_count": 0,
            "updated_at": None,
        }

    def save_track_progress(self, track_id, position_ms=None, pos_a_ms="keep",
                            pos_b_ms="keep", speed=None, status=None,
                            listened_ms=None, loop_count=None):
        """写曲目进度。`pos_a_ms` / `pos_b_ms` 的默认值 `"keep"` 是**故意的**：
        `None` 在这两栏是合法值（表示"没有设 A 点"），拿 `None` 当"不修改"
        会永远清不掉 A 点。所以缺省用哨兵，`None` 一律按真实值写入。"""
        fields = {}
        if position_ms is not None:
            fields["position_ms"] = max(0, int(position_ms))
        if pos_a_ms != "keep":
            fields["pos_a_ms"] = pos_a_ms
        if pos_b_ms != "keep":
            fields["pos_b_ms"] = pos_b_ms
        if speed is not None:
            fields["speed"] = float(speed)
        if status is not None:
            fields["status"] = status
        if listened_ms is not None:
            fields["listened_ms"] = max(0, int(listened_ms))
        if loop_count is not None:
            fields["loop_count"] = max(0, int(loop_count))
        if not fields:
            return
        fields["updated_at"] = datetime.now().isoformat(timespec="seconds")

        columns = ", ".join(fields)
        placeholders = ", ".join("?" for _ in fields)
        self.connection.execute(
            f"INSERT INTO track_progress (track_id, {columns}) VALUES (?, {placeholders}) "
            f"ON CONFLICT(track_id) DO UPDATE SET "
            + ", ".join(f"{name}=excluded.{name}" for name in fields),
            (track_id, *fields.values()),
        )
        self.connection.commit()

    def add_track_listened(self, track_id, milliseconds, loops=0):
        """累计跟读时长（毫秒）与循环次数。**累加**，不是覆盖——
        进度里存的是"这一段总共听了多久"（4.3 数据依赖：时长归曲目）。"""
        milliseconds = int(milliseconds)
        loops = int(loops)
        if milliseconds <= 0 and loops <= 0:
            return
        self.connection.execute(
            """
            INSERT INTO track_progress (track_id, listened_ms, loop_count, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(track_id) DO UPDATE SET
                listened_ms = listened_ms + excluded.listened_ms,
                loop_count = loop_count + excluded.loop_count,
                updated_at = excluded.updated_at
            """,
            (
                track_id,
                max(0, milliseconds),
                max(0, loops),
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        self.connection.commit()

    def set_track_status(self, track_id, status):
        """曲目状态点：`None` 未听 / `listening` 在听 / `done` 已磕完。"""
        if status is not None and status not in TRACK_STATES:
            raise ValueError(f"未知的曲目状态：{status}")
        self.save_track_progress(track_id, status=status)

    # ---- 分段标记（D4 方案 B）----

    def add_segment(self, track_id, title, start_ms, end_ms=None):
        row = self.connection.execute(
            "SELECT COALESCE(MAX(seq), 0) AS last FROM track_segments WHERE track_id = ?",
            (track_id,),
        ).fetchone()
        cursor = self.connection.execute(
            """
            INSERT INTO track_segments (track_id, seq, title, start_ms, end_ms, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                track_id,
                (row["last"] or 0) + 1,
                title,
                int(start_ms),
                int(end_ms) if end_ms is not None else None,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        self.connection.commit()
        return cursor.lastrowid

    def list_segments(self, track_id):
        return [
            dict(row)
            for row in self.connection.execute(
                "SELECT * FROM track_segments WHERE track_id = ? ORDER BY start_ms, id",
                (track_id,),
            )
        ]

    def delete_segment(self, segment_id):
        self.connection.execute("DELETE FROM track_segments WHERE id = ?", (segment_id,))
        self.connection.commit()

    def rename_segment(self, segment_id, title):
        self.connection.execute(
            "UPDATE track_segments SET title = ? WHERE id = ?", (title, segment_id)
        )
        self.connection.commit()

    def replace_track_file(self, track_id, path, title, size, content_hash=None):
        """把一段曲目指向另一个文件（"重新导入这一段"）。

        **不新建曲目**：文本、AB 点、跟读时长、循环次数、列表里的位置全部挂在这个
        `track_id` 上，"文件丢了我重新给一个"是最不该受罚的操作。
        """
        self.connection.execute(
            "UPDATE audio_tracks SET path = ?, title = ?, size = ?, content_hash = ? WHERE id = ?",
            (path, title, size, content_hash, track_id),
        )
        self.connection.commit()

    # ---- 当前播放上下文 ----

    def playback_state(self):
        row = self.connection.execute("SELECT * FROM playback_state WHERE id = 1").fetchone()
        return dict(row) if row else None

    def set_playback_state(self, playlist_id, track_id):
        """记下"上次在听哪一条"，供 P0「继续上次」用（4.3 流程 E）。

        只有**单行**：这是"当前"而不是"历史"，堆成多行就等于没有当前。
        """
        self.connection.execute(
            """
            INSERT INTO playback_state (id, playlist_id, track_id, updated_at)
            VALUES (1, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                playlist_id = excluded.playlist_id,
                track_id = excluded.track_id,
                updated_at = excluded.updated_at
            """,
            (playlist_id, track_id, datetime.now().isoformat(timespec="seconds")),
        )
        self.connection.commit()

    def audio_library_usage(self):
        """音频库占用（D15 要求导入时告知代价、P5 要显示）。"""
        row = self.connection.execute(
            "SELECT COUNT(*) AS tracks, COALESCE(SUM(size), 0) AS bytes FROM audio_tracks"
        ).fetchone()
        return {"tracks": row["tracks"], "bytes": row["bytes"]}

    # ==================================================================
    # 导出 / 导入（6.5）
    # ==================================================================

    def export_payload(self):
        """导出**第二层数据**为可 JSON 序列化的字典（6.5）。

        不含第一层（词条、文件索引、音频本体）——那些可重建，导出也更小更易读。
        不含任何密钥（当前版本尚无密钥，占位说明见 P5）。
        """
        data = {}
        for table in SECOND_LAYER_TABLES:
            rows = self.connection.execute(f"SELECT * FROM {table}").fetchall()
            data[table] = [dict(row) for row in rows]
        return {
            "app": "TOPIK Study Hub",
            "kind": "second-layer-export",
            "schema_version": SCHEMA_VERSION,
            "exported_at": datetime.now().isoformat(timespec="seconds"),
            "data": data,
        }

    def export_to_file(self, path):
        payload = self.export_payload()
        Path(path).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return path

    def import_payload(self, payload):
        """从导出字典恢复第二层数据。**原子操作**：先校验，再整体写入，失败全回滚。

        返回写入的表名 → 行数。任何一步出错都抛异常且不留半成品（6.5）。
        """
        if not isinstance(payload, dict):
            raise ValueError("备份格式不正确：应为 JSON 对象")
        if payload.get("kind") != "second-layer-export":
            raise ValueError("这不是本工具导出的备份文件")
        try:
            version = int(payload.get("schema_version"))
        except (TypeError, ValueError) as error:
            raise ValueError("备份缺少有效的 schema_version") from error
        if version > SCHEMA_VERSION:
            raise ValueError(
                f"备份来自更新的版本（schema {version} > {SCHEMA_VERSION}），请先升级工具"
            )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ValueError("备份内容缺少 data 段")

        restored = {}
        with self.connection:  # 成功即提交，异常自动回滚
            for table in SECOND_LAYER_TABLES:
                rows = data.get(table)
                if rows is None:
                    continue
                if not isinstance(rows, list):
                    raise ValueError(f"备份中 {table} 段不是列表")
                columns = self._column_names(table)
                self.connection.execute(f"DELETE FROM {table}")
                for row in rows:
                    if not isinstance(row, dict):
                        raise ValueError(f"备份中 {table} 的记录不是对象")
                    usable = {k: v for k, v in row.items() if k in columns}
                    if not usable:
                        raise ValueError(f"备份中 {table} 的记录字段无法识别")
                    names = ", ".join(usable)
                    placeholders = ", ".join("?" for _ in usable)
                    self.connection.execute(
                        f"INSERT INTO {table} ({names}) VALUES ({placeholders})",
                        tuple(usable.values()),
                    )
                restored[table] = len(rows)
        return restored

    def import_from_file(self, path):
        raw = Path(path).read_text(encoding="utf-8")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as error:
            raise ValueError(f"备份文件不是合法 JSON：{error}") from error
        return self.import_payload(payload)

    # ==================================================================
    # 存储占用（P5）
    # ==================================================================

    def storage_usage(self):
        return {
            "db": self.path.stat().st_size if self.path.exists() else 0,
            "backup": dir_size(BACKUP_DIR),
            "snippets": dir_size(SNIPPETS_DIR),
            "audio": dir_size(AUDIO_DIR),
            "recordings": dir_size(self.get_recording_dir()),
            "data_dir": str(DATA_DIR),
        }

    def close(self):
        self.connection.close()