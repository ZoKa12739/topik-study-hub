"""全局视觉主题。

参照 Linear Desktop App 的视觉语言，规范见 `design/DESIGN.md`。
所有取值都来自该文档第 3 章（设计 token），此处不新增任何本文档未定义的色值。

三条纪律（DESIGN.md §3.1）：
  1. 每屏最多一个实心强调色元素，单个面积 < 2% 屏幕
  2. 层级由 1px 边框与底色的极小阶差承担，不用阴影、不用大字号差
  3. 状态色永远伴随图标或文字，不单独承载语义

色调演进（DESIGN.md §3.6）：
  v1.1 中性冷黑 → v1.3 暖绿炭 → v1.4 浅色。
  每次调整都不是"更接近 Linear"，而是让取值适配本产品的使用前提：
  长时间注视、需要一点色彩识别度、白天为主。

字体（见 ui/fonts.py）：
  Pretendard 管韩文/拉丁，中文回退微软雅黑——Pretendard 不含简体中文字形。

注意：Qt QSS 不支持 CSS 变量，色值必须内联。改动色值时请同步 `DESIGN.md`。
"""

from pathlib import Path

from ui.fonts import FONT_STACK

# QSS 的 image: url() 不支持 data URI，复选框对勾只能走真实资源文件。
# 路径从模块位置推导（不依赖当前工作目录——见 PRODUCT_SPEC D10 的教训）。
_CHECK_SVG = (Path(__file__).resolve().parent / "assets" / "check.svg").as_posix()

