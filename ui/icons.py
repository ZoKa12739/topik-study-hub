"""单色线性图标（D-3）。

用法：
    from ui.icons import icon
    button.setIcon(icon("folder"))
    item.setIcon(nav_icon("calendar"))
    item.setIcon(icon("star", TEXT_COLORS["muted"], 16))   # 要指定色时从 theme 取

设计约束（见 `design/DESIGN.md` §1 L9 / §3.5）：
  * 全部 16px 线性图标，24×24 viewBox，圆头圆角接合
  * 单色。颜色由调用方传入，默认 `DEFAULT_COLOR`（text-secondary）
  * **不使用 emoji**——emoji 天然彩色、尺寸不一，是"低饱和克制"最直接的破坏者

为什么是内联 SVG 字符串而不是 `ui/icons/*.svg` 资源文件：
  1. Qt 不支持 CSS 的 `currentColor`，重新着色只能靠字符串替换，内联最直接
  2. 避免运行时资源路径解析——本项目已被硬编码路径坑过一次（见 PRODUCT_SPEC D10）
  3. 30 个小图标集中一处，便于审计与统一调整

图标为手绘的通用线性图形，未引入第三方素材，因此无授权问题。
"""

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from ui.theme import TEXT_COLORS

# 默认色对齐 design/DESIGN.md §3.1 的 text-secondary。
# 取值从 `ui/theme.py` 取，不在此处写字面量——浅色主题下这里曾漏改
# （沿用深色主题的 #8A8F98 / #A0A4AB），图标在浅底上几乎看不见。
DEFAULT_COLOR = TEXT_COLORS["secondary"]
SELECTED_COLOR = TEXT_COLORS["primary"]

# 描边宽度。浅色主题下从 2 调到 2.25：
# 深色笔画落在浅底上，视觉上比浅色笔画落在深底上更细（后者会轻微"发光"变粗）。
# 这是补偿感知差异，不是随意调大。
STROKE_WIDTH = 2.25

_SVG = {
    # ---- 导航 ----
    "calendar": '<rect x="3" y="5" width="18" height="16" rx="2"/>'
                '<path d="M3 10h18M8 3v4M16 3v4"/>',
    "book": '<path d="M4 4.5A2.5 2.5 0 0 1 6.5 2H20v18H6.5A2.5 2.5 0 0 0 4 22z"/>'
            '<path d="M4 17.5A2.5 2.5 0 0 1 6.5 15H20"/>',
    "headphones": '<path d="M4 15v-3a8 8 0 0 1 16 0v3"/>'
                  '<rect x="2.5" y="14" width="4.5" height="7" rx="1.5"/>'
                  '<rect x="17" y="14" width="4.5" height="7" rx="1.5"/>',
    "folder": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2.5h8a2 2 0 0 1 2 2V18a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "folder-open": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2.5h7a2 2 0 0 1 2 2V11"/>'
                   '<path d="M2.5 11h19l-2 8a2 2 0 0 1-2 1.5H5A2 2 0 0 1 3 19z"/>',
    "scissors": '<circle cx="6" cy="6" r="2.6"/><circle cx="6" cy="18" r="2.6"/>'
                '<path d="M20 4L8.1 15.9M14.5 14.5L20 20M8.1 8.1L12 12"/>',

    # ---- 动作 ----
    "search": '<circle cx="11" cy="11" r="7.5"/><path d="M16.5 16.5L21 21"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "check": '<path d="M20 6.5L9.5 17 4 11.5"/>',
    "check-circle": '<circle cx="12" cy="12" r="9"/><path d="M8.3 12.3l2.6 2.6 4.8-5.2"/>',
    "x": '<path d="M18 6L6 18M6 6l12 12"/>',
    "x-circle": '<circle cx="12" cy="12" r="9"/><path d="M14.8 9.2l-5.6 5.6M9.2 9.2l5.6 5.6"/>',
    "trash": '<path d="M3.5 6.5h17M9.5 6.5V4.5A1.5 1.5 0 0 1 11 3h2a1.5 1.5 0 0 1 1.5 1.5v2"/>'
             '<path d="M18.5 6.5l-.8 12.6A1.5 1.5 0 0 1 16.2 20.5H7.8A1.5 1.5 0 0 1 6.3 19.1L5.5 6.5"/>',
    "refresh": '<path d="M20.5 12a8.5 8.5 0 1 1-2.4-5.9"/><path d="M20.5 4v4.5H16"/>',
    "alert": '<path d="M10.3 3.9L1.9 18a2 2 0 0 0 1.7 3h16.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/>'
             '<path d="M12 9.5v4M12 17.2v.1"/>',
    "upload": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>'
              '<path d="M7.5 8.5L12 4l4.5 4.5M12 4v11"/>',
    "download": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>'
                '<path d="M7.5 11L12 15.5 16.5 11M12 3.5v12"/>',
    "clipboard": '<path d="M15 4.5h2A2 2 0 0 1 19 6.5v13a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2v-13a2 2 0 0 1 2-2h2"/>'
                 '<rect x="8.5" y="2.5" width="7" height="4" rx="1.2"/>',
    "settings": '<path d="M4 21v-6M4 11V3M12 21v-9M12 8V3M20 21v-4M20 13V3"/>'
                '<path d="M1.5 15h5M9.5 8h5M17.5 17h5"/>',
    "note": '<path d="M12 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h9l5-5V7"/>'
            '<path d="M20 7l-4-4v5h4"/>',

    # ---- 文件类型（P3 资料库） ----
    "file-text": '<path d="M14 2.5H6.5A1.5 1.5 0 0 0 5 4v16a1.5 1.5 0 0 0 1.5 1.5h11A1.5 1.5 0 0 0 19 20V7.5z"/>'
                 '<path d="M14 2.5V7.5H19"/><path d="M8.5 12.5h7M8.5 16h5"/>',
    "image": '<rect x="3" y="4" width="18" height="16" rx="2"/>'
             '<circle cx="8.5" cy="9.5" r="1.5"/><path d="M21 15.5l-4.5-4L6 20"/>',
    "music": '<path d="M9 18V5.5l11-2V16"/><circle cx="6" cy="18" r="3"/><circle cx="17" cy="16" r="3"/>',
    "film": '<rect x="2.5" y="4" width="19" height="16" rx="2"/>'
            '<path d="M7.5 4v16M16.5 4v16M2.5 12h19"/>',
    "presentation": '<rect x="3" y="4" width="18" height="11" rx="1.5"/><path d="M12 15v5M8.5 20h7"/>',

    # ---- 播放器（P2） ----
    "play": '<path d="M7 4.5l12 7.5-12 7.5z"/>',
    "pause": '<path d="M9 4.5v15M15 4.5v15"/>',
    "skip-back": '<path d="M18.5 20L8.5 12l10-8z"/><path d="M5 19V5"/>',
    "skip-forward": '<path d="M5.5 4L15.5 12l-10 8z"/><path d="M19 5v14"/>',
    "repeat": '<path d="M17 2.5L20.5 6 17 9.5"/><path d="M3.5 11.5V10a4 4 0 0 1 4-4h13"/>'
              '<path d="M7 21.5L3.5 18 7 14.5"/><path d="M20.5 12.5V14a4 4 0 0 1-4 4h-13"/>',
    # 折叠/展开用（P2 的播放列表可折叠，D16）
    "chevron-left": '<path d="M14.5 6L8.5 12l6 6"/>',
    "chevron-right": '<path d="M9.5 6l6 6-6 6"/>',

    # ---- 星标与发音 ----
    "star": '<path d="M12 2.6l2.9 5.9 6.5.95-4.7 4.6 1.1 6.45L12 17.45 6.2 20.5l1.1-6.45-4.7-4.6 6.5-.95z"/>',
    "star-filled": '<path d="M12 2.6l2.9 5.9 6.5.95-4.7 4.6 1.1 6.45L12 17.45 6.2 20.5l1.1-6.45-4.7-4.6 6.5-.95z"'
                   ' fill="{c}" stroke="{c}"/>',
    "volume": '<path d="M11 5L6 9H2v6h4l5 4V5z"/>'
              '<path d="M15.5 8.5a5 5 0 0 1 0 7"/>'
              '<path d="M19 5a10 10 0 0 1 0 14"/>',
}

