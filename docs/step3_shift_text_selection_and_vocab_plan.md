# 影子跟读增强 · 第三步实施计划：Shift 划词与 P1 单词仓联动

## 1. 目标与背景 (Goal Description)

在影子跟读听力真题时，用户经常在原文或解析中遇到生词。目前如果想要查词或记录，必须切换到外部词典或手动在 P1 单词仓新建，割裂了专注跟读的心流。
**本任务目标**：在保持现有平滑抓手平移（`HandPdfView`）不变的前提下，支持按住 `Shift` 拖拽框选 PDF 文本，松开后呼出快捷菜单（复制 / 加入生词本）；点击「加入生词本」弹出轻量确认窗快速补充释义，直通 P1 单词仓并落库。

---

## 2. 交互与行为规范 (UX Specifications)

1. **抓手平移与框选平衡**：
   - **普通左键拖拽**：完全保持现有逻辑，抓手平移（`ClosedHandCursor`），页面跟随滚动。
   - **按住 `Shift` 拖拽**：光标变为工字光标（`IBeamCursor`）或十字光标，在页面上拉出半透明选择框（`QRubberBand`）。
2. **划词释放菜单**：
   - 松开鼠标左键时，计算选区对应的 PDF 坐标并提取文字（去除首尾空白与换行冗余）：
     - 若提取文本为空（如纯白边框或纯图像扫描页），静默清除选框，不弹扰民菜单。
     - 若成功提取文本，在光标位置弹出微型上下文菜单（`QMenu`）：
       - `复制文本`（点击后写入系统剪贴板，弹出 Toast `已复制文本`）
       - `加入生词本 (Ctrl+D)`（点击打开快速入库弹窗）
3. **生词快速确认弹窗 (`AddWordDialog`)**：
   - 模态小弹窗，样式继承全局 `APP_STYLESHEET`，不产生割裂感。
   - 字段包括：
     - **韩语单词**：预填框选文字（单行文本框，允许微调修正）。
     - **中文释义**：单行输入框，**打开时自动获得焦点**，占位文字 `输入中文释义（可选，回车保存）`。
     - **来源备注**：只读/微型展示（例如 `来源：64届 听力 13~14题 P.5`）。
   - 快捷操作：
     - 按 `Enter` 直接保存入库。
     - 按 `Esc` 取消。
4. **P1 单词仓入库逻辑**：
   - 自动存入默认专有词表：「**跟读生词本**」（若不存在则自动创建，词表 `source_path` 标记为 `shadowing_collected`）。
   - 增量生成序号 `seq`，生成 `word_key`，写入 `words` 表。
   - 若填写了释义或备注，同步记录到 `word_notes`。
   - 入库成功后通过 `show_toast` 提示：`已将「{korean}」加入跟读生词本`。

---

## 3. 详细改动方案 (Proposed Changes)

### 3.1 核心数据层：`core/database.py`

#### [MODIFY] `core/database.py`

新增面向单词写入的业务接口 `add_single_word`：