APP_STYLESHEET = f"""
/* ============================================================
   Token 速查（改色时对照 design/DESIGN.md §3.1）
   bg-app       #F6F8F5    bg-sidebar   #EFF3EF    bg-surface  #FFFFFF
   bg-elevated  #F2F5F1    bg-hover     #EDF1EC    bg-selected #E3EBE5
   border       #DDE5DE    border-strong #C6D2C9
   text-primary #2A322D    text-secondary #4C5650  text-muted  #646E68
   text-faint   #98A29C
   accent       #1F6B57    （用于文字/图标/边框/填充）
   accent-warm  #8A5F12    （仅用于倒计时——DESIGN.md §6.1 的"那一处温度"）
   success #1F7A4D  warning #8A6410  danger #B4342B  info #1F5FA8
   对比度（相对 bg-app）：primary 12.2 / secondary 7.1 / muted 5.0
                          accent 6.0 / accent-warm 5.3 / faint 2.4（仅占位符）
   ============================================================ */

/* ---------- 基础 ---------- */

* {{
    font-family: {FONT_STACK};
}}

QMainWindow, QWidget#pageRoot, QStackedWidget {{
    background: #F6F8F5;
    color: #2A322D;
}}

QWidget {{ color: #2A322D; }}

QToolTip {{
    background: #2A322D;
    color: #F6F8F5;
    border: none;
    border-radius: 6px;
    padding: 6px 8px;
}}

/* ---------- 侧边栏 ---------- */
/* 视觉收敛：与主背景极小色差、极淡分割线、紧凑导航、低存在感浅色高亮 */

QFrame#sidebar {{
    background: #F4F7F4;
    border-right: 1px solid #E6ECE6;
}}

QFrame#sidebarDivider {{
    background: #E6ECE6;
    border: none;
    max-height: 1px;
}}

QLabel#brandTitle {{
    color: #2A322D;
    font-size: 16px;
    font-weight: 600;
    letter-spacing: -0.2px;
}}

QLabel#brandSubtitle {{
    color: #98A29C;
    font-size: 12px;
}}

/* 侧边栏分组标题（学习、资料） */
QLabel#navSectionTitleFirst {{
    color: #98A29C;      /* text-faint */
    font-size: 13px;
    font-weight: 400;
    padding-left: 10px;  /* 需与导航项内容对齐 */
    margin-bottom: 8px;
}}

QLabel#navSectionTitle {{
    color: #98A29C;      /* text-faint */
    font-size: 13px;
    font-weight: 400;
    padding-left: 10px;  /* 需与导航项内容对齐 */
    margin-top: 20px;
    margin-bottom: 8px;
}}

/* 导航项按钮样式（对齐 Secondary Navigation 的交互与视觉） */
QPushButton#navItem {{
    background: transparent;
    border: none;
    border-radius: 6px;
    padding: 6px 10px;
    margin: 1px 0;
    color: #4C5650;
    font-size: 14px;
    text-align: left;
    outline: 0;
}}

QPushButton#navItem:hover {{
    background: #EDF2ED;
    color: #2A322D;
}}

QPushButton#navItem:checked {{
    background: #EBF1EC;
    color: #1F6B57;
    font-weight: 500;
}}

QLabel#sidebarFooter {{
    color: #98A29C;
    font-size: 11px;
    padding: 2px 4px;
}}

QListWidget#navigation {{
    background: transparent;
    border: none;
    color: #4C5650;
    padding: 2px 0px;
    outline: 0;
    font-size: 14px;
}}

QListWidget#navigation::item {{
    border-radius: 6px;
    padding: 6px 10px;
    margin: 1px 0;
    color: #4C5650;
}}

QListWidget#navigation::item:hover {{
    background: #EDF2ED;
    color: #2A322D;
}}

/* 选中态：保留浅色高亮，降低存在感，避免过重 */
QListWidget#navigation::item:selected {{
    background: #EBF1EC;
    color: #1F6B57;
    font-weight: 500;
}}

/* 底部设置入口（Secondary Navigation）：与主导航项对齐 */
QPushButton#navSettings {{
    background: transparent;
    border: none;
    border-radius: 6px;
    padding: 6px 10px;
    margin: 1px 0;
    color: #4C5650;
    font-size: 14px;
    text-align: left;
}}

QPushButton#navSettings:hover {{
    background: #EDF2ED;
    color: #2A322D;
}}

QPushButton#navSettings:checked {{
    background: #EBF1EC;
    color: #1F6B57;
    font-weight: 500;
}}

/* ---------- 窗口右上角控制按钮（无边框 CSD） ---------- */

QPushButton#winCtrlBtn, QPushButton#winCloseBtn {{
    background: transparent;
    border: none;
    border-radius: 6px;
    padding: 0px;
    min-width: 38px;
    max-width: 38px;
    min-height: 30px;
    max-height: 30px;
    outline: 0;
}}

QPushButton#winCtrlBtn:hover {{
    background: #E6ECE7;
}}

QPushButton#winCtrlBtn:pressed {{
    background: #DCE5DE;
}}

QPushButton#winCloseBtn:hover {{
    background: #B4342B;
}}

QPushButton#winCloseBtn:pressed {{
    background: #962922;
}}

/* ---------- 页头 ---------- */
/* DESIGN.md §3.2：跨度压缩，层级改由明度承担 */

QLabel#pageEyebrow {{
    color: #646E68;
    font-size: 13px;
    font-weight: 500;
}}

QLabel#pageTitle {{
    color: #2A322D;
    font-size: 22px;
    font-weight: 600;
}}

QLabel#pageSubtitle {{
    color: #646E68;
    font-size: 14px;
}}

QLabel#sectionTitle {{
    color: #2A322D;
    font-size: 15px;
    font-weight: 600;
}}

QLabel#muted {{ color: #646E68; font-size: 14px; }}

QLabel#faint {{ color: #98A29C; font-size: 13px; }}

/* 快捷键"键帽"（P5）。复用 bg-elevated + border，不引入新 token */
QLabel#shortcutKey {{
    color: #2A322D;
    font-size: 13px;
    background: #F2F5F1;
    border: 1px solid #DDE5DE;
    border-radius: 4px;
    padding: 2px 8px;
}}

/* 倒计时小卡片：模块化数字展示 */
QFrame#countdownCard {{
    background: #FFFFFF;
    border: 1px solid #DDE5DE;
    border-radius: 8px;
}}

QLabel#countdownTarget {{
    color: #4C5650;
    font-size: 13px;
    font-weight: 600;
}}

QLabel#countdownSub {{
    color: #98A29C;
    font-size: 11px;
}}

QLabel#countdownNumber {{
    color: #1F6B57;
    font-size: 28px;
    font-weight: 700;
}}

QLabel#countdownUnit {{
    color: #646E68;
    font-size: 13px;
    font-weight: 500;
    margin-top: 8px;
}}

QLabel#countdown {{
    color: #8A5F12;
    font-size: 15px;
    font-weight: 600;
}}

/* 韩文正文独立一档，比中文大一档（DESIGN.md §3.2） */
QLabel#koreanText {{
    font-family: "Pretendard", "Microsoft YaHei UI", sans-serif;
    font-size: 16px;
    color: #2A322D;
}}

/* ---------- 面板与卡片 ---------- */
/* DESIGN.md §3.4：1px 边框承担层级，无阴影；圆角 8px */

QFrame#surface, QFrame#metricCard {{
    background: #FFFFFF;
    border: 1px solid #DDE5DE;
    border-radius: 8px;
}}

QFrame#metricCard {{ min-height: 76px; }}

QLabel#metricValue {{
    color: #2A322D;
    font-size: 24px;
    font-weight: 600;
}}

/* 连续学习用强调色——给指标行一点色彩层次 */
QLabel#metricValueAccent {{
    color: #1F6B57;
    font-size: 24px;
    font-weight: 600;
}}

QLabel#metricCaption {{
    color: #646E68;
    font-size: 13px;
}}

/* ---------- P0 本周回顾视图 Day Cells ---------- */

QFrame#dayCell, #dayCell {{
    background: #F2F5F1;
    border: 1px solid #DDE5DE;
    border-radius: 6px;
}}

QFrame#dayCellActive, #dayCellActive {{
    background: #E9F4EE;
    border: 1px solid #A9D2BC;
    border-radius: 6px;
}}

QFrame#dayCellToday, #dayCellToday {{
    background: #FFFFFF;
    border: 1.5px solid #1F6B57;
    border-radius: 6px;
}}

QFrame#dayCellToday[active="true"], #dayCellToday[active="true"],
QFrame#dayCellTodayActive, #dayCellTodayActive {{
    background: #E9F4EE;
    border: 1.5px solid #1F6B57;
    border-radius: 6px;
}}

QFrame#dayCellFuture, #dayCellFuture {{
    background: transparent;
    border: 1px dashed #DDE5DE;
    border-radius: 6px;
}}

QLabel#dayCellWeekday, #dayCellWeekday {{
    font-size: 13px;
    font-weight: 600;
    color: #4C5650;
}}

QLabel#dayCellDate, #dayCellDate {{
    font-size: 11px;
    color: #98A29C;
}}

QLabel#dayCellStat, #dayCellStat {{
    font-size: 12px;
    font-weight: 500;
    color: #1F6B57;
}}

QLabel#dayCellEmpty, #dayCellEmpty {{
    font-size: 12px;
    color: #98A29C;
}}

/* ---------- 输入 ---------- */

QLineEdit, QTextEdit, QPlainTextEdit {{
    background: #FFFFFF;
    border: 1px solid #DDE5DE;
    border-radius: 6px;
    padding: 8px 11px;
    color: #2A322D;
    font-size: 15px;
    selection-background-color: #1F6B57;
    selection-color: #FFFFFF;
}}

QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{
    border: 1px solid #1F6B57;
}}

QLineEdit:disabled, QTextEdit:disabled {{
    color: #98A29C;
    background: #F4F7F3;
}}

QLineEdit::placeholder {{ color: #98A29C; }}

/* P2 双语文本面板：卡片内的无边框编辑器；韩语一档单独抬高字号 */
QTextEdit#panelEditor, QTextEdit#koreanTextEditor {{
    border: none;
    background: transparent;
    padding: 4px 2px;
}}

QTextEdit#koreanTextEditor {{
    font-family: "Pretendard", "Microsoft YaHei UI", sans-serif;
    font-size: 16px;
}}

/* ---------- 下拉框（P2 语速、P3 科目） ---------- */

QComboBox {{
    background: #FFFFFF;
    border: 1px solid #DDE5DE;
    border-radius: 6px;
    padding: 7px 10px;
    color: #2A322D;
    font-size: 15px;
    min-width: 68px;
}}

QComboBox:hover {{ background: #F2F5F1; border-color: #C6D2C9; }}
QComboBox:focus {{ border-color: #1F6B57; }}

QComboBox::drop-down {{ border: none; width: 18px; }}

QComboBox QAbstractItemView {{
    background: #FFFFFF;
    border: 1px solid #C6D2C9;
    border-radius: 6px;
    color: #2A322D;
    font-size: 15px;
    selection-background-color: #E3EBE5;
    selection-color: #2A322D;
    outline: 0;
}}

/* ---------- 按钮 ---------- */
/* 次级按钮：浅底 + 1px 边框——低对比但边界清晰（DESIGN.md 差异 7） */

QPushButton {{
    background: #FFFFFF;
    border: 1px solid #DDE5DE;
    border-radius: 6px;
    padding: 8px 13px;
    color: #2A322D;
    font-size: 15px;
}}

QPushButton:hover {{ background: #F2F5F1; border-color: #C6D2C9; }}
QPushButton:pressed {{ background: #E3EBE5; }}

QPushButton:disabled {{
    background: #F4F7F3;
    border-color: #DDE5DE;
    color: #98A29C;
}}

QPushButton#primaryButton {{
    background: #1F6B57;
    border: 1px solid #1F6B57;
    border-radius: 6px;
    color: #FFFFFF;
    font-weight: 600;
    padding: 8px 15px;
}}

QPushButton#primaryButton:hover {{ background: #185844; border-color: #185844; }}
QPushButton#primaryButton:pressed {{ background: #134534; }}

QPushButton#primaryButton:disabled {{
    background: #C4D6CE;
    border-color: #C4D6CE;
    color: #FFFFFF;
}}

QPushButton#iconButton {{
    background: #FFFFFF;
    border: 1px solid #DDE5DE;
    border-radius: 6px;
    color: #4C5650;
    font-weight: 400;
    padding: 7px 12px;
}}

QPushButton#iconButton:hover {{
    background: #F2F5F1;
    border-color: #C6D2C9;
    color: #2A322D;
}}

QPushButton#dangerButton {{
    background: transparent;
    border: 1px solid #DFB5B0;
    border-radius: 6px;
    color: #B4342B;
}}

QPushButton#dangerButton:hover {{ background: #FAF0EF; border-color: #C99A94; }}

/* 禁用态必须显式写：危险色落在按钮上却点不动，比按钮消失更容易误解。
   退回 border / text-faint 两档，"有东西但此刻不可用"一眼可辨 */
QPushButton#dangerButton:disabled {{ border-color: #DDE5DE; color: #98A29C; }}

/* ---------- P0 仪表盘的补充卡片（第 7 期 · 4.1 的 H4） ---------- */

/* 「继续上次」的一行。用 QPushButton 是因为**整行都是点击目标**（"一键回到原处"），
   而内部的文字由子控件承担：QPushButton 的 text **不会换行**，而
   《列表名》第 N 段 这类标题在 300px 的侧栏里必然折行。
   透明填充 + 透明描边：它是卡片里的一行内容，不是一颗按钮 */
QPushButton#resumeRow {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 8px 10px;
    text-align: left;
}}

QPushButton#resumeRow:hover {{ background: #F2F5F1; border-color: #DDE5DE; }}
QPushButton#resumeRow:pressed {{ background: #E3EBE5; }}

/* 「今日已自动记录」条里可点的一个片段。分段可点 = 跳到产生它的模块，
   所以给强调色：它在一行灰字里是唯一的动作 */
QPushButton#recordSegment {{
    background: transparent;
    border: none;
    border-radius: 4px;
    padding: 1px 6px;
    color: #1F6B57;
    font-size: 14px;
}}

QPushButton#recordSegment:hover {{ background: #E3EBE5; }}

/* ---------- 列表 ---------- */
/* DESIGN.md §4：删除每行分割线，改用 hover/selected 填充与留白 */

QListWidget, QListView {{
    background: transparent;
    border: 1px solid #DDE5DE;
    border-radius: 8px;
    outline: 0;
    font-size: 15px;
}}

QListWidget::item, QListView::item {{
    padding: 8px 10px;
    margin: 1px 2px;
    border: none;
    border-radius: 6px;
    color: #2A322D;
}}

QListWidget::item:hover, QListView::item:hover {{ background: #EDF1EC; }}
QListWidget::item:selected, QListView::item:selected {{ background: #E3EBE5; color: #2A322D; }}

QListWidget#taskList {{
    background: transparent;
    border: none;
    outline: 0;
}}

QListWidget#taskList::item {{
    padding: 2px 4px;
    margin: 0;
    border: none;
    border-radius: 6px;
}}

QListWidget#taskList::item:selected {{
    background: transparent;
}}

QPushButton#taskDeleteButton {{
    background: #FFFFFF;
    border: 1px solid #DDE5DE;
    border-radius: 6px;
    color: #6A776F;
    font-size: 13px;
    font-weight: 400;
    padding: 2px 8px;
    min-height: 22px;
}}

QPushButton#taskDeleteButton:hover {{
    background: #FAF0EF;
    border-color: #DFB5B0;
    color: #B4342B;
}}

/* ---------- P2 影子跟读：连续工作区与轻量 Toolbar ---------- */

QFrame#shadowingToolbar {{
    background: transparent;
    border: none;
}}

QFrame#shadowingListPanel {{
    background: #FFFFFF;
    border: 1px solid #DDE5DE;
    border-radius: 6px;
}}

/* 播放列表消除嵌套边框，与外层 panel 一体化 */
QListWidget#trackList {{
    background: transparent;
    border: none;
    border-radius: 0;
}}

/* P3 资料库、P4 碎片列表与分段 */
QListWidget#vaultList, QListWidget#snippetList,
QListWidget#segmentList {{ background: #FFFFFF; }}

/* P2 曲目行与分段行收紧留白 */
QListWidget#trackList::item, QListWidget#segmentList::item {{
    padding: 6px 10px;
    margin: 1px 2px;
    border-radius: 5px;
}}
QListWidget#trackList::item:hover {{ background: #EDF2ED; }}
QListWidget#trackList::item:selected {{ background: #EBF1EC; color: #1F6B57; }}

/* ---------- PDF 阅读区（P2 对照区） ---------- */
/* 消除 Card-in-Card 边框，让 PDF 阅读区以 1px 顶部分割线与标题栏衔接 */

QPdfView#pdfView {{
    background: #FAFBF9;
    border: none;
    border-top: 1px solid #E6ECE6;
    border-radius: 0;
}}

/* P2 底部播放器卡片 */
QFrame#shadowingPlayerCard {{
    background: #FFFFFF;
    border: 1px solid #DDE5DE;
    border-radius: 6px;
}}

QFrame#shadowingPlayerCard QPushButton {{
    padding: 4px 10px;
    font-size: 13px;
    min-height: 28px;
    max-height: 28px;
}}

QFrame#shadowingPlayerCard QPushButton#primaryButton {{
    padding: 4px 14px;
    font-size: 13px;
    min-height: 28px;
    max-height: 28px;
}}

QFrame#shadowingPlayerCard QPushButton#iconButton {{
    padding: 3px 6px;
    font-size: 13px;
    min-height: 28px;
    max-height: 28px;
}}

QFrame#shadowingPlayerCard QComboBox {{
    padding: 3px 6px 3px 8px;
    font-size: 13px;
    min-height: 26px;
    max-height: 26px;
}}

/* P2 工作区分隔条：轻量隐形，悬停反馈 */
QSplitter#shadowingSplitter::handle {{
    background: transparent;
}}
QSplitter#shadowingSplitter::handle:hover,
QSplitter#shadowingSplitter::handle:pressed {{
    background: #DDE5DE;
}}

/* P2 收起/展开左栏：准备条最左边那个圆角小按钮 */
QPushButton#edgeToggle {{
    background: #FFFFFF;
    border: 1px solid #DDE5DE;
    border-radius: 6px;
    color: #4C5650;
    padding: 0;
}}
QPushButton#edgeToggle:hover {{ background: #EDF1EC; border-color: #C6D2C9; }}
QPushButton#edgeToggle:pressed {{ background: #E3EBE5; }}

/* ---------- 跟读录音（P2 底部右半边） ---------- */

/* 待录：安静的白底 + 语义色描边，旁边是"● 录音"的字样 */
QPushButton#recordButton {{
    background: #FFFFFF;
    border: 1px solid #B4342B;
    border-radius: 7px;
    color: #B4342B;
    font-weight: 600;
    padding: 5px 10px;
}}
QPushButton#recordButton:hover {{ background: #FAF0EF; }}
QPushButton#recordButton:pressed {{ background: #F5E2E0; }}

/* 正在录：整块语义红 + 白字。**文字同时变成"■ 停止"**——
   状态色不单独承载语义（DESIGN.md §3.1 纪律 3） */
QPushButton#recordButtonActive {{
    background: #B4342B;
    border: 1px solid #B4342B;
    border-radius: 7px;
    color: #FFFFFF;
    font-weight: 600;
    padding: 5px 10px;
}}
QPushButton#recordButtonActive:hover {{ background: #9C2C24; }}

/* 底部控制卡里分割"原音 | 录音"的竖线。QFrame 的线在部分样式下用调色板的文字色画，
   所以 `color` 与 `background` 都给上，两边都能落到同一个灰 */
QFrame#cardDivider {{
    color: #DDE5DE;
    background: #DDE5DE;
    border: none;
    max-width: 1px;
}}

/* ---------- 复选框 ---------- */

QCheckBox {{
    color: #2A322D;
    font-size: 15px;
    spacing: 9px;
}}

QCheckBox::indicator {{
    width: 17px;
    height: 17px;
    border: 1px solid #B9C6BC;
    border-radius: 4px;
    background: #FFFFFF;
}}

QCheckBox::indicator:hover {{ border-color: #1F6B57; }}

QCheckBox::indicator:checked {{
    background: #1F6B57;
    border-color: #1F6B57;
}}

/* ---------- 表格（P1 词表） ---------- */

QTableWidget, QTableView {{
    background: #FFFFFF;
    alternate-background-color: #FFFFFF;
    border: 1px solid #DDE5DE;
    border-radius: 8px;
    color: #2A322D;
    font-size: 15px;
    gridline-color: transparent;
    outline: 0;
    selection-background-color: #E3EBE5;
    selection-color: #2A322D;
}}

QTableWidget::item, QTableView::item {{
    padding: 6px 8px;
    border: none;
}}

QTableWidget::item:selected, QTableView::item:selected {{
    background: #E3EBE5;
    color: #2A322D;
}}

QHeaderView {{
    background: transparent;
    border: none;
    border-top-left-radius: 7px;
    border-top-right-radius: 7px;
}}

QHeaderView::section {{
    background: #FFFFFF;
    color: #646E68;
    border: none;
    border-bottom: 1px solid #C6D2C9;
    padding: 7px 8px;
    font-size: 14px;
    font-weight: 500;
}}

QHeaderView::section:first {{
    border-top-left-radius: 7px;
}}

QHeaderView::section:last {{
    border-top-right-radius: 7px;
}}

QHeaderView::section:hover {{ color: #2A322D; }}
QHeaderView::section:checked {{ color: #2A322D; font-weight: 500; }}

QTableCornerButton::section {{ background: transparent; border: none; }}

/* ---------- 滑块（P2 进度条） ---------- */

QSlider::groove:horizontal {{
    height: 4px;
    background: #DDE5DE;
    border-radius: 2px;
}}

QSlider::sub-page:horizontal {{ background: #1F6B57; border-radius: 2px; }}

QSlider::handle:horizontal {{
    background: #1F6B57;
    width: 13px;
    height: 13px;
    margin: -5px 0;
    border-radius: 6px;
}}

QSlider::handle:horizontal:hover {{ background: #185844; }}

/* ---------- 分隔条 ---------- */

QSplitter::handle {{
    background: transparent;
}}
QSplitter::handle:horizontal {{
    width: 8px;
}}
QSplitter::handle:vertical {{
    height: 8px;
}}

/* ---------- 日期输入（P5 考试日期） ---------- */
/* 复用与 QComboBox 完全相同的取值，不引入新 token */

QDateEdit, QDateTimeEdit {{
    background: #FFFFFF;
    border: 1px solid #DDE5DE;
    border-radius: 6px;
    padding: 7px 10px;
    color: #2A322D;
    font-size: 15px;
    min-width: 120px;
}}

QDateEdit:hover, QDateTimeEdit:hover {{ background: #F2F5F1; border-color: #C6D2C9; }}
QDateEdit:focus, QDateTimeEdit:focus {{ border-color: #1F6B57; }}
QDateEdit::drop-down, QDateTimeEdit::drop-down {{ border: none; width: 18px; }}

QCalendarWidget QWidget {{ font-size: 14px; }}
QCalendarWidget QAbstractItemView:enabled {{
    color: #2A322D;
    background: #FFFFFF;
    selection-background-color: #E3EBE5;
    selection-color: #2A322D;
}}

/* ---------- 滚动区域（P4 图片预览、P5 设置页） ---------- */

QScrollArea, QScrollArea#previewScroll {{
    background: transparent;
    border: 1px solid #DDE5DE;
    border-radius: 6px;
}}

QScrollArea > QWidget > QWidget {{ background: transparent; }}

/* ---------- 滚动条 ---------- */
/* 去掉两端箭头 */

QScrollBar:vertical {{
    background: transparent;
    width: 11px;
    margin: 0;
    border: none;
}}

QScrollBar::handle:vertical {{
    background: #C6D2C9;
    border-radius: 5px;
    min-height: 28px;
    margin: 2px;
}}

QScrollBar::handle:vertical:hover {{ background: #ADBCB1; }}
QScrollBar::handle:vertical:pressed {{ background: #98A29C; }}

QScrollBar:horizontal {{
    background: transparent;
    height: 11px;
    margin: 0;
    border: none;
}}

QScrollBar::handle:horizontal {{
    background: #C6D2C9;
    border-radius: 5px;
    min-width: 28px;
    margin: 2px;
}}

QScrollBar::handle:horizontal:hover {{ background: #ADBCB1; }}
QScrollBar::handle:horizontal:pressed {{ background: #98A29C; }}

QScrollBar::add-line, QScrollBar::sub-line {{
    width: 0;
    height: 0;
    background: none;
    border: none;
}}

QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

QAbstractScrollArea::corner {{
    background: transparent;
    border: none;
}}

/* ---------- 菜单与弹窗 ---------- */

QMenu {{
    background: #FFFFFF;
    border: 1px solid #C6D2C9;
    border-radius: 6px;
    padding: 4px;
    color: #2A322D;
    font-size: 15px;
}}

QMenu::item {{ padding: 7px 20px 7px 10px; border-radius: 4px; }}
QMenu::item:selected {{ background: #E3EBE5; }}
QMenu::separator {{ height: 1px; background: #DDE5DE; margin: 4px 6px; }}

QMessageBox {{ background: #FFFFFF; }}
QMessageBox QLabel {{ color: #2A322D; font-size: 15px; }}

/* 首次启动向导（P6）。模态对话框是唯一允许的"新窗口"（PRODUCT_SPEC 2.1） */
QDialog#onboardingDialog {{ background: #F6F8F5; }}

/* ---------- P1 单词仓（第 3 期） ---------- */

/* 词表选择器展开弹窗：支持拖拽排序与行末 × 删除 */
QFrame#wordListPopup {{
    background: #FFFFFF;
    border: 1px solid #C6D2C9;
    border-radius: 6px;
}}

QListWidget#wordListPopupView {{
    background: transparent;
    border: none;
    outline: 0;
    padding: 4px;
}}

QListWidget#wordListPopupView::item {{
    padding: 0px;
    margin: 1px 0;
    border: none;
    border-radius: 5px;
    color: #2A322D;
}}

QListWidget#wordListPopupView::item:hover {{
    background: #EDF1EC;
}}

QListWidget#wordListPopupView::item:selected {{
    background: #E3EBE5;
    color: #2A322D;
}}

QLabel#wordListItemText {{
    background: transparent;
    color: #2A322D;
    font-size: 14px;
}}

QPushButton#wordListDeleteBtn {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 4px;
    padding: 0px;
}}

QPushButton#wordListDeleteBtn:hover {{
    background: #FAF0EF;
    border-color: #DFB5B0;
}}

/* 模式切换：浏览 / 过词 / 专攻 三段控件。
   选中态用 bg-selected 填充 + accent 文字——不给实心强调块（DESIGN.md 差异 2 的纪律：
   高频、低信息量的控件不抢注意力），但文字转 accent 足以表达"你在这一段" */
QPushButton#modeSwitch {{
    background: transparent;
    border: 1px solid #DDE5DE;
    border-radius: 6px;
    padding: 6px 15px;
    color: #4C5650;
    font-size: 14px;
}}

QPushButton#modeSwitch:hover {{ background: #F2F5F1; color: #2A322D; }}

QPushButton#modeSwitch:checked {{
    background: #E3EBE5;
    border-color: #C6D2C9;
    color: #1F6B57;
}}

/* 过词/专攻实体卡片 */
QFrame#vocabCard {{
    background: #FFFFFF;
    border: 1px solid #DDE5DE;
    border-radius: 12px;
}}

QLabel#vocabCardMeta {{
    color: #98A29C;
    font-size: 12px;
    font-weight: 500;
}}

QLabel#vocabCardProgress {{
    color: #98A29C;
    font-size: 12px;
}}

QLabel#vocabCardHint {{
    color: #98A29C;
    font-size: 11px;
    margin-top: 4px;
}}

/* 过词/专攻卡：居中主体文字 */
QLabel#quizWord {{
    font-family: "Pretendard", "Microsoft YaHei UI", sans-serif;
    font-size: 42px;
    font-weight: 600;
    color: #2A322D;
}}

QLabel#quizMeaning {{
    font-size: 18px;
    color: #4C5650;
}}

QLabel#quizSeq {{
    color: #98A29C;
    font-size: 13px;
}}

/* 过词判断按钮：按键提示是本卡唯一的操作路径，故比普通次级按钮高一档字重 */
QPushButton#judgeButton {{
    background: #FFFFFF;
    border: 1px solid #DDE5DE;
    border-radius: 6px;
    padding: 10px 22px;
    color: #2A322D;
    font-size: 15px;
}}

QPushButton#judgeButton:hover {{ background: #F2F5F1; border-color: #C6D2C9; }}

/* 详情卡里的重点标记：单行图标 + 文字，不用实心按钮（重点不是本页的主操作） */
QPushButton#starToggle {{
    background: transparent;
    border: none;
    border-radius: 4px;
    padding: 3px 7px;
    color: #646E68;
    font-size: 13px;
    text-align: left;
}}

QPushButton#starToggle:hover {{ background: #F2F5F1; color: #2A322D; }}

/* 笔记自动保存提示：极轻，只做"写了没有"的确认，不抢视线 */
QLabel#saveHint {{ color: #98A29C; font-size: 12px; }}

/* ---------- 语义状态色（DESIGN.md §3.1） ---------- */
/* 用法：给 QLabel 设对应 objectName。必须同时有文字或图标，不单独用颜色表意 */

QLabel#stateSuccess {{ color: #1F7A4D; font-size: 14px; }}
QLabel#stateWarning {{ color: #8A6410; font-size: 14px; }}
QLabel#stateDanger  {{ color: #B4342B; font-size: 14px; }}
QLabel#stateInfo    {{ color: #1F5FA8; font-size: 14px; }}

/* ---------- P1 单词卡片 ---------- */

QLabel#wordDisplay {{
    font-family: "Pretendard", "Microsoft YaHei UI", sans-serif;
    font-size: 26px;
    font-weight: 600;
    color: #2A322D;
}}

QLabel#wordMeaning {{
    font-size: 16px;
    color: #4C5650;
}}

/* ---------- P3 资料库（第 4 期） ---------- */

/* 标签胶囊：可点击 → 按标签过滤。用淡填充而不是描边按钮，
   因为它是"资料上的一个标记"，不是页面上的一个操作 */
QPushButton#tagChip {{
    background: #E3EBE5;
    border: 1px solid #DDE5DE;
    border-radius: 11px;
    padding: 3px 10px;
    color: #1F6B57;
    font-size: 13px;
    text-align: left;
}}

QPushButton#tagChip:hover {{ background: #D7E4DA; border-color: #C6D2C9; }}

/* 胶囊右侧的移除按钮：窄，且不带文字，靠 tooltip 说明 */
QPushButton#chipRemove {{
    background: transparent;
    border: none;
    border-radius: 4px;
    padding: 3px;
}}
QPushButton#chipRemove:hover {{ background: #F2F5F1; }}

/* 详情侧栏与设置页的滚动容器：border 由内部 QFrame#surface 承担，这里再画一圈就是双线 */
QScrollArea#detailScroll, QScrollArea#settingsScroll {{ border: none; background: transparent; }}

/* ---------- 碎片/预览占位 ---------- */

QLabel#previewArea {{
    color: #98A29C;
    font-size: 14px;
}}

/* ---------- D-4 组件：空状态 / 提示条 / 轻提示 / 行内进度 ---------- */
/* 见 ui/components.py；命名契约见 PRODUCT_SPEC 3.2 */

QLabel#emptyStateTitle {{
    color: #2A322D;
    font-size: 16px;
    font-weight: 600;
}}

QLabel#emptyStateDesc {{
    color: #646E68;
    font-size: 14px;
}}

/* 提示条：底色淡染 + 1px 边框；语义色由**图标**承担，正文保持 text-primary 以便阅读 */
QFrame#banner {{ border-radius: 6px; border: 1px solid #DDE5DE; background: #F2F5F1; }}
QFrame#bannerInfo    {{ background: #EAF1FA; border: 1px solid #C3D7ED; }}
QFrame#bannerWarning {{ background: #FAF2E2; border: 1px solid #E4D0A6; }}
QFrame#bannerDanger  {{ background: #FBEDEB; border: 1px solid #EEC5BF; }}
QFrame#bannerSuccess {{ background: #E9F4EE; border: 1px solid #BFDCCB; }}

QLabel#bannerText {{ color: #2A322D; font-size: 14px; }}

/* 轻提示：浮在内容之上，用更实的边框与底色区分层级 */
QLabel#toast {{
    border-radius: 6px;
    padding: 8px 14px;
    font-size: 14px;
    color: #2A322D;
    background: #FFFFFF;
    border: 1px solid #C6D2C9;
}}
QLabel#toastInfo    {{ background: #EAF1FA; border: 1px solid #A9C6E6; }}
QLabel#toastWarning {{ background: #FAF2E2; border: 1px solid #D9BE8C; }}
QLabel#toastDanger  {{ background: #FBEDEB; border: 1px solid #E0AFA7; }}
QLabel#toastSuccess {{ background: #E9F4EE; border: 1px solid #A9D2BC; }}

/* 行内进度：细条，配合 InlineProgress 的固定高度 4px */
QProgressBar#progressInline {{
    background: #DDE5DE;
    border: none;
    border-radius: 2px;
}}

QProgressBar#progressInline::chunk {{
    background: #1F6B57;
    border-radius: 2px;
}}
"""

