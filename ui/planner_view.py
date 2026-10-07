"""P0 今日学习（`PRODUCT_SPEC` 4.1）。

四个指标、两张补充卡、一块本周概览，读数全部来自现成的表——**这一页不产生任何记录**，
它是活动日志与几张进度表的读数面（5.1 的闭环里，它是"③ 回顾"的落点）。

因此有一件事必须做对：**每次进入这一页都要重读**。别的页面刚写进去的记录，要让回到首页
这一下就看见（5.3：「数值实时更新（或每次进入 P0 时刷新）」），所以刷新挂在 `showEvent`
上，而不是只在构造时读一次。
"""

from datetime import date, datetime

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ui.style import restyle

# 活动日志的类型 → 中文名（5.2）。与 `core/database.py` 的 `HIGHLIGHT_RANK` 是一对：
# 那张表定"谁排前面"（口径，5.4），这张表定"叫什么"（措辞）。改一处时看一眼另一处。
ACTIVITY_LABELS = {
    "shadowing_minutes": "跟读",
    "vocab_triaged": "过词",
    "vocab_drilled": "专攻通过",
    "material_opened": "打开资料",
    "task_completed": "完成任务",
}

# 「今日已自动记录」条上出现哪些事件。**这不是"所有会产生记录的类型"，而是 5.2 的事件表
# 里展示位置写了"P0「今日已自动记录」"的那三条**：
#   * `material_opened` 明写"不展示（仅用于高频排序）"
#   * `task_completed` 明写展示在"连续学习天数、累计完成"
# 所以两者都不上这条。而任务就在正上方的任务卡里，再列一遍是重复。
RECORD_BAR_TYPES = ("shadowing_minutes", "vocab_triaged", "vocab_drilled")

# 条上每一段点下去落到哪个模块
RECORD_BAR_TARGETS = {
    "shadowing_minutes": "shadowing",
    "vocab_triaged": "vocab",
    "vocab_drilled": "vocab",
}

# H4 侧栏的宽度。三条内容（《列表名》第 N 段 / 上次听到 12:34 / 已过 320 / 1400）
# 都要能折行放下，比 240px 的全局侧边栏宽一档。
SIDE_COLUMN_WIDTH = 300


def _mmss(milliseconds):
    """毫秒 → `12:34`。超过一小时的才补上小时段。"""
    total = max(0, int(milliseconds)) // 1000
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


