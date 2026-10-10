import sys
from pathlib import Path

from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QScrollBar,
    QSlider,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import QAbstractNativeEventFilter, QEvent, QObject, QSize, Qt, Signal
from PySide6.QtGui import QCursor, QKeySequence, QPixmap, QShortcut

from core.database import StudyDatabase
from ui.icons import icon, nav_icon
from ui.planner_view import PlannerView
from ui.settings_view import SettingsView
from ui.theme import TEXT_COLORS

if sys.platform == "win32":
    import ctypes
    import ctypes.wintypes

    class _MARGINS(ctypes.Structure):
        _fields_ = [
            ("cxLeftWidth", ctypes.c_int),
            ("cxRightWidth", ctypes.c_int),
            ("cyTopHeight", ctypes.c_int),
            ("cyBottomHeight", ctypes.c_int),
        ]


class _WindowControlButton(QPushButton):
    """右上角无边框窗口控制按钮（最小化 / 最大化还原 / 关闭）。"""

    def __init__(self, icon_name, tooltip, is_close=False, parent=None):
        super().__init__(parent)
        self._is_close = is_close
        self.setObjectName("winCloseBtn" if is_close else "winCtrlBtn")
        self.setFixedSize(38, 30)
        self.setIconSize(QSize(18, 18))
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setToolTip(tooltip)
        self.set_icon_name(icon_name)

    def set_icon_name(self, icon_name):
        self._icon_name = icon_name
        self._normal_icon = icon(icon_name, TEXT_COLORS["secondary"], 18)
        hover_color = "#FFFFFF" if self._is_close else TEXT_COLORS["primary"]
        self._hover_icon = icon(icon_name, hover_color, 18)
        self.setIcon(self._normal_icon)

    def enterEvent(self, event):
        self.setIcon(self._hover_icon)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.setIcon(self._normal_icon)
        super().leaveEvent(event)


class _Win32FramelessFilter(QAbstractNativeEventFilter):
    """拦截 WM_NCCALCSIZE 消除系统原生白条，同时保留 WS_THICKFRAME | WS_CAPTION 的最小化/最大化原生过渡动画。"""

    def __init__(self, hwnd):
        super().__init__()
        self._hwnd = int(hwnd)

    def set_hwnd(self, hwnd):
        self._hwnd = int(hwnd)

    def nativeEventFilter(self, eventType, message):
        if sys.platform == "win32" and eventType == b"windows_generic_MSG" and message:
            try:
                msg = ctypes.wintypes.MSG.from_address(int(message))
                if msg.hWnd == self._hwnd and msg.message == 0x0083 and msg.wParam:
                    return True, 0
            except Exception:
                pass
        return False, 0


class _WindowDragFilter(QObject):
    """独立的全局鼠标事件过滤器，把拖拽与双击最大化转发给 MainWindow。"""

    def __init__(self, window):
        super().__init__(window)
        self._window = window

    def eventFilter(self, watched, event):
        win = self._window
        if win is not None and win._handle_drag_mouse_event(watched, event):
            return True
        return super().eventFilter(watched, event)


class NavigationItemProxy:
    """保持对 QListWidgetItem 类似接口的兼容性。"""

    def __init__(self, text, icon):
        self._text = text
        self._icon = icon

    def text(self):
        return self._text

    def icon(self):
        return self._icon


class NavigationWidget(QWidget):
    """侧边栏主导航（支持分组标题、精准左对齐与统一按钮样式）。"""

    currentRowChanged = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(0, 0, 0, 0)
        self.box.setSpacing(2)

        self._buttons = []
        self._current_row = -1

        # 分组 1：学习
        self.lbl_study = QLabel("学习")
        self.lbl_study.setObjectName("navSectionTitleFirst")
        self.box.addWidget(self.lbl_study)

        # 分组 2：资料
        self.lbl_materials = QLabel("资料")
        self.lbl_materials.setObjectName("navSectionTitle")

    def addItem(self, item_or_icon, title=""):
        if hasattr(item_or_icon, "text"):
            title = item_or_icon.text()
            icon_obj = item_or_icon.icon()
        else:
            icon_obj = item_or_icon

        index = len(self._buttons)
        if index == 3:
            self.box.addWidget(self.lbl_materials)

        btn = QPushButton(title)
        btn.setObjectName("navItem")
        btn.setIcon(icon_obj)
        btn.setIconSize(QSize(16, 16))
        btn.setCheckable(True)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFixedHeight(32)
        btn.clicked.connect(lambda _, idx=index: self.setCurrentRow(idx))

        self._buttons.append(btn)
        self.box.addWidget(btn)

    def count(self):
        return len(self._buttons)

    def item(self, index):
        if 0 <= index < len(self._buttons):
            btn = self._buttons[index]
            return NavigationItemProxy(btn.text(), btn.icon())
        return None

    def currentRow(self):
        return self._current_row

    def setCurrentRow(self, index):
        if index == self._current_row:
            if index >= 0 and not self._buttons[index].isChecked():
                self._buttons[index].setChecked(True)
            return
        self._current_row = index
        for i, btn in enumerate(self._buttons):
            btn.setChecked(i == index)
        if index >= 0:
            self.currentRowChanged.emit(index)

    def clearSelection(self):
        self.setCurrentRow(-1)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Down:
            nxt = min(len(self._buttons) - 1, max(0, self._current_row + 1))
            self.setCurrentRow(nxt)
            event.accept()
        elif event.key() == Qt.Key_Up:
            prev = max(0, self._current_row - 1)
            self.setCurrentRow(prev)
            event.accept()
        else:
            super().keyPressEvent(event)


