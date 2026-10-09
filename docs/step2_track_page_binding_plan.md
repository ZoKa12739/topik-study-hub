# 影子跟读增强 · 第二步实施计划：曲目级页码关联（切题自动翻页）

## 1. 目标与背景 (Goal Description)

在影子跟读模块（P2 `ShadowingView`）中，现有的 PDF 页码记忆是绑定在整个播放列表维度（`playlists.kr_page` / `cn_page`）。用户在练习听力真题时，每个听力题段（例如“13~14题”）在原文试卷与中文解析手册中均有固定的对应页码。
**本任务目标**：支持为每个曲目（Track）在当前播放列表中显式绑定对应的「原文页码」与「解析页码」。在左侧切换曲目时，右侧两份 PDF 自动平滑跳转到该题的起始页，彻底免去手动来回翻页对齐的痛点。

---

## 2. 交互与行为规范 (UX Specifications)

1. **显式固定起始页心智**：
   - 翻页本身不自动覆盖曲目的起始页（避免用户临时翻看前后题时破坏本题位置）。
   - 用户翻到目标页后，通过明确的操作将当前页码绑定为本题起始页。
2. **两处绑定入口**：
   - **面板独立图钉**：在左（原文）右（解析）两个 `PdfPanel` 头部各增加一个「📌 绑定本页」小按钮（`btn_pin`），支持独立将当前所在页保存为当前曲目的对应页码。
   - **曲目右键菜单**：在左侧曲目列表（`track_list`）右键菜单中提供「将当前页码绑定为本题页码」，一次性同时将原文与解析当前页绑定给该曲目。
   - 绑定成功后弹出轻量 Toast 提示，如：`已将「64届 听力 13~14题」关联至 原文 P.5 / 解析 P.32`。
3. **切题自动跳转**：
   - 在左侧点击曲目、快捷键上一段/下一段（`Ctrl+Left` / `Ctrl+Right`）或自动切下一首时：
     - 若目标曲目配置了 `kr_page`，原文 PDF 自动跳转至该页；
     - 若目标曲目配置了 `cn_page`，解析 PDF 自动跳转至该页；
     - 若目标曲目未配置某项页码，则保持当前 PDF 位置不变，不强制重置。
4. **曲目列表视觉提示（低对比轻量呈现）**：
   - 在曲目项右侧或副标题中，以弱化灰色文字显示绑定的页码标签（如 `P.5 / P.32`），一目了然。

---

## 3. 详细改动方案 (Proposed Changes)

### 3.1 核心数据层：`core/database.py`

#### [MODIFY] `core/database.py`

1. **Schema 迁移（增量无损）**：
   在 `_migrate()` 方法中检查并为 `playlist_items` 添加 `kr_page` 和 `cn_page` 字段：
   ```python
   # playlist_items 增加 kr_page, cn_page
   cols = {row["name"] for row in self.connection.execute("PRAGMA table_info(playlist_items)").fetchall()}
   if "kr_page" not in cols:
       self.connection.execute("ALTER TABLE playlist_items ADD COLUMN kr_page INTEGER")
   if "cn_page" not in cols:
       self.connection.execute("ALTER TABLE playlist_items ADD COLUMN cn_page INTEGER")
   ```

2. **读取查询更新**：
   修改 `playlist_items(self, playlist_id)` 查询语句，包含 `i.kr_page` 与 `i.cn_page`：
   ```sql
   SELECT t.id AS track_id, t.path, t.title, t.duration_ms, t.size,
          t.content_hash, t.kr_text, t.cn_text,
          i.sort_order, i.kr_page, i.cn_page,
          g.position_ms, g.pos_a_ms, g.pos_b_ms, g.speed, g.status,
          g.listened_ms, g.loop_count
   FROM playlist_items i
   JOIN audio_tracks t ON t.id = i.track_id
   LEFT JOIN track_progress g ON g.track_id = t.id
   WHERE i.playlist_id = ?
   ORDER BY i.sort_order, t.id
   ```

3. **新增持久化接口**：
   ```python
   def set_playlist_item_pages(self, playlist_id: int, track_id: int, kr_page="keep", cn_page="keep") -> None:
       """记录播放列表中某首曲目绑定的 PDF 页码。
       
       使用 'keep' 标记未变更项，传入整数更新页码，传入 None 清除绑定。
       """
       if playlist_id is None or track_id is None:
           return
       updates = []
       params = []
       if kr_page != "keep":
           updates.append("kr_page = ?")
           params.append(int(kr_page) if kr_page is not None else None)
       if cn_page != "keep":
           updates.append("cn_page = ?")
           params.append(int(cn_page) if cn_page is not None else None)
       if not updates:
           return
       params.extend([playlist_id, track_id])
       with self.connection:
           self.connection.execute(
               f"UPDATE playlist_items SET {', '.join(updates)} WHERE playlist_id = ? AND track_id = ?",
               tuple(params),
           )
   ```

