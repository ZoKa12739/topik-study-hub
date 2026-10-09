"""P3 资料库的**文件系统扫描**（`PRODUCT_SPEC` 4.4 / D6 方案 C）。

## 这一层负责什么

只做一件事：把资料根目录走一遍，产出一份 `records`（第一层：路径、名字、科目、
扩展名、大小、mtime）。**它不碰数据库。** 落库由调用方在主线程做
（`StudyDatabase.apply_material_scan`）。

## 为什么扫描不碰 sqlite

`sqlite3.connect()` 默认 `check_same_thread=True`，一个连接跨线程使用会直接抛
ProgrammingError；本项目是**单连接**贯穿六个页面（`MainWindow` 只建一个
`StudyDatabase`），所以线程里唯一的正确做法就是不碰它。扫描慢在 I/O，落库快在
内存——把慢的部分放进线程，就已经达到了"不阻塞 UI"的全部目的，没有必要为了
"在后台写库"再去开第二个连接（那会引入并发写与锁的问题，换不到任何收益）。

## 唯一的写操作：删除文件

模块开头说过"扫描只产出记录、不碰数据库"，那句仍然成立。但本模块现在还有第二个公开
函数 `delete_file`——**它是整个工具唯一会改动资料文件的地方**，由用户在 P3 的右键菜单
里显式发起、经一次确认弹框，并且送进回收站而不是直接删除。原则 P6（资料文件只读）
因此有了一个写明了的例外，见 `PRODUCT_SPEC` 4.4 的 `〔v1.9 实施裁定〕`。

## 排除规则：显式判定，不用子串

原实现靠 `if "TOPIK_Study_Hub" in root: continue` 判断"这是不是本工具自己的目录"——
把工具项目**改名或移动**就失效了，而且任何名字里含这串字的资料夹都会被误杀。
现在直接比对 `core/config.PROJECT_ROOT` 的绝对路径：**工具自己的项目目录永远不是
学习资料**，且判据与目录名无关（4.4 状态表最后一条点名要改的就是这里）。

## 增量靠什么

`(size, mtime)` 两个字段。mtime 取整秒——NTFS 的精度足够判断"文件变没变"，
而把它存成浮点会在导出 JSON 时引入无意义的精度噪声。
"""

import ctypes
import os
import sys
from ctypes import wintypes
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from core.config import OTHER_SUBJECT, PROJECT_ROOT, normalize_path, subject_of
from core.snippets import IMAGE_EXTS

# 资料库收录的文档课件与图片扩展名白名单（音视频、.tsv/.csv、压缩包等不进资料库）
DOC_EXTS = frozenset(
    {
        ".pdf",
        ".doc",
        ".docx",
        ".ppt",
        ".pptx",
        ".xls",
        ".xlsx",
        ".md",
        ".txt",
        ".html",
        ".htm",
        ".hwp",
    }
)
MATERIAL_EXTS = DOC_EXTS | IMAGE_EXTS

# 永远不进索引的目录名。前四个是工具/系统产生的，后两个是编辑器与回收站。
SKIP_DIR_NAMES = frozenset(
    {
        "_font_cache",
        "__pycache__",
        ".git",
        ".workbuddy",
        ".idea",
        ".vscode",
        "$RECYCLE.BIN",
        "System Volume Information",
    }
)

# 工具自身项目目录的绝对路径（规范化大小写，Windows 下大小写不敏感）
_PROJECT_ROOT_KEY = os.path.normcase(os.path.abspath(str(PROJECT_ROOT)))


def file_extension(name):
    return os.path.splitext(name)[1].lower()


def is_ignored_file(name):
    """隐藏文件与 Office 的临时锁文件（`~$xxx.docx`）。"""
    return name.startswith(".") or name.startswith("~$")


def is_material_file(name):
    """判断文件后缀是否属于资料库收录的课件或图片。"""
    return file_extension(name) in MATERIAL_EXTS


