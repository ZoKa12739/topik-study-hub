"""动效令牌：内容层动效的唯一参数出处。

覆盖两类东西：

1. **过词/专攻卡的换词编排**（`VocabView._play_entrance`）：序号、大字、
   释义三个内容叶子的错峰淡入落位。
2. **Toast 出入场时长**（`components.Toast`）。

不覆盖 `ShadowingView` 的播放列表抽屉——那是容器位移，另一套约束
（定宽 + 内部容器 `pos`），常量和理由都在 `shadowing_view.py` 里。

三条约定（与 `CLAUDE.md` 微动效黄金法则同源）：

* **只动内容，不动 chrome**。卡片外框、按钮、进度条永远静止；
  动的只有序号 / 大字 / 释义三个叶子。静止的 chrome 是"锚"，
  约 180ms 的动效有了它才不显吵。
* **透明度先行 + 小位移**。不用 scale、旋转、回弹（`OutBack`）、
  弹性曲线——那些才叫张狂；`OutCubic` 快进慢停（像落位）就够了。
* **总量压在 200ms 内**。>300ms 在过词这种高频动作上发黏，
  <60ms 像闪帧。连按时后一次从静止点重播，**输入永远不等动画**。
"""

from PySide6.QtCore import QEasingCurve

# ---- 时长（ms）----
DUR_MICRO = 100   # 序号等元信息：最短，第一个落位
DUR_SOFT = 130    # 释义：跟随大字收尾
DUR_BASE = 150    # 大字：视觉主角
DUR_REVEAL = 180  # Space 揭示释义：用户主动 paced，值得稍隆重

# ---- Toast（顶部轻提示）：入场短，退场略长且加速，退场不催 ----
DUR_TOAST_IN = 140
DUR_TOAST_OUT = 200

# ---- 错峰延迟（ms）：制造"依次落位"的层次，而不是整块一起蹦 ----
DELAY_WORD = 20
DELAY_MEANING = 50

# ---- 位移（px）：小到不抢戏，大到读得出方向 ----
DX_SEQ = 8
DX_WORD = 18
DX_MEANING = 12
DY_REVEAL = 8     # 揭示时释义的上移量（向上为负）

# ---- 曲线 ----
EASE_ENTER = QEasingCurve.OutCubic  # 快进慢停：像落位
EASE_EXIT = QEasingCurve.InCubic    # 加速离场，不拖尾
