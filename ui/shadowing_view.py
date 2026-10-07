"""P2 · 影子跟读（`PRODUCT_SPEC` 4.3）。

## 这一页的形状

```
H1 页头
H2 材料准备条：导入音频 / 导入韩文 TXT / 导入中文 TXT / 转换 MKV·MP4
               第二行放状态文字（挤在一行里会被截成半截）
H3 分栏 ── 左：播放列表（可折叠，D16 方案 B）
          右：双语 PDF 双栏（上）+ 播放控制卡（下，D3 方案 A）
```

## 音频是"库 + 列表"，不是"一次载入一个文件"（v1.2）

原实现只有一个「载入音频」按钮，隐含"一次只听一段"的假设，与"跟读是连续的日常行为"
不符。现在：导入 = **复制进工具音频库**（`core/audio.py`，D15 方案 A），曲目进播放列表，
每段各有自己的 AB 点、语速、播放位置与状态。

**源文件永远是只读的。** 复制是唯一会发生的动作——"绝不修改、移动、删除用户资料"
是 1.2 的铁律，复制不违反它。

## 对照文本用**现成的 PDF**（2026-10-06 验收修订）

原设计是每段曲目配一份手打/导入的 TXT。用户的实际材料本来就是 PDF（一份原文、
一份解析），而且**按播放列表成套存在**（"96 届听力"列表 ↔ 96 听力原文 + 96 听力解析）。
所以对照区改成两份 PDF 阅读面板，挂在**播放列表**上，不挂在曲目上。

PDF **只记路径、不复制**：它们本来就躺在用户的资料树里（P3 也是这样索引 PDF 的），
几十上百 MB 的副本没有意义。代价是文件被移走时会失效——面板会明说"文件不在了"。

## 两个"循环"是不同的层次（4.3 专门点名，容易混淆）

| 层次 | 作用范围 | 控件 |
|---|---|---|
| **AB 循环** | 当前曲目内的一段区间反复播放 | 设为 A 点 / 设为 B 点 |
| **列表循环** | 整个列表的推进方式：顺序 / 列表循环 / 单曲循环 | 循环模式下拉 |

AB 生效时播到 B 点回到 A 点，**不会**推进到下一段——用户正是在死磕这一句。

## 跟读时长的定义（4.3 数据依赖）

* 只统计**处于播放状态**的时间；暂停、拖动进度条的时间不计
* **正向前进跳转不计**——跳过的部分没有真的听（所以每次跳转都要挂起增量记账，
  否则把滑块一拖到底就会凭空长出几分钟）
* AB 循环内的重复播放**计入**（D5 方案 A：重复跟读正是有效学习），另记循环次数
* 粒度：活动日志按**整分钟**；曲目自己的 `listened_ms` 按毫秒累计
* 触发写入：暂停 / 切曲 / 离开页面 / 关窗口——**四条路径缺一条就会丢时间**

## 退出路径上必须做的事

`flush_study_session()` 是这一页对外的**唯一**冲刷入口：它落跟读时长、播放位置、
AB 点、语速与曲目状态。`MainWindow.closeEvent` 调它，`hideEvent`（离开页面）也调它。
加新的出口就调它，不要另起一套。
"""

import os
import re
import shlex
import time
from datetime import datetime

from PySide6.QtCore import QPointF, QSize, QThread, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QKeySequence, QShortcut
from PySide6.QtMultimedia import (
    QAudioInput,
    QAudioOutput,
    QMediaCaptureSession,
    QMediaDevices,
    QMediaFormat,
    QMediaPlayer,
    QMediaRecorder,
)
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPdfWidgets import QPdfView
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSplitter,
    QStackedWidget,
    QStyle,
    QStyleOptionSlider,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core import audio
from core.config import AUDIO_DIR
from ui.components import Banner, EmptyState, make_copyable, show_toast
from ui.icons import icon
from ui.style import restyle
from ui.theme import STATE_COLORS, TEXT_COLORS

# 曲目状态点的**形状 + 文字**。§9.3 要求不靠颜色单独承载语义，所以两样都给。
# `None` 是"未听"——不是枚举成员，是"这一栏还没写过"。
TRACK_STATUS_LABELS = {
    None: ("○", "未听"),
    "listening": ("●", "在听"),
    "done": ("✓", "已磕完"),
}

LOOP_MODES = ("顺序", "列表循环", "单曲循环")
LOOP_SEQUENTIAL, LOOP_LIST, LOOP_SINGLE = 0, 1, 2

SPEEDS = (("0.5×", 0.5), ("0.75×", 0.75), ("1.0×", 1.0), ("1.25×", 1.25), ("1.5×", 1.5))

# 合成时原音那一路的音量（自己的声音是 1.0）。用户按听感定的：原音压到 75%
# 才盖不住自己的声音。改这一个数就够，别在视图里另写一个 0.75。
MIX_ORIGINAL_GAIN = 0.75

COLLAPSED_KEY = "shadowing_list_collapsed"
SPLITTER_WIDTHS_KEY = "shadowing_splitter_widths"
PDF_SPLITTER_WIDTHS_KEY = "shadowing_pdf_splitter_widths"

# 收起/展开左栏的圆角小按钮（嵌在准备条最左边）。
# 收起是**整块隐藏**左栏，不把它压成细条。
EDGE_TOGGLE_WIDTH = 32
EDGE_TOGGLE_HEIGHT = 30

# 左栏展开时的最小宽度（窄到看不全曲目名就没有意义了）
EXPANDED_MIN_WIDTH = 240

# 翻页很频繁（滚轮一滑就是好几页），落库用防抖合并，别一页一个事务
PAGE_SAVE_DEBOUNCE_MS = 800

PDF_FILTER = "PDF (*.pdf);;所有文件 (*)"
ZOOM_STEPS = (0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0)


def stamp(milliseconds):
    """时长格式化。**支持小时**（4.3 边界：两小时的音频不能让时间显示溢出）。"""
    if not milliseconds or milliseconds < 0:
        return "--:--"
    total = round(milliseconds / 1000)
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def elide(text, limit=26):
    """面板标题里只留文件名，太长就截中间——**保留扩展名**，
    因为"这是什么文件"比"文件名后半段"重要。"""
    if len(text) <= limit:
        return text
    keep = limit // 2
    return f"{text[:keep]}…{text[-(limit - keep - 1):]}"


def clock(seconds):
    """录音计时。不能直接用 `stamp()`：它把 0 当成"没有时长"显示成 `--:--`，
    而录音刚开始时该显示的是 `00:00`。"""
    minutes, secs = divmod(max(0, int(seconds)), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def safe_stem(text, fallback="跟读"):
    """把曲目名变成一个能当文件名的东西（Windows 不允许 `\\ / : * ? " < > |`）。"""
    cleaned = "".join(ch for ch in (text or "") if ch not in '\\/:*?"<>|').strip()
    return cleaned or fallback


def preferred_audio_format():
    """挑一个这台机器**真的能编码**的录音格式。

    AAC（`.m4a`）的体积大约是 WAV 的十分之一，跟读一录就是几分钟到几十分钟，
    这个差别很实在；但没有 AAC 后端时退回 WAV——宁可文件大，不能录不出来。
    两个格式都是 Qt 自己在运行时汇报的能力，不是拍脑袋写的。
    """
    formats = QMediaFormat().supportedFileFormats(QMediaFormat.ConversionMode.Encode)
    codecs = QMediaFormat().supportedAudioCodecs(QMediaFormat.ConversionMode.Encode)
    if (QMediaFormat.FileFormat.Mpeg4Audio in formats
            and QMediaFormat.AudioCodec.AAC in codecs):
        chosen = QMediaFormat(QMediaFormat.FileFormat.Mpeg4Audio)
        chosen.setAudioCodec(QMediaFormat.AudioCodec.AAC)
        return chosen, ".m4a"
    chosen = QMediaFormat(QMediaFormat.FileFormat.Wave)
    chosen.setAudioCodec(QMediaFormat.AudioCodec.Wave)
    return chosen, ".wav"


class ClickableSlider(QSlider):
    """支持点击直接跳转并继续拖动的进度条滑块。"""

    def __init__(self, orientation=Qt.Orientation.Horizontal, parent=None):
        super().__init__(orientation, parent)
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            opt = QStyleOptionSlider()
            self.initStyleOption(opt)
            handle_rect = self.style().subControlRect(
                QStyle.ComplexControl.CC_Slider, opt, QStyle.SubControl.SC_SliderHandle, self
            )
            handle_len = handle_rect.width() if self.orientation() == Qt.Orientation.Horizontal else handle_rect.height()
            span = max(1, (self.width() if self.orientation() == Qt.Orientation.Horizontal else self.height()) - handle_len)
            pos = int(event.position().x() if self.orientation() == Qt.Orientation.Horizontal else event.position().y()) - handle_len // 2
            val = QStyle.sliderValueFromPosition(
                self.minimum(), self.maximum(), pos, span, opt.upsideDown
            )
            self.setValue(val)
            self.sliderMoved.emit(val)
        super().mousePressEvent(event)


class HandPdfView(QPdfView):
    zoom_requested = Signal(int)
    focused = Signal()

    def mousePressEvent(self, event):
        self.focused.emit()
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._drag_pos = event.position().toPoint()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if getattr(self, "_dragging", False):
            pos = event.position().toPoint()
            delta = pos - self._drag_pos
            self._drag_pos = pos
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and getattr(self, "_dragging", False):
            self._dragging = False
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def enterEvent(self, event):
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        super().enterEvent(event)

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.focused.emit()
            self.zoom_requested.emit(1 if event.angleDelta().y() > 0 else -1)
            event.accept()
            return
        super().wheelEvent(event)


class PdfPanel(QFrame):
    """一份 PDF 的阅读面板（4.3 的对照区）。

    只做三件事：载入、显示、把"选文件"和"翻页"的意图冒泡出去。**不落库**——路径与页码
    都属于播放列表，由页面统一写。
    """

    choose_requested = Signal()
    page_changed = Signal()
    focused = Signal()
    zoom_requested = Signal(int)

    def __init__(self, heading, empty_hint, parent=None):
        super().__init__(parent)
        self.setObjectName("surface")
        self._path = None

        box = QVBoxLayout(self)
        box.setContentsMargins(10, 10, 10, 10)
        box.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(6)
        self.lbl_heading = QLabel(heading)
        self.lbl_heading.setObjectName("sectionTitle")
        head.addWidget(self.lbl_heading)
        self.lbl_name = QLabel("未选择")
        self.lbl_name.setObjectName("faint")
        self.lbl_name.setWordWrap(False)
        head.addWidget(self.lbl_name, 1)
        self.lbl_page = QLabel("")
        self.lbl_page.setObjectName("faint")
        head.addWidget(self.lbl_page)
        self.btn_open = QPushButton()
        self.btn_open.setObjectName("iconButton")
        self.btn_open.setIcon(icon("folder-open"))
        self.btn_open.setToolTip("用系统默认的 PDF 阅读器打开")
        self.btn_open.setEnabled(False)
        head.addWidget(self.btn_open)
        self.btn_choose = QPushButton("选择 PDF")
        self.btn_choose.setObjectName("iconButton")
        self.btn_choose.setIcon(icon("file-text"))
        head.addWidget(self.btn_choose)
        box.addLayout(head)

        self.view = HandPdfView()
        self.view.setObjectName("pdfView")
        self.view.setPageMode(QPdfView.PageMode.MultiPage)
        self.view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
        self.document = QPdfDocument(self)
        self.view.setDocument(self.document)

        hint = QWidget()
        hint_box = QVBoxLayout(hint)
        label = QLabel(empty_hint)
        label.setObjectName("faint")
        label.setAlignment(Qt.AlignCenter)
        label.setWordWrap(True)
        hint_box.addStretch()
        hint_box.addWidget(label)
        hint_box.addStretch()

        self.stack = QStackedWidget()
        self.stack.addWidget(self.view)   # 0
        self.stack.addWidget(hint)        # 1
        box.addWidget(self.stack, 1)

        self.btn_choose.clicked.connect(self.choose_requested)
        self.btn_open.clicked.connect(self.open_externally)
        # 翻页就记：`currentPage` 是 0 起的，存进库时 +1（给人看的页码从 1 开始）
        self.view.pageNavigator().currentPageChanged.connect(self._on_page_changed)
        self.view.focused.connect(self.focused.emit)
        self.view.zoom_requested.connect(self.zoom_requested.emit)

    def path(self):
        return self._path

    def current_page(self):
        """当前页，**1 起**。取不到就当第一页——页码只是个便利，不该让页面崩掉。"""
        try:
            return int(self.view.pageNavigator().currentPage()) + 1
        except (TypeError, ValueError, AttributeError):
            return 1

    def _on_page_changed(self, _page):
        if self._path:
            self.lbl_page.setText(f"第 {self.current_page()} 页")
            self.page_changed.emit()

    def load(self, path, page=1):
        """载入一份 PDF 并跳到 `page`。空路径或文件不存在 → 切到空状态并把原因写在标题行。"""
        self._path = None
        self.btn_open.setEnabled(False)
        self.lbl_page.setText("")
        if not path:
            self.lbl_name.setText("未选择")
            self.lbl_name.setToolTip("")
            self._show_hint()
            return False
        if not os.path.isfile(path):
            self.lbl_name.setText("文件不在了")
            self.lbl_name.setToolTip(path)
            self._show_hint()
            return False
        error = self.document.load(path)
        if error != QPdfDocument.Error.None_:
            self.lbl_name.setText("打不开")
            self.lbl_name.setToolTip(path)
            self._show_hint()
            return False
        self._path = path
        self.lbl_name.setText(elide(os.path.basename(path)))
        self.lbl_name.setToolTip(path)
        self.btn_open.setEnabled(True)
        self.stack.setCurrentIndex(0)
        self.jump_to_page(page)
        return True

    def jump_to_page(self, page):
        """跳到第 `page` 页（1 起）。读数取不到就当 1.0——
        `jump` 的 zoom 传 0 会把视图缩成一个点，宁可给个正常值。"""
        navigator = self.view.pageNavigator()
        try:
            zoom = float(navigator.currentZoom()) or 1.0
        except (TypeError, ValueError, AttributeError):
            zoom = 1.0
        try:
            navigator.jump(max(0, int(page) - 1), QPointF(0.0, 0.0), zoom)
        except TypeError:
            return
        self.lbl_page.setText(f"第 {self.current_page()} 页")

    def _show_hint(self):
        if hasattr(self.document, "close"):
            self.document.close()
        self.stack.setCurrentIndex(1)

    def open_externally(self):
        if self._path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._path))

    def set_zoom(self, factor):
        self._zoom = factor
        if factor is None:
            self.view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
            return
        self.view.setZoomMode(QPdfView.ZoomMode.Custom)
        self.view.setZoomFactor(factor)