def scan_material(root, progress=None, should_cancel=None):
    """遍历资料根目录（支持多层结构），仅收录课件与图片并通过路径智能推断科目。

    返回 `(records, notes)`：

    * `records` — `[{path, name, subject, ext, size, mtime}]`，绝对路径，第一层数据
    * `notes`   — `{"errors": [...], "skipped": N}`；单个文件读不到**不让整次索引失败**
      （4.4 状态表：在索引完成后汇总提示"N 个文件无法读取"）

    `progress(discovered_count)` 会被频繁调用（每 200 个文件一次），
    `should_cancel()` 返回真时尽快收工——**取消掉的扫描不是完整扫描**，
    调用方据此禁止"标记缺失"，否则会把整个资料库误判成丢失。
    """
    records = []
    notes = {"errors": [], "skipped": 0}
    root_path = Path(root) if root else None
    if not root_path or not root_path.is_dir():
        return records, notes

    root_str = normalize_path(root_path)

    def report():
        if progress:
            progress(len(records))

    _walk(root_str, root_str, records, notes, should_cancel, report)
    return records, notes


def _walk(directory, root_str, records, notes, should_cancel, report):
    """深度优先。用 `os.scandir` 而不是 `os.walk`——`DirEntry` 自带 stat 缓存，
    同一个文件不会为了拿大小/时间再多跑一次系统调用。"""
    try:
        entries = list(os.scandir(directory))
    except OSError as error:
        notes["errors"].append(f"{directory}：{error.strerror or error}")
        return

    for entry in entries:
        if should_cancel and should_cancel():
            return
        try:
            if entry.is_dir(follow_symlinks=False):
                # 隐藏目录、工具/系统目录，以及**本工具自己的项目目录**（整棵子树剪掉）
                if entry.name.startswith(".") or entry.name in SKIP_DIR_NAMES:
                    continue
                if os.path.normcase(os.path.abspath(entry.path)) == _PROJECT_ROOT_KEY:
                    continue
                _walk(entry.path, root_str, records, notes, should_cancel, report)
            elif entry.is_file(follow_symlinks=False):
                if is_ignored_file(entry.name) or not is_material_file(entry.name):
                    notes["skipped"] += 1
                    continue
                stat = entry.stat()
                norm_path = normalize_path(entry.path)
                subject = subject_of(norm_path, root_str) or OTHER_SUBJECT
                records.append(
                    {
                        "path": norm_path,
                        "name": entry.name,
                        "subject": subject,
                        "ext": file_extension(entry.name),
                        "size": stat.st_size,
                        "mtime": int(stat.st_mtime),
                    }
                )
                if len(records) % 200 == 0:
                    report()
        except OSError as error:
            # 权限不足 / 路径过长 / 文件在扫描途中被删——记一笔，继续走
            notes["errors"].append(f"{entry.path}：{error.strerror or error}")

    report()


def pdf_page_count(path):
    """PDF 的页数，失败返回 `None`。

    **按需调用**（详情面板选中 PDF 时），不在索引时批量跑：为了一个显示用的分母
    去打开资料库里每一个 PDF，是把索引成本从"读目录项"抬成"解析文件结构"。

    PyMuPDF 是可选依赖（`requirements.txt` 里有，但装了才生效）：缺了就让阅读进度
    退化成"看到第 N 页"，不弹错、不影响索引。新版把包名从 `fitz` 改成了 `pymupdf`，
    两个名字都试一遍——只写 `fitz` 会在新版本上打一条弃用警告，只写 `pymupdf`
    会在旧版本上直接 ImportError。
    """
    try:
        import pymupdf  # 新版包名
    except ImportError:
        try:
            import fitz as pymupdf  # 旧版包名
        except ImportError:
            return None
    try:
        with pymupdf.open(path) as document:
            return document.page_count or None
    except Exception:  # noqa: BLE001 —— 损坏/加密的 PDF 不该让详情面板崩掉
        return None


