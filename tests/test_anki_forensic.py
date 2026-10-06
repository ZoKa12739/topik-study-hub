"""Forensic integrity verification and stress test suite for Milestone 2 (Anki Export).

Executes comprehensive static, behavioral, dynamic query, filesystem, and Qt UI
inspections under Benchmark Mode integrity constraints.
"""

import csv
import io
import os
import shutil
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

from core.database import StudyDatabase


class TestAnkiDynamicQueryForensics(unittest.TestCase):
    """Forensic verification of SQLite dynamic query behavior."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / "test_forensic.db"
        self.db = StudyDatabase(self.db_path)

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_zero_words_returns_empty_and_zero_count(self):
        """Verify that an empty database or database without fuzzy/unknown returns [] and 0."""
        self.assertEqual(self.db.count_anki_export_words(), 0)
        self.assertEqual(self.db.get_anki_export_words(), [])

        # Add notes with only 'known' and None states
        self.db.set_word_state("사과", "known")
        self.db.set_word_state("배", None)
        self.assertEqual(self.db.count_anki_export_words(), 0)
        self.assertEqual(self.db.get_anki_export_words(), [])

    def test_dynamic_reflection_of_state_mutations(self):
        """Verify that queries dynamically reflect insertions and state transitions."""
        # Insert unknown note
        self.db.set_word_state("가다", "unknown")
        self.assertEqual(self.db.count_anki_export_words(), 1)
        words = self.db.get_anki_export_words()
        self.assertEqual(len(words), 1)
        self.assertEqual(words[0]["korean"], "가다")
        self.assertEqual(words[0]["state"], "unknown")

        # Insert fuzzy note
        self.db.set_word_state("오다", "fuzzy")
        self.assertEqual(self.db.count_anki_export_words(), 2)

        # Transition fuzzy to known
        self.db.set_word_state("오다", "known")
        self.assertEqual(self.db.count_anki_export_words(), 1)
        words = self.db.get_anki_export_words()
        self.assertEqual(len(words), 1)
        self.assertEqual(words[0]["korean"], "가다")

    def test_orphan_notes_preservation(self):
        """Verify that notes without corresponding words in `words` table are preserved."""
        self.db.set_word_state("고아단어1", "unknown")
        self.db.save_word_note("고아단어1", "用户孤儿笔记内容", False)
        self.db.set_word_state("고아단어2", "fuzzy")

        words = self.db.get_anki_export_words()
        self.assertEqual(len(words), 2)
        by_key = {w["korean"]: w for w in words}
        self.assertIn("고아단어1", by_key)
        self.assertEqual(by_key["고아단어1"]["meaning"], "")
        self.assertEqual(by_key["고아단어1"]["note"], "用户孤儿笔记内容")
        self.assertEqual(by_key["고아단어1"]["list_names"], "")

    def test_multi_list_deduplication_and_tag_aggregation(self):
        """Verify that a word present across multiple word lists produces exactly one card with aggregated tags."""
        # Create two lists
        res1 = self.db.import_word_list(
            "高级词汇 表一",
            "dummy1.tsv",
            [(1, "배제하다", "排除，排斥"), (2, "유지하다", "保持")],
        )
        res2 = self.db.import_word_list(
            "中级精选 表二",
            "dummy2.tsv",
            [(1, "배제하다", "排除，排斥 (中级)"), (3, "공부하다", "学习")],
        )
        list1_id = res1["list_id"]
        list2_id = res2["list_id"]

        # Set state to unknown for 배제하다
        self.db.set_word_state("배제하다", "unknown")
        self.db.save_word_note("배제하다", "注意发音 [배제]", False)

        # Total export
        self.assertEqual(self.db.count_anki_export_words(), 1)
        words = self.db.get_anki_export_words()
        self.assertEqual(len(words), 1)
        card = words[0]
        self.assertEqual(card["korean"], "배제하다")
        # Ensure list names aggregated
        lists_in_card = card["list_names"].split(",")
        self.assertEqual(len(lists_in_card), 2)
        self.assertIn("高级词汇 表一", lists_in_card)
        self.assertIn("中级精选 表二", lists_in_card)

        # Filtering by list_id
        count1 = self.db.count_anki_export_words(list_id=list1_id)
        words1 = self.db.get_anki_export_words(list_id=list1_id)
        self.assertEqual(count1, 1)
        self.assertEqual(len(words1), 1)
        self.assertEqual(words1[0]["korean"], "배제하다")
        self.assertEqual(words1[0]["list_names"], "高级词汇 表一")

        # List without matching state
        self.db.set_word_state("공부하다", "known")
        count2 = self.db.count_anki_export_words(list_id=list2_id)
        self.assertEqual(count2, 1)

    def test_sorting_order_unknown_before_fuzzy(self):
        """Verify unknown words strictly precede fuzzy words, each alphabetically sorted."""
        self.db.set_word_state("나_fuzzy", "fuzzy")
        self.db.set_word_state("가_fuzzy", "fuzzy")
        self.db.set_word_state("다_unknown", "unknown")
        self.db.set_word_state("라_unknown", "unknown")

        words = self.db.get_anki_export_words()
        self.assertEqual(len(words), 4)
        order = [w["korean"] for w in words]
        # unknown should be first (sorted by korean: 다_unknown, 라_unknown)
        # then fuzzy (sorted by korean: 가_fuzzy, 나_fuzzy)
        self.assertEqual(order, ["다_unknown", "라_unknown", "가_fuzzy", "나_fuzzy"])


class TestAnkiFormattingForensics(unittest.TestCase):
    """Forensic verification of Anki export formatting, HTML escaping, and tag generation."""

    def test_tsv_format_directives_and_columns(self):
        words = [
            {
                "korean": "사과",
                "meaning": "苹果",
                "note": "每日一苹果",
                "state": "unknown",
                "list_names": "水果词汇",
            }
        ]
        tsv = StudyDatabase.format_anki_export(words, delimiter="\t")
        reader = csv.reader(io.StringIO(tsv), delimiter="\t")
        rows = list(reader)
        self.assertEqual(rows[0], ["#separator:tab"])
        self.assertEqual(rows[1], ["#html:true"])
        self.assertEqual(rows[2], ["#tags column:4"])
        self.assertEqual(rows[3], ["#columns:韩语", "背面", "笔记", "标签"])

        row = rows[4]
        self.assertEqual(len(row), 4)
        self.assertEqual(row[0], "사과")
        self.assertIn("苹果", row[1])
        self.assertIn("每日一苹果", row[1])
        self.assertEqual(row[2], "每日一苹果")
        self.assertEqual(row[3], "TOPIK 待专攻 待专攻::不认识 水果词汇")

    def test_csv_format_directives_and_columns(self):
        words = [
            {
                "korean": "배",
                "meaning": "梨子, 船",
                "note": "多义词",
                "state": "fuzzy",
                "list_names": "基础词汇",
            }
        ]
        csv_text = StudyDatabase.format_anki_export(words, delimiter=",")
        reader = csv.reader(io.StringIO(csv_text), delimiter=",")
        rows = list(reader)
        self.assertEqual(rows[0], ["#separator:Comma"])
        self.assertEqual(rows[1], ["#html:true"])
        self.assertEqual(rows[2], ["#tags column:4"])
        self.assertEqual(rows[3], ["#columns:韩语", "背面", "笔记", "标签"])

        row = rows[4]
        self.assertEqual(len(row), 4)
        self.assertEqual(row[0], "배")
        self.assertIn("梨子, 船", row[1])
        self.assertEqual(row[3], "TOPIK 待专攻 待专攻::模糊 基础词汇")

    def test_html_escaping_and_xss_protection(self):
        """Verify HTML characters (<, >, &, \") are safely escaped in Back card."""
        words = [
            {
                "korean": "위험한단어",
                "meaning": "含义含有 <script> & \"引号\"",
                "note": "笔记含有 <b>加粗</b> & <style>",
                "state": "unknown",
                "list_names": "测试",
            }
        ]
        tsv = StudyDatabase.format_anki_export(words, delimiter="\t")
        reader = csv.reader(io.StringIO(tsv), delimiter="\t")
        rows = [r for r in reader if r and not r[0].startswith("#")]
        self.assertEqual(len(rows), 1)
        back_card = rows[0][1]

        # Verify Back card escaping
        self.assertNotIn("<script>", back_card)
        self.assertIn("&lt;script&gt;", back_card)
        self.assertIn("&amp;", back_card)
        self.assertIn("&quot;引号&quot;", back_card)
        self.assertIn("&lt;b&gt;加粗&lt;/b&gt;", back_card)
        self.assertIn("&lt;style&gt;", back_card)

    def test_newlines_converted_to_br_in_back_card(self):
        """Verify CR, LF, CRLF are converted to <br> in Back card."""
        words = [
            {
                "korean": "줄바꿈",
                "meaning": "Line 1\r\nLine 2\nLine 3\rLine 4",
                "note": "Note 1\r\nNote 2",
                "state": "fuzzy",
                "list_names": "",
            }
        ]
        tsv = StudyDatabase.format_anki_export(words, delimiter="\t")
        reader = csv.reader(io.StringIO(tsv), delimiter="\t")
        rows = [r for r in reader if r and not r[0].startswith("#")]
        self.assertEqual(len(rows), 1)
        back_card = rows[0][1]
        self.assertIn("Line 1<br>Line 2<br>Line 3<br>Line 4", back_card)
        self.assertIn("Note 1<br>Note 2", back_card)

    def test_back_field_fallbacks(self):
        """Verify back field formatting with various missing components."""
        cases = [
            # 1. Both meaning and note
            (
                {"korean": "k1", "meaning": "M", "note": "N", "state": "unknown"},
                'M<br><br><small style="color:#666"><b>[笔记]</b> N</small>',
            ),
            # 2. Only meaning
            ({"korean": "k2", "meaning": "M", "note": "", "state": "unknown"}, "M"),
            # 3. Only note (orphan note)
            (
                {"korean": "k3", "meaning": "", "note": "N", "state": "fuzzy"},
                '<small style="color:#666"><b>[笔记]</b> N</small>',
            ),
            # 4. Neither meaning nor note
            (
                {"korean": "k4", "meaning": "", "note": "", "state": "fuzzy"},
                '<small style="color:#999">（暂无释义）</small>',
            ),
        ]
        for item, expected_back in cases:
            tsv = StudyDatabase.format_anki_export([item])
            reader = csv.reader(io.StringIO(tsv), delimiter="\t")
            data_rows = [r for r in reader if r and not r[0].startswith("#")]
            self.assertEqual(data_rows[0][1], expected_back)

    def test_tag_sanitization_and_hierarchy(self):
        """Verify tag hierarchy and space replacement for list names."""
        words = [
            {
                "korean": "태그테스트",
                "meaning": "test",
                "note": "",
                "state": "unknown",
                "list_names": "TOPIK II 高频 3000, 考前 冲刺 词表, TOPIK",
            }
        ]
        tsv = StudyDatabase.format_anki_export(words)
        reader = csv.reader(io.StringIO(tsv), delimiter="\t")
        data_rows = [r for r in reader if r and not r[0].startswith("#")]
        tags = data_rows[0][3].split()
        self.assertIn("TOPIK", tags)
        self.assertIn("待专攻", tags)
        self.assertIn("待专攻::不认识", tags)
        self.assertIn("TOPIK_II_高频_3000", tags)
        self.assertIn("考前_冲刺_词表", tags)
        # Duplicate TOPIK tag should not be repeated
        self.assertEqual(tags.count("TOPIK"), 1)


class TestAnkiFilesystemExportForensics(unittest.TestCase):
    """Forensic verification of filesystem writing, BOM encoding, and delimiter inference."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / "test_export.db"
        self.db = StudyDatabase(self.db_path)
        self.db.set_word_state("테스트1", "unknown")
        self.db.set_word_state("테스트2", "fuzzy")

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_export_file_creates_nested_directory_and_utf8_sig(self):
        nested_path = Path(self.temp_dir) / "nested" / "subdir" / "anki_deck.tsv"
        self.assertFalse(nested_path.parent.exists())

        count = self.db.export_anki_file(nested_path)
        self.assertEqual(count, 2)
        self.assertTrue(nested_path.is_file())

        # Inspect raw bytes for UTF-8 BOM
        raw_bytes = nested_path.read_bytes()
        self.assertTrue(
            raw_bytes.startswith(b"\xef\xbb\xbf"),
            "Export file MUST start with UTF-8 BOM (\\xef\\xbb\\xbf)",
        )

        # Read back as utf-8-sig
        text = nested_path.read_text(encoding="utf-8-sig")
        self.assertIn("#separator:tab", text)
        self.assertIn("테스트1", text)
        self.assertIn("테스트2", text)

    def test_export_file_csv_delimiter_inference(self):
        csv_path = Path(self.temp_dir) / "anki_deck.csv"
        count = self.db.export_anki_file(csv_path)
        self.assertEqual(count, 2)

        text = csv_path.read_text(encoding="utf-8-sig")
        self.assertIn("#separator:Comma", text)


class TestSettingsViewUIForensics(unittest.TestCase):
    """Forensic verification of SettingsView integration with StudyDatabase and Qt."""

    @classmethod
    def setUpClass(cls):
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / "test_ui.db"
        self.db = StudyDatabase(self.db_path)

        from ui.settings_view import SettingsView
        self.view = SettingsView(self.db)
        self.view.show()

    def tearDown(self):
        self.view.deleteLater()
        self.db.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_button_existence_and_properties(self):
        from PySide6.QtWidgets import QPushButton
        self.assertTrue(hasattr(self.view, "btn_export_anki"))
        btn = self.view.btn_export_anki
        self.assertIsInstance(btn, QPushButton)
        self.assertEqual(btn.text(), "Anki 导出")
        self.assertEqual(btn.objectName(), "iconButton")
        self.assertIn("待专攻", btn.toolTip())

    def test_zero_words_shows_warning_banner(self):
        """When 0 words, clicking export must show warning banner and NOT open file dialog."""
        with patch("PySide6.QtWidgets.QFileDialog.getSaveFileName") as mock_dialog:
            self.view.export_anki()
            mock_dialog.assert_not_called()

        self.assertFalse(self.view.banner.isHidden())
        self.assertIn("当前没有待专攻的单词", self.view.banner._text.text())

    def test_user_cancel_file_dialog(self):
        """When user cancels file dialog, no export occurs and banner remains hidden."""
        self.db.set_word_state("단어", "unknown")
        with patch("PySide6.QtWidgets.QFileDialog.getSaveFileName", return_value=("", "")):
            self.view.export_anki()

        self.assertTrue(self.view.banner.isHidden())

    def test_successful_export_workflow(self):
        """When export succeeds, file is written, banner is cleared, toast is shown."""
        self.db.set_word_state("성공단어", "unknown")
        out_path = str(Path(self.temp_dir) / "exported_deck.tsv")

        with patch("PySide6.QtWidgets.QFileDialog.getSaveFileName", return_value=(out_path, "tsv")):
            with patch("ui.settings_view.show_toast") as mock_toast:
                self.view.export_anki()
                mock_toast.assert_called_once()
                self.assertIn("已导出 1 个待专攻单词", mock_toast.call_args[0][1])

        self.assertTrue(self.view.banner.isHidden())
        self.assertTrue(os.path.isfile(out_path))

    def test_oserror_displays_danger_banner(self):
        """When filesystem raises OSError, danger banner is displayed."""
        self.db.set_word_state("에러단어", "unknown")
        with patch("PySide6.QtWidgets.QFileDialog.getSaveFileName", return_value=("/invalid/path/deck.tsv", "tsv")):
            with patch.object(self.db, "export_anki_file", side_effect=OSError("Permission denied")):
                self.view.export_anki()

        self.assertFalse(self.view.banner.isHidden())
        self.assertIn("导出失败，无法写入文件", self.view.banner._text.text())


if __name__ == "__main__":
    unittest.main()
