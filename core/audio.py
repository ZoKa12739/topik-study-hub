"""P2 音频库的**文件系统一侧**（`PRODUCT_SPEC` 4.3 / D15 方案 A）。

## 它与 `core/library.py` 的分工

| | `core/library.py`（P3） | `core/audio.py`（P2） |
|---|---|---|
| 面向 | **用户的**资料目录 | **工具自己的**音频库（`data/audio/`） |
| 权限 | 只读——绝不改动用户资料 | **写**——把用户选中的文件复制进来 |
| 产出 | 索引记录 | 复制结果 + 内容哈希 |

两者都**不碰 sqlite**：落库由调用方在主线程做。理由与 P3 相同——本项目是单连接贯穿
六个页面，`sqlite3` 默认 `check_same_thread=True`。

## 为什么是"复制"而不是"引用"（D15 方案 A）

用户说的是"把录音直接传到软件里"，复制是唯一与这句话对应的动作；引用方案的曲目会
因为源文件一移动就失效。代价是磁盘空间，所以**必须把代价告知用户**——本模块提供
`disk_report()`，视图在导入后把"这次复制了多少、音频库现在多大"说出来。

**源文件永远是只读的。** 本模块只读源文件，只写音频库目录，从不移动、改名或删除
用户的任何文件（`PRODUCT_SPEC` 1.2 铁律）。`copy_into_library` 唯一会删的东西是
它自己刚写的 `.part` 临时文件。
"""

import hashlib
import os
import shutil

from core.config import AUDIO_DIR, normalize_path

# 能被导入的音频格式。与 ffmpeg 转换产出的 `.mp3` 一起构成音频库的全部内容。
AUDIO_EXTENSIONS = (".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac", ".wma")

# 文件选择器的过滤器字符串（`QFileDialog` 用），从同一个来源生成，不另抄一份
AUDIO_FILTER = "音频 (" + " ".join(f"*{ext}" for ext in AUDIO_EXTENSIONS) + ");;所有文件 (*)"

VIDEO_EXTENSIONS = (".mkv", ".mp4", ".mov", ".avi", ".flv")
VIDEO_FILTER = "视频 (" + " ".join(f"*{ext}" for ext in VIDEO_EXTENSIONS) + ");;所有文件 (*)"


class AudioImportError(RuntimeError):
    """导入失败。消息是**面向用户的中文**——视图会直接把它放进 `Banner`，
    所以这里不写堆栈式的措辞，只写"哪个文件、出了什么事、还剩多少空间"。"""


def human_size(num_bytes):
    """人类可读的大小。D15 要求把"音频库占了多少"说清楚，说清楚的前提是能读。"""
    size = float(num_bytes or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024


def content_hash(path, chunk_size=1024 * 1024):
    """文件内容的 sha256（十六进制）。读不到就返回 `None`。

    **分块读**：一段两小时的录音有几百 MB，整个读进内存只是为了算哈希，
    在弱机上会直接把工具顶到卡死。

    哈希是 D15 去重的唯一依据，也是 6.2 说的"重建音频库时把文本关联回来"的钥匙——
    所以它宁可返回 `None`（调用方退化成"照常复制一份"）也不抛异常。
    """
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            while True:
                block = handle.read(chunk_size)
                if not block:
                    break
                digest.update(block)
    except OSError:
        return None
    return digest.hexdigest()


def library_path(relative, destination_dir=None):
    """音频库内的相对路径 → 绝对路径。存相对路径的理由见 `add_audio_track`。"""
    return os.path.join(str(destination_dir or AUDIO_DIR), str(relative))


def exists_in_library(relative, destination_dir=None):
    return bool(relative) and os.path.isfile(library_path(relative, destination_dir))


def disk_report(source_size, destination_dir=None):
    """给用户看的代价账单：这次要写多少、还剩多少、音频库现在多大。"""
    target = str(destination_dir or AUDIO_DIR)
    os.makedirs(target, exist_ok=True)
    usage = shutil.disk_usage(target)
    library_bytes = sum(
        os.path.getsize(os.path.join(target, name))
        for name in os.listdir(target)
        if os.path.isfile(os.path.join(target, name))
    )
    return {
        "free": usage.free,
        "required": int(source_size or 0),
        "library_bytes": library_bytes,
    }


def unique_name(title, destination_dir):
    """库里重名时加 ` (2)` / ` (3)`……**不覆盖**已有文件。

    取名字而不是取 `content_hash` 做文件名，是因为用户会在文件管理器里翻这个目录；
    一串十六进制对人是零信息。
    """
    stem, extension = os.path.splitext(title)
    candidate, index = title, 2
    while os.path.exists(os.path.join(destination_dir, candidate)):
        candidate = f"{stem} ({index}){extension}"
        index += 1
    return candidate


def copy_into_library(source, destination_dir=None):
    """把 `source` 复制进音频库，返回记录字典。**源文件只读，不动它。**

    返回 `{path, title, size, absolute}`；`path` 是库内相对路径（`normalize_path`
    过的形态，与 P3 用同一套路径纪律）。

    三条失败路径都在**动手之前**判掉或收干净：

    * 格式不支持 → `AudioImportError`，一个字节都不写
    * 空间不足 → 复制前就拒绝（留 5% 余量），不留半个文件
    * 复制中途出错 → 写的是 `.part`，异常路径上把它删掉

    有了 `.part`，音频库目录里就永远不会出现"看起来是音频、其实是半截"的文件——
    那种文件会被下一次索引当成真曲目，然后播放到一半失败。
    """
    target_dir = str(destination_dir or AUDIO_DIR)
    os.makedirs(target_dir, exist_ok=True)

    source = str(source)
    title = os.path.basename(source)
    extension = os.path.splitext(title)[1].lower()
    if extension not in AUDIO_EXTENSIONS:
        raise AudioImportError(
            f"〔{title}〕不是支持的音频格式（支持 {'、'.join(AUDIO_EXTENSIONS)}）"
        )

    try:
        size = os.path.getsize(source)
    except OSError as error:
        raise AudioImportError(f"〔{title}〕读不到：{error.strerror or error}") from error

    report = disk_report(size, target_dir)
    if report["free"] < size * 1.05:
        raise AudioImportError(
            f"空间不足，无法复制〔{title}〕。需要 {human_size(size)}，"
            f"磁盘剩余 {human_size(report['free'])}。"
        )

    name = unique_name(title, target_dir)
    absolute = os.path.join(target_dir, name)
    partial = absolute + ".part"
    try:
        shutil.copyfile(source, partial)
        os.replace(partial, absolute)   # 同目录改名是原子的：要么没有，要么完整
    except OSError as error:
        _remove_quietly(partial)
        raise AudioImportError(f"复制〔{title}〕失败：{error.strerror or error}") from error

    return {
        "path": normalize_path(name),
        "title": name,
        "size": size,
        "absolute": absolute,
    }


def _remove_quietly(path):
    try:
        os.remove(path)
    except OSError:
        pass


def delete_from_library(relative, destination_dir=None):
    """把文件从音频库移走（删曲目时由调用方决定要不要连文件一起清）。

    只允许删**音频库内部**的路径：`relative` 逃出库目录时直接拒绝。曲线的
    `..\\..` 会把源文件删掉，而"绝不删用户文件"是本项目的铁律。
    """
    target_dir = os.path.abspath(str(destination_dir or AUDIO_DIR))
    absolute = os.path.abspath(library_path(relative, target_dir))
    if os.path.commonpath([target_dir, absolute]) != target_dir:
        raise AudioImportError("拒绝删除音频库之外的路径")
    _remove_quietly(absolute)