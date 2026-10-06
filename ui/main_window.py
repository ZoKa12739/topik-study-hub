from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QKeySequence, QShortcut

from core.database import StudyDatabase
from ui.icons import nav_icon
from ui.planner_view import PlannerView
from ui.settings_view import SettingsView


class MainWindow(QMainWindow):
    # 内容区的固定页面顺序。**索引是隐式契约**——
    # 侧边栏导航项通过 currentRowChanged → setCurrentIndex 直接映射到 0~4，
    # P5 设置页不在导航列表里，固定放在最后（索引 5）。
    # 因此 0~4 的顺序不可改动；closeEvent 也依赖它能拿到影子跟读页。
    NAV_PAGES = ("今日学习", "智能单词仓", "影子跟读", "TOPIK 资料库", "知识碎片")
    SETTINGS_INDEX = 5

    def __init__(self):
        super().__init__()
        self.database = StudyDatabase()
        self.setWindowTitle("TOPIK Study Hub")
        self.resize(1180, 760)
        self.setMinimumSize(980, 650)
        self.init_ui()

    def init_ui(self):
        central_widget = QWidget()
        central_widget.setObjectName("pageRoot")
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(240)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(14, 18, 14, 14)
        sidebar_layout.setSpacing(6)

        brand = QLabel("TOPIK\nStudy Hub")
        brand.setObjectName("brandTitle")
        brand.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        sidebar_layout.addWidget(brand)
        subtitle = QLabel("把今天的努力，变成看得见的进步")
        subtitle.setObjectName("brandSubtitle")
        subtitle.setWordWrap(True)
        sidebar_layout.addWidget(subtitle)

        self.sidebar = QListWidget()
        self.sidebar.setObjectName("navigation")
        # 导航项文字较宽时会渲染出水平滚动条（见 DESIGN.md 差异 2）
        self.sidebar.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        sidebar_layout.addWidget(self.sidebar, 1)

        # 底部设置入口（PRODUCT_SPEC 2.1：导航列表之下、页脚之上）。
        # 它不属于五个模块，因此不进导航列表，而是单独一个可选中按钮。
        self.btn_settings = QPushButton("设置与数据")
        self.btn_settings.setObjectName("navSettings")
        self.btn_settings.setIcon(nav_icon("settings"))
        self.btn_settings.setCheckable(True)
        self.btn_settings.setCursor(Qt.PointingHandCursor)
        self.btn_settings.clicked.connect(self.open_settings)
        sidebar_layout.addWidget(self.btn_settings)

        footer = QLabel("本地优先 · 学习记录保存在此设备")
        footer.setObjectName("brandSubtitle")
        footer.setWordWrap(True)
        sidebar_layout.addWidget(footer)

        self.stacked_widget = QStackedWidget()
        main_layout.addWidget(sidebar)
        main_layout.addWidget(self.stacked_widget, 1)

        self.setup_tabs()
        # 导航行 → 内容区。忽略 -1（清空选中，切到设置页时会发生）
        self.sidebar.currentRowChanged.connect(self._on_nav_changed)
        self.sidebar.setCurrentRow(0)
        self._setup_shortcuts()

    def setup_tabs(self):
        from ui.shadowing_view import ShadowingView
        from ui.snippets_view import SnippetsView
        from ui.vault_view import VaultView
        from ui.vocab_view import VocabView

        # 保留强引用：closeEvent 等地方按名字取用，不再依赖"第 2 个是影子跟读"的隐式约定
        self.planner_view = PlannerView(self.database)
        self.vocab_view = VocabView(self.database)
        self.shadowing_view = ShadowingView(self.database)
        self.vault_view = VaultView(self.database)
        self.snippets_view = SnippetsView(self.database)
        self.settings_view = SettingsView(self.database)

        pages = [
            self.planner_view,
            self.vocab_view,
            self.shadowing_view,
            self.vault_view,
            self.snippets_view,
        ]
        icon_names = ("calendar", "book", "headphones", "folder", "scissors")
        for title, widget, icon_name in zip(self.NAV_PAGES, pages, icon_names):
            item = QListWidgetItem(nav_icon(icon_name), title)
            # 行高 30px，对齐 DESIGN.md §3.3 的导航密度
            item.setSizeHint(QSize(0, 30))
            self.sidebar.addItem(item)
            self.stacked_widget.addWidget(widget)

        # P5 设置页：不在导航列表里，固定为最后一页
        self.stacked_widget.addWidget(self.settings_view)
        assert self.stacked_widget.indexOf(self.settings_view) == self.SETTINGS_INDEX

        # P3 ↔ P4 的双向联动（`PRODUCT_SPEC` 4.4 / 4.5 的页面关系）：
        #   资料库双击一张图片 → 碎片页选中它；碎片页「在资料库中定位」→ 资料库选中来源。
        # 两边都只发信号、不直接引用对方，跳页由组合根决定。
        self.vault_view.open_in_snippets.connect(self._show_snippet)
        self.snippets_view.reveal_in_vault.connect(self._show_vault)
        # 资料库扫完一轮 → 碎片页重读清单（它只读索引，自己不扫目录）
        self.vault_view.index_updated.connect(self.snippets_view.refresh)

        # P0 的两条出口（4.1 的流程 B、5.3 的可点片段）：都是"跳到产生这份记录的地方"。
        # 「继续上次」的音频一路回到 P2 的原列表原曲目原位置——那一步是 P2 自己在构造时
        # 就 `restore_playback_state()` 做掉的（`playback_state` 只有一行，就是"当前"），
        # 所以这里只需切页；词表则要指名道姓地选中那一份（`VocabView.select_list`）。
        self.planner_view.open_shadowing.connect(self._show_shadowing)
        self.planner_view.open_vocab.connect(self._show_vocab)

        # 资料目录 / 脚本路径变更 → 让依赖它们的页面重扫
        self.settings_view.paths_changed.connect(self._on_paths_changed)
        # 音频设备变更 → 让 P2 把新设备装到播放器/录音链上（它自己不会跟随系统默认）
        self.settings_view.audio_devices_changed.connect(self.shadowing_view.refresh_devices)
        self.settings_view.data_reloaded.connect(self.refresh_all)

    def _setup_shortcuts(self):
        # PRODUCT_SPEC 2.6：Ctrl+1~5 切模块，Ctrl+, 打开设置
        for index in range(len(self.NAV_PAGES)):
            shortcut = QShortcut(QKeySequence(f"Ctrl+{index + 1}"), self)
            shortcut.activated.connect(lambda i=index: self._goto_page(i))
        QShortcut(QKeySequence("Ctrl+,"), self).activated.connect(self.open_settings)

    def _goto_page(self, index):
        self.sidebar.setCurrentRow(index)

    def _on_nav_changed(self, row):
        if row < 0:
            return
        self.stacked_widget.setCurrentIndex(row)
        self.btn_settings.setChecked(False)

    def open_settings(self, checked=None):
        # checked 参数：QPushButton.clicked 会带一个 bool，这里忽略它
        self.btn_settings.setChecked(True)
        # 清空导航选中（-1 会被 _on_nav_changed 忽略），再切到设置页
        self.sidebar.setCurrentRow(-1)
        self.stacked_widget.setCurrentIndex(self.SETTINGS_INDEX)
        self.settings_view.refresh()

    def _show_snippet(self, path):
        self._goto_page(self.NAV_PAGES.index("知识碎片"))
        self.snippets_view.select_path(path)

    def _show_shadowing(self):
        self._goto_page(self.NAV_PAGES.index("影子跟读"))

    def _show_vocab(self, list_id):
        self._goto_page(self.NAV_PAGES.index("智能单词仓"))
        if list_id and list_id > 0:
            self.vocab_view.select_list(list_id)

    def _show_vault(self, path):
        self._goto_page(self.NAV_PAGES.index("TOPIK 资料库"))
        if path:
            self.vault_view.locate(path)

    def _on_paths_changed(self):
        self.vault_view.refresh()
        self.snippets_view.refresh()

    def refresh_all(self):
        self.planner_view.refresh()
        self.vocab_view.refresh()
        self.shadowing_view.refresh()
        self.vault_view.refresh()
        self.snippets_view.refresh()
        self.settings_view.refresh()

    def focus_today_input(self):
        """P6 向导完成后把焦点交给今日任务输入框（PRODUCT_SPEC 4.7）。"""
        self.sidebar.setCurrentRow(0)
        self.planner_view.task_input.setFocus()

    def closeEvent(self, event):
        # 冲刷还没落库的学习数据，再关库——否则这部分会丢：
        #   * 影子跟读的跟读时长、播放位置、AB 点，以及还在防抖窗口里的原文/译文
        #   * P1 正在编辑、还在 1.5 秒防抖窗口里的笔记
        #   * P4 正在编辑的碎片说明与标题（同一条理由，另一处 1.5 秒防抖）
        self.shadowing_view.flush_study_session()
        self.vocab_view.flush_pending()
        self.snippets_view.flush_pending()
        # 后台线程要收干净：P3 的目录扫描、P2 的 ffmpeg 转换、P4 的缩略图解码。
        # QThread 还在跑就把窗口拆掉，Qt 会打印 "Destroyed while thread is still running"
        self.vault_view.shutdown()
        self.shadowing_view.shutdown()
        self.snippets_view.shutdown()
        self.database.close()
        super().closeEvent(event)