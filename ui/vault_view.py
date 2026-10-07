"""P3 · TOPIK 资料库（`PRODUCT_SPEC` 4.4 / D6 方案 C）。

## 这一页是**只读**的

工具不改动任何资料文件，只负责让你找到它（4.4 目的）。唯一写回的是索引与用户自己
产生的东西（标签、阅读进度、打开次数）——全在数据库里，不碰文件系统。

## 索引是持久化的，且按 mtime 增量

原实现每次进入页面都 `os.walk` 一遍：进一次卡一次，而且没法挂标签与阅读进度
（没有任何持久化的挂载点）。D6 选 C——**持久化索引 + 启动时增量扫描**：
`core/library.py` 走一遍文件系统，`StudyDatabase.apply_material_scan` 只写变化的行。

扫描在线程里（`ScanWorker`），但**只有扫描在线程里**：sqlite 连接不跨线程，落库回主线程。

## 第二层数据挂在 `path` 上，不挂在 `material_id` 上

标签、阅读进度、打开次数都是用户花时间产生的，而索引表随时可以整个删掉重建（D6）。
挂在自增 id 上，重建一次它们就指向别的文件了。这与词表把笔记挂在 `word_key` 上是
同一条规则（D2）。

## 排除工具自己的目录

原实现靠 `"TOPIK_Study_Hub" in root` 判断，项目改名/移动就失效。现在比对
`core/config.PROJECT_ROOT` 的绝对路径——判据与目录名无关（4.4 状态表最后一条）。
"""

import os
import subprocess
import sys
from datetime import datetime

from PySide6.QtCore import QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QBrush, QColor, QDesktopServices, QIntValidator
from PySide6.QtWidgets import (
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
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core.config import SUBJECT_FOLDERS, normalize_path
from core.library import ScanWorker, delete_file, file_extension, pdf_page_count
from core.snippets import IMAGE_EXTS
from ui.components import (
    Banner,
    EmptyState,
    GhostLineEdit,
    InlineProgress,
    PillListDelegate,
    make_copyable,
    show_toast,
)
from ui.icons import icon
from ui.theme import TEXT_COLORS

# 扩展名 → 图标名（单色线性，取代原先的彩色 emoji）
_FILE_ICONS = {
    ".pdf": "file-text",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".gif": "image",
    ".mp3": "music",
    ".wav": "music",
    ".m4a": "music",
    ".mp4": "film",
    ".mkv": "film",
    ".pptx": "presentation",
    ".ppt": "presentation",
}


def file_icon_name(file_name):
    return _FILE_ICONS.get(file_extension(file_name), "file-text")


def human_size(size):
    """字节 → 人能读的量级。索引里存的是原始字节，显示才换算。"""
    if not size:
        return "—"
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return "—"


def reveal_in_folder(path):
    """在资源管理器里选中这个文件（4.4 的「在文件夹中显示」兜底操作）。

    `explorer /select,<path>` 是 Windows 上唯一能在**文件管理器里定位到具体文件**的做法；
    非 Windows 退回"打开所在目录"。失败不抛异常——这是个兜底按钮，它自己再报错就很难看。
    """
    try:
        if sys.platform.startswith("win"):
            subprocess.Popen(["explorer", f"/select,{os.path.normpath(path)}"])
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))
    except OSError:
        return False
    return True


# 排序模式：显示名 → 排序键。改这里就要动 `_sort_key`。
_SORT_MODES = (
    ("名称 A→Z", "name"),
    ("修改时间 新→旧", "mtime"),
    ("大小 大→小", "size"),
    ("按科目", "subject"),
)

# 列表一次最多建多少个 item。QListWidget 不是虚拟化视图，万级条目会明显变卡；
# 4.4 的边界条款要求"虚拟化或分页"，这里选的是**有上限的分页提示**：
# 超出时只在状态行说明"匹配 N 条，显示前 M 条"，不让界面卡住也不假装全都在。
MAX_ROWS = 3000