```python
def add_single_word(self, list_name: str, korean: str, meaning: str = "", note: str = "") -> dict:
    """向指定词表（默认'跟读生词本'）追加单个生词。
    
    若词表不存在则自动创建；若生词已存在则更新其释义与笔记。
    """
    cleaned_kr = korean.strip()
    cleaned_meaning = meaning.strip()
    if not cleaned_kr:
        raise ValueError("单词内容不能为空")

    w_key = self.word_key(cleaned_kr)
    with self.connection:
        # 1. 查找或创建词表
        row = self.connection.execute(
            "SELECT id FROM word_lists WHERE name = ?", (list_name,)
        ).fetchone()
        if row:
            list_id = row["id"]
        else:
            next_order = self.connection.execute(
                "SELECT COALESCE(MAX(sort_order), 0) + 1 FROM word_lists"
            ).fetchone()[0]
            cursor = self.connection.execute(
                "INSERT INTO word_lists (name, source_path, item_count, sort_order) VALUES (?, ?, ?, ?)",
                (list_name, "shadowing_collected", 0, next_order),
            )
            list_id = cursor.lastrowid
            self.connection.execute(
                "INSERT OR IGNORE INTO word_list_progress (list_id) VALUES (?)", (list_id,)
            )

        # 2. 检查词表内是否已有此词
        existing_word = self.connection.execute(
            "SELECT id, seq FROM words WHERE list_id = ? AND word_key = ?",
            (list_id, w_key),
        ).fetchone()

        if existing_word:
            word_id = existing_word["id"]
            if cleaned_meaning:
                self.connection.execute(
                    "UPDATE words SET meaning = ? WHERE id = ?",
                    (cleaned_meaning, word_id),
                )
        else:
            max_seq = self.connection.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM words WHERE list_id = ?",
                (list_id,),
            ).fetchone()[0]
            cursor = self.connection.execute(
                "INSERT INTO words (list_id, seq, korean, meaning, word_key) VALUES (?, ?, ?, ?, ?)",
                (list_id, max_seq + 1, cleaned_kr, cleaned_meaning, w_key),
            )
            word_id = cursor.lastrowid
            # 刷新词表条目数
            self.connection.execute(
                "UPDATE word_lists SET item_count = (SELECT COUNT(*) FROM words WHERE list_id = ?) WHERE id = ?",
                (list_id, list_id),
            )

        # 3. 若有备注，写入第二层 word_notes
        if note.strip():
            self.save_word_note(w_key, note.strip())

    return {"word_id": word_id, "list_id": list_id, "korean": cleaned_kr}
```

---

### 3.2 界面交互层：`ui/shadowing_view.py`

#### [MODIFY] `ui/shadowing_view.py`

1. **扩展 `HandPdfView` 支持 Shift 框选**：
   ```python
   class HandPdfView(QPdfView):
       zoom_requested = Signal(int)
       focused = Signal()
       text_selected = Signal(str, QPoint)  # (extracted_text, global_pos)

       def __init__(self, parent=None):
           super().__init__(parent)
           self._rubber_band = None
           self._selection_origin = None
           self._is_selecting = False

       def mousePressEvent(self, event):
           self.focused.emit()
           # 按住 Shift 触发划词选区
           if event.button() == Qt.MouseButton.LeftButton and (event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
               self._is_selecting = True
               self._selection_origin = event.position().toPoint()
               if not self._rubber_band:
                   self._rubber_band = QRubberBand(QRubberBand.Shape.Rectangle, self.viewport())
               self._rubber_band.setGeometry(QRect(self._selection_origin, QSize()))
               self._rubber_band.show()
               self.setCursor(Qt.CursorShape.IBeamCursor)
               event.accept()
               return

           # 原有抓手平移保持不变
           if event.button() == Qt.MouseButton.LeftButton:
               self._dragging = True
               self._drag_pos = event.position().toPoint()
               self.setCursor(Qt.CursorShape.ClosedHandCursor)
               event.accept()
               return
           super().mousePressEvent(event)

       def mouseMoveEvent(self, event):
           if self._is_selecting and self._rubber_band:
               rect = QRect(self._selection_origin, event.position().toPoint()).normalized()
               self._rubber_band.setGeometry(rect)
               event.accept()
               return

           if getattr(self, "_dragging", False):
               # 原有抓手平移滚动条逻辑
               ...
           super().mouseMoveEvent(event)

       def mouseReleaseEvent(self, event):
           if self._is_selecting and event.button() == Qt.MouseButton.LeftButton:
               self._is_selecting = False
               rect = self._rubber_band.geometry() if self._rubber_band else QRect()
               if self._rubber_band:
                   self._rubber_band.hide()
               self.setCursor(Qt.CursorShape.OpenHandCursor)
               if rect.width() > 8 and rect.height() > 8:
                   self._extract_selected_text(rect, event.globalPosition().toPoint())
               event.accept()
               return

           # 原有平移释放逻辑保持不变
           ...
           super().mouseReleaseEvent(event)
   ```

