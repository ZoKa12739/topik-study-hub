"""P4 知识碎片的**文件系统侧**（`PRODUCT_SPEC` 4.5）。

## 碎片有两个来源，合成同一份清单

1. **资料库里的图片**——写作 / 听力 / 阅读 / 单词 下的 `.png` / `.jpg`。它们**已经在
   P3 的 `materials` 索引里**，所以本模块直接读索引快照，绝不自己走一遍目录树。
2. **收集进来的图片**——粘贴与导入都把文件复制进 `data/snippets/`（第 11 章 Q2 的
   决策："资料文件夹应保持用户自己的东西"）。这个目录是本工具自己写的，条目在百级
   以内，直接 `os.scandir` 一遍最省事。

## 为什么不 `os.walk` 找资料库里的图片

原实现每次进页面、每次启动都递归扫一遍整个资料根目录。4.5 的边界条款把它点名为已
知缺陷——"资料多时打开工具即卡"。走 P3 的持久化索引则没有这个问题：索引本来就是
"哪些图片在哪儿、文件还在不在"的答案（`materials.missing`），再扫一遍是把同一份事实
重新算一次，而且这次是在 UI 线程的入口上。

## 这里产出的全是第一层

路径、名字、来源、文件还在不在——都能从文件系统重建。用户在碎片上写的标题、说明、
标签是第二层，只存在数据库里，本模块一个字段都不碰。`collect_records` 因此还需要
一份"库里已经有什么"（`existing`），目的是**只产出真正变化了的那几行**，让每次刷新
不必把几百行 UPDATE 重放一遍。
"""

import os
import shutil
from datetime import datetime

from core.config import SNIPPETS_DIR, normalize_path

# 认作碎片的扩展名。Qt 的图片插件对这几个都能解码；gif 收进来是因为资料目录里真有
# 动图截图。
IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"})

# 收集目录里的文件在「来源」下拉里的名字。粘贴与导入落在同一个目录、之后的处理完全
# 一致，因此不按"怎么进来的"分两类——那个区分**重建不出来**（文件一旦落盘，路径里
# 没有任何关于来源的痕迹），写进库里就是一条会失真的字段。
COLLECTED_SOURCE = "收集"


def is_image(name):
    return os.path.splitext(name)[1].lower() in IMAGE_EXTS


def collect_records(material_rows, existing, snippets_dir=SNIPPETS_DIR):
    """合成一份碎片清单（第一层）。返回 `[{path, name, source_subject, missing}]`。

    * `material_rows` —— `StudyDatabase.library_snapshot()` 的结果。**只读，不触发扫描。**
    * `existing`      —— `{path: {"source_subject":…, "missing":…}}`，库里已有的碎片。
    * 只返回**新增的、或来源/存在性变了的**行；没变化的一律不返回，于是刷新时写库的
      工作量与实际变化量成正比，而不是与碎片总数成正比。
    """
    records = []
    seen_in_materials = set()

    for row in material_rows:
        if not is_image(row["name"]):
            continue
        path = row["path"]
        seen_in_materials.add(path)
        subject = row.get("subject") or ""
        missing = bool(row.get("missing"))
        known = existing.get(path)
        if known is not None and known["source_subject"] == subject and bool(known["missing"]) == missing:
            continue
        records.append(
            {"path": path, "name": row["name"], "source_subject": subject, "missing": missing}
        )

    # 收集目录：整目录 scandir 一遍（百级条目，比查库还快），顺便找出"库里记着、
    # 文件已经不在了"的那些。收集进来的碎片不在 P3 索引里，missing 只能这样得到。
    live = set()
    directory = str(snippets_dir)
    try:
        entries = list(os.scandir(directory))
    except OSError:
        entries = []
    for entry in entries:
        try:
            if not entry.is_file() or not is_image(entry.name):
                continue
        except OSError:
            continue
        path = normalize_path(entry.path)
        live.add(path)
        known = existing.get(path)
        if known is not None and known["source_subject"] == COLLECTED_SOURCE and not known["missing"]:
            continue
        records.append(
            {
                "path": path,
                "name": entry.name,
                "source_subject": COLLECTED_SOURCE,
                "missing": False,
            }
        )

    for path, known in existing.items():
        if path in live or path in seen_in_materials:
            continue
        if _direct_child_of(path, directory):
            now_missing = True
            default_subject = COLLECTED_SOURCE
        else:
            # 不在当前资料库快照里的历史碎片（例如切换了资料根目录）：只要磁盘上原图片文件还在，
            # 就继续保留展示，绝不因切换根目录而消失；若曾被误标为 missing=1 也会在此自愈恢复。
            now_missing = not os.path.isfile(path)
            default_subject = ""
        if bool(known["missing"]) != now_missing:
            records.append(
                {
                    "path": path,
                    "name": os.path.basename(path),
                    "source_subject": known["source_subject"] or default_subject,
                    "missing": now_missing,
                }
            )

    return records


def _direct_child_of(path, directory):
    """`path` 是否**直接**位于 `directory` 里。

    判据是"父目录相等"，不是"路径以它开头"——后者在"目录名恰好是另一个路径的一段"
    时会误判（`core/library.py` 因为子串判断踩过一次）。收集目录是平铺的（本工具从不
    往里写子目录），而上面的 `scandir` 也不递归，两个口径必须一致，否则会有一批文件
    "扫不到、却也不算丢"。
    """
    try:
        return os.path.normcase(os.path.dirname(path)) == os.path.normcase(
            os.path.normpath(directory)
        )
    except (TypeError, ValueError):
        return False


def unique_destination(directory, file_name):
    """给一个不会覆盖已有文件的目标路径。

    粘贴的落点带时间戳（`snippet_20261006_193000.png`），同秒两次粘贴会撞名；
    导入的是原文件名，`PixPin_1.png` 撞名更是常态。撞上就加 `_2`、`_3`……
    **绝不覆盖**——那个文件可能是用户昨天导进来的另一个碎片。
    """
    stem, ext = os.path.splitext(file_name)
    candidate = os.path.join(str(directory), file_name)
    index = 2
    while os.path.exists(candidate):
        candidate = os.path.join(str(directory), f"{stem}_{index}{ext}")
        index += 1
    return candidate


def pasted_file_name(image_format="PNG", now=None):
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    return f"snippet_{stamp}.{image_format.lower()}"


def copy_image_into(source, snippets_dir=SNIPPETS_DIR):
    """把外部图片复制进碎片目录，返回新路径（或抛 `OSError`）。"""
    os.makedirs(str(snippets_dir), exist_ok=True)
    destination = unique_destination(snippets_dir, os.path.basename(source))
    shutil.copy2(source, destination)
    return normalize_path(destination)