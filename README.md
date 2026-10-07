# TOPIK Study Hub

本地优先的 TOPIK 韩语备考桌面工具箱。PySide6 + SQLite，单用户、无服务端，网络功能不是必需品且默认关闭。

为一位备考 TOPIK 的学习者写的自用工具：把「今天学什么」「单词怎么过」「跟读怎么练」「资料在哪」「碎片怎么攒」这五件事收进一个窗口，并且把每天做过的事情记录成可以回看的数字。

## 功能

窗口左侧是五个模块页，右下角是设置页（`Ctrl+,`）。

| 页面 | 做什么 |
|---|---|
| **今日学习** | 四个指标卡（今日完成、连续学习、本周进度、今日亮点）+ 考试倒计时模块卡 + 7 日周回顾条带 + 今日自动记录条 + 继续上次。**只读现成的表，自身不产生任何记录** |
| **智能单词仓** | 词表导入、Anki 导出与三种背词模式：**浏览**（表格，查词、记笔记、改三态、加星）、**过词**（居中实体卡片，一词一判定 `1`/`2`/`3`，`Space` 揭晓释义，`Ctrl+Z` 撤回）、**专攻**（居中实体卡片，释义展开、支持跨词表专攻与单步回退） |
| **影子跟读** | 音频库与播放列表、AB 循环、进度条点击跳转、跟读录音混音（按 `{届数}届{起止题号}第{N}次.mp3` 命名并可一键打开录音文件夹）；听读时长按分钟累计进活动日志 |
| **TOPIK 资料库** | 写作 / 听力 / 阅读 / 单词 四个科目的资料索引，支持 PDF 内置预览、标签、阅读进度、打开次数与右键删除（走回收站） |
| **知识碎片** | 从资料库里筛出图片，也可粘贴 / 导入；每张图带标题（修改标题同步重命名磁盘文件）与笔记，可跳回资料库原位置 |
| **设置与数据** | 考试信息、资料根目录、ffmpeg 自检、数据导出 / 导入 / 备份、Anki 导出 |

首次启动有一次性向导（三步，每步可跳过）。

## 运行

```powershell
pip install -r requirements.txt
python main.py
```

Windows 上也可以双击 `Start_Study_Hub.bat`（内部是 `pythonw main.py`，不弹控制台）。

### 环境要求

- **Python 3.10 及以上**（开发环境为 3.13）
- **ffmpeg** 在 `PATH` 上 —— 用于将跟读人声录音与听力原音混音导出 MP3（缺失或混音失败时会自动降级保留纯人声录音）
- **PyMuPDF** 是可选依赖，用来预览 PDF 与读取页数；缺失或装坏时降级为不显示页数，不报错

`requirements.txt`：`PySide6` / `PyMuPDF` / `ffmpeg-python` / `schedule`。

### 数据在哪

首次运行会自动创建 `data/`，其中：

| 路径 | 内容 |
|---|---|
| `data/study_hub.db` | SQLite 数据库，全部记录都在这里 |
| `data/backup/` | 迁移前的自动备份与每日备份（各保留最新 7 份） |
| `data/audio/` | 音频库 |
| `data/recordings/` | 跟读录音 |
| `data/snippets/` | 粘贴 / 导入进来的图片 |

整个 `data/` **不进版本库** —— 那是使用者的私人学习数据，不是项目源码。

数据库带 schema 版本号。升级时会先备份再迁移，迁移失败则保留原文件不动，绝不静默换成一个空库。

## 目录结构

```
main.py               入口：建 QApplication、注册字体、套样式表、开主窗口
core/
  config.py           路径推导、资料根目录探测、schema 版本
  database.py         唯一数据层（stdlib sqlite3），全部读写都在这
  library.py          P3 文件系统扫描（QThread）与回收站删除
  snippets.py         P4 碎片来源合并与图片导入
ui/
  main_window.py      组合根 —— 唯一构造视图的地方，六个视图共享一个 StudyDatabase
  theme.py            整个样式表，一个字符串；色值全部来自 design/DESIGN.md
  fonts.py            Pretendard 加载与字体栈
  icons.py            32 个内联 SVG 线性图标
  style.py            restyle() / set_state()，运行时切换 objectName
  components.py       EmptyState / Banner / InlineProgress / Toast
  *_view.py           六个页面
docs/
  PRODUCT_SPEC.md     权威产品规格 v1.11（功能、页面、数据模型、决策 D1–D16、路线图）
design/
  DESIGN.md           权威视觉规格 v1.8（色板 / 字号 / 间距 token，Linear 派生的 10 条规则）
USER_CONTEXT.md       实际学习投入时间统计规范与原则
samples/              合成测试夹具：100 词 TSV、中韩对照文本各一份
tests/                单元测试套件（Anki 导出、周回顾、P0 指标，共 92 项）
tools/check_names.py  用 ast 找出「读了但从未绑定」的名字
```

