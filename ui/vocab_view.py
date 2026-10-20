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

from PySide6.QtCore import (
    QAbstractTableModel,
    QEvent,
    QModelIndex,
    QPoint,
    QRect,
    QSize,
    QTimer,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QKeySequence,
    QPainter,
    QPen,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QTableView,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.tts import BatchTTSPreloadWorker, WordSpeaker, count_uncached_words
from ui.components import Banner, EmptyState, LabelMotion, make_copyable, show_toast
from ui.icons import icon
from ui.motion import (
    DELAY_MEANING,
    DELAY_WORD,
    DUR_BASE,
    DUR_MICRO,
    DUR_REVEAL,
    DUR_SOFT,
    DX_MEANING,
    DX_SEQ,
    DX_WORD,
    DY_REVEAL,
)
from ui.style import restyle
from ui.theme import (
    ACCENT,
    ACCENT_WARM,
    BG_HOVER,
    BG_SURFACE,
    BORDER_COLOR,
    BORDER_STRONG,
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


class _WordListDeleteButton(QPushButton):
    """词表展开列表行末的 × 删除按钮。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("wordListDeleteBtn")
        self.setFixedSize(22, 22)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setToolTip("删除此词表")
        self._normal_icon = icon("x", TEXT_COLORS["muted"], 12)
        self._hover_icon = icon("x", STATE_COLORS["danger"], 12)
        self.setIcon(self._normal_icon)
        self.setIconSize(QSize(12, 12))

    def enterEvent(self, event):
        self.setIcon(self._hover_icon)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.setIcon(self._normal_icon)
        super().leaveEvent(event)


class _WordListRowWidget(QWidget):
    """词表展开列表单行：左侧词表名（透传鼠标事件供列表拖拽/点击），右侧 × 删除按钮。"""

    def __init__(self, text, list_id, on_delete, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 4, 6, 4)
        layout.setSpacing(8)

        self.label = QLabel(text)
        self.label.setObjectName("wordListItemText")
        self.label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        layout.addWidget(self.label, 1)

        if list_id is not None:
            self.setToolTip("上下拖动可调整词表顺序")
            btn = _WordListDeleteButton(self)
            btn.clicked.connect(lambda _checked=False, lid=list_id: on_delete(lid))
            layout.addWidget(btn, 0, Qt.AlignVCenter)


class _WordListPopupList(QListWidget):
    """支持拖拽排序（首项「全部词表」固定在顶部）与点击选择的词表弹出列表。"""

    item_activated = Signal(int)
    order_changed = Signal(list)

    def __init__(self, on_delete, parent=None):
        super().__init__(parent)
        self.setObjectName("wordListPopupView")
        self.setMouseTracking(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self._on_delete = on_delete
        self._entries = []
        self._press_pos = QPoint()
        self._press_row = -1
        self._drag_row = -1
        self._dragging = False

    def populate(self, entries, selected_row=0):
        self._entries = list(entries)
        self.clear()
        for text, list_id in self._entries:
            item = QListWidgetItem(self)
            item.setSizeHint(QSize(0, 32))
            item.setData(Qt.UserRole, list_id)
            self.addItem(item)
            row_widget = _WordListRowWidget(text, list_id, self._on_delete, self)
            self.setItemWidget(item, row_widget)
        if 0 <= selected_row < self.count():
            self.setCurrentRow(selected_row)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            pos = event.position().toPoint()
            item = self.itemAt(pos)
            row = self.row(item) if item is not None else -1
            self._press_pos = pos
            self._press_row = row
            # 第 0 项「全部词表」固定在顶部，不参与拖动
            self._drag_row = row if row >= 1 else -1
            self._dragging = False
            if row >= 0:
                self.setCurrentRow(row)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (event.buttons() & Qt.LeftButton) and self._drag_row >= 1:
            pos = event.position().toPoint()
            if not self._dragging:
                if (pos - self._press_pos).manhattanLength() >= QApplication.startDragDistance():
                    self._dragging = True
                    self.viewport().setCursor(Qt.ClosedHandCursor)
            if self._dragging and self.count() > 2:
                clamped_y = max(0, min(self.viewport().height() - 1, pos.y()))
                target_item = self.itemAt(QPoint(12, clamped_y))
                if target_item is not None:
                    target_row = self.row(target_item)
                else:
                    target_row = self.count() - 1 if pos.y() >= self.viewport().height() else 1
                target_row = max(1, min(self.count() - 1, target_row))
                if target_row != self._drag_row:
                    entry = self._entries.pop(self._drag_row)
                    self._entries.insert(target_row, entry)
                    self._drag_row = target_row
                    self.populate(self._entries, selected_row=target_row)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.viewport().unsetCursor()
            was_dragging = self._dragging
            press_row = self._press_row
            self._dragging = False
            self._drag_row = -1
            self._press_row = -1
            if was_dragging:
                self.order_changed.emit(list(self._entries))
            else:
                pos = event.position().toPoint()
                item = self.itemAt(pos)
                release_row = self.row(item) if item is not None else -1
                if release_row >= 0 and release_row == press_row:
                    self.item_activated.emit(release_row)
            event.accept()
            return
        super().mouseReleaseEvent(event)


class WordListComboBox(QComboBox):
    """词表下拉框：展开列表支持拖动调整词表顺序，以及在每份词表右侧显示 × 删除按钮。"""

    order_changed = Signal(list)
    delete_requested = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._popup = _WordListPopup(self)

    def showPopup(self):
        if self.count() == 0:
            return
        self._popup.open_for_combo()

    def hidePopup(self):
        if self._popup.isVisible():
            self._popup.hide()
        super().hidePopup()


class _WordListPopup(QFrame):
    """WordListComboBox 的自定义弹出层。"""

    def __init__(self, combo: WordListComboBox):
        super().__init__(combo, Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setObjectName("wordListPopup")
        self._combo = combo

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.list_widget = _WordListPopupList(self._on_delete_clicked, self)
        self.list_widget.item_activated.connect(self._on_item_activated)
        self.list_widget.order_changed.connect(self._on_order_changed)
        layout.addWidget(self.list_widget)

    def open_for_combo(self):
        entries = [
            (self._combo.itemText(i), self._combo.itemData(i))
            for i in range(self._combo.count())
        ]
        current_idx = max(0, self._combo.currentIndex())
        self.list_widget.populate(entries, selected_row=current_idx)

        fm = self._combo.fontMetrics()
        max_text_w = max((fm.horizontalAdvance(text) for text, _ in entries), default=140)
        popup_width = max(self._combo.width(), max_text_w + 68)
        row_count = len(entries)
        popup_height = min(320, max(42, row_count * 34 + 10))
        self.setFixedSize(popup_width, popup_height)

        global_pos = self._combo.mapToGlobal(QPoint(0, self._combo.height() + 2))
        self.move(global_pos)
        self.show()
        self.list_widget.setFocus()

    def _on_item_activated(self, row):
        self.hide()
        if 0 <= row < self._combo.count():
            self._combo.setCurrentIndex(row)

    def _on_order_changed(self, entries):
        current_data = self._combo.currentData()
        self._combo.blockSignals(True)
        self._combo.clear()
        new_index = 0
        ordered_ids = []
        for idx, (text, data) in enumerate(entries):
            self._combo.addItem(text, data)
            if data == current_data:
                new_index = idx
            if isinstance(data, int):
                ordered_ids.append(data)
        self._combo.setCurrentIndex(new_index)
        self._combo.blockSignals(False)
        self.list_widget.setCurrentRow(new_index)
        self._combo.order_changed.emit(ordered_ids)

    def _on_delete_clicked(self, list_id):
        self.hide()
        if isinstance(list_id, int):
            self._combo.delete_requested.emit(list_id)


class WordCardGridModel(QAbstractTableModel):
    """卡片网格的数据模型（支持双列/多列横向流排布与高性能虚拟化渲染）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._words = []
        self._columns = 2

    def set_words(self, words, columns=2):
        self.beginResetModel()
        self._words = list(words)
        self._columns = max(1, columns)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        if not self._words:
            return 0
        return (len(self._words) + self._columns - 1) // self._columns

    def columnCount(self, parent=QModelIndex()):
        return self._columns

    def word_at(self, row, col):
        idx = row * self._columns + col
        if 0 <= idx < len(self._words):
            return self._words[idx]
        return None

    def index_for_word(self, korean):
        for idx, word in enumerate(self._words):
            if word.get("korean") == korean:
                r = idx // self._columns
                c = idx % self._columns
                return self.index(r, c)
        return QModelIndex()

    def flags(self, index):
        if not index.isValid():
            return Qt.NoItemFlags
        if not self.word_at(index.row(), index.column()):
            return Qt.NoItemFlags
        return Qt.ItemIsSelectable | Qt.ItemIsEnabled

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        word = self.word_at(index.row(), index.column())
        if not word:
            return None
        if role == Qt.UserRole:
            return word
        return None


class WordCardDelegate(QStyledItemDelegate):
    """紧凑横向流卡片委托绘制器：韩语大字 + 中文释义 + 状态角标 + 悬停快捷入口。"""

    action_clicked = Signal(str, dict)
    card_double_clicked = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.test_mode = False
        self._hover_index = QModelIndex()
        self._hover_action = None

    def set_test_mode(self, enabled: bool):
        self.test_mode = enabled

    def set_hover(self, index: QModelIndex, action: str = None):
        self._hover_index = index
        self._hover_action = action

    def sizeHint(self, option, index):
        return QSize(320, 70)

    def paint(self, painter: QPainter, option, index: QModelIndex):
        word = index.data(Qt.UserRole)
        if not word:
            return

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)

        rect = option.rect.adjusted(5, 3, -5, -3)
        is_selected = bool(option.state & QStyle.State_Selected)
        is_hovered = (index == self._hover_index)
        state = word.get("state", "") or ""
        is_drill = state in _TO_DRILL

        # 1. 卡片背景底色
        bg_color = QColor(ROW_DRILL_BG if is_drill else BG_SURFACE)
        if is_hovered and not is_drill:
            bg_color = QColor(BG_HOVER)
        painter.setBrush(QBrush(bg_color))

        # 2. 边框
        if is_selected:
            painter.setPen(QPen(QColor(ACCENT), 1.5))
        elif is_hovered:
            painter.setPen(QPen(QColor(BORDER_STRONG), 1))
        else:
            painter.setPen(QPen(QColor(BORDER_COLOR), 1))

        painter.drawRoundedRect(rect, 8, 8)

        # 3. 尺寸与定位
        rx = rect.right() - 10
        top_y = rect.top() + 8

        # 状态色标药丸（右上角）
        state_text = STATE_LABELS.get(state, "未过")
        st_color = QColor(state_color(state))
        pill_rect = QRect(rx - 44, top_y, 44, 20)

        pill_bg_map = {
            "known": QColor("#E6F3EB"),
            "fuzzy": QColor("#FEF7E6"),
            "unknown": QColor("#FDEEEB"),
            "": QColor("#F0F2F0"),
        }
        painter.setBrush(QBrush(pill_bg_map.get(state, QColor("#F0F2F0"))))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(pill_rect, 10, 10)

        pill_font = QFont(painter.font())
        pill_font.setPointSize(9)
        pill_font.setBold(True)
        painter.setFont(pill_font)
        painter.setPen(QPen(st_color))
        painter.drawText(pill_rect, Qt.AlignCenter, state_text)

        # 药丸左侧：悬停时浮现动作图标，平时展示星标与笔记指示
        curr_x = rx - 48
        if is_hovered:
            # 发音
            tts_rect = QRect(curr_x - 22, top_y, 20, 20)
            is_tts_h = (self._hover_action == "tts")
            icon("volume", ACCENT if is_tts_h else TEXT_COLORS["muted"], 14).paint(painter, tts_rect)
            curr_x -= 24

            # 星标
            star_rect = QRect(curr_x - 22, top_y, 20, 20)
            is_star_h = (self._hover_action == "star")
            is_starred = bool(word.get("is_starred"))
            star_color = ACCENT_WARM if (is_starred or is_star_h) else TEXT_COLORS["muted"]
            icon("star-filled" if is_starred else "star", star_color, 14).paint(painter, star_rect)
            curr_x -= 24

            # 笔记
            note_rect = QRect(curr_x - 22, top_y, 20, 20)
            is_note_h = (self._hover_action == "note")
            icon("edit", ACCENT if is_note_h else TEXT_COLORS["muted"], 14).paint(painter, note_rect)
            curr_x -= 24
        else:
            if word.get("is_starred"):
                star_rect = QRect(curr_x - 18, top_y + 2, 16, 16)
                icon("star-filled", ACCENT_WARM, 13).paint(painter, star_rect)
                curr_x -= 20
            if word.get("note"):
                note_rect = QRect(curr_x - 18, top_y + 2, 16, 16)
                icon("edit", TEXT_COLORS["faint"], 13).paint(painter, note_rect)
                curr_x -= 20

        # 第一行内容：韩文大字 (16pt Pretendard, 约 21px) + 中文释义 (11pt)
        kr_font = QFont("Pretendard", 16)
        kr_font.setBold(True)
        painter.setFont(kr_font)
        painter.setPen(QPen(QColor(TEXT_COLORS["primary"])))

        fm_kr = QFontMetrics(kr_font)
        korean_str = word.get("korean", "")
        kr_w = fm_kr.horizontalAdvance(korean_str)
        kr_rect = QRect(rect.left() + 12, top_y - 2, kr_w, 26)
        painter.drawText(kr_rect, Qt.AlignLeft | Qt.AlignVCenter, korean_str)

        # 中文释义
        cn_x = kr_rect.right() + 12
        cn_w = max(0, curr_x - cn_x - 6)
        if cn_w > 10:
            cn_font = QFont(painter.font())
            cn_font.setPointSize(11)
            cn_font.setBold(False)
            painter.setFont(cn_font)

            if self.test_mode and not (is_hovered or is_selected):
                painter.setPen(QPen(QColor(TEXT_COLORS["faint"])))
                painter.drawText(QRect(cn_x, top_y - 2, cn_w, 26), Qt.AlignLeft | Qt.AlignVCenter, "••••••")
            else:
                meaning_str = word.get("meaning", "") or ""
                fm_cn = QFontMetrics(cn_font)
                elided_cn = fm_cn.elidedText(meaning_str, Qt.ElideRight, cn_w)
                painter.setPen(QPen(QColor(TEXT_COLORS["secondary"])))
                painter.drawText(QRect(cn_x, top_y - 2, cn_w, 26), Qt.AlignLeft | Qt.AlignVCenter, elided_cn)

        # 第二行内容：编号标签 (#12) + 笔记摘要
        bot_y = rect.top() + 36
        bot_font = QFont(painter.font())
        bot_font.setPointSize(10)
        bot_font.setBold(False)
        painter.setFont(bot_font)

        seq_text = f"#{word.get('seq', '')}"
        if word.get("list_name"):
            seq_text = f"[{word['list_name']}] #{word.get('seq', '')}"
        fm_bot = QFontMetrics(bot_font)
        seq_w = fm_bot.horizontalAdvance(seq_text)

        painter.setPen(QPen(QColor(TEXT_COLORS["faint"])))
        painter.drawText(QRect(rect.left() + 12, bot_y, seq_w, 18), Qt.AlignLeft | Qt.AlignVCenter, seq_text)

        # 笔记摘要展示
        note_text = (word.get("note") or "").strip().replace("\n", " ")
        if note_text:
            note_x = rect.left() + 12 + seq_w + 10
            note_w = max(0, rect.right() - 12 - note_x)
            if note_w > 20:
                elided_note = fm_bot.elidedText(f"📝 {note_text}", Qt.ElideRight, note_w)
                painter.setPen(QPen(QColor(TEXT_COLORS["muted"])))
                painter.drawText(QRect(note_x, bot_y, note_w, 18), Qt.AlignLeft | Qt.AlignVCenter, elided_note)

        painter.restore()

    def editorEvent(self, event, model, option, index):
        word = index.data(Qt.UserRole)
        if not word:
            return False

        rect = option.rect.adjusted(5, 3, -5, -3)
        rx = rect.right() - 10
        top_y = rect.top() + 8

        pill_rect = QRect(rx - 44, top_y, 44, 20)
        tts_rect = QRect(rx - 48 - 22, top_y, 20, 20)
        star_rect = QRect(rx - 48 - 46, top_y, 20, 20)
        note_rect = QRect(rx - 48 - 70, top_y, 20, 20)

        pos = event.pos() if hasattr(event, "pos") else None

        if event.type() == QEvent.MouseMove:
            action = None
            if pill_rect.contains(pos):
                action = "state"
            elif tts_rect.contains(pos):
                action = "tts"
            elif star_rect.contains(pos):
                action = "star"
            elif note_rect.contains(pos):
                action = "note"

            if self._hover_index != index or self._hover_action != action:
                self._hover_index = index
                self._hover_action = action
                if option.widget:
                    option.widget.viewport().update()
            return False

        if event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
            if pill_rect.contains(pos):
                self.action_clicked.emit("state", word)
                return True
            if tts_rect.contains(pos):
                self.action_clicked.emit("tts", word)
                return True
            if star_rect.contains(pos):
                self.action_clicked.emit("star", word)
                return True
            if note_rect.contains(pos):
                self.action_clicked.emit("note", word)
                return True

        if event.type() == QEvent.MouseButtonDblClick and event.button() == Qt.LeftButton:
            self.card_double_clicked.emit(word)
            return True

        return super().editorEvent(event, model, option, index)


class _VocabCardTableView(QTableView):
    """卡片网格专用 QTableView，处理键盘快捷键与悬停脱离。"""

    def leaveEvent(self, event):
        delegate = self.itemDelegate()
        if hasattr(delegate, "set_hover"):
            delegate.set_hover(QModelIndex(), None)
            self.viewport().update()
        super().leaveEvent(event)

    def keyPressEvent(self, event):
        parent_view = self.parent()
        while parent_view and not hasattr(parent_view, "open_note_drawer"):
            parent_view = parent_view.parent()

        if parent_view:
            if event.matches(QKeySequence.Copy) or (event.modifiers() & Qt.ControlModifier and event.key() == Qt.Key_C):
                parent_view.copy_current_word("korean")
                event.accept()
                return
            if event.key() == Qt.Key_N:
                parent_view.open_note_drawer()
                event.accept()
                return
            if event.key() == Qt.Key_R:
                parent_view.speak_current_word()
                event.accept()
                return
            if event.key() == Qt.Key_Escape:
                parent_view.close_note_drawer()
                event.accept()
                return
            if event.key() in (Qt.Key_1, Qt.Key_2, Qt.Key_3):
                mapping = {Qt.Key_1: "known", Qt.Key_2: "fuzzy", Qt.Key_3: "unknown"}
                parent_view._on_quick_digit_key(mapping[event.key()])
                event.accept()
                return

        super().keyPressEvent(event)


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

        self.speaker = WordSpeaker(self.database, self)
        self.speaker.notice.connect(self._on_speaker_notice)

        self._batch_tts_worker = None
        self._batch_list_id = None
        # 后台下载队列：同一时刻只跑一条下载，但它**不属于当前选中的词表**——
        # 切词表不打断，导入新词表自动开始，已在跑时再点别表是排队而非抢占。
        self._tts_queue = []

        self.init_ui()
        self.refresh_lists()
        self.reload_browse()

        self._in_event_filter = False
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
            self.destroyed.connect(self._remove_app_event_filter)

    def _remove_app_event_filter(self, *args):
        app = QApplication.instance()
        if app is not None:
            try:
                app.removeEventFilter(self)
            except Exception:
                pass

    def closeEvent(self, event):
        self._remove_app_event_filter()
        super().closeEvent(event)

    def eventFilter(self, watched, event):
        if getattr(self, "_in_event_filter", False):
            return super().eventFilter(watched, event)
        self._in_event_filter = True
        try:
            if event is not None and event.type() == QEvent.Type.MouseButtonPress:
                if self.isVisible():
                    if (
                        watched is getattr(self, "lbl_pass_cn", None)
                        and event.button() == Qt.MouseButton.LeftButton
                        and self._mode == self.MODE_PASS
                        and 0 <= self._pass_index < len(self._pass_rows)
                    ):
                        if not self._revealed:
                            self._just_revealed_by_click = True
                            self._revealed = True
                            self._render_pass_meaning()
                            return True
                        self._just_revealed_by_click = False

                    # 点击笔记框外部区域时取消聚焦，清除焦点边框与输入光标
                    for editor in (getattr(self, "txt_notes", None), getattr(self, "txt_drill_notes", None)):
                        if editor is not None and editor.hasFocus():
                            if watched not in (editor, getattr(editor, "viewport", lambda: None)()):
                                editor.clearFocus()

                    # 点击文本标签外部区域时清除文本选区和可能存在的光标
                    for lbl in (
                        getattr(self, "lbl_detail_kr", None),
                        getattr(self, "lbl_detail_cn", None),
                        getattr(self, "lbl_pass_kr", None),
                        getattr(self, "lbl_pass_cn", None),
                        getattr(self, "lbl_drill_kr", None),
                        getattr(self, "lbl_drill_cn", None),
                    ):
                        if lbl is not None and watched is not lbl:
                            if hasattr(lbl, "hasSelectedText") and lbl.hasSelectedText():
                                lbl.setSelection(0, 0)
                            lbl.clearFocus()
            elif event is not None and event.type() == QEvent.Type.MouseButtonRelease:
                if (
                    self.isVisible()
                    and watched is getattr(self, "lbl_pass_cn", None)
                    and event.button() == Qt.MouseButton.LeftButton
                    and self._mode == self.MODE_PASS
                    and 0 <= self._pass_index < len(self._pass_rows)
                ):
                    if getattr(self, "_just_revealed_by_click", False):
                        self._just_revealed_by_click = False
                    elif self._revealed and not self.lbl_pass_cn.hasSelectedText():
                        self._revealed = False
                        self._render_pass_meaning()
                        return True
        except Exception:
            pass
        finally:
            self._in_event_filter = False
        return super().eventFilter(watched, event)

    # ==================================================================
    # 布局
    # ==================================================================

    def init_ui(self):
        self.setObjectName("panePage")
        self.setAttribute(Qt.WA_StyledBackground, True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._build_action_bar())

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
        frame = QFrame()
        frame.setObjectName("commandBar")
        bar = QHBoxLayout(frame)
        bar.setContentsMargins(20, 10, 20, 10)
        bar.setSpacing(8)

        title = QLabel("智能单词仓")
        title.setObjectName("sectionTitle")
        bar.addWidget(title)
        bar.addSpacing(8)

        self.combo_list = WordListComboBox()
        self.combo_list.setMinimumWidth(190)
        self.combo_list.setToolTip("决定了过词与专攻的作业范围；展开后可上下拖动调整词表顺序")
        self.combo_list.currentIndexChanged.connect(self._on_list_changed)
        self.combo_list.order_changed.connect(self._on_list_order_changed)
        self.combo_list.delete_requested.connect(self.delete_current_list)
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

        self.btn_auto_tts = QPushButton("自动发音")
        self.btn_auto_tts.setObjectName("modeSwitch")
        self.btn_auto_tts.setCheckable(True)
        self.btn_auto_tts.setCursor(Qt.PointingHandCursor)
        self.btn_auto_tts.setFocusPolicy(Qt.NoFocus)
        self.btn_auto_tts.setToolTip("过词与专攻模式下，切换到下一个单词时自动朗读韩语发音")
        self.btn_auto_tts.clicked.connect(self._on_auto_tts_toggled)
        self.btn_auto_tts.setVisible(False)
        self._sync_auto_tts_button(self.database.get_tts_auto_play())
        bar.addWidget(self.btn_auto_tts)

        self.btn_load_tsv = QPushButton("载入词表")
        self.btn_load_tsv.setObjectName("iconButton")
        self.btn_load_tsv.setIcon(icon("folder-open"))
        self.btn_load_tsv.clicked.connect(self.load_tsv_dialog)
        bar.addWidget(self.btn_load_tsv)

        # 搜索框：规格 4.2 —— 仅浏览模式（过词/专攻要的是专注，不是过滤）
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("韩文或中文…")
        self.search_input.setFixedWidth(180)
        self.search_input.textChanged.connect(self._on_search_changed)
        bar.addWidget(self.search_input)
        return frame

    # ---------------------------------------------------------------- 浏览

    def _build_browse_page(self):
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)

        # 词表元信息与全局控制枢纽：统计概览 · 状态胶囊筛选 · 自测开关 · 视图切换
        meta_bar = QFrame()
        meta_bar.setObjectName("paneSubBar")
        meta_row = QHBoxLayout(meta_bar)
        meta_row.setContentsMargins(16, 6, 16, 6)
        meta_row.setSpacing(10)

        self.lbl_meta = QLabel("")
        self.lbl_meta.setObjectName("muted")
        meta_row.addWidget(self.lbl_meta)

        # 垂直分隔线
        div = QFrame()
        div.setFrameShape(QFrame.VLine)
        div.setFrameShadow(QFrame.Plain)
        div.setObjectName("paneDivider")
        meta_row.addWidget(div)

        # 状态胶囊筛选组 [全部] [不认识] [模糊] [认识]
        self._filter_btn_group = QButtonGroup(self)
        self._filter_btn_group.setExclusive(True)
        self._state_filter = "all"
        filter_specs = [
            ("all", "全部"),
            ("unknown", "不认识"),
            ("fuzzy", "模糊"),
            ("known", "认识"),
        ]
        self._filter_pills = {}
        for key, label in filter_specs:
            btn = QPushButton(label)
            btn.setObjectName("filterPill")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            if key == "all":
                btn.setChecked(True)
            self._filter_btn_group.addButton(btn)
            btn.clicked.connect(lambda _c, k=key: self._on_filter_pill_clicked(k))
            meta_row.addWidget(btn)
            self._filter_pills[key] = btn

        meta_row.addStretch()

        # 背诵自测开关：隐藏释义（Hover/选中临时揭示）
        self.btn_test_mode = QPushButton("隐藏释义")
        self.btn_test_mode.setObjectName("testModeToggle")
        self.btn_test_mode.setIcon(icon("eye-off", TEXT_COLORS["secondary"], 14))
        self.btn_test_mode.setCheckable(True)
        self.btn_test_mode.setCursor(Qt.PointingHandCursor)
        self.btn_test_mode.setToolTip("开启背诵自测模式：默认隐藏中文释义，鼠标悬停或选中词条时临时清晰呈现")
        self.btn_test_mode.clicked.connect(self._on_test_mode_toggled)
        meta_row.addWidget(self.btn_test_mode)

        # 视图切换按钮组：卡片网格 / 紧凑表格
        self._view_mode = "grid"
        self._view_btn_group = QButtonGroup(self)
        self._view_btn_group.setExclusive(True)

        self.btn_view_grid = QPushButton("网格")
        self.btn_view_grid.setObjectName("viewSwitchBtn")
        self.btn_view_grid.setIcon(icon("grid", TEXT_COLORS["secondary"], 14))
        self.btn_view_grid.setCheckable(True)
        self.btn_view_grid.setChecked(True)
        self.btn_view_grid.setCursor(Qt.PointingHandCursor)
        self.btn_view_grid.setToolTip("卡片网格视图：双列横向流排布，大字快速扫词")
        self.btn_view_grid.clicked.connect(lambda: self._set_browse_view_mode("grid"))
        self._view_btn_group.addButton(self.btn_view_grid)
        meta_row.addWidget(self.btn_view_grid)

        self.btn_view_table = QPushButton("表格")
        self.btn_view_table.setObjectName("viewSwitchBtn")
        self.btn_view_table.setIcon(icon("list", TEXT_COLORS["secondary"], 14))
        self.btn_view_table.setCheckable(True)
        self.btn_view_table.setCursor(Qt.PointingHandCursor)
        self.btn_view_table.setToolTip("紧凑表格视图：带个人笔记摘要的高密度数据表格")
        self.btn_view_table.clicked.connect(lambda: self._set_browse_view_mode("table"))
        self._view_btn_group.addButton(self.btn_view_table)
        meta_row.addWidget(self.btn_view_table)

        # 批量预缓存发音按钮（全部就绪时自动隐藏）
        self.btn_batch_tts = QPushButton("缓存发音")
        self.btn_batch_tts.setObjectName("iconButton")
        self.btn_batch_tts.setIcon(icon("download"))
        self.btn_batch_tts.setCursor(Qt.PointingHandCursor)
        self.btn_batch_tts.setToolTip("预先下载本表全部未缓存的发音音频，以便完全离线使用")
        self.btn_batch_tts.clicked.connect(self._toggle_batch_tts_from_button)
        self.btn_batch_tts.setVisible(False)
        meta_row.addWidget(self.btn_batch_tts)

        box.addWidget(meta_bar)

        # 主分栏容器：左侧全宽主展示区，右侧按需呼出的笔记抽屉
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setObjectName("vocabSplitter")
        self.splitter.setHandleWidth(1)
        self.splitter.setChildrenCollapsible(True)

        self.left_stack = QStackedWidget()

        # 0: 卡片网格视图
        self.grid_model = WordCardGridModel(self)
        self.grid_delegate = WordCardDelegate(self)
        self.grid_delegate.action_clicked.connect(self._on_grid_action_clicked)
        self.grid_delegate.card_double_clicked.connect(lambda word: self.open_note_drawer(word))

        self.grid_view = _VocabCardTableView(self)
        self.grid_view.setObjectName("vocabCardGrid")
        self.grid_view.setModel(self.grid_model)
        self.grid_view.setItemDelegate(self.grid_delegate)
        self.grid_view.setShowGrid(False)
        self.grid_view.verticalHeader().setVisible(False)
        self.grid_view.verticalHeader().setDefaultSectionSize(70)
        self.grid_view.horizontalHeader().setVisible(False)
        self.grid_view.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.grid_view.setSelectionMode(QTableView.SingleSelection)
        self.grid_view.setSelectionBehavior(QTableView.SelectItems)
        self.grid_view.setMouseTracking(True)
        self.grid_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.grid_view.customContextMenuRequested.connect(self._grid_context_menu)
        self.grid_view.selectionModel().selectionChanged.connect(self._on_grid_selection_changed)

        self.left_stack.addWidget(self.grid_view)  # 0

        # 1: 紧凑表格视图
        self.table = QTableWidget(0, 5)
        self.table.setObjectName("vocabTable")
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(38)
        self.table.horizontalHeader().setHighlightSections(False)
        self.table.itemSelectionChanged.connect(self._on_table_selection_changed)
        self.table.itemDoubleClicked.connect(lambda _item: self.open_note_drawer())
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._table_context_menu)

        self.left_stack.addWidget(self.table)      # 1

        # 2: 空数据提示
        self.empty = EmptyState("", "")
        self.empty.action_clicked.connect(self._on_empty_action)
        self._empty_action = None
        self.left_stack.addWidget(self.empty)      # 2

        self.splitter.addWidget(self.left_stack)

        # 右侧按需呼出的笔记抽屉（平时收起）
        self.drawer = self._build_note_drawer()
        self.drawer.setVisible(False)
        self.splitter.addWidget(self.drawer)
        self.splitter.setSizes([1000, 0])

        box.addWidget(self.splitter, 1)
        self._configure_columns(False)
        return page

    def _build_note_drawer(self):
        drawer = QFrame()
        drawer.setObjectName("vocabNoteDrawer")
        drawer.setMinimumWidth(320)
        drawer.setMaximumWidth(420)
        box = QVBoxLayout(drawer)
        box.setContentsMargins(18, 16, 18, 16)
        box.setSpacing(10)

        head = QHBoxLayout()
        heading = QLabel("个人笔记")
        heading.setObjectName("sectionTitle")
        head.addWidget(heading)
        head.addStretch()

        self.btn_drawer_close = QPushButton()
        self.btn_drawer_close.setObjectName("drawerCloseBtn")
        self.btn_drawer_close.setIcon(icon("x", TEXT_COLORS["muted"], 14))
        self.btn_drawer_close.setCursor(Qt.PointingHandCursor)
        self.btn_drawer_close.setToolTip("关闭笔记抽屉 (Esc)")
        self.btn_drawer_close.clicked.connect(self.close_note_drawer)
        head.addWidget(self.btn_drawer_close)
        box.addLayout(head)

        self.lbl_detail_seq = QLabel("")
        self.lbl_detail_seq.setObjectName("quizSeq")
        box.addWidget(self.lbl_detail_seq)

        kr_row = QHBoxLayout()
        self.lbl_detail_kr = QLabel("请选择单词")
        self.lbl_detail_kr.setObjectName("wordDisplay")
        self.lbl_detail_kr.setWordWrap(True)
        kr_row.addWidget(make_copyable(self.lbl_detail_kr), 1)

        self.btn_detail_speak = QPushButton("发音")
        self.btn_detail_speak.setObjectName("starToggle")
        self.btn_detail_speak.setIcon(icon("volume", TEXT_COLORS["muted"], 14))
        self.btn_detail_speak.setCursor(Qt.PointingHandCursor)
        self.btn_detail_speak.setFocusPolicy(Qt.NoFocus)
        self.btn_detail_speak.setToolTip("朗读韩语发音 (R)")
        self.btn_detail_speak.setEnabled(False)
        self.btn_detail_speak.clicked.connect(self.speak_current_word)
        kr_row.addWidget(self.btn_detail_speak)

        self.btn_star = QPushButton("标记重点")
        self.btn_star.setObjectName("starToggle")
        self.btn_star.setIcon(icon("star", TEXT_COLORS["muted"], 14))
        self.btn_star.setCursor(Qt.PointingHandCursor)
        self.btn_star.setFocusPolicy(Qt.NoFocus)
        self.btn_star.setToolTip("标记/取消重点星标")
        self.btn_star.clicked.connect(self.toggle_star)
        kr_row.addWidget(self.btn_star)
        box.addLayout(kr_row)

        self.lbl_detail_cn = QLabel("")
        self.lbl_detail_cn.setObjectName("wordMeaning")
        self.lbl_detail_cn.setWordWrap(True)
        box.addWidget(make_copyable(self.lbl_detail_cn))

        # 三态切换
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

        div = QFrame()
        div.setObjectName("paneDivider")
        div.setFrameShape(QFrame.HLine)
        div.setFrameShadow(QFrame.Plain)
        box.addWidget(div)

        # 个人笔记编辑区
        note_head = QHBoxLayout()
        note_label = QLabel("笔记内容")
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

        hint = QLabel("按 Esc 可随时收起抽屉 · 离开或切换自动保存")
        hint.setObjectName("faint")
        hint.setAlignment(Qt.AlignCenter)
        box.addWidget(hint)

        return drawer

    # ---------------------------------------------------------------- 过词

    def _build_pass_page(self):
        page = QWidget()
        page.setFocusPolicy(Qt.ClickFocus)
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 8, 0, 8)
        page_layout.setAlignment(Qt.AlignCenter)

        card = QFrame()
        card.setObjectName("vocabCard")
        card.setFocusPolicy(Qt.ClickFocus)
        card.setMinimumWidth(640)
        card.setMaximumWidth(760)
        card.setMinimumHeight(440)

        box = QVBoxLayout(card)
        box.setContentsMargins(28, 22, 28, 22)
        box.setSpacing(12)

        # 顶部极淡周边信息行：左侧词表名与序号，右侧过词进度
        head = QHBoxLayout()
        self.lbl_pass_meta = QLabel("")
        self.lbl_pass_meta.setObjectName("vocabCardMeta")
        head.addWidget(self.lbl_pass_meta)
        head.addStretch()
        self.lbl_pass_progress = QLabel("")
        self.lbl_pass_progress.setObjectName("vocabCardProgress")
        head.addWidget(self.lbl_pass_progress)
        box.addLayout(head)

        box.addStretch(1)

        # 中部定高容器：词长、释义长短、揭示前后的字号差都会改变这一块的自然高度，
        # 若直接排在上下两条 stretch 之间，变化会被平摊到两侧、按钮行跟着上下漂移
        # （用户反馈的"跳"）。定高并内部居中后，这块高度恒定，按钮行永不漂移。
        # 150px 覆盖「42px 词 + 两行释义」的极端情况；超出（罕见的长释义折三行）
        # 才允许容器继续长高。
        middle = QWidget()
        middle.setMinimumHeight(150)
        middle_box = QVBoxLayout(middle)
        middle_box.setContentsMargins(0, 0, 0, 0)
        middle_box.setSpacing(12)
        middle_box.addStretch(1)

        self.lbl_pass_seq = QLabel("")
        self.lbl_pass_seq.setObjectName("quizSeq")
        self.lbl_pass_seq.setAlignment(Qt.AlignCenter)
        middle_box.addWidget(self.lbl_pass_seq)

        self.lbl_pass_kr = QLabel("")
        self.lbl_pass_kr.setObjectName("quizWord")
        self.lbl_pass_kr.setAlignment(Qt.AlignCenter)
        self.lbl_pass_kr.setWordWrap(True)
        middle_box.addWidget(make_copyable(self.lbl_pass_kr))

        self.lbl_pass_cn = QLabel("")
        self.lbl_pass_cn.setObjectName("quizMeaning")
        self.lbl_pass_cn.setAlignment(Qt.AlignCenter)
        self.lbl_pass_cn.setWordWrap(True)
        middle_box.addWidget(make_copyable(self.lbl_pass_cn))

        middle_box.addStretch(1)
        box.addWidget(middle)

        box.addStretch(1)

        # 操作按钮区
        actions = QHBoxLayout()
        actions.setSpacing(10)
        actions.addStretch()

        self.btn_pass_speak = QPushButton("发音")
        self.btn_pass_speak.setObjectName("buttonOutline")
        self.btn_pass_speak.setIcon(icon("volume", TEXT_COLORS["secondary"], 15))
        self.btn_pass_speak.setCursor(Qt.PointingHandCursor)
        self.btn_pass_speak.setFocusPolicy(Qt.NoFocus)
        self.btn_pass_speak.setToolTip("朗读当前单词发音 (R / .)")
        self.btn_pass_speak.clicked.connect(self.speak_current_word)
        actions.addWidget(self.btn_pass_speak)

        self.btn_pass_undo = QPushButton("↩ 回退")
        self.btn_pass_undo.setObjectName("buttonOutline")
        self.btn_pass_undo.setCursor(Qt.PointingHandCursor)
        self.btn_pass_undo.setFocusPolicy(Qt.NoFocus)
        self.btn_pass_undo.setToolTip("回退上一个词 (Ctrl+Z)")
        self.btn_pass_undo.clicked.connect(self._undo_pass)
        self.btn_pass_undo.setEnabled(False)
        actions.addWidget(self.btn_pass_undo)

        self._judge_buttons = {}
        for key, label in (("known", "1　认识"), ("fuzzy", "2　模糊"), ("unknown", "3　不认识")):
            button = QPushButton(label)
            button.setObjectName("judgeButton")
            button.setFocusPolicy(Qt.NoFocus)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(lambda _checked, s=key: self._judge(s))
            actions.addWidget(button)
            self._judge_buttons[key] = button
        actions.addStretch()
        box.addLayout(actions)

        # 底部淡灰快捷键弱指引
        self.lbl_pass_hint = QLabel("1 认识 · 2 模糊 · 3 不认识 · Space / 0 揭示释义 · R / . 发音 · Ctrl+Z 回退")
        self.lbl_pass_hint.setObjectName("vocabCardHint")
        self.lbl_pass_hint.setAlignment(Qt.AlignCenter)
        box.addWidget(self.lbl_pass_hint)

        # 换词编排（绑定在本 View 上防 GC）：序号 → 大字 → 释义依次落位，
        # 卡片外框 / 按钮 / 进度条不动。参数唯一出处是 ui/motion.py。
        self._pass_entrance = (
            LabelMotion(self, self.lbl_pass_seq),
            LabelMotion(self, self.lbl_pass_kr),
            LabelMotion(self, self.lbl_pass_cn),
        )
        self._meaning_motion = self._pass_entrance[2]

        page_layout.addWidget(card)
        return page

    # ---------------------------------------------------------------- 专攻

    def _build_drill_page(self):
        page = QWidget()
        page.setFocusPolicy(Qt.ClickFocus)
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 8, 0, 8)
        outer.setAlignment(Qt.AlignCenter)

        self.drill_stack = QStackedWidget()
        outer.addWidget(self.drill_stack)

        card = QFrame()
        card.setObjectName("vocabCard")
        card.setFocusPolicy(Qt.ClickFocus)
        card.setMinimumWidth(640)
        card.setMaximumWidth(760)
        card.setMinimumHeight(440)
        box = QVBoxLayout(card)
        box.setContentsMargins(28, 22, 28, 22)
        box.setSpacing(10)

        # 顶部极淡周边信息行：左侧专攻进度，右侧保存提示与状态
        head = QHBoxLayout()
        self.lbl_drill_meta = QLabel("")
        self.lbl_drill_meta.setObjectName("vocabCardMeta")
        head.addWidget(self.lbl_drill_meta)
        head.addStretch()
        self.lbl_drill_progress = QLabel("")
        self.lbl_drill_progress.setObjectName("vocabCardProgress")
        head.addWidget(self.lbl_drill_progress)
        self.lbl_drill_saved = QLabel("")
        self.lbl_drill_saved.setObjectName("saveHint")
        head.addWidget(self.lbl_drill_saved)
        box.addLayout(head)

        box.addStretch(1)

        # 与过词卡同一套定高容器：专攻切词时释义可能从一行变两行，
        # 不定高就会把下方笔记区和按钮行一起顶下去。
        middle = QWidget()
        middle.setMinimumHeight(150)
        middle_box = QVBoxLayout(middle)
        middle_box.setContentsMargins(0, 0, 0, 0)
        middle_box.setSpacing(12)
        middle_box.addStretch(1)

        self.lbl_drill_seq = QLabel("")
        self.lbl_drill_seq.setObjectName("quizSeq")
        self.lbl_drill_seq.setAlignment(Qt.AlignCenter)
        middle_box.addWidget(self.lbl_drill_seq)

        self.lbl_drill_kr = QLabel("")
        self.lbl_drill_kr.setObjectName("quizWord")
        self.lbl_drill_kr.setAlignment(Qt.AlignCenter)
        self.lbl_drill_kr.setWordWrap(True)
        middle_box.addWidget(make_copyable(self.lbl_drill_kr))

        self.lbl_drill_cn = QLabel("")
        self.lbl_drill_cn.setObjectName("quizMeaning")
        self.lbl_drill_cn.setAlignment(Qt.AlignCenter)
        self.lbl_drill_cn.setWordWrap(True)
        middle_box.addWidget(make_copyable(self.lbl_drill_cn))

        middle_box.addStretch(1)
        box.addWidget(middle)

        # 专攻笔记输入区：恢复原先样式（直接放置 QTextEdit，无外框包装与标题）
        self.txt_drill_notes = QTextEdit()
        self.txt_drill_notes.setPlaceholderText("写下联想记忆——这一遍的目的就是让下次不用再攻它")
        self.txt_drill_notes.setMaximumHeight(100)
        self.txt_drill_notes.textChanged.connect(
            lambda: self._on_note_edited(self.txt_drill_notes, self.lbl_drill_saved)
        )
        box.addWidget(self.txt_drill_notes)

        box.addStretch(1)

        # 操作按钮区：加入发音与回退选项
        actions = QHBoxLayout()
        actions.setSpacing(10)
        actions.addStretch()

        self.btn_drill_speak = QPushButton("发音")
        self.btn_drill_speak.setObjectName("buttonOutline")
        self.btn_drill_speak.setIcon(icon("volume", TEXT_COLORS["secondary"], 15))
        self.btn_drill_speak.setCursor(Qt.PointingHandCursor)
        self.btn_drill_speak.setFocusPolicy(Qt.NoFocus)
        self.btn_drill_speak.setToolTip("朗读当前单词发音 (R / .)")
        self.btn_drill_speak.clicked.connect(self.speak_current_word)
        actions.addWidget(self.btn_drill_speak)

        self.btn_drill_undo = QPushButton("↩ 回退")
        self.btn_drill_undo.setObjectName("buttonOutline")
        self.btn_drill_undo.setCursor(Qt.PointingHandCursor)
        self.btn_drill_undo.setFocusPolicy(Qt.NoFocus)
        self.btn_drill_undo.setToolTip("回退上一个专攻的词 (Ctrl+Z)")
        self.btn_drill_undo.clicked.connect(self._undo_drill)
        self.btn_drill_undo.setEnabled(False)
        actions.addWidget(self.btn_drill_undo)

        for label, known in (("1　认识了", True), ("2　还是不会", False)):
            button = QPushButton(label)
            button.setObjectName("judgeButton")
            button.setFocusPolicy(Qt.NoFocus)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(lambda _checked, k=known: self._drill_judge(k))
            actions.addWidget(button)
        actions.addStretch()
        box.addLayout(actions)

        # 底部淡灰快捷键弱指引
        self.lbl_drill_hint = QLabel("1 认识了 · 2 还是不会 · R / . 发音 · Ctrl+Z 回退上一个")
        self.lbl_drill_hint.setObjectName("vocabCardHint")
        self.lbl_drill_hint.setAlignment(Qt.AlignCenter)
        box.addWidget(self.lbl_drill_hint)

        # 与过词卡对称：换词编排同样三层错峰，只滑内容不滑卡片
        self._drill_entrance = (
            LabelMotion(self, self.lbl_drill_seq),
            LabelMotion(self, self.lbl_drill_kr),
            LabelMotion(self, self.lbl_drill_cn),
        )

        card_wrapper = QWidget()
        card_w_layout = QVBoxLayout(card_wrapper)
        card_w_layout.setContentsMargins(0, 0, 0, 0)
        card_w_layout.setAlignment(Qt.AlignCenter)
        card_w_layout.addWidget(card)

        self.drill_stack.addWidget(card_wrapper)   # 0

        self.empty_drill = EmptyState("", "")
        self.empty_drill.action_clicked.connect(self._on_summary_action)
        self.drill_stack.addWidget(self.empty_drill)   # 1
        return page

    # ==================================================================
    # 键盘与发音
    # ==================================================================

    def _setup_shortcuts(self):
        # WindowShortcut：只要窗口在前台且当前处于过词/专攻，无需精准聚焦特定文本即可生效
        self._digit_shortcuts = []
        for key, state in _JUDGE_KEYS:
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.WindowShortcut)
            shortcut.activated.connect(lambda s=state: self._judge(s))
            self._digit_shortcuts.append(shortcut)

        self._reveal_shortcuts = []
        for key in (QKeySequence(Qt.Key_Space), QKeySequence(Qt.Key_0), QKeySequence(Qt.Key_Insert)):
            shortcut = QShortcut(key, self)
            shortcut.setContext(Qt.WindowShortcut)
            shortcut.activated.connect(self._toggle_reveal)
            self._reveal_shortcuts.append(shortcut)
        self._space_shortcut = self._reveal_shortcuts[0]

        self._undo_shortcut = QShortcut(QKeySequence.Undo, self)
        self._undo_shortcut.setContext(Qt.WindowShortcut)
        self._undo_shortcut.activated.connect(self._handle_undo)

        self._speak_shortcuts = []
        for key in (QKeySequence("R"), QKeySequence(".")):
            shortcut = QShortcut(key, self)
            shortcut.setContext(Qt.WindowShortcut)
            shortcut.activated.connect(self._handle_speak_shortcut)
            self._speak_shortcuts.append(shortcut)
        self._speak_shortcut = self._speak_shortcuts[0]

        # 2.6 的全局键里，`Ctrl+F` / `Ctrl+S` 落在有搜索框与笔记编辑区的模块上
        self._find_shortcut = QShortcut(QKeySequence.Find, self)
        self._find_shortcut.setContext(Qt.WindowShortcut)
        self._find_shortcut.activated.connect(self._focus_search)

        self._save_shortcut = QShortcut(QKeySequence.Save, self)
        self._save_shortcut.setContext(Qt.WindowShortcut)
        self._save_shortcut.activated.connect(self._save_now)

        # `Ctrl+C` 快捷键：作用域跟着表格与卡片网格焦点走
        self._copy_shortcut = QShortcut(QKeySequence.Copy, self.table)
        self._copy_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self._copy_shortcut.activated.connect(self.copy_current_word)

        if hasattr(self, "grid_view"):
            self._grid_copy_shortcut = QShortcut(QKeySequence.Copy, self.grid_view)
            self._grid_copy_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            self._grid_copy_shortcut.activated.connect(self.copy_current_word)

        self._sync_shortcuts()

    def _sync_shortcuts(self):
        visible = self.isVisible()
        active = visible and self._mode != self.MODE_BROWSE
        for shortcut in self._digit_shortcuts:
            shortcut.setEnabled(active)
        for shortcut in getattr(self, "_reveal_shortcuts", [self._space_shortcut]):
            shortcut.setEnabled(active and self._mode == self.MODE_PASS)
        self._undo_shortcut.setEnabled(active)
        for shortcut in getattr(self, "_speak_shortcuts", [getattr(self, "_speak_shortcut", None)]):
            if shortcut:
                shortcut.setEnabled(visible)

    def _keyboard_ok(self):
        """键盘只在过词/专攻生效，且不得抢走文本输入框的输入。"""
        if not self.isVisible() or self._mode == self.MODE_BROWSE:
            return False
        focus = QApplication.focusWidget()
        return not isinstance(focus, (QLineEdit, QTextEdit))

    def _handle_speak_shortcut(self):
        """快捷键 `R` / `.`：三个模式均可朗读当前词，但输入框聚焦时不拦截。"""
        if not self.isVisible():
            return
        focus = QApplication.focusWidget()
        if isinstance(focus, (QLineEdit, QTextEdit)):
            return
        self.speak_current_word()

    def _sync_auto_tts_button(self, enabled: bool):
        if not hasattr(self, "btn_auto_tts"):
            return
        self.btn_auto_tts.setChecked(bool(enabled))
        self.btn_auto_tts.setIcon(
            icon("volume", ACCENT if enabled else TEXT_COLORS["secondary"], 14)
        )

    def _on_auto_tts_toggled(self, checked: bool):
        self.database.set_tts_auto_play(checked)
        self._sync_auto_tts_button(checked)
        if checked and self._mode in (self.MODE_PASS, self.MODE_DRILL):
            self.speak_current_word()

    def speak_current_word(self):
        """朗读当前模式下的活跃单词。"""
        korean = ""
        if self._mode == self.MODE_BROWSE:
            entry = self._detail or self._selected_word_entry()
            korean = entry["korean"] if entry else ""
        elif self._mode == self.MODE_PASS:
            if 0 <= self._pass_index < len(self._pass_rows):
                korean = self._pass_rows[self._pass_index]["korean"]
        elif self._mode == self.MODE_DRILL:
            if 0 <= self._drill_index < len(self._drill_rows):
                korean = self._drill_rows[self._drill_index]["korean"]
        if korean:
            self.speaker.speak(korean, manual=True)

    def refresh_audio_device(self):
        """P5 音频输出设备变更时同步到单词发音播放器。"""
        self.speaker.refresh_audio_device()

    def _on_speaker_notice(self, level: str, message: str):
        if self.isVisible():
            show_toast(self.window(), message, level=level)

    def _handle_undo(self):
        """统一处理 Ctrl+Z 回退快捷键：过词与专攻均可回退。"""
        if not self._keyboard_ok():
            return
        if self._mode == self.MODE_PASS:
            self._undo_pass()
        elif self._mode == self.MODE_DRILL:
            self._undo_drill()

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

        self._check_and_prompt_tts_cache(list_id)

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
        in_browse = mode == self.MODE_BROWSE
        self.btn_load_tsv.setVisible(in_browse)
        self.search_input.setVisible(in_browse)
        if hasattr(self, "btn_auto_tts"):
            self.btn_auto_tts.setVisible(not in_browse)
            self._sync_auto_tts_button(self.database.get_tts_auto_play())
        self.banner.clear()
        self._sync_shortcuts()

        if mode == self.MODE_BROWSE:
            self.reload_browse()
        elif mode == self.MODE_PASS:
            self._pass_begin()
            self.setFocus()
        else:
            self._drill_begin()
            self.setFocus()

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
        """保留兼容钩子（删除入口已移至词表展开列表右侧）。"""
        btn = getattr(self, "btn_delete_list", None)
        if btn is not None:
            btn.setEnabled(self.current_list_id() is not None)

    def _on_list_order_changed(self, ordered_ids):
        """词表下拉列表拖动排序落库。"""
        if not ordered_ids:
            return
        self.database.reorder_word_lists(ordered_ids)
        self._lists = self.database.list_word_lists()
        # 若当前在「全部词表」下浏览，表格按词表顺序展示，同步刷新
        if self.current_list_id() is None and self._mode == self.MODE_BROWSE:
            self.reload_browse()

    def refresh(self):
        """数据被外部改动（导入备份、换资料目录）后重读一遍。"""
        self._flush_note()
        self._sync_auto_tts_button(self.database.get_tts_auto_play())
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

        # 先决定要不要自动下载，再切选择器。反序会让 _on_list_changed 先弹一次
        # "是否下载发音"的提示，紧接着又被下载进度顶掉——只闪一下就没有了。
        tts_note = self._maybe_auto_start_tts(result["list_id"])

        index = self.combo_list.findData(result["list_id"])
        if index >= 0:
            self.combo_list.setCurrentIndex(index)
        self.reload_browse()

        message = f"已载入 {result['count']} 个词条"
        if skipped:
            message += f"，跳过 {skipped} 行（列数不足或没有韩文）"
        if result["reused"]:
            message += "。这是重新导入，历史笔记、状态与过词断点均已保留。"
        message += tts_note
        self.banner.show_message("success", message)

    def delete_current_list(self, target_list_id=None):
        """删除指定的（或当前选中的）词表。**只删第一层**——笔记与三态原样保留。

        破坏性操作，所以弹一次模态确认。`Banner` / `show_toast` 只适合说"发生了什么"，
        不适合问"要不要做"；而模态框会造成"没有事件循环就永久阻塞"的那个老问题在这里
        不成立——确认框是从用户点击里弹出来的，事件循环正在运行。P5 的备份导入确认
        已经开了这个先例（`settings_view.py` 的 `QMessageBox.question`）。
        """
        list_id = target_list_id if isinstance(target_list_id, int) else self.current_list_id()
        if list_id is None:
            self.banner.show_message("info", "「全部词表」不是一个可删除的词表。")
            return
        name = next((row["name"] for row in self._lists if row["id"] == list_id), "") or self.current_list_name()
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

        was_current = (list_id == self.current_list_id())
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
        # 先处理后台下载：正在下载/排队的正是被删的这份时，词都没了，下载没有意义。
        if removed is not None:
            if self._batch_list_id == list_id and self._tts_busy():
                self._cancel_batch_tts_preload()
            elif list_id in self._tts_queue:
                self._tts_queue.remove(list_id)
                self._sync_batch_tts_button(self.current_list_id())
        self.refresh_lists()
        if was_current or self.current_list_id() is None:
            self.set_mode(self.MODE_BROWSE)
        elif self._mode == self.MODE_BROWSE:
            self.reload_browse()

    # ==================================================================
    # 浏览模式
    # ==================================================================

    def _configure_columns(self, show_source):
        """表格列随范围变化：「全部词表」多一列来源（流程 F 的 `来自：…`）。"""
        self._show_source = show_source
        self._offset = 1 if show_source else 0
        header = self.table.horizontalHeader()
        if show_source:
            self.table.setColumnCount(6)
            self.table.setHorizontalHeaderLabels(["词表", "编号", "韩语", "中文释义", "个人笔记", "状态"])
            for column in (0, 1):
                header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
            header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
            header.setSectionResizeMode(3, QHeaderView.Stretch)
            header.setSectionResizeMode(4, QHeaderView.Stretch)
            header.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        else:
            self.table.setColumnCount(5)
            self.table.setHorizontalHeaderLabels(["编号", "韩语", "中文释义", "个人笔记", "状态"])
            header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
            header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
            header.setSectionResizeMode(2, QHeaderView.Stretch)
            header.setSectionResizeMode(3, QHeaderView.Stretch)
            header.setSectionResizeMode(4, QHeaderView.ResizeToContents)

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
        if hasattr(self, "btn_detail_speak"):
            self.btn_detail_speak.setEnabled(False)

        self._apply_filter()

    def _append_row(self, row, source=""):
        index = self.table.rowCount()
        self.table.insertRow(index)
        offset = self._offset

        if self._show_source:
            self.table.setItem(index, 0, QTableWidgetItem(source or row.get("list_name", "")))

        item = QTableWidgetItem(str(row["seq"]))
        item.setTextAlignment(Qt.AlignCenter)
        self.table.setItem(index, offset, item)

        item = QTableWidgetItem(row["korean"])
        font = QFont("Pretendard", 12)
        font.setBold(True)
        item.setFont(font)
        self.table.setItem(index, offset + 1, item)

        if getattr(self, "_test_mode", False):
            m_item = QTableWidgetItem("••••••")
            m_item.setForeground(QColor(TEXT_COLORS["faint"]))
        else:
            m_item = QTableWidgetItem(row.get("meaning") or "")
        self.table.setItem(index, offset + 2, m_item)

        note_str = (row.get("note") or "").strip().replace("\n", " ")
        n_item = QTableWidgetItem(note_str)
        n_item.setForeground(QColor(TEXT_COLORS["muted"]))
        self.table.setItem(index, offset + 3, n_item)

        self._write_state_cell(index, row.get("state") or "")

    def _write_state_cell(self, index, state):
        """写状态列并把待专攻行上淡暖底。"""
        item = QTableWidgetItem(STATE_LABELS.get(state, "未过"))
        item.setTextAlignment(Qt.AlignCenter)
        item.setForeground(QColor(state_color(state)))
        self.table.setItem(index, self._offset + 4, item)

        background = QColor(ROW_DRILL_BG if state in _TO_DRILL else ROW_BASE_BG)
        for column in range(self.table.columnCount()):
            cell = self.table.item(index, column)
            if cell is not None:
                cell.setBackground(background)

    # ---------------------------------------------------------------- 复制与菜单

    def _selected_word_entry(self):
        """当前选中的词条行（网格或表格）。"""
        if getattr(self, "_view_mode", "grid") == "grid":
            if hasattr(self, "grid_view") and hasattr(self, "grid_model"):
                index = self.grid_view.currentIndex()
                if index.isValid():
                    return self.grid_model.word_at(index.row(), index.column())
            return None
        else:
            if hasattr(self, "table"):
                index = self.table.currentRow()
                if 0 <= index < len(getattr(self, "_filtered_rows", [])):
                    return self._filtered_rows[index]
            return None

    def copy_current_word(self, field="korean"):
        """把当前词条复制到剪贴板。"""
        entry = self._selected_word_entry() or self._detail
        if entry is None:
            return
        meaning = entry.get("meaning") or ""
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
        """表格右键菜单。"""
        index = self.table.indexAt(pos)
        if index.isValid():
            self.table.setCurrentCell(index.row(), index.column())
        entry = self._selected_word_entry()
        if not entry:
            return

        menu = QMenu(self)
        a_speak = menu.addAction("朗读发音 (R)")
        a_speak.triggered.connect(lambda: self.speaker.speak(entry["korean"], manual=True))
        menu.addSeparator()

        a_copy_kr = menu.addAction(f"复制韩语: {entry['korean']}")
        a_copy_cn = menu.addAction(f"复制释义: {entry.get('meaning', '')}")
        a_copy_both = menu.addAction("复制韩语与释义 (Tab 分隔)")
        menu.addSeparator()

        current_state = entry.get("state", "")
        for key, label in (("known", "认识 (1)"), ("fuzzy", "模糊 (2)"), ("unknown", "不认识 (3)")):
            action = menu.addAction(f"设为「{label}」")
            action.setCheckable(True)
            action.setChecked(current_state == key)
            action.triggered.connect(lambda _c, s=key, w=entry: self.set_word_state_for_word(w, s))

        menu.addSeparator()
        is_starred = bool(entry.get("is_starred"))
        a_star = menu.addAction("取消重点星标" if is_starred else "标记为重点星标")
        a_star.triggered.connect(lambda: self.toggle_star_for_word(entry))

        a_note = menu.addAction("编辑个人笔记 (N)")
        a_note.triggered.connect(lambda: self.open_note_drawer(entry))

        chosen = menu.exec(self.table.viewport().mapToGlobal(pos))
        if chosen == a_copy_kr:
            self.copy_current_word("korean")
        elif chosen == a_copy_cn:
            self.copy_current_word("meaning")
        elif chosen == a_copy_both:
            self.copy_current_word("both")

    def _grid_context_menu(self, pos):
        """卡片网格右键菜单。"""
        index = self.grid_view.indexAt(pos)
        if not index.isValid():
            return
        self.grid_view.setCurrentIndex(index)
        word = index.data(Qt.UserRole)
        if not word:
            return

        menu = QMenu(self)
        a_speak = menu.addAction("朗读发音 (R)")
        a_speak.triggered.connect(lambda: self.speaker.speak(word["korean"], manual=True))
        menu.addSeparator()

        a_copy_kr = menu.addAction(f"复制韩语: {word['korean']}")
        a_copy_cn = menu.addAction(f"复制释义: {word.get('meaning', '')}")
        a_copy_both = menu.addAction("复制韩语与释义 (Tab 分隔)")
        menu.addSeparator()

        current_state = word.get("state", "")
        for key, label in (("known", "认识 (1)"), ("fuzzy", "模糊 (2)"), ("unknown", "不认识 (3)")):
            action = menu.addAction(f"设为「{label}」")
            action.setCheckable(True)
            action.setChecked(current_state == key)
            action.triggered.connect(lambda _c, s=key, w=word: self.set_word_state_for_word(w, s))

        menu.addSeparator()
        is_starred = bool(word.get("is_starred"))
        a_star = menu.addAction("取消重点星标" if is_starred else "标记为重点星标")
        a_star.triggered.connect(lambda: self.toggle_star_for_word(word))

        a_note = menu.addAction("编辑个人笔记 (N)")
        a_note.triggered.connect(lambda: self.open_note_drawer(word))

        chosen = menu.exec(self.grid_view.viewport().mapToGlobal(pos))
        if chosen == a_copy_kr:
            self.copy_current_word("korean")
        elif chosen == a_copy_cn:
            self.copy_current_word("meaning")
        elif chosen == a_copy_both:
            self.copy_current_word("both")

    def _update_meta(self, list_id):
        total_rows = len(getattr(self, "_browse_rows", []))
        if list_id is None:
            self.lbl_meta.setText(
                f"全部词表 · 共 {total_rows} 个词条"
            )
            if hasattr(self, "btn_batch_tts"):
                self.btn_batch_tts.setVisible(False)
            return

        counts = self.database.list_counts(list_id)
        self.lbl_meta.setText(
            f"{self.current_list_name()} · {counts['total']} 个词 · "
            f"已过 {counts['passed']} · 待专攻 {counts['to_drill']}"
        )
        if hasattr(self, "btn_batch_tts"):
            self._sync_batch_tts_button(list_id)

    def _toggle_batch_tts_from_button(self):
        list_id = self.current_list_id()
        if list_id is None:
            return
        if self._tts_busy():
            if self._batch_list_id == list_id:
                self._cancel_batch_tts_preload()
            elif list_id in self._tts_queue:
                # 再点一次排队中的本表 = 取消排队
                self._tts_queue.remove(list_id)
                self.banner.show_message("info", f"《{self._list_name(list_id)}》已取消排队。")
                self._banner_action = None
                self._sync_batch_tts_button(list_id)
            else:
                # 后台在下载**别的**词表：点本表是排到队尾，不是抢占——抢占就是
                # 用户报过的"切个词表，原来那个下载就没了"
                self._enqueue_tts_preload(list_id)
        else:
            self._start_batch_tts_preload(list_id)

    def _tts_busy(self):
        return self._batch_tts_worker is not None and self._batch_tts_worker.isRunning()

    def _online_tts_enabled(self):
        """批量预缓存整套入口的闸：P5 显式开了联网发音才出现（D11：默认关闭、逐项开启）。"""
        return self.database.get_tts_mode() == "online"

    def _list_name(self, list_id):
        for row in self.database.list_word_lists():
            if row["id"] == list_id:
                return row["name"]
        return ""

    def _uncached_count(self, list_id):
        words_data = self.database.list_words(list_id)
        if not words_data:
            return 0
        voice = self.database.get_tts_voice()
        return count_uncached_words([row["korean"] for row in words_data], voice)

    def _batch_tts_tooltip(self, list_id):
        base = "预先下载本表全部未缓存的发音音频，以便完全离线使用"
        if not self._tts_busy():
            return base
        if self._batch_list_id == list_id:
            return "正在后台下载本表发音，点击停止；切换到别的词表也不会中断"
        name = self._list_name(self._batch_list_id) or "别的词表"
        if list_id in self._tts_queue:
            return f"已排入下载队列（当前在后台下载《{name}》），点击取消排队"
        return f"后台正在下载《{name}》，点击把本表排到队尾（不会中断当前下载）"

    def _sync_batch_tts_button(self, list_id):
        if not hasattr(self, "btn_batch_tts"):
            return
        if list_id is None:
            self.btn_batch_tts.setVisible(False)
            return
        # 批量预缓存是**联网工作流的一环**：本地模式下整块隐藏——联网默认关闭、
        # 逐项显式开启（PRODUCT_SPEC D11 方案 A）。用户在 P5 显式选了联网音质，
        # 这个下载入口才出现。
        if not self._online_tts_enabled():
            self.btn_batch_tts.setVisible(False)
            return

        button = self.btn_batch_tts
        button.setToolTip(self._batch_tts_tooltip(list_id))

        if self._tts_busy() and self._batch_list_id == list_id:
            # 正在下载本表：文案以进度回调为准，但进度还没到（刚启动/启动失败）时
            # 也得让按钮脱离静态态，不能留着"缓存发音 (N 词未存)"这种过期文案
            button.setVisible(True)
            if not button.text().startswith("下载中"):
                button.setText("下载中…")
                button.setEnabled(True)
                button.setIcon(icon("download"))
            return

        if self._tts_busy():
            # 后台在下载别的词表：本表只能排队（点一下入队，再点取消）
            if list_id in self._tts_queue:
                button.setVisible(True)
                button.setText(f"排队中 · 第 {self._tts_queue.index(list_id) + 1} 位")
                button.setEnabled(True)
                button.setIcon(icon("download"))
            else:
                uncached = self._uncached_count(list_id)
                if uncached == 0:
                    button.setVisible(False)
                else:
                    button.setVisible(True)
                    button.setText(f"排队下载（{uncached} 词未存）")
                    button.setEnabled(True)
                    button.setIcon(icon("download"))
            return

        uncached = self._uncached_count(list_id)
        if uncached == 0:
            # 词表发音已全部就绪时直接隐藏该按钮，保持工具条整洁
            button.setVisible(False)
        else:
            button.setVisible(True)
            button.setText(f"缓存发音 ({uncached} 词未存)")
            button.setEnabled(True)
            button.setIcon(icon("download"))

    def _check_and_prompt_tts_cache(self, list_id):
        """切换词表时：当前词表有未缓存单词、且没有下载在跑，弹条提示一键开始。

        已有下载在跑时**不提示**：本表在跑/排队时提示多余；后台在跑别表时提示会和
        进度横幅打架。排队入口在元信息栏那个按钮上（悬停有说明）。

        曾经的实现在这里 cancel 掉正在跑的下载——正是"切个词表下载就没了"的根因。
        """
        if list_id is None or not self._online_tts_enabled():
            return
        if self._tts_busy() or list_id in self._tts_queue:
            return

        uncached = self._uncached_count(list_id)
        if uncached > 0:
            list_name = self._list_name(list_id)
            self.banner.show_message(
                "info",
                f"《{list_name}》尚有 {uncached} 个单词未缓存发音，是否提前自动转换？",
                action_text="立即下载发音",
                action_icon="download",
            )
            self._banner_action = lambda: self._start_batch_tts_preload(list_id)

    def _maybe_auto_start_tts(self, list_id):
        """导入后按需自动开始预下载，返回拼进"已载入"提示的那句话（没启动则空串）。

        只在 P5 显式开了联网发音时才动（D11）。已有下载在跑就排队，不抢占。
        """
        if list_id is None or not self._online_tts_enabled():
            return ""
        uncached = self._uncached_count(list_id)
        if uncached == 0:
            return ""
        name = self._list_name(list_id)
        if self._tts_busy():
            self._enqueue_tts_preload(list_id, announce=False)
            return (
                f"。《{name}》有 {uncached} 个未缓存发音，已排入下载队列，"
                "当前下载完成后自动开始"
            )
        if self._start_batch_tts_preload(list_id, auto=True):
            return f"。《{name}》有 {uncached} 个未缓存发音，已开始后台下载（切词表不会中断）"
        return ""

    def _enqueue_tts_preload(self, list_id, announce=True):
        """排进后台下载队列。重复请求（正在跑或已在队里）忽略。"""
        if list_id is None:
            return False
        if list_id == self._batch_list_id or list_id in self._tts_queue:
            return False
        self._tts_queue.append(list_id)
        if announce:
            ahead = len(self._tts_queue) - 1
            tail = f"，前面还有 {ahead} 份" if ahead else ""
            self.banner.show_message(
                "info",
                f"《{self._list_name(list_id)}》已排入下载队列{tail}，当前下载完成后自动开始。",
                action_text="停止",
                action_icon="x",
            )
            self._banner_action = self._cancel_batch_tts_preload
        self._sync_batch_tts_button(self.current_list_id())
        return True

    def _start_next_queued_tts(self):
        """取出队列里下一份还有未缓存词的词表并开始。返回是否真的启动了。"""
        while self._tts_queue:
            list_id = self._tts_queue.pop(0)
            if list_id == self._batch_list_id or not self._list_name(list_id):
                continue  # 正在跑（不该发生）/ 词表已删
            if not self._uncached_count(list_id):
                continue  # 排队期间被别的入口下载完了，别为空跑再起一个线程
            if self._start_batch_tts_preload(list_id):
                return True
        return False

    def _start_batch_tts_preload(self, list_id, auto=False):
        """开始后台预下载一份词表的发音。

        **不抢占**：已有下载在跑时改为排队。曾经的实现进来就 cancel 前一个，
        表现为"切个词表 / 导个新词表，原来那个下载就没了"。
        `auto=True` 表示调用方已经说明了来意（导入完成的提示、队列接力），
        这里不再重复弹一次"准备中"横幅。
        """
        if list_id is None or not self._online_tts_enabled():
            return False
        if self._tts_busy():
            return self._enqueue_tts_preload(list_id, announce=not auto)

        words_data = self.database.list_words(list_id)
        if not words_data:
            return False
        raw_words = [row["korean"] for row in words_data]
        voice = self.database.get_tts_voice()
        azure_key = self.database.get_azure_speech_key()
        azure_region = self.database.get_azure_speech_region()
        list_name = self._list_name(list_id)

        worker = BatchTTSPreloadWorker(
            raw_words,
            voice=voice,
            azure_key=azure_key,
            azure_region=azure_region,
            parent=self,
        )
        self._batch_tts_worker = worker
        self._batch_list_id = list_id

        # 把 list_id 一起带进回调：后台下载下"正在下载的那一份"和"当前选中的
        # 那一份"经常不是同一份，按钮与横幅都得按 list_id 说话。
        worker.progress.connect(
            lambda done, total, word: self._on_batch_tts_progress(
                done, total, word, list_id, list_name
            )
        )
        worker.finished.connect(
            lambda downloaded, total: self._on_batch_tts_finished(
                downloaded, total, list_id, list_name
            )
        )
        worker.error.connect(
            lambda err: self._on_batch_tts_error(err, list_id, list_name)
        )

        if not auto:
            queue_note = f"，队列里还有 {len(self._tts_queue)} 份" if self._tts_queue else ""
            self.banner.show_message(
                "info",
                f"正在后台预下载《{list_name}》发音：准备中…"
                f"（切换到别的词表也会继续{queue_note}）",
                action_text="停止",
                action_icon="x",
            )
            self._banner_action = self._cancel_batch_tts_preload
        if hasattr(self, "btn_batch_tts") and self.current_list_id() == list_id:
            self.btn_batch_tts.setText("下载中…")
            self.btn_batch_tts.setEnabled(True)

        worker.start()
        return True

    def _on_batch_tts_progress(self, done, total, word, list_id, list_name):
        if total <= 0:
            return
        pct = int(done * 100 / total)
        queue_note = f" · 排队 {len(self._tts_queue)} 份" if self._tts_queue else ""
        self.banner.show_message(
            "info",
            f"正在后台预下载《{list_name}》发音：{done}/{total}（{pct}%）· {word}…{queue_note}",
            action_text="停止",
            action_icon="x",
        )
        self._banner_action = self._cancel_batch_tts_preload
        if hasattr(self, "btn_batch_tts") and self.current_list_id() == list_id:
            self.btn_batch_tts.setText(f"下载中 {done}/{total}")

    def _on_batch_tts_finished(self, downloaded, total, list_id, list_name):
        self._batch_tts_worker = None
        self._batch_list_id = None
        self.banner.show_message(
            "success",
            f"《{list_name}》发音预下载完成（新增缓存 {downloaded} 词，共 {total} 词），离线可流畅秒播。",
        )
        self._banner_action = None
        show_toast(self.window(), f"《{list_name}》发音已全部就绪", level="success")
        # 队列接力：下一份自动开始，用户不用守在这页
        self._start_next_queued_tts()
        self._sync_batch_tts_button(self.current_list_id())

    def _on_batch_tts_error(self, err, list_id, list_name):
        self._batch_tts_worker = None
        self._batch_list_id = None
        dropped = len(self._tts_queue)
        self._tts_queue.clear()
        message = f"预下载《{list_name}》发音中断：{err}"
        if dropped:
            message += f"。排队中的 {dropped} 份词表一并取消——网络不通时逐个重试只是重复失败"
        self.banner.show_message(
            "warning",
            message,
            action_text="重试",
            action_icon="refresh",
        )
        # 重试**失败的那一份**：后台下载下它和"当前选中的词表"经常不是同一份
        self._banner_action = lambda: self._start_batch_tts_preload(list_id)
        self._sync_batch_tts_button(self.current_list_id())

    def _cancel_batch_tts_preload(self):
        name = ""
        if self._batch_tts_worker is not None and self._batch_tts_worker.isRunning():
            name = self._list_name(self._batch_list_id) if self._batch_list_id else ""
            self._batch_tts_worker.cancel()
            self._batch_tts_worker.wait(1500)
        dropped = len(self._tts_queue)
        self._batch_tts_worker = None
        self._batch_list_id = None
        self._tts_queue.clear()

        if name or dropped:
            # "停止"是用户点下来的，得告诉他停的是什么、顺带取消了哪些排队
            message = f"已停止《{name}》的发音预下载。" if name else "已停止发音预下载。"
            if dropped:
                message += f"排队中的 {dropped} 份词表一并取消。"
            self.banner.show_message("info", message)
            self._banner_action = None
        else:
            self.banner.clear()
        self._sync_batch_tts_button(self.current_list_id())

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
            self._show_active_view()

    def _on_search_changed(self, text):
        self._apply_filter(text)

    def _apply_filter(self, text=None):
        needle = (self.search_input.text() if text is None else text).strip().lower()
        state_filter = getattr(self, "_state_filter", "all")

        filtered = []
        for row in getattr(self, "_browse_rows", []):
            state = row.get("state", "") or ""
            if state_filter == "unknown" and state != "unknown":
                continue
            elif state_filter == "fuzzy" and state != "fuzzy":
                continue
            elif state_filter == "known" and state != "known":
                continue

            if needle:
                kr = row.get("korean", "").lower()
                cn = (row.get("meaning") or "").lower()
                nt = (row.get("note") or "").lower()
                if needle not in kr and needle not in cn and needle not in nt:
                    continue

            filtered.append(row)

        self._filtered_rows = filtered

        # 刷新卡片网格
        if hasattr(self, "grid_model"):
            self.grid_model.set_words(filtered)

        # 刷新表格
        if hasattr(self, "table"):
            self.table.setRowCount(0)
            for row in filtered:
                self._append_row(row, row.get("list_name", ""))

        list_id = self.current_list_id()
        self._update_meta(list_id)

        if not self._browse_rows:
            self._show_browse_empty(list_id)
        elif not filtered:
            self._show_empty(
                "没有找到符合筛选条件的词条",
                "可尝试切换状态筛选胶囊或清除搜索框文字。",
                action_text="重置筛选",
                action_icon="x",
                glyph="search",
                handler=self._reset_filters,
            )
        else:
            self._show_active_view()

    def _show_active_view(self):
        if getattr(self, "_view_mode", "grid") == "grid":
            self.left_stack.setCurrentIndex(0)
        else:
            self.left_stack.setCurrentIndex(1)

    def _set_browse_view_mode(self, mode):
        prev_word = self._selected_word_entry() or self._detail
        self._view_mode = mode
        if hasattr(self, "btn_view_grid"):
            self.btn_view_grid.setChecked(mode == "grid")
        if hasattr(self, "btn_view_table"):
            self.btn_view_table.setChecked(mode == "table")
        if getattr(self, "_filtered_rows", []):
            self._show_active_view()
            if prev_word:
                korean = prev_word.get("korean", "")
                if mode == "grid" and hasattr(self, "grid_view") and hasattr(self, "grid_model"):
                    idx = self.grid_model.index_for_word(korean)
                    if idx.isValid():
                        self.grid_view.setCurrentIndex(idx)
                        self.grid_view.scrollTo(idx)
                elif mode == "table" and hasattr(self, "table"):
                    offset = self._offset
                    for r in range(self.table.rowCount()):
                        item = self.table.item(r, offset + 1)
                        if item and item.text() == korean:
                            self.table.setCurrentItem(item)
                            self.table.scrollToItem(item)
                            break

    def _on_filter_pill_clicked(self, key):
        self._state_filter = key
        self._apply_filter()

    def _reset_filters(self):
        self.search_input.clear()
        if hasattr(self, "_filter_pills") and "all" in self._filter_pills:
            self._filter_pills["all"].setChecked(True)
        self._state_filter = "all"
        self._apply_filter()

    def _on_test_mode_toggled(self, checked):
        self._test_mode = checked
        if hasattr(self, "grid_delegate"):
            self.grid_delegate.set_test_mode(checked)
            self.grid_view.viewport().update()

        if hasattr(self, "table"):
            offset = self._offset
            curr_row = self.table.currentRow()
            for r_idx, row in enumerate(getattr(self, "_filtered_rows", [])):
                cell = self.table.item(r_idx, offset + 2)
                if cell is not None:
                    if checked and r_idx != curr_row:
                        cell.setText("••••••")
                        cell.setForeground(QColor(TEXT_COLORS["faint"]))
                    else:
                        cell.setText(row.get("meaning") or "")
                        cell.setForeground(QColor(TEXT_COLORS["primary"]))

    def _on_grid_selection_changed(self):
        self.grid_view.viewport().update()
        if hasattr(self, "drawer") and self.drawer.isVisible():
            word = self._selected_word_entry()
            if word:
                self.open_note_drawer(word)

    def _on_table_selection_changed(self):
        if getattr(self, "_test_mode", False):
            offset = self._offset
            curr = self.table.currentRow()
            for r_idx in range(self.table.rowCount()):
                cell = self.table.item(r_idx, offset + 2)
                if cell is not None and r_idx < len(self._filtered_rows):
                    if r_idx == curr:
                        cell.setText(self._filtered_rows[r_idx].get("meaning") or "")
                        cell.setForeground(QColor(TEXT_COLORS["primary"]))
                    else:
                        cell.setText("••••••")
                        cell.setForeground(QColor(TEXT_COLORS["faint"]))

        if hasattr(self, "drawer") and self.drawer.isVisible():
            word = self._selected_word_entry()
            if word:
                self.open_note_drawer(word)

    def _on_grid_action_clicked(self, action, word):
        if action == "tts":
            self.speaker.speak(word["korean"], manual=True)
        elif action == "star":
            self.toggle_star_for_word(word)
        elif action == "note":
            self.open_note_drawer(word)
        elif action == "state":
            curr = word.get("state", "") or ""
            cycle = {"": "known", "known": "fuzzy", "fuzzy": "unknown", "unknown": ""}
            next_state = cycle.get(curr, "known")
            self.set_word_state_for_word(word, next_state if next_state else None)

    def open_note_drawer(self, word=None):
        if word is None:
            word = self._selected_word_entry()
        if not word:
            return
        self._flush_note()
        self._detail = word

        if hasattr(self, "lbl_detail_seq"):
            self.lbl_detail_seq.setText(f"第 {word.get('seq', '')} 个")
        if hasattr(self, "lbl_detail_kr"):
            self.lbl_detail_kr.setText(word.get("korean", ""))
        if hasattr(self, "lbl_detail_cn"):
            self.lbl_detail_cn.setText(word.get("meaning", "") or "")
        if hasattr(self, "btn_detail_speak"):
            self.btn_detail_speak.setEnabled(True)

        note_data = self.database.get_word_note(word["korean"])
        self.txt_notes.blockSignals(True)
        self.txt_notes.setPlainText(note_data["note"] if note_data else "")
        self.txt_notes.blockSignals(False)
        self.txt_notes.setProperty("wordKey", word["korean"])
        self.lbl_browse_saved.setText("")

        self._sync_state_buttons(note_data["state"] if note_data else word.get("state"))
        self._sync_star_button(bool(note_data["is_starred"]) if note_data else bool(word.get("is_starred")))

        self.drawer.setVisible(True)
        total = self.splitter.width()
        if total > 500:
            self.splitter.setSizes([max(300, total - 340), 340])
        self.txt_notes.setFocus()

    def close_note_drawer(self):
        self._flush_note()
        self.drawer.setVisible(False)
        self.splitter.setSizes([self.splitter.width(), 0])
        if getattr(self, "_view_mode", "grid") == "grid":
            self.grid_view.setFocus()
        else:
            self.table.setFocus()

    def show_details(self):
        if hasattr(self, "drawer") and self.drawer.isVisible():
            word = self._selected_word_entry()
            if word:
                self.open_note_drawer(word)

    def _sync_state_buttons(self, state):
        for key, button in self._state_buttons.items():
            button.setChecked(key == state)

    def _on_detail_state(self, state):
        """抽屉里手动改状态。"""
        if self._detail is None:
            return
        self.set_word_state_for_word(self._detail, state)

    def set_word_state_for_word(self, word, state):
        korean = word["korean"]
        note = self.database.get_word_note(korean)
        current = (note["state"] if note else None) or word.get("state")
        new_state = None if current == state else state

        self.database.set_word_state(korean, new_state)
        self._sync_state_buttons(new_state)
        self._repaint_active_row(korean)
        self._update_meta(self.current_list_id())

    def toggle_star(self):
        entry = self._detail or self._selected_word_entry()
        if entry is None:
            return
        self.toggle_star_for_word(entry)

    def toggle_star_for_word(self, word):
        korean = word["korean"]
        note = self.database.get_word_note(korean)
        current_text = (
            self.txt_notes.toPlainText()
            if self._detail and self._detail.get("korean") == korean
            else (note["note"] if note else "")
        )
        is_starred = not bool(note["is_starred"]) if note else True
        self.database.save_word_note(korean, current_text, is_starred)
        self._pending_editor = self._pending_hint = None
        self._sync_star_button(is_starred)
        self._repaint_active_row(korean)
        self._update_meta(self.current_list_id())

    def _on_quick_digit_key(self, state):
        word = self._selected_word_entry()
        if word:
            self.set_word_state_for_word(word, state)

    def _repaint_active_row(self, korean):
        note = self.database.get_word_note(korean)
        state = (note["state"] if note else None) or ""
        is_starred = int(note["is_starred"]) if note else 0
        note_text = (note["note"] if note else "") or ""

        for r in self._browse_rows:
            if r["korean"] == korean:
                r["state"] = state
                r["is_starred"] = is_starred
                r["note"] = note_text
                break
        for r in getattr(self, "_filtered_rows", []):
            if r["korean"] == korean:
                r["state"] = state
                r["is_starred"] = is_starred
                r["note"] = note_text
                break

        offset = self._offset
        for row_idx in range(self.table.rowCount()):
            kr_item = self.table.item(row_idx, offset + 1)
            if kr_item is not None and kr_item.text() == korean:
                n_item = self.table.item(row_idx, offset + 3)
                if n_item is not None:
                    n_item.setText(note_text.strip().replace("\n", " "))
                self._write_state_cell(row_idx, state)
                break

        if hasattr(self, "grid_view"):
            self.grid_view.viewport().update()

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
            if hasattr(self, "btn_pass_speak"):
                self.btn_pass_speak.setEnabled(False)
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
        # 进入过词模式的第一个词也走一遍编排：切模式是个"用户主动 paced"的时刻
        self._play_entrance(self._pass_entrance, direction=1)

    def _show_pass_current(self):
        finished = self._pass_index >= len(self._pass_rows)
        self.lbl_pass_seq.setText("")
        self.lbl_pass_kr.setText("这一遍过完了" if finished else "")
        for button in self._judge_buttons.values():
            button.setEnabled(not finished)
        if hasattr(self, "btn_pass_speak"):
            self.btn_pass_speak.setEnabled(not finished)
        if hasattr(self, "btn_pass_undo"):
            self.btn_pass_undo.setEnabled(bool(self._undo_stack))

        if finished:
            if hasattr(self, "lbl_pass_meta"):
                self.lbl_pass_meta.setText(f"《{self.current_list_name()}》")
            self.lbl_pass_cn.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.lbl_pass_cn.unsetCursor()
            restyle(self.lbl_pass_cn, "quizMeaning")
            self.lbl_pass_cn.setText(
                "切到「专攻」去攻被标出来的词，或者直接再过一遍。"
            )
        else:
            row = self._pass_rows[self._pass_index]
            if hasattr(self, "lbl_pass_meta"):
                self.lbl_pass_meta.setText(f"《{self.current_list_name()}》")

            self.lbl_pass_seq.setText(f"第 {row['seq']} 个")
            self.lbl_pass_kr.setText(row["korean"])
            self._revealed = False
            self._render_pass_meaning()
            if self.database.get_tts_auto_play() and self.isVisible():
                self.speaker.speak(row["korean"])

        self._update_pass_progress()

    def _render_pass_meaning(self):
        """释义默认遮住（D14 推荐）：这一遍是自测，先看答案再看题就没有意义了。"""
        if self._pass_index >= len(self._pass_rows):
            return
        row = self._pass_rows[self._pass_index]
        meaning_display = row["meaning"] or "（这份词表没有释义）"
        self.lbl_pass_cn.setCursor(Qt.PointingHandCursor)
        if self._revealed:
            self.lbl_pass_cn.setTextInteractionFlags(Qt.TextSelectableByMouse)
            restyle(self.lbl_pass_cn, "quizMeaning")
            self.lbl_pass_cn.setText(meaning_display)
        else:
            self.lbl_pass_cn.setTextInteractionFlags(Qt.NoTextInteraction)
            restyle(self.lbl_pass_cn, "faint")
            self.lbl_pass_cn.setText("释义已遮住 · 点击或按 Space / 0 揭示")

    @staticmethod
    def _play_entrance(entrance, direction=1):
        """换词编排：序号 → 大字 → 释义依次淡入落位（`ui/motion.py` 定参）。

        三个都是**内容叶子**；卡片外框、按钮、进度条完全静止——那层静止的
        chrome 是锚，约 180ms 的动效有了它才明显而不吵。

        `direction=1` 从右侧入场（向前过词），`-1` 镜像（回退）。
        连按时下一次从各标签的静止点重播（`LabelMotion` 保证不漂移），
        输入永远不等动画——这是它敢出现在高频路径上的前提。
        """
        seq_motion, word_motion, meaning_motion = entrance
        seq_motion.play(dx=DX_SEQ * direction, duration=DUR_MICRO)
        word_motion.play(dx=DX_WORD * direction, duration=DUR_BASE, delay=DELAY_WORD)
        meaning_motion.play(
            dx=DX_MEANING * direction, duration=DUR_SOFT, delay=DELAY_MEANING
        )

    def _toggle_reveal(self):
        if not self._keyboard_ok() or self._pass_index >= len(self._pass_rows):
            return
        self._revealed = not self._revealed
        self._render_pass_meaning()
        if self._revealed:
            # 揭示是用户主动 paced 的动作，值得全卡最隆重的一次：释义上移淡入
            self._meaning_motion.play(dy=-DY_REVEAL, duration=DUR_REVEAL)

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

        if previous_state is None:
            self.database.note_vocab_progress(
                "vocab_triaged", list_id=list_id, list_name=self.current_list_name()
            )

        row["state"] = state
        self._pass_index += 1
        self._show_pass_current()
        if hasattr(self, "btn_pass_undo"):
            self.btn_pass_undo.setEnabled(bool(self._undo_stack))
        # 状态先落库、文字先换好，动画只负责"落位"——不阻塞、不等待
        self._play_entrance(self._pass_entrance, direction=1)

    def _undo_pass(self):
        """`Ctrl+Z` 退回上一个词（流程 B 第 7 条）：状态与断点一起还原。"""
        if not self._undo_stack:
            return
        korean, previous_state, previous_cursor = self._undo_stack.pop()
        list_id = self.current_list_id()
        self.database.pass_word(list_id, previous_cursor, korean, previous_state)
        if previous_state is None:
            self.database.note_vocab_progress(
                "vocab_triaged", list_id=list_id, list_name=self.current_list_name(), delta=-1
            )
        self._pass_index = max(0, self._pass_index - 1)
        if self._pass_index < len(self._pass_rows):
            self._pass_rows[self._pass_index]["state"] = previous_state or ""
        self._show_pass_current()
        if hasattr(self, "btn_pass_undo"):
            self.btn_pass_undo.setEnabled(bool(self._undo_stack))
        # 回退整套镜像：从左侧入场，方向本身就是"退回去了"的反馈
        self._play_entrance(self._pass_entrance, direction=-1)

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
        self._drill_undo_stack = []
        if not self._drill_rows:
            self._show_drill_summary(first_time=True)
            return
        self.drill_stack.setCurrentIndex(0)
        self._show_drill_current()
        self._sync_drill_undo_button()
        # 同上：进入专攻的首词也落位一次
        self._play_entrance(self._drill_entrance, direction=1)

    def _show_drill_current(self):
        if self._drill_index >= len(self._drill_rows):
            self._finish_drill_round()
            return
        row = self._drill_rows[self._drill_index]
        if hasattr(self, "lbl_drill_meta"):
            self.lbl_drill_meta.setText(f"《{self.current_list_name()}》")

        state_text = "不认识" if row["state"] == "unknown" else "模糊"
        self.lbl_drill_seq.setText(f"第 {row['seq']} 个 · {state_text}")
        self.lbl_drill_kr.setText(row["korean"])
        self.lbl_drill_cn.setText(row["meaning"] or "（这份词表没有释义）")

        note = self.database.get_word_note(row["korean"])
        self.txt_drill_notes.blockSignals(True)
        self.txt_drill_notes.setPlainText(note["note"] if note else "")
        self.txt_drill_notes.blockSignals(False)
        self.txt_drill_notes.setProperty("wordKey", row["korean"])
        self.lbl_drill_saved.setText("")
        self._update_drill_progress()
        self._sync_drill_undo_button()
        if self.database.get_tts_auto_play() and self.isVisible():
            self.speaker.speak(row["korean"])

    def _drill_judge(self, known):
        """专攻的两个动作。`认识了` 出列；`还是不会` 原样留在待专攻里。"""
        if self._drill_index >= len(self._drill_rows):
            return
        self._flush_note()
        row = self._drill_rows[self._drill_index]
        korean = row["korean"]
        prev_state = row["state"]
        if not hasattr(self, "_drill_undo_stack"):
            self._drill_undo_stack = []
        self._drill_undo_stack.append((self._drill_index, korean, prev_state, known))
        if known:
            self.database.set_word_state(korean, "known")
            self.database.note_vocab_progress(
                "vocab_drilled",
                list_id=self.current_list_id(),
                list_name=self.current_list_name(),
            )
            row["cleared"] = True
            self._drill_cleared += 1
        self._drill_index += 1
        self._show_drill_current()
        self._sync_drill_undo_button()
        # 与过词同一套编排；笔记区（QTextEdit）不参与——文本编辑器动起来只会碍事
        self._play_entrance(self._drill_entrance, direction=1)

    def _undo_drill(self):
        """回退上一个专攻的词 (Ctrl+Z 或 点击回退按钮)。"""
        if not getattr(self, "_drill_undo_stack", None):
            return
        self._flush_note()
        idx, korean, prev_state, was_known = self._drill_undo_stack.pop()
        list_id = self.current_list_id()
        if was_known:
            self.database.set_word_state(korean, prev_state)
            self.database.note_vocab_progress(
                "vocab_drilled",
                list_id=list_id,
                list_name=self.current_list_name(),
                delta=-1,
            )
            self._drill_cleared = max(0, self._drill_cleared - 1)
            if idx < len(self._drill_rows):
                self._drill_rows[idx]["cleared"] = False
        self._drill_index = idx
        if self.drill_stack.currentIndex() != 0:
            self.drill_stack.setCurrentIndex(0)
        self._show_drill_current()
        self._sync_drill_undo_button()
        # 与过词回退对称：整套镜像，从左侧入场
        self._play_entrance(self._drill_entrance, direction=-1)

    def _sync_drill_undo_button(self):
        if hasattr(self, "btn_drill_undo"):
            self.btn_drill_undo.setEnabled(bool(getattr(self, "_drill_undo_stack", None)))

    def _finish_drill_round(self):
        list_id = self.current_list_id()
        if not self._drill_round_counted:
            self.database.mark_drill_round(list_id)
            self._drill_round_counted = True
        self._sync_drill_undo_button()
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
        self._repaint_active_row(korean)

    def flush_pending(self):
        """关窗口前冲刷未落库的笔记（`MainWindow.closeEvent` 调用）。"""
        self._flush_note()

    def shutdown(self):
        """关窗口时停止播放并等待后台发音线程结束。"""
        self._cancel_batch_tts_preload()
        self.speaker.shutdown()

    def showEvent(self, event):
        super().showEvent(event)
        self._sync_shortcuts()

    def hideEvent(self, event):
        # 切到别的页面也要冲刷：本页不是当前页时，防抖定时器还要再等 1.5 秒
        self._flush_note()
        self.speaker.stop()
        for shortcut in getattr(self, "_digit_shortcuts", []):
            shortcut.setEnabled(False)
        for shortcut in getattr(self, "_reveal_shortcuts", []):
            shortcut.setEnabled(False)
        if hasattr(self, "_space_shortcut"):
            self._space_shortcut.setEnabled(False)
        if hasattr(self, "_undo_shortcut"):
            self._undo_shortcut.setEnabled(False)
        for shortcut in getattr(self, "_speak_shortcuts", []):
            shortcut.setEnabled(False)
        if hasattr(self, "_speak_shortcut"):
            self._speak_shortcut.setEnabled(False)
        super().hideEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape and hasattr(self, "drawer") and self.drawer.isVisible():
            self.close_note_drawer()
            event.accept()
            return
        super().keyPressEvent(event)

    # ==================================================================
    # 空状态与提示条
    # ==================================================================

    def _show_empty(self, title, description="", action_text=None, action_icon=None,
                    glyph="folder-open", handler=None):
        self.empty.set_content(title, description, action_text, action_icon, glyph)
        self._empty_action = handler
        self.left_stack.setCurrentIndex(2)

    def _show_table(self):
        self._show_active_view()

    def _on_empty_action(self):
        if self._empty_action:
            self._empty_action()

    def _on_banner_action(self):
        if self._banner_action:
            self._banner_action()
