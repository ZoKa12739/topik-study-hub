"""P4 · 知识碎片（`PRODUCT_SPEC` 4.5 / D7 方案 C / D8 方案 B）。

## 这一页只做收集与查看，**不做文字识别**

「提取图片文字」按钮、`ImageOCRWorker`、结果文本框都已随 v1.6 移除（见 4.5 的实施
裁定）。因此这里没有 OCR 相关控件，也没有 `ocr_text` 字段——识别在工具之外完成。

## 碎片清单来自两个地方，但**不扫目录树**

1. **资料库里的图片**——直接读 P3 的持久化索引（`materials` 快照），一行不查文件系统。
2. **收集进来的图片**——`data/snippets/`，粘贴与导入的落点；这是本工具自己写的目录，
   条目在百级以内，`os.scandir` 一遍最省事。

原实现每次进页面、每次启动都递归扫一遍整个资料根目录找图片，4.5 的边界条款把它点名
为已知缺陷（"资料多时打开工具即卡"）。现在这一页的刷新成本与"资料有多少"无关。

## 缩略图按需解码

`QListWidget` 不是虚拟化视图：一进页面就给几百行都解码缩略图，等于先卡几秒。所以只
解码**当前可见的那几行**（滚动/筛选/改尺寸后重算一遍），并且用
`QImageReader.setScaledSize` 让 Qt 在解码阶段就缩到缩略图尺寸——一张 4K 截图不再先
解出几十 MB 的位图再缩回去。

## 标题与笔记是第二层

默认标题取文件名；用户改过之后，那个名字就是用户花时间写的东西。防抖 1.5 秒落库，
`hideEvent` 与 `MainWindow.closeEvent` 各补一道冲刷——与 P1 同一条理由：退出路径上
没冲掉，刚写的就没了（原则 P4）。
"""

import os

from PySide6.QtCore import QMutex, QMutexLocker, QSize, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QImage, QImageReader, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.config import SNIPPETS_DIR, SUBJECT_FOLDERS, normalize_path
from core.snippets import (
    COLLECTED_SOURCE,
    collect_records,
    copy_image_into,
    pasted_file_name,
    unique_destination,
)
from ui.components import Banner, EmptyState, make_copyable, show_toast
from ui.icons import icon
from ui.theme import TEXT_COLORS
from ui.vault_view import reveal_in_folder

# 缩略图的边长（像素）。140 是网格里的显示尺寸，解码到 200 留一点余量给高 DPI。
THUMB_PX = 200

# 一次最多建多少个 item。网格视图下 `QListWidget` 会为每一行建真实控件，条目上万时
# 光建表就卡住。4.5 的边界条款要求"惰性/分页"，这里取的是**有上限的分页提示**：
# 超出只显示前 N 张，并在状态行说明"匹配多少张、只显示多少张"，不假装全都在。
MAX_ITEMS = 500


