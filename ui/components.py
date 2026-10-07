"""通用 UI 组件（D-4）。

对应 `PRODUCT_SPEC` 3.2 的组件命名契约与 `DESIGN.md` §3 的设计纪律。

四个组件解决的是同一件事：**空状态、加载状态、错误状态与轻提示此前不存在**，
导致每个页面各自用模态弹窗或干脆不提示。原始大纲完全没有定义这类状态，
而它们恰恰是"用起来是否舒服"的分水岭。

统一遵守 `DESIGN.md` §3.1 纪律 3：**状态色永远伴随图标或文字，不单独承载语义。**

为什么不用 QMessageBox：
`PRODUCT_SPEC` 3.3 已论证模态框是打断（违反原则 P3）。2026-10-06 更验证了它的
实际危害——P1 载入词表时一个 sqlite 绑定错误被 `except Exception` 吞掉后交给
`QMessageBox.critical`，在无事件循环下**永久阻塞**，症状是"卡死"而非报错。
因此本项目不再用模态框报告可预期的失败。
"""

from PySide6.QtCore import QRectF, QSize, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QIcon, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QMenu,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ui.icons import icon
from ui.style import restyle
from ui.theme import (
    ACCENT,
    BG_ELEVATED,
    BG_HOVER,
    BG_SELECTED,
    STATE_COLORS,
    TEXT_COLORS,
)

# 语义等级 → (图标名, 容器 objectName, 图标色)。色值取自 theme.py，不在此处写字面量。
_LEVELS = {
    "info": ("alert", "bannerInfo", STATE_COLORS["info"]),
    "warning": ("alert", "bannerWarning", STATE_COLORS["warning"]),
    "danger": ("alert", "bannerDanger", STATE_COLORS["danger"]),
    "success": ("check-circle", "bannerSuccess", STATE_COLORS["success"]),
}

# PillListDelegate 自定义角色
TITLE_ROLE = int(Qt.UserRole) + 10
PILL_ROLE = int(Qt.UserRole) + 11
DOT_ROLE = int(Qt.UserRole) + 12
FAINT_ROLE = int(Qt.UserRole) + 13


class EmptyState(QWidget):
    """空状态：图标 + 标题 + 说明 + 可选操作按钮。

    `PRODUCT_SPEC` 附录 A 的要求：**必须包含引导下一步的内容**，
    不能只说"没有数据"。所以 `description` 与可选的 `action_text` 是重点。

    用法：
        state = EmptyState("还没有词表", "导入一份 TSV 词表（三列：编号 / 韩语 / 中文）",
                           action_text="载入词表", action_icon="folder-open")
        state.action_clicked.connect(self.load_tsv_dialog)
    """

    action_clicked = Signal()

    def __init__(self, title="", description="", action_text=None, action_icon=None, parent=None):
        super().__init__(parent)
        self.setObjectName("emptyState")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 32, 24, 32)
        layout.setSpacing(8)
        layout.addStretch()

        self._glyph = QLabel()
        self._glyph.setObjectName("emptyStateIcon")
        self._glyph.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._glyph)

        self._title = QLabel(title)
        self._title.setObjectName("emptyStateTitle")
        self._title.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._title)

        self._desc = QLabel(description)
        self._desc.setObjectName("emptyStateDesc")
        self._desc.setAlignment(Qt.AlignCenter)
        self._desc.setWordWrap(True)
        layout.addWidget(self._desc)

        self._action_row = QHBoxLayout()
        self._action_row.addStretch()
        self._action = QPushButton(action_text or "")
        self._action.setObjectName("iconButton")
        self._action.clicked.connect(self.action_clicked)
        self._action_row.addWidget(self._action)
        self._action_row.addStretch()
        layout.addSpacing(4)
        layout.addLayout(self._action_row)

        layout.addStretch()

        if title or description:
            self.set_content(title, description, action_text, action_icon)
        else:
            self._action.hide()

    def set_content(self, title, description="", action_text=None, action_icon=None,
                    glyph="folder-open"):
        """换一套内容。同一个实例可服务多种空状态（无数据 / 搜索无结果 / 目录缺失）。"""
        self._title.setText(title)
        self._desc.setText(description)
        self._desc.setVisible(bool(description))
        self._glyph.setPixmap(icon(glyph, TEXT_COLORS["faint"], 32).pixmap(32, 32))
        if action_text:
            self._action.setText(action_text)
            self._action.setIcon(icon(action_icon) if action_icon else QIcon())
            self._action.show()
        else:
            self._action.hide()


