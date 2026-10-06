"""Adversarial UI and Integration Stress Tests for SettingsView Anki Export.

Milestone 2 (Phase 7): Adversarially exercises SettingsView.export_anki()
under offscreen Qt:
- Button existence, icon, tooltip, objectName, layout stability
- Signal/slot wiring (button.click -> export_anki)
- Zero exportable words -> Banner warning displayed, QFileDialog suppressed
- Dialog cancellation -> Clean exit, no crash, no export executed
- Write failure (simulated PermissionError / OSError) -> Banner danger displayed, no crash
- Real filesystem write failure (e.g. writing over an existing directory) -> Banner danger displayed
- Successful export (TSV and CSV) -> Banner cleared, toast displayed, BOM and directives verified
- Default save path resolution (with/without material_root/单词)
- State transitions (zero words -> cancel -> failure -> success)
- Stress and layout stability under repeated invocations
- Full MainWindow integration test with SettingsView
"""

import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

# Force headless offscreen Qt platform before importing PySide6
os.environ["QT_QPA_PLATFORM"] = "offscreen"

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from PySide6.QtCore import QDate
from PySide6.QtWidgets import QApplication, QFileDialog, QPushButton

from core.config import DATA_DIR
from core.database import StudyDatabase
from ui.main_window import MainWindow
from ui.settings_view import SettingsView

APP = QApplication.instance() or QApplication([])


