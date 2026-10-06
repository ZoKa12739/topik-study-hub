"""P1 · 智能单词仓（`PRODUCT_SPEC` 4.2）。

三个模式，对应两遍式背词的三个动作：

| 模式 | 形态 | 干什么 |
|---|---|---|
| 浏览 | 表格 + 详情卡 | 查与整理：搜索、写笔记、手动改状态 |
| 过词 | 全屏单卡 | 第一遍：一屏一个词，一次一个判断（`1`/`2`/`3`） |
| 专攻 | 全屏单卡 | 第二遍：只过被标出来的词，释义展开 + 写笔记 |

**为什么过词不能是表格：** 4.2 把这条写成硬性约束——表格适合"查"和"整理"，
不适合"过"。一屏几十个词，眼睛会乱跑，也会被已经认识的词干扰。

**释义默认遮住（D14）：** 第一遍是自测。先看到释义再判断，判断就没有意义了，
标出来的词会明显偏少，第二遍专攻的对象就是错的。`Space` 临时揭示，供犹豫时偷看。

**键盘是过词成败的关键（4.2）：** 手不应离开键盘。`1`/`2`/`3` 判档、`Space` 揭示、
`Ctrl+Z` 回退。这一遍要能几分钟过完几百个词，所以判断后**无动画、无确认、无停顿**。

**断点：** 落在 `word_list_progress.cursor_seq`，中途退出随时能接上（流程 B 第 8 条）。

**笔记自动保存：** 输入停止 1.5 秒落库。原实现只在"切换到另一个词"时保存，
直接关工具就会丢掉正在编辑的内容——4.2 点名这是违反原则 P4 的缺陷。本版还在
`hideEvent` 与 `MainWindow.closeEvent` 里各加了一道冲刷。

**词表来源是 TSV 导入，工具内不做 PDF/OCR 转换：** 扫描件的识别在工具之外完成
（用户提供的 TSV 是唯一入口）。原先的「从 PDF 提取」依赖外部脚本 + Tesseract，
对双栏表格版面实测命中率为 0，已整体移除。
词表格式不符走行内 `Banner`，不弹 `QMessageBox`（见 `ui/components.py`）；
过词中断也不弹确认（4.2 的边界条款）。**唯一的模态框是「删除词表」的二次确认**——
问"要不要做"和报"发生了什么"是两件事，前者挡在动作前面，后者不该打断。

**词表删除只删第一层：** `words` / `word_list_progress` / `word_lists` 行删掉，
`word_notes` 一个字段都不碰（理由见 `core/database.py::delete_word_list`）。
所以删完再导入同一份 TSV，笔记与三态会跟着回来。
"""

import os
from pathlib import Path

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor, QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ui.components import Banner, EmptyState, make_copyable, show_toast
from ui.icons import icon
from ui.style import restyle
from ui.theme import (
    ACCENT_WARM,
    ROW_BASE_BG,
    ROW_DRILL_BG,
    STATE_COLORS,
    TEXT_COLORS,
)

# 三态的显示名与语义色。**颜色永远伴随文字**（DESIGN.md §3.1 纪律 3）——
# 这里文字本身就是状态名，颜色只是冗余强化。
STATE_LABELS = {"known": "认识", "fuzzy": "模糊", "unknown": "不认识", "": "未过"}

_STATE_COLORS = {
    "known": STATE_COLORS["success"],
    "fuzzy": STATE_COLORS["warning"],
    "unknown": STATE_COLORS["danger"],
    "": TEXT_COLORS["muted"],
}

# 待专攻 = 模糊 + 不认识（D14：不认识的排在专攻队列前面，但两者都算"要再看"）
_TO_DRILL = ("fuzzy", "unknown")

_JUDGE_KEYS = (("1", "known"), ("2", "fuzzy"), ("3", "unknown"))


def state_color(state):
    return _STATE_COLORS.get(state or "", _STATE_COLORS[""])


