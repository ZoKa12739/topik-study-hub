"""运行时样式切换。

Qt 不会因为 `setObjectName()` 自动重算样式表——必须先 unpolish 再 polish，
否则新 objectName 对应的 QSS 规则不会生效。这个坑不写注释很容易踩第二次。
"""

# 语义状态 → objectName（对应 ui/theme.py 底部的语义色规则）
STATE_OBJECT_NAMES = {
    "success": "stateSuccess",
    "warning": "stateWarning",
    "danger": "stateDanger",
    "info": "stateInfo",
}


def restyle(widget, object_name):
    """切换 objectName 并立即重新应用样式表。"""
    widget.setObjectName(object_name)
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def set_state(widget, state):
    """把标签切到某个语义状态色。

    state: 'success' / 'warning' / 'danger' / 'info'，或 None 表示退回次要文字色。

    注意（DESIGN.md §3.1 纪律 3）：**颜色不得单独承载语义**，
    调用方必须保证该标签的文字本身已说明发生了什么。
    """
    restyle(widget, STATE_OBJECT_NAMES[state] if state else "muted")