def _decode_thumbnail(path):
    """把一张图解码成缩略图。失败了返回空 `QImage`（调用方据此不再重试）。"""
    reader = QImageReader(path)
    reader.setAutoTransform(True)
    size = reader.size()
    if size.isValid() and (size.width() > THUMB_PX or size.height() > THUMB_PX):
        # 关键的一步：让**解码器**按目标尺寸解，而不是先解全图再 `scaled()`
        reader.setScaledSize(size.scaled(QSize(THUMB_PX, THUMB_PX), Qt.KeepAspectRatio))
    image = reader.read()
    if image.isNull():
        return image
    if image.width() > THUMB_PX or image.height() > THUMB_PX:
        # 头部没报出尺寸的格式（少数插件如此）：退回到解完再缩
        image = image.scaled(THUMB_PX, THUMB_PX, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    return image


class ThumbnailWorker(QThread):
    """在后台按需解码缩略图。

    队列是**替换**而不是追加：快速滚动时，已经滚过去的那几屏不该还排在前面积压着。
    信号只传 path + 解码结果，不碰数据库——sqlite 连接不跨线程（与 `core/library.py`
    同一条纪律）。
    """

    thumb_ready = Signal(str, QImage)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._mutex = QMutex()
        self._queue = []
        self._running = True

    def set_queue(self, paths):
        with QMutexLocker(self._mutex):
            self._queue = list(paths)

    def run(self):
        while True:
            with QMutexLocker(self._mutex):
                if not self._running:
                    return
                path = self._queue.pop(0) if self._queue else None
            if path is None:
                self.msleep(30)
                continue
            # 失败也照发：视图据此把这一项记进"不再重试"，否则每次滚动都会把
            # 同一张坏图重新解码一遍（图片格式不支持、文件被删、文件本身就是坏的）
            self.thumb_ready.emit(path, _decode_thumbnail(path))

    def stop(self):
        with QMutexLocker(self._mutex):
            self._running = False
            self._queue = []
        self.wait(3000)


class SnippetsView(QWidget):
    """P4 知识碎片。"""

    # 「在资料库中定位」/ 空提示条上的「打开资料库」都走这个信号；
    # 空字符串 = 只切页、不定位（`MainWindow` 负责解释）。
    reveal_in_vault = Signal(str)

    NOTE_DEBOUNCE_MS = 1500

    def __init__(self, database):
        super().__init__()
        self.database = database

        self._rows = []            # `list_snippets()` 的全量
        self._shown = []           # 当前筛选后的结果
        self._selected = None      # 选中项的 path（**不是行号**：列表会重建）
        self._items = {}           # path → QListWidgetItem（缩略图回来时按它找回那一行）
        self._iconized = set()     # 已经拿到缩略图的 path
        self._failed = set()       # 解码失败过的 path（不再重试）
        self._last_request = None  # 上一次的缩略图请求，用来跳过重复计算
        self._empty_action = None
        self._banner_action = None
        self._preview_source = None

        self._pending_editor = None
        self._pending_hint = None
        self._pending_note_path = None
        self._pending_title_path = None

        self._note_timer = QTimer(self)
        self._note_timer.setSingleShot(True)
        self._note_timer.setInterval(self.NOTE_DEBOUNCE_MS)
        self._note_timer.timeout.connect(self._flush_note)

        self.thumb_worker = ThumbnailWorker(self)
        self.thumb_worker.thumb_ready.connect(self._on_thumb_ready)
        self.thumb_worker.start()

        self.init_ui()
        self.refresh()

    # ==================================================================
    # 布局
    # ==================================================================

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        title = QLabel("知识碎片")
        title.setObjectName("pageTitle")
        layout.addWidget(title)

        subtitle = QLabel(
            "零散截图的收集处。在别处截好图，回到这里按 Ctrl+V 贴进来；也可以直接导入图片。"
        )
        subtitle.setObjectName("pageSubtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        layout.addLayout(self._build_toolbar())

        self.lbl_status = QLabel("")
        self.lbl_status.setObjectName("faint")
        self.lbl_status.setWordWrap(True)
        layout.addWidget(self.lbl_status)

        self.banner = Banner()
        self.banner.action_clicked.connect(self._on_banner_action)
        layout.addWidget(self.banner)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._build_list_area())
        splitter.addWidget(self._build_detail_area())
        splitter.setSizes([520, 560])
        layout.addWidget(splitter, 1)

        self.apply_view_mode(True)

    def _build_toolbar(self):
        bar = QHBoxLayout()
        bar.setSpacing(8)

        self.btn_paste = QPushButton(icon("clipboard"), "粘贴图片 (Ctrl+V)")
        self.btn_paste.clicked.connect(self.paste_image)
        bar.addWidget(self.btn_paste)

        self.btn_import = QPushButton(icon("upload"), "导入图片")
        self.btn_import.clicked.connect(self.import_images)
        bar.addWidget(self.btn_import)

        label = QLabel("来源")
        label.setObjectName("muted")
        bar.addWidget(label)
        self.combo_source = QComboBox()
        self.combo_source.setMinimumWidth(96)
        self.combo_source.currentIndexChanged.connect(self.apply_filter)
        bar.addWidget(self.combo_source)

        label = QLabel("标签")
        label.setObjectName("muted")
        bar.addWidget(label)
        self.combo_tag = QComboBox()
        self.combo_tag.setMinimumWidth(110)
        self.combo_tag.currentIndexChanged.connect(self.apply_filter)
        bar.addWidget(self.combo_tag)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("搜索文件名 / 标题 / 笔记（例如 副词、91届）…")
        self.search_input.textChanged.connect(self.apply_filter)
        bar.addWidget(self.search_input, 1)

        # 复用 P1 的模式切换样式（可选中按钮），不新增 design token
        self.btn_view_mode = QPushButton(icon("image"), "网格")
        self.btn_view_mode.setObjectName("modeSwitch")
        self.btn_view_mode.setCheckable(True)
        self.btn_view_mode.setChecked(True)
        self.btn_view_mode.setToolTip("在网格视图与列表视图之间切换")
        self.btn_view_mode.toggled.connect(self.apply_view_mode)
        bar.addWidget(self.btn_view_mode)
        return bar

    def _build_list_area(self):
        self.list_widget = QListWidget()
        self.list_widget.setObjectName("snippetList")
        self.list_widget.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list_widget.itemSelectionChanged.connect(self._on_selection_changed)
        self.list_widget.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.list_widget.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self._show_context_menu)
        self.list_widget.verticalScrollBar().valueChanged.connect(
            lambda _value: self._request_visible_thumbs()
        )
        # 拖动左右分隔条会改变列表宽度（每行露出几张图就变了），但不会改变本页尺寸
        self.list_widget.viewport().installEventFilter(self)

        self.empty = EmptyState("", "")
        self.empty.action_clicked.connect(self._on_empty_action)

        self.list_stack = QStackedWidget()
        self.list_stack.addWidget(self.list_widget)   # 0
        self.list_stack.addWidget(self.empty)         # 1
        return self.list_stack

    def _build_detail_area(self):
        split = QSplitter(Qt.Vertical)

        # ---- 上：预览 ----
        self.preview_scroll = QScrollArea()
        self.preview_scroll.setObjectName("previewScroll")
        self.preview_scroll.setWidgetResizable(True)
        self.preview_scroll.setFrameShape(QFrame.NoFrame)

        self.lbl_image = QLabel("请在左侧选择一张截图")
        self.lbl_image.setObjectName("previewArea")
        self.lbl_image.setAlignment(Qt.AlignCenter)
        self.lbl_image.setMinimumHeight(160)
        self.lbl_image.setToolTip("双击用系统看图工具打开原图")
        self.preview_scroll.setWidget(self.lbl_image)
        # 拖动分隔条会改变预览区尺寸，但不会改变本页的尺寸——只能靠事件过滤器重算缩放
        self.preview_scroll.viewport().installEventFilter(self)
        split.addWidget(self.preview_scroll)

        # ---- 下：详情（笔记 / 标签 / 动作） ----
        panel = QFrame()
        panel.setObjectName("surface")
        box = QVBoxLayout(panel)
        box.setContentsMargins(16, 16, 16, 16)
        box.setSpacing(8)

        self.lbl_detail_name = QLabel("未选择碎片")
        self.lbl_detail_name.setObjectName("sectionTitle")
        self.lbl_detail_name.setWordWrap(True)
        box.addWidget(make_copyable(self.lbl_detail_name))

        self.lbl_detail_meta = QLabel("")
        self.lbl_detail_meta.setObjectName("muted")
        self.lbl_detail_meta.setWordWrap(True)
        box.addWidget(self.lbl_detail_meta)

        self.lbl_detail_path = QLabel("")
        self.lbl_detail_path.setObjectName("faint")
        self.lbl_detail_path.setWordWrap(True)
        box.addWidget(make_copyable(self.lbl_detail_path))

        heading = QLabel("标题")
        heading.setObjectName("sectionTitle")
        box.addSpacing(4)
        box.addWidget(heading)
        self.title_input = QLineEdit()
        self.title_input.setPlaceholderText("给这张图起个名字（默认用文件名）")
        self.title_input.textEdited.connect(self._on_title_edited)
        self.title_input.editingFinished.connect(self._flush_title)
        box.addWidget(self.title_input)

        note_row = QHBoxLayout()
        heading = QLabel("说明")
        heading.setObjectName("sectionTitle")
        note_row.addWidget(heading)
        note_row.addStretch()
        self.lbl_note_hint = QLabel("")
        self.lbl_note_hint.setObjectName("muted")
        note_row.addWidget(self.lbl_note_hint)
        box.addSpacing(4)
        box.addLayout(note_row)

        self.note_edit = QTextEdit()
        self.note_edit.setPlaceholderText("这张图在讲什么？当时为什么存它？（自动保存）")
        self.note_edit.setFixedHeight(96)
        self.note_edit.textChanged.connect(self._on_note_edited)
        box.addWidget(self.note_edit)

        heading = QLabel("标签")
        heading.setObjectName("sectionTitle")
        box.addSpacing(4)
        box.addWidget(heading)

        self.tag_box = QWidget()
        self.tag_layout = QVBoxLayout(self.tag_box)
        self.tag_layout.setContentsMargins(0, 0, 0, 0)
        self.tag_layout.setSpacing(4)
        box.addWidget(self.tag_box)

        tag_row = QHBoxLayout()
        tag_row.setSpacing(6)
        self.tag_input = QLineEdit()
        self.tag_input.setPlaceholderText("新标签，如 副词 / 91届")
        self.tag_input.returnPressed.connect(self.add_tag)
        tag_row.addWidget(self.tag_input, 1)
        self.btn_add_tag = QPushButton("添加")
        self.btn_add_tag.setObjectName("iconButton")
        self.btn_add_tag.clicked.connect(lambda: self.add_tag())
        tag_row.addWidget(self.btn_add_tag)
        box.addLayout(tag_row)

        self.lbl_tag_pool = QLabel("已有标签，点一下加给这张图")
        self.lbl_tag_pool.setObjectName("faint")
        box.addWidget(self.lbl_tag_pool)

        self.tag_pool = QWidget()
        self.tag_pool_layout = QGridLayout(self.tag_pool)
        self.tag_pool_layout.setContentsMargins(0, 0, 0, 0)
        self.tag_pool_layout.setHorizontalSpacing(6)
        self.tag_pool_layout.setVerticalSpacing(4)
        box.addWidget(self.tag_pool)

        actions = QHBoxLayout()
        actions.setSpacing(6)
        self.btn_locate = QPushButton(icon("folder"), "在资料库中定位")
        self.btn_locate.setToolTip("切到资料库并选中这张图的来源")
        self.btn_locate.clicked.connect(self._locate_in_vault)
        actions.addWidget(self.btn_locate)
        self.btn_reveal = QPushButton(icon("folder-open"), "在文件夹中显示")
        self.btn_reveal.clicked.connect(self._reveal)
        actions.addWidget(self.btn_reveal)
        self.btn_open = QPushButton(icon("image"), "打开原图")
        self.btn_open.clicked.connect(self._open_externally)
        actions.addWidget(self.btn_open)
        actions.addStretch()
        box.addSpacing(4)
        box.addLayout(actions)

        detail_scroll = QScrollArea()
        detail_scroll.setObjectName("detailScroll")
        detail_scroll.setWidgetResizable(True)
        detail_scroll.setFrameShape(QFrame.NoFrame)
        detail_scroll.setWidget(panel)
        split.addWidget(detail_scroll)

        split.setSizes([320, 380])
        return split

    # ==================================================================
    # 数据 → 界面
    # ==================================================================

    def refresh(self):
        """重读碎片清单。

        登记只读 P3 的**索引快照**，绝不在这里走目录树（D6-C：扫描是资料库那一页的
        职责）。索引还没建起来时，用一条行内提示说清楚"图片清单还不全"，并给一个过去
        建索引的入口——而不是自己偷偷扫一遍。

        `MainWindow` 在三个时刻调它：设置页改了资料目录、资料库扫完一轮、数据被重新
        导入。构造时也调一次（那时索引多半还没有内容）。
        """
        self._flush_note()
        self._flush_title()

        existing = {row["path"]: row for row in self.database.list_snippets()}
        records = collect_records(self.database.library_snapshot(), existing)
        # 只有真的登记了新行才重读一遍：一次刷新里绝大多数情况是"什么都没变"，
        # 那时 `existing` 就是最新的，不必再查一次库
        added = self.database.register_snippets(records)
        self._rows = self.database.list_snippets() if added else list(existing.values())

        self._update_index_banner()
        self.reload()

    def reload(self, keep=None):
        """重读（已在内存里的）碎片列表并重建界面。`keep` 是要保住的选中项 path。"""
        if keep is not None:
            self._selected = keep
        self._refresh_source_combo()
        self._refresh_tag_combo()
        self.apply_filter()

    def _update_index_banner(self):
        if self.database.last_indexed_at():
            self.banner.clear()
            self._banner_action = None
            return
        self._banner_action = lambda: self.reveal_in_vault.emit("")
        self.banner.show_message(
            "info",
            "资料库还没有建立索引，下面只有已经收集进来的碎片。"
            "去「TOPIK 资料库」重建索引，资料目录里的截图也会出现在这里。",
            action_text="打开资料库",
            action_icon="folder",
        )

    def _refresh_source_combo(self):
        """来源下拉按**实际出现的**来源建，不写死四科——收藏目录里可能一张图都没有，
        而某个科目也可能一张截图都没有，列一个永远筛不出东西的选项只会让人怀疑工具。"""
        current = self.combo_source.currentData() if self.combo_source.count() else None
        present = {row["source_subject"] or "" for row in self._rows}
        order = [COLLECTED_SOURCE, *SUBJECT_FOLDERS]
        self.combo_source.blockSignals(True)
        self.combo_source.clear()
        self.combo_source.addItem("全部来源", None)
        for source in order:
            if source in present:
                self.combo_source.addItem(source, source)
        for source in sorted(present - set(order)):
            if source:
                self.combo_source.addItem(source, source)
        index = self.combo_source.findData(current)
        self.combo_source.setCurrentIndex(index if index >= 0 else 0)
        self.combo_source.blockSignals(False)

    def _refresh_tag_combo(self):
        current = self.combo_tag.currentData() if self.combo_tag.count() else None
        self.combo_tag.blockSignals(True)
        self.combo_tag.clear()
        self.combo_tag.addItem("全部标签", None)
        for row in self.database.all_snippet_tags():
            self.combo_tag.addItem(f"#{row['tag']}（{row['n']}）", row["tag"])
        index = self.combo_tag.findData(current)
        self.combo_tag.setCurrentIndex(index if index >= 0 else 0)
        self.combo_tag.blockSignals(False)

    def apply_filter(self, *_ignored):
        source = self.combo_source.currentData()
        tag = self.combo_tag.currentData()
        keyword = self.search_input.text().strip().lower()

        rows = []
        for row in self._rows:
            if row["missing"]:
                # 文件不在原位就不列出来：这一页是"翻看自己的截图"，点开一张已经不在
                # 的图只会得到一句"加载失败"。索引与笔记都还在，文件回来它就回来。
                continue
            if source is not None and row["source_subject"] != source:
                continue
            if tag and tag not in row["tags"]:
                continue
            if keyword and not self._matches(row, keyword):
                continue
            rows.append(row)

        # 新收录的排在前面：刚贴进来的图应该在第一屏
        rows.sort(key=lambda row: (row["created_at"] or "", row["path"]), reverse=True)
        self._shown = rows
        self._render_list()
        self._update_status()
        self._update_empty_state()

    def _matches(self, row, keyword):
        for field in (row["title"], os.path.basename(row["path"]), row["note"]):
            if field and keyword in field.lower():
                return True
        return False

    def clear_filters(self):
        self.search_input.clear()
        if self.combo_source.count():
            self.combo_source.setCurrentIndex(0)
        if self.combo_tag.count():
            self.combo_tag.setCurrentIndex(0)

    def _render_list(self):
        keep = self._selected
        self.list_widget.blockSignals(True)   # 重建期间的选中变化不该去刷详情面板
        self.list_widget.clear()
        # 列表清空 = 所有缩略图都没了，标记要一起清，否则它们再也不会被重新解码
        self._iconized.clear()
        self._items.clear()
        self._last_request = None

        for row in self._shown[:MAX_ITEMS]:
            item = QListWidgetItem(self._item_text(row))
            item.setData(Qt.UserRole, row["path"])
            item.setToolTip(self._tooltip(row))
            self.list_widget.addItem(item)
            self._items[row["path"]] = item
        self.list_widget.blockSignals(False)

        if keep:
            for index in range(self.list_widget.count()):
                if self.list_widget.item(index).data(Qt.UserRole) == keep:
                    self.list_widget.setCurrentRow(index)   # 信号活着 → 详情面板同步
                    break
            else:
                self._select(None)
        else:
            self._select(None)

        self._request_visible_thumbs()

    def _display_title(self, row):
        return row["title"] or os.path.basename(row["path"])

    def _item_text(self, row):
        title = self._display_title(row)
        if self.btn_view_mode.isChecked():
            return title
        bits = [row["source_subject"] or "未分类"]
        if row["tags"]:
            bits.append(" ".join("#" + tag for tag in row["tags"]))
        if row["note"]:
            bits.append("有说明")
        return f"{title}\n    └─ " + " · ".join(bits)

    def _tooltip(self, row):
        lines = [self._display_title(row), row["path"]]
        if row["tags"]:
            lines.append(" ".join("#" + tag for tag in row["tags"]))
        return "\n".join(lines)

    def _update_status(self):
        live = sum(1 for row in self._rows if not row["missing"])
        bits = [f"共 {live} 张碎片"]
        active = []
        if self.combo_source.currentData() is not None:
            active.append(self.combo_source.currentText())
        if self.combo_tag.currentData():
            active.append(f"#{self.combo_tag.currentData()}")
        keyword = self.search_input.text().strip()
        if keyword:
            active.append(f"“{keyword}”")
        if active:
            bits.append(f"筛选：{' + '.join(active)} · 匹配 {len(self._shown)} 张")
        if len(self._shown) > MAX_ITEMS:
            bits.append(f"只显示前 {MAX_ITEMS} 张，缩小范围可看其余")
        self.lbl_status.setText(" · ".join(bits))

    def apply_view_mode(self, is_grid):
        """网格 / 列表切换（D7 方案 C：两种场景都覆盖，默认网格）。"""
        self.btn_view_mode.setText("网格" if is_grid else "列表")
        self.btn_view_mode.setIcon(icon("image" if is_grid else "file-text"))
        self.list_widget.setViewMode(QListWidget.IconMode if is_grid else QListWidget.ListMode)
        self.list_widget.setResizeMode(
            QListWidget.Adjust if is_grid else QListWidget.Fixed
        )
        self.list_widget.setWordWrap(is_grid)
        if is_grid:
            self.list_widget.setSpacing(12)
            self.list_widget.setGridSize(QSize(150, 170))
            self.list_widget.setIconSize(QSize(130, 130))
        else:
            self.list_widget.setSpacing(4)
            self.list_widget.setGridSize(QSize())
            self.list_widget.setIconSize(QSize(56, 56))
        # 两行文字的列表项与一行的网格项高度不同，模式一换就得重建
        self._render_list()

    # ==================================================================
    # 缩略图（只解码看得见的那几行）
    # ==================================================================

    def _request_visible_thumbs(self):
        if self.list_stack.currentIndex() != 0:
            return
        viewport_rect = self.list_widget.viewport().rect()
        wanted = []
        for index in range(self.list_widget.count()):
            item = self.list_widget.item(index)
            path = item.data(Qt.UserRole)
            if path in self._iconized or path in self._failed:
                continue
            if self.list_widget.visualItemRect(item).intersects(viewport_rect):
                wanted.append(path)

        request = tuple(wanted)
        if request == self._last_request:
            return   # 滚动停在原地时不重复排队
        self._last_request = request
        self.thumb_worker.set_queue(wanted)

    def _on_thumb_ready(self, path, image):
        if image.isNull():
            self._failed.add(path)
            return
        item = self._items.get(path)
        if item is None:
            # 解码期间列表被重建/筛选掉了这一项：结果直接丢弃，不补进新的列表
            return
        item.setIcon(QPixmap.fromImage(image))
        self._iconized.add(path)

    def eventFilter(self, obj, event):
        if event.type() == event.Type.Resize:
            if obj is self.preview_scroll.viewport():
                self._fit_preview()
            elif obj is self.list_widget.viewport():
                # 一屏能露出几张图变了，可见集合就得重算
                self._request_visible_thumbs()
        return False

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._request_visible_thumbs()

    # ==================================================================
    # 选择与详情
    # ==================================================================

    def _row_for(self, path):
        for row in self._rows:
            if row["path"] == path:
                return row
        return None

    def _on_selection_changed(self):
        items = self.list_widget.selectedItems()
        path = items[0].data(Qt.UserRole) if items else None
        self._select(self._row_for(path) if path else None)

    def _select(self, row):
        # 先冲刷：切换选中项之后，编辑器里的文字已经属于上一张图了
        self._flush_note()
        self._flush_title()

        self._selected = row["path"] if row else None
        enabled = row is not None
        for button in (self.btn_open, self.btn_reveal, self.btn_add_tag):
            button.setEnabled(enabled)
        # 收集进来的碎片不在资料库里，没有"来源"可以定位
        self.btn_locate.setEnabled(
            enabled and row["source_subject"] != COLLECTED_SOURCE
        )

        if row is None:
            self.lbl_detail_name.setText("未选择碎片")
            self.lbl_detail_meta.clear()
            self.lbl_detail_path.clear()
            self.title_input.clear()
            self.title_input.setEnabled(False)
            self.note_edit.clear()
            self.note_edit.setEnabled(False)
            self.tag_input.clear()
            self.tag_input.setEnabled(False)
            self._render_tags([])
            self._render_tag_pool([])
            self._show_preview(None)
            return

        os_path = row["path"]
        self.lbl_detail_name.setText(self._display_title(row))
        meta = [row["source_subject"] or "未分类"]
        extension = os.path.splitext(os_path)[1].lstrip(".").upper()
        if extension:
            meta.append(extension)
        if row["width"] and row["height"]:
            meta.append(f"{row['width']}×{row['height']}")
        if row["created_at"]:
            meta.append(f"收录于 {row['created_at'][:10]}")
        self.lbl_detail_meta.setText(" · ".join(meta))
        self.lbl_detail_path.setText(os_path)

        self.title_input.setEnabled(True)
        self.title_input.setText(row["title"])
        self.note_edit.setEnabled(True)
        self.note_edit.blockSignals(True)
        self.note_edit.setPlainText(row["note"])
        self.note_edit.blockSignals(False)
        self.lbl_note_hint.setText("")
        self.tag_input.setEnabled(True)
        self._render_tags(row["tags"])
        self._render_tag_pool(row["tags"])
        self._show_preview(os_path, row)

    def _show_preview(self, path, row=None):
        if not path:
            self._preview_source = None
            self.lbl_image.setPixmap(QPixmap())
            self.lbl_image.setText("请在左侧选择一张截图")
            return
        pixmap = QPixmap(path)
        if pixmap.isNull():
            self._preview_source = None
            self.lbl_image.setPixmap(QPixmap())
            self.lbl_image.setText("图片加载失败")
            return
        self._preview_source = pixmap
        self.lbl_image.setText("")
        self._fit_preview()
        if row is not None and (not row["width"] or not row["height"]):
            # 尺寸只为显示用，**选中时才写**：为了几个数字去读遍所有图片的头部，
            # 是把"打开这一页"的成本抬成"解析整个碎片目录"
            self.database.set_snippet_size(path, pixmap.width(), pixmap.height())
            row["width"], row["height"] = pixmap.width(), pixmap.height()
            self.lbl_detail_meta.setText(
                self.lbl_detail_meta.text() + f" · {pixmap.width()}×{pixmap.height()}"
            )

    def _fit_preview(self):
        if self._preview_source is None or self._preview_source.isNull():
            return
        available = self.preview_scroll.viewport().size()
        if available.width() < 40 or available.height() < 40:
            return
        # 按容器缩放（4.5 边界：整屏 4K 截图也不该撑出滚动条），要看细节双击交给系统看图工具
        self.lbl_image.setPixmap(
            self._preview_source.scaled(
                available, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
        )

    # ==================================================================
    # 标签
    # ==================================================================

    def _render_tags(self, tags):
        while self.tag_layout.count():
            item = self.tag_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # 先摘出父子关系再 deleteLater：Qt 删除是延迟的，
                # 只调 deleteLater 的话旧胶囊会留在旧位置直到下一轮事件循环
                widget.setParent(None)
                widget.deleteLater()

        if not tags:
            hint = QLabel("（还没有标签）")
            hint.setObjectName("faint")
            self.tag_layout.addWidget(hint)
            return

        for tag in tags:
            row = QHBoxLayout()
            row.setSpacing(4)
            chip = QPushButton(f"#{tag}")
            chip.setObjectName("tagChip")
            chip.setCursor(Qt.PointingHandCursor)
            chip.setToolTip("按这个标签过滤")
            chip.clicked.connect(lambda _checked=False, value=tag: self.filter_by_tag(value))
            row.addWidget(chip)
            remove = QPushButton()
            remove.setObjectName("chipRemove")
            remove.setIcon(icon("x", TEXT_COLORS["faint"], 12))
            remove.setToolTip(f"移除标签 #{tag}")
            remove.clicked.connect(lambda _checked=False, value=tag: self.remove_tag(value))
            row.addWidget(remove)
            row.addStretch()

            wrapper = QWidget()
            wrapper.setLayout(row)
            self.tag_layout.addWidget(wrapper)

    def _render_tag_pool(self, current):
        """已有标签的快捷入口，排除这张图已经有的那些——标签会因此收敛到同一批名字上。"""
        while self.tag_pool_layout.count():
            item = self.tag_pool_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

        pool = [
            row["tag"] for row in self.database.all_snippet_tags() if row["tag"] not in current
        ]
        visible = bool(pool) and self._selected is not None
        self.lbl_tag_pool.setVisible(visible)
        self.tag_pool.setVisible(visible)
        if not visible:
            return

        for index, tag in enumerate(pool):
            chip = QPushButton(f"#{tag}")
            chip.setObjectName("tagChip")
            chip.setCursor(Qt.PointingHandCursor)
            chip.setToolTip(f"把 #{tag} 加给这张碎片")
            chip.clicked.connect(lambda _checked=False, value=tag: self.add_tag(value))
            # 一行三个：侧栏只有三百多宽，排成一行会被挤成省略号
            self.tag_pool_layout.addWidget(chip, index // 3, index % 3)

    def add_tag(self, tag=None):
        tag = tag if tag is not None else self.tag_input.text()
        if not self._selected or not self.database.add_snippet_tag(self._selected, tag):
            return
        self.tag_input.clear()
        self._reload_row(self._selected)

    def remove_tag(self, tag):
        if not self._selected:
            return
        self.database.remove_snippet_tag(self._selected, tag)
        self._reload_row(self._selected)

    def filter_by_tag(self, tag):
        index = self.combo_tag.findData(tag)
        if index >= 0:
            self.combo_tag.setCurrentIndex(index)   # 触发的 apply_filter 会重建列表

    def _reload_row(self, path):
        """就地刷新一行（加/删标签这类小变化不必把整张列表重读一遍）。"""
        row = self._row_for(path)
        if row is None:
            return
        row["tags"] = self.database.snippet_tags(path)
        self._refresh_tag_combo()
        self.apply_filter()

    # ==================================================================
    # 标题与笔记（第二层，防抖落库）
    # ==================================================================

    def _on_title_edited(self, _text):
        self._pending_title_path = self._selected

    def _flush_title(self):
        if self._pending_title_path is None:
            return
        path, self._pending_title_path = self._pending_title_path, None
        row = self._row_for(path)
        if row is None:
            return
        title = self.title_input.text().strip() if path == self._selected else row["title"]
        self.database.rename_snippet(path, title)
        row["title"] = title
        self.lbl_detail_name.setText(self._display_title(row))

    def _on_note_edited(self):
        if self._selected is None:
            return
        self._pending_editor = True
        self._pending_note_path = self._selected
        self._pending_hint = self.lbl_note_hint
        self.lbl_note_hint.setText("保存中…")
        self._note_timer.start()

    def _flush_note(self):
        """把待保存的说明落库。

        切换碎片、切筛选、离开本页、关窗口前都会调它——与 P1 同一条理由：只在"切到
        另一张图"时保存的话，直接关工具就会丢掉正在写的东西（原则 P4）。
        """
        if self._pending_editor is None:
            return
        path, self._pending_note_path = self._pending_note_path, None
        self._pending_editor = None
        hint, self._pending_hint = self._pending_hint, None
        self._note_timer.stop()
        if not path:
            return
        note = self.note_edit.toPlainText()
        self.database.save_snippet_note(path, note)
        row = self._row_for(path)
        if row is not None:
            row["note"] = note
        if hint is not None:
            hint.setText("已保存")

    def flush_pending(self):
        """关窗口前冲刷未落库的说明与标题（`MainWindow.closeEvent` 调用）。"""
        self._flush_note()
        self._flush_title()

    def hideEvent(self, event):
        # 切到别的页面也要冲刷：本页不是当前页时，防抖定时器还要再等 1.5 秒
        self.flush_pending()
        super().hideEvent(event)

    def shutdown(self):
        """窗口关闭时收线程（`MainWindow.closeEvent` 调用）。

        不这么做，`QThread` 会在还在跑的时候被销毁——Qt 会打印
        "Destroyed while thread is still running"，运气不好直接崩在退出路径上。
        """
        self.flush_pending()
        self.thumb_worker.stop()

    # ==================================================================
    # 收集
    # ==================================================================

    def keyPressEvent(self, event):
        """`Ctrl+V` 贴图。

        只在本页（或它的子控件）持有焦点时生效：列表与按钮不处理 `Ctrl+V`，按键会
        冒泡到这里；而搜索框和笔记编辑器自己会处理它（贴文字），不会走到这里。
        """
        if event.matches(QKeySequence.Paste):
            self.paste_image()
        else:
            super().keyPressEvent(event)

    def paste_image(self):
        image = QApplication.clipboard().image()
        if image.isNull():
            show_toast(self.window(), "剪贴板里没有图片", "warning")
            return

        os.makedirs(str(SNIPPETS_DIR), exist_ok=True)
        destination = unique_destination(SNIPPETS_DIR, pasted_file_name())
        if not image.save(destination, "PNG"):
            show_toast(self.window(), "剪贴板图片保存失败", "danger")
            return
        self.select_path(destination)
        show_toast(self.window(), "已贴入剪贴板里的截图")

    def import_images(self):
        paths, _filter = QFileDialog.getOpenFileNames(
            self,
            "导入图片",
            self._import_start_dir(),
            "图片 (*.png *.jpg *.jpeg *.gif *.webp *.bmp)",
        )
        if not paths:
            return

        copied = []
        failed = []
        for source in paths:
            try:
                copied.append(copy_image_into(source, SNIPPETS_DIR))
            except OSError:
                failed.append(os.path.basename(source))
        if copied:
            self.select_path(copied[-1])
        if failed:
            self.banner.show_message(
                "warning", f"{len(failed)} 张图片没能复制进来：{failed[0]}"
            )
        if copied:
            show_toast(self.window(), f"已导入 {len(copied)} 张图片")

    def _import_start_dir(self):
        """文件选择器从资料根起步。截图多半就散在资料里，从那儿开始少点几次。"""
        root = self.database.get_material_root()
        return root if root and os.path.isdir(root) else ""

    def select_path(self, path):
        """选中一张碎片并滚到它（P3 双击图片 → 这里；贴完图后也用它定位新碎片）。"""
        path = normalize_path(path)
        self.clear_filters()          # 否则新碎片可能正被当前筛选挡在外面
        self._selected = path         # reload 按 path 找回选中项，得先设上
        self.refresh()

        for index in range(self.list_widget.count()):
            item = self.list_widget.item(index)
            if item.data(Qt.UserRole) == path:
                self.list_widget.scrollToItem(item, QAbstractItemView.PositionAtCenter)
                return path
        self.banner.show_message("warning", f"这张图不在碎片清单里：{path}")
        return None

    # ==================================================================
    # 空状态与提示条
    # ==================================================================

    def _show_empty(self, title, description="", action_text=None, action_icon=None,
                    glyph="image", handler=None):
        self.empty.set_content(title, description, action_text, action_icon, glyph)
        self._empty_action = handler
        self.list_stack.setCurrentIndex(1)

    def _show_list(self):
        self.list_stack.setCurrentIndex(0)
        self._request_visible_thumbs()

    def _on_empty_action(self):
        if self._empty_action:
            self._empty_action()

    def _on_banner_action(self):
        if self._banner_action:
            self._banner_action()

    def _update_empty_state(self):
        live = [row for row in self._rows if not row["missing"]]
        if not live:
            self._show_empty(
                "还没有知识碎片",
                "两种收集方式：在别处截好图后回到这里按 Ctrl+V 直接贴进来，"
                "或点「导入图片」把已有的截图文件收进来。"
                "资料库里的截图会在索引建好后自动出现在这里。",
                action_text="粘贴剪贴板里的图",
                action_icon="clipboard",
                handler=self.paste_image,
            )
            return
        if not self._shown:
            self._show_empty(
                "没有找到匹配的碎片",
                "换个关键词，或放宽来源与标签。",
                action_text="清除筛选",
                action_icon="x",
                glyph="search",
                handler=self.clear_filters,
            )
            return
        self._show_list()

    # ==================================================================
    # 动作
    # ==================================================================

    def _on_item_double_clicked(self, item):
        self._open_externally(item.data(Qt.UserRole))

    def _show_context_menu(self, position):
        item = self.list_widget.itemAt(position)
        if item is None:
            return
        path = item.data(Qt.UserRole)
        menu = QMenu(self)
        action = menu.addAction(icon("image"), "打开原图")
        action.triggered.connect(lambda: self._open_externally(path))
        action = menu.addAction(icon("folder-open"), "在文件夹中显示")
        action.triggered.connect(lambda: reveal_in_folder(path))
        action = menu.addAction(icon("clipboard"), "复制路径")
        action.triggered.connect(lambda: QApplication.clipboard().setText(path))
        row = self._row_for(path)
        if row is not None and row["source_subject"] != COLLECTED_SOURCE:
            action = menu.addAction(icon("folder"), "在资料库中定位")
            action.triggered.connect(lambda: self.reveal_in_vault.emit(path))
        menu.exec(self.list_widget.viewport().mapToGlobal(position))

    def _open_externally(self, path):
        if not path or not os.path.exists(path):
            show_toast(self.window(), "这个文件已经不在原位置了", "warning")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _reveal(self):
        if self._selected:
            reveal_in_folder(self._selected)

    def _locate_in_vault(self):
        if self._selected:
            self.reveal_in_vault.emit(self._selected)