class DayCell(QFrame):
    """本周回顾单日方块控件（周一至周日）。

    支持属性（.weekday_label / .date_label / .stat_label）与字典式两种访问方式，
    继承自 QFrame，支持所有 QSS 状态选择器（#dayCell, #dayCellActive, #dayCellToday, #dayCellFuture）。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("dayCell")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 8, 6, 8)
        layout.setSpacing(3)
        layout.setAlignment(Qt.AlignCenter)

        self.weekday_label = QLabel()
        self.weekday_label.setObjectName("dayCellWeekday")
        self.weekday_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.weekday_label)

        self.date_label = QLabel()
        self.date_label.setObjectName("dayCellDate")
        self.date_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.date_label)

        self.stat_label = QLabel("—")
        self.stat_label.setObjectName("dayCellEmpty")
        self.stat_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.stat_label)

    def __getitem__(self, key):
        if isinstance(key, int):
            raise IndexError(key)
        if key == "frame":
            return self
        if key == "weekday":
            return self.weekday_label
        if key == "date":
            return self.date_label
        if key == "stat":
            return self.stat_label
        raise KeyError(key)


class PlannerView(QWidget):
    # 跳去别的模块。**这一页不引用任何别的页面**——跳页由组合根决定（与 P3 ↔ P4 的
    # 双向联动同一条纪律）。这两个信号同时服务「继续上次」卡与「今日已自动记录」条：
    # 前者是"回到刚才的地方"，后者是"去看看那条记录"，落到的是同一个动作。
    open_shadowing = Signal()
    open_vocab = Signal(int)   # 参数是词表 id；-1 表示没有具体词表

    def __init__(self, database):
        super().__init__()
        self.database = database
        self.init_ui()
        self.refresh()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(16)

        eyebrow = QLabel(datetime.now().strftime("%Y 年 %m 月 %d 日 · 今日学习"))
        eyebrow.setObjectName("pageEyebrow")
        layout.addWidget(eyebrow)
        heading = QHBoxLayout()
        heading.setSpacing(12)
        title_block = QVBoxLayout()
        self.page_title = QLabel("让今天的学习，有一个好开始")
        self.page_title.setObjectName("pageTitle")
        title_block.addWidget(self.page_title)
        subtitle = QLabel("从一个小任务开始，学习记录会自动沉淀在这里。")
        subtitle.setObjectName("pageSubtitle")
        title_block.addWidget(subtitle)
        heading.addLayout(title_block)
        heading.addStretch()

        self.countdown_card = QFrame()
        self.countdown_card.setObjectName("countdownCard")
        cd_layout = QHBoxLayout(self.countdown_card)
        cd_layout.setContentsMargins(16, 8, 16, 8)
        cd_layout.setSpacing(14)

        cd_left = QVBoxLayout()
        cd_left.setSpacing(2)
        cd_left.setAlignment(Qt.AlignVCenter)
        self.countdown_target = QLabel("TOPIK 考试")
        self.countdown_target.setObjectName("countdownTarget")
        cd_left.addWidget(self.countdown_target)
        self.countdown_sub = QLabel("备考倒计时还有")
        self.countdown_sub.setObjectName("countdownSub")
        cd_left.addWidget(self.countdown_sub)
        cd_layout.addLayout(cd_left)

        cd_right = QHBoxLayout()
        cd_right.setSpacing(3)
        cd_right.setAlignment(Qt.AlignVCenter | Qt.AlignRight)
        self.countdown_number = QLabel("—")
        self.countdown_number.setObjectName("countdownNumber")
        cd_right.addWidget(self.countdown_number)
        self.countdown_unit = QLabel("天")
        self.countdown_unit.setObjectName("countdownUnit")
        cd_right.addWidget(self.countdown_unit)
        cd_layout.addLayout(cd_right)

        self.countdown = QLabel()
        self.countdown.setVisible(False)

        heading.addWidget(self.countdown_card)
        layout.addLayout(heading)

        # H2 指标行（D1 的四张卡）：今日完成 / 连续学习 / 本周进度 / 今日亮点。
        # 「累计完成」不在这里——它只增不减、不指向任何行动，降到了 H5（见 D1）。
        metrics = QGridLayout()
        metrics.setHorizontalSpacing(12)
        _, self.done_value = self._add_metric(metrics, 0, "今日完成", "0 / 0")
        _, self.streak_value = self._add_metric(metrics, 1, "连续学习", "0 天")
        # 连续学习用强调色，给指标行一点色彩层次（DESIGN.md §3.6 B）。
        # **整行只有这一处强调色**——四张卡都上色就等于都没上色。
        self.streak_value.setObjectName("metricValueAccent")
        _, self.week_value = self._add_metric(metrics, 2, "本周进度", "0 / 7 天")
        self.highlight_caption, self.highlight_value = self._add_metric(
            metrics, 3, "今日亮点", "—"
        )
        layout.addLayout(metrics)

        body = QHBoxLayout()
        body.setSpacing(16)
        main_column = QVBoxLayout()
        main_column.setSpacing(16)
        main_column.addWidget(self._build_task_card(), 1)
        # 5.3：位置是"P0 任务卡下方，一行，只读"。放在主列里而不是 H4 的侧栏，
        # 是因为它按 5.3 的格式必须排成**一行**（跟读 24 分钟 · 过词 320 个 ·
        # 专攻通过 12 个），300px 的侧栏放不下，硬塞就会折成三行、不再是一条。
        self.record_card = self._build_record_bar()
        main_column.addWidget(self.record_card)
        body.addLayout(main_column, 1)

        # H4 侧栏：只有「继续上次」一张卡。没有可继续的上下文时**整列隐藏**，
        # 让任务卡占满整宽——而不是在右边留一条空白带。
        self.resume_card = self._build_resume_card()
        side_column = QVBoxLayout()
        side_column.setSpacing(16)
        side_column.addWidget(self.resume_card)
        side_column.addStretch()
        body.addLayout(side_column)
        layout.addLayout(body, 1)

        # H5 底部：本周概览。按"折叠或次屏"里的**次屏**落点——不进首屏抢位置，
        # 但也不需要折叠控件的机械感。
        layout.addWidget(self._build_week_card())

    # ---- 构建 ----

    def _add_metric(self, layout, column, caption, value):
        """一张指标卡。返回 `(字幕 label, 数值 label)`——数值右侧那两张卡的字幕会被改写
        （`今日亮点 · 跟读`），所以字幕也得拿得到。"""
        card = QFrame()
        card.setObjectName("metricCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 12, 16, 12)
        card_layout.setSpacing(4)
        caption_label = QLabel(caption)
        caption_label.setObjectName("metricCaption")
        caption_label.setMinimumHeight(18)
        value_label = QLabel(value)
        value_label.setObjectName("metricValue")
        value_label.setMinimumHeight(30)
        card_layout.addWidget(caption_label)
        card_layout.addWidget(value_label)
        layout.addWidget(card, 0, column)
        return caption_label, value_label

    def _build_task_card(self):
        task_card = QFrame()
        task_card.setObjectName("surface")
        task_layout = QVBoxLayout(task_card)
        task_layout.setContentsMargins(16, 16, 16, 16)
        task_layout.setSpacing(12)
        header = QHBoxLayout()
        section = QLabel("今日任务")
        section.setObjectName("sectionTitle")
        header.addWidget(section)
        header.addStretch()
        self.task_count = QLabel()
        self.task_count.setObjectName("muted")
        header.addWidget(self.task_count)
        task_layout.addLayout(header)

        add_row = QHBoxLayout()
        self.task_input = QLineEdit()
        self.task_input.setPlaceholderText("添加一个具体的小目标，例如：精听第 91 届第 12 题")
        self.task_input.returnPressed.connect(self.add_task)
        add_button = QPushButton("添加任务")
        add_button.setObjectName("primaryButton")
        add_button.clicked.connect(self.add_task)
        add_row.addWidget(self.task_input, 1)
        add_row.addWidget(add_button)
        task_layout.addLayout(add_row)

        self.empty_label = QLabel("今天还没有任务。先写下最想完成的一件事吧。")
        self.empty_label.setObjectName("muted")
        self.empty_label.setAlignment(Qt.AlignCenter)
        task_layout.addWidget(self.empty_label)
        self.task_list = QListWidget()
        self.task_list.setObjectName("taskList")
        self.task_list.setMinimumHeight(80)
        task_layout.addWidget(self.task_list, 1)
        return task_card

    def _build_resume_card(self):
        card = QFrame()
        card.setObjectName("surface")
        card.setFixedWidth(SIDE_COLUMN_WIDTH)
        outer = QVBoxLayout(card)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(4)
        title = QLabel("继续上次")
        title.setObjectName("sectionTitle")
        outer.addWidget(title)
        self.resume_rows = QVBoxLayout()
        self.resume_rows.setSpacing(2)
        outer.addLayout(self.resume_rows)
        return card

    def _build_record_bar(self):
        card = QFrame()
        card.setObjectName("surface")
        outer = QVBoxLayout(card)
        outer.setContentsMargins(16, 10, 16, 10)
        outer.setSpacing(0)
        self.record_row = QHBoxLayout()
        self.record_row.setSpacing(0)
        outer.addLayout(self.record_row)
        return card

    def _build_week_card(self):
        card = QFrame()
        card.setObjectName("surface")
        inner = QVBoxLayout(card)
        inner.setContentsMargins(16, 12, 16, 12)
        inner.setSpacing(8)

        # 顶部标题与累计完成（保留 D1 决策下右侧的累计完成）
        header_row = QHBoxLayout()
        header_row.setSpacing(12)
        title = QLabel("本周回顾")
        title.setObjectName("sectionTitle")
        header_row.addWidget(title)
        header_row.addStretch()

        total_caption = QLabel("累计完成")
        total_caption.setObjectName("muted")
        header_row.addWidget(total_caption)
        self.total_completed_value = QLabel("0 项")
        self.total_completed_value.setObjectName("metricValue")
        header_row.addWidget(self.total_completed_value)
        inner.addLayout(header_row)

        # 7 天水平活动条（周一至周日）
        self.day_strip = QHBoxLayout()
        self.day_strip.setSpacing(8)
        self.day_cells = []
        for _ in range(7):
            cell = DayCell()
            self.day_strip.addWidget(cell, 1)
            self.day_cells.append(cell)
        inner.addLayout(self.day_strip)

        # 底部周概览提示与分类汇总
        self.week_note = QLabel()
        self.week_note.setObjectName("muted")
        self.week_note.setWordWrap(True)
        inner.addWidget(self.week_note)

        self.week_summary_label = QLabel()
        self.week_summary_label.setObjectName("muted")
        self.week_summary_label.setWordWrap(True)
        inner.addWidget(self.week_summary_label)

        return card

    # ---- 刷新 ----

    def refresh(self):
        self._refresh_header()
        # 一次查询喂三处：指标卡「今日亮点」、下面的记录条、以及（借 stats）本周概览。
        # 卡取第一条、条排全部——共用同一份结果，两处的数就不可能对不上。
        stats = self.database.dashboard_stats()
        records = self.database.today_activity()
        self._refresh_metrics(stats, records)
        self._refresh_record_bar(records)
        self._refresh_resume(self.database.resume_context())
        self._refresh_week(stats)
        self._refresh_tasks()

    def showEvent(self, event):
        """每次进入这一页都重读（5.3：「数值实时更新（或每次进入 P0 时刷新）」）。

        别的页面刚写进去的记录，必须回到首页这一下就看得见，所以不能只在构造时读一次。
        `QStackedWidget` 切页时会 hide 旧页、show 新页，因此每次进入都会到；而且这个钩子
        **晚于**旧页面的 `hideEvent`——P2 的跟读时长正是在那里冲刷落库的，
        于是"离开 P2 之前那一段"也算进了今天。
        """
        super().showEvent(event)
        self.refresh()

    def _refresh_header(self):
        label, exam_date_text = self.database.get_exam()
        try:
            days = (date.fromisoformat(exam_date_text) - date.today()).days
            days_num = max(days, 0)
            self.countdown_target.setText(f"距 {label}" if label else "TOPIK 考试")
            self.countdown_sub.setText("冲刺倒计时还有")
            self.countdown_number.setText(str(days_num))
            self.countdown_unit.setText("天")
            self.countdown.setText(f"距 {label} 还有 {days_num} 天")
        except ValueError:
            self.countdown_target.setText(label or "TOPIK 考试")
            self.countdown_sub.setText("未设置考试日期")
            self.countdown_number.setText("—")
            self.countdown_unit.setText("")
            self.countdown.setText(label or "")

    def _refresh_metrics(self, stats, records):
        self.done_value.setText(f"{stats['completed_today']} / {stats['total_today']}")
        self.streak_value.setText(f"{stats['streak']} 天")
        self.week_value.setText(f"{stats['week_days']} / {stats['week_total']} 天")

        top = records[0] if records else None
        if top is None:
            # 4.1：无活动时显示 `—`，**不显示 0**——0 是"今天还有机会"，
            # 破折号是"今天还没有数据"，两者不该长一样。
            self.highlight_caption.setText("今日亮点")
            self.highlight_value.setText("—")
            return
        label = ACTIVITY_LABELS.get(top["activity_type"], top["activity_type"])
        self.highlight_caption.setText(f"今日亮点 · {label}")
        self.highlight_value.setText(_amount_text(top))

    def _refresh_record_bar(self, records):
        _clear_layout(self.record_row)
        shown = [row for row in records if row["activity_type"] in RECORD_BAR_TYPES]
        # 5.3：无数据时**整条隐藏**，不写"今天还没有记录"这类空话。
        self.record_card.setVisible(bool(shown))
        if not shown:
            return
        prefix = QLabel("今日已记录：")
        prefix.setObjectName("muted")
        self.record_row.addWidget(prefix)
        for index, record in enumerate(shown):
            if index:
                separator = QLabel("·")
                separator.setObjectName("muted")
                self.record_row.addWidget(separator)
            self.record_row.addWidget(self._segment_button(record))
        self.record_row.addStretch()

    def _segment_button(self, record):
        """条上的一个可点片段。点它就是跳到产生这条记录的模块（5.3）。"""
        label = ACTIVITY_LABELS.get(record["activity_type"], record["activity_type"])
        button = QPushButton(f"{label} {_amount_text(record)}")
        button.setObjectName("recordSegment")
        button.setCursor(Qt.PointingHandCursor)
        # `clicked` 会带一个 bool，而槽不吃参数——用默认参数把它吃掉，
        # 同时把记录**按值**绑进闭包，别让它跟着循环变量走。
        button.clicked.connect(lambda _=False, row=dict(record): self._open_record(row))
        return button

    def _open_record(self, record):
        target = RECORD_BAR_TARGETS.get(record["activity_type"])
        if target == "shadowing":
            self.open_shadowing.emit()
        elif target == "vocab":
            # `ref_id` 是记账时顺手留下的词表 id（见 `note_vocab_progress`）。
            # 没有就用 -1 表示"回到单词仓，但不指定哪一份词表"。
            self.open_vocab.emit(int(record["ref_id"] or -1))

    def _refresh_resume(self, contexts):
        _clear_layout(self.resume_rows)
        for context in contexts:
            self.resume_rows.addWidget(self._resume_row(context))
        # 两者都没有时整张卡隐藏（2.5 / 5.3 同一条纪律：没有就说没有）
        self.resume_card.setVisible(bool(contexts))

    def _resume_row(self, context):
        """「继续上次」的一行（2.5）。整行可点，所以用 `QPushButton`；文字放在它内部的
        子控件里，因为 `QPushButton` 的 text 不换行，而这些标题在 300px 里必然折行。"""
        if context["kind"] == "audio":
            if context["playlist"] and context["segment"]:
                headline = f"继续跟读《{context['playlist']}》第 {context['segment']} 段"
                detail = f"{context['title']} · 上次听到 {_mmss(context['position_ms'])}"
            else:
                headline = f"继续跟读《{context['title']}》"
                detail = f"上次听到 {_mmss(context['position_ms'])}"
            handler = lambda _=False: self.open_shadowing.emit()   # noqa: E731
        else:
            headline = f"继续过词《{context['name']}》"
            detail = (
                f"已过 {context['passed']} / {context['total']}，"
                f"{context['to_drill']} 个待专攻"
            )
            list_id = context["list_id"]
            handler = lambda _=False, value=list_id: self.open_vocab.emit(value)   # noqa: E731

        button = QPushButton()
        button.setObjectName("resumeRow")
        button.setCursor(Qt.PointingHandCursor)
        inside = QVBoxLayout(button)
        inside.setContentsMargins(0, 0, 0, 0)
        inside.setSpacing(2)
        head = QLabel(headline)
        head.setWordWrap(True)
        inside.addWidget(head)
        under = QLabel(detail)
        under.setObjectName("muted")
        under.setWordWrap(True)
        inside.addWidget(under)
        button.clicked.connect(handler)
        return button

    def _refresh_week(self, stats=None):
        review = self.database.weekly_review_data()
        self.total_completed_value.setText(f"{review['total_completed']} 项")
        # 「连续学习」与「本周进度」在"这周每天都在学"时会显示相近的值（D1 自己列出的
        # 缺点），所以这里把两者的差别写明：一个跨周累计、一个每周一重置。
        self.week_note.setText(
            f"本周 {review['week_days_studied']} / {review['week_total_days']} 天有学习 · "
            f"连续 {review['streak']} 天（跨周累计，不随周一重置）"
        )

        # 刷新 7 天方块（周一至周日）
        days = review["days"]
        for idx, day_info in enumerate(days):
            if idx >= len(self.day_cells):
                break
            cell = self.day_cells[idx]
            weekday_lbl = cell.weekday_label
            date_lbl = cell.date_label
            stat_lbl = cell.stat_label

            weekday_lbl.setText(day_info["weekday"])
            date_lbl.setText(day_info["short_date"])

            is_today = day_info["is_today"]
            is_future = day_info["is_future"]
            has_study = day_info["has_study"]
            acts = day_info["activities"]
            task_count = day_info["tasks_completed"]

            if is_future:
                restyle(cell, "dayCellFuture")
                restyle(stat_lbl, "dayCellEmpty")
                stat_lbl.setText("—")
                cell.setToolTip(f"{day_info['date']} {day_info['weekday']}\n（尚未到来）")
            elif has_study:
                if is_today:
                    restyle(cell, "dayCellToday")
                    cell.setProperty("active", "true")
                else:
                    restyle(cell, "dayCellActive")
                    cell.setProperty("active", "true")

                restyle(stat_lbl, "dayCellStat")

                # 选择优先展示的活动
                if acts.get("shadowing_minutes", {}).get("amount", 0) > 0:
                    amt = acts["shadowing_minutes"]["amount"]
                    stat_lbl.setText(f"跟读 {amt}分")
                elif acts.get("vocab_triaged", {}).get("amount", 0) > 0:
                    amt = acts["vocab_triaged"]["amount"]
                    stat_lbl.setText(f"过词 {amt}个")
                elif acts.get("vocab_drilled", {}).get("amount", 0) > 0:
                    amt = acts["vocab_drilled"]["amount"]
                    stat_lbl.setText(f"专攻 {amt}个")
                elif task_count > 0:
                    stat_lbl.setText(f"任务 {task_count}项")
                elif acts.get("material_opened", {}).get("amount", 0) > 0:
                    amt = acts["material_opened"]["amount"]
                    stat_lbl.setText(f"资料 {amt}次")
                else:
                    stat_lbl.setText("✓ 已学")

                # 详细提示
                lines = [f"{day_info['date']} {day_info['weekday']}" + (" (今天)" if is_today else "")]
                if "shadowing_minutes" in acts:
                    lines.append(f"• 跟读：{acts['shadowing_minutes']['amount']} {acts['shadowing_minutes']['unit']}")
                if "vocab_triaged" in acts:
                    lines.append(f"• 过词：{acts['vocab_triaged']['amount']} {acts['vocab_triaged']['unit']}")
                if "vocab_drilled" in acts:
                    lines.append(f"• 专攻通过：{acts['vocab_drilled']['amount']} {acts['vocab_drilled']['unit']}")
                if "material_opened" in acts:
                    lines.append(f"• 打开资料：{acts['material_opened']['amount']} {acts['material_opened']['unit']}")
                if task_count > 0:
                    lines.append(f"• 完成任务：{task_count} 项")
                cell.setToolTip("\n".join(lines))
            else:
                # 过去未学习日
                if is_today:
                    restyle(cell, "dayCellToday")
                    cell.setProperty("active", "false")
                else:
                    restyle(cell, "dayCell")
                    cell.setProperty("active", "false")

                restyle(stat_lbl, "dayCellEmpty")
                stat_lbl.setText("—")
                title_line = f"{day_info['date']} {day_info['weekday']}" + (" (今天)" if is_today else "")
                cell.setToolTip(f"{title_line}\n无学习记录（休息日）")

        # 本周累计各项汇总文案
        totals = review["totals"]
        summary_items = []
        if totals.get("shadowing_minutes", 0) > 0:
            summary_items.append(f"跟读 {totals['shadowing_minutes']} 分钟")
        if totals.get("vocab_triaged", 0) > 0:
            summary_items.append(f"过词 {totals['vocab_triaged']} 个")
        if totals.get("vocab_drilled", 0) > 0:
            summary_items.append(f"专攻通过 {totals['vocab_drilled']} 个")
        if totals.get("tasks", 0) > 0:
            summary_items.append(f"完成任务 {totals['tasks']} 项")

        if summary_items:
            self.week_summary_label.setText(f"本周累计：{' · '.join(summary_items)}")
        else:
            self.week_summary_label.setText("本周暂无累计活动记录")

    def _refresh_tasks(self):
        self.task_list.clear()
        tasks = self.database.today_tasks()
        self.task_count.setText(f"{len(tasks)} 项待办")
        self.empty_label.setVisible(not tasks)
        self.task_list.setVisible(bool(tasks))
        for task in tasks:
            item = QListWidgetItem()
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(6, 2, 6, 2)
            checkbox = QCheckBox(task["title"])
            checkbox.setChecked(bool(task["completed"]))
            checkbox.toggled.connect(
                lambda checked, task_id=task["id"]: self.toggle_task(task_id, checked)
            )
            delete_button = QPushButton("删除")
            delete_button.setObjectName("taskDeleteButton")
            delete_button.clicked.connect(lambda _, task_id=task["id"]: self.delete_task(task_id))
            row_layout.addWidget(checkbox, 1)
            row_layout.addWidget(delete_button)
            item.setSizeHint(QSize(0, 36))
            self.task_list.addItem(item)
            self.task_list.setItemWidget(item, row)

    # ---- 任务 ----

    def add_task(self):
        title = self.task_input.text().strip()
        if not title:
            self.task_input.setFocus()
            return
        self.database.add_task(title)
        self.task_input.clear()
        self.refresh()

    def toggle_task(self, task_id, checked):
        self.database.set_task_completed(task_id, checked)
        self.refresh()

    def delete_task(self, task_id):
        self.database.delete_task(task_id)
        self.refresh()


def _amount_text(record):
    """`24 分钟` / `15 个`。单位存在 `unit` 里（5.2 的"记录内容"）。"""
    return f"{record['amount']} {record['unit'] or ''}".strip()


def _clear_layout(layout):
    """清空一个布局里的全部控件。

    必须连控件一起 `deleteLater()`：`takeAt` 只把布局项摘下来，控件本身仍是这一页的
    子控件，会继续画在原地——刷新几次之后侧栏里就叠着一摞旧行。
    """
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()