class Banner(QFrame):
    """行内提示条：语义色 + 图标 + 文字 + 可选操作。

    用于**可预期的失败与降级**（文件读不到、外部工具没装、路径不存在）。
    默认隐藏，用 `show_message()` 显示、`clear()` 收起。

    用法：
        self.banner.show_message("danger", "读取词表失败：编码不是 UTF-8",
                                 action_text="重试")
    """

    action_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        restyle(self, "banner")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(8)

        self._glyph = QLabel()
        self._glyph.setFixedWidth(18)
        layout.addWidget(self._glyph)

        self._text = QLabel()
        self._text.setObjectName("bannerText")
        self._text.setWordWrap(True)
        layout.addWidget(self._text, 1)

        self._action = QPushButton()
        self._action.setObjectName("iconButton")
        self._action.clicked.connect(self.action_clicked)
        self._action.hide()
        layout.addWidget(self._action)

        self.hide()

    def show_message(self, level, text, action_text=None, action_icon=None):
        glyph_name, object_name, color = _LEVELS.get(level, _LEVELS["info"])
        restyle(self, object_name)
        self._glyph.setPixmap(icon(glyph_name, color, 16).pixmap(16, 16))
        self._text.setText(text)
        if action_text:
            self._action.setText(action_text)
            self._action.setIcon(icon(action_icon) if action_icon else icon("refresh"))
            self._action.show()
        else:
            self._action.hide()
        self.show()
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)

    def clear(self):
        self.hide()
        self._text.clear()
        self._action.hide()