# ======================================================================
# 删除（**本模块唯一的写操作**，见下）
# ======================================================================

# SHFileOperationW 的常量。用它的理由只有一个：`FOF_ALLOWUNDO` 能把文件送进
# **回收站**而不是直接抹掉——删资料是不可逆动作里最重的一种，能撤销是底线。
FO_DELETE = 3
FOF_SILENT = 0x0004
FOF_NOCONFIRMATION = 0x0010
FOF_ALLOWUNDO = 0x0040
FOF_NOERRORUI = 0x0400


class _SHFILEOPSTRUCTW(ctypes.Structure):
    """Win32 的 `SHFILEOPSTRUCTW`。

    别改成 `_pack_ = 1`：`fFlags` 是 WORD（2 字节），后面那个 BOOL 要按 4 字节对齐，
    C 编译器会插 2 字节填充，ctypes 的默认对齐正好与之一致。压紧反而错位。
    """

    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("wFunc", wintypes.UINT),
        ("pFrom", wintypes.LPCWSTR),
        ("pTo", wintypes.LPCWSTR),
        ("fFlags", ctypes.c_uint16),
        ("fAnyOperationsAborted", wintypes.BOOL),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", wintypes.LPCWSTR),
    ]


def delete_file(path):
    """把文件送进系统回收站。返回 `(是否成功, 说明)`，**不抛异常**。

    为什么走回收站而不是 `os.remove`：`PRODUCT_SPEC` 的原则 P6 是"资料文件只读"，
    这一页的删除是它唯一的例外（4.4 的 `〔v1.9 实施裁定〕`）。例外要站得住，就得让
    误点可撤销——回收站是这里唯一能提供的"撤销"。

    `pFrom` 必须是**双 null 结尾**的字符串：Win32 的多字符串约定，少一个 null 会把
    后面的内存当成第二个文件名，行为不可预期。

    非 Windows 不做替代实现：Linux/macOS 的"回收站"是各桌面环境各自的规范，随便挑
    一个实现等于假装成功。这里如实返回失败，让调用方提示用户去文件管理器删。
    """
    if not sys.platform.startswith("win"):
        return False, "当前系统不支持在工具内送回收站，请在文件管理器里删除"

    target = os.path.abspath(str(path))
    if not os.path.exists(target):
        return False, "文件已经不在这个位置了"
    if not os.path.isfile(target):
        # 索引里只会有文件，但这里挡一道：`SHFileOperation` 对目录是**整棵删**，
        # 一个手滑传进来的文件夹就是一次不可挽回的事故，而它并不需要这个能力
        return False, "只能删除文件，不能删除文件夹"

    operation = _SHFILEOPSTRUCTW()
    operation.wFunc = FO_DELETE
    operation.pFrom = target + "\0\0"
    operation.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI

    try:
        result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(operation))
    except OSError as error:   # noqa: BLE001 —— 调用本身失败也别让页面崩掉
        return False, f"调用系统接口失败：{error}"

    if result != 0:
        return False, f"系统返回错误码 {result}"
    if operation.fAnyOperationsAborted:
        return False, "操作被系统中断"
    if os.path.exists(target):
        # 返回 0 但文件还在：少见（文件被别的进程占着），如实报告而不是假装删掉了
        return False, "文件仍存在，可能正被其他程序占用"
    return True, ""


class ScanWorker(QThread):
    """在线程里跑 `scan_material`。

    信号只传**数据**，不传数据库：见模块开头的单连接说明。
    """

    progressed = Signal(int)
    scanned = Signal(object, object)  # records, notes（名字避开 QThread 自带的 finished）

    def __init__(self, root, parent=None):
        super().__init__(parent)
        self.root = root
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def is_cancelled(self):
        return self._cancelled

    def run(self):
        records, notes = scan_material(
            self.root, progress=self.progressed.emit, should_cancel=self.is_cancelled
        )
        self.scanned.emit(records, notes)