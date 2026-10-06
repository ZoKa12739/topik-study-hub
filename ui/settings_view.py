"""P5 · 设置与数据（`PRODUCT_SPEC` 4.6）。

本页解决三件事：

1. 把此前**无处配置**的项集中起来：考试日期与届次、资料根目录。
   这两项原先是硬编码的——资料根目录硬编码成"从 `__file__` 向上三级"，项目一移动就失效
   （实测已失效）。配置化见 D10 方案 C。
2. 提供"本地优先"定位要求的**数据备份出口**（6.5）：导出/导入第二层数据、手动备份、
   存储占用可视化。没有版本控制的环境里，备份是必需项而非可选项。
3. 外部工具自查（ffmpeg）：它是 P2 转码的硬依赖，缺了用户只会看到"转换失败"，
   却不知道原因。**Tesseract 已随 OCR 整体移除**（词表改走 TSV 导入），不再自查。

**联网功能分组（4.6 的 H6）属第 8 期，本页暂不提供**——它需要先有联网开关框架与密钥管理
（12.5），现在放一个空壳开关就是假实现。见 `PRODUCT_SPEC` §10 第 8 期。

**不使用 `QMessageBox` 报告可预期失败**（见 `ui/components.py` 与 `CLAUDE.md`）：
目录不存在、导入格式不符都走行内提示条。唯一保留模态框的地方是**导入前的二次确认**——
那是真正的破坏性操作确认，且此时事件循环正在运行。
"""

import os
import shutil
import subprocess
from datetime import date

from PySide6.QtCore import QDate, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtMultimedia import QMediaDevices
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core.config import DATA_DIR
from core.database import BACKUP_KEEP
from ui.components import Banner, show_toast
from ui.icons import icon
from ui.style import set_state

# 快捷键只读展示（PRODUCT_SPEC 2.6）。Q4 已定：不做自定义，先观察真实使用。
_SHORTCUTS = (
    ("Ctrl+1 ~ Ctrl+5", "切换到今日学习 / 单词仓 / 影子跟读 / 资料库 / 知识碎片"),
    ("Ctrl+,", "打开本页（设置与数据）"),
    ("Ctrl+F", "聚焦当前页搜索框"),
    ("Space", "播放 / 暂停（仅影子跟读，输入框聚焦时失效）"),
    ("A / B / C", "设置 A 点 / B 点 / 清除（仅影子跟读）"),
    ("← / →", "后退 / 前进 3 秒（仅影子跟读）"),
    ("Ctrl+S", "保存当前笔记"),
)