# 需要拼接运行时路径的规则单独追加，避免把整段样式表变成 f-string
# （那样所有 QSS 花括号都要转义，很易错）
APP_STYLESHEET = APP_STYLESHEET + f"""
/* ---------- 复选框选中态的对勾（资源文件见 ui/assets/check.svg） ---------- */

QCheckBox::indicator:checked {{
    image: url({_CHECK_SVG});
}}
"""

# ---------------------------------------------------------------------------
# 需要在**代码里**用色的地方：图标着色、QTableWidgetItem 的底色与文字色。
#
# 这几处没法走 QSS —— `QTableWidgetItem` 的颜色由数据角色承载，`icon()` 是
# 把色值插进 SVG 字符串。为了让"颜色只有 `design/DESIGN.md` 一个来源"这条纪律
# 仍然成立，凡是代码里要用到的色值都在这里导出一次，视图与图标模块不写字面量。
# 取值与上面样式表里的内联值一一对应，改色时**两处一起改**。
# ---------------------------------------------------------------------------

BG_SURFACE = "#FFFFFF"

# DESIGN.md §3.1 的四个文字档
TEXT_COLORS = {
    "primary": "#2A322D",
    "secondary": "#4C5650",
    "muted": "#646E68",
    "faint": "#98A29C",
}

ACCENT = "#1F6B57"
ACCENT_WARM = "#8A5F12"  # 仅倒计时与"重点"标记

# DESIGN.md §3.1 的四个语义状态色
STATE_COLORS = {
    "success": "#1F7A4D",
    "warning": "#8A6410",
    "danger": "#B4342B",
    "info": "#1F5FA8",
}

# ---------------------------------------------------------------------------
# 表格行的底色。**QTableWidgetItem 的底色不走 QSS**（它由 QTableWidgetItem 的
# 数据角色承载，样式表管不到），只能在代码里设 QColor。
#
#   ROW_DRILL_BG  待专攻行的淡暖底（DESIGN.md §3.1 的 `bg-drill`）
#                 —— `PRODUCT_SPEC` 4.2 把这个行状态记为 `state-starred`；
#                    v1.2 的两遍式流程定下它真正的含义是"待专攻"（模糊 + 不认识）
#   ROW_BASE_BG   普通行（= bg-surface）
# ---------------------------------------------------------------------------

ROW_DRILL_BG = "#FFF6E3"
ROW_BASE_BG = BG_SURFACE