## 开发

三道快速检查，**每一道能抓到下一道抓不到的东西，所以三道都要跑**：

```powershell
python -m compileall -q core ui main.py   # 只查语法，不做名字解析
python tools/check_names.py               # 用 ast 找出未定义的名字
$env:QT_QPA_PLATFORM='offscreen'; python -c "from PySide6.QtWidgets import QApplication; from ui.main_window import MainWindow; app=QApplication([]); w=MainWindow(); print(w.stacked_widget.count())"
```

第三条会构造每一个视图（数据库访问与文件系统扫描都发生在建界面时），预期打印 `6`。**它会碰到真实的 `data/study_hub.db`**，可能触发迁移；要安全地试 schema 改动，请对副本运行 `StudyDatabase(某个副本路径)`。

注意：`QT_QPA_PLATFORM=offscreen` 下 `QFontDatabase.families()` 返回 **0** —— 没有任何系统字体加载，离屏截图里每个字都是空白方块。离屏渲染只能用来验证**布局、颜色、间距、密度**；字体栈、中文回退与韩文可读性必须在真实窗口里看。

`tests/` 下是基于临时数据库的单元测试套件（共 92 项，覆盖 Anki 导出、周回顾口径与 P0 指标），可通过 `python -m unittest discover tests` 一键运行。

三个约定，改代码前值得知道：

- **所有颜色和尺寸都来自 `design/DESIGN.md` §3**，`ui/theme.py` 是它的逐字转写。视图里不要写十六进制字面量，也不要在视图里调 `setStyleSheet()` —— 控件级样式表会盖过应用级样式表。
- **第一层 / 第二层**：`materials`、`words`、`snippets` 的文件相关列是可以从磁盘重建的；标签、笔记、三态、阅读进度是使用者做出来的，**绝不能丢**。所有写操作都按这条线划。
- **退出路径欠使用者几件事**：跟读分钟的冲刷、P1 与 P4 的待写入笔记、P3 扫描线程与 P4 缩略图线程的 `shutdown()`。新增离开页面的路径就要补上对应的 flush，否则丢的是使用者的输入或时长。

## 关于 OCR

早期版本通过 Tesseract 从 PDF 和图片提取词表，**已整体移除**。原因：外部脚本对两栏表格固定用 `--psm 6`，对手工核对的 50 词基准实测 **0/50**；而一半的词表 PDF 本身带完好的文本层，OCR 反而把它毁掉。

现在词表由外部提供 TSV，导入格式是 `vocab_view.load_tsv` 的契约：**三列制表符分隔 —— `编号 / 韩语 / 中文`，无表头，`utf-8-sig`，`\t` 分隔**。少于三列或韩语列为空的行会被静默丢弃。见 `samples/README.md`。

## 字体

界面字体栈是 `Pretendard → Microsoft YaHei UI → Microsoft YaHei → sans-serif`。

Pretendard **不含简体中文字形**（实测 11/11 缺失），中文必须回退到微软雅黑。`ui/fonts.py` 用 `addApplicationFontFromData` 而非 `addApplicationFont(path)`，因为本项目根目录含中文与韩文，而 Qt 按路径加载字体在路径含非 ASCII 时**一律返回 -1**。

仓库里只放实际加载的 4 个字重（Regular / Medium / SemiBold / Bold，约 6 MB）；整套 Pretendard 的其余字重与 web 格式没有上传。字体许可为 **SIL Open Font License 1.1**，见 `design/Pretendard-1.3.9/LICENSE.txt`。

## 文档

- `docs/PRODUCT_SPEC.md` —— 权威产品规格（v1.11）。功能边界、页面结构、数据模型、决策记录 D1–D16 都在这里，改行为前先读它
- `design/DESIGN.md` —— 权威视觉规格（v1.8）
- `USER_CONTEXT.md` —— 学习时间记录需求与设计原则
- `CLAUDE.md` / `AGENTS.md` —— 给 coding agent 的项目说明：架构、踩过的坑、每条踩坑背后的实测证据
- `docs/Toolkit_Design_Proposal.md` —— 最初的构想稿，已被 `PRODUCT_SPEC.md` 取代
- `docs/task_plan.md` / `docs/findings.md` / `docs/progress.md` —— 开发会话与阶段演进记录

## 许可

项目代码本身尚未指定开源许可。仓库内的第三方素材各自遵循其原有许可：Pretendard 为 SIL OFL 1.1。