def format_size(num_bytes):
    """人类可读的字节数。"""
    value = float(num_bytes or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


class SettingsView(QWidget):
    """P5 设置与数据页。"""

    # 资料根目录 / 脚本路径变更后发出，让 MainWindow 去刷新 P1/P3/P4
    paths_changed = Signal()
    # 播放/录音设备变更后发出，让 P2 把新设备装到播放器上
    audio_devices_changed = Signal()
    # 导入备份后发出，让 MainWindow 刷新全部数据驱动的页面
    data_reloaded = Signal()

    def __init__(self, database):
        super().__init__()
        self.database = database
        self.init_ui()
        self.refresh()

    # ---------------------------------------------------------------- 布局

    def init_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setObjectName("settingsScroll")
        outer.addWidget(scroll)

        content = QWidget()
        scroll.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(16)

        eyebrow = QLabel("设置与数据")
        eyebrow.setObjectName("pageEyebrow")
        layout.addWidget(eyebrow)
        title = QLabel("配置与备份")
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        subtitle = QLabel("考试信息、资料位置、外部工具与数据备份。改动立即生效并写入本地数据库。")
        subtitle.setObjectName("pageSubtitle")
        layout.addWidget(subtitle)

        self.banner = Banner()
        layout.addWidget(self.banner)

        self._build_exam_group(layout)
        self._build_paths_group(layout)
        self._build_tools_group(layout)
        self._build_audio_group(layout)
        self._build_shortcuts_group(layout)
        self._build_data_group(layout)
        layout.addStretch()

    def _group(self, layout, title, hint=None):
        card = QFrame()
        card.setObjectName("surface")
        box = QVBoxLayout(card)
        box.setContentsMargins(16, 16, 16, 16)
        box.setSpacing(10)
        heading = QLabel(title)
        heading.setObjectName("sectionTitle")
        box.addWidget(heading)
        if hint:
            note = QLabel(hint)
            note.setObjectName("faint")
            note.setWordWrap(True)
            box.addWidget(note)
        layout.addWidget(card)
        return box

    def _field_row(self, box, caption, control, button=None, caption_width=96):
        row = QHBoxLayout()
        row.setSpacing(10)
        label = QLabel(caption)
        label.setObjectName("muted")
        label.setFixedWidth(caption_width)
        row.addWidget(label)
        row.addWidget(control, 1)
        if button is not None:
            row.addWidget(button)
        box.addLayout(row)
        return row

    # ---------------------------------------------------------------- 分组一：备考设置

    def _build_exam_group(self, layout):
        box = self._group(layout, "备考设置", "倒计时与届次名称会显示在首页。")

        self.input_label = QLineEdit()
        self.input_label.setPlaceholderText("例如：第 109 届 TOPIK 考试")
        self._field_row(box, "届次名称", self.input_label)

        self.input_date = QDateEdit()
        self.input_date.setCalendarPopup(True)
        self.input_date.setDisplayFormat("yyyy-MM-dd")
        self._field_row(box, "考试日期", self.input_date)

        button_row = QHBoxLayout()
        button_row.addStretch()
        self.btn_save_exam = QPushButton("保存考试信息")
        self.btn_save_exam.setObjectName("primaryButton")
        self.btn_save_exam.clicked.connect(self.save_exam)
        button_row.addWidget(self.btn_save_exam)
        box.addLayout(button_row)

    # ---------------------------------------------------------------- 分组二：资料位置

    def _build_paths_group(self, layout):
        box = self._group(
            layout,
            "资料位置",
            "资料根目录应含有 写作 / 听力 / 阅读 / 单词 等子文件夹。"
            "移动资料后在这里重新指定即可，不必改动程序。",
        )

        self.input_root = QLineEdit()
        self.input_root.setReadOnly(True)
        btn_root = QPushButton("更改")
        btn_root.setObjectName("iconButton")
        btn_root.clicked.connect(self.change_material_root)
        self._field_row(box, "资料根目录", self.input_root, btn_root, caption_width=110)
        self.root_status = QLabel("")
        self.root_status.setObjectName("muted")
        self.root_status.setWordWrap(True)
        box.addWidget(self.root_status)

        # 跟读录音的落点（P2 的录音按钮用它）。与资料根目录分开配：一个是"用户已有的
        # 资料"，一个是"工具自己录出来的东西"，默认位置也不同。
        self.input_recording_dir = QLineEdit()
        self.input_recording_dir.setReadOnly(True)
        btn_recording = QPushButton("更改")
        btn_recording.setObjectName("iconButton")
        btn_recording.clicked.connect(self.change_recording_dir)
        self._field_row(box, "录音保存位置", self.input_recording_dir, btn_recording,
                        caption_width=110)

    # ---------------------------------------------------------------- 分组三：外部工具检测

    def _build_tools_group(self, layout):
        box = self._group(
            layout,
            "外部工具检测",
            "ffmpeg 是硬依赖：缺了它无法在工具内把 MKV/MP4 转成 MP3，可手动转换后再导入。",
        )

        self.lbl_ffmpeg = QLabel("ffmpeg：尚未检测")
        self.lbl_ffmpeg.setObjectName("muted")
        box.addWidget(self.lbl_ffmpeg)

        row = QHBoxLayout()
        row.addStretch()
        self.btn_detect = QPushButton("重新检测")
        self.btn_detect.setObjectName("iconButton")
        self.btn_detect.setIcon(icon("refresh"))
        self.btn_detect.clicked.connect(self.detect_tools)
        row.addWidget(self.btn_detect)
        box.addLayout(row)

    # ---------------------------------------------------------------- 分组四：音频设备

    def _build_audio_group(self, layout):
        """影子跟读用哪个设备放原音、用哪个设备录音。

        **为什么要有这一节**：Qt 在 `QAudioOutput` / `QAudioInput` 建好的那一刻就把设备
        定下来了，之后用户去系统里换默认输出，它不会跟着换——症状就是"换了系统默认
        设备，声音还是从电脑扬声器出来"。所以把选择权直接交给用户，而不是指望它跟随。
        """
        box = self._group(
            layout,
            "音频设备",
            "影子跟读的原音播放与跟读录音各用哪个设备。选「自动」时，"
            "播放走系统默认输出，录音交给 Qt 自己挑（通常跟着系统的通信设备，"
            "插着耳麦就是耳麦的麦）。",
        )

        self.combo_output = QComboBox()
        self.combo_input = QComboBox()
        self._field_row(box, "原音播放到", self.combo_output, caption_width=110)
        self._field_row(box, "录音来自", self.combo_input, caption_width=110)

        row = QHBoxLayout()
        row.addStretch()
        self.btn_rescan_audio = QPushButton("重新扫描设备")
        self.btn_rescan_audio.setObjectName("iconButton")
        self.btn_rescan_audio.setIcon(icon("refresh"))
        self.btn_rescan_audio.setToolTip("插拔耳机之后点一下")
        self.btn_rescan_audio.clicked.connect(self.rescan_audio_devices)
        row.addWidget(self.btn_rescan_audio)
        box.addLayout(row)

        self.combo_output.currentIndexChanged.connect(self.save_audio_devices)
        self.combo_input.currentIndexChanged.connect(self.save_audio_devices)

    def scan_audio_devices(self):
        """按当前的设备列表重建两个下拉框，并选回已保存的那一项。"""
        for combo, kind, devices, default_text in (
            (self.combo_output, "output", QMediaDevices.audioOutputs(), "自动"),
            (self.combo_input, "input", QMediaDevices.audioInputs(), "自动"),
        ):
            saved = self.database.get_audio_device(kind)
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(f"（{default_text}）", "")
            for device in devices:
                combo.addItem(device.description(), device.description())
            index = combo.findData(saved)
            combo.setCurrentIndex(index if index >= 0 else 0)
            combo.blockSignals(False)

    def rescan_audio_devices(self):
        self.scan_audio_devices()
        show_toast(self.window(), "设备列表已刷新")

    def save_audio_devices(self):
        self.database.set_audio_device("output", self.combo_output.currentData() or "")
        self.database.set_audio_device("input", self.combo_input.currentData() or "")
        self.audio_devices_changed.emit()

    # ---------------------------------------------------------------- 分组五：快捷键

    def _build_shortcuts_group(self, layout):
        box = self._group(layout, "快捷键", "目前只读展示；等观察到哪些键确有需要再考虑自定义。")
        for keys, desc in _SHORTCUTS:
            row = QHBoxLayout()
            row.setSpacing(10)
            key = QLabel(keys)
            key.setObjectName("shortcutKey")
            row.addWidget(key, 0)
            text = QLabel(desc)
            text.setObjectName("muted")
            text.setWordWrap(True)
            row.addWidget(text, 1)
            box.addLayout(row)

    # ---------------------------------------------------------------- 分组六：数据

    def _build_data_group(self, layout):
        box = self._group(
            layout,
            "数据",
            "导出只包含**不可重建**的数据（任务、活动、笔记与词状态、断点、配置、碎片标题与说明）；"
            f"词条、文件索引等可从源文件重建的内容不导出。备份保留最近 {BACKUP_KEEP} 份。",
        )

        self.lbl_usage = QLabel("存储占用：计算中…")
        self.lbl_usage.setObjectName("muted")
        self.lbl_usage.setWordWrap(True)
        box.addWidget(self.lbl_usage)

        self.lbl_schema = QLabel("")
        self.lbl_schema.setObjectName("faint")
        box.addWidget(self.lbl_schema)

        row = QHBoxLayout()
        row.setSpacing(10)
        self.btn_export = QPushButton("导出备份")
        self.btn_export.setObjectName("iconButton")
        self.btn_export.setIcon(icon("download"))
        self.btn_export.clicked.connect(self.export_backup)
        self.btn_import = QPushButton("导入备份")
        self.btn_import.setObjectName("iconButton")
        self.btn_import.setIcon(icon("upload"))
        self.btn_import.clicked.connect(self.import_backup)
        self.btn_open_dir = QPushButton("打开数据目录")
        self.btn_open_dir.setObjectName("iconButton")
        self.btn_open_dir.setIcon(icon("folder-open"))
        self.btn_open_dir.clicked.connect(self.open_data_dir)
        self.btn_backup_now = QPushButton("立即备份")
        self.btn_backup_now.setObjectName("iconButton")
        self.btn_backup_now.setIcon(icon("clipboard"))
        self.btn_backup_now.clicked.connect(self.backup_now)
        self.btn_export_anki = QPushButton("Anki 导出")
        self.btn_export_anki.setObjectName("iconButton")
        self.btn_export_anki.setIcon(icon("download"))
        self.btn_export_anki.setToolTip("导出待专攻单词（模糊与不认识）为 Anki 牌组 (TSV/CSV)")
        self.btn_export_anki.clicked.connect(self.export_anki)
        for button in (
            self.btn_export,
            self.btn_import,
            self.btn_open_dir,
            self.btn_backup_now,
            self.btn_export_anki,
        ):
            row.addWidget(button)
        row.addStretch()
        box.addLayout(row)

    # ---------------------------------------------------------------- 刷新与动作

    def refresh(self):
        """从数据库重新读取全部取值。切回本页或导入数据后调用。"""
        label, exam_date = self.database.get_exam()
        self.input_label.setText(label)
        parsed = QDate.fromString(exam_date, "yyyy-MM-dd")
        if parsed.isValid():
            self.input_date.setDate(parsed)
        else:
            self.input_date.setDate(QDate.currentDate())

        self.input_root.setText(self.database.get_material_root())
        self._refresh_root_status()
        self.input_recording_dir.setText(self.database.get_recording_dir())
        self.scan_audio_devices()
        self._refresh_usage()
        self.detect_tools()

    def _refresh_root_status(self):
        root = self.input_root.text().strip()
        if not root:
            self.root_status.setText("尚未设置：P3 资料库与 P4 碎片页会提示你先到这里指定目录。")
            set_state(self.root_status, "warning")
        elif not os.path.isdir(root):
            self.root_status.setText(f"该目录不存在，请重新选择：{root}")
            set_state(self.root_status, "danger")
        else:
            self.root_status.setText("目录有效。")
            set_state(self.root_status, "success")

    def _refresh_usage(self):
        usage = self.database.storage_usage()
        self.lbl_usage.setText(
            "存储占用："
            f"数据库 {format_size(usage['db'])} · "
            f"备份 {format_size(usage['backup'])} · "
            f"碎片 {format_size(usage['snippets'])} · "
            f"音频库 {format_size(usage['audio'])} · "
            f"跟读录音 {format_size(usage['recordings'])}"
        )
        self.lbl_schema.setText(
            f"数据目录：{usage['data_dir']}　·　数据库 schema 版本：{self.database.schema_version()}"
        )

    def save_exam(self):
        label = self.input_label.text().strip()
        if not label:
            self.banner.show_message("warning", "届次名称不能为空。")
            return
        chosen = self.input_date.date()
        if not chosen.isValid():
            self.banner.show_message("warning", "考试日期无效，未写入数据库。")
            return
        self.database.set_exam(label, chosen.toString("yyyy-MM-dd"))
        self.banner.clear()
        show_toast(self.window(), "考试信息已保存")

    def change_material_root(self):
        start = self.database.get_material_root() or str(DATA_DIR)
        chosen = QFileDialog.getExistingDirectory(self, "选择资料根目录", start)
        if not chosen:
            return
        if not os.path.isdir(chosen):
            self.banner.show_message("danger", f"该目录不存在：{chosen}")
            return
        self.database.set_material_root(chosen)
        self.input_root.setText(chosen)
        self._refresh_root_status()
        self.banner.clear()
        self.paths_changed.emit()
        show_toast(self.window(), "资料根目录已更新")

    def change_recording_dir(self):
        """改跟读录音的落点。目录不存在会被创建（那是工具自己要写的地方，
        不像资料根目录必须已经存在）。"""
        start = self.database.get_recording_dir() or str(DATA_DIR)
        chosen = QFileDialog.getExistingDirectory(self, "选择录音保存位置", start)
        if not chosen:
            return
        try:
            os.makedirs(chosen, exist_ok=True)
        except OSError as error:
            self.banner.show_message(
                "danger", f"这个位置不能用：{error.strerror or error}"
            )
            return
        self.database.set_recording_dir(chosen)
        self.input_recording_dir.setText(chosen)
        self.banner.clear()
        self._refresh_usage()
        self.paths_changed.emit()
        show_toast(self.window(), "录音保存位置已更新")

    # ---------------------------------------------------------------- 外部工具

    def detect_tools(self):
        ok_ffmpeg, detail_ffmpeg = self._probe("ffmpeg", ["-version"], "ffmpeg")
        self._apply_tool_status(self.lbl_ffmpeg, "ffmpeg", ok_ffmpeg, detail_ffmpeg)

    @staticmethod
    def _probe(executable, args, display):
        """检测外部工具。只捕获可预期的失败（找不到可执行文件 / 子进程错误）。"""
        path = shutil.which(executable)
        if not path:
            return False, f"未找到 {display}，请安装后加入 PATH"
        try:
            result = subprocess.run(
                [path, *args],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                timeout=8,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except (OSError, subprocess.SubprocessError) as error:
            return False, f"{display} 无法运行：{error}"
        first_line = (result.stdout or "").strip().splitlines()
        version = first_line[0] if first_line else ""
        if result.returncode == 0:
            return True, version
        return False, f"{display} 返回错误码 {result.returncode}"

    def _apply_tool_status(self, label, display, ok, detail):
        if ok:
            label.setText(f"{display}：就绪　{detail}"[:160])
            set_state(label, "success")
        else:
            label.setText(f"{display}：{detail}")
            set_state(label, "warning")

    # ---------------------------------------------------------------- 数据备份

    def export_backup(self):
        default_name = f"topik-backup-{date.today().isoformat()}.json"
        path, _ = QFileDialog.getSaveFileName(
            self, "导出备份", str(DATA_DIR / default_name), "JSON 备份 (*.json)"
        )
        if not path:
            return
        try:
            self.database.export_to_file(path)
        except OSError as error:
            self.banner.show_message("danger", f"导出失败，无法写入文件：{error}")
            return
        self.banner.clear()
        show_toast(self.window(), "备份已导出")

    def import_backup(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择备份文件", str(DATA_DIR), "JSON 备份 (*.json);;所有文件 (*)"
        )
        if not path:
            return
        confirmed = QMessageBox.question(
            self,
            "确认导入",
            "导入会用备份内容**替换**当前的任务、活动记录、笔记与词状态、断点与配置。\n"
            "此操作不可撤销，建议先「导出备份」留一份当前数据。\n\n确定继续吗？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirmed != QMessageBox.Yes:
            return
        try:
            self.database.import_from_file(path)
        except ValueError as error:
            # 格式/版本问题：导入是原子的，现有数据未被改动
            self.banner.show_message("danger", f"导入失败，现有数据保持不变：{error}")
            return
        except OSError as error:
            self.banner.show_message("danger", f"无法读取备份文件：{error}")
            return
        self.banner.clear()
        self.refresh()
        self.data_reloaded.emit()
        show_toast(self.window(), "备份已导入")

    def open_data_dir(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(DATA_DIR)))

    def backup_now(self):
        destination = self.database.backup_now()
        self._refresh_usage()
        show_toast(self.window(), f"已备份：{destination.name}")

    def export_anki(self):
        count = self.database.count_anki_export_words()
        if count == 0:
            self.banner.show_message("warning", "当前没有待专攻的单词（状态为模糊或不认识）。")
            return

        default_name = f"topik-anki-待专攻-{date.today().isoformat()}.tsv"
        root = self.database.get_material_root()
        start_dir = (
            os.path.join(root, "单词")
            if (root and os.path.isdir(os.path.join(root, "单词")))
            else str(DATA_DIR)
        )
        default_path = os.path.join(start_dir, default_name)

        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出 Anki 牌组",
            default_path,
            "Anki TSV 牌组 (*.tsv);;Anki CSV 牌组 (*.csv);;文本文件 (*.txt);;所有文件 (*)",
        )
        if not path:
            return

        try:
            exported = self.database.export_anki_file(path)
        except OSError as error:
            self.banner.show_message("danger", f"导出失败，无法写入文件：{error}")
            return

        self.banner.clear()
        show_toast(self.window(), f"已导出 {exported} 个待专攻单词至 Anki 牌组")