"""P6 · 首次启动向导（`PRODUCT_SPEC` 4.7）。

原则 P2 要求"零配置启动"，但产品里确实有两个**无法自动确定**的项：考试日期（有默认值
但仍需用户确认）与资料根目录（可探测但可能探测失败）。用三步向导一次性解决，
之后永不再打扰。

关键设计要求（4.7）：**每步都可跳过，跳过即使用默认值，且之后可从 P5 修改。**
向导绝不能变成门槛——所以底部始终有「跳过此步」，且直接关掉窗口也算全部跳过。

这是全项目**允许使用模态对话框**的少数场景之一：它只在首次启动出现，
且此时主窗口已 `show()`、事件循环正在运行（模态框在无事件循环下永久阻塞，
这正是本项目曾经踩过的坑，见 `ui/components.py`）。
"""

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QDateEdit,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core.config import SUBJECT_FOLDERS, detect_material_root


class OnboardingDialog(QDialog):
    STEPS = 3

    def __init__(self, database, parent=None):
        super().__init__(parent)
        self.database = database
        self.step = 0
        self.setObjectName("onboardingDialog")
        self.setWindowTitle("欢迎使用 TOPIK Study Hub")
        self.setModal(True)
        self.setMinimumWidth(560)
        self._build()

    # ---------------------------------------------------------------- 布局

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 20)
        layout.setSpacing(16)

        self.dots = QLabel()
        self.dots.setObjectName("faint")
        layout.addWidget(self.dots)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._page_welcome())
        self.stack.addWidget(self._page_exam())
        self.stack.addWidget(self._page_paths())
        layout.addWidget(self.stack, 1)

        self.banner = QLabel()
        self.banner.setObjectName("muted")
        self.banner.setWordWrap(True)
        self.banner.hide()
        layout.addWidget(self.banner)

        controls = QHBoxLayout()
        self.btn_skip = QPushButton("跳过此步")
        self.btn_skip.setObjectName("iconButton")
        self.btn_skip.clicked.connect(self.skip_step)
        controls.addWidget(self.btn_skip)
        controls.addStretch()
        self.btn_back = QPushButton("上一步")
        self.btn_back.setObjectName("iconButton")
        self.btn_back.clicked.connect(self.go_back)
        controls.addWidget(self.btn_back)
        self.btn_next = QPushButton("下一步")
        self.btn_next.setObjectName("primaryButton")
        self.btn_next.clicked.connect(self.go_next)
        controls.addWidget(self.btn_next)
        layout.addLayout(controls)

        self._sync()

    def _page_welcome(self):
        page = QWidget()
        box = QVBoxLayout(page)
        box.setSpacing(12)
        title = QLabel("欢迎使用 TOPIK Study Hub")
        title.setObjectName("pageTitle")
        box.addWidget(title)
        intro = QLabel(
            "一个本地优先的备考工具箱：单词仓、影子跟读、资料库与知识碎片，"
            "学习记录会自动沉淀在**这台设备**上，不需要联网。"
        )
        intro.setObjectName("pageSubtitle")
        intro.setWordWrap(True)
        box.addWidget(intro)
        note = QLabel(
            "接下来用两步确认两件无法自动确定的事：考试日期与资料目录。"
            "每一步都可以跳过，之后随时能在「设置与数据」里修改。"
        )
        note.setObjectName("muted")
        note.setWordWrap(True)
        box.addWidget(note)
        box.addStretch()
        return page

    def _page_exam(self):
        page = QWidget()
        box = QVBoxLayout(page)
        box.setSpacing(12)
        title = QLabel("确认考试信息")
        title.setObjectName("sectionTitle")
        box.addWidget(title)
        hint = QLabel("首页的倒计时用它计算。跳过则使用默认值。")
        hint.setObjectName("muted")
        box.addWidget(hint)

        row_label = QHBoxLayout()
        cap = QLabel("届次名称")
        cap.setObjectName("muted")
        cap.setFixedWidth(88)
        label_text, exam_date = self.database.get_exam()
        self.input_label = QLineEdit(label_text)
        row_label.addWidget(cap)
        row_label.addWidget(self.input_label, 1)
        box.addLayout(row_label)

        row_date = QHBoxLayout()
        cap2 = QLabel("考试日期")
        cap2.setObjectName("muted")
        cap2.setFixedWidth(88)
        self.input_date = QDateEdit()
        self.input_date.setCalendarPopup(True)
        self.input_date.setDisplayFormat("yyyy-MM-dd")
        parsed = QDate.fromString(exam_date, "yyyy-MM-dd")
        self.input_date.setDate(parsed if parsed.isValid() else QDate.currentDate())
        row_date.addWidget(cap2)
        row_date.addWidget(self.input_date, 1)
        box.addLayout(row_date)
        box.addStretch()
        return page

    def _page_paths(self):
        page = QWidget()
        box = QVBoxLayout(page)
        box.setSpacing(12)
        title = QLabel("确认资料根目录")
        title.setObjectName("sectionTitle")
        box.addWidget(title)
        hint = QLabel(
            "工具会自动递归扫描该目录下的课件与图片，并按目录/文件名智能归类到 "
            + " / ".join(SUBJECT_FOLDERS)
            + " / 其他。"
        )
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        box.addWidget(hint)

        row = QHBoxLayout()
        cap = QLabel("资料根目录")
        cap.setObjectName("muted")
        cap.setFixedWidth(88)
        self.input_root = QLineEdit(self.database.get_material_root())
        self.input_root.setReadOnly(True)
        btn_choose = QPushButton("选择…")
        btn_choose.setObjectName("iconButton")
        btn_choose.clicked.connect(self.choose_root)
        row.addWidget(cap)
        row.addWidget(self.input_root, 1)
        row.addWidget(btn_choose)
        box.addLayout(row)

        detected = detect_material_root()
        self.detected_hint = QLabel()
        self.detected_hint.setObjectName("faint")
        self.detected_hint.setWordWrap(True)
        if detected and detected == self.input_root.text():
            self.detected_hint.setText(f"已自动探测到：{detected}")
        elif detected:
            self.detected_hint.setText(f"探测到可能的目录：{detected}（可点「选择…」改成别的）")
        else:
            self.detected_hint.setText("没有自动探测到目录，请手动选择，或跳过稍后在设置里指定。")
        box.addWidget(self.detected_hint)
        box.addStretch()
        return page

    # ---------------------------------------------------------------- 交互

    def choose_root(self):
        start = self.input_root.text() or ""
        chosen = QFileDialog.getExistingDirectory(self, "选择资料根目录", start)
        if chosen:
            self.input_root.setText(chosen)

    def _sync(self):
        self.stack.setCurrentIndex(self.step)
        self.dots.setText("　".join(
            ("●" if i == self.step else "○") for i in range(self.STEPS)
        ))
        last = self.step == self.STEPS - 1
        self.btn_skip.setVisible(self.step > 0)
        self.btn_back.setVisible(self.step > 0)
        self.btn_next.setText("完成" if last else "下一步")
        self.banner.hide()

    def go_next(self):
        if self.step == self.STEPS - 1:
            self._finish()
            return
        self.step += 1
        self._sync()

    def go_back(self):
        if self.step > 0:
            self.step -= 1
            self._sync()

    def skip_step(self):
        """跳过此步 = 使用默认值（已由 database 播种），直接前进。"""
        if self.step == self.STEPS - 1:
            self._finish(apply_paths=False)
        else:
            self.step += 1
            self._sync()

    def _finish(self, apply_paths=True):
        self._apply_exam()
        if apply_paths:
            self._apply_paths()
        self.accept()

    def _apply_exam(self):
        label = self.input_label.text().strip()
        chosen = self.input_date.date()
        if label and chosen.isValid():
            self.database.set_exam(label, chosen.toString("yyyy-MM-dd"))

    def _apply_paths(self):
        path = self.input_root.text().strip()
        if path:
            self.database.set_material_root(path)