---

### 3.2 界面交互层：`ui/shadowing_view.py`

#### [MODIFY] `ui/shadowing_view.py`

1. **`PdfPanel` 类增加绑定按钮与信号**：
   - 增加信号：`pin_requested = Signal()`
   - 在 `head_wrap`（`PdfPanel.__init__`）的页码标签右侧添加 `self.btn_pin = QPushButton()`：
     - 图标：`icon("pin")`（若图标库无 pin 则用书签 `bookmark` 或文本 `📌`）
     - 对象名：`"iconButton"`
     - 工具提示：`f"将当前页设为当前曲目的{heading}起始页"`
     - 点击连接：`self.btn_pin.clicked.connect(self.pin_requested.emit)`
   - 新增页面跳转方法：
     ```python
     def jump_to_page(self, page_num: int):
         """安全翻到指定页（1-indexed）。"""
         if self.document.pageCount() <= 0:
             return
         target = max(1, min(page_num, self.document.pageCount()))
         self.view.pageNavigator().jump(target - 1, QPointF(), 0)
     ```

2. **`ShadowingView` 连接面板图钉信号**：
   - 在 `_build_workspace()` 中：
     ```python
     self.kr_pdf.pin_requested.connect(lambda: self._pin_active_track_page("kr"))
     self.cn_pdf.pin_requested.connect(lambda: self._pin_active_track_page("cn"))
     ```
   - 实现 `_pin_active_track_page(which)`：
     - 校验 `self.playlist_id` 与 `self.track_id`。
     - 若未选曲目，提示 `请先在左侧选择一首曲目`。
     - 获取当前页码：`panel.current_page()`。
     - 调用 `self.database.set_playlist_item_pages(...)`。
     - 刷新当前内存中的 item 数据，弹出 Toast 提示：`已将「{track_title}」关联至 {which_name} P.{page}`。

3. **曲目列表右键菜单扩展**：
   - 在 `_track_context_menu(pos)` 中：
     ```python
     act_pin = menu.addAction("📌 将当前页码设为本曲起始页")
     act_clear_pin = menu.addAction("清除本曲绑定的页码")
     ```
   - 点击后执行两边页码的提取与持久化，更新视图。

4. **切题自动跳转执行**：
   - 在 `_load_track(track_id)` 或 `_set_current_track_ui` 中，读取曲目数据字典：
     ```python
     kr_page = item.get("kr_page")
     if kr_page and self.kr_pdf.document.pageCount() > 0:
         self._loading_pages = True
         self.kr_pdf.jump_to_page(kr_page)
         self._loading_pages = False

     cn_page = item.get("cn_page")
     if cn_page and self.cn_pdf.document.pageCount() > 0:
         self._loading_pages = True
         self.cn_pdf.jump_to_page(cn_page)
         self._loading_pages = False
     ```
   - 注意：跳转时设置 `self._loading_pages = True`，防止触发 `_on_pdf_page_changed` 误把载入动作当成用户翻页去保存列表默认页。

---

## 4. 验证计划 (Verification Plan)

### 4.1 自动化检查指令
执行项目标准的三层门禁验证：
```powershell
python -m compileall -q core ui main.py
python tools/check_names.py
$env:QT_QPA_PLATFORM='offscreen'; python -c "from PySide6.QtWidgets import QApplication; from ui.main_window import MainWindow; app=QApplication([]); w=MainWindow(); print(w.stacked_widget.count())"
```

### 4.2 数据层测试脚本
编写临时脚本针对内存库或测试库测试迁移与读写：
```python
db = StudyDatabase(":memory:")
# 验证 table_info(playlist_items) 包含 kr_page, cn_page
# 验证 set_playlist_item_pages 和 playlist_items 返回正确字段
```

### 4.3 人工功能验证流程
1. 打开应用，进入「影子跟读」页面。
2. 在左侧选择一个包含曲目的播放列表，载入一份双语或试卷 PDF。
3. 选中第 1 题，把左边 PDF 翻到第 2 页，点击原文标题旁的 `📌` 按钮；将右边 PDF 翻到第 15 页，点击解析标题旁的 `📌` 按钮。观察 Toast 提示。
4. 选中第 2 题，把两份 PDF 分别翻到第 3 页和第 18 页，右键该曲目选择「📌 将当前页码设为本曲起始页」。
5. 来回点击第 1 题和第 2 题，验证两份 PDF 是否精准自动翻回各自对应的页码。