class FFmpegWorker(QThread):
    """后台跑一次 ffmpeg。

    产出先写成临时文件，成功后才改名到最终名字——**中途失败不留半个文件**
    （那种文件会被当成真曲目对待，然后播到一半失败）。改名由调用方做：最终名字要考虑
    库里是否重名、或要不要替掉原文件，那是业务的规则，不是转码进程的规则。

    `args` 是**完整的参数表**（不含 `ffmpeg` 自己）。原先这里写死了"视频抽音轨"那一条
    命令行，而跟读混音要的是另一条（两个输入 + amix + 起播偏移），所以改成传参：
    两条路共用一个 worker 类，退出路径也只需要收一种线程。
    """

    finished = Signal(bool, str)   # 成功?, 成功给输出路径 / 失败给原因
    progress = Signal(str)

    def __init__(self, args, output, diagnostic_log=None, parent=None):
        super().__init__(parent)
        self.args = list(args)
        self.output = output
        self.diagnostic_log = diagnostic_log
        self._process = None
        self._cancelled = False

    def cancel(self):
        self._cancelled = True
        process = self._process
        if process is not None and process.poll() is None:
            process.kill()

    def run(self):
        import subprocess

        last_lines = []
        all_lines = []
        try:
            self._process = subprocess.Popen(
                ["ffmpeg", *self.args],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                encoding="utf-8",
                errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            for line in self._process.stdout:
                line_str = line.strip()
                if line_str:
                    all_lines.append(line_str)
                    last_lines.append(line_str)
                    if len(last_lines) > 12:
                        last_lines.pop(0)
                self.progress.emit(line_str)
            self._process.wait()
        except FileNotFoundError:
            self.finished.emit(False, "未检测到 ffmpeg")
            return
        except OSError as error:
            self.finished.emit(False, str(error))
            return
        except Exception as error:
            self.finished.emit(False, str(error))
            return

        if self._cancelled:
            return
        if self.diagnostic_log:
            try:
                with open(self.diagnostic_log, "a", encoding="utf-8") as log:
                    log.write("\nFFmpeg exit_code: %s\n" % self._process.returncode)
                    log.write("\n".join(all_lines))
                    log.write("\n")
            except OSError:
                pass
        ok = self._process.returncode == 0 and os.path.isfile(self.output)
        if not ok and os.path.isfile(self.output):
            try:
                os.remove(self.output)
            except OSError:
                pass
        err_msg = "FFmpeg 执行失败"
        if not ok and last_lines:
            err_msg += f": {last_lines[-1]}"
        self.finished.emit(ok, self.output if ok else err_msg)


class ShadowingView(QWidget):
    """P2 影子跟读。"""

    def __init__(self, database):
        super().__init__()
        self.database = database

        # ---- 播放上下文 ----
        self.playlist_id = None
        self.track_id = None
        self.pos_a = self.pos_b = -1          # -1 = 没设；毫秒
        self.loop_mode = LOOP_SEQUENTIAL
        self.last_position = 0
        self.unlogged_ms = 0                  # 还没进活动日志的毫秒（跨曲目累计）
        self.track_unlogged_ms = 0            # 还没写进本题目 listened_ms 的毫秒
        self.loop_count = 0                   # 本次会话里的 AB 循环次数（D5）
        self._seek_target = None              # 正在跳转的目标位置，见 _seek
        self._pending_position = None         # 载入时就等着落下的定位
        self._rebuilding = False
        self._banner_action = None
        self._closed = False
        self._collapsed = False
        self._active_pdf = None
        self.pdf_zoom = None
        self._loading_pages = False
        self._restoring_splitter_widths = False           # 正在按记忆恢复页码，期间的翻页信号忽略

        self._page_timer = QTimer(self)
        self._page_timer.setSingleShot(True)
        self._page_timer.setInterval(PAGE_SAVE_DEBOUNCE_MS)
        self._page_timer.timeout.connect(self._save_pdf_pages)

        self._items = []                      # 当前列表的曲目缓存（渲染用）

        self.player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.player.setAudioOutput(self.audio_output)
        self.audio_output.setVolume(0.8)

        # ---- 跟读录音（底部右半边）----
        # 捕获对象**第一次录音时才建**（见 _setup_recorder）：没有麦克风的机器上，
        # 提前初始化它没有任何好处。
        self.capture = None
        self.audio_input = None
        self.recorder = None
        self._recording = False
        self._record_session_active = False
        self._record_seconds = 0
        self._record_paused_at = None
        self._record_paused_total = 0.0
        self._last_recording = ""
        # 录音期间"原音实际放到哪"的轨迹（见 _mix_recording）。每一项是一段：
        # {start, end, path, pos, speed} = 录音内时间轴上的起止秒数、原音文件、
        # 原音内部的起点秒数、当时的语速。
        self._rec_segments = []
        self._rec_started = None              # 录音真正开始的那一刻（monotonic 秒）
        self._setting_speed = False           # 程序在按曲目恢复语速，不是用户在改
        self._mix_worker = None
        self._record_timer = QTimer(self)
        self._record_timer.setInterval(1000)
        self._record_timer.timeout.connect(self._record_tick)

        # 设备一变（插耳机、拔耳机、系统里换默认）就重新装一遍，见 apply_audio_devices
        devices = QMediaDevices(self)
        devices.audioOutputsChanged.connect(self.apply_audio_devices)
        devices.audioInputsChanged.connect(self.apply_audio_devices)

        self.init_ui()
        self.setup_connections()
        self.refresh()
        self.restore_playback_state()

    # ==================================================================
    # 布局
    # ==================================================================

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)
        # 拖拽导入（见 dropEvent）。装在页面上而不是列表上：拖到页面的任何位置都认。
        self.setAcceptDrops(True)

        eyebrow = QLabel("沉浸式听力")
        eyebrow.setObjectName("pageEyebrow")
        layout.addWidget(eyebrow)
        title = QLabel("影子跟读")
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        subtitle = QLabel("音频、原文与解析放在同一个专注空间；只有在播放时才计时。")
        subtitle.setObjectName("pageSubtitle")
        layout.addWidget(subtitle)

        layout.addWidget(self._build_prepare_bar())

        self.banner = Banner()
        self.banner.action_clicked.connect(self._on_banner_action)
        layout.addWidget(self.banner)

        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setObjectName("shadowingSplitter")
        self.list_panel = self._build_list_panel()
        self.splitter.addWidget(self.list_panel)
        self.splitter.addWidget(self._build_workspace())
        self.splitter.setSizes([300, 820])
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setCollapsible(0, True)
        self.splitter.splitterMoved.connect(self._save_main_splitter_width)
        layout.addWidget(self.splitter, 1)
        QTimer.singleShot(0, self._restore_main_splitter_width)

    def _build_prepare_bar(self):
        """材料准备条。**只留真正要点的东西**：导入、转换，右侧是 PDF 缩放。

        "准备材料"四个字与缩放原来的独立一行都去掉了（验收意见）：它们占的是标题行
        的位置，去掉之后准备条与下面的 PDF 对照区齐平，播放列表也跟着齐平了。
        状态文字仍单独占一行——挤在同一行里时它会被截成半截，而那行字正是 D15
        要求告知用户的导入代价。
        """
        bar = QFrame()
        bar.setObjectName("shadowingToolbar")
        box = QVBoxLayout(bar)
        box.setContentsMargins(0, 2, 0, 4)
        box.setSpacing(4)

        row = QHBoxLayout()
        row.setSpacing(8)

        # 收起/展开左栏的圆角小按钮：**放在准备条里、最左边**（验收意见指定的位置）。
        # 做成嵌在行内而不是悬浮在工作区边缘——悬浮时它不占宽度，会和这一行的第一个
        # 按钮打架，位置还得跟着窗口尺寸手动算，得不偿失。
        self.btn_toggle_list = QPushButton()
        self.btn_toggle_list.setObjectName("edgeToggle")
        self.btn_toggle_list.setFixedSize(EDGE_TOGGLE_WIDTH, EDGE_TOGGLE_HEIGHT)
        self.btn_toggle_list.setCursor(Qt.PointingHandCursor)
        self.btn_toggle_list.setFocusPolicy(Qt.NoFocus)
        row.addWidget(self.btn_toggle_list)

        self.btn_import_audio = QPushButton("导入音频")
        self.btn_import_audio.setObjectName("iconButton")
        self.btn_import_audio.setIcon(icon("music"))
        self.btn_import_audio.setToolTip("多选或拖拽音频文件；会复制进工具自己的音频库，源文件不动")
        row.addWidget(self.btn_import_audio)

        self.btn_open_recordings = QPushButton("录音文件夹")
        self.btn_open_recordings.setObjectName("iconButton")
        self.btn_open_recordings.setIcon(icon("folder-open"))
        self.btn_open_recordings.setToolTip("打开已保存跟读录音所在的文件夹")
        row.addWidget(self.btn_open_recordings)

        row.addStretch()

        zoom_label = QLabel("PDF 缩放")
        zoom_label.setObjectName("muted")
        row.addWidget(zoom_label)
        self.btn_zoom_out = QPushButton("−")
        self.btn_zoom_in = QPushButton("＋")
        self.btn_zoom_fit = QPushButton("适宽")
        for button in (self.btn_zoom_out, self.btn_zoom_in):
            button.setObjectName("iconButton")
            button.setFixedWidth(36)
        self.btn_zoom_fit.setObjectName("iconButton")
        self.btn_zoom_fit.setFixedWidth(60)
        self.btn_zoom_fit.setToolTip("当前 PDF 缩放到适合面板宽度")
        self.lbl_zoom = QLabel("适宽")
        self.lbl_zoom.setObjectName("faint")
        self.lbl_zoom.setFixedWidth(56)
        self.lbl_zoom.setAlignment(Qt.AlignCenter)
        row.addWidget(self.btn_zoom_out)
        row.addWidget(self.lbl_zoom)
        row.addWidget(self.btn_zoom_in)
        row.addWidget(self.btn_zoom_fit)
        box.addLayout(row)

        self.lbl_status = QLabel("")
        self.lbl_status.setObjectName("faint")
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setVisible(False)
        box.addWidget(self.lbl_status)
        return bar

    def _build_list_panel(self):
        """左栏。折叠按钮**在这块面板自己的标题行里**（用户验收要求），折叠后
        面板收缩成一条窄条、按钮留在原处可点——按钮跟着内容一起消失就再也展不开了。"""
        panel = QFrame()
        panel.setObjectName("shadowingListPanel")
        panel.setMinimumWidth(EXPANDED_MIN_WIDTH)

        outer = QVBoxLayout(panel)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        head = QHBoxLayout()
        head.setContentsMargins(10, 10, 10, 6)
        head.setSpacing(6)
        self.lbl_list_heading = QLabel("播放列表")
        self.lbl_list_heading.setObjectName("sectionTitle")
        head.addWidget(self.lbl_list_heading)
        head.addStretch()
        outer.addLayout(head)

        body = QWidget()
        self.list_body = body
        box = QVBoxLayout(body)
        box.setContentsMargins(10, 0, 10, 10)
        box.setSpacing(8)

        # 列表的选、建、删三件事放在一起——它们回答的是同一个问题："这是哪一份列表"。
        # （验收意见：新建从顶部工具条挪到这里；删除原先只藏在右键菜单里，等于没有。）
        picker = QHBoxLayout()
        picker.setSpacing(6)
        self.combo_playlist = QComboBox()
        self.combo_playlist.setMinimumWidth(120)
        self.combo_playlist.setToolTip("切换播放列表；对照用的两份 PDF 也跟着列表走")
        self.btn_new_list = QPushButton()
        self.btn_new_list.setObjectName("iconButton")
        self.btn_new_list.setIcon(icon("plus"))
        self.btn_new_list.setFixedWidth(34)
        self.btn_new_list.setToolTip("新建一个播放列表")
        self.btn_delete_list = QPushButton()
        self.btn_delete_list.setObjectName("iconButton")
        self.btn_delete_list.setIcon(icon("trash"))
        self.btn_delete_list.setFixedWidth(34)
        self.btn_delete_list.setToolTip("删除这份列表（曲目与跟读记录会保留）")
        picker.addWidget(self.combo_playlist, 1)
        picker.addWidget(self.btn_new_list)
        picker.addWidget(self.btn_delete_list)
        box.addLayout(picker)

        self.lbl_list_meta = QLabel("")
        self.lbl_list_meta.setObjectName("muted")
        self.lbl_list_meta.setWordWrap(True)
        box.addWidget(self.lbl_list_meta)

        self.track_list = QListWidget()
        self.track_list.setObjectName("trackList")
        self.track_list.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        # 拖拽排序（4.3 流程 A 第 4 条）：内部移动，落库在 _on_rows_moved
        self.track_list.setDragDropMode(QListWidget.InternalMove)
        self.track_list.setDefaultDropAction(Qt.MoveAction)
        self.track_list.setContextMenuPolicy(Qt.CustomContextMenu)

        self.empty_tracks = EmptyState()
        self.list_stack = QStackedWidget()
        self.list_stack.addWidget(self.track_list)   # 0
        self.list_stack.addWidget(self.empty_tracks) # 1
        box.addWidget(self.list_stack, 1)

        foot = QHBoxLayout()
        foot.setSpacing(6)
        self.btn_remove_track = QPushButton("移出列表")
        self.btn_remove_track.setObjectName("iconButton")
        self.btn_remove_track.setIcon(icon("x"))
        self.btn_remove_track.setToolTip("只从这份列表移走，音频与跟读记录都保留")
        foot.addWidget(self.btn_remove_track)
        foot.addStretch()
        box.addLayout(foot)

        outer.addWidget(body, 1)
        return panel

    def _build_workspace(self):
        page = QWidget()
        self.workspace_page = page
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(8)

        pdf_split = QSplitter(Qt.Horizontal)
        pdf_split.setObjectName("shadowingSplitter")
        self.kr_pdf = PdfPanel("原文", "这份播放列表还没有配原文 PDF")
        self.cn_pdf = PdfPanel("解析", "这份播放列表还没有配解析 PDF")
        self._active_pdf = self.kr_pdf
        self.kr_pdf.focused.connect(lambda: self._set_active_pdf(self.kr_pdf))
        self.cn_pdf.focused.connect(lambda: self._set_active_pdf(self.cn_pdf))
        self.kr_pdf.zoom_requested.connect(lambda d: self.step_zoom(d, self.kr_pdf))
        self.cn_pdf.zoom_requested.connect(lambda d: self.step_zoom(d, self.cn_pdf))
        pdf_split.addWidget(self.kr_pdf)
        pdf_split.addWidget(self.cn_pdf)
        pdf_split.setSizes([520, 520])
        self._pdf_splitter = pdf_split
        pdf_split.splitterMoved.connect(lambda pos, index, splitter=pdf_split: self._save_pdf_splitter_width(splitter))
        QTimer.singleShot(0, lambda splitter=pdf_split: self._restore_pdf_splitter_width(splitter))
        # 上边不再有独立的一行：准备条就是这一栏的顶，和左栏齐平（验收意见）
        box.addWidget(pdf_split, 1)

        box.addWidget(self._build_player_card())
        return page

    def _build_player_card(self):
        """底部：左边原音、右边录音。"""
        card = QFrame()
        card.setObjectName("shadowingPlayerCard")
        outer = QHBoxLayout(card)
        outer.setContentsMargins(16, 10, 16, 10)
        outer.setSpacing(14)

        left = QWidget()
        box = QVBoxLayout(left)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(8)

        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        self.lbl_current = QLabel("从左侧选择一个音频开始跟读")
        self.lbl_current.setObjectName("sectionTitle")
        self.lbl_current.setWordWrap(False)
        self.lbl_current.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        title_box.addWidget(make_copyable(self.lbl_current))

        self.lbl_track_meta = QLabel("")
        self.lbl_track_meta.setObjectName("muted")
        self.lbl_track_meta.setWordWrap(False)
        title_box.addWidget(self.lbl_track_meta)
        head.addLayout(title_box, 1)

        self.combo_speed = QComboBox()
        self.combo_speed.addItems([text for text, _ in SPEEDS])
        self.combo_speed.setCurrentText("1.0×")
        self.combo_speed.setToolTip("播放速度")
        self.combo_speed.setFixedWidth(72)
        head.addWidget(self.combo_speed, 0, Qt.AlignVCenter)

        self.combo_loop = QComboBox()
        self.combo_loop.addItems(LOOP_MODES)
        self.combo_loop.setToolTip("列表的推进方式：顺序 / 列表循环 / 单曲循环。曲目内的 A-B 循环是另一层")
        self.combo_loop.setFixedWidth(88)
        head.addWidget(self.combo_loop, 0, Qt.AlignVCenter)

        box.addLayout(head)

        timeline = QHBoxLayout()
        timeline.setSpacing(8)
        self.lbl_current_time = QLabel("00:00")
        self.lbl_current_time.setObjectName("muted")
        self.lbl_total_time = QLabel("00:00")
        self.lbl_total_time.setObjectName("muted")
        self.slider = ClickableSlider(Qt.Horizontal)
        self.slider.setObjectName("timeline")
        timeline.addWidget(self.lbl_current_time)
        timeline.addWidget(self.slider, 1)
        timeline.addWidget(self.lbl_total_time)
        box.addLayout(timeline)

        transport = QHBoxLayout()
        transport.setSpacing(6)
        self.btn_prev = QPushButton()
        self.btn_prev.setObjectName("iconButton")
        self.btn_prev.setIcon(icon("skip-back"))
        self.btn_prev.setFixedSize(32, 28)
        self.btn_prev.setToolTip("上一个音频（Ctrl+←）")

        self.btn_play = QPushButton("播放")
        self.btn_play.setObjectName("primaryButton")
        self.btn_play.setIcon(icon("play", "#FFFFFF", 14))
        self.btn_play.setFixedHeight(28)
        self.btn_play.setMinimumWidth(68)

        self.btn_next = QPushButton()
        self.btn_next.setObjectName("iconButton")
        self.btn_next.setIcon(icon("skip-forward"))
        self.btn_next.setFixedSize(32, 28)
        self.btn_next.setToolTip("下一个音频（Ctrl+→）")

        for button in (self.btn_prev, self.btn_play, self.btn_next):
            transport.addWidget(button)

        transport.addStretch()

        self.btn_a = QPushButton("设 A 点")
        self.btn_b = QPushButton("设 B 点")
        self.btn_clear_ab = QPushButton("清除")
        self.btn_clear_ab.setToolTip("清除 A-B 点")

        for button in (self.btn_a, self.btn_b):
            button.setObjectName("iconButton")
            button.setFixedHeight(28)
            button.setMinimumWidth(66)
            transport.addWidget(button)

        self.btn_clear_ab.setObjectName("iconButton")
        self.btn_clear_ab.setFixedHeight(28)
        self.btn_clear_ab.setMinimumWidth(46)
        transport.addWidget(self.btn_clear_ab)

        self._transport_controls = [
            self.btn_prev, self.btn_next, self.btn_play, self.combo_speed, self.combo_loop,
            self.btn_a, self.btn_b, self.btn_clear_ab,
        ]
        box.addLayout(transport)

        outer.addWidget(left, 1)

        divider = QFrame()
        divider.setObjectName("cardDivider")
        divider.setFrameShape(QFrame.VLine)
        divider.setFrameShadow(QFrame.Plain)
        outer.addWidget(divider)

        # 录音区给暂停按钮留出明确空间，避免和计时挤在一起
        outer.addWidget(self._build_recorder_panel())
        return card

    def _build_recorder_panel(self):
        """跟读录音：一个按钮 + 一个计时。"""
        panel = QWidget()
        box = QVBoxLayout(panel)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(4)

        heading = QLabel("跟读录音")
        heading.setObjectName("sectionTitle")
        box.addWidget(heading)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.btn_record = QPushButton("● 录音")
        self.btn_record.setObjectName("recordButton")
        self.btn_record.setCursor(Qt.PointingHandCursor)
        self.btn_record.setFocusPolicy(Qt.NoFocus)
        self.btn_record.setMinimumWidth(80)
        self.btn_record.setFixedHeight(30)
        self.btn_record.setToolTip(
            "跟着原音念，录成一条音轨存到设置里指定的位置。带耳机效果最好。"
        )
        row.addWidget(self.btn_record)

        self.btn_pause_record = QPushButton("Ⅱ 暂停")
        self.btn_pause_record.setObjectName("iconButton")
        self.btn_pause_record.setMinimumWidth(72)
        self.btn_pause_record.setFixedHeight(30)
        self.btn_pause_record.setEnabled(False)
        self.btn_pause_record.setToolTip("暂停/继续录音")
        row.addWidget(self.btn_pause_record)

        self.lbl_record_time = QLabel("00:00")
        self.lbl_record_time.setObjectName("muted")
        row.addWidget(self.lbl_record_time)
        row.addStretch()
        box.addLayout(row)

        self.lbl_recording = QLabel("还没有录音")
        self.lbl_recording.setObjectName("faint")
        self.lbl_recording.setWordWrap(True)
        box.addWidget(self.lbl_recording)

        box.addStretch()
        panel.setFixedWidth(225)
        return panel

    # ==================================================================
    # 连接与快捷键
    # ==================================================================

    def setup_connections(self):
        self.btn_import_audio.clicked.connect(self.import_audio)
        self.btn_open_recordings.clicked.connect(self.open_recordings_folder)
        self.empty_tracks.action_clicked.connect(self.import_audio)
        self.btn_new_list.clicked.connect(self.create_playlist)
        self.btn_delete_list.clicked.connect(self.delete_current_playlist)
        self.btn_toggle_list.clicked.connect(self.toggle_list_panel)
        self.btn_remove_track.clicked.connect(self.remove_current_from_playlist)

        self.kr_pdf.choose_requested.connect(lambda: self.choose_pdf("kr"))
        self.cn_pdf.choose_requested.connect(lambda: self.choose_pdf("cn"))
        self.kr_pdf.page_changed.connect(self._on_pdf_page_changed)
        self.cn_pdf.page_changed.connect(self._on_pdf_page_changed)
        self.btn_zoom_out.clicked.connect(lambda: self.step_zoom(-1))
        self.btn_zoom_in.clicked.connect(lambda: self.step_zoom(1))
        self.btn_zoom_fit.clicked.connect(self.fit_pdf_width)

        self.combo_playlist.currentIndexChanged.connect(self._on_playlist_changed)
        self.track_list.itemClicked.connect(self._on_track_clicked)
        self.track_list.customContextMenuRequested.connect(self._track_menu)
        self.track_list.model().rowsMoved.connect(self._on_rows_moved)

        self.btn_prev.clicked.connect(lambda: self.goto_track(-1))
        self.btn_next.clicked.connect(lambda: self.goto_track(1))
        self.btn_play.clicked.connect(self.toggle_play)
        self.btn_a.clicked.connect(self.set_a)
        self.btn_b.clicked.connect(self.set_b)
        self.btn_clear_ab.clicked.connect(self.clear_ab)
        self.combo_speed.currentTextChanged.connect(self._on_speed_changed)
        self.combo_loop.currentIndexChanged.connect(self._on_loop_mode_changed)

        self.player.durationChanged.connect(self.update_duration)
        self.player.positionChanged.connect(self.update_position)
        self.player.playbackStateChanged.connect(self._on_playback_state)
        self.player.errorOccurred.connect(self.on_player_error)
        self.player.mediaStatusChanged.connect(self._on_media_status)
        self.slider.sliderMoved.connect(self._seek)

        self.btn_record.clicked.connect(self.toggle_recording)
        self.btn_pause_record.clicked.connect(self.toggle_pause_recording)

        self._setup_shortcuts()

    def _setup_shortcuts(self):
        # 作用域都是 WidgetWithChildrenShortcut：切到别的页面就不会误触发。
        self._shortcuts = []
        for keys, handler in (
            ("Ctrl+Left", lambda: self.goto_track(-1)),
            ("Ctrl+Right", lambda: self.goto_track(1)),
        ):
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            shortcut.activated.connect(handler)
            self._shortcuts.append(shortcut)

        # 单字母键单独建：它们要在"正在打字"时**真的被禁用**，而不是"触发后自己
        # 判断一下再退出"。两者的差别是实打实的——`QShortcut` 会在按键送达文本框
        # 之前把它截走，光靠处理函数里 `return` 的话，用户在输入框里打不出 `a`。
        # 4.3 的边界条款点名了这一条（当前这页没有常驻文本框，但改名/新建的输入框
        # 会短暂拿到焦点，机制留着）。
        self._letter_shortcuts = []
        for keys, handler in (("Space", self.toggle_play), ("A", self.set_a), ("B", self.set_b)):
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            shortcut.activated.connect(handler)
            self._letter_shortcuts.append(shortcut)

        app = QApplication.instance()
        if app is not None:
            app.focusChanged.connect(self._on_focus_changed)

    def _on_focus_changed(self, _old, new):
        """焦点一进文本框就卸掉单字母快捷键，离开再装回来。"""
        typing = isinstance(new, (QLineEdit, QTextEdit))
        for shortcut in self._letter_shortcuts:
            shortcut.setEnabled(not typing)

    def _typing(self):
        """再兜一道：焦点在文本框里时，单字母动作一律不执行。"""
        return isinstance(QApplication.focusWidget(), (QLineEdit, QTextEdit))

    def _set_status(self, text):
        self.lbl_status.setText(text)
        self.lbl_status.setVisible(bool(text))

    # ==================================================================
    # 播放列表
    # ==================================================================

    def refresh(self):
        """重读播放列表与当前曲目（P5 恢复备份后由 `MainWindow.refresh_all` 调）。"""
        self.reload_playlists()
        self.reload_tracks()
        self._apply_collapsed(self.database.get_setting(COLLAPSED_KEY) == "1")
        self.apply_audio_devices()
        if self.track_id is None:
            self._render_track(None)
        self._render_documents()

    def reload_playlists(self):
        playlists = self.database.list_playlists()
        self.combo_playlist.blockSignals(True)
        self.combo_playlist.clear()
        for playlist in playlists:
            self.combo_playlist.addItem(playlist["name"], playlist["id"])
        if self.playlist_id is None and playlists:
            self.playlist_id = playlists[0]["id"]
        index = self.combo_playlist.findData(self.playlist_id)
        if index < 0:
            # 选中的那份列表已经不在了（别处删掉、或刚恢复了备份）：**以组合框的实际
            # 选择为准**，不能让 `playlist_id` 停在一个界面上根本看不到的 id 上——
            # 那时后面的写入（比如"给这份列表挂 PDF"）会落到用户没选的列表上，
            # 界面上看起来就是"配好的 PDF 又没了"。
            self.playlist_id = self.combo_playlist.currentData()
        else:
            self.combo_playlist.setCurrentIndex(index)
        self.combo_playlist.blockSignals(False)

    def reload_tracks(self):
        self._items = self.database.playlist_items(self.playlist_id)
        self._render_tracks()
        self._update_list_meta()

    def _render_tracks(self):
        self._rebuilding = True
        self.track_list.clear()
        for index, item in enumerate(self._items, start=1):
            entry = QListWidgetItem(self._track_line(index, item))
            entry.setData(Qt.UserRole, item["track_id"])
            entry.setSizeHint(QSize(0, 46))
            if not audio.exists_in_library(item["path"], AUDIO_DIR):
                # 缺失的曲目整行压淡——但**文字里已经写明"缺失"**，颜色只是冗余
                # （§9.3：不让颜色单独承载语义）
                entry.setForeground(QColor(TEXT_COLORS["faint"]))
            self.track_list.addItem(entry)
        self._rebuilding = False
        self._sync_track_selection()
        self._update_empty_state()

    def _track_line(self, index, item):
        mark, label = TRACK_STATUS_LABELS.get(item["status"], TRACK_STATUS_LABELS[None])
        if not audio.exists_in_library(item["path"], AUDIO_DIR):
            second = "缺失 · 文件已不在音频库中"
        else:
            duration = stamp(item["duration_ms"]) if item["duration_ms"] else "—"
            second = f"{mark} {label} · {duration}"
        return f"{index}. {elide(item['title'], 22)}\n{second}"

    def _sync_track_selection(self):
        for row in range(self.track_list.count()):
            entry = self.track_list.item(row)
            if entry.data(Qt.UserRole) == self.track_id:
                self.track_list.setCurrentRow(row)
                break
        else:
            self.track_list.setCurrentRow(-1)

    def _update_list_meta(self):
        summary = self._playlist_summary()
        if summary is None:
            self.lbl_list_meta.setText("还没有播放列表")
            return
        self.lbl_list_meta.setText(
            f"{summary['name']} · {summary['track_count']} 个音频 · "
            f"已完成 {summary['done_count']}"
        )

    def _update_empty_state(self):
        """左栏空状态换内容：没有列表 / 列表是空的，两种说法不一样。"""
        if not self._items:
            if self.combo_playlist.count() == 0:
                self.empty_tracks.set_content(
                    "还没有播放列表",
                    "点「导入音频」把音频复制进工具音频库（源文件原地不动）。",
                    action_text="导入音频",
                    action_icon="music",
                    glyph="music",
                )
            else:
                self.empty_tracks.set_content(
                    "这份列表还没有音频",
                    "直接导入音频就行，会自动复制进音频库并加到这份列表。",
                    action_text="导入音频",
                    action_icon="music",
                    glyph="music",
                )
            self.list_stack.setCurrentIndex(1)
        else:
            self.list_stack.setCurrentIndex(0)

    def _on_playlist_changed(self, index):
        if index < 0:
            return
        self.flush_study_session()
        self.playlist_id = self.combo_playlist.itemData(index)
        self.track_id = None
        self._render_track(None)
        self.reload_tracks()
        self._render_documents()
        self._set_status("")

    def create_playlist(self):
        name, accepted = QInputDialog.getText(self, "新建播放列表", "列表名称")
        if not accepted or not name.strip():
            return
        self.playlist_id = self.database.create_playlist(name.strip())
        self.track_id = None
        self.reload_playlists()
        self.reload_tracks()
        self._render_track(None)
        self._render_documents()
        self._set_status(f"已新建列表：{name.strip()}")

    def rename_current_playlist(self):
        summary = self._playlist_summary()
        if summary is None:
            return
        name, accepted = QInputDialog.getText(
            self, "重命名播放列表", "列表名称", text=summary["name"]
        )
        if not accepted or not name.strip():
            return
        self.database.rename_playlist(self.playlist_id, name.strip())
        self.reload_playlists()
        self._update_list_meta()

    def delete_current_playlist(self):
        """删列表**不动曲目**：AB 点、跟读时长、状态都留在曲目上。"""
        summary = self._playlist_summary()
        if summary is None:
            return
        self.database.delete_playlist(self.playlist_id)
        self.track_id = None
        self.playlist_id = None
        self._render_track(None)
        self.reload_playlists()
        self.reload_tracks()
        self._render_documents()
        show_toast(self.window(), f"已删除列表「{summary['name']}」（曲目与跟读记录已保留）")

    def _playlist_summary(self):
        for playlist in self.database.list_playlists():
            if playlist["id"] == self.playlist_id:
                return playlist
        return None

    # ---- 折叠（D16）----

    def toggle_list_panel(self):
        # 用 `_collapsed` 而不是 `isVisible()`：父窗口自己没显示时，所有子控件的
        # `isVisible()` 都是 False，拿它当状态会把"折叠"和"窗口没开"混成一件事。
        self._apply_collapsed(not self._collapsed)
        self.database.set_setting(COLLAPSED_KEY, "1" if self._collapsed else "0")

    def _apply_collapsed(self, collapsed):
        """折叠 = **整块隐藏**左栏（验收意见：不要压成细长条）。

        控制它的按钮是工作区左上角那个悬浮小片，不随左栏一起藏，所以收起之后随时
        能点回来。展开时按记住的宽度还原，不会一展开就变成默认值。
        """
        self._collapsed = bool(collapsed)
        if self._collapsed:
            self._expanded_width = max(self.list_panel.width(), EXPANDED_MIN_WIDTH)
            self.list_panel.hide()
        else:
            self.list_panel.show()
            width = getattr(self, "_expanded_width", None)
            if width is None:
                width = self._load_width_map(SPLITTER_WIDTHS_KEY).get("main")
            if not isinstance(width, int): width = 300
            total = sum(self.splitter.sizes()) or 1120
            width = max(EXPANDED_MIN_WIDTH, min(width, max(EXPANDED_MIN_WIDTH, total - 240)))
            self._restoring_splitter_widths = True
            self.splitter.setSizes([width, max(1, total - width)])
            self._restoring_splitter_widths = False
        self.btn_toggle_list.setIcon(icon("chevron-right" if self._collapsed else "chevron-left"))
        self.btn_toggle_list.setToolTip(
            "展开播放列表" if self._collapsed else "收起播放列表，把宽度让给 PDF（D16）"
        )

    def _load_width_map(self, key):
        raw = self.database.get_setting(key, "")
        if not raw: return {}
        try:
            import json
            value = json.loads(raw)
            return value if isinstance(value, dict) else {}
        except (TypeError, ValueError): return {}

    def _save_width_map(self, key, values):
        import json
        self.database.set_setting(key, json.dumps(values, ensure_ascii=False))

    def _save_main_splitter_width(self, _pos, _index):
        if self._restoring_splitter_widths or self._collapsed: return
        sizes = self.splitter.sizes()
        if not sizes or sizes[0] < EXPANDED_MIN_WIDTH: return
        values = self._load_width_map(SPLITTER_WIDTHS_KEY)
        values["main"] = int(sizes[0])
        self._save_width_map(SPLITTER_WIDTHS_KEY, values)

    def _restore_main_splitter_width(self):
        width = self._load_width_map(SPLITTER_WIDTHS_KEY).get("main")
        if not isinstance(width, int): return
        total = sum(self.splitter.sizes())
        if total <= 0: return
        width = max(EXPANDED_MIN_WIDTH, min(width, max(EXPANDED_MIN_WIDTH, total - 240)))
        self._restoring_splitter_widths = True
        self.splitter.setSizes([width, max(1, total - width)])
        self._restoring_splitter_widths = False
        self._expanded_width = width

    def _save_pdf_splitter_width(self, splitter):
        if self._restoring_splitter_widths: return
        sizes = splitter.sizes()
        if len(sizes) < 2 or sizes[0] < 240 or sizes[1] < 240: return
        values = self._load_width_map(PDF_SPLITTER_WIDTHS_KEY)
        values["shadowing_pdf"] = int(sizes[0])
        self._save_width_map(PDF_SPLITTER_WIDTHS_KEY, values)

    def _restore_pdf_splitter_width(self, splitter):
        width = self._load_width_map(PDF_SPLITTER_WIDTHS_KEY).get("shadowing_pdf")
        if not isinstance(width, int): return
        total = sum(splitter.sizes())
        if total <= 0: return
        width = max(240, min(width, max(240, total - 240)))
        self._restoring_splitter_widths = True
        splitter.setSizes([width, max(1, total - width)])
        self._restoring_splitter_widths = False

    # ==================================================================
    # 对照 PDF（挂在播放列表上）
    # ==================================================================

    def _render_documents(self):
        """播放列表换了 → 两份 PDF 跟着换，并**回到各自上次读到的那一页**。"""
        summary = self._playlist_summary()
        self._loading_pages = True
        self.kr_pdf.load(
            summary["kr_pdf"] if summary else None,
            (summary["kr_page"] if summary and summary["kr_page"] else 1),
        )
        self.cn_pdf.load(
            summary["cn_pdf"] if summary else None,
            (summary["cn_page"] if summary and summary["cn_page"] else 1),
        )
        self._loading_pages = False
        self._apply_zoom()

    def _on_pdf_page_changed(self):
        """翻页 → 攒着，停手 0.8 秒再落库。"""
        if getattr(self, "_loading_pages", False):
            return   # 载入时的定位不算"用户翻页"，别把恢复动作当成新位置写回去
        self._page_timer.start()

    def _save_pdf_pages(self):
        self._page_timer.stop()
        if self._closed or self.playlist_id is None:
            return
        self.database.set_playlist_documents(
            self.playlist_id,
            kr_page=self.kr_pdf.current_page(),
            cn_page=self.cn_pdf.current_page(),
        )

    def choose_pdf(self, which):
        """选一份 PDF。默认从资料根目录开始找——原文与解析本来就躺在那儿。"""
        if self.playlist_id is None:
            self._show_banner("info", "先新建一个播放列表，再给它配 PDF。")
            return
        start = self.database.get_material_root() or ""
        title = "选择原文 PDF" if which == "kr" else "选择解析 PDF"
        path, _ = QFileDialog.getOpenFileName(self, title, start, PDF_FILTER)
        if not path:
            return
        panel = self.kr_pdf if which == "kr" else self.cn_pdf
        if not panel.load(path):
            self._show_banner("danger", f"〔{os.path.basename(path)}〕打不开，换一份试试。")
            return
        # 换了一份 PDF 就把页码归 1：新文件页数和旧的对不上，留着旧页码只会跳到
        # 一个毫无关系的位置上
        if which == "kr":
            self.database.set_playlist_documents(self.playlist_id, kr_pdf=path, kr_page=1)
        else:
            self.database.set_playlist_documents(self.playlist_id, cn_pdf=path, cn_page=1)
        # 状态里**带上列表名**：PDF 挂在列表上，配错列表是这里唯一容易犯的错，
        # 而错了以后界面上看不出来（换个列表就"没了"）。
        summary = self._playlist_summary()
        self._set_status(
            f"已挂到「{summary['name'] if summary else '?'}」：{os.path.basename(path)}"
        )

    # ---- 缩放 ----

    def _set_active_pdf(self, panel):
        self._active_pdf = panel
        self.pdf_zoom = getattr(panel, "_zoom", None)
        self.lbl_zoom.setText("适宽" if self.pdf_zoom is None else f"{round(self.pdf_zoom * 100)}%")

    def _apply_zoom(self, panel=None):
        panel = panel or self._active_pdf or self.kr_pdf
        panel.set_zoom(getattr(panel, "_zoom", None))
        self.pdf_zoom = getattr(panel, "_zoom", None)
        self.lbl_zoom.setText("适宽" if self.pdf_zoom is None else f"{round(self.pdf_zoom * 100)}%")

    def step_zoom(self, direction, panel=None):
        panel = panel or self._active_pdf or self.kr_pdf
        current = getattr(panel, "_zoom", None)
        if current is None:
            current = 1.0
        else:
            index = min(range(len(ZOOM_STEPS)), key=lambda i: abs(ZOOM_STEPS[i] - current))
            current = ZOOM_STEPS[max(0, min(len(ZOOM_STEPS) - 1, index + direction))]
        panel._zoom = current
        self._apply_zoom(panel)

    def fit_pdf_width(self, panel=None):
        panel = panel or self._active_pdf or self.kr_pdf
        panel._zoom = None
        self._apply_zoom(panel)

    # ==================================================================
    # 导入
    # ==================================================================

    def import_audio(self):
        """导入音频：复制进音频库 → 加入当前列表。源文件只读。"""
        paths, _ = QFileDialog.getOpenFileNames(self, "选择音频", "", audio.AUDIO_FILTER)
        if paths:
            self.import_paths(paths)

    def import_paths(self, paths):
        """批量导入。**逐个独立成败**：一个文件不支持，其余照常导入
        （4.3 状态表：不整批失败）。"""
        if self.playlist_id is None:
            self.playlist_id = self.database.create_playlist("我的听力")
            # 新建之后要让下拉框也认得它，否则列表里有曲目、选择器却是空的
            self.reload_playlists()

        imported, reused, failures, copied_bytes = 0, 0, [], 0
        for path in paths:
            try:
                digest = audio.content_hash(path)
                existing = self.database.find_track_by_hash(digest)
                if existing is not None:
                    # D15：已存在就只加入列表，不重复复制
                    reused += 1
                    self.database.add_track_to_playlist(self.playlist_id, existing["id"])
                    continue
                record = audio.copy_into_library(path, AUDIO_DIR)
                track_id = self.database.add_audio_track(
                    record["path"],
                    record["title"],
                    size=record["size"],
                    content_hash=digest,
                    source_path=os.path.abspath(path),
                )
                self.database.add_track_to_playlist(self.playlist_id, track_id)
                imported += 1
                copied_bytes += record["size"]
            except audio.AudioImportError as error:
                failures.append(str(error))
            except OSError as error:
                failures.append(f"〔{os.path.basename(path)}〕导入失败：{error}")

        self.reload_tracks()
        if self.track_id is None and self._items:
            self.load_track(self._items[0]["track_id"])
        self._report_import(len(paths), imported, reused, failures, copied_bytes)

    def _report_import(self, total, imported, reused, failures, copied_bytes):
        """D15：**代价必须告知**。这里用状态文字 + 提示条，不弹模态
        （本项目不用 `QMessageBox` 报告可预期结果，理由见 `ui/components.py`）。"""
        usage = self.database.audio_library_usage()
        parts = [f"本次导入 {imported} 个"]
        if reused:
            parts.append(f"{reused} 个已存在（只加入列表，未重复复制）")
        if failures:
            parts.append(f"{len(failures)} 个失败")
        parts.append(f"音频库 {usage['tracks']} 个 / {audio.human_size(usage['bytes'])}")
        self._set_status(" · ".join(parts))

        if failures:
            head = failures[0] if len(failures) == 1 else f"{failures[0]}（另有 {len(failures) - 1} 个失败）"
            self._show_banner("warning", head, action_text="重试", handler=self.import_audio)
        elif imported:
            self.banner.clear()
            show_toast(self.window(), f"已导入 {imported} 个文件（{audio.human_size(copied_bytes)}）")

    @staticmethod
    def _remove_quietly(path):
        try:
            os.remove(path)
        except OSError:
            pass

    # ==================================================================
    # 曲目切换
    # ==================================================================

    def _on_track_clicked(self, entry):
        self.load_track(entry.data(Qt.UserRole))

    def load_track(self, track_id, autoplay=True):
        """切到某一段。**先把上一段的欠账结清**：时长、位置、AB、语速一起落库。"""
        if track_id is None:
            return
        if track_id == self.track_id:
            if autoplay and self.player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
                self.player.play()
            return

        # 录音期间切曲目：先把上一首在轨迹里封口，免得上一首的路径和越界时长跨到新曲目里
        if self._recording:
            self._rec_close_segment()

        self.flush_study_session()
        item = self.database.get_track(track_id)
        if item is None:
            return

        self.track_id = track_id
        self.banner.clear()
        self._render_track(item)
        # 列表里那一行也要跟着亮起来：自动推进到下一段时是程序在换曲目，
        # 不主动同步的话高亮还停在上一行，看起来像"点错了"。
        self._sync_track_selection()

        progress = self.database.track_progress(track_id)
        self.pos_a = progress["pos_a_ms"] if progress["pos_a_ms"] is not None else -1
        self.pos_b = progress["pos_b_ms"] if progress["pos_b_ms"] is not None else -1
        self.loop_count = 0
        self.track_unlogged_ms = 0
        self._sync_ab_buttons()
        self._set_speed(progress["speed"])

        absolute = audio.library_path(item["path"], AUDIO_DIR)
        if not os.path.isfile(absolute):
            # 音频库里的文件被删了：曲目行与跟读记录都留着（4.3 状态表）
            self.player.setSource(QUrl())
            self.lbl_current.setText(f"{item['title']}（文件已不在音频库中）")
            self.btn_play.setEnabled(False)
            self._show_banner(
                "warning",
                "这个音频的文件已不在音频库中。可以重新导入，跟读记录会保留。",
                action_text="重新导入",
                handler=lambda: self.reimport_track(track_id),
            )
            return

        self.btn_play.setEnabled(True)
        # 换了源，上一条曲目的位置记忆就作废。留着它，新曲目第一帧会被当成"从旧位置
        # 接着播"（跟读时长凭空长一截），录音轨迹也会记错起点。
        self.last_position = 0
        self._seek_target = None
        self.player.setSource(QUrl.fromLocalFile(absolute))
        self._pending_position = None
        if progress["status"] != "done":
            self.database.set_track_status(track_id, "listening")
        self.database.set_playback_state(self.playlist_id, track_id)
        self._reload_track_row()
        if autoplay:
            self.player.play()

    def _render_track(self, item):
        """控制卡随曲目切换。没有曲目时给一句"从左侧选一段"，并把走带控件禁用。"""
        if item is None:
            self.lbl_current.setText("从左侧选择一个音频开始跟读")
            self.lbl_current.setToolTip("")
            self.lbl_track_meta.setText("")
            self._set_transport_enabled(False)
            return
        self.lbl_current.setText(item["title"])
        self.lbl_current.setToolTip(item["title"])
        self._set_transport_enabled(True)
        self._update_track_meta()

    def _set_transport_enabled(self, enabled):
        for widget in self._transport_controls:
            widget.setEnabled(enabled)
        if not enabled:
            self.btn_play.setText("播放")
            self.btn_play.setIcon(icon("play", "#FFFFFF", 14))
            self.btn_clear_ab.setEnabled(False)

    def _reload_track_row(self):
        """只刷新当前这一行的文字（状态点/时长/缺失标记），重建整表代价更大。"""
        self._items = self.database.playlist_items(self.playlist_id)
        row = self.track_list.currentRow()
        item = self._current_item()
        if item is not None and 0 <= row < self.track_list.count():
            self.track_list.item(row).setText(self._track_line(row + 1, item))
        self._update_list_meta()
        self._update_track_meta()

    def _current_item(self):
        for item in self._items:
            if item["track_id"] == self.track_id:
                return item
        return None

    def _update_track_meta(self):
        item = self._current_item()
        if item is None:
            self.lbl_track_meta.setText("")
            return
        _, label = TRACK_STATUS_LABELS.get(item["status"], TRACK_STATUS_LABELS[None])
        listened = item["listened_ms"] or 0
        parts = [label]
        if listened:
            parts.append(f"已跟读 {stamp(listened)}")
        if item["loop_count"]:
            parts.append(f"AB 循环 {item['loop_count']} 次")
        self.lbl_track_meta.setText(" · ".join(parts))

    def goto_track(self, step):
        """上一段 / 下一段。没有列表或列表只有一段时不动，而不是静默出错。"""
        if not self._items:
            return
        ids = [item["track_id"] for item in self._items]
        if self.track_id in ids:
            index = ids.index(self.track_id) + step
        else:
            index = 0
        if index < 0:
            index = len(ids) - 1 if self.loop_mode == LOOP_LIST else 0
        elif index >= len(ids):
            index = 0 if self.loop_mode == LOOP_LIST else len(ids) - 1
        self.load_track(ids[index])

    def _advance_after_finish(self):
        """一段播完之后的推进：AB → 单曲 → 下一条（顺序到末尾即停）。"""
        ids = [item["track_id"] for item in self._items]
        if self.track_id not in ids:
            return
        index = ids.index(self.track_id)
        if self.loop_mode == LOOP_SINGLE:
            self._seek(0)
            self.player.play()
            return
        if index + 1 < len(ids):
            self.load_track(ids[index + 1])
        elif self.loop_mode == LOOP_LIST:
            self.load_track(ids[0])
        else:
            # 顺序播完即停（4.3 核心组件表）
            self.btn_play.setText("播放")
            self.btn_play.setIcon(icon("play", "#FFFFFF", 14))
            self._set_status("这份列表播完了")

    def remove_current_from_playlist(self):
        if self.track_id is None or self.playlist_id is None:
            return
        self.flush_study_session()
        self.database.remove_track_from_playlist(self.playlist_id, self.track_id)
        self.track_id = None
        self.player.setSource(QUrl())
        self._render_track(None)
        self.reload_tracks()

    # ==================================================================
    # 拖拽排序
    # ==================================================================

    def _on_rows_moved(self, *_):
        if self._rebuilding:
            return   # 重建列表时也会走这里，别把渲染当成用户拖拽
        ordered = [
            self.track_list.item(row).data(Qt.UserRole) for row in range(self.track_list.count())
        ]
        self.database.reorder_playlist(self.playlist_id, ordered)
        # 序号是渲染出来的（1. 2. 3.），顺序变了就得整表重画一次。
        # **推到事件循环下一轮**再画：此刻拖放还没结束，在信号处理里 clear() 掉
        # 正在被拖的列表，Qt 会崩在悬空指针上。
        QTimer.singleShot(0, self.reload_tracks)

    # ==================================================================
    # 播放与进度
    # ==================================================================

    def toggle_play(self):
        if self._typing():
            return
        if self.track_id is None:
            item = self._items[0] if self._items else None
            if item is None:
                return
            self.load_track(item["track_id"])
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            self.flush_study_session()
        else:
            self.player.play()

    def _on_playback_state(self, state):
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.btn_play.setText("暂停" if playing else "播放")
        self.btn_play.setIcon(icon("pause" if playing else "play", "#FFFFFF", 14))
        # 录音期间：原音开始出声就记一段轨迹，停下就给它封口。暂停要**封口而不是
        # 合并**——那几秒麦克风还在录，你的时间轴还在走，只是原音没出声。
        if playing:
            self._rec_open_segment()
        else:
            self._rec_close_segment()
        if not playing:
            self.flush_study_session()

    def update_duration(self, duration):
        item = self._current_item()
        if item is not None and duration > 0 and not item["duration_ms"]:
            self.database.set_track_duration(item["track_id"], duration)
            self._reload_track_row()
        self.slider.setRange(0, max(0, duration))
        self.lbl_total_time.setText(stamp(duration))

    def update_position(self, position):
        playing = self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        if self._seek_target is not None:
            # 跳转刚落地：这一跳不是"听过"，不记账。
            # **无条件清掉标记**（不比对"是否落在目标附近"）：播放器会把越界的目标
            # 夹到文件长度上，比对距离的话这种跳转永远等不到"落点"，标记就一直挂着——
            # 那之后所有真实播放时间都会因为是"跳转途中"而不再计入。
            self._seek_target = None
            self.last_position = position
        else:
            if playing and position >= self.last_position:
                delta = position - self.last_position
                self.unlogged_ms += delta
                self.track_unlogged_ms += delta
            self.last_position = position

        if not self.slider.isSliderDown():
            self.slider.setValue(position)
        self.lbl_current_time.setText(stamp(position))

        # AB 循环：播到 B 点回到 A 点。**不推进列表**——用户正是在死磕这一句。
        if self.pos_a >= 0 and self.pos_b > self.pos_a and position >= self.pos_b:
            self._seek(self.pos_a)
            self.loop_count += 1
            self._update_track_meta()

    def _seek(self, position):
        """跳转。**跳过去的那一段不算听过**（4.3 时长定义）。

        不挂起增量记账的话，把进度条一路拖到底会凭空长出十几分钟的学习记录——
        而那个数字是用户拿来判断"今天练了多少"的。AB 回跳与分段跳转同样走这里。
        """
        position = max(0, int(position))
        self._seek_target = position
        self.last_position = position
        # 录音期间跳转 = 原音在轨迹上拐了个弯：旧的一段在这里封口，播放中就另起一段
        self._rec_jump(position)
        self.player.setPosition(position)
        self.slider.setValue(position)
        self.lbl_current_time.setText(stamp(position))

    def _on_media_status(self, status):
        if status in (QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia):
            # 媒体真的就绪了：把载入时挂起的定位落下来（见 load_track）
            if self._pending_position is not None:
                target, self._pending_position = self._pending_position, None
                self._seek(target)
            return
        if status != QMediaPlayer.MediaStatus.EndOfMedia:
            return
        if self.pos_a >= 0 and self.pos_b > self.pos_a:
            self._seek(self.pos_a)
            self.loop_count += 1
            self.player.play()
            return
        self._advance_after_finish()

    def on_player_error(self, error, message):
        # Qt 在载入新音源时可能先发一次 NoError，忽略掉
        if error == QMediaPlayer.Error.NoError:
            return
        self._set_status("播放失败")
        self._show_banner(
            "danger", f"音频无法播放：{message}。可以先用其他工具转成 MP3 再导入。"
        )

    def _on_speed_changed(self, text):
        speed = dict(SPEEDS).get(text, 1.0)
        self.player.setPlaybackRate(speed)
        # 录音期间改语速：语速决定"这几秒里原音走过了多少素材"，混音要靠它换算，
        # 所以旧的一段到此封口、按新语速另起一段（见 _mix_args）。
        if self._recording and not self._setting_speed:
            self._rec_jump(None)
        if self.track_id is not None:
            self.database.save_track_progress(self.track_id, speed=speed)

    def _set_speed(self, speed):
        """按曲目恢复语速。库里可能存着不在档位上的值（手改过库），就近取一档。"""
        closest = min(SPEEDS, key=lambda pair: abs(pair[1] - float(speed or 1.0)))
        # 这是程序在按曲目恢复设置，不是用户在改——别让它被当成一次语速变更去切轨迹段
        self._setting_speed = True
        try:
            if self.combo_speed.currentText() != closest[0]:
                self.combo_speed.setCurrentText(closest[0])
            self.player.setPlaybackRate(closest[1])
        finally:
            self._setting_speed = False

    def _on_loop_mode_changed(self, index):
        self.loop_mode = index

    # ---- AB 点 ----

    def set_a(self):
        if self._typing():
            return
        if self.track_id is None:
            return
        self.pos_a = max(0, int(self.player.position()))
        # 设新的 A 点时，旧的 B 点可能已经落在它前面，一并清掉
        if self.pos_b <= self.pos_a:
            self.pos_b = -1
        self._sync_ab_buttons()
        self._save_ab()

    def set_b(self):
        if self._typing():
            return
        if self.track_id is None:
            return
        position = int(self.player.position())
        if self.pos_a < 0:
            self._show_banner("info", "先设 A 点，再设 B 点。")
            return
        if position <= self.pos_a:
            self._show_banner("warning", "请将 B 点设在 A 点之后。")
            return
        self.pos_b = position
        self._sync_ab_buttons()
        self._save_ab()

    def clear_ab(self):
        self.pos_a = self.pos_b = -1
        self._sync_ab_buttons()
        self._save_ab()

    def _sync_ab_buttons(self):
        self.btn_a.setText(f"A: {stamp(self.pos_a)}" if self.pos_a >= 0 else "设 A 点")
        self.btn_b.setText(f"B: {stamp(self.pos_b)}" if self.pos_b >= 0 else "设 B 点")
        self.btn_a.setToolTip(f"A 点: {stamp(self.pos_a)}" if self.pos_a >= 0 else "设为 A 点（快捷键 A）")
        self.btn_b.setToolTip(f"B 点: {stamp(self.pos_b)}" if self.pos_b >= 0 else "设为 B 点（快捷键 B）")
        active = self.pos_a >= 0 and self.pos_b > self.pos_a
        self.btn_clear_ab.setEnabled(active)

    def _save_ab(self):
        if self.track_id is None:
            return
        self.database.save_track_progress(
            self.track_id,
            pos_a_ms=self.pos_a if self.pos_a >= 0 else None,
            pos_b_ms=self.pos_b if self.pos_b >= 0 else None,
        )

    # ---- 状态点 ----

    def toggle_done(self):
        item = self._current_item()
        if item is None:
            return
        if item["status"] == "done":
            self.database.set_track_status(self.track_id, "listening")
        else:
            self.database.set_track_status(self.track_id, "done")
        self._reload_track_row()

    # ==================================================================
    # 跟读录音
    #
    # 麦克风那一路是**连续**的：按下录音到停手之间，时间轴上每一秒都有你的声音。
    # 原音那一路不是——它会暂停、会被拖动、会换曲目、会改语速。所以原音按**轨迹**
    # 记：每一次起播 / 暂停 / 跳转 / 换语速都开一段、封一段（_rec_open_segment /
    # _rec_close_segment / _rec_jump），录完照着轨迹把原音剪回它本来的样子。
    # ==================================================================

    def _setup_recorder(self):
        """第一次录音时才搭捕获链。没有麦克风的机器上，提前初始化没有好处。"""
        if self.recorder is not None:
            return
        self.capture = QMediaCaptureSession(self)
        self.audio_input = QAudioInput(self)
        self.recorder = QMediaRecorder(self)
        self.capture.setAudioInput(self.audio_input)
        self.capture.setRecorder(self.recorder)
        self.recorder.recorderStateChanged.connect(self._on_recorder_state)
        self.recorder.errorOccurred.connect(self._on_recorder_error)
        # 输入设备也是显式装的（理由同 apply_audio_devices）
        self.apply_audio_devices()

    def open_recordings_folder(self):
        """在系统文件管理器中打开已保存跟读录音的文件夹。"""
        folder = self.database.get_recording_dir()
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as error:
            self._show_banner("danger", f"录音目录不可访问：{error.strerror or error}")
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(folder)):
            self._show_banner("warning", f"无法打开录音文件夹：{folder}")

    def _generate_recording_filename(self, folder, extension):
        """按照业务规范生成录音文件名。

        规则示例：“影子跟读96届 13-24第5次.m4a”
        格式组成：影子跟读 + [播放列表名] + 空格 + [曲目标题] + 第[N]次 + 扩展名。
        自动遍历历史录音目录（包含归档等子目录），计算当前列表与曲目的下一个序号 N。
        """
        playlist_text = self.combo_playlist.currentText().strip() if self.combo_playlist.count() > 0 else ""
        if playlist_text:
            playlist_norm = re.sub(r"^(\d+)\s*讲$", r"\1届", playlist_text)
            playlist_clean = safe_stem(playlist_norm, "")
        else:
            playlist_clean = ""

        item = self._current_item()
        track_title = item.get("title", "") if item else ""
        track_stem = os.path.splitext(track_title)[0] if track_title else ""
        track_clean = safe_stem(track_stem, "")

        if playlist_clean and track_clean:
            base = f"影子跟读{playlist_clean} {track_clean}"
        elif playlist_clean:
            base = f"影子跟读{playlist_clean}"
        elif track_clean:
            base = f"影子跟读 {track_clean}"
        else:
            base = "影子跟读"

        # 扫描历史目录（含子目录如归档）提取最大已有录音序号
        pl_chars = re.sub(r"\s+", "", playlist_clean) if playlist_clean else ""
        tr_chars = re.sub(r"\s+", "", track_clean) if track_clean else ""
        pl_pat = r"\s*".join(re.escape(c) for c in pl_chars) if pl_chars else ""
        tr_pat = r"\s*".join(re.escape(c) for c in tr_chars) if tr_chars else ""

        if pl_pat and tr_pat:
            pattern = re.compile(rf"^影子跟读\s*{pl_pat}\s+{tr_pat}\s*第(\d+)次", re.IGNORECASE)
        elif pl_pat:
            pattern = re.compile(rf"^影子跟读\s*{pl_pat}\s*第(\d+)次", re.IGNORECASE)
        elif tr_pat:
            pattern = re.compile(rf"^影子跟读\s*{tr_pat}\s*第(\d+)次", re.IGNORECASE)
        else:
            pattern = re.compile(r"^影子跟读\s*第(\d+)次", re.IGNORECASE)

        max_count = 0
        if os.path.isdir(folder):
            try:
                for root, dirs, files in os.walk(folder):
                    for fname in files:
                        if fname.startswith("."):
                            continue
                        match = pattern.search(fname)
                        if match:
                            try:
                                val = int(match.group(1))
                                if val > max_count:
                                    max_count = val
                            except ValueError:
                                pass
            except OSError:
                pass

        count = max_count + 1
        name = f"{base}第{count}次{extension}"
        while os.path.exists(os.path.join(folder, name)):
            count += 1
            name = f"{base}第{count}次{extension}"
        return name

    def toggle_recording(self):
        # “暂停”状态仍属于同一次录音会话，不能因为 _recording 仍为 True
        # 而把停止按钮和暂停逻辑混在一起。
        if self._record_session_active:
            self.stop_recording()
        else:
            self.start_recording()

    def toggle_pause_recording(self):
        if self.recorder is None or not self._record_session_active:
            return
        state = self.recorder.recorderState()
        if state == QMediaRecorder.RecorderState.RecordingState:
            self.recorder.pause()
        elif state == QMediaRecorder.RecorderState.PausedState:
            self.recorder.record()

    def start_recording(self):

        """按一下开始录。**不动原音的播放状态**——跟读是"原音在放、我在跟"，
        录音按钮顺手把原音停了或开了都会打断这件事。

        错误全部走 Banner（可预期的失败不用模态，见 `ui/components.py`）。
        """
        if QMediaDevices.defaultAudioInput().isNull():
            self._show_banner(
                "warning",
                "没有找到可用的麦克风。检查一下系统输入设备，"
                "以及 Windows 设置里的麦克风权限。",
            )
            return

        folder = self.database.get_recording_dir()
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as error:
            self._show_banner("danger", f"录音目录不可写：{error.strerror or error}")
            return

        self._setup_recorder()
        media_format, extension = preferred_audio_format()
        name = self._generate_recording_filename(folder, extension)
        path = os.path.join(folder, name)

        self.recorder.setMediaFormat(media_format)
        self.recorder.setQuality(QMediaRecorder.Quality.HighQuality)
        # **不设声道数**：曾经为了省一半体积写死单声道，而 Windows 的 WMF 编码器在
        # 声道数与输入不一致时会写出一条空音轨（文件有、时长有、就是没声）。
        # 省下来的那点体积不值得冒这个险。
        self.recorder.setOutputLocation(QUrl.fromLocalFile(path))

        self._record_session_active = True
        self._record_seconds = 0
        self._record_paused_at = None
        self._record_paused_total = 0.0
        self.lbl_record_time.setText(clock(0))
        # 轨迹从零开始。第一条等录音状态真的翻到 Recording 再记（见
        # _on_recorder_state）——按下按钮到文件真的开始写之间还有一点延迟。
        self._rec_segments = []
        self._rec_started = None
        hint = "原音 + 你的声音" if self._rec_source_path() else "只录你的声音"
        self.lbl_recording.setText(
            f"正在录…（{self.current_input_name() or '默认麦克风'} · {hint}）"
        )
        self.recorder.record()

    def stop_recording(self):
        if self.recorder is not None and self._record_session_active:
            self.recorder.stop()

    def _record_tick(self):
        self._record_seconds += 1
        self.lbl_record_time.setText(clock(self._record_seconds))

    def _on_recorder_state(self, state):
        recording = state == QMediaRecorder.RecorderState.RecordingState
        paused = state == QMediaRecorder.RecorderState.PausedState
        active = recording or paused
        was_active = self._recording
        self._recording = recording

        now = time.monotonic()
        if recording:
            if not was_active:
                self._record_seconds = 0
                self._record_paused_at = None
                self._record_paused_total = 0.0
                self.lbl_record_time.setText(clock(0))
                self._rec_started = now
                self._rec_segments = []
            elif self._record_paused_at is not None:
                self._record_paused_total += max(0.0, now - self._record_paused_at)
                self._record_paused_at = None
            self._record_timer.start()
            self._rec_open_segment()
        elif paused:
            self._record_timer.stop()
            if self._record_paused_at is None: self._record_paused_at = now
            self._rec_close_segment()
        else:
            self._record_timer.stop()
            if self._record_paused_at is not None:
                self._record_paused_total += max(0.0, now - self._record_paused_at)
                self._record_paused_at = None
            self._rec_close_segment()

        restyle(self.btn_record, "recordButtonActive" if active else "recordButton")
        self.btn_record.setText("■ 停止" if active else "● 录音")
        self.btn_pause_record.setEnabled(active)
        self.btn_pause_record.setText("Ⅱ 继续" if paused else "Ⅱ 暂停")
        if not active and self._record_session_active:
            self._record_session_active = False
            self._finish_recording()

    def _finish_recording(self):
        """停下来之后才知道最终落在哪个文件上（`actualLocation`），把结果说给用户听。"""
        if self.recorder is None:
            return
        location = self.recorder.actualLocation().toLocalFile() or \
            self.recorder.outputLocation().toLocalFile()
        if not location or not os.path.isfile(location) or not os.path.getsize(location):
            return
        self._last_recording = location
        size = os.path.getsize(location)

        # 确保所有轨迹段都已正常闭合
        now = self._rec_elapsed()
        for seg in self._rec_segments:
            if seg.get("end") is None:
                seg["end"] = max(seg.get("start", 0.0), now)
            elif seg["end"] < seg.get("start", 0.0):
                seg["end"] = seg.get("start", 0.0)

        segments, self._rec_segments = self._rec_segments, []
        self._rec_started = None
        if segments:
            self._mix_recording(location, size, segments)
            return
        self.lbl_recording.setText(
            f"已保存 {os.path.basename(location)}（{audio.human_size(size)}）"
            f" · 来自 {self.current_input_name() or '默认麦克风'}"
        )
        self.lbl_recording.setToolTip(location)
        self._warn_if_silent(location, size)

    # ---- 原音轨迹：只在录音期间记，录完据此合成 ----

    def _rec_elapsed(self):
        """录音内部的时间轴（秒），不包含暂停期间。"""
        if self._rec_started is None: return 0.0
        paused = self._record_paused_total
        if self._record_paused_at is not None:
            paused += max(0.0, time.monotonic() - self._record_paused_at)
        return max(0.0, time.monotonic() - self._rec_started - paused)

    def _rec_source_path(self):
        """当前曲目在音频库里的绝对路径；没有可播的文件就 `None`。"""
        item = self._current_item()
        if item is None:
            return None
        absolute = audio.library_path(item["path"], AUDIO_DIR)
        return absolute if os.path.isfile(absolute) else None

    def _rec_open_segment(self, position_ms=None):
        """原音从这一刻起（继续）出声——按当前位置开一段轨迹。

        **位置取 `last_position` 而不是 `player.position()`**：`_seek` 刚发出时
        `setPosition` 还没生效，播放器读回来的是旧位置；而轨迹要的是"接下来听到的
        是从哪一秒开始的"，那正是 `_seek` 刚写进 `last_position` 的值。
        """
        if not self._recording:
            return
        if self.player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            # 原音没在响，轨迹上就不该有这一段。按下录音时播放正好停着就是这种情况
            # ——不拦的话，那几秒会被算成"原音在这里响过"。
            return
        path = self._rec_source_path()
        if path is None or not os.path.isfile(path):
            return

        now = self._rec_elapsed()

        # 如果上一段还开着：
        if self._rec_segments and self._rec_segments[-1]["end"] is None:
            prev = self._rec_segments[-1]
            if prev.get("path") == path:
                # 同一个音频文件，已经在记了，不重复开段
                return
            # 切了新音频但旧段还没封口：立即封口旧段
            prev["end"] = max(prev.get("start", 0.0), now)

        if position_ms is None:
            position_ms = self.last_position
        pos_sec = max(0.0, float(position_ms or 0) / 1000.0)

        # 校验避免 seek 超出文件自身时长导致 ffmpeg 解码 0 帧
        item = self._current_item()
        dur_ms = item.get("duration_ms") if item else None
        if dur_ms and dur_ms > 0:
            max_pos = float(dur_ms) / 1000.0
            if pos_sec >= max_pos:
                return

        self._rec_segments.append({
            "start": now,
            "end": None,
            "path": path,
            "pos": pos_sec,
            "speed": float(self.player.playbackRate() or 1.0),
        })

    def _rec_close_segment(self):
        """原音停了（暂停 / 播放结束 / 录音结束 / 切曲目）：给当前这一段封口。"""
        if self._rec_segments and self._rec_segments[-1]["end"] is None:
            now = self._rec_elapsed()
            self._rec_segments[-1]["end"] = max(self._rec_segments[-1].get("start", 0.0), now)

    def _rec_jump(self, position_ms):
        """原音跳到别处去了（拖进度条 / AB 回跳 / 换语速）。

        旧的一段在这里封口；**正在播放**就从新位置另起一段，暂停或停止时只封口不开
        新段——那几秒原音本来就没出声，混出来的音频里也不该有它。
        """
        if not self._recording:
            return
        self._rec_close_segment()
        self._rec_open_segment(position_ms)

    def _mix_recording(self, location, size, segments):
        """把**录音期间实际听到的那段原音**贴回录音里，合成"原音 + 你的声音"。

        **为什么是事后按轨迹重建，而不是"直接录系统正在放的声音"**：Qt 没有把播放器的
        输出接进捕获链的公开接口（`QMediaCaptureSession` 的 `setAudioOutput` 是给监听用
        的，不是混音），Windows 的环回采集（WASAPI loopback）在 QtMultimedia 里也没开放。
        而 ffmpeg 本来就是本项目的依赖，照轨迹剪一遍最简单，也最可控——混不成功时
        **人声那条还在**，不会两头落空。

        轨迹就是答案。原来的做法是"按下录音那一刻的位置 + 整段原音"，它有两个必然的
        错法：你只念了一半就停手，混出来的后半段接着放原音（只剩听力没有你）；你念到
        中间拖了进度条或换了曲目，贴上去的还是开头那一段。现在每一段都带着"在录音的
        第几秒、从原音的哪一秒起、放了多久"，剪出来和你听到的是一回事。
        """
        if self._closed:
            return
        if self._mix_worker is not None and self._mix_worker.isRunning():
            self._show_banner("info", "上一次合成还没跑完，等它结束再录。")
            self.lbl_recording.setText(f"已保存 {os.path.basename(location)}（仅人声）")
            return

        # 过滤并清洗轨迹段：
        # 1. 音频源文件必须真实存在
        # 2. 持续时长 >= 0.15 秒（过滤切曲目/拖拽产生的极小碎片，防止 ffmpeg amix 因空帧崩溃）
        # 3. 确保 pos 和 speed 合法
        valid_segments = []
        for seg in segments:
            path = seg.get("path")
            if not path or not os.path.isfile(path):
                continue
            start = float(seg.get("start", 0.0))
            end = float(seg.get("end") if seg.get("end") is not None else start)
            wall = end - start
            if wall < 0.15:
                continue
            pos = max(0.0, float(seg.get("pos", 0.0)))
            speed = float(seg.get("speed") or 1.0)
            if speed <= 0:
                speed = 1.0

            valid_segments.append({
                "start": start,
                "end": end,
                "path": path,
                "pos": pos,
                "speed": speed,
            })

        if not valid_segments:
            self.lbl_recording.setText(
                f"已保存 {os.path.basename(location)}（仅人声，{audio.human_size(size)}）"
                f" · 来自 {self.current_input_name() or '默认麦克风'}"
            )
            self.lbl_recording.setToolTip(location)
            self._warn_if_silent(location, size)
            return

        mixed = os.path.join(
            os.path.dirname(location),
            f".temp_mix_{os.path.basename(location)}"
        )
        if not mixed.lower().endswith(".m4a"):
            mixed = os.path.splitext(mixed)[0] + ".m4a"
        args = self._mix_args(location, valid_segments, mixed)
        self.lbl_recording.setText(
            f"正在合成原音…（{len(valid_segments)} 段 · {audio.human_size(size)} 的人声）"
        )
        diagnostic_log = mixed + ".ffmpeg.log"
        self._mix_diagnostic_log = diagnostic_log
        try:
            with open(diagnostic_log, "w", encoding="utf-8") as log:
                log.write(f"recording: {location}\nrecording_size: {size}\nsegments: {len(valid_segments)}\n")
                for index, seg in enumerate(valid_segments, start=1):
                    log.write(f"segment {index}: start={seg['start']:.3f} end={seg['end']:.3f} wall={seg['end']-seg['start']:.3f} pos={seg['pos']:.3f} speed={seg['speed']:.4f} path={seg['path']}\n")
                log.write(f"command: ffmpeg {shlex.join(args)}\n")
        except OSError:
            diagnostic_log = None
        self._mix_worker = FFmpegWorker(args, mixed, diagnostic_log=diagnostic_log)
        self._mix_worker.finished.connect(self._on_mix_finished)
        self._mix_worker.start()

    @staticmethod
    def _mix_args(location, segments, output):
        """轨迹 → 一条 ffmpeg 命令行。

        **单独拆出来是为了能读第二遍**：滤波器图是这一页最难在屏幕上看出对错的地方。

        一段原音一个输入（`-ss` 定起点、`-t` 定长度），`adelay` 把这段推到它在录音里
        该出现的位置上，最后和麦克风那一路 `amix`。

        * **`-t` 让每段有头有尾**——这是"只念了一半，后面却还在放原音"的解药。
        * **原音压到 `MIX_ORIGINAL_GAIN`**：两路等响时原音会盖住你的声音。只降原音，
          自己的声音不动——跟读录音里"我念的"才是主角。
        * **`aformat` 先统一成 48k 立体声**：`adelay` 的延迟值要按声道数逐个写
          （`1000|1000`），先统一才写得对，也省得 amix 去猜两路的布局。
        * **语速算进去**：0.75 倍速下，同样的录音秒数只走过四分之三的素材，所以
          先按 `时长 × 语速` 截取，再用 `atempo` 拉回同样的时长，才和你听到的对齐。
        * **`adelay` 必须在 `atempo` 之前，且延迟先乘语速。** 反过来写（先 atempo 再
          adelay）ffmpeg 7.1 会让第一帧带一个接近 INT64_MAX 的 PTS，整条 m4a 写出
          来没有可读的时长（`Duration: N/A`、码率 70929 kb/s），播放器拖不动。
          atempo 是整条流等比缩放，所以延迟先乘语速、之后被缩回去，长度正好。
          别再"顺手"把两个滤镜调换回来。
        """
        args = ["-y", "-nostdin"]
        filters = []
        for index, segment in enumerate(segments):
            wall = max(0.1, float(segment["end"]) - float(segment["start"]))
            speed = float(segment["speed"] or 1.0)
            args += [
                "-ss", f"{float(segment['pos']):.3f}",
                "-t", f"{wall * speed:.3f}",
                "-i", segment["path"],
            ]
            # volume 放在最前面：它只按样本做增益，离 atempo/adelay 那套时间戳机制
            # 越远越好（顺序坑见上面那条）
            chain = (
                "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
                f"volume={MIX_ORIGINAL_GAIN},"
                "asetpts=PTS-STARTPTS"
            )
            delay = int(round(float(segment["start"]) * speed * 1000))
            if delay > 0:
                chain += f",adelay={delay}|{delay}"
            if abs(speed - 1.0) > 0.01:
                chain += f",atempo={speed:.4f}"
            filters.append(f"[{index}:a]{chain}[s{index}]")

        voice = len(segments)
        args += ["-i", location]
        filters.append(
            f"[{voice}:a]aformat=sample_fmts=fltp:sample_rates=48000:"
            f"channel_layouts=stereo,asetpts=PTS-STARTPTS[v]"
        )
        # normalize=0：amix 默认会把每路都压一半，那样原音就比你听的时候小一截。
        # duration=longest：麦克风那一路是时间轴的真相——暂停时原音本来就该是静的，
        # 录完还在停着的那几秒也只该剩下你的声音。
        mix = "".join(f"[s{index}]" for index in range(len(segments))) + "[v]"
        filters.append(
            f"{mix}amix=inputs={len(segments) + 1}:duration=longest:normalize=0[a]"
        )
        args += [
            "-filter_complex", ";".join(filters),
            "-map", "[a]",
            "-c:a", "aac", "-b:a", "160k",
            output,
        ]
        return args

    def _on_mix_finished(self, success, result):
        raw = self._last_recording
        diagnostic_log = getattr(self, "_mix_diagnostic_log", None)
        if not success:
            detail = str(result)
            if diagnostic_log and os.path.isfile(diagnostic_log):
                detail = f"{detail}；诊断日志：{diagnostic_log}"
            if "未检测到 ffmpeg" in str(result):
                self._show_banner(
                    "warning",
                    "没检测到 ffmpeg，这条录音里只有你的声音。装上 ffmpeg 之后，"
                    "录音会自动把原音一起混进去。",
                )
            else:
                self._show_banner("warning", f"原音合成失败：{detail}。人声录音已完整保存。")
            if raw and os.path.isfile(raw):
                self.lbl_recording.setText(f"已保存 {os.path.basename(raw)}（仅人声）")
                self.lbl_recording.setToolTip(raw)
            return

        if diagnostic_log and os.path.isfile(diagnostic_log):
            try: os.remove(diagnostic_log)
            except OSError: pass
        self._mix_diagnostic_log = None
        final_path = raw
        if raw and os.path.isfile(raw):
            self._warn_if_silent(raw, os.path.getsize(raw))
            if os.path.splitext(raw)[1].lower() != ".m4a" and result.lower().endswith(".m4a"):
                target_path = os.path.splitext(raw)[0] + ".m4a"
                try:
                    os.replace(result, target_path)
                    self._remove_quietly(raw)
                    final_path = target_path
                except OSError:
                    final_path = result
            else:
                try:
                    os.replace(result, raw)
                    final_path = raw
                except OSError:
                    try:
                        os.remove(raw)
                        os.rename(result, raw)
                        final_path = raw
                    except OSError:
                        final_path = result
        else:
            final_path = result

        size = os.path.getsize(final_path)
        self._last_recording = final_path
        self.lbl_recording.setText(
            f"已保存 {os.path.basename(final_path)}（原音 + 你的声音，{audio.human_size(size)}）"
        )
        self.lbl_recording.setToolTip(final_path)

    def _warn_if_silent(self, location, size):
        """录到一条**静音**音轨时，文件照样存在、时长照样有，只是没声音——
        用户看到的是"录音没声音"，却不知道是麦克风的问题。这里替他判断一下。

        判据只对压缩格式成立：AAC 的静音段几乎不占字节，正常说话大约每秒十几 KB，
        而**低于 2KB/秒基本就是没采到东西**。WAV 是定长，字节数说明不了任何问题，
        所以只对非 WAV 提示，宁可漏报不误报。
        """
        if os.path.splitext(location)[1].lower() == ".wav":
            return
        seconds = max(1, self._record_seconds)
        if size >= seconds * 2000:
            return
        self._show_banner(
            "warning",
            f"这条录音几乎没有声音（{seconds} 秒只有 {audio.human_size(size)}）。"
            f"当前用的是「{self.current_input_name() or '默认麦克风'}」——"
            "检查一下它是不是被静音、被物理静音键关掉，或者 Windows 的麦克风权限没开；"
            "也可以在「设置与数据 → 音频设备」里换一路麦克风再试。",
        )

    def _on_recorder_error(self, error, message):
        self._record_timer.stop()
        self._record_paused_at = None
        self._record_paused_total = 0.0
        self._record_session_active = False
        self._recording = False
        restyle(self.btn_record, "recordButton")
        self.btn_record.setText("● 录音")
        self.lbl_recording.setText("录音失败")
        self._show_banner(
            "warning",
            f"录音失败：{message or error}。看看麦克风是不是被别的程序占着。",
        )

    # ---- 播放 / 录音设备 ----

    def refresh_devices(self):
        """设置页换了设备之后由 `MainWindow` 调。"""
        self.apply_audio_devices()

    def apply_audio_devices(self):
        """把设置里指定的播放/录音设备装上去。

        **输出必须显式设置，不能指望它跟随系统默认。** Qt 在 `QAudioOutput` 建好那一刻就把
        设备定下来了（Windows 上是 WMF 后端），之后用户在系统里换默认输出，它不会跟着换
        ——"我切了系统默认设备却还是电脑扬声器"就是这么来的。

        **输入相反：没指定就一个字都别动。** `QAudioInput` 自己的默认本来就跟着系统的
        *通信*设备走（插着耳麦时就是耳麦的麦），而我上次改成强行装 `defaultAudioInput()`
        之后，这台机器被切到了"阵列麦克风 (AMD Audio Device)"——那一路要是被静音、
        被物理静音键关掉、或没拿到 Windows 的麦克风权限，录出来就是一条**安静的音轨**，
        而且不报任何错。少改一处比多改一处安全。
        """
        output = self._match_device(QMediaDevices.audioOutputs(),
                                    self.database.get_audio_device("output"))
        if output is None:
            output = QMediaDevices.defaultAudioOutput()
        if not output.isNull() and output.description() != self.audio_output.device().description():
            self.audio_output.setDevice(output)

        if self.audio_input is not None:
            source = self._match_device(QMediaDevices.audioInputs(),
                                        self.database.get_audio_device("input"))
            # `source is None` 有两种情况：没配过（空串）、配过但设备不在了。
            # 两种都交给 Qt 自己的默认，不替用户选。
            if source is not None and source.description() != self.audio_input.device().description():
                self.audio_input.setDevice(source)

    def current_input_name(self):
        """当前录音用的是哪一路（给提示条用）。"""
        if self.audio_input is None:
            return ""
        return self.audio_input.device().description()

    @staticmethod
    def _match_device(devices, wanted):
        """按**描述名**找设备；没指定或找不到返回 `None`（调用方退回系统默认）。"""
        if not wanted:
            return None
        for device in devices:
            if device.description() == wanted:
                return device
        return None

    # ==================================================================
    # 冲刷与收尾
    # ==================================================================

    def flush_study_session(self):
        """**这一页对外的唯一冲刷入口**：跟读时长、曲目进度、位置、AB、语速。

        调用点：暂停、切曲、切列表、移出列表、离开页面（`hideEvent`）、关窗口
        （`MainWindow.closeEvent`）。少一处，用户就白练一段——所以新加出口时调它，
        不要再写第二条冲刷路径。
        """
        if self._closed:
            # 关库之后还会来一次 `hideEvent`（窗口拆除时 Qt 才发），那时再写
            # 就是 `Cannot operate on a closed database`——退出路径上崩一下，
            # 用户看到的是"关窗口报错"，而不是"关窗口关得慢了一点"。
            return
        if self.track_id is not None:
            if self.track_unlogged_ms or self.loop_count:
                self.database.add_track_listened(
                    self.track_id, self.track_unlogged_ms, self.loop_count
                )
                self.track_unlogged_ms = 0
                self.loop_count = 0
            self.database.save_track_progress(
                self.track_id,
                position_ms=None,
                speed=dict(SPEEDS).get(self.combo_speed.currentText(), 1.0),
            )
            self._update_track_meta()

        # 活动日志按整分钟记（4.3）；不足一分钟的部分留着，下次接着攒
        minutes = self.unlogged_ms // 60000
        if minutes:
            item = self._current_item()
            self.database.record_activity(
                "shadowing_minutes",
                minutes,
                note=item["title"] if item else None,
                unit="分钟",
                ref_type="audio_track",
                ref_id=self.track_id,
            )
            self.unlogged_ms -= minutes * 60000

        self.database.set_playback_state(self.playlist_id, self.track_id)
        # 还没到防抖时间就离开页面/关窗口时，页码也得落下
        self._save_pdf_pages()

    def hideEvent(self, event):
        # 离开 P2 也要结账（4.3 明确点名这是原实现缺失的路径）。
        # 音频**继续播**——切去别的页面再回来不该被打断（4.2 状态保持）。
        self.flush_study_session()
        super().hideEvent(event)

    # ---- 拖拽导入（4.3 核心组件："支持多选与拖拽"）----

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        """把文件拖进来 = 导入音频。

        曲目列表自己是 InternalMove，外部 URL 的 drop 它不接，事件会冒泡到这里——
        所以在列表上松手也能导入，用户不需要瞄准。
        """
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if not paths:
            return
        event.acceptProposedAction()
        audio_paths = [
            path for path in paths
            if os.path.splitext(path)[1].lower() in audio.AUDIO_EXTENSIONS
        ]
        video_paths = [
            path for path in paths
            if os.path.splitext(path)[1].lower() in audio.VIDEO_EXTENSIONS
        ]
        if audio_paths:
            self.import_paths(audio_paths)
        if video_paths:
            self._show_banner("info", "已停止支持视频直接转换，请先转为音频文件（MP3 / M4A 等）后再导入。")
        elif not audio_paths:
            self._show_banner("warning", "拖进来的文件不是支持的音频格式，未导入。")

    def shutdown(self):
        """关窗口时把后台合成收干净（与 P3 的 `vault_view.shutdown()` 同一条纪律：
        QThread 还在跑就拆窗口，Qt 会打印 "Destroyed while thread is still running"）。

        同时把 `_closed` 立起来：库已经被关掉了，此后到达的 `hideEvent` 不该再写库。
        """
        self._closed = True
        self._page_timer.stop()
        # 正在录音时必须收干净：录了一半的文件既不能播也不能续，比没录还糟
        self._record_timer.stop()
        if self._recording:
            self.stop_recording()
        # 合成线程同理：跑到一半被拆掉，输出就停在一个半截文件上
        mixer = self._mix_worker
        if mixer is not None and mixer.isRunning():
            mixer.cancel()
            mixer.wait(3000)
            # 半截的合成产物留着没有意义，还会被当成一条真录音
            if mixer.output:
                self._remove_quietly(mixer.output)
        self.player.stop()

    # ---- 错误提示 ----

    def _show_banner(self, level, text, action_text=None, handler=None):
        self._banner_action = handler
        self.banner.show_message(level, text, action_text=action_text)

    def _on_banner_action(self):
        handler = self._banner_action
        self._banner_action = None
        if handler is not None:
            handler()

    # ---- 曲目右键菜单 ----

    def _track_menu(self, pos):
        entry = self.track_list.itemAt(pos)
        if entry is None:
            return
        self.track_list.setCurrentRow(self.track_list.row(entry))
        track_id = entry.data(Qt.UserRole)
        item = next((row for row in self._items if row["track_id"] == track_id), None)
        missing = item is None or not audio.exists_in_library(item["path"], AUDIO_DIR)

        menu = QMenu(self)
        play = menu.addAction("播放这个音频")
        done = menu.addAction("取消已磕完" if item and item["status"] == "done" else "标记已磕完")
        reveal = menu.addAction("在文件夹中显示")
        reimport = menu.addAction("重新导入这个音频…")
        reimport.setEnabled(missing)
        menu.addSeparator()
        remove = menu.addAction("移出列表")
        menu.addSeparator()
        rename_list = menu.addAction("重命名列表…")
        delete_list = menu.addAction("删除列表…")

        chosen = menu.exec(self.track_list.viewport().mapToGlobal(pos))
        if chosen is play:
            self.load_track(track_id)
        elif chosen is done:
            self.track_id = track_id
            self.toggle_done()
        elif chosen is reveal:
            self._reveal(track_id)
        elif chosen is reimport:
            self.reimport_track(track_id)
        elif chosen is remove:
            self.track_id = track_id
            self.remove_current_from_playlist()
        elif chosen is rename_list:
            self.rename_current_playlist()
        elif chosen is delete_list:
            self.delete_current_playlist()

    def _reveal(self, track_id):
        item = self.database.get_track(track_id)
        if item is None:
            return
        path = audio.library_path(item["path"], AUDIO_DIR)
        folder = os.path.dirname(path)
        if not os.path.isdir(folder):
            self._show_banner("warning", "音频库目录不存在。")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def reimport_track(self, track_id):
        """把新文件换到这一段身上：**AB 点、跟读时长、列表位置全部保留**。

        做法是先复制入库、再把 `audio_tracks.path` 指向新文件，而不是"删一段、加一段"——
        后者会把用户攒下的跟读记录一起删掉，而"文件丢了我重新给一个"是最不该受罚的操作。
        """
        item = self.database.get_track(track_id)
        if item is None:
            return
        paths, _ = QFileDialog.getOpenFileNames(self, "重新导入这个音频", "", audio.AUDIO_FILTER)
        if not paths:
            return
        try:
            record = audio.copy_into_library(paths[0], AUDIO_DIR)
        except audio.AudioImportError as error:
            self._show_banner("warning", str(error))
            return
        self.database.replace_track_file(
            track_id,
            record["path"],
            record["title"],
            record["size"],
            audio.content_hash(record["absolute"]),
        )
        if track_id == self.track_id:
            self.track_id = None
            self.load_track(track_id)
        self.reload_tracks()
        show_toast(self.window(), f"已重新关联：{record['title']}")

    # ==================================================================
    # 跨天继续（流程 E）
    # ==================================================================

    def restore_playback_state(self):
        """回到上次的列表与曲目。**不自动播放**——打开工具不该突然出声。"""
        state = self.database.playback_state()
        if not state or state["track_id"] is None:
            return
        if self.database.get_track(state["track_id"]) is None:
            return
        if state["playlist_id"] is not None:
            self.playlist_id = state["playlist_id"]
            self.reload_playlists()
            self.reload_tracks()
            self._render_documents()
        self.load_track(state["track_id"], autoplay=False)