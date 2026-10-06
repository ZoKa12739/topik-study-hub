"""字体加载。

从 `design/Pretendard-1.3.9/` 加载 Pretendard 并注册到 Qt。

**为什么必须配中文回退字体：**
Pretendard 是韩文/拉丁字体，**不含简体中文字形**。实测结果（QRawFont 逐字查字形索引）：

| 字符集 | 覆盖 |
|---|---|
| 韩文 | 全覆盖 |
| 拉丁 | 全覆盖 |
| 日文假名 | 全覆盖 |
| 中文标点（，。、；：（）「」） | 全覆盖 |
| **简体中文（成功载入个单词学习资料）** | **缺 11/11** |
| **韩文汉字（韓國語工夫）** | **缺 5/5** |

因此界面中文必须由系统字体承担。这与本项目既有脚本的做法一致——
`单词/单词表转文本PDF工具.py` 第 62 行：
    # 中文字体：微软雅黑（Pretendard 不含简体中文字形，故中文列仍用雅黑）

**字体栈顺序即上述策略：** Pretendard → 微软雅黑 → sans-serif。
韩文与拉丁落在 Pretendard，汉字回退到雅黑。

字体许可：SIL Open Font License 1.1（见 `design/Pretendard-1.3.9/LICENSE.txt`），
允许随软件分发。

调用时机：**必须在 QApplication 创建之后**调用 `register_fonts()`。

**为什么用 addApplicationFontFromData 而不是 addApplicationFont(path)：**
`QFontDatabase.addApplicationFont()` 传路径时，**路径含非 ASCII 字符会返回 -1**。
本项目根目录的各级目录名含中文与韩文，
实测四种组合：

| 路径 | 结果 |
|---|---|
| 相对路径（纯 ASCII） | ✓ 注册成功 |
| **绝对路径（含中文/韩文）** | **✗ 返回 -1** |
| 绝对路径（纯 ASCII） | ✓ 注册成功 |
| `addApplicationFontFromData` | ✓ 注册成功 |

所以改成用 Python 读取字节（Python 的文件 IO 正确处理 Unicode 路径），
Qt 只接收数据，不碰路径。

**这个约束的范围：** 实测它**只影响 `QFontDatabase.addApplicationFont`**。
QSS 里的 `image: url(<含中文的绝对路径>)` 是**正常工作的**——复选框对勾
（`ui/assets/check.svg`）在同样的非 ASCII 路径下能正确渲染（逐像素验证过）。
不要把这个限制过度推广到 Qt 的其他按路径加载入口。
"""

from pathlib import Path

from PySide6.QtCore import QByteArray
from PySide6.QtGui import QFont, QFontDatabase

from core.config import PROJECT_ROOT

# 只加载用到的 4 个字重（对应 QSS 里的 400 / 500 / 600 / 700）
_FONT_DIR = PROJECT_ROOT / "design" / "Pretendard-1.3.9" / "public" / "static"
_FILES = (
    "Pretendard-Regular.otf",
    "Pretendard-Medium.otf",
    "Pretendard-SemiBold.otf",
    "Pretendard-Bold.otf",
)

# 界面字体栈。中文必须排在 Pretendard 之后回退，见模块开头说明。
FONT_STACK = '"Pretendard", "Microsoft YaHei UI", "Microsoft YaHei", sans-serif'

# 全局基准字号（pt）。QSS 里逐项的字号是在此基础上的细化。
BASE_POINT_SIZE = 10.5

_loaded_families: list = []


def register_fonts() -> list:
    """把 Pretendard 注册进 Qt 字体库，返回实际注册到的族名。

    重复调用是安全的（Qt 内部会去重）。返回空列表说明字体目录缺失——
    此时应用仍可运行，只是韩文会回退到系统字体。
    """
    global _loaded_families
    if _loaded_families:
        return _loaded_families

    families = []
    for name in _FILES:
        path = _FONT_DIR / name
        if not path.exists():
            continue
        # 不能用 addApplicationFont(path)：路径含中文/韩文时返回 -1（见模块开头）
        font_id = QFontDatabase.addApplicationFontFromData(QByteArray(path.read_bytes()))
        if font_id >= 0:
            families.extend(QFontDatabase.applicationFontFamilies(font_id))

    # 去重：4 个字重注册后族名都是 "Pretendard"
    _loaded_families = sorted(set(families))
    return _loaded_families


def apply_application_font(app) -> None:
    """设置应用级基准字体。

    这条设置覆盖 QSS 未指定 font-size 的控件（导航项、表格单元格、
    列表项等），否则它们会用 Qt 默认字号，比界面其余部分小一截。
    """
    font = QFont()
    font.setFamilies(["Pretendard", "Microsoft YaHei UI", "Microsoft YaHei"])
    font.setPointSizeF(BASE_POINT_SIZE)
    app.setFont(font)


def diagnostic() -> str:
    """字体加载状态，便于自查。"""
    return (
        f"Pretendard 目录: {_FONT_DIR}\n"
        f"目录存在: {_FONT_DIR.exists()}\n"
        f"已注册族: {_loaded_families or '（未加载）'}\n"
        f"字体栈: {FONT_STACK}"
    )