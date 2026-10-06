"""Adversarial UI and Integration Stress Tests for PlannerView (Milestone 1).

Covers offscreen Qt instantiation, empty and populated databases,
repeated showEvent/refresh cycles (leak and widget duplication checks),
DayCell integrity, tooltips, total_completed_value, and week_note.
"""

import os
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

# Force headless offscreen Qt platform before importing PySide6
os.environ["QT_QPA_PLATFORM"] = "offscreen"

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import QApplication, QWidget

from core.database import StudyDatabase
from ui.planner_view import DayCell, PlannerView

# Ensure single QApplication instance across test suite
APP = QApplication.instance() or QApplication([])


class TestPlannerViewAdversarial(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_adversarial.db"
        self.database = StudyDatabase(self.db_path)

    def tearDown(self):
        try:
            self.database.connection.close()
        except Exception:
            pass
        self.temp_dir.cleanup()

    def _log_activity(self, activity_type, amount, unit="分钟", logged_on=None):
        if logged_on is None:
            logged_on = date.today().isoformat()
        self.database.connection.execute(
            """
            INSERT INTO activity_log (activity_type, amount, unit, logged_on, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (activity_type, amount, unit, logged_on, f"{logged_on} 12:00:00"),
        )
        self.database.connection.commit()

    # -------------------------------------------------------------
    # 1. Empty Database Tests
    # -------------------------------------------------------------
    def test_planner_view_empty_database(self):
        """PlannerView must instantiate cleanly with an empty database."""
        planner = PlannerView(self.database)
        APP.processEvents()

        # Day cells contract
        self.assertEqual(len(planner.day_cells), 7, "day_cells must contain exactly 7 cells")
        self.assertEqual(planner.day_strip.count(), 7, "day_strip layout must hold 7 widgets")

        weekdays_expected = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        for idx, cell in enumerate(planner.day_cells):
            self.assertIsInstance(cell, DayCell)
            self.assertEqual(cell.weekday_label.text(), weekdays_expected[idx])
            self.assertRegex(cell.date_label.text(), r"^\d{2}/\d{2}$")
            self.assertEqual(cell.stat_label.text(), "—")
            # Tooltip must be non-empty even on empty DB
            tooltip = cell.toolTip()
            self.assertTrue(len(tooltip) > 0, f"Cell {idx} tooltip must not be empty")

            # DayCell dict-like access
            self.assertEqual(cell["weekday"], cell.weekday_label)
            self.assertEqual(cell["date"], cell.date_label)
            self.assertEqual(cell["stat"], cell.stat_label)
            self.assertEqual(cell["frame"], cell)
            with self.assertRaises(KeyError):
                _ = cell["nonexistent_key"]

        # Metric values
        self.assertEqual(planner.total_completed_value.text(), "0 项")
        self.assertIn("本周 0 / 7 天有学习", planner.week_note.text())
        self.assertIn("连续 0 天", planner.week_note.text())
        self.assertEqual(planner.week_summary_label.text(), "本周暂无累计活动记录")
        self.assertEqual(planner.record_card.isVisible(), False)
        self.assertEqual(planner.resume_card.isVisible(), False)

    # -------------------------------------------------------------
    # 2. Populated Database Tests
    # -------------------------------------------------------------
    def test_planner_view_populated_database_full_week(self):
        """PlannerView with rich multi-day activities when evaluated on Sunday (full week elapsed)."""
        from unittest.mock import patch
        import core.database
        import ui.planner_view

        # Freeze today as 2026-10-11 (Sunday)
        frozen_sunday = date(2026, 10, 11)

        class MockDateSunday(date):
            @classmethod
            def today(cls):
                return frozen_sunday

        monday = date(2026, 10, 5)

        # Populate week activities
        # Monday: shadowing
        self._log_activity("shadowing_minutes", 45, unit="分钟", logged_on=monday.isoformat())
        # Tuesday: vocab_triaged
        tuesday = monday + timedelta(days=1)
        self._log_activity("vocab_triaged", 120, unit="个", logged_on=tuesday.isoformat())
        # Wednesday: vocab_drilled
        wednesday = monday + timedelta(days=2)
        self._log_activity("vocab_drilled", 30, unit="个", logged_on=wednesday.isoformat())
        # Thursday: tasks
        thursday = monday + timedelta(days=3)
        self.database.connection.execute(
            "INSERT INTO tasks (title, completed, due_date, completed_at, created_at) VALUES (?, 1, ?, ?, ?)",
            ("第91届真题词汇整理", thursday.isoformat(), thursday.isoformat(), thursday.isoformat()),
        )
        self.database.connection.execute(
            "INSERT INTO tasks (title, completed, due_date, completed_at, created_at) VALUES (?, 1, ?, ?, ?)",
            ("听力精听第1-5题", thursday.isoformat(), thursday.isoformat(), thursday.isoformat()),
        )
        # Friday: material_opened
        friday = monday + timedelta(days=4)
        self._log_activity("material_opened", 5, unit="次", logged_on=friday.isoformat())
        # Saturday: rest day (no records)
        # Sunday (today): shadowing
        self._log_activity("shadowing_minutes", 15, unit="分钟", logged_on=frozen_sunday.isoformat())

        # Completed task from an earlier week (should contribute to total_completed but not current week tasks)
        past_week_date = monday - timedelta(days=7)
        self.database.connection.execute(
            "INSERT INTO tasks (title, completed, due_date, completed_at, created_at) VALUES (?, 1, ?, ?, ?)",
            ("旧周任务", past_week_date.isoformat(), past_week_date.isoformat(), past_week_date.isoformat()),
        )
        self.database.connection.commit()

        with patch("core.database.date", MockDateSunday), patch("ui.planner_view.date", MockDateSunday):
            planner = PlannerView(self.database)
            APP.processEvents()

            # Day cells length
            self.assertEqual(len(planner.day_cells), 7)

            # Check total_completed_value (3 completed tasks in total: 2 on Thu, 1 on past week)
            self.assertEqual(planner.total_completed_value.text(), "3 项")

            # Check week_note reflects 6 studied days (Mon..Fri + Sun)
            note_text = planner.week_note.text()
            self.assertIn("本周 6 / 7 天有学习", note_text)

            # Check week summary breakdown
            summary_text = planner.week_summary_label.text()
            self.assertIn("跟读 60 分钟", summary_text)  # 45 + 15
            self.assertIn("过词 120 个", summary_text)
            self.assertIn("专攻通过 30 个", summary_text)
            self.assertIn("完成任务 2 项", summary_text)

            # Monday cell verification (studied)
            mon_cell = planner.day_cells[0]
            self.assertEqual(mon_cell.weekday_label.text(), "周一")
            self.assertEqual(mon_cell.stat_label.text(), "跟读 45分")
            self.assertEqual(mon_cell.objectName(), "dayCellActive")
            mon_tooltip = mon_cell.toolTip()
            self.assertIn("• 跟读：45 分钟", mon_tooltip)
            self.assertTrue(len(mon_tooltip) > 0)

            # Tuesday cell verification (studied)
            tue_cell = planner.day_cells[1]
            self.assertEqual(tue_cell.weekday_label.text(), "周二")
            self.assertEqual(tue_cell.stat_label.text(), "过词 120个")
            self.assertEqual(tue_cell.objectName(), "dayCellActive")
            self.assertIn("• 过词：120 个", tue_cell.toolTip())

            # Wednesday cell verification (studied)
            wed_cell = planner.day_cells[2]
            self.assertEqual(wed_cell.weekday_label.text(), "周三")
            self.assertEqual(wed_cell.stat_label.text(), "专攻 30个")
            self.assertEqual(wed_cell.objectName(), "dayCellActive")
            self.assertIn("• 专攻通过：30 个", wed_cell.toolTip())

            # Thursday cell verification (studied tasks)
            thu_cell = planner.day_cells[3]
            self.assertEqual(thu_cell.weekday_label.text(), "周四")
            self.assertEqual(thu_cell.stat_label.text(), "任务 2项")
            self.assertEqual(thu_cell.objectName(), "dayCellActive")
            self.assertIn("• 完成任务：2 项", thu_cell.toolTip())

            # Friday cell verification (material opened)
            fri_cell = planner.day_cells[4]
            self.assertEqual(fri_cell.weekday_label.text(), "周五")
            self.assertEqual(fri_cell.stat_label.text(), "资料 5次")
            self.assertEqual(fri_cell.objectName(), "dayCellActive")
            self.assertIn("• 打开资料：5 次", fri_cell.toolTip())

            # Saturday cell verification (rest day)
            sat_cell = planner.day_cells[5]
            self.assertEqual(sat_cell.weekday_label.text(), "周六")
            self.assertEqual(sat_cell.stat_label.text(), "—")
            self.assertEqual(sat_cell.objectName(), "dayCell")
            self.assertIn("无学习记录（休息日）", sat_cell.toolTip())

            # Sunday cell verification (today with study)
            sun_cell = planner.day_cells[6]
            self.assertEqual(sun_cell.weekday_label.text(), "周日")
            self.assertEqual(sun_cell.stat_label.text(), "跟读 15分")
            self.assertEqual(sun_cell.objectName(), "dayCellToday")
            self.assertEqual(sun_cell.property("active"), "true")
            self.assertIn("(今天)", sun_cell.toolTip())

    def test_planner_view_future_days_handling(self):
        """PlannerView must render future days as dayCellFuture with '—' and '尚未到来' tooltip."""
        today = date.today()
        monday = today - timedelta(days=today.weekday())

        # If today is Monday..Saturday, there are future days in the week
        if today.weekday() < 6:
            planner = PlannerView(self.database)
            APP.processEvents()

            for offset in range(today.weekday() + 1, 7):
                cell = planner.day_cells[offset]
                self.assertEqual(cell.objectName(), "dayCellFuture")
                self.assertEqual(cell.stat_label.text(), "—")
                self.assertIn("（尚未到来）", cell.toolTip())

    # -------------------------------------------------------------
    # 3. Stress Test: Multiple Refreshes and showEvents
    # -------------------------------------------------------------
    def test_planner_view_refresh_and_showevent_stress(self):
        """Call showEvent and refresh hundreds of times to verify stability and leak freedom."""
        today = date.today()
        monday = today - timedelta(days=today.weekday())
        self._log_activity("shadowing_minutes", 20, unit="分钟", logged_on=monday.isoformat())
        self.database.add_task("测试压力任务 1")
        self.database.add_task("测试压力任务 2")

        planner = PlannerView(self.database)
        APP.processEvents()

        # Record initial widget counts
        initial_day_cells_count = len(planner.day_cells)
        initial_day_strip_count = planner.day_strip.count()
        initial_day_cell_widgets = len(planner.findChildren(DayCell))

        self.assertEqual(initial_day_cells_count, 7)
        self.assertEqual(initial_day_strip_count, 7)
        self.assertEqual(initial_day_cell_widgets, 7)

        # Fire 250 refresh calls
        for _ in range(250):
            planner.refresh()

        # Fire 250 showEvent calls
        show_event = QShowEvent()
        for _ in range(250):
            planner.showEvent(show_event)

        # Flush Qt deferred deletions
        APP.processEvents()
        APP.sendPostedEvents()

        # Verify absolutely no duplication of day cells
        self.assertEqual(len(planner.day_cells), 7, "day_cells length must remain strictly 7")
        self.assertEqual(planner.day_strip.count(), 7, "day_strip layout item count must remain strictly 7")
        self.assertEqual(len(planner.findChildren(DayCell)), 7, "DayCell widget count must remain strictly 7")

        # Verify values remain consistent
        self.assertEqual(planner.day_cells[0].stat_label.text(), "跟读 20分")
        self.assertIn("跟读 20 分钟", planner.week_summary_label.text())

    # -------------------------------------------------------------
    # 4. Live Interactivity & Signal Reactive Updates
    # -------------------------------------------------------------
    def test_planner_view_task_interaction_updates_h5(self):
        """Interactively adding and toggling tasks must immediately update week overview."""
        planner = PlannerView(self.database)
        APP.processEvents()

        self.assertEqual(planner.total_completed_value.text(), "0 项")

        # Add a task via UI input
        planner.task_input.setText("新口语打卡目标")
        planner.add_task()
        APP.processEvents()

        tasks = self.database.today_tasks()
        self.assertEqual(len(tasks), 1)
        task_id = tasks[0]["id"]
        self.assertEqual(planner.total_completed_value.text(), "0 项")

        # Toggle task to completed
        planner.toggle_task(task_id, True)
        APP.processEvents()

        # total_completed_value must update immediately
        self.assertEqual(planner.total_completed_value.text(), "1 项")
        # Today's cell must show task completed
        today_idx = date.today().weekday()
        today_cell = planner.day_cells[today_idx]
        self.assertIn("任务 1项", today_cell.stat_label.text())
        self.assertIn("• 完成任务：1 项", today_cell.toolTip())

        # Toggle task back to uncompleted
        planner.toggle_task(task_id, False)
        APP.processEvents()
        self.assertEqual(planner.total_completed_value.text(), "0 项")

        # Delete task
        planner.delete_task(task_id)
        APP.processEvents()
        self.assertEqual(len(self.database.today_tasks()), 0)

    # -------------------------------------------------------------
    # 5. Adversarial Edge Cases: Corrupt / Extreme Inputs
    # -------------------------------------------------------------
    def test_planner_view_adversarial_edge_cases(self):
        """Test null units, 0 amounts, negative amounts, unknown activity types, and volume."""
        today = date.today()

        # 1. Null unit in activity_log
        self.database.connection.execute(
            "INSERT INTO activity_log (activity_type, amount, unit, logged_on) VALUES (?, ?, ?, ?)",
            ("shadowing_minutes", 10, None, today.isoformat()),
        )
        # 2. 0 amount
        self.database.connection.execute(
            "INSERT INTO activity_log (activity_type, amount, unit, logged_on) VALUES (?, ?, ?, ?)",
            ("vocab_triaged", 0, "个", today.isoformat()),
        )
        # 3. Unknown activity type with large volume
        self.database.connection.execute(
            "INSERT INTO activity_log (activity_type, amount, unit, logged_on) VALUES (?, ?, ?, ?)",
            ("unregistered_extreme_action", 999999, "点", today.isoformat()),
        )
        self.database.connection.commit()

        planner = PlannerView(self.database)
        APP.processEvents()

        # Check that PlannerView does not crash
        self.assertEqual(len(planner.day_cells), 7)
        today_cell = planner.day_cells[today.weekday()]
        self.assertTrue(len(today_cell.toolTip()) > 0)
        # Verify fallback for shadowing with None unit uses default '分钟'
        self.assertIn("• 跟读：10 分钟", today_cell.toolTip())

        # Test 1000 activity log entries on a single day in batch
        batch = [("shadowing_minutes", 1, "分钟", today.isoformat(), f"{today.isoformat()} 12:00:00") for _ in range(1000)]
        self.database.connection.executemany(
            "INSERT INTO activity_log (activity_type, amount, unit, logged_on, created_at) VALUES (?, ?, ?, ?, ?)",
            batch,
        )
        self.database.connection.commit()

        planner.refresh()
        APP.processEvents()

        # Check aggregated amount: 10 + 1000 = 1010
        self.assertEqual(today_cell.stat_label.text(), "跟读 1010分")
        self.assertIn("跟读 1010 分钟", planner.week_summary_label.text())

    # -------------------------------------------------------------
    # 6. Integration Stress: StackedWidget & MainWindow Switching Lifecycle
    # -------------------------------------------------------------
    def test_planner_view_within_main_window_switching_stress(self):
        """Stress-test rapid page switching that repeatedly hides and shows PlannerView."""
        from PySide6.QtWidgets import QStackedWidget
        from ui.main_window import MainWindow

        # Test within isolated QStackedWidget with temp DB
        stacked = QStackedWidget()
        planner = PlannerView(self.database)
        dummy = QWidget()
        stacked.addWidget(planner)
        stacked.addWidget(dummy)
        stacked.setCurrentIndex(0)
        APP.processEvents()

        # Switch between views rapidly 50 times
        for _ in range(50):
            stacked.setCurrentIndex(1)
            APP.processEvents()
            stacked.setCurrentIndex(0)  # triggers showEvent -> refresh
            APP.processEvents()

        # Verify planner state remains intact
        self.assertEqual(len(planner.day_cells), 7)
        self.assertEqual(planner.day_strip.count(), 7)
        self.assertEqual(len(planner.findChildren(DayCell)), 7)

        # Test MainWindow lifecycle
        main_win = MainWindow()
        APP.processEvents()
        self.assertEqual(main_win.stacked_widget.currentIndex(), 0)
        self.assertEqual(len(main_win.planner_view.day_cells), 7)
        # Switch navigation tabs
        main_win.sidebar.setCurrentRow(1)
        APP.processEvents()
        main_win.sidebar.setCurrentRow(0)
        APP.processEvents()
        main_win.close()
        APP.processEvents()

    # -------------------------------------------------------------
    # 7. Adversarial Strings: Emojis, Script tags, and Long text
    # -------------------------------------------------------------
    def test_planner_view_adversarial_strings_and_emojis(self):
        """Adversarially inject emojis, XSS script tags, and long titles."""
        long_title = "🎯 " + "<script>alert('xss')</script> " * 10 + "🔥" * 20
        self.database.add_task(long_title)

        planner = PlannerView(self.database)
        APP.processEvents()

        self.assertEqual(len(planner.day_cells), 7)
        tasks = self.database.today_tasks()
        self.assertEqual(len(tasks), 1)

        # Toggle task
        planner.toggle_task(tasks[0]["id"], True)
        APP.processEvents()

        # Ensure tooltip formatting does not crash with emojis and script tags
        today_cell = planner.day_cells[date.today().weekday()]
        tooltip = today_cell.toolTip()
        self.assertIn("• 完成任务：1 项", tooltip)
        self.assertEqual(planner.total_completed_value.text(), "1 项")


if __name__ == "__main__":
    unittest.main(verbosity=2)