class TestSettingsAnkiAdversarial(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        self.db_path = self.temp_path / "test_settings_anki.db"
        self.database = StudyDatabase(self.db_path)
        self.view = SettingsView(self.database)
        # In Qt, child widgets report isVisible()=True only when ancestor is shown
        self.view.show()
        APP.processEvents()

    def tearDown(self):
        try:
            self.view.close()
            self.view.deleteLater()
            APP.processEvents()
        except Exception:
            pass
        try:
            self.database.connection.close()
        except Exception:
            pass
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def _insert_test_word(self, korean, meaning, state, note="", list_name="TOPIK中级"):
        """Helper to insert word with notes and states into test database."""
        cur = self.database.connection.cursor()
        cur.execute(
            "INSERT OR IGNORE INTO word_lists (name, item_count) VALUES (?, 1)",
            (list_name,),
        )
        cur.execute("SELECT id FROM word_lists WHERE name = ?", (list_name,))
        list_id = cur.fetchone()["id"]

        cur.execute(
            """
            INSERT INTO words (list_id, seq, korean, meaning, word_key)
            VALUES (?, 1, ?, ?, ?)
            """,
            (list_id, korean, meaning, korean),
        )
        cur.execute(
            """
            INSERT OR REPLACE INTO word_notes (word_key, note, state, updated_at)
            VALUES (?, ?, ?, datetime('now'))
            """,
            (korean, note, state),
        )
        self.database.connection.commit()

    def _insert_orphan_note(self, word_key, state, note=""):
        """Helper to insert orphan note row without matching entry in words table."""
        self.database.connection.execute(
            """
            INSERT OR REPLACE INTO word_notes (word_key, note, state, updated_at)
            VALUES (?, ?, ?, datetime('now'))
            """,
            (word_key, note, state),
        )
        self.database.connection.commit()

    # -----------------------------------------------------------------
    # 1. Button Properties, Icon, Tooltip, and Layout Stability
    # -----------------------------------------------------------------

    def test_btn_export_anki_properties_and_layout(self):
        """Verify Anki export button has correct properties, icon, tooltip, and layout."""
        btn = getattr(self.view, "btn_export_anki", None)
        self.assertIsNotNone(btn, "SettingsView must have btn_export_anki attribute")
        self.assertIsInstance(btn, QPushButton)
        self.assertEqual(btn.text(), "Anki 导出")
        self.assertEqual(btn.objectName(), "iconButton")
        self.assertEqual(
            btn.toolTip(),
            "导出待专攻单词（模糊与不认识）为 Anki 牌组 (TSV/CSV)",
        )
        # Verify icon is set and not null
        self.assertFalse(btn.icon().isNull(), "btn_export_anki icon must be loaded")

        # Verify button is attached to a layout
        parent_layout = btn.parentWidget()
        self.assertIsNotNone(parent_layout, "btn_export_anki must have a parent widget")

        # Verify button is enabled and visible
        self.assertTrue(btn.isEnabled())
        self.assertTrue(btn.isVisible())

    def test_btn_export_anki_signal_connection(self):
        """Verify clicking btn_export_anki triggers export_anki."""
        with patch.object(self.view, "export_anki") as mock_export:
            self.view.btn_export_anki.click()
            mock_export.assert_called_once()

    # -----------------------------------------------------------------
    # 2. Zero Words Workflow (Banner Warning)
    # -----------------------------------------------------------------

    def test_export_anki_zero_words_shows_warning_banner(self):
        """When 0 words are in fuzzy/unknown state, show warning banner and do not open file dialog."""
        # Ensure database has 0 exportable words
        self.assertEqual(self.database.count_anki_export_words(), 0)

        # Ensure banner starts hidden
        self.view.banner.clear()
        self.assertFalse(self.view.banner.isVisible())

        # Mock QFileDialog.getSaveFileName to ensure it is NEVER called
        with patch("ui.settings_view.QFileDialog.getSaveFileName") as mock_dialog:
            self.view.export_anki()
            mock_dialog.assert_not_called()

        # Verify warning banner is displayed
        self.assertTrue(self.view.banner.isVisible(), "Banner must be visible")
        self.assertEqual(self.view.banner.objectName(), "bannerWarning")
        self.assertIn("当前没有待专攻的单词（状态为模糊或不认识）。", self.view.banner._text.text())

    def test_export_anki_zero_words_with_only_known_words(self):
        """When words exist but all have state='known' or 'unseen', exportable count is 0 -> warning."""
        self._insert_test_word("사과", "苹果", "known")
        self.assertEqual(self.database.count_anki_export_words(), 0)

        with patch("ui.settings_view.QFileDialog.getSaveFileName") as mock_dialog:
            self.view.export_anki()
            mock_dialog.assert_not_called()

        self.assertTrue(self.view.banner.isVisible())
        self.assertEqual(self.view.banner.objectName(), "bannerWarning")
        self.assertIn("当前没有待专攻的单词", self.view.banner._text.text())

    # -----------------------------------------------------------------
    # 3. User Canceling File Dialog (Returns Empty String)
    # -----------------------------------------------------------------

    def test_export_anki_user_cancel_dialog_no_action_and_no_crash(self):
        """When user cancels the file dialog (returns empty path), do nothing, don't crash."""
        self._insert_test_word("나무", "树", "unknown")
        self.assertEqual(self.database.count_anki_export_words(), 1)

        # Clear any banner
        self.view.banner.clear()

        # Mock QFileDialog returning empty string (dialog canceled)
        with patch("ui.settings_view.QFileDialog.getSaveFileName", return_value=("", "")), \
             patch.object(self.database, "export_anki_file") as mock_export_file, \
             patch("ui.settings_view.show_toast") as mock_toast:
            # Trigger export
            self.view.export_anki()

            # export_anki_file and toast must NOT be called
            mock_export_file.assert_not_called()
            mock_toast.assert_not_called()

        # Banner should remain cleared (or not show danger)
        self.assertNotEqual(self.view.banner.objectName(), "bannerDanger")

    # -----------------------------------------------------------------
    # 4. Write Failure (Simulated PermissionError & OSError)
    # -----------------------------------------------------------------

    def test_export_anki_write_failure_permission_error_shows_danger_banner(self):
        """When export fails due to PermissionError, catch OSError and show banner danger without crashing."""
        self._insert_test_word("하늘", "天空", "fuzzy")
        self.assertEqual(self.database.count_anki_export_words(), 1)

        fake_path = "C:/System/Protected/anki.tsv"

        with patch("ui.settings_view.QFileDialog.getSaveFileName", return_value=(fake_path, "Anki TSV 牌组 (*.tsv)")), \
             patch.object(self.database, "export_anki_file", side_effect=PermissionError("[Errno 13] Permission denied")), \
             patch("ui.settings_view.show_toast") as mock_toast:
            # Calling export_anki must NOT raise unhandled exception
            try:
                self.view.export_anki()
            except Exception as e:
                self.fail(f"export_anki raised unhandled exception on write failure: {e}")

            mock_toast.assert_not_called()

        # Verify Banner danger is displayed
        self.assertTrue(self.view.banner.isVisible(), "Banner must be visible on write failure")
        self.assertEqual(self.view.banner.objectName(), "bannerDanger")
        self.assertIn("导出失败，无法写入文件：", self.view.banner._text.text())
        self.assertIn("Permission denied", self.view.banner._text.text())

    def test_export_anki_write_failure_generic_os_error(self):
        """When export fails due to generic OSError, show banner danger gracefully."""
        self._insert_test_word("바다", "大海", "unknown")
        fake_path = "Z:/NonExistentDrive/anki.tsv"

        with patch("ui.settings_view.QFileDialog.getSaveFileName", return_value=(fake_path, "Anki TSV 牌组 (*.tsv)")), \
             patch.object(self.database, "export_anki_file", side_effect=OSError("[Errno 5] Input/output error")):
            self.view.export_anki()

        self.assertTrue(self.view.banner.isVisible())
        self.assertEqual(self.view.banner.objectName(), "bannerDanger")
        self.assertIn("导出失败，无法写入文件：", self.view.banner._text.text())
        self.assertIn("Input/output error", self.view.banner._text.text())

    def test_export_anki_write_failure_real_filesystem_directory_conflict(self):
        """Real OS write failure: attempting to write a file over an existing directory path raises OSError."""
        self._insert_test_word("구름", "云", "fuzzy")
        conflict_dir = self.temp_path / "existing_dir_target"
        conflict_dir.mkdir(exist_ok=True)

        # Pass an existing directory path as the output file path
        with patch("ui.settings_view.QFileDialog.getSaveFileName", return_value=(str(conflict_dir), "Anki TSV 牌组 (*.tsv)")):
            self.view.export_anki()

        # Real OS returns PermissionError or IsADirectoryError (both OSError)
        self.assertTrue(self.view.banner.isVisible())
        self.assertEqual(self.view.banner.objectName(), "bannerDanger")
        self.assertIn("导出失败，无法写入文件：", self.view.banner._text.text())

    # -----------------------------------------------------------------
    # 5. Successful Export Workflow (TSV & CSV)
    # -----------------------------------------------------------------

    def test_export_anki_success_tsv(self):
        """Verify successful TSV export clears banner, shows toast, writes UTF-8 BOM file."""
        self._insert_test_word("학교", "学校", "unknown", note="高频词")
        self._insert_orphan_note("선생님", "fuzzy", note="孤儿笔记测试")

        out_file = self.temp_path / "output_test.tsv"

        with patch("ui.settings_view.QFileDialog.getSaveFileName", return_value=(str(out_file), "Anki TSV 牌组 (*.tsv)")), \
             patch("ui.settings_view.show_toast") as mock_toast:
            # Trigger export
            self.view.export_anki()

            # Verify toast was called with 2 exported words
            mock_toast.assert_called_once()
            args, _ = mock_toast.call_args
            self.assertIn("2", args[1])
            self.assertIn("待专攻单词", args[1])

        # Verify banner is hidden
        self.assertFalse(self.view.banner.isVisible())

        # Verify physical file on disk
        self.assertTrue(out_file.exists())
        raw_bytes = out_file.read_bytes()
        self.assertTrue(raw_bytes.startswith(b"\xef\xbb\xbf"), "Must contain UTF-8 BOM")

        content = out_file.read_text(encoding="utf-8-sig")
        self.assertIn("#separator:tab", content)
        self.assertIn("#html:true", content)
        self.assertIn("#tags column:4", content)
        self.assertIn("학교", content)
        self.assertIn("선생님", content)
        self.assertIn("待专攻::不认识", content)
        self.assertIn("待专攻::模糊", content)

    def test_export_anki_success_csv(self):
        """Verify successful CSV export automatically uses comma delimiter."""
        self._insert_test_word("친구", "朋友", "unknown")
        out_file = self.temp_path / "output_test.csv"

        with patch("ui.settings_view.QFileDialog.getSaveFileName", return_value=(str(out_file), "Anki CSV 牌组 (*.csv)")):
            self.view.export_anki()

        self.assertTrue(out_file.exists())
        content = out_file.read_text(encoding="utf-8-sig")
        self.assertIn("#separator:Comma", content)
        self.assertIn("韩语,背面,笔记,标签", content)
        self.assertIn("친구", content)

    # -----------------------------------------------------------------
    # 6. Default Save Path Calculation Logic
    # -----------------------------------------------------------------

    def test_default_save_path_resolution(self):
        """Verify default directory calculation under different material_root configurations."""
        self._insert_test_word("시험", "考试", "unknown")

        # Case A: material_root has 单词 subfolder
        mat_root = self.temp_path / "material_root"
        words_sub = mat_root / "单词"
        words_sub.mkdir(parents=True)
        self.database.set_material_root(str(mat_root))

        captured_paths = []
        def capture_get_save_filename(parent, title, default_path, filters):
            captured_paths.append(default_path)
            return ("", "")

        with patch("ui.settings_view.QFileDialog.getSaveFileName", side_effect=capture_get_save_filename):
            self.view.export_anki()

        self.assertEqual(len(captured_paths), 1)
        expected_today = date.today().isoformat()
        self.assertTrue(captured_paths[0].startswith(str(words_sub)))
        self.assertIn(f"topik-anki-待专攻-{expected_today}.tsv", captured_paths[0])

        # Case B: material_root exists but has NO 单词 subfolder -> fall back to DATA_DIR
        empty_mat = self.temp_path / "empty_mat"
        empty_mat.mkdir()
        self.database.set_material_root(str(empty_mat))

        captured_paths.clear()
        with patch("ui.settings_view.QFileDialog.getSaveFileName", side_effect=capture_get_save_filename):
            self.view.export_anki()

        self.assertEqual(len(captured_paths), 1)
        self.assertTrue(captured_paths[0].startswith(str(DATA_DIR)))

        # Case C: when database returns empty material_root -> fall back to DATA_DIR
        with patch.object(self.database, "get_material_root", return_value=""):
            captured_paths.clear()
            with patch("ui.settings_view.QFileDialog.getSaveFileName", side_effect=capture_get_save_filename):
                self.view.export_anki()

            self.assertEqual(len(captured_paths), 1)
            self.assertTrue(captured_paths[0].startswith(str(DATA_DIR)))

    # -----------------------------------------------------------------
    # 7. State Transition and Stress Cycles
    # -----------------------------------------------------------------

    def test_state_transition_sequence(self):
        """Stress-test sequential state transitions: zero words -> cancel -> failure -> success."""
        # Step 1: Zero words -> Warning banner
        self.assertEqual(self.database.count_anki_export_words(), 0)
        self.view.export_anki()
        self.assertTrue(self.view.banner.isVisible())
        self.assertEqual(self.view.banner.objectName(), "bannerWarning")

        # Step 2: Add word -> User cancels -> Warning banner state remains untouched
        self._insert_test_word("단어", "单词", "fuzzy")
        with patch("ui.settings_view.QFileDialog.getSaveFileName", return_value=("", "")):
            self.view.export_anki()
        # Still not danger
        self.assertNotEqual(self.view.banner.objectName(), "bannerDanger")

        # Step 3: Write failure -> Danger banner replaces warning
        fake_path = "C:/fake/path.tsv"
        with patch("ui.settings_view.QFileDialog.getSaveFileName", return_value=(fake_path, "")), \
             patch.object(self.database, "export_anki_file", side_effect=PermissionError("Denied")):
            self.view.export_anki()
        self.assertTrue(self.view.banner.isVisible())
        self.assertEqual(self.view.banner.objectName(), "bannerDanger")

        # Step 4: Write success -> Banner cleared
        out_file = self.temp_path / "success.tsv"
        with patch("ui.settings_view.QFileDialog.getSaveFileName", return_value=(str(out_file), "")):
            self.view.export_anki()
        self.assertFalse(self.view.banner.isVisible())
        self.assertTrue(out_file.exists())

    def test_repeated_export_stress_layout_stability(self):
        """Repeatedly trigger export_anki 50 times with alternating outcomes to verify stability."""
        self._insert_test_word("스트레스", "压力", "unknown")
        out_file = self.temp_path / "stress_out.tsv"

        # Record initial widget layout geometry and counts
        btn = self.view.btn_export_anki
        init_text = btn.text()
        init_object_name = btn.objectName()

        for i in range(50):
            mode = i % 3
            if mode == 0:
                # Cancel
                with patch("ui.settings_view.QFileDialog.getSaveFileName", return_value=("", "")):
                    self.view.export_anki()
            elif mode == 1:
                # Failure
                with patch("ui.settings_view.QFileDialog.getSaveFileName", return_value=("fail.tsv", "")), \
                     patch.object(self.database, "export_anki_file", side_effect=OSError("Disk Full")):
                    self.view.export_anki()
                self.assertEqual(self.view.banner.objectName(), "bannerDanger")
            else:
                # Success
                with patch("ui.settings_view.QFileDialog.getSaveFileName", return_value=(str(out_file), "")):
                    self.view.export_anki()
                self.assertFalse(self.view.banner.isVisible())

            APP.processEvents()

        # Layout, button text, object name must remain completely stable
        self.assertEqual(btn.text(), init_text)
        self.assertEqual(btn.objectName(), init_object_name)
        self.assertTrue(btn.isEnabled())

    # -----------------------------------------------------------------
    # 8. Full MainWindow Integration Lifecycle
    # -----------------------------------------------------------------

    def test_mainwindow_integration_settings_export_anki(self):
        """Verify SettingsView and Anki export button in full MainWindow lifecycle."""
        with patch("ui.vault_view.VaultView.start_scan"):
            main_win = MainWindow()
            APP.processEvents()
            try:
                self.assertTrue(hasattr(main_win, "settings_view"))
                settings = main_win.settings_view
                self.assertTrue(hasattr(settings, "btn_export_anki"))
                self.assertEqual(settings.btn_export_anki.text(), "Anki 导出")

                # Exercise export_anki through MainWindow
                with patch("ui.settings_view.QFileDialog.getSaveFileName", return_value=("", "")):
                    settings.btn_export_anki.click()

                APP.processEvents()
            finally:
                main_win.close()
                APP.processEvents()


if __name__ == "__main__":
    unittest.main()