class VaultView(QWidget):
    """P3 资料库。"""

    # 双击一张图片 → 交给 P4 看（`PRODUCT_SPEC` 4.4 的出口：P4（双击图片））。
    # 图片在资料库里是"翻着看"的东西，外部看图工具一次只能开一张，而碎片页能一次
    # 扫一屏、还能记说明与标签——所以图片不走 `open_path`。
    open_in_snippets = Signal(str)

    # 索引变了就发一次：一轮扫描落库之后，以及右键删掉一份资料之后。
    # P4 的碎片清单有一半来自这份索引（第 6 期），它自己不会扫目录，
    # 只能等这个信号再重读一次——否则刚删掉的那张图会一直留在碎片页上。
    index_updated = Signal()

    # "启动时增量扫描"（D6-C）的实际触发点：排在事件循环起来之后。
    # 扫描本身在线程里，但把它挂在构造期，等于让每次启动都先等一次目录遍历。
    SCAN_DELAY_MS = 700

    def __init__(self, database):
        super().__init__()
        self.database = database

        self._rows = []          # library_snapshot 的全量
        self._shown = []         # 当前筛选+排序后的结果
        self._selected = None    # 选中项的 path（**不是索引**：列表会重建，行号会变）
        self._missing_dirs = []
        self._scan_worker = None
        self._pending_locate = None   # 「在资料库中定位」的目标，扫描完成后要选中它
        self._empty_action = None
        self._banner_action = None

        self.init_ui()
        self.reload()
        QTimer.singleShot(self.SCAN_DELAY_MS, self.start_scan)

    @property
    def root_dir(self):
        """资料根目录。取自 settings（P5/P6 配置），不再从 `__file__` 硬编码推导。

        原实现用"向上三级"当资料根，项目一移动就指向错误目录——实测本机资料已在
        `课程资料/` 之下又迁进一层，而推导结果停在 `课程资料/`。见 PRODUCT_SPEC D10。
        """
        try:
            return self.database.get_material_root()
        except Exception:
            return ""

    # ==================================================================
    # 布局
    # ==================================================================

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 20)
        layout.setSpacing(12)

        layout.addWidget(self._build_toolbar())

        self.banner = Banner()
        self.banner.action_clicked.connect(self._on_banner_action)
        layout.addWidget(self.banner)

        layout.addWidget(self._build_progress_row())

        splitter = QSplitter(Qt.Horizontal)
        splitter.setObjectName("vaultSplitter")
        splitter.setHandleWidth(1)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_list_area())
        splitter.addWidget(self._build_detail_panel())
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setSizes([720, 340])
        layout.addWidget(splitter, 1)

    def _build_toolbar(self):
        frame = QFrame()
        frame.setObjectName("commandBar")
        bar = QHBoxLayout(frame)
        bar.setContentsMargins(0, 0, 0, 8)
        bar.setSpacing(8)

        title = QLabel("资料索引")
        title.setObjectName("sectionTitle")
        bar.addWidget(title)

        self.combo_subject = QComboBox()
        self.combo_subject.addItem("科目: 全部", "全部")
        for s in SUBJECT_FOLDERS:
            self.combo_subject.addItem(f"科目: {s}", s)
        self.combo_subject.setMinimumWidth(112)
        self.combo_subject.currentIndexChanged.connect(self.apply_filter)
        bar.addWidget(self.combo_subject)

        self.combo_tag = QComboBox()
        self.combo_tag.setMinimumWidth(120)
        self.combo_tag.currentIndexChanged.connect(self.apply_filter)
        bar.addWidget(self.combo_tag)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("搜索文件名或路径（例如 91届、阅读）…")
        self.search_input.textChanged.connect(self.apply_filter)
        bar.addWidget(self.search_input, 1)

        self.combo_sort = QComboBox()
        for text, key in _SORT_MODES:
            self.combo_sort.addItem(f"排序: {text}", key)
        self.combo_sort.setMinimumWidth(154)
        self.combo_sort.currentIndexChanged.connect(self.apply_filter)
        bar.addWidget(self.combo_sort)

        self.btn_refresh = QPushButton("重建索引")
        self.btn_refresh.setObjectName("iconButton")
        self.btn_refresh.setIcon(icon("refresh"))
        self.btn_refresh.setToolTip("重新比对资料目录；只更新有变化的文件")
        self.btn_refresh.clicked.connect(self.start_scan)
        bar.addWidget(self.btn_refresh)
        return frame

    def _build_progress_row(self):
        """索引进行中的行内反馈（4.4 状态表：加载时要有进度，且不阻塞 UI）。"""
        self._progress_box = QWidget()
        row = QHBoxLayout(self._progress_box)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)

        self.progress = InlineProgress()
        self.progress.setFixedWidth(160)
        row.addWidget(self.progress)

        self.lbl_index = QLabel("")
        self.lbl_index.setObjectName("faint")
        row.addWidget(self.lbl_index, 1)

        self._progress_box.hide()
        return self._progress_box

    def _build_list_area(self):
        self.list_widget = QListWidget()
        self.list_widget.setObjectName("vaultList")
        self._list_delegate = PillListDelegate(self.list_widget)
        self.list_widget.setItemDelegate(self._list_delegate)
        self.list_widget.itemSelectionChanged.connect(self._on_selection_changed)
        self.list_widget.itemDoubleClicked.connect(self._on_double_clicked)
        self.list_widget.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self._show_context_menu)

        self.empty = EmptyState("", "")
        self.empty.action_clicked.connect(self._on_empty_action)

        self.list_stack = QStackedWidget()
        self.list_stack.addWidget(self.list_widget)   # 0
        self.list_stack.addWidget(self.empty)         # 1
        return self.list_stack

    def _build_detail_panel(self):
        """详情侧栏（4.4 信息层级 H4）：元信息 + 标签编辑 + 上次阅读 + 快速操作。"""
        panel = QFrame()
        panel.setObjectName("flatDetailPanel")
        box = QVBoxLayout(panel)
        box.setContentsMargins(16, 12, 16, 16)
        box.setSpacing(8)

        self.lbl_detail_name = QLabel("未选择资料")
        self.lbl_detail_name.setObjectName("sectionTitle")
        self.lbl_detail_name.setWordWrap(True)
        # 资料名多半是韩语，跟单词一样需要能拿走
        box.addWidget(make_copyable(self.lbl_detail_name))

        self.lbl_detail_meta = QLabel("")
        self.lbl_detail_meta.setObjectName("muted")
        self.lbl_detail_meta.setWordWrap(True)
        box.addWidget(self.lbl_detail_meta)

        self.lbl_detail_path = QLabel("")
        self.lbl_detail_path.setObjectName("faint")
        self.lbl_detail_path.setWordWrap(True)
        # 路径要能选中复制：报表、报错、去文件夹里找，全靠它
        box.addWidget(make_copyable(self.lbl_detail_path))

        # ---- 阅读进度 ----
        heading = QLabel("阅读进度")
        heading.setObjectName("sectionTitle")
        box.addSpacing(4)
        box.addWidget(heading)

        page_row = QHBoxLayout()
        page_row.setSpacing(6)
        self.page_input = GhostLineEdit("看到第几页")
        self.page_input.setValidator(QIntValidator(0, 99999, self))
        self.page_input.setFixedWidth(96)
        # 外部程序打开 PDF 时**无法定位到页**（4.4 流程 D 的已知限制），
        # 所以页码由用户自己记：这里存的是"你上次看到哪"，不是"跳到哪"。
        self.page_input.setToolTip("记下你看到第几页；外部打开 PDF 时无法自动跳页，所以由你自己记")
        self.page_input.editingFinished.connect(self._save_page)
        page_row.addWidget(self.page_input)
        self.lbl_pages = QLabel("")
        self.lbl_pages.setObjectName("muted")
        page_row.addWidget(self.lbl_pages)
        page_row.addStretch()
        box.addLayout(page_row)

        self.lbl_page_hint = QLabel("")
        self.lbl_page_hint.setObjectName("faint")
        self.lbl_page_hint.setWordWrap(True)
        box.addWidget(self.lbl_page_hint)

        # ---- 标签 ----
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
        self.tag_input = GhostLineEdit("新标签，如 真题 / 105届")
        self.tag_input.returnPressed.connect(self.add_tag)
        tag_row.addWidget(self.tag_input, 1)
        self.btn_add_tag = QPushButton("添加")
        self.btn_add_tag.setObjectName("iconButton")
        self.btn_add_tag.clicked.connect(lambda: self.add_tag())
        tag_row.addWidget(self.btn_add_tag)
        box.addLayout(tag_row)

        # 已有标签的快捷入口。**一个文件该打的标签，通常是别的文件已经打过的那个**
        # （同一届、同一科目、同一来源），每次手打既慢又会写出"真题"与"真题 "这种
        # 看起来一样、比出来不等的两个标签——这个页面的数据本来就不该靠手工保持一致。
        self.lbl_tag_pool = QLabel("已有标签，点一下加给这份资料")
        self.lbl_tag_pool.setObjectName("faint")
        box.addWidget(self.lbl_tag_pool)

        self.tag_pool = QWidget()
        self.tag_pool_layout = QGridLayout(self.tag_pool)
        self.tag_pool_layout.setContentsMargins(0, 0, 0, 0)
        self.tag_pool_layout.setHorizontalSpacing(6)
        self.tag_pool_layout.setVerticalSpacing(4)
        box.addWidget(self.tag_pool)

        # ---- 上次打开 ----
        self.lbl_usage = QLabel("")
        self.lbl_usage.setObjectName("faint")
        self.lbl_usage.setWordWrap(True)
        box.addSpacing(4)
        box.addWidget(self.lbl_usage)

        box.addStretch()

        # ---- 快速操作 ----
        # 分两行：侧栏宽 340，三个按钮挤一行会顶到边框
        first = QHBoxLayout()
        first.setSpacing(6)
        self.btn_open = QPushButton("打开")
        self.btn_open.setObjectName("iconButton")
        self.btn_open.setIcon(icon("file-text"))
        self.btn_open.clicked.connect(lambda: self.open_path(self._selected))
        first.addWidget(self.btn_open)

        self.btn_reveal = QPushButton("在文件夹中显示")
        self.btn_reveal.setObjectName("iconButton")
        self.btn_reveal.setIcon(icon("folder-open"))
        self.btn_reveal.clicked.connect(lambda: reveal_in_folder(self._selected or ""))
        first.addWidget(self.btn_reveal)
        first.addStretch()
        box.addLayout(first)

        second = QHBoxLayout()
        second.setSpacing(6)
        self.btn_relocate = QPushButton("重新定位")
        self.btn_relocate.setObjectName("iconButton")
        self.btn_relocate.setIcon(icon("refresh"))
        self.btn_relocate.setToolTip("文件被移动或改名后，把索引与标签接到新位置")
        self.btn_relocate.clicked.connect(lambda: self.relocate(self._selected))
        second.addWidget(self.btn_relocate)
        second.addStretch()
        box.addLayout(second)

        scroll = QScrollArea()
        scroll.setObjectName("detailScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(panel)
        scroll.setMinimumWidth(320)
        scroll.setMaximumWidth(400)
        return scroll

    # ==================================================================
    # 索引
    # ==================================================================

    def start_scan(self, *_ignored):
        """手动或启动时触发一次增量扫描。已在扫描中则忽略。"""
        if self._scan_worker is not None:
            return
        root = self.root_dir
        if not root or not os.path.isdir(root):
            # 目录都没有，扫描只会把整个索引标成"缺失"——空状态自己会说清楚原因
            self.reload()
            return

        self.btn_refresh.setEnabled(False)
        self._progress_box.show()
        self.progress.set_value(None)
        self.lbl_index.setText("正在扫描资料目录…")

        worker = ScanWorker(root, self)
        worker.progressed.connect(self._on_scan_progress)
        worker.scanned.connect(self._on_scan_done)
        worker.finished.connect(worker.deleteLater)
        self._scan_worker = worker
        worker.start()

    def _on_scan_progress(self, count):
        self.lbl_index.setText(f"正在索引… 已发现 {count} 个文件")

    def _on_scan_done(self, records, notes):
        worker, self._scan_worker = self._scan_worker, None
        # 被取消的扫描**不是完整扫描**：没走完的那棵树里每个文件都会"看起来不在"，
        # 拿它去标记缺失，等于取消一次索引就毁掉整页状态。
        complete = worker is None or not worker.is_cancelled()

        summary = self.database.apply_material_scan(records, complete=complete)
        self._progress_box.hide()
        self.btn_refresh.setEnabled(True)
        if self._pending_locate:
            # 「在资料库中定位」指向的文件刚才还不在索引里（新加的、或路径变过）。
            # `_render_list` 找不到它就会把选中项清空，所以这里补回来再重建一次。
            self._selected = self._pending_locate
            self._pending_locate = None
        self.reload()
        self._report_scan(summary, notes)
        self.index_updated.emit()

    def _report_scan(self, summary, notes):
        errors = notes.get("errors") or []
        if errors:
            self.banner.show_message(
                "warning",
                f"{len(errors)} 个文件无法读取（其余已完成索引）：{errors[0]}",
            )
        elif summary["added"] or summary["updated"]:
            # 没变化时**不提示**——D6-C 的增量扫描是日常启动路径，
            # 每次都弹一条"索引完成"会变成一个没人看的噪声源
            extra = f" · 标记缺失 {summary['missing']}" if summary["missing"] else ""
            show_toast(
                self.window(),
                f"索引完成：新增 {summary['added']} · 更新 {summary['updated']}{extra}",
            )

    def shutdown(self):
        """窗口关闭时收线程。

        不这么做，`QThread` 会在还在跑的时候被销毁——Qt 会打印
        "Destroyed while thread is still running"，运气不好直接崩在退出路径上。
        """
        worker, self._scan_worker = self._scan_worker, None
        if worker is not None:
            worker.cancel()
            worker.wait(3000)

    def refresh(self):
        """设置页改了资料目录 / 数据被重新导入后调用（MainWindow 接的）。"""
        self.start_scan()

    # ==================================================================
    # 数据 → 界面
    # ==================================================================

    def reload(self, keep=None):
        """重读全量索引并重建列表。`keep` 是要保住的选中项 path。"""
        if keep is not None:
            self._selected = keep
        try:
            self._rows = self.database.library_snapshot()
        except Exception:
            return
        root = self.root_dir
        self._missing_dirs = (
            [name for name in SUBJECT_FOLDERS if not os.path.isdir(os.path.join(root, name))]
            if root
            else []
        )
        self._refresh_tag_combo()
        self.apply_filter()

    def _refresh_tag_combo(self):
        current = self.combo_tag.currentData() if self.combo_tag.count() else None
        self.combo_tag.blockSignals(True)
        self.combo_tag.clear()
        self.combo_tag.addItem("标签: 全部", None)
        for row in self.database.all_material_tags():
            self.combo_tag.addItem(f"标签: #{row['tag']}（{row['n']}）", row["tag"])
        index = self.combo_tag.findData(current)
        self.combo_tag.setCurrentIndex(index if index >= 0 else 0)
        self.combo_tag.blockSignals(False)

    def apply_filter(self, *_ignored):
        subject = self.combo_subject.currentData() or "全部"
        tag = self.combo_tag.currentData()
        keyword = self.search_input.text().strip().lower()

        rows = []
        for row in self._rows:
            if subject != "全部" and row["subject"] != subject:
                continue
            if tag and tag not in row["tags"]:
                continue
            if keyword and keyword not in row["name"].lower() and keyword not in row["path"].lower():
                continue
            rows.append(row)

        rows.sort(key=self._sort_key())
        self._shown = rows
        self._render_list()
        self._update_empty_state()
        self._update_index_label()

    def _sort_key(self):
        mode = self.combo_sort.currentData()
        if mode == "mtime":
            return lambda row: (-(row["mtime"] or 0), row["name"].lower())
        if mode == "size":
            return lambda row: (-(row["size"] or 0), row["name"].lower())
        if mode == "subject":
            return lambda row: (
                SUBJECT_FOLDERS.index(row["subject"]) if row["subject"] in SUBJECT_FOLDERS else 9,
                row["name"].lower(),
            )
        return lambda row: row["name"].lower()

    def _relative_dir(self, path):
        root = self.root_dir
        if not root:
            return ""
        try:
            relative = os.path.relpath(os.path.dirname(path), root)
        except ValueError:
            # 不同盘符之间没有相对路径（资料根在 D:、文件在 E:），退回显示绝对目录
            return os.path.dirname(path)
        return "" if relative == "." else relative

    def _item_text(self, row):
        bits = []
        relative = self._relative_dir(row["path"])
        if relative:
            bits.append(relative)
        if row["tags"]:
            bits.append(" ".join("#" + tag for tag in row["tags"]))
        if row["last_page"]:
            bits.append(
                f"看到第 {row['last_page']} 页"
                + (f" / 共 {row['total_pages']} 页" if row["total_pages"] else "")
            )
        if row["missing"]:
            bits.append("文件已不在原位置")
        return f"{row['name']}\n" + (" · ".join(bits) if bits else "—")

    def _populate_vault_item(self, item, row):
        item.setText(self._item_text(row))
        item.setSizeHint(QSize(0, 52))
        item.setData(PillListDelegate.TITLE_ROLE, row["name"])
        item.setData(PillListDelegate.FAINT_ROLE, bool(row["missing"]))
        pills = []
        relative = self._relative_dir(row["path"])
        if relative:
            pills.append((relative, False))
        for tag in row["tags"]:
            pills.append((f"#{tag}", False))
        if row["last_page"]:
            page_text = f"第 {row['last_page']}" + (
                f" / {row['total_pages']} 页" if row["total_pages"] else " 页"
            )
            pills.append((page_text, True))
        if row["missing"]:
            pills.append(("文件已不在原位置", True))
        if not pills:
            pills.append(("根目录", True))
        item.setData(PillListDelegate.PILL_ROLE, pills)

    def _render_list(self):
        keep = self._selected
        self.list_widget.blockSignals(True)   # 重建期间的选中变化不该去刷详情面板
        self.list_widget.clear()
        for row in self._shown[:MAX_ROWS]:
            item = QListWidgetItem(self._item_text(row))
            item.setIcon(icon(file_icon_name(row["name"])))
            item.setData(Qt.UserRole, row["path"])
            item.setToolTip(row["path"])
            if row["missing"]:
                # 缺失是"东西还在册子上、文件不在原位"——降一档灰，不隐藏（4.4 状态表）
                item.setForeground(QBrush(QColor(TEXT_COLORS["faint"])))
            font = item.font()
            font.setPixelSize(13)
            item.setFont(font)
            self._populate_vault_item(item, row)
            self.list_widget.addItem(item)
        self.list_widget.blockSignals(False)

        # 选中态按 path 找回：列表每次筛选都重建，行号会变
        if keep:
            for index in range(self.list_widget.count()):
                if self.list_widget.item(index).data(Qt.UserRole) == keep:
                    self.list_widget.setCurrentRow(index)   # 信号活着 → 详情面板同步
                    break
            else:
                self._select(None)
        else:
            self._select(None)

    def _update_index_label(self):
        if self._scan_worker is not None:
            return   # 扫描期间由 _on_scan_progress 独占这一行
        stamp = self.database.last_indexed_at()
        bits = [f"上次索引 {stamp[:16].replace('T', ' ')}" if stamp else "尚未建立索引"]
        bits.append(f"共 {len(self._rows)} 份资料")
        if self._filter_summary() != "无筛选":
            bits.append(f"当前筛选：{self._filter_summary()} · 匹配 {len(self._shown)} 条")
        if len(self._shown) > MAX_ROWS:
            bits.append(f"列表只显示前 {MAX_ROWS} 条，缩小范围可看其余")
        self.lbl_index.setText(" · ".join(bits))

    def _filter_summary(self):
        bits = []
        subject = self.combo_subject.currentData() or "全部"
        if subject != "全部":
            bits.append(subject)
        tag = self.combo_tag.currentData()
        if tag:
            bits.append(f"#{tag}")
        keyword = self.search_input.text().strip()
        if keyword:
            bits.append(f"“{keyword}”")
        return " + ".join(bits) if bits else "无筛选"

    # ==================================================================
    # 空状态（4.4 状态表：按"先目录、后文件、再搜索"的顺序判断）
    # ==================================================================

    def _show_empty(self, title, description="", action_text=None, action_icon=None,
                    glyph="folder", handler=None):
        self.empty.set_content(title, description, action_text, action_icon, glyph)
        self._empty_action = handler
        self.list_stack.setCurrentIndex(1)

    def _show_list(self):
        self.list_stack.setCurrentIndex(0)

    def _on_empty_action(self):
        if self._empty_action:
            self._empty_action()

    def _on_banner_action(self):
        if self._banner_action:
            self._banner_action()

    def _update_empty_state(self):
        root = self.root_dir
        if not root:
            self._show_empty(
                "还没有设置资料目录",
                "请到「设置与数据」里选择资料根目录——"
                "它应当含有 写作 / 听力 / 阅读 / 单词 等子文件夹。",
                glyph="alert",
            )
            return

        if not os.path.isdir(root):
            self._show_empty(
                "找不到资料文件夹",
                f"期望的位置：{root}\n"
                "该目录下应有 写作 / 听力 / 阅读 / 单词 等子文件夹。"
                "如果资料已经移动，请到「设置与数据」重新指定。",
                glyph="alert",
            )
            return

        if self._missing_dirs and not self._rows:
            self._show_empty(
                f"缺少子文件夹：{' / '.join(self._missing_dirs)}",
                "在这些文件夹里放上资料后点「重建索引」即可。",
                action_text="重建索引",
                action_icon="refresh",
                glyph="alert",
                handler=self.start_scan,
            )
            return

        if self._scan_worker is not None and not self._rows:
            self._show_empty(
                "正在建立索引…",
                "第一次进入要扫一遍资料目录；之后每次只比对有变化的文件。",
                glyph="folder-open",
            )
            return

        if not self._rows and not self.database.last_indexed_at():
            # 索引还没建过（刚装好、或刚换过资料目录），与"目录里确实没东西"是两回事：
            # 前者要的是"点一下开始扫"，后者要的是"把文件放进去"
            self._show_empty(
                "还没有建立索引",
                f"扫描一次即可列出 {root} 下的资料。之后每次启动只会比对有变化的文件。",
                action_text="开始索引",
                action_icon="refresh",
                glyph="folder-open",
                handler=self.start_scan,
            )
            return

        if not self._rows:
            self._show_empty(
                "这个资料目录下还没有资料",
                "把文件放进 写作 / 听力 / 阅读 / 单词 子文件夹，索引就会扫到它们。",
                action_text="重建索引",
                action_icon="refresh",
                handler=self.start_scan,
            )
            return

        if not self._shown:
            summary = self._filter_summary()
            self._show_empty(
                "没有找到匹配的资料",
                f"当前筛选：{summary}。可以换个关键词，或放宽科目与标签。",
                action_text="清除筛选",
                action_icon="x",
                glyph="search",
                handler=self.clear_filters,
            )
            return

        self._show_list()

    def clear_filters(self):
        self.search_input.clear()
        self.combo_subject.setCurrentIndex(0)
        if self.combo_tag.count():
            self.combo_tag.setCurrentIndex(0)

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
        self._selected = row["path"] if row else None
        enabled = row is not None
        for button in (self.btn_open, self.btn_reveal, self.btn_relocate, self.btn_add_tag):
            button.setEnabled(enabled)

        if row is None:
            self.lbl_detail_name.setText("未选择资料")
            self.lbl_detail_meta.clear()
            self.lbl_detail_path.clear()
            self.lbl_usage.clear()
            self.page_input.clear()
            self.page_input.setEnabled(False)
            self.lbl_pages.clear()
            self.lbl_page_hint.clear()
            self.tag_input.clear()
            self._render_tags([])
            self._render_tag_pool([])
            return

        self.lbl_detail_name.setText(row["name"])
        self.lbl_detail_meta.setText(
            " · ".join(
                part
                for part in (
                    row["subject"],
                    row["ext"].lstrip(".").upper() or "未知类型",
                    human_size(row["size"]),
                    f"改动于 {_stamp(row['mtime'])}" if row["mtime"] else "",
                )
                if part
            )
        )
        self.lbl_detail_path.setText(row["path"] + ("　（文件已不在原位置）" if row["missing"] else ""))
        self.lbl_usage.setText(
            f"上次打开：{_stamp_text(row['last_opened_at'])} · 共打开 {row['open_count']} 次"
            if row["open_count"]
            else "还没有打开过"
        )

        is_pdf = row["ext"] == ".pdf"
        self._probe_page_count(row, is_pdf)
        self.page_input.setEnabled(is_pdf)
        if not is_pdf:
            self.page_input.clear()
            self.lbl_pages.clear()
            self.lbl_page_hint.setText("页码只对 PDF 记录。")
        else:
            self.page_input.setText(str(row["last_page"]) if row["last_page"] else "")
            self.lbl_pages.setText(f"/ 共 {row['total_pages']} 页" if row["total_pages"] else "/ 页数未知")
            self.lbl_page_hint.setText("外部程序打开 PDF 时无法自动跳页，页码由你自己记。")
        self._render_tags(row["tags"])
        self._render_tag_pool(row["tags"])

    def _probe_page_count(self, row, is_pdf):
        """选中 PDF 时补一次总页数。**按需**做，不在索引时批量跑——
        为了一个显示用的分母去解析资料库里每一个 PDF，是把索引成本从读目录项
        抬成解析文件结构。拿不到就退化成"看到第 N 页"，不影响任何功能。"""
        if not is_pdf or row["missing"] or row["total_pages"]:
            return
        if not os.path.exists(row["path"]):
            return
        total = pdf_page_count(row["path"])
        if total:
            self.database.set_reading_progress(row["path"], row["last_page"], total)
            row["total_pages"] = total

    def _render_tags(self, tags):
        while self.tag_layout.count():
            item = self.tag_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # 先摘出父子关系再 deleteLater：Qt 删除是延迟的，
                # 只调 deleteLater 的话旧胶囊会保留在旧位置直到下一轮事件循环
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
        """已有标签的快捷入口，排除这份资料已经有的那些。

        只列**别人用过的**标签：这样"打过的标签"会自然收敛到同一批名字上，
        而不是每份文件都长出一个新写法。没有可选标签、或没选中文件时整块收起。
        """
        while self.tag_pool_layout.count():
            item = self.tag_pool_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

        pool = [row["tag"] for row in self.database.all_material_tags()
                if row["tag"] not in current]
        visible = bool(pool) and self._selected is not None
        self.lbl_tag_pool.setVisible(visible)
        self.tag_pool.setVisible(visible)
        if not visible:
            return

        for index, tag in enumerate(pool):
            chip = QPushButton(f"#{tag}")
            chip.setObjectName("tagChip")
            chip.setCursor(Qt.PointingHandCursor)
            chip.setToolTip(f"把 #{tag} 加给这份资料")
            chip.clicked.connect(lambda _checked=False, value=tag: self.add_tag(value))
            # 一行三个：侧栏只有 340 宽，排成一行会被挤成省略号
            self.tag_pool_layout.addWidget(chip, index // 3, index % 3)

    def _refresh_row_text(self, path):
        """就地更新一行的文字（打开次数、页码这类小变化不必重建整张列表）。"""
        for index in range(self.list_widget.count()):
            item = self.list_widget.item(index)
            if item.data(Qt.UserRole) == path:
                row = self._row_for(path)
                if row is not None:
                    self._populate_vault_item(item, row)
                    # 颜色也要跟着 missing 一起回来，否则"文件找回来了"这一行仍然灰着
                    item.setForeground(
                        QBrush(QColor(
                            TEXT_COLORS["faint"] if row["missing"] else TEXT_COLORS["primary"]
                        ))
                    )
                break

    # ==================================================================
    # 动作
    # ==================================================================

    def locate(self, path):
        """选中某一份资料（P4 的「在资料库中定位」调它）。

        先清掉筛选：目标很可能正被当前筛选挡在外面，切过来却什么都看不到，比不切还困惑。
        """
        path = normalize_path(path)
        self.clear_filters()          # 会触发一次重建，所以 `_selected` 必须在它之后设
        self._selected = path
        if self._row_for(path) is None:
            self._pending_locate = path   # 索引里还没有它，扫一轮，扫完由 _on_scan_done 补选中
            self.start_scan()
        self.reload()

    def _on_double_clicked(self, item):
        path = item.data(Qt.UserRole)
        if file_extension(path) in IMAGE_EXTS:
            self.open_in_snippets.emit(path)
            return
        self.open_path(path)

    def _show_context_menu(self, position):
        item = self.list_widget.itemAt(position)
        if item is None:
            return
        path = item.data(Qt.UserRole)
        menu = QMenu(self)
        action = menu.addAction(icon("file-text"), "打开")
        action.triggered.connect(lambda: self.open_path(path))
        action = menu.addAction(icon("folder-open"), "在文件夹中显示")
        action.triggered.connect(lambda: reveal_in_folder(path))
        action = menu.addAction(icon("clipboard"), "复制路径")
        action.triggered.connect(lambda: QApplication.clipboard().setText(path))
        menu.addSeparator()
        # 文件已经不在了就只剩索引这一行可删——菜单项跟着改名字，不留一个点了没反应的
        # "删除文件"
        action = menu.addAction(
            icon("trash"), "删除文件" if os.path.exists(path) else "从索引中移除"
        )
        action.triggered.connect(lambda: self.delete_path(path))
        menu.exec(self.list_widget.viewport().mapToGlobal(position))

    def delete_path(self, path):
        """删除一份资料：文件送回收站 + 索引行删掉。

        **这是整个工具唯一会改动资料文件的地方**（原则 P6 的唯一例外，见 4.4 的
        `〔v1.9 实施裁定〕`）。三条纪律：

        * 先问再做——"要不要做"和"发生了什么"是两件事，前者挡在动作前是本项目既有做法
          （`vocab_view.delete_list` 与 `settings_view.import_data` 都这么做）；
        * 送**回收站**，不是抹掉：能撤销是这个例外站得住的唯一理由；
        * 只删第一层——标签、阅读进度、碎片标题与说明都留着，文件还原后自动跟回来。
        """
        row = self._row_for(path)
        name = row["name"] if row else os.path.basename(path)
        exists = os.path.exists(path)

        if exists:
            question = (
                f"删除《{name}》？\n\n"
                "文件会被移到「系统回收站」（可以还原），同时从索引里移除。\n"
                "标签与阅读进度会留在数据库里，文件还原后自动重新关联。"
            )
        else:
            question = (
                f"《{name}》已经不在原位置了。\n\n"
                "这一行会从索引里移除。磁盘上没有任何东西会被改动。"
            )
        answer = QMessageBox.question(
            self, "删除文件" if exists else "从索引中移除", question,
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        if exists:
            ok, message = delete_file(path)
            if not ok:
                self.banner.show_message("danger", f"删除失败：{message}")
                return

        self.database.remove_material_row(path)
        if self._selected == path:
            self._selected = None
        self.reload()
        # 碎片页那边这一行也该隐去（它只读索引，等不到下一次扫描就不会更新）
        self.index_updated.emit()
        show_toast(self.window(), "已移到回收站" if exists else "已从索引中移除")

    def open_path(self, path):
        if not path:
            return
        row = self._row_for(path)
        name = row["name"] if row else os.path.basename(path)

        if not os.path.exists(path):
            # 打开时才检测存在性（4.4 状态表）：索引不常驻监看文件系统，
            # 唯一能确知"它不在了"的时刻就是这次尝试
            self.database.mark_material_missing(path)
            if row is not None:
                row["missing"] = 1
            self.banner.show_message(
                "warning",
                f"「{name}」已不在原路径。文件被移动或改名了？重新定位一下，"
                "标签与阅读进度会跟着走。",
                action_text="重新定位",
                action_icon="refresh",
            )
            self._banner_action = lambda: self.relocate(path)
            self.reload(keep=path)
            return

        if not QDesktopServices.openUrl(QUrl.fromLocalFile(path)):
            self.banner.show_message(
                "warning",
                f"系统没有可打开「{row['ext'].lstrip('.') if row else name}」这类文件的程序。",
                action_text="在文件夹中显示",
                action_icon="folder-open",
            )
            self._banner_action = lambda: reveal_in_folder(path)
            return

        self.banner.clear()
        if row is not None and row["missing"]:
            # 文件在原位又出现了（或者被挪回来）——索引里那面旗子要跟着放下，
            # 否则这一行会一直灰着，而灰着的那行看起来就像"功能坏了"
            self.database.mark_material_missing(path, missing=False)
        usage = self.database.note_material_opened(path)
        if row is not None and usage:
            row["open_count"] = usage["open_count"]
            row["last_opened_at"] = usage["last_opened_at"]
            row["missing"] = 0
            self._refresh_row_text(path)
            self.lbl_usage.setText(
                f"上次打开：{_stamp_text(row['last_opened_at'])} · 共打开 {row['open_count']} 次"
            )

    def relocate(self, old_path):
        if not old_path:
            return
        start = os.path.dirname(old_path)
        if not os.path.isdir(start):
            start = self.root_dir or ""
        new_path, _ = QFileDialog.getOpenFileName(self, "重新定位资料文件", start)
        if not new_path:
            return
        if os.path.normcase(new_path) == os.path.normcase(old_path):
            return

        result = self.database.relocate_material(old_path, new_path)
        if result is None:
            self.banner.show_message("warning", "这份资料已经不在索引里了，重建索引即可。")
            self.reload()
            return
        self.banner.clear()
        show_toast(self.window(), f"已重新定位到「{result['name']}」，标签与阅读进度已保留")
        self.reload(keep=result["path"])

    def _save_page(self):
        path = self._selected
        if not path:
            return
        row = self._row_for(path)
        if row is None or row["ext"] != ".pdf":
            return
        text = self.page_input.text().strip()
        page = int(text) if text.isdigit() else 0
        if page == row["last_page"]:
            return
        self.database.set_reading_progress(path, page, row["total_pages"])
        row["last_page"] = page
        self._refresh_row_text(path)

    def add_tag(self, tag=None):
        """加标签。`tag` 为空串/None 时取输入框里的内容（两条入口共用一个方法）。

        `tag=False` 也要当成空——`QPushButton.clicked` 会带一个 bool 参数，
        拿它当标签会把 `False` 当成一个名字写进库。
        """
        path = self._selected
        if not path:
            return
        if not tag:
            tag = self.tag_input.text()
        tag = str(tag).strip()
        if not tag:
            return
        if not self.database.add_material_tag(path, tag):
            return
        self.tag_input.clear()
        self.reload(keep=path)

    def remove_tag(self, tag):
        path = self._selected
        if not path:
            return
        self.database.remove_material_tag(path, tag)
        self.reload(keep=path)

    def filter_by_tag(self, tag):
        index = self.combo_tag.findData(tag)
        if index >= 0:
            self.combo_tag.setCurrentIndex(index)   # 触发 apply_filter
        else:
            self.combo_tag.setCurrentIndex(0)


def _stamp(mtime):
    """Unix 秒 → `2026-10-06 15:40`。"""
    if not mtime:
        return ""
    return datetime.fromtimestamp(int(mtime)).strftime("%Y-%m-%d %H:%M")


def _stamp_text(value):
    """ISO 时间戳 → `2026-10-06 15:40`。空值给出可读的占位。"""
    if not value:
        return "—"
    return str(value)[:16].replace("T", " ")