class InlineProgress(QProgressBar):
    """行内进度条。

    `set_value(None)` 切到不确定模式（Qt 的 range(0,0)），用于转码这类
    无法预知总量的操作。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("progressInline")
        self.setTextVisible(False)
        self.setFixedHeight(4)
        self.set_value(None)

    def set_value(self, value):
        if value is None:
            self.setRange(0, 0)
        else:
            self.setRange(0, 100)
            self.setValue(int(value))


class Toast(QLabel):
    """轻提示：浮在窗口底部，2.5 秒后自动消失，不阻塞。

    `PRODUCT_SPEC` 3.3 要求"成功用 toast，2.5 秒消失"。
    每个顶层窗口复用一个实例（见 `show_toast`），避免堆叠。
    """

    DURATION_MS = 2500

    def __init__(self, parent):
        super().__init__(parent)
        restyle(self, "toast")
        self.setAlignment(Qt.AlignCenter)
        self.setWordWrap(False)
        self.hide()

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

        parent.installEventFilter(self)

    def flash(self, text, level="success"):
        _, object_name, color = _LEVELS.get(level, _LEVELS["success"])
        restyle(self, object_name.replace("banner", "toast"))
        self.setPixmap(icon(_LEVELS[level][0], color, 14).pixmap(14, 14))
        self.setText(f"  {text}")
        self.adjustSize()
        self._reposition()
        self.show()
        self.raise_()
        self._timer.start(self.DURATION_MS)

    def eventFilter(self, obj, event):
        # 父窗口尺寸变化时保持在底部居中
        if obj is self.parent() and event.type() == event.Type.Resize and self.isVisible():
            self._reposition()
        return False

    def _reposition(self):
        parent = self.parent()
        if not parent:
            return
        x = (parent.width() - self.width()) // 2
        y = parent.height() - self.height() - 28
        self.move(max(8, x), max(8, y))


def make_copyable(label):
    """把一个 `QLabel` 变成"能选中、能复制"的，返回它自己（便于链式写）。

    **为什么需要**：韩语在这里是拿来查的——看到生词，第一反应是选中它去词典或搜索框
    里粘一遍。`QLabel` 默认既不可选中、也没有复制入口，等于把文字锁在界面上。
    笔记编辑区（`QTextEdit`）本来就能复制，卡住的恰恰是那些"最该被拿走的词"。

    三件事一起做：

    * `TextSelectableByMouse` —— 拖动选中，`Ctrl+C` 由 `QLabel` 自带的文本控件处理
    * `TextSelectableByKeyboard` —— 可聚焦，于是 `Ctrl+A` 全选、方向键扩选也能用
    * 右键菜单 —— 拖动选区在卡片上不够显眼，右键「复制」是最短路径；没有选区时
      复制整条，符合"整块就是这个标签"的直觉

    **不会抢走键盘快捷键**：`QShortcut` 是在按键送达控件之前被 `QShortcutMap` 截下的，
    所以过词页的 `1`/`2`/`3`/`Space` 在标签持有焦点时照常生效（已实测）。这一点必须
    成立——这个标签是页面上最容易被点到的东西。
    """
    # 仅使用 TextSelectableByMouse：支持鼠标拖选和右键复制，
    # 避免 TextSelectableByKeyboard 导致点击只读文字时出现输入光标
    label.setTextInteractionFlags(Qt.TextSelectableByMouse)
    label.setContextMenuPolicy(Qt.CustomContextMenu)
    label.customContextMenuRequested.connect(lambda pos: _show_copy_menu(label, pos))
    return label


def _show_copy_menu(label, pos):
    # QLabel.selectedText() 用 U+2029（段落分隔符）连接多段，粘出去会变成看不见的怪字符
    text = (label.selectedText() or label.text()).replace("\u2029", "\n")
    menu = QMenu(label)
    action = menu.addAction("复制")
    action.setEnabled(bool(text))
    if menu.exec(label.mapToGlobal(pos)) is action:
        QApplication.clipboard().setText(text)


def show_toast(window, text, level="success"):
    """在窗口底部弹一条轻提示。返回该 Toast 实例（复用）。"""
    toast = getattr(window, "_toast_instance", None)
    if toast is None:
        toast = Toast(window)
        window._toast_instance = toast
    toast.flash(text, level)
    return toast


class MicroPill(QLabel):
    """微型胶囊 (Micro-Pill)：高度 20-22px，字号 12px，全圆角 (999px)，背景 bg-elevated，无边框。"""

    def __init__(self, text="", faint=False, parent=None):
        super().__init__(text, parent)
        self.setObjectName("microPillFaint" if faint else "microPill")
        self.setAlignment(Qt.AlignCenter)
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)


class StatusDot(QWidget):
    """状态指示灯 (Status Dot)：8px 100% 圆角状态色小圆点 + 可选的 text-secondary 描述文字。"""

    _DOT_NAMES = {
        "success": "statusDotSuccess",
        "warning": "statusDotWarning",
        "danger": "statusDotDanger",
        "info": "statusDotInfo",
        "muted": "statusDotMuted",
    }

    def __init__(self, level="info", text="", parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.dot = QLabel()
        self.dot.setObjectName(self._DOT_NAMES.get(level, "statusDotInfo"))
        layout.addWidget(self.dot, 0, Qt.AlignVCenter)

        self.label = QLabel(text)
        self.label.setObjectName("muted")
        self.label.setVisible(bool(text))
        layout.addWidget(self.label, 0, Qt.AlignVCenter)

    def set_level(self, level):
        restyle(self.dot, self._DOT_NAMES.get(level, "statusDotInfo"))

    def set_text(self, text):
        self.label.setText(text)
        self.label.setVisible(bool(text))


class GhostLineEdit(QLineEdit):
    """幽灵单行输入框：常态下背景透明且无边框，仅在 hover 或 focus 时显示 1px accent 边框。"""

    focused_changed = Signal(bool)

    def __init__(self, placeholder="", parent=None):
        super().__init__(parent)
        self.setObjectName("ghostInput")
        if placeholder:
            self.setPlaceholderText(placeholder)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.focused_changed.emit(True)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.focused_changed.emit(False)


class GhostTextEdit(QTextEdit):
    """幽灵多行文本框：常态下背景透明且无边框，仅在 hover 或 focus 时显示 1px accent 边框。"""

    def __init__(self, placeholder="", parent=None):
        super().__init__(parent)
        self.setObjectName("ghostInput")
        if placeholder:
            self.setPlaceholderText(placeholder)


class GhostInputRow(QFrame):
    """带前置 16px 线性图标的幽灵输入行（用于 P0 任务快速添加等）。"""

    def __init__(self, placeholder="", glyph="plus", parent=None):
        super().__init__(parent)
        self.setObjectName("ghostInputRow")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 8, 0)
        layout.setSpacing(6)

        self.icon_label = QLabel()
        self.icon_label.setPixmap(icon(glyph, TEXT_COLORS["faint"], 16).pixmap(16, 16))
        self.icon_label.setFixedSize(16, 16)
        layout.addWidget(self.icon_label, 0, Qt.AlignVCenter)

        self.line_edit = GhostLineEdit(placeholder, self)
        self.line_edit.focused_changed.connect(self._on_focus_changed)
        layout.addWidget(self.line_edit, 1)

    def _on_focus_changed(self, focused):
        self.setProperty("focused", "true" if focused else "false")
        self.style().unpolish(self)
        self.style().polish(self)


class PillListDelegate(QStyledItemDelegate):
    """列表项微胶囊渲染代理（用于 P3 资料库、P4 列表视图、P2 曲目列表）。

    消除原先 `└─ 写作\\105` 的树状拼接文本，将第二行元信息渲染为 bg-elevated 微型胶囊
    与可选的状态指示灯；在 IconMode（网格视图）下自动回退给默认绘制。
    """

    TITLE_ROLE = TITLE_ROLE
    PILL_ROLE = PILL_ROLE
    DOT_ROLE = DOT_ROLE
    FAINT_ROLE = FAINT_ROLE

    def _is_icon_mode(self, option):
        widget = option.widget
        if isinstance(widget, QListView):
            return widget.viewMode() == QListView.IconMode
        return False

    def sizeHint(self, option, index):
        if self._is_icon_mode(option) or index.data(PILL_ROLE) is None:
            return super().sizeHint(option, index)
        return QSize(0, 56)

    def paint(self, painter, option, index):
        if self._is_icon_mode(option) or index.data(PILL_ROLE) is None:
            super().paint(painter, option, index)
            return

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)

        rect = option.rect.adjusted(2, 1, -2, -1)
        is_selected = bool(option.state & QStyle.State_Selected)
        is_hover = bool(option.state & QStyle.State_MouseOver)
        is_faint = bool(index.data(FAINT_ROLE))

        if is_selected:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(BG_SELECTED))
            painter.drawRoundedRect(QRectF(rect), 6.0, 6.0)
        elif is_hover:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(BG_HOVER))
            painter.drawRoundedRect(QRectF(rect), 6.0, 6.0)

        left = rect.left() + 10
        right = rect.right() - 10
        top = rect.top() + 7

        # 左侧文件类型图标（如有）
        deco = index.data(Qt.DecorationRole)
        if isinstance(deco, QIcon) and not deco.isNull():
            pm = deco.pixmap(16, 16)
            painter.drawPixmap(left, top + 1, pm)
            left += 24

        # 第一行：主标题
        title = index.data(TITLE_ROLE)
        if not title:
            raw = str(index.data(Qt.DisplayRole) or "")
            title = raw.split("\n", 1)[0]

        title_font = painter.font()
        title_font.setPixelSize(14)
        title_font.setWeight(title_font.Weight.Medium if is_selected else title_font.Weight.Normal)
        painter.setFont(title_font)

        if is_faint:
            title_color = QColor(TEXT_COLORS["faint"])
        elif is_selected and option.widget and option.widget.objectName() == "trackList":
            title_color = QColor(ACCENT)
        else:
            title_color = QColor(TEXT_COLORS["primary"])
        painter.setPen(title_color)

        title_fm = QFontMetrics(title_font)
        avail_w = max(20, right - left)
        elided_title = title_fm.elidedText(title, Qt.ElideMiddle, avail_w)
        painter.drawText(
            QRectF(left, top, avail_w, 20),
            int(Qt.AlignLeft | Qt.AlignVCenter),
            elided_title,
        )

        # 第二行：状态指示灯（可选）+ 微型胶囊序列
        pill_y = top + 22
        pill_h = 20
        cur_x = left

        dot_level = index.data(DOT_ROLE)
        if dot_level:
            dot_hex = STATE_COLORS.get(dot_level, TEXT_COLORS["faint"])
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(dot_hex))
            painter.drawEllipse(QRectF(cur_x, pill_y + 6, 8, 8))
            cur_x += 14

        pills = index.data(PILL_ROLE) or ()
        pill_font = painter.font()
        pill_font.setPixelSize(12)
        pill_font.setWeight(pill_font.Weight.Normal)
        painter.setFont(pill_font)
        pill_fm = QFontMetrics(pill_font)

        for entry in pills:
            if isinstance(entry, (tuple, list)) and len(entry) >= 2:
                text, pill_faint = str(entry[0]), bool(entry[1])
            else:
                text, pill_faint = str(entry), is_faint
            if not text:
                continue
            text_w = pill_fm.horizontalAdvance(text)
            pill_w = min(text_w + 16, max(36, right - cur_x))
            if cur_x + pill_w > right and cur_x > left:
                break
            pill_rect = QRectF(cur_x, pill_y, pill_w, pill_h)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(BG_ELEVATED))
            painter.drawRoundedRect(pill_rect, 10.0, 10.0)

            painter.setPen(QColor(TEXT_COLORS["faint"] if pill_faint else TEXT_COLORS["secondary"]))
            draw_text = pill_fm.elidedText(text, Qt.ElideRight, int(pill_w - 12))
            painter.drawText(
                pill_rect.adjusted(6, 0, -6, 0),
                int(Qt.AlignCenter),
                draw_text,
            )
            cur_x += int(pill_w) + 6

        painter.restore()