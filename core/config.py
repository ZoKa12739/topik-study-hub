"""项目路径与默认配置。

本模块同时承担两件事：

1. **解析项目自身的路径**（`PROJECT_ROOT` / `DATA_DIR` / `DATABASE_PATH` / `BACKUP_DIR`）。
   一律从 `__file__` 推导，不依赖当前工作目录。

2. **提供资料根目录的"自动探测"**，对应 `PRODUCT_SPEC` D10 方案 C：
   *首次自动探测预填，之后以配置为准*。

为什么要探测（而不是硬编码推导）：原实现用"从 `__file__` 向上三级"当作资料根目录，
项目一移动或资料重排就静默失效。实测本机的资料在 `课程资料/` 之下又往下迁了一层，
硬编码推导指到的 `课程资料/` 下**根本没有**
写作/听力/阅读/单词——也就是说 P3 资料库与 P4 碎片页在这台机器上一直是坏的。
探测 + 把结果落进 `settings` 表，才是稳定做法。

**探测只用于首次预填。** 真正的取值存在 `settings` 表（见 `core/database.py`）；
探测函数绝不抛异常，失败就交给 P6 向导 / P5 设置页让用户手动选。
"""

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DATA_DIR = PROJECT_ROOT / "data"
DATABASE_PATH = DATA_DIR / "study_hub.db"
BACKUP_DIR = DATA_DIR / "backup"          # 迁移前备份与每日备份（PRODUCT_SPEC 6.4 / 6.5）
SNIPPETS_DIR = DATA_DIR / "snippets"      # P4 碎片图片存放处（D-2 决策，第 6 期启用）
AUDIO_DIR = DATA_DIR / "audio"            # P2 音频库（D15 决策，第 5 期启用）
RECORDINGS_DIR = DATA_DIR / "recordings"  # P2 跟读录音的默认落点（P5 可改，见 settings 表）
TTS_DIR = DATA_DIR / "tts"                # P1 单词发音缓存目录（F3 联网发音本地缓存）

# 资料库里被认作"科目"的四个标准分类，以及未匹配时的"其他"兜底分类。
SUBJECT_FOLDERS = ("写作", "听力", "阅读", "单词")
OTHER_SUBJECT = "其他"
MATERIAL_SUBJECTS = (*SUBJECT_FOLDERS, OTHER_SUBJECT)

# 智能科目推断关键词（按科目顺序匹配）
_SUBJECT_KEYWORDS = (
    ("写作", ("写作", "작문", "쓰기", "作文")),
    ("听力", ("听力", "듣기")),
    ("阅读", ("阅读", "읽기")),
    ("单词", ("单词", "词汇", "어휘", "단어")),
)

# 探测时要跳过的目录名：项目自身、字体缓存、Python 缓存
_SKIP_DIRS = {"TOPIK_Study_Hub", "_font_cache", "__pycache__", ".git", ".workbuddy"}

# 默认考试信息。只用于**首次**填入 settings 表；之后以库里的值为准（P5 可改）。
DEFAULT_EXAM_DATE = "2027-04-11"
DEFAULT_EXAM_LABEL = "第 109 届 TOPIK 考试"

# 默认单词发音配置（F3 & Azure AI Speech）
DEFAULT_TTS_MODE = "online"
DEFAULT_TTS_VOICE = "azure_ko_dragon_hd"
DEFAULT_AZURE_REGION = "eastus"


# 当前 schema 版本（PRODUCT_SPEC 6.4）。每次表结构变更都要 +1 并补一段迁移。
# v2：word_states → words + word_notes（D2 方案 B），引入三态（D14）
# v3：P3 资料库索引 materials + 第二层 material_tags / material_usage / reading_progress
# v4：资料路径规范化（`/` → `\`）并合并由此产生的重复行——见 normalize_path 的说明
# v5：P2 音频库——audio_tracks / playlists / playlist_items / track_progress /
#     track_segments / playback_state（纯加法，没有要搬运的旧数据）
# v6：playlists 补 kr_pdf / cn_pdf——每份列表配"原文"与"解析"两份 PDF
# v7：playlists 再补 kr_page / cn_page——两份 PDF 各自记住读到第几页
# v8：P4 知识碎片——snippets / snippet_tags（纯加法，没有要搬运的旧数据）
# v9：playlist_items 补 kr_page / cn_page——曲目级页码关联（切题自动翻页）
SCHEMA_VERSION = 9