2. **选区文字提取算法 (`_extract_selected_text`)**：
   - 利用当前页码与 `pymupdf`（已在 requirements 中，优雅降级）或 QtPdf 进行选区映射提取：
   - 提取逻辑：
     ```python
     def _extract_selected_text(self, viewport_rect: QRect, global_pos: QPoint):
         # 计算当前页对应的缩放与物理坐标
         page_num = self.pageNavigator().currentPage()
         pdf_path = getattr(self.parent(), "_path", None)
         text = ""
         if pdf_path and os.path.exists(pdf_path):
             try:
                 import fitz
                 doc = fitz.open(pdf_path)
                 if 0 <= page_num < len(doc):
                     page = doc[page_num]
                     # 将视口像素映射到 PDF 点（根据当前 page 宽度和视口宽比例）
                     scale_x = page.rect.width / max(1, self.viewport().width())
                     scale_y = page.rect.height / max(1, self.viewport().height())
                     # 计算 clip 矩形（考虑滚动条偏移）
                     scroll_x = self.horizontalScrollBar().value()
                     scroll_y = self.verticalScrollBar().value()
                     rx = (viewport_rect.x() + scroll_x) * scale_x
                     ry = (viewport_rect.y() + scroll_y) * scale_y
                     rw = viewport_rect.width() * scale_x
                     rh = viewport_rect.height() * scale_y
                     clip = fitz.Rect(rx, ry, rx + rw, ry + rh)
                     text = page.get_text("text", clip=clip).strip()
             except Exception:
                 pass
         
         # 清理提取文本中的多余换行与空格
         text = " ".join(text.split())
         if text:
             self.text_selected.emit(text, global_pos)
     ```

3. **创建生词确认弹窗 `AddWordDialog`**：
   ```python
   class AddWordDialog(QDialog):
       def __init__(self, korean: str, context_source: str = "", parent=None):
           super().__init__(parent)
           self.setWindowTitle("加入生词本")
           self.setFixedWidth(420)
           self.setObjectName("addWordDialog")
           # 布局与输入控件：
           # 1. 韩语词汇（QLineEdit）
           # 2. 中文释义（QLineEdit，自动焦点，按回车触发 accept）
           # 3. 来源小字说明
           # 4. 取消 / 保存按钮
   ```

4. **连接划词菜单与弹窗**：
   - 当 `text_selected(text, global_pos)` 触发时：
     - 构建微型 `QMenu`：
       - `复制`: `QApplication.clipboard().setText(text)` + `show_toast("已复制")`
       - `加入生词本`: 弹出 `AddWordDialog`。用户保存后调用 `database.add_single_word(...)` 并弹出成功 Toast。

---

## 4. 验证计划 (Verification Plan)

### 4.1 自动化检查指令
```powershell
python -m compileall -q core ui main.py
python tools/check_names.py
$env:QT_QPA_PLATFORM='offscreen'; python -c "from PySide6.QtWidgets import QApplication; from ui.main_window import MainWindow; app=QApplication([]); w=MainWindow(); print(w.stacked_widget.count())"
```

### 4.2 单元逻辑验证
在 Python 控制台或临时测试中验证：
```python
db = StudyDatabase(test_db_path)
res = db.add_single_word("跟读生词本", "듣기", "听力", "来自真题64届第1题")
assert res["korean"] == "듣기"
# 验证 P1 单词仓能读出该词
```

### 4.3 人工功能验证流程
1. 打开影子跟读，载入一份带文字层的真题 PDF。
2. **测试平移**：普通鼠标左键拖动，验证视口依然正常顺畅平移。
3. **测试划词**：按住键盘 `Shift` 键并在文本区域拖拽，验证出现半透明选框，松开后在光标处弹出菜单。
4. **测试复制**：点击「复制」，在文本编辑器粘贴，验证文字正确提取。
5. **测试加入生词本**：按住 `Shift` 框选生词并点击「加入生词本」，在弹出窗口输入中文释义按回车。
6. **联动验证**：切换到 P1「单词仓」，检查左侧词表列表中是否出现「跟读生词本」，并验证刚才添加的词汇与释义已完整入库。