_TEMPLATE = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" '
    'fill="none" stroke="{c}" stroke-width="' + str(STROKE_WIDTH) + '" '
    'stroke-linecap="round" stroke-linejoin="round">'
    "{body}</svg>"
)

_cache: dict = {}


def available() -> list:
    """已定义的图标名。"""
    return sorted(_SVG)


def _render(name, color, size):
    body = _SVG[name].replace("{c}", color)
    svg = _TEMPLATE.format(c=color, body=body)
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    if not renderer.isValid():
        raise ValueError(f"icon {name!r} 生成的 SVG 无效")
    # 同时提供 1x / 2x 位图，交给 Qt 按屏幕缩放比挑选
    pixmaps = []
    for px in {size, size * 2}:
        pm = QPixmap(px, px)
        pm.fill(Qt.transparent)
        painter = QPainter(pm)
        renderer.render(painter)
        painter.end()
        pixmaps.append(pm)
    return pixmaps


def icon(name, color=DEFAULT_COLOR, size=16, selected_color=None) -> QIcon:
    """取一个单色线性图标。

    name            : `available()` 中的名字
    color           : 描边色，默认 text-muted
    size            : 逻辑像素尺寸，默认 16
    selected_color  : 选中态颜色（用于 QListWidget 导航项），省略则与 color 相同
    """
    if name not in _SVG:
        raise KeyError(
            f"未定义的图标 {name!r}；可用：{', '.join(available())}"
        )
    key = (name, color, size, selected_color)
    cached = _cache.get(key)
    if cached is not None:
        return cached

    result = QIcon()
    for pm in _render(name, color, size):
        result.addPixmap(pm, QIcon.Mode.Normal, QIcon.State.Off)

    if selected_color and selected_color != color:
        for pm in _render(name, selected_color, size):
            result.addPixmap(pm, QIcon.Mode.Selected, QIcon.State.Off)
            result.addPixmap(pm, QIcon.Mode.Active, QIcon.State.Off)
            result.addPixmap(pm, QIcon.Mode.Normal, QIcon.State.On)
            result.addPixmap(pm, QIcon.Mode.Active, QIcon.State.On)

    _cache[key] = result
    return result


def nav_icon(name, size=16) -> QIcon:
    """侧边栏导航项图标：未选中 text-secondary，选中 text-primary。"""
    return icon(name, DEFAULT_COLOR, size, selected_color=SELECTED_COLOR)