class MainWindow(QMainWindow):
    # 内容区的固定页面顺序。**索引是隐式契约**——
    # 侧边栏导航项通过 currentRowChanged → setCurrentIndex 直接映射到 0~4，
    # P5 设置页不在导航列表里，固定放在最后（索引 5）。
    # 因此 0~4 的顺序不可改动；closeEvent 也依赖它能拿到影子跟读页。
    NAV_PAGES = ("今日学习", "智能单词仓", "影子跟读", "TOPIK 资料库", "知识碎片")
    SETTINGS_INDEX = 5
    RESIZE_MARGIN = 6
    DRAG_BAR_HEIGHT = 36

    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowMinMaxButtonsHint
            | Qt.WindowType.WindowSystemMenuHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self._drag_press_global = None
        self._drag_window_offset = None
        self._dragging_window = False
        self._in_drag_filter = False
        self.database = StudyDatabase()
        self.setWindowTitle("TOPIK Study Hub")
        self.resize(1180, 760)
        self.setMinimumSize(980, 650)
        self.init_ui()

    def init_ui(self):
        central_widget = QWidget()
        central_widget.setObjectName("pageRoot")
        self.setCentralWidget(central_widget)
        root_layout = QVBoxLayout(central_widget)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # 顶部 QQ 式 36px 通栏（左侧品牌图标+应用名，右侧悬浮最小化/最大化/关闭）
        self.top_bar = QFrame()
        self.top_bar.setObjectName("topTitleBar")
        self.top_bar.setFixedHeight(self.DRAG_BAR_HEIGHT)
        top_layout = QHBoxLayout(self.top_bar)
        top_layout.setContentsMargins(12, 0, 12, 0)
        top_layout.setSpacing(0)

        brand_container = QWidget()
        brand_layout = QHBoxLayout(brand_container)
        brand_layout.setContentsMargins(0, 0, 0, 0)
        brand_layout.setSpacing(0)

        icon_box = QWidget()
        icon_box.setFixedWidth(28)
        ib_lay = QHBoxLayout(icon_box)
        ib_lay.setContentsMargins(6, 0, 4, 0)
        ib_lay.setSpacing(0)

        icon_label = QLabel()
        icon_label.setObjectName("brandIcon")
        icon_path = Path(__file__).resolve().parent / "assets" / "app_icon.png"
        if icon_path.exists():
            pm = QPixmap(str(icon_path)).scaled(
                18, 18, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            icon_label.setPixmap(pm)
            icon_label.setFixedSize(18, 18)
        ib_lay.addWidget(icon_label)
        brand_layout.addWidget(icon_box)

        brand_title = QLabel("TOPIK Study Hub")
        brand_title.setObjectName("brandTitle")
        brand_layout.addWidget(brand_title)
        brand_layout.addStretch()

        top_layout.addWidget(brand_container)
        top_layout.addStretch(1)
        root_layout.addWidget(self.top_bar)

        # 下方主工作区：左侧无边框导航栏 + 右侧四周悬浮留缝的 10px 圆角大白岛
        main_layout = QHBoxLayout()
        main_layout.setContentsMargins(0, 8, 8, 8)
        main_layout.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(220)
        self.sidebar_frame = sidebar
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(12, 8, 12, 12)
        sidebar_layout.setSpacing(0)

        self.sidebar = NavigationWidget()
        sidebar_layout.addWidget(self.sidebar)
        sidebar_layout.addStretch(1)

        # 底部 Secondary Navigation 分组（P0：极淡分割线 + 设置与数据）
        sec_divider = QFrame()
        sec_divider.setObjectName("sidebarDivider")
        sec_divider.setFixedHeight(1)
        sidebar_layout.addWidget(sec_divider)
        sidebar_layout.addSpacing(4)

        self.btn_settings = QPushButton("设置与数据")
        self.btn_settings.setObjectName("navSettings")
        self.btn_settings.setIcon(nav_icon("settings"))
        self.btn_settings.setIconSize(QSize(16, 16))
        self.btn_settings.setCheckable(True)
        self.btn_settings.setCursor(Qt.PointingHandCursor)
        self.btn_settings.setFixedHeight(32)
        self.btn_settings.clicked.connect(self.open_settings)
        sidebar_layout.addWidget(self.btn_settings)

        footer = QLabel("本地优先 · 学习记录保存在此设备")
        footer.setObjectName("sidebarFooter")
        footer.setWordWrap(True)
        sidebar_layout.addWidget(footer)

        self.stacked_widget = QStackedWidget()
        self.stacked_widget.setObjectName("mainContentIsland")
        self.stacked_widget.setContentsMargins(1, 1, 1, 1)
        main_layout.addWidget(sidebar)
        main_layout.addWidget(self.stacked_widget, 1)
        root_layout.addLayout(main_layout, 1)

        self.setup_tabs()
        # 导航行 → 内容区。忽略 -1（清空选中，切到设置页时会发生）
        self.sidebar.currentRowChanged.connect(self._on_nav_changed)
        self.sidebar.setCurrentRow(0)
        self._setup_shortcuts()
        self._build_window_controls()
        self._position_window_controls()

    def setup_tabs(self):
        from ui.shadowing_view import ShadowingView
        from ui.snippets_view import SnippetsView
        from ui.vault_view import VaultView
        from ui.vocab_view import VocabView

        # 懒装载登记表：索引 → 构造工厂。**启动只建 P0 一页**，其余首次导航到时才建。
        # 实测（2026-10-10，本机数据规模）：六个视图全建 = ~1.85 s，其中 P0 只占 21 ms，
        # 其余五页（Shadowing 697 / Vault 533 / Settings 376 / Vocab 161 / Snippets ~40 ms）
        # 全耗在用户第一眼根本不看的内容上——规格 §9.1 的"冷启动 ≤ 3 秒"就是被它们吃掉的。
        self._page_factories = {
            1: lambda: VocabView(self.database),
            2: lambda: ShadowingView(self.database),
            3: lambda: VaultView(self.database),
            4: lambda: SnippetsView(self.database),
            self.SETTINGS_INDEX: lambda: SettingsView(self.database),
        }
        # 索引 → 属性名。未构造前属性为 None；closeEvent / refresh_* 一律判空
        self._page_attrs = {
            1: "vocab_view",
            2: "shadowing_view",
            3: "vault_view",
            4: "snippets_view",
            self.SETTINGS_INDEX: "settings_view",
        }
        # P0 是落地页，21 ms，随窗口一起建
        self.planner_view = PlannerView(self.database)
        self.vocab_view = None
        self.shadowing_view = None
        self.vault_view = None
        self.snippets_view = None
        self.settings_view = None

        # 占位页撑住 QStackedWidget 的索引：0~4 是导航页，最后是设置页。
        # 索引是侧边栏 currentRowChanged 直接映射的隐式契约，懒装载不改变它。
        self._placeholders = {}
        icon_names = ("calendar", "book", "headphones", "folder", "scissors")
        for index, (title, icon_name) in enumerate(zip(self.NAV_PAGES, icon_names)):
            item = QListWidgetItem(nav_icon(icon_name), title)
            # 行高 32px，保持紧凑舒适的导航间距
            item.setSizeHint(QSize(0, 32))
            self.sidebar.addItem(item)
            self.stacked_widget.addWidget(
                self.planner_view if index == 0 else self._make_placeholder(index)
            )

        # P5 设置页：不在导航列表里，固定为最后一页（先放占位页，首次 open_settings 换上真身）
        self.stacked_widget.addWidget(self._make_placeholder(self.SETTINGS_INDEX))
        assert (
            self.stacked_widget.indexOf(self._placeholders[self.SETTINGS_INDEX])
            == self.SETTINGS_INDEX
        )

        # P0 的两条出口（4.1 的流程 B、5.3 的可点片段）：都是"跳到产生这份记录的地方"。
        # 「继续上次」的音频一路回到 P2 的原列表原曲目原位置——那一步是 P2 自己在构造时
        # 就 `restore_playback_state()` 做掉的（`playback_state` 只有一行，就是"当前"），
        # 所以这里只需切页；词表则要指名道姓地选中那一份（`VocabView.select_list`）。
        self.planner_view.open_shadowing.connect(self._show_shadowing)
        self.planner_view.open_vocab.connect(self._show_vocab)

        # 其余页的信号在 _wire_page 里接——它们构造得晚，且只接组合根的方法，
        # 不依赖对端是否已存在（见 _wire_page 的注释）。

    def _make_placeholder(self, index):
        """懒装载占位页：样式与页面一致（#panePage 透明底），只是空的。

        用"占位 + 就地替换"而不是等用到再 addWidget：addWidget 追加会把设置页
        推离 SETTINGS_INDEX=5，而那个索引是导航映射的契约。
        """
        page = QWidget()
        page.setObjectName("panePage")
        page.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._placeholders[index] = page
        return page

    def _ensure_page(self, index):
        """按索引取页面；没构造过就地构造，并把占位页换成真身。

        跳转、flush、refresh、close 一律经这里取页——直接摸属性会撞 None。
        首次进入某页会有一次该页的构造耗时（P2 ~0.7 s / P3 ~0.3 s），
        这是把启动成本改成"用到才付"之后的结果，不是回归。
        """
        attr = self._page_attrs.get(index)
        if attr is None:
            # P0 不在登记表里：它随窗口一起构造，永远在场
            return self.planner_view
        view = getattr(self, attr)
        if view is not None:
            return view
        view = self._page_factories[index]()
        placeholder = self._placeholders.pop(index, None)
        if placeholder is not None:
            # 先 remove 再 insert 回原索引：该页此前从未显示过，不可能是当前页，
            # 换页不改变其余页的索引
            self.stacked_widget.removeWidget(placeholder)
            self.stacked_widget.insertWidget(index, view)
            placeholder.deleteLater()
        self._wire_page(index, view)
        setattr(self, attr, view)
        return view

    def _wire_page(self, index, view):
        """页面构造后接信号。**一律接组合根的方法**，不在视图之间直连——
        对端可能晚一步才构造，经根方法读当前属性才不依赖构造顺序。"""
        if index == 3:      # VaultView
            # P3 ↔ P4 的双向联动（`PRODUCT_SPEC` 4.4 / 4.5 的页面关系）：
            #   资料库双击一张图片 → 碎片页选中它；碎片页「在资料库中定位」→ 资料库选中来源。
            # 两边都发信号、不直接引用对方，跳页由组合根决定。
            view.open_in_snippets.connect(self._show_snippet)
            # 资料库扫完一轮 → 碎片页重读清单（它只读索引，自己不扫目录）
            view.index_updated.connect(self._on_vault_index_updated)
        elif index == 4:    # SnippetsView
            view.reveal_in_vault.connect(self._show_vault)
        elif index == self.SETTINGS_INDEX:  # SettingsView
            # 资料目录 / 脚本路径变更 → 让依赖它们的页面重扫
            view.paths_changed.connect(self._on_paths_changed)
            # 音频设备变更 → 让 P2 和 P1 把新设备装到播放器/录音链上（它自己不会跟随系统默认）
            view.audio_devices_changed.connect(self._on_audio_devices_changed)
            # 数据被重新导入 → 各页重读
            view.data_reloaded.connect(self.refresh_all)

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
        # 首次进入某页才构造它（懒装载），再切过去
        self._ensure_page(row)
        self.stacked_widget.setCurrentIndex(row)
        self.btn_settings.setChecked(False)

    def open_settings(self, checked=None):
        # checked 参数：QPushButton.clicked 会带一个 bool，这里忽略它
        self.btn_settings.setChecked(True)
        # 设置页懒装载：第一次打开才构造
        self._ensure_page(self.SETTINGS_INDEX)
        # 清空导航选中（-1 会被 _on_nav_changed 忽略），再切到设置页
        self.sidebar.setCurrentRow(-1)
        self.stacked_widget.setCurrentIndex(self.SETTINGS_INDEX)
        self.settings_view.refresh()

    def _show_snippet(self, path):
        self._goto_page(self.NAV_PAGES.index("知识碎片"))
        self._ensure_page(self.NAV_PAGES.index("知识碎片")).select_path(path)

    def _show_shadowing(self):
        self._goto_page(self.NAV_PAGES.index("影子跟读"))

    def _show_vocab(self, list_id):
        index = self.NAV_PAGES.index("智能单词仓")
        self._goto_page(index)
        if list_id and list_id > 0:
            self._ensure_page(index).select_list(list_id)

    def _show_vault(self, path):
        index = self.NAV_PAGES.index("TOPIK 资料库")
        self._goto_page(index)
        if path:
            self._ensure_page(index).locate(path)

    def _on_vault_index_updated(self):
        # 资料库扫完一轮 → 碎片页重读清单（P4 只读索引，自己不扫目录）
        if self.snippets_view is not None:
            self.snippets_view.refresh()

    def _on_audio_devices_changed(self):
        # P2 和 P1 把新设备装到播放器/录音链上（它们自己不会跟随系统默认）
        if self.shadowing_view is not None:
            self.shadowing_view.refresh_devices()
        if self.vocab_view is not None:
            self.vocab_view.refresh_audio_device()

    def _on_paths_changed(self):
        # 资料目录变更 → 重扫依赖它的页面。没构造过的页不刷：
        # 它下次构造时读的就是新值，白刷一遍只是把成本提前付掉
        if self.vault_view is not None:
            self.vault_view.refresh()
        if self.snippets_view is not None:
            self.snippets_view.refresh()

    def refresh_all(self):
        # 数据被重新导入后各页重读。没构造过的页同理：构造时自然读到新数据
        self.planner_view.refresh()
        if self.vocab_view is not None:
            self.vocab_view.refresh()
        if self.shadowing_view is not None:
            self.shadowing_view.refresh()
        if self.vault_view is not None:
            self.vault_view.refresh()
        if self.snippets_view is not None:
            self.snippets_view.refresh()
        if self.settings_view is not None:
            self.settings_view.refresh()

    def focus_today_input(self):
        """P6 向导完成后把焦点交给今日任务输入框（PRODUCT_SPEC 4.7）。"""
        self.sidebar.setCurrentRow(0)
        self.planner_view.task_input.setFocus()

    # ---------------------------------------------------------------- 无边框窗口控制与原生交互

    def _build_window_controls(self):
        self.window_controls = QWidget(self)
        self.window_controls.setObjectName("windowControls")
        lay = QHBoxLayout(self.window_controls)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)

        self.btn_min = _WindowControlButton("window-minimize", "最小化", parent=self.window_controls)
        self.btn_min.clicked.connect(self._minimize_window)
        lay.addWidget(self.btn_min)

        self.btn_max = _WindowControlButton("window-maximize", "最大化", parent=self.window_controls)
        self.btn_max.clicked.connect(self._toggle_max_restore)
        lay.addWidget(self.btn_max)

        self.btn_close = _WindowControlButton("window-close", "关闭", is_close=True, parent=self.window_controls)
        self.btn_close.clicked.connect(self.close)
        lay.addWidget(self.btn_close)

        self.window_controls.adjustSize()
        self._sync_window_state_button()

    def _is_window_maximized(self):
        """实时查询窗口是否处于最大化状态。

        在 Windows 下直接读 `user32.IsZoomed(hwnd)`：当通过 `WM_SYSCOMMAND` 或系统贴边触发
        最大化/还原时，`QWindow.windowStateChanged` 发出那一刻 `QWidget.isMaximized()` 尚未
        消费 `QEvent.WindowStateChange`（仍停留在上一次的旧值），直接读 `self.isMaximized()`
        会导致图标、圆角和边距全部滞后一拍（甚至反相）。
        """
        if sys.platform == "win32" and QApplication.platformName() == "windows":
            try:
                hwnd = int(self.winId())
                if hwnd:
                    return bool(ctypes.windll.user32.IsZoomed(hwnd))
            except Exception:
                pass
        win = self.windowHandle()
        if win is not None:
            return bool(win.windowState() & Qt.WindowState.WindowMaximized)
        return self.isMaximized()

    def _maximized_frame_margins(self):
        """最大化时 Windows 为 WS_THICKFRAME 窗口外扩的隐形边框逻辑像素（常态为 (0, 0)）。"""
        if sys.platform != "win32" or QApplication.platformName() != "windows":
            return 0, 0
        if not self._is_window_maximized() or self.isFullScreen():
            return 0, 0
        try:
            hwnd = int(self.winId())
            if not hwnd:
                return 0, 0
            user32 = ctypes.windll.user32
            dpi = user32.GetDpiForWindow(hwnd) or 96
            # SM_CXSIZEFRAME = 32, SM_CYSIZEFRAME = 33, SM_CXPADDEDBORDER = 92
            fx_px = user32.GetSystemMetricsForDpi(32, dpi) + user32.GetSystemMetricsForDpi(92, dpi)
            fy_px = user32.GetSystemMetricsForDpi(33, dpi) + user32.GetSystemMetricsForDpi(92, dpi)
            dpr = float(self.devicePixelRatioF() or 1.0)
            return int(round(fx_px / dpr)), int(round(fy_px / dpr))
        except Exception:
            return 8, 8

    def _sync_maximized_margins(self):
        cw = self.centralWidget()
        if cw is None or cw.layout() is None:
            return
        mx, my = self._maximized_frame_margins()
        cw.layout().setContentsMargins(mx, my, mx, my)

    def _position_window_controls(self):
        if not hasattr(self, "window_controls") or self.window_controls is None:
            return
        self.window_controls.adjustSize()
        mx, my = self._maximized_frame_margins()
        x = max(0, self.width() - self.window_controls.width() - 8 - mx)
        self.window_controls.move(x, 4 + my)
        self.window_controls.raise_()

    def _minimize_window(self):
        if sys.platform == "win32" and QApplication.platformName() == "windows":
            try:
                self._apply_win32_window_effects()
                hwnd = int(self.winId())
                if hwnd:
                    user32 = ctypes.windll.user32
                    user32.ReleaseCapture()
                    # WM_SYSCOMMAND (0x0112), SC_MINIMIZE (0xF020)：触发 Windows 原生最小化缩放动画
                    user32.SendMessageW(hwnd, 0x0112, 0xF020, 0)
                    return
            except Exception:
                pass
        self.showMinimized()

    def _toggle_max_restore(self):
        if sys.platform == "win32" and QApplication.platformName() == "windows":
            try:
                self._apply_win32_window_effects()
                hwnd = int(self.winId())
                if hwnd:
                    user32 = ctypes.windll.user32
                    # 双击事件发生在鼠标第二次按下（WM_LBUTTONDBLCLK）期间，此时 Qt 持有 SetCapture；
                    # 若不先 ReleaseCapture()，Windows 会直接忽略 SC_MAXIMIZE / SC_RESTORE。
                    user32.ReleaseCapture()
                    # WM_SYSCOMMAND (0x0112): SC_RESTORE = 0xF120, SC_MAXIMIZE = 0xF030
                    cmd = 0xF120 if self._is_window_maximized() else 0xF030
                    user32.SendMessageW(hwnd, 0x0112, cmd, 0)
                    self._apply_win32_window_effects()
                    self._sync_window_state_button()
                    self._sync_maximized_margins()
                    self._position_window_controls()
                    return
            except Exception:
                pass
        if self._is_window_maximized():
            self.showNormal()
        else:
            self.showMaximized()
        self._apply_win32_window_effects()
        self._sync_window_state_button()
        self._sync_maximized_margins()
        self._position_window_controls()

    def _sync_window_state_button(self):
        if not hasattr(self, "btn_max") or self.btn_max is None:
            return
        if self._is_window_maximized():
            self.btn_max.set_icon_name("window-restore")
            self.btn_max.setToolTip("向下还原")
        else:
            self.btn_max.set_icon_name("window-maximize")
            self.btn_max.setToolTip("最大化")

    def _apply_win32_window_effects(self):
        if sys.platform != "win32" or QApplication.platformName() != "windows":
            return
        try:
            hwnd = int(self.winId())
            if not hwnd:
                return
            app = QApplication.instance()
            if app is not None:
                if getattr(self, "_win32_filter", None) is None:
                    self._win32_filter = _Win32FramelessFilter(hwnd)
                    app.installNativeEventFilter(self._win32_filter)
                else:
                    self._win32_filter.set_hwnd(hwnd)

            user32 = ctypes.windll.user32
            # 补齐 WS_THICKFRAME | WS_CAPTION | WS_MAXIMIZEBOX | WS_MINIMIZEBOX | WS_SYSMENU，
            # 让 DWM 启用原生最小化/最大化缩放过渡动画，同时由 _Win32FramelessFilter 拦截 WM_NCCALCSIZE 消除白条
            style = user32.GetWindowLongPtrW(hwnd, -16)
            needed = 0x00040000 | 0x00C00000 | 0x00010000 | 0x00020000 | 0x00080000
            if (style & needed) != needed:
                user32.SetWindowLongPtrW(hwnd, -16, style | needed)
                user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0037)

            margins = _MARGINS(0, 0, 0, 1)
            ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(
                ctypes.wintypes.HWND(hwnd), ctypes.byref(margins)
            )
            # DWMWA_WINDOW_CORNER_PREFERENCE = 33; DWMWCP_ROUND = 2, DWMWCP_DONOTROUND = 1
            corner_pref = ctypes.c_int(1 if (self._is_window_maximized() or self.isFullScreen()) else 2)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                ctypes.wintypes.HWND(hwnd),
                33,
                ctypes.byref(corner_pref),
                ctypes.sizeof(corner_pref),
            )
        except Exception:
            pass

    def showEvent(self, event):
        super().showEvent(event)
        app = QApplication.instance()
        if app is not None:
            if getattr(self, "_drag_filter", None) is None:
                self._drag_filter = _WindowDragFilter(self)
            app.installEventFilter(self._drag_filter)
        win = self.windowHandle()
        if win is not None and not getattr(self, "_win_state_connected", False):
            win.windowStateChanged.connect(self._on_window_state_changed)
            self._win_state_connected = True
        self._apply_win32_window_effects()
        self._sync_window_state_button()
        self._sync_maximized_margins()
        self._position_window_controls()

    def hideEvent(self, event):
        self._clear_resize_cursor()
        self._remove_drag_event_filter()
        super().hideEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_maximized_margins()
        self._position_window_controls()

    def _on_window_state_changed(self, _state=None):
        self._clear_resize_cursor()
        self._sync_window_state_button()
        self._apply_win32_window_effects()
        self._sync_maximized_margins()
        self._position_window_controls()

    def _edge_at(self, local_pos):
        """返回窗口四周 6px 边缘对应的 Qt.Edge 组合与光标形状；非边缘或最大化时返回 (None, None)。"""
        if self._is_window_maximized() or self.isFullScreen():
            return None, None
        x, y = local_pos.x(), local_pos.y()
        w, h = self.width(), self.height()
        m = self.RESIZE_MARGIN
        if not (0 <= x <= w and 0 <= y <= h):
            return None, None
        left = x < m
        right = x >= w - m
        top = y < m
        bottom = y >= h - m
        if top and left:
            return Qt.Edge.TopEdge | Qt.Edge.LeftEdge, Qt.CursorShape.SizeFDiagCursor
        if bottom and right:
            return Qt.Edge.BottomEdge | Qt.Edge.RightEdge, Qt.CursorShape.SizeFDiagCursor
        if top and right:
            return Qt.Edge.TopEdge | Qt.Edge.RightEdge, Qt.CursorShape.SizeBDiagCursor
        if bottom and left:
            return Qt.Edge.BottomEdge | Qt.Edge.LeftEdge, Qt.CursorShape.SizeBDiagCursor
        if left:
            return Qt.Edge.LeftEdge, Qt.CursorShape.SizeHorCursor
        if right:
            return Qt.Edge.RightEdge, Qt.CursorShape.SizeHorCursor
        if top:
            return Qt.Edge.TopEdge, Qt.CursorShape.SizeVerCursor
        if bottom:
            return Qt.Edge.BottomEdge, Qt.CursorShape.SizeVerCursor
        return None, None

    def _update_resize_cursor(self, cursor_shape):
        current = getattr(self, "_resize_cursor_shape", None)
        if cursor_shape is None:
            if current is not None:
                QApplication.restoreOverrideCursor()
                self._resize_cursor_shape = None
        else:
            if current is None:
                QApplication.setOverrideCursor(cursor_shape)
                self._resize_cursor_shape = cursor_shape
            elif current != cursor_shape:
                QApplication.changeOverrideCursor(cursor_shape)
                self._resize_cursor_shape = cursor_shape

    def _clear_resize_cursor(self):
        self._update_resize_cursor(None)

    def _is_drag_region(self, global_pos):
        """判定全局坐标是否处于可拖拽/双击最大化的空白热区（顶部 38px 通栏 + 侧边栏非交互空白处）。"""
        local_pos = self.mapFromGlobal(global_pos)
        x, y = local_pos.x(), local_pos.y()
        w, h = self.width(), self.height()
        if not (0 <= x < w and 0 <= y < h):
            return False
        edges, _ = self._edge_at(local_pos)
        if edges is not None:
            return False

        sidebar_w = self.sidebar_frame.width() if hasattr(self, "sidebar_frame") else 228
        in_top_bar = y <= self.DRAG_BAR_HEIGHT
        in_sidebar = x < sidebar_w
        if not (in_top_bar or in_sidebar):
            return False

        target = self.childAt(local_pos)
        cur = target
        interactive_types = (
            QAbstractButton,
            QLineEdit,
            QTextEdit,
            QPlainTextEdit,
            QComboBox,
            QAbstractSpinBox,
            QSlider,
            QScrollBar,
            QAbstractItemView,
        )
        while cur is not None and cur is not self:
            if cur is getattr(self, "window_controls", None):
                return False
            if isinstance(cur, interactive_types):
                return False
            cur = cur.parentWidget()
        return True

    def _handle_drag_mouse_event(self, watched, event):
        if getattr(self, "_in_drag_filter", False):
            return False
        self._in_drag_filter = True
        try:
            if event is not None:
                etype = event.type()
                if etype in (
                    QEvent.Type.HoverMove,
                    QEvent.Type.MouseButtonDblClick,
                    QEvent.Type.MouseButtonPress,
                    QEvent.Type.MouseMove,
                    QEvent.Type.MouseButtonRelease,
                    QEvent.Type.Leave,
                ) and self.isVisible():
                    if etype == QEvent.Type.HoverMove and watched is self:
                        if QApplication.mouseButtons() == Qt.MouseButton.NoButton:
                            _, cursor_shape = self._edge_at(event.position().toPoint())
                            self._update_resize_cursor(cursor_shape)
                        return False
                    if etype == QEvent.Type.Leave and watched is self:
                        self._clear_resize_cursor()
                        return False
                    if isinstance(watched, QWidget) and watched.window() is self:
                        if etype == QEvent.Type.MouseButtonDblClick:
                            if event.button() == Qt.MouseButton.LeftButton:
                                gpos = event.globalPosition().toPoint()
                                if self._is_drag_region(gpos):
                                    self._drag_press_global = None
                                    self._dragging_window = False
                                    self._toggle_max_restore()
                                    return True
                        elif etype == QEvent.Type.MouseButtonPress:
                            if event.button() == Qt.MouseButton.LeftButton:
                                gpos = event.globalPosition().toPoint()
                                local_pos = self.mapFromGlobal(gpos)
                                edges, _ = self._edge_at(local_pos)
                                if edges is not None:
                                    self._clear_resize_cursor()
                                    win = self.windowHandle()
                                    if win is not None and win.startSystemResize(edges):
                                        return True
                                if self._is_drag_region(gpos):
                                    self._drag_press_global = gpos
                                    self._drag_window_offset = gpos - self.frameGeometry().topLeft()
                                    self._dragging_window = False
                        elif etype == QEvent.Type.MouseMove:
                            gpos = event.globalPosition().toPoint()
                            if event.buttons() == Qt.MouseButton.NoButton:
                                _, cursor_shape = self._edge_at(self.mapFromGlobal(gpos))
                                self._update_resize_cursor(cursor_shape)
                            elif self._drag_press_global is not None:
                                if not (event.buttons() & Qt.MouseButton.LeftButton):
                                    self._drag_press_global = None
                                    self._dragging_window = False
                                else:
                                    if (
                                        not self._dragging_window
                                        and (gpos - self._drag_press_global).manhattanLength() >= 4
                                    ):
                                        self._dragging_window = True
                                        self._clear_resize_cursor()
                                        win = self.windowHandle()
                                        # 优先交给系统级移动循环（原生支持 Win11 贴边分屏 Aero Snap 与多屏 DPI）
                                        if win is not None and win.startSystemMove():
                                            self._drag_press_global = None
                                            self._dragging_window = False
                                            return True
                                    if self._dragging_window and self._drag_window_offset is not None:
                                        if self.isMaximized():
                                            self.showNormal()
                                            self._sync_window_state_button()
                                            self._drag_window_offset = QCursor.pos() - self.frameGeometry().topLeft()
                                        self.move(gpos - self._drag_window_offset)
                                        return True
                        elif etype == QEvent.Type.MouseButtonRelease:
                            self._drag_press_global = None
                            self._dragging_window = False
        except Exception:
            pass
        finally:
            self._in_drag_filter = False
        return False

    def _remove_drag_event_filter(self, *_args):
        self._clear_resize_cursor()
        try:
            app = QApplication.instance()
            filt = getattr(self, "_drag_filter", None)
            if app is not None and filt is not None:
                app.removeEventFilter(filt)
        except RuntimeError:
            pass

    def closeEvent(self, event):
        self._remove_drag_event_filter()
        try:
            app = QApplication.instance()
            win32_filt = getattr(self, "_win32_filter", None)
            if app is not None and win32_filt is not None:
                app.removeNativeEventFilter(win32_filt)
                self._win32_filter = None
        except RuntimeError:
            pass
        # 冲刷还没落库的学习数据，再关库——否则这部分会丢：
        #   * 影子跟读的跟读时长、播放位置、AB 点，以及还在防抖窗口里的原文/译文
        #   * P1 正在编辑、还在 1.5 秒防抖窗口里的笔记
        #   * P4 正在编辑的碎片说明与标题（同一条理由，另一处 1.5 秒防抖）
        # 懒装载的页没构造过 = 从未被打开 = 没有什么待冲刷、没有线程在跑，直接跳过
        if self.shadowing_view is not None:
            self.shadowing_view.flush_study_session()
        if self.vocab_view is not None:
            self.vocab_view.flush_pending()
        if self.snippets_view is not None:
            self.snippets_view.flush_pending()
        # 后台线程要收干净：P1/P5 的 TTS 合成、P3 的目录扫描、P2 的 ffmpeg 转换、P4 的缩略图解码。
        # QThread 还在跑就把窗口拆掉，Qt 会打印 "Destroyed while thread is still running"
        if self.vocab_view is not None:
            self.vocab_view.shutdown()
        if self.settings_view is not None:
            self.settings_view.shutdown()
        if self.vault_view is not None:
            self.vault_view.shutdown()
        if self.shadowing_view is not None:
            self.shadowing_view.shutdown()
        if self.snippets_view is not None:
            self.snippets_view.shutdown()
        self.database.close()
        super().closeEvent(event)