def normalize_path(path):
    """路径的**存储形态**：`os.path.normpath`。

    Windows 上这会把 `/` 换成反斜杠、去掉 `.` 段与结尾的分隔符。这是必需品而不是洁癖：
    `QFileDialog.getOpenFileName` 返回**正斜杠**形式（`D:/x/y.png`），而 `os.scandir`
    返回**反斜杠**形式——两者指向同一个文件，字符串却不相等。踩过的一次：用 P3 的
    「重新定位」把文件从 `单词/` 挪到 `阅读/` 之后，下次扫描认不出这个路径，于是为
    同一个文件**另插一行**（新行的科目是对的），把原来那行标成"已不在原位置"，
    而标签还挂在原来那行上。
    """
    return os.path.normpath(str(path))


def path_key(path):
    """路径的**比较形态**：在存储形态之上再折平大小写。

    磁盘上 `D:\\X` 与 `d:\\x` 是同一个文件，字符串比较必须按这个事实来，
    否则就又是一次"看起来一样、比出来不等"。
    """
    return os.path.normcase(normalize_path(path))


def is_under_root(path, root):
    """判断 `path` 是否位于 `root` 目录树下（含 `root` 本身）。"""
    if not path or not root:
        return False
    try:
        pk = path_key(path)
        rk = path_key(root)
        return os.path.commonpath([pk, rk]) == rk
    except ValueError:
        return False


def subject_of(path, root):
    """从路径反推科目：严格按「精确同名目录 > 目录名含关键词 > 文件名含关键词 > 其他」优先级。

    优先从距离文件最近的父目录向上查找，避免文件名中的偶发字眼覆盖所属目录分类，
    同时支持多层嵌套或任意命名的资料根目录。若不在 `root` 之下则返回 `None`。
    """
    if not root:
        return None
    try:
        relative = os.path.relpath(normalize_path(path), normalize_path(root))
    except ValueError:
        return None   # 不同盘符，没有相对路径
    if relative == ".." or relative.startswith(".." + os.sep):
        return None   # 不在资料根之下

    parts = [p for p in relative.split(os.sep) if p and p != "."]
    if not parts:
        return OTHER_SUBJECT
    dir_parts = parts[:-1]
    file_stem = os.path.splitext(parts[-1])[0].lower()

    # 1. 精确同名目录（从最近父目录向上）
    for part in reversed(dir_parts):
        if part in SUBJECT_FOLDERS:
            return part

    # 2. 目录名含关键词（从最近父目录向上）
    for part in reversed(dir_parts):
        lower_part = part.lower()
        for subject, keywords in _SUBJECT_KEYWORDS:
            if any(kw in lower_part for kw in keywords):
                return subject

    # 3. 文件名含关键词
    for subject, keywords in _SUBJECT_KEYWORDS:
        if any(kw in file_stem for kw in keywords):
            return subject

    # 4. 兜底归入「其他」
    return OTHER_SUBJECT


def detect_material_root(search_base=None, max_depth=3):
    """探测同时含有写作/听力/阅读/单词四个子文件夹的目录。

    从 `search_base`（默认 `PROJECT_ROOT.parent`）做**限深广度优先**，
    返回最浅的那个匹配目录（绝对路径字符串），找不到返回 `None`。

    用 BFS 而非 DFS 是为了拿到**最浅**的匹配：越浅越可能是用户真正想指的资料根，
    深层的同名文件夹更可能是巧合。
    """
    base = Path(search_base) if search_base else PROJECT_ROOT.parent
    if not base.is_dir():
        return None

    frontier = [(base, 0)]
    while frontier:
        current, depth = frontier.pop(0)
        try:
            if all((current / name).is_dir() for name in SUBJECT_FOLDERS):
                return str(current)
        except OSError:
            continue
        if depth >= max_depth:
            continue
        try:
            children = sorted(entry for entry in current.iterdir() if entry.is_dir())
        except OSError:
            continue
        for entry in children:
            if entry.name in _SKIP_DIRS:
                continue
            frontier.append((entry, depth + 1))
    return None


def dir_size(path):
    """目录占用字节数（递归）。目录不存在或不可读时返回 0，不抛异常。"""
    total = 0
    target = Path(path)
    if not target.is_dir():
        return 0
    try:
        for item in target.rglob("*"):
            try:
                if item.is_file():
                    total += item.stat().st_size
            except OSError:
                continue
    except OSError:
        return total
    return total