class VocabView(QWidget):
    """P1 智能单词仓。"""

    MODE_BROWSE, MODE_PASS, MODE_DRILL = 0, 1, 2
    NOTE_DEBOUNCE_MS = 1500

    def __init__(self, database):
        super().__init__()
        self.database = database

        self._lists = []
        self._mode = self.MODE_BROWSE
        self._offset = 0          # 表格里"编号"列的列号（"全部词表"范围下为 1）
        self._show_source = False

        self._browse_rows = []
        self._browse_source = []
        self._detail = None

        self._pass_rows = []
        self._pass_index = 0
        self._undo_stack = []
        self._revealed = False

        self._drill_rows = []
        self._drill_index = 0
        self._drill_cleared = 0
        self._drill_round_counted = False
        self._summary_action = None

        self._pending_editor = None
        self._pending_hint = None
        self._note_timer = QTimer(self)
        self._note_timer.setSingleShot(True)
        self._note_timer.setInterval(self.NOTE_DEBOUNCE_MS)
        self._note_timer.timeout.connect(self._flush_note)

        self.init_ui()
        self.refresh_lists()
        self.reload_browse()

    # ==================================================================
    # 布局
    # ==================================================================

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        eyebrow = QLabel("智能单词仓")
        eyebrow.setObjectName("pageEyebrow")
        layout.addWidget(eyebrow)
        title = QLabel("词表与过词")
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        subtitle = QLabel("先过一遍标出不会的词，再专攻它们。笔记、状态与断点都保存在本机。")
        subtitle.setObjectName("pageSubtitle")
        layout.addWidget(subtitle)

        layout.addLayout(self._build_action_bar())

        self.banner = Banner()
        self.banner.action_clicked.connect(self._on_banner_action)
        self._banner_action = None
        layout.addWidget(self.banner)

        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_browse_page())   # 0
        self.pages.addWidget(self._build_pass_page())     # 1
        self.pages.addWidget(self._build_drill_page())    # 2
        layout.addWidget(self.pages, 1)

        self._setup_shortcuts()

    def _build_action_bar(self):
        bar = QHBoxLayout()
        bar.setSpacing(8)

        self.combo_list = QComboBox()
        self.combo_list.setMinimumWidth(190)
        self.combo_list.setToolTip("决定了过词与专攻的作业范围")
        self.combo_list.currentIndexChanged.connect(self._on_list_changed)
        bar.addWidget(self.combo_list)

        # 模式切换：浏览 / 过词 / 专攻 三段控件（4.2 核心组件）。
        # 互斥由 `_sync_mode_buttons()` 负责，**不靠 Qt**——它们只是 checkable 的
        # `QPushButton`，可选按钮不会自动成组（`autoExclusive` 默认 false），
        # `setChecked(True)` 也从不撼动别人。这条注释留着是因为踩过一次：
        # 缺了同步，"点过的按钮"会一个个累积在选中态，三个全绿且退不回去。
        self._mode_buttons = []
        for index, label in enumerate(("浏览", "过词", "专攻")):
            button = QPushButton(label)
            button.setObjectName("modeSwitch")
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            # NoFocus：键盘由 1/2/3 承担，按钮不该抢焦点
            button.setFocusPolicy(Qt.NoFocus)
            button.clicked.connect(lambda _checked, i=index: self.set_mode(i))
            bar.addWidget(button)
            self._mode_buttons.append(button)
        self._sync_mode_buttons()

        bar.addStretch()

        self.btn_load_tsv = QPushButton("载入词表")
        self.btn_load_tsv.setObjectName("iconButton")
        self.btn_load_tsv.setIcon(icon("folder-open"))
        self.btn_load_tsv.clicked.connect(self.load_tsv_dialog)
        bar.addWidget(self.btn_load_tsv)

        # 删除词表：危险操作，用 dangerButton 与其他次级按钮区分开。
        # 「全部词表」不是一份具体词表，没有可删的行——那里它是禁用的。
        self.btn_delete_list = QPushButton("删除词表")
        self.btn_delete_list.setObjectName("dangerButton")
        self.btn_delete_list.setIcon(icon("trash", STATE_COLORS["danger"], 14))
        self.btn_delete_list.setToolTip("删除当前选中的词表；笔记、状态与重点标记会保留")
        self.btn_delete_list.clicked.connect(self.delete_current_list)
        bar.addWidget(self.btn_delete_list)

        # 搜索框：规格 4.2 —— 仅浏览模式（过词/专攻要的是专注，不是过滤）
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("韩文或中文…")
        self.search_input.setFixedWidth(180)
        self.search_input.textChanged.connect(self._on_search_changed)
        bar.addWidget(self.search_input)
        return bar

    # ---------------------------------------------------------------- 浏览

    def _build_browse_page(self):
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(10)

        # 词表元信息条：词表名 · N 个词 · 已过 X · 待专攻 M
        self.lbl_meta = QLabel("")
        self.lbl_meta.setObjectName("muted")
        box.addWidget(self.lbl_meta)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setObjectName("vocabSplitter")

        self.table = QTableWidget(0, 4)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        # 样式由全局主题提供（QTableWidget / QHeaderView），见 ui/theme.py
        self.table.itemSelectionChanged.connect(self.show_details)
        # 表格不是文本控件，没有内置复制——而这一页最常见的动作就是"查到一个词、
        # 把它拿走"。右键菜单与 Ctrl+C 都接在 `copy_current_word` 上。
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._table_context_menu)

        self.empty = EmptyState("", "")
        self.empty.action_clicked.connect(self._on_empty_action)
        self._empty_action = None

        self.left_stack = QStackedWidget()
        self.left_stack.addWidget(self.table)   # 0
        self.left_stack.addWidget(self.empty)   # 1
        splitter.addWidget(self.left_stack)

        splitter.addWidget(self._build_detail_card())
        splitter.setSizes([620, 320])

        box.addWidget(splitter, 1)
        self._configure_columns(False)
        return page

    def _build_detail_card(self):
        card = QFrame()
        card.setObjectName("surface")
        box = QVBoxLayout(card)
        box.setContentsMargins(16, 16, 16, 16)
        box.setSpacing(8)

        head = QHBoxLayout()
        heading = QLabel("单词卡片")
        heading.setObjectName("sectionTitle")
        head.addWidget(heading)
        head.addStretch()
        # 重点标记保留为次级控件：三态回答"会不会"，重点回答"想不想再看一眼"，
        # 是两个独立的轴（一个词可以既"认识"又被标为重点）。
        self.btn_star = QPushButton("标记重点")
        self.btn_star.setObjectName("starToggle")
        self.btn_star.setCursor(Qt.PointingHandCursor)
        self.btn_star.setFocusPolicy(Qt.NoFocus)
        self.btn_star.clicked.connect(self.toggle_star)
        head.addWidget(self.btn_star)
        box.addLayout(head)

        self.lbl_detail_seq = QLabel("")
        self.lbl_detail_seq.setObjectName("quizSeq")
        self.lbl_detail_seq.setAlignment(Qt.AlignCenter)
        box.addWidget(self.lbl_detail_seq)

        self.lbl_detail_kr = QLabel("请在左侧选择单词")
        self.lbl_detail_kr.setObjectName("wordDisplay")
        self.lbl_detail_kr.setAlignment(Qt.AlignCenter)
        self.lbl_detail_kr.setWordWrap(True)
        box.addWidget(make_copyable(self.lbl_detail_kr))

        self.lbl_detail_cn = QLabel("")
        self.lbl_detail_cn.setObjectName("wordMeaning")
        self.lbl_detail_cn.setAlignment(Qt.AlignCenter)
        self.lbl_detail_cn.setWordWrap(True)
        box.addWidget(make_copyable(self.lbl_detail_cn))

        # 三态切换（v1.2 修订：原"标记为重点"的二元开关不够用，见 D14）
        box.addSpacing(4)
        state_row = QHBoxLayout()
        state_row.setSpacing(6)
        self._state_buttons = {}
        for key in ("known", "fuzzy", "unknown"):
            button = QPushButton(STATE_LABELS[key])
            button.setObjectName("modeSwitch")
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(lambda _checked, s=key: self._on_detail_state(s))
            state_row.addWidget(button)
            self._state_buttons[key] = button
        box.addLayout(state_row)

        note_head = QHBoxLayout()
        note_label = QLabel("个人笔记")
        note_label.setObjectName("muted")
        note_head.addWidget(note_label)
        note_head.addStretch()
        self.lbl_browse_saved = QLabel("")
        self.lbl_browse_saved.setObjectName("saveHint")
        note_head.addWidget(self.lbl_browse_saved)
        box.addLayout(note_head)

        self.txt_notes = QTextEdit()
        self.txt_notes.setPlaceholderText("联想记忆、易混词、例句…输入停止 1.5 秒自动保存")
        self.txt_notes.textChanged.connect(
            lambda: self._on_note_edited(self.txt_notes, self.lbl_browse_saved)
        )
        box.addWidget(self.txt_notes, 1)
        return card

    # ---------------------------------------------------------------- 过词

    def _build_pass_page(self):
        page = QFrame()
        page.setObjectName("surface")
        box = QVBoxLayout(page)
        box.setContentsMargins(32, 22, 32, 22)
        box.setSpacing(10)

        head = QHBoxLayout()
        self.lbl_pass_progress = QLabel("")
        self.lbl_pass_progress.setObjectName("muted")
        head.addWidget(self.lbl_pass_progress)
        head.addStretch()
        hint = QLabel("1 认识 · 2 模糊 · 3 不认识 · Space 揭示释义 · Ctrl+Z 回退")
        hint.setObjectName("faint")
        head.addWidget(hint)
        box.addLayout(head)

        box.addStretch()

        self.lbl_pass_seq = QLabel("")
        self.lbl_pass_seq.setObjectName("quizSeq")
        self.lbl_pass_seq.setAlignment(Qt.AlignCenter)
        box.addWidget(self.lbl_pass_seq)

        self.lbl_pass_kr = QLabel("")
        self.lbl_pass_kr.setObjectName("quizWord")
        self.lbl_pass_kr.setAlignment(Qt.AlignCenter)
        self.lbl_pass_kr.setWordWrap(True)
        box.addWidget(make_copyable(self.lbl_pass_kr))

        self.lbl_pass_cn = QLabel("")
        self.lbl_pass_cn.setObjectName("quizMeaning")
        self.lbl_pass_cn.setAlignment(Qt.AlignCenter)
        self.lbl_pass_cn.setWordWrap(True)
        box.addWidget(make_copyable(self.lbl_pass_cn))

        box.addStretch()

        actions = QHBoxLayout()
        actions.setSpacing(12)
        actions.addStretch()
        self._judge_buttons = {}
        for key, label in (("known", "1　认识"), ("fuzzy", "2　模糊"), ("unknown", "3　不认识")):
            button = QPushButton(label)
            button.setObjectName("judgeButton")
            # NoFocus：按钮一旦拿到焦点，Space 就变成"点按钮"而不是"揭示释义"
            button.setFocusPolicy(Qt.NoFocus)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(lambda _checked, s=key: self._judge(s))
            actions.addWidget(button)
            self._judge_buttons[key] = button
        actions.addStretch()
        box.addLayout(actions)
        return page

    # ---------------------------------------------------------------- 专攻

    def _build_drill_page(self):
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)

        self.drill_stack = QStackedWidget()
        outer.addWidget(self.drill_stack)

        card = QFrame()
        card.setObjectName("surface")
        box = QVBoxLayout(card)
        box.setContentsMargins(32, 22, 32, 22)
        box.setSpacing(10)

        head = QHBoxLayout()
        self.lbl_drill_progress = QLabel("")
        self.lbl_drill_progress.setObjectName("muted")
        head.addWidget(self.lbl_drill_progress)
        head.addStretch()
        self.lbl_drill_saved = QLabel("")
        self.lbl_drill_saved.setObjectName("saveHint")
        head.addWidget(self.lbl_drill_saved)
        box.addLayout(head)

        box.addStretch()

        self.lbl_drill_seq = QLabel("")
        self.lbl_drill_seq.setObjectName("quizSeq")
        self.lbl_drill_seq.setAlignment(Qt.AlignCenter)
        box.addWidget(self.lbl_drill_seq)

        self.lbl_drill_kr = QLabel("")
        self.lbl_drill_kr.setObjectName("quizWord")
        self.lbl_drill_kr.setAlignment(Qt.AlignCenter)
        self.lbl_drill_kr.setWordWrap(True)
        box.addWidget(make_copyable(self.lbl_drill_kr))

        # 专攻是学习不是测试，释义默认展开（流程 C 第 3 条）——与过词相反
        self.lbl_drill_cn = QLabel("")
        self.lbl_drill_cn.setObjectName("quizMeaning")
        self.lbl_drill_cn.setAlignment(Qt.AlignCenter)
        self.lbl_drill_cn.setWordWrap(True)
        box.addWidget(make_copyable(self.lbl_drill_cn))

        self.txt_drill_notes = QTextEdit()
        self.txt_drill_notes.setPlaceholderText("写下联想记忆——这一遍的目的就是让下次不用再攻它")
        self.txt_drill_notes.setMaximumHeight(120)
        self.txt_drill_notes.textChanged.connect(
            lambda: self._on_note_edited(self.txt_drill_notes, self.lbl_drill_saved)
        )
        box.addWidget(self.txt_drill_notes)

        box.addStretch()

        actions = QHBoxLayout()
        actions.setSpacing(12)
        actions.addStretch()
        for label, known in (("1　认识了", True), ("2　还是不会", False)):
            button = QPushButton(label)
            button.setObjectName("judgeButton")
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(lambda _checked, k=known: self._drill_judge(k))
            actions.addWidget(button)
        actions.addStretch()
        box.addLayout(actions)

        self.drill_stack.addWidget(card)   # 0

        self.empty_drill = EmptyState("", "")
        self.empty_drill.action_clicked.connect(self._on_summary_action)
        self.drill_stack.addWidget(self.empty_drill)   # 1
        return page

    # ==================================================================
    # 键盘
    # ==================================================================

    def _setup_shortcuts(self):
        # WidgetWithChildrenShortcut：只有本页（或其子控件）持有焦点时才生效，
        # 所以切到别的页面时这些键不会误触发。
        self._digit_shortcuts = []
        for key, state in _JUDGE_KEYS:
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            shortcut.activated.connect(lambda s=state: self._judge(s))
            self._digit_shortcuts.append(shortcut)

        self._space_shortcut = QShortcut(QKeySequence(Qt.Key_Space), self)
        self._space_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self._space_shortcut.activated.connect(self._toggle_reveal)

        self._undo_shortcut = QShortcut(QKeySequence.Undo, self)
        self._undo_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self._undo_shortcut.activated.connect(self._undo_pass)

        # 2.6 的全局键里，`Ctrl+F` / `Ctrl+S` 落在有搜索框与笔记编辑区的模块上，
        # 本页就是第一个（P5 的快捷键表已注明其余随各自模块在第 3~5 期落地）。
        self._find_shortcut = QShortcut(QKeySequence.Find, self)
        self._find_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self._find_shortcut.activated.connect(self._focus_search)

        self._save_shortcut = QShortcut(QKeySequence.Save, self)
        self._save_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self._save_shortcut.activated.connect(self._save_now)

        # `Ctrl+C` 挂在**表格**上而不是本页：作用域跟着焦点走，搜索框与笔记编辑区
        # 里的 Ctrl+C 仍然是它们自己的复制，不会被这里抢走。
        self._copy_shortcut = QShortcut(QKeySequence.Copy, self.table)
        self._copy_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self._copy_shortcut.activated.connect(self.copy_current_word)

        self._sync_shortcuts()

    def _sync_shortcuts(self):
        guessing = self._mode != self.MODE_BROWSE
        for shortcut in self._digit_shortcuts:
            shortcut.setEnabled(guessing)
        # Space 与 Ctrl+Z 只属于过词：专攻卡里有笔记编辑区，
        # 那里 Ctrl+Z 必须是文本撤销、Space 必须是打空格。
        self._space_shortcut.setEnabled(self._mode == self.MODE_PASS)
        self._undo_shortcut.setEnabled(self._mode == self.MODE_PASS)

    def _keyboard_ok(self):
        """键盘只在过词/专攻生效，且**不得抢走文本输入**。

        少了这道判断，用户在搜索框里输入 "1" 或者写笔记时按 "1"，
        就会把词判出去——4.2 要求键盘优先，但优先的前提是别抢输入。
        """
        if self._mode == self.MODE_BROWSE:
            return False
        focus = QApplication.focusWidget()
        return not isinstance(focus, (QLineEdit, QTextEdit))

    def _focus_search(self):
        """`Ctrl+F`：回到浏览模式并聚焦搜索框。"""
        if self._mode != self.MODE_BROWSE:
            self.set_mode(self.MODE_BROWSE)
        self.search_input.setFocus()
        self.search_input.selectAll()

    def _save_now(self):
        """`Ctrl+S`：立刻落库，不等防抖。"""
        self._flush_note()
        show_toast(self.window(), "笔记已保存")

    # ==================================================================
    # 词表选择
    # ==================================================================

    def current_list_id(self):
        """当前作业词表。`None` = 「全部词表」，只支持浏览与检索（流程 F）。"""
        data = self.combo_list.currentData()
        return data if isinstance(data, int) else None

    def current_list_name(self):
        list_id = self.current_list_id()
        return next((row["name"] for row in self._lists if row["id"] == list_id), "")

    def refresh_lists(self):
        """重建词表选择器。导入新词表或外部改过数据后调用。

        尽量保住当前选中项——用户导入第二份词表时不该被弹回「全部词表」。
        """
        previous = self.combo_list.currentData()
        self._lists = self.database.list_word_lists()

        self.combo_list.blockSignals(True)
        self.combo_list.clear()
        self.combo_list.addItem("全部词表", None)
        for row in self._lists:
            counts = self.database.list_counts(row["id"])
            self.combo_list.addItem(f"{row['name']} · {counts['total']} 个词", row["id"])
        index = self.combo_list.findData(previous)
        self.combo_list.setCurrentIndex(index if index >= 0 else (1 if self._lists else 0))
        self.combo_list.blockSignals(False)
        self._sync_delete_button()

    def select_list(self, list_id):
        """按 id 选中一份词表，返回是否选中。P0「继续上次」跳过来时用（2.5）。

        先 `refresh_lists()` 再找：P0 手上的 id 是记账那一刻的快照，而这一页的选择器
        可能已经旧了（刚导入、刚删过）。找不到就**什么都不做**——那份词表已经被删掉了，
        此时"跳到「全部词表」"比"留在原地"更让人意外。
        """
        self.refresh_lists()
        index = self.combo_list.findData(list_id)
        if index < 0:
            return False
        # 切到同一项不会发信号，那也没关系：本来就已经在那份词表上了
        self.combo_list.setCurrentIndex(index)
        return True

    def _on_list_changed(self, _index):
        self._flush_note()
        self.banner.clear()
        self._sync_delete_button()
        list_id = self.current_list_id()
        if list_id is None and self._mode != self.MODE_BROWSE:
            # 「全部词表」没有统一的断点与序号，过词/专攻必须落到一份具体词表
            self.set_mode(self.MODE_BROWSE)
            return
        if self._mode == self.MODE_BROWSE:
            self.reload_browse()
        elif self._mode == self.MODE_PASS:
            self._pass_begin()
        else:
            self._drill_begin()

    def set_mode(self, mode):
        """切换浏览 / 过词 / 专攻。"""
        self._flush_note()
        if mode in (self.MODE_PASS, self.MODE_DRILL) and self.current_list_id() is None:
            self.banner.show_message(
                "warning",
                "过词与专攻按词表进行，请先在左侧选择一份词表。"
                "「全部词表」只用于浏览与跨词表检索。",
            )
            # 这次点击被拒绝了，但按钮自己已经先翻成选中——必须把三个一起写回，
            # 否则被拒的那一个会留在选中态，看起来像"切过去了却没反应"。
            self._sync_mode_buttons()
            return

        self._mode = mode
        self._sync_mode_buttons()
        self.pages.setCurrentIndex(mode)
        self.search_input.setVisible(mode == self.MODE_BROWSE)
        self.banner.clear()
        self._sync_shortcuts()

        if mode == self.MODE_BROWSE:
            self.reload_browse()
        elif mode == self.MODE_PASS:
            self._pass_begin()
        else:
            self._drill_begin()

    def _sync_mode_buttons(self):
        """让三个模式按钮**恒有且仅有一个**选中。

        它们是 `QPushButton` 不是 `QRadioButton`，Qt 不会替我们互斥；而且
        `QPushButton.clicked` 是**先**把按钮的 checked 取反、再发信号，所以每个
        点击的默认结果都是"多一个选中"。这里每次把三个一起重写一遍，是唯一
        不依赖 Qt 隐式行为、也最容易验证的写法。
        """
        for index, button in enumerate(self._mode_buttons):
            button.setChecked(index == self._mode)

    def _sync_delete_button(self):
        """「全部词表」不是一个可删的实体，那里禁用删除。"""
        self.btn_delete_list.setEnabled(self.current_list_id() is not None)

    def refresh(self):
        """数据被外部改动（导入备份、换资料目录）后重读一遍。"""
        self._flush_note()
        self.refresh_lists()
        self.set_mode(self._mode)

    # ==================================================================
    # 导入
    # ==================================================================

    def _material_root(self):
        return self.database.get_material_root()

    def _tsv_start_dir(self):
        """TSV 选择框的起始目录。

        优先 `<资料根>/单词/tsv`——词表的实际存放处（见 `samples/README.md`）。
        没有这个子目录时退回 `<资料根>/单词`，再退回资料根本身。
        """
        root = self._material_root()
        if not root:
            return ""
        for relative in (os.path.join("单词", "tsv"), "单词"):
            candidate = os.path.join(root, relative)
            if os.path.isdir(candidate):
                return candidate
        return root

    def load_tsv_dialog(self):
        file_name, _ = QFileDialog.getOpenFileName(
            self, "选择 TSV 词表文件", self._tsv_start_dir(), "TSV 词表 (*.tsv);;所有文件 (*)"
        )
        if file_name:
            self.load_tsv(file_name)

    def load_tsv(self, file_path):
        """读一份 TSV 并入库。**重新导入同一份文件不会丢笔记与进度**（流程 A 第 6 条）。"""
        try:
            with open(file_path, "r", encoding="utf-8-sig") as handle:
                lines = handle.readlines()
        except (OSError, UnicodeDecodeError) as error:
            # 只把"用户可预期的失败"转成提示。编程错误不能被伪装成读取失败——
            # 2026-10-06 就是这里把一个 sqlite 绑定错误吞成了永久阻塞的模态框。
            self.banner.show_message("danger", f"读取词表失败：{error}")
            return

        rows = []
        skipped = 0
        for index, line in enumerate(lines):
            if not line.strip():
                continue
            parts = line.rstrip("\r\n").split("\t")
            if len(parts) < 3 or not parts[1].strip():
                skipped += 1
                continue
            try:
                seq = int(parts[0].strip())
            except ValueError:
                seq = index + 1
            rows.append((seq, parts[1], parts[2]))

        if not rows:
            self.banner.show_message(
                "danger",
                f"这个文件里没有可用词条。词表需要三列、制表符分隔——编号 / 韩语 / 中文；"
                f"该文件共 {len(lines)} 行，没有一行符合。",
            )
            return

        result = self.database.import_word_list(Path(file_path).stem, file_path, rows)

        self.set_mode(self.MODE_BROWSE)
        self.refresh_lists()
        index = self.combo_list.findData(result["list_id"])
        if index >= 0:
            self.combo_list.setCurrentIndex(index)
        self.reload_browse()

        message = f"已载入 {result['count']} 个词条"
        if skipped:
            message += f"，跳过 {skipped} 行（列数不足或没有韩文）"
        if result["reused"]:
            message += "。这是重新导入，历史笔记、状态与过词断点均已保留。"
        self.banner.show_message("success", message)

    def delete_current_list(self):
        """删除当前选中的词表。**只删第一层**——笔记与三态原样保留。

        破坏性操作，所以弹一次模态确认。`Banner` / `show_toast` 只适合说"发生了什么"，
        不适合问"要不要做"；而模态框会造成"没有事件循环就永久阻塞"的那个老问题在这里
        不成立——确认框是从用户点击里弹出来的，事件循环正在运行。P5 的备份导入确认
        已经开了这个先例（`settings_view.py` 的 `QMessageBox.question`）。
        """
        list_id = self.current_list_id()
        if list_id is None:
            self.banner.show_message("info", "「全部词表」不是一个可删除的词表。")
            return
        name = self.current_list_name()
        counts = self.database.list_counts(list_id)
        confirmed = QMessageBox.question(
            self,
            "删除词表",
            f"将删除词表「{name}」及其 {counts['total']} 个词条。\n\n"
            "笔记、三态与重点标记不会删除——它们按「韩语」关联，同一个词若还在别的"
            "词表里，那边完全不受影响；词条本身可以随时从 TSV 再导入一次。\n\n"
            "此操作不可撤销，确定继续吗？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirmed != QMessageBox.Yes:
            return

        # 先冲刷防抖窗口里的笔记：那份笔记属于即将被删的词表。若放到删除之后冲刷，
        # `_flush_note` 会给一个刚消失的词补建一条再也看不到的孤儿笔记行。
        self._flush_note()

        removed = self.database.delete_word_list(list_id)
        if removed is None:
            # 选中项本来就来自这个下拉，正常不该发生；外部改过库才可能
            self.banner.show_message("warning", "这份词表已经不存在了，列表已刷新。")
        else:
            show_toast(
                self.window(),
                f"已删除词表「{removed['name']}」，移除 {removed['count']} 个词条",
            )

        # 词表没了，它的断点与专攻队列也就无从谈起。`refresh_lists` 屏蔽了信号，
        # 所以 `set_mode` 必须显式再调一次——否则页面会停在已删词表的过词卡上。
        self.refresh_lists()
        self.set_mode(self.MODE_BROWSE)

    # ==================================================================
    # 浏览模式
    # ==================================================================

    def _configure_columns(self, show_source):
        """表格列随范围变化：「全部词表」多一列来源（流程 F 的 `来自：…`）。"""
        self._show_source = show_source
        self._offset = 1 if show_source else 0
        header = self.table.horizontalHeader()
        if show_source:
            self.table.setColumnCount(5)
            self.table.setHorizontalHeaderLabels(["词表", "编号", "韩语", "中文释义", "状态"])
            for column in (0, 1):
                header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
            header.setSectionResizeMode(2, QHeaderView.Stretch)
            header.setSectionResizeMode(3, QHeaderView.Stretch)
            header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        else:
            self.table.setColumnCount(4)
            self.table.setHorizontalHeaderLabels(["编号", "韩语", "中文释义", "状态"])
            header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
            header.setSectionResizeMode(1, QHeaderView.Stretch)
            header.setSectionResizeMode(2, QHeaderView.Stretch)
            header.setSectionResizeMode(3, QHeaderView.ResizeToContents)

    def reload_browse(self):
        """重建浏览表格与元信息条。"""
        list_id = self.current_list_id()
        if list_id is None:
            rows = self.database.all_words()
            self._browse_source = [row["list_name"] for row in rows]
        else:
            rows = self.database.list_words(list_id)
            self._browse_source = ["" for _ in rows]
        self._browse_rows = rows

        self._configure_columns(list_id is None)
        self._detail = None
        self.table.setRowCount(0)
        for row, source in zip(rows, self._browse_source):
            self._append_row(row, source)
        self._update_meta(list_id)

        if not rows:
            self._show_browse_empty(list_id)
        else:
            self._apply_filter(self.search_input.text())

    def _append_row(self, row, source):
        index = self.table.rowCount()
        self.table.insertRow(index)
        offset = self._offset

        if self._show_source:
            self.table.setItem(index, 0, QTableWidgetItem(source))

        item = QTableWidgetItem(str(row["seq"]))
        item.setTextAlignment(Qt.AlignCenter)
        self.table.setItem(index, offset, item)

        item = QTableWidgetItem(row["korean"])
        font = QFont()
        font.setBold(True)
        item.setFont(font)
        self.table.setItem(index, offset + 1, item)

        self.table.setItem(index, offset + 2, QTableWidgetItem(row["meaning"] or ""))

        self._write_state_cell(index, row["state"] or "")

    def _write_state_cell(self, index, state):
        """写状态列并把待专攻行上淡暖底。

        `QTableWidgetItem` 的底色不走 QSS（见 `ui/theme.py` 的常量说明），
        所以这里是代码设色——色值仍只来自 `theme.py` 一个来源。
        """
        item = QTableWidgetItem(STATE_LABELS.get(state, "未过"))
        item.setTextAlignment(Qt.AlignCenter)
        item.setForeground(QColor(state_color(state)))
        self.table.setItem(index, self._offset + 3, item)

        background = QColor(ROW_DRILL_BG if state in _TO_DRILL else ROW_BASE_BG)
        for column in range(self.table.columnCount()):
            cell = self.table.item(index, column)
            if cell is not None:
                cell.setBackground(background)

    # ---------------------------------------------------------------- 复制

    def _selected_word_entry(self):
        """当前选中的词条行；表格没有选中行时返回 `None`。

        行号到 `_browse_rows` 的下标是**一一对应**的：搜索只做隐藏、不重排，也没开排序，
        这与 `show_details` 用的是同一种映射。
        """
        index = self.table.currentRow()
        if index < 0 or index >= len(self._browse_rows):
            return None
        return self._browse_rows[index]

    def copy_current_word(self, field="korean"):
        """把当前词条复制到剪贴板。

        默认**只复制韩语**：韩语才是拿去查的东西，把中文释义一起塞进剪贴板，
        粘到词典里还要再删一次。另两种组合在右键菜单里（`Ctrl+C` 走这条默认路径）。
        """
        entry = self._selected_word_entry()
        if entry is None:
            return
        meaning = entry["meaning"] or ""
        text = {
            "korean": entry["korean"],
            "meaning": meaning,
            "both": f"{entry['korean']}\t{meaning}",
        }.get(field, entry["korean"])
        if not text.strip():
            return
        QApplication.clipboard().setText(text)
        show_toast(self.window(), f"已复制「{entry['korean']}」")

    def _table_context_menu(self, pos):
        """表格右键：见得到，才想得到要复制什么。"""
        # 右键先选中指针底下的那一行——否则菜单会作用在"上次选中的行"上，
        # 而用户看着的是"右键点的那一行"。顺带把详情卡也同步过去。
        index = self.table.indexAt(pos)
        if index.isValid():
            self.table.setCurrentCell(index.row(), index.column())
        menu = QMenu(self)
        entry = self._selected_word_entry()
        actions = []
        for text, field in (
            ("复制韩语", "korean"),
            ("复制释义", "meaning"),
            ("复制韩语与释义", "both"),
        ):
            action = menu.addAction(text)
            action.setEnabled(entry is not None)
            actions.append((action, field))
        chosen = menu.exec(self.table.viewport().mapToGlobal(pos))
        for action, field in actions:
            if chosen is action:
                self.copy_current_word(field)
                return

    def _update_meta(self, list_id):
        if list_id is None:
            self.lbl_meta.setText(
                f"全部词表 · 共 {len(self._browse_rows)} 个词条"
                "（同一个韩文词在多份词表里共享笔记与状态）"
            )
            return
        counts = self.database.list_counts(list_id)
        self.lbl_meta.setText(
            f"{self.current_list_name()} · {counts['total']} 个词 · "
            f"已过 {counts['passed']} · 待专攻 {counts['to_drill']}"
        )

    def _show_browse_empty(self, list_id):
        if list_id is None:
            self._show_empty(
                "还没有词表",
                "导入一份 TSV 词表（三列、制表符分隔：编号 / 韩语 / 中文）。"
                "导入后笔记、状态与过词进度都会保存在本机。",
                action_text="载入词表",
                action_icon="folder-open",
                glyph="folder-open",
                handler=self.load_tsv_dialog,
            )
        else:
            self._show_empty(
                "这份词表里没有词条",
                "它可能是空的，或导入时每一行都被跳过了。",
                action_text="换一份词表",
                action_icon="folder-open",
                glyph="folder-open",
                handler=self.load_tsv_dialog,
            )

    def _on_search_changed(self, text):
        self._apply_filter(text)

    def _apply_filter(self, text):
        needle = (text or "").strip().lower()
        offset = self._offset
        visible = 0
        for index in range(self.table.rowCount()):
            korean = self.table.item(index, offset + 1).text()
            meaning = self.table.item(index, offset + 2).text()
            match = not needle or needle in korean.lower() or needle in meaning.lower()
            self.table.setRowHidden(index, not match)
            if match:
                visible += 1

        if not self.table.rowCount():
            return
        if needle and not visible:
            self._show_empty(
                f'没有找到包含"{text.strip()}"的词条',
                "可以只输入词的一部分，或换中文释义搜索。",
                action_text="清除搜索",
                action_icon="x",
                glyph="search",
                handler=self.search_input.clear,
            )
        else:
            self._show_table()

    def show_details(self):
        selected = self.table.selectedItems()
        if not selected:
            return
        index = selected[0].row()
        if index >= len(self._browse_rows):
            return
        self._flush_note()

        row = self._browse_rows[index]
        self._detail = row
        self.lbl_detail_seq.setText(f"第 {row['seq']} 个")
        self.lbl_detail_kr.setText(row["korean"])
        self.lbl_detail_cn.setText(row["meaning"] or "")

        note = self.database.get_word_note(row["korean"])
        # 屏蔽信号：载入笔记不该触发"内容变化 → 自动保存"
        self.txt_notes.blockSignals(True)
        self.txt_notes.setPlainText(note["note"] if note else "")
        self.txt_notes.blockSignals(False)
        self.txt_notes.setProperty("wordKey", row["korean"])
        self.lbl_browse_saved.setText("")

        self._sync_state_buttons(note["state"] if note else None)
        self._sync_star_button(bool(note["is_starred"]) if note else False)

    def _sync_state_buttons(self, state):
        for key, button in self._state_buttons.items():
            button.setChecked(key == state)

    def _on_detail_state(self, state):
        """详情卡里手动改状态。再点一次当前态 = 撤销，回到"未过"。

        **不推进断点**：手动整理不是"过词"，不该影响下次从哪儿接着过。
        """
        if self._detail is None:
            return
        korean = self._detail["korean"]
        note = self.database.get_word_note(korean)
        current = note["state"] if note else None
        new_state = None if current == state else state

        self.database.set_word_state(korean, new_state)
        self._sync_state_buttons(new_state)
        self._repaint_detail_row()
        self._update_meta(self.current_list_id())

    def _repaint_detail_row(self):
        selected = self.table.selectedItems()
        if not selected:
            return
        index = selected[0].row()
        if index >= len(self._browse_rows):
            return
        row = self._browse_rows[index]
        note = self.database.get_word_note(row["korean"])
        state = (note["state"] if note else None) or ""
        row["state"] = state
        row["is_starred"] = int(note["is_starred"]) if note else 0
        self._write_state_cell(index, state)

    def toggle_star(self):
        """重点标记。与三态是两个独立的轴（6.4 第 6 条末句：星标不参与专攻队列）。"""
        if self._detail is None:
            return
        korean = self._detail["korean"]
        note = self.database.get_word_note(korean)
        is_starred = not bool(note["is_starred"]) if note else True
        self.database.save_word_note(korean, self.txt_notes.toPlainText(), is_starred)
        # 刚写的这份就是最新的，别让防抖再写一遍
        self._pending_editor = self._pending_hint = None
        self._sync_star_button(is_starred)
        self._repaint_detail_row()

    def _sync_star_button(self, is_starred):
        self.btn_star.setText("已标重点" if is_starred else "标记重点")
        self.btn_star.setIcon(
            icon("star-filled", ACCENT_WARM, 14)
            if is_starred
            else icon("star", TEXT_COLORS["muted"], 14)
        )

    # ==================================================================
    # 过词模式
    # ==================================================================

    def _pass_begin(self):
        list_id = self.current_list_id()
        if list_id is None:
            return
        self._pass_rows = self.database.list_words(list_id)
        self._undo_stack = []
        if not self._pass_rows:
            self.lbl_pass_progress.setText("")
            self.lbl_pass_seq.setText("")
            self.lbl_pass_kr.setText("这份词表里没有词条")
            self.lbl_pass_cn.setText("先在「浏览」里载入一份 TSV。")
            restyle(self.lbl_pass_cn, "faint")
            for button in self._judge_buttons.values():
                button.setEnabled(False)
            return

        cursor = self.database.get_list_cursor(list_id)
        start = 0
        for index, row in enumerate(self._pass_rows):
            if row["seq"] <= cursor:
                start = index + 1
            else:
                break
        if start >= len(self._pass_rows):
            # 断点已经到表尾：说明上一遍过完了。从头再来一遍，并说清楚发生了什么，
            # 否则用户看到"已过 1400 / 1400"还以为工具坏了。
            start = 0
            self.banner.show_message("info", "这份词表上次已经过完，现在从头再过一遍。")
        self._pass_index = start
        for button in self._judge_buttons.values():
            button.setEnabled(True)
        self._show_pass_current()

    def _show_pass_current(self):
        finished = self._pass_index >= len(self._pass_rows)
        self.lbl_pass_seq.setText("")
        self.lbl_pass_kr.setText("这一遍过完了" if finished else "")
        for button in self._judge_buttons.values():
            button.setEnabled(not finished)

        if finished:
            restyle(self.lbl_pass_cn, "quizMeaning")
            self.lbl_pass_cn.setText(
                "切到「专攻」去攻被标出来的词，或者直接再过一遍。"
            )
        else:
            row = self._pass_rows[self._pass_index]
            self.lbl_pass_seq.setText(f"第 {row['seq']} 个")
            self.lbl_pass_kr.setText(row["korean"])
            self._revealed = False
            self._render_pass_meaning()

        self._update_pass_progress()

    def _render_pass_meaning(self):
        """释义默认遮住（D14 推荐）：这一遍是自测，先看答案再看题就没有意义了。"""
        if self._pass_index >= len(self._pass_rows):
            return
        row = self._pass_rows[self._pass_index]
        if self._revealed:
            restyle(self.lbl_pass_cn, "quizMeaning")
            self.lbl_pass_cn.setText(row["meaning"] or "（这份词表没有释义）")
        else:
            restyle(self.lbl_pass_cn, "faint")
            self.lbl_pass_cn.setText("释义已遮住 · 按 Space 揭示")

    def _toggle_reveal(self):
        if not self._keyboard_ok() or self._pass_index >= len(self._pass_rows):
            return
        self._revealed = not self._revealed
        self._render_pass_meaning()

    def _judge(self, state):
        """`1`/`2`/`3` 与三个按钮的共同入口。"""
        if not self._keyboard_ok():
            return
        if self._mode == self.MODE_PASS:
            self._pass_judge(state)
        elif self._mode == self.MODE_DRILL:
            # 专攻只有两个动作：`1` = 认识了，`2`/`3` 都归"还是不会"
            self._drill_judge(state == "known")

    def _pass_judge(self, state):
        if self._pass_index >= len(self._pass_rows):
            return
        list_id = self.current_list_id()
        row = self._pass_rows[self._pass_index]
        korean = row["korean"]

        note = self.database.get_word_note(korean)
        previous_state = note["state"] if note else None
        # 状态与断点在同一个事务里落库；返回推进前的断点，供 Ctrl+Z 还原
        previous_cursor = self.database.pass_word(list_id, row["seq"], korean, state)
        self._undo_stack.append((korean, previous_state, previous_cursor))

        # 活动日志（5.2）：只在**第一次**过这个词时记一个。5.4 的口径是「当日**新增**
        # 过词的词数」，而"新增"在这里有精确的判据——`previous_state is None` 就是
        # 「这个词此前没有任何状态」。它同时保证了「今日过词」与同一页上的
        # 「已过 320 / 1400」变化量一致：后者的分子正好是"状态非空"的词数
        # （`list_counts`）。来回重过同一个词不该把这个数刷上去。
        if previous_state is None:
            self.database.note_vocab_progress(
                "vocab_triaged", list_id=list_id, list_name=self.current_list_name()
            )

        row["state"] = state
        self._pass_index += 1
        self._show_pass_current()

    def _undo_pass(self):
        """`Ctrl+Z` 退回上一个词（流程 B 第 7 条）：状态与断点一起还原。

        回退本身也写一条 `word_state_log` 流水——那张表是只追加的历史，
        改历史比追加一条修正记录更糟。
        """
        if not self._keyboard_ok() or not self._undo_stack:
            return
        korean, previous_state, previous_cursor = self._undo_stack.pop()
        list_id = self.current_list_id()
        self.database.pass_word(list_id, previous_cursor, korean, previous_state)
        # 退掉的活动日志计数与退掉的状态是同一件事：这一条只在当初记过时才退，
        # 否则「已过」减了 1 而「今日过词」不动，同一页上两个数当场打架（5.4）。
        if previous_state is None:
            self.database.note_vocab_progress(
                "vocab_triaged", list_id=list_id, list_name=self.current_list_name(), delta=-1
            )
        self._pass_index = max(0, self._pass_index - 1)
        if self._pass_index < len(self._pass_rows):
            self._pass_rows[self._pass_index]["state"] = previous_state or ""
        self._show_pass_current()

    def _update_pass_progress(self):
        """`已过 320 / 1400 · 已标记 87`。

        **口径与元信息条一致**（都取数据库实时值），否则两处的数字会打架——
        `PRODUCT_SPEC` 5.4 明确要求指标口径统一，不然统计没有意义。
        """
        list_id = self.current_list_id()
        if list_id is None:
            self.lbl_pass_progress.setText("")
            return
        counts = self.database.list_counts(list_id)
        self.lbl_pass_progress.setText(
            f"已过 {counts['passed']} / {counts['total']} · 已标记 {counts['to_drill']}"
        )

    # ==================================================================
    # 专攻模式
    # ==================================================================

    def _drill_begin(self):
        list_id = self.current_list_id()
        if list_id is None:
            return
        self._drill_rows = [dict(row) for row in self.database.drill_queue(list_id)]
        self._drill_index = 0
        self._drill_cleared = 0
        self._drill_round_counted = False
        if not self._drill_rows:
            self._show_drill_summary(first_time=True)
            return
        self.drill_stack.setCurrentIndex(0)
        self._show_drill_current()

    def _show_drill_current(self):
        if self._drill_index >= len(self._drill_rows):
            self._finish_drill_round()
            return
        row = self._drill_rows[self._drill_index]
        self.lbl_drill_seq.setText(
            f"第 {row['seq']} 个" + (" · 不认识" if row["state"] == "unknown" else " · 模糊")
        )
        self.lbl_drill_kr.setText(row["korean"])
        self.lbl_drill_cn.setText(row["meaning"] or "（这份词表没有释义）")

        note = self.database.get_word_note(row["korean"])
        self.txt_drill_notes.blockSignals(True)
        self.txt_drill_notes.setPlainText(note["note"] if note else "")
        self.txt_drill_notes.blockSignals(False)
        self.txt_drill_notes.setProperty("wordKey", row["korean"])
        self.lbl_drill_saved.setText("")
        self._update_drill_progress()

    def _drill_judge(self, known):
        """专攻的两个动作。`认识了` 出列；`还是不会` 原样留在待专攻里。

        **不推进过词断点**：第二遍是"攻"，不是重新"过"。在这里改断点会把用户
        第一遍的进度搅乱（一个词可能出现在断点之前的位置）。
        """
        if self._drill_index >= len(self._drill_rows):
            return
        self._flush_note()
        row = self._drill_rows[self._drill_index]
        if known:
            self.database.set_word_state(row["korean"], "known")
            # 5.4：今日专攻 = 当日**转为 known** 的词数。专攻的队列只收
            # `fuzzy` / `unknown`，所以"按一下就是一个真实的转移"，不必再判断前置状态；
            # 专攻也没有撤销键，因此这里只有加、没有减。
            self.database.note_vocab_progress(
                "vocab_drilled",
                list_id=self.current_list_id(),
                list_name=self.current_list_name(),
            )
            row["cleared"] = True
            self._drill_cleared += 1
        # 「还是不会」不动状态：词自然留在待专攻队列里，下一轮再出现（流程 C 第 5、6 条）
        self._drill_index += 1
        self._show_drill_current()

    def _finish_drill_round(self):
        list_id = self.current_list_id()
        if not self._drill_round_counted:
            self.database.mark_drill_round(list_id)
            self._drill_round_counted = True
        self._show_drill_summary(first_time=False)

    def _show_drill_summary(self, first_time):
        counts = self.database.list_counts(self.current_list_id())
        remaining = counts["to_drill"]
        if first_time:
            self.empty_drill.set_content(
                "还没有待专攻的词",
                "专攻只过被标出来的词。先做一遍「过词」，把不会的和模糊的标出来。",
                action_text="开始过词",
                action_icon="check",
                glyph="book",
            )
            self._summary_action = lambda: self.set_mode(self.MODE_PASS)
        elif remaining:
            self.empty_drill.set_content(
                "本轮完成",
                f"本轮清掉了 {self._drill_cleared} 个词，还有 {remaining} 个需要再看一遍。",
                action_text="再开一轮",
                action_icon="refresh",
                glyph="check-circle",
            )
            self._summary_action = self._drill_begin
        else:
            self.empty_drill.set_content(
                "这份词表已全部掌握",
                "待专攻已经清空。可以换下一份词表，或者挑几个词写点联想笔记。",
                glyph="check-circle",
            )
            self._summary_action = None
        self.drill_stack.setCurrentIndex(1)

    def _on_summary_action(self):
        if self._summary_action:
            self._summary_action()

    def _update_drill_progress(self):
        counts = self.database.list_counts(self.current_list_id())
        remaining = max(0, len(self._drill_rows) - self._drill_index)
        self.lbl_drill_progress.setText(
            f"本轮剩 {remaining} · 累计待专攻 {counts['to_drill']}"
        )

    # ==================================================================
    # 笔记自动保存
    # ==================================================================

    def _on_note_edited(self, editor, hint):
        self._pending_editor = editor
        self._pending_hint = hint
        hint.setText("保存中…")
        self._note_timer.start()

    def _flush_note(self):
        """把待保存的笔记落库。

        切词、切模式、切词表、离开本页、关窗口前都会调它。原实现只在"切换到
        另一个词"时保存，直接关工具就会丢掉正在编辑的内容——4.2 把这条点名为
        违反原则 P4 的缺陷。
        """
        if self._pending_editor is None:
            return
        editor, hint = self._pending_editor, self._pending_hint
        self._pending_editor = self._pending_hint = None
        self._note_timer.stop()

        korean = editor.property("wordKey")
        if not korean:
            return
        note = self.database.get_word_note(korean)
        self.database.save_word_note(
            korean, editor.toPlainText(), bool(note["is_starred"]) if note else False
        )
        if hint is not None:
            hint.setText("已保存")

    def flush_pending(self):
        """关窗口前冲刷未落库的笔记（`MainWindow.closeEvent` 调用）。"""
        self._flush_note()

    def hideEvent(self, event):
        # 切到别的页面也要冲刷：本页不是当前页时，防抖定时器还要再等 1.5 秒
        self._flush_note()
        super().hideEvent(event)

    # ==================================================================
    # 空状态与提示条
    # ==================================================================

    def _show_empty(self, title, description="", action_text=None, action_icon=None,
                    glyph="folder-open", handler=None):
        self.empty.set_content(title, description, action_text, action_icon, glyph)
        self._empty_action = handler
        self.left_stack.setCurrentIndex(1)

    def _show_table(self):
        self.left_stack.setCurrentIndex(0)

    def _on_empty_action(self):
        if self._empty_action:
            self._empty_action()

    def _on_banner_action(self):
        if self._banner_action:
            self._banner_action()