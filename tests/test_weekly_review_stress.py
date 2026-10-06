"""Standalone adversarial stress-test suite for StudyDatabase.weekly_review_data().

Tests week boundaries, zero records, task-only, activity-only, mixed records,
mathematical invariants against week_progress() / _has_study(), accumulation of totals,
and adversarial edge cases.
"""

import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from unittest.mock import patch

# Ensure project root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.database import StudyDatabase


class BaseDBStressTest(unittest.TestCase):
    """Base test case providing an isolated temporary SQLite database and helper utilities."""

    def setUp(self):
        self.temp_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp_file.close()
        self.db_path = self.temp_file.name
        self.db = StudyDatabase(self.db_path)

    def tearDown(self):
        self.db.close()
        if os.path.exists(self.db_path):
            try:
                os.remove(self.db_path)
            except OSError:
                pass

    def insert_activity(self, activity_type, amount=1, unit=None, logged_on=None):
        """Helper to insert activity_log records for arbitrary historical/simulated dates."""
        if logged_on is None:
            logged_on = date.today().isoformat()
        elif isinstance(logged_on, date):
            logged_on = logged_on.isoformat()
        self.db.connection.execute(
            """
            INSERT INTO activity_log
                (activity_type, amount, unit, logged_on, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (activity_type, amount, unit, logged_on, f"{logged_on}T12:00:00"),
        )
        self.db.connection.commit()

    def insert_task(self, title, due_date=None, completed=0, completed_at=None):
        """Helper to insert tasks for arbitrary historical/simulated dates."""
        if due_date is None:
            due_date = date.today().isoformat()
        elif isinstance(due_date, date):
            due_date = due_date.isoformat()
        if isinstance(completed_at, date):
            completed_at = completed_at.isoformat()
        cur = self.db.connection.execute(
            """
            INSERT INTO tasks
                (title, due_date, completed, completed_at)
            VALUES (?, ?, ?, ?)
            """,
            (title, due_date, int(completed), completed_at),
        )
        self.db.connection.commit()
        return cur.lastrowid


class TestWeekBoundaries(BaseDBStressTest):
    """Stress-test week boundary calculations: Monday..Sunday, month ends, year ends, leap years."""

    def test_target_date_monday(self):
        """Target date on Monday: Monday should be day 0, Sunday day 6."""
        target = date(2026, 10, 5)  # Monday
        data = self.db.weekly_review_data(target)
        self.assertEqual(data["monday"], "2026-10-05")
        self.assertEqual(data["sunday"], "2026-10-11")
        self.assertEqual(len(data["days"]), 7)
        self.assertEqual(data["days"][0]["date"], "2026-10-05")
        self.assertEqual(data["days"][0]["weekday"], "周一")
        self.assertEqual(data["days"][6]["date"], "2026-10-11")
        self.assertEqual(data["days"][6]["weekday"], "周日")

    def test_target_date_wednesday(self):
        """Target date on Wednesday: Monday should still be day 0, Sunday day 6."""
        target = date(2026, 10, 7)  # Wednesday
        data = self.db.weekly_review_data(target)
        self.assertEqual(data["monday"], "2026-10-05")
        self.assertEqual(data["sunday"], "2026-10-11")
        self.assertEqual(data["days"][2]["date"], "2026-10-07")
        self.assertEqual(data["days"][2]["weekday"], "周三")

    def test_target_date_sunday(self):
        """Target date on Sunday: Monday should still be day 0, Sunday day 6."""
        target = date(2026, 10, 11)  # Sunday
        data = self.db.weekly_review_data(target)
        self.assertEqual(data["monday"], "2026-10-05")
        self.assertEqual(data["sunday"], "2026-10-11")
        self.assertEqual(data["days"][6]["date"], "2026-10-11")
        self.assertEqual(data["days"][6]["weekday"], "周日")

    def test_month_end_crossing(self):
        """Target date crossing month boundary (2026-09-30 Wed to 2026-10-01 Thu)."""
        target = date(2026, 10, 1)  # Thursday
        data = self.db.weekly_review_data(target)
        self.assertEqual(data["monday"], "2026-09-28")
        self.assertEqual(data["sunday"], "2026-10-04")
        expected_dates = [
            "2026-09-28", "2026-09-29", "2026-09-30",
            "2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04"
        ]
        expected_short_dates = [
            "09/28", "09/29", "09/30",
            "10/01", "10/02", "10/03", "10/04"
        ]
        self.assertEqual([d["date"] for d in data["days"]], expected_dates)
        self.assertEqual([d["short_date"] for d in data["days"]], expected_short_dates)

    def test_leap_year_february_crossing(self):
        """Target date crossing February in a leap year (2024-02-29 Thu)."""
        target = date(2024, 2, 29)  # Leap year Thursday
        data = self.db.weekly_review_data(target)
        self.assertEqual(data["monday"], "2024-02-26")
        self.assertEqual(data["sunday"], "2024-03-03")
        expected_dates = [
            "2024-02-26", "2024-02-27", "2024-02-28", "2024-02-29",
            "2024-03-01", "2024-03-02", "2024-03-03"
        ]
        self.assertEqual([d["date"] for d in data["days"]], expected_dates)

    def test_non_leap_year_february_crossing(self):
        """Target date crossing February in a non-leap year (2025-02-28 Fri)."""
        target = date(2025, 2, 28)  # Non-leap year Friday
        data = self.db.weekly_review_data(target)
        self.assertEqual(data["monday"], "2025-02-24")
        self.assertEqual(data["sunday"], "2025-03-02")
        expected_dates = [
            "2025-02-24", "2025-02-25", "2025-02-26", "2025-02-27", "2025-02-28",
            "2025-03-01", "2025-03-02"
        ]
        self.assertEqual([d["date"] for d in data["days"]], expected_dates)

    def test_year_end_crossing_dec_to_jan(self):
        """Target date crossing year boundary (2025-12-31 Wed to 2026-01-01 Thu)."""
        target = date(2026, 1, 1)  # Thursday
        data = self.db.weekly_review_data(target)
        self.assertEqual(data["monday"], "2025-12-29")
        self.assertEqual(data["sunday"], "2026-01-04")
        expected_dates = [
            "2025-12-29", "2025-12-30", "2025-12-31",
            "2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"
        ]
        self.assertEqual([d["date"] for d in data["days"]], expected_dates)

    def test_year_end_crossing_data_query(self):
        """Verify that BETWEEN query across year end correctly captures logs on both sides."""
        # 2025-12-30 and 2026-01-02
        self.insert_activity("shadowing_minutes", 20, "分钟", logged_on=date(2025, 12, 30))
        self.insert_activity("shadowing_minutes", 30, "分钟", logged_on=date(2026, 1, 2))
        # Outside week: 2025-12-28 (Sunday before) and 2026-01-05 (Monday after)
        self.insert_activity("shadowing_minutes", 100, "分钟", logged_on=date(2025, 12, 28))
        self.insert_activity("shadowing_minutes", 100, "分钟", logged_on=date(2026, 1, 5))

        data = self.db.weekly_review_data(date(2026, 1, 1))
        self.assertEqual(data["totals"]["shadowing_minutes"], 50)
        self.assertEqual(data["week_days_studied"], 2)

    def test_target_date_string_format(self):
        """weekly_review_data accepts string format 'YYYY-MM-DD'."""
        data_from_str = self.db.weekly_review_data("2026-10-06")
        data_from_date = self.db.weekly_review_data(date(2026, 10, 6))
        self.assertEqual(data_from_str["monday"], data_from_date["monday"])
        self.assertEqual(data_from_str["sunday"], data_from_date["sunday"])
        self.assertEqual(len(data_from_str["days"]), 7)


class TestDatabaseScenarios(BaseDBStressTest):
    """Test weekly_review_data under 0 records, tasks-only, activities-only, and mixed records."""

    def test_zero_records(self):
        """Database with 0 records must return clean zeroed aggregates."""
        data = self.db.weekly_review_data(date(2026, 10, 6))
        self.assertEqual(data["week_days_studied"], 0)
        self.assertEqual(data["week_total_days"], 7)
        self.assertEqual(data["streak"], 0)
        self.assertEqual(data["total_completed"], 0)
        self.assertEqual(
            data["totals"],
            {"shadowing_minutes": 0, "vocab_triaged": 0, "vocab_drilled": 0, "tasks": 0}
        )
        for day in data["days"]:
            self.assertFalse(day["has_study"])
            self.assertEqual(day["tasks_completed"], 0)
            self.assertEqual(day["activities"], {})

    def test_tasks_only_records(self):
        """Database with only task records."""
        mon = date(2026, 10, 5)
        wed = date(2026, 10, 7)
        sun = date(2026, 10, 11)
        prev_sun = date(2026, 10, 4)
        next_mon = date(2026, 10, 12)

        # In-week completed tasks
        self.insert_task("Mon Task 1", due_date=mon, completed=1, completed_at=mon)
        self.insert_task("Mon Task 2", due_date=mon, completed=1, completed_at=mon)
        self.insert_task("Wed Task 1", due_date=wed, completed=1, completed_at=wed)
        self.insert_task("Sun Task 1", due_date=sun, completed=1, completed_at=sun)

        # In-week uncompleted task
        self.insert_task("Wed Uncompleted", due_date=wed, completed=0)

        # Outside-week completed tasks
        self.insert_task("Prev Sun Task", due_date=prev_sun, completed=1, completed_at=prev_sun)
        self.insert_task("Next Mon Task", due_date=next_mon, completed=1, completed_at=next_mon)

        data = self.db.weekly_review_data(wed)
        self.assertEqual(data["week_days_studied"], 3)  # Mon, Wed, Sun
        self.assertEqual(data["totals"]["tasks"], 4)   # 2 on Mon, 1 on Wed, 1 on Sun
        self.assertEqual(data["totals"]["shadowing_minutes"], 0)
        self.assertEqual(data["totals"]["vocab_triaged"], 0)
        self.assertEqual(data["totals"]["vocab_drilled"], 0)
        self.assertEqual(data["total_completed"], 6)   # 4 in-week + 2 outside-week

        # Check day-level details
        self.assertTrue(data["days"][0]["has_study"])
        self.assertEqual(data["days"][0]["tasks_completed"], 2)
        self.assertFalse(data["days"][1]["has_study"])
        self.assertEqual(data["days"][1]["tasks_completed"], 0)
        self.assertTrue(data["days"][2]["has_study"])
        self.assertEqual(data["days"][2]["tasks_completed"], 1)
        self.assertTrue(data["days"][6]["has_study"])
        self.assertEqual(data["days"][6]["tasks_completed"], 1)

    def test_activities_only_records(self):
        """Database with only activity records."""
        mon = date(2026, 10, 5)
        tue = date(2026, 10, 6)
        thu = date(2026, 10, 8)
        prev_sun = date(2026, 10, 4)

        # Mon: multiple shadowing sessions
        self.insert_activity("shadowing_minutes", 15, "分钟", logged_on=mon)
        self.insert_activity("shadowing_minutes", 25, "分钟", logged_on=mon)
        # Tue: vocab triage and drill
        self.insert_activity("vocab_triaged", 30, "个", logged_on=tue)
        self.insert_activity("vocab_drilled", 10, "个", logged_on=tue)
        # Thu: material_opened (should count towards has_study, but not totals)
        self.insert_activity("material_opened", 3, "次", logged_on=thu)
        # Outside week activity
        self.insert_activity("shadowing_minutes", 50, "分钟", logged_on=prev_sun)

        data = self.db.weekly_review_data(mon)
        self.assertEqual(data["week_days_studied"], 3)  # Mon, Tue, Thu
        self.assertEqual(data["totals"]["shadowing_minutes"], 40)  # 15 + 25
        self.assertEqual(data["totals"]["vocab_triaged"], 30)
        self.assertEqual(data["totals"]["vocab_drilled"], 10)
        self.assertEqual(data["totals"]["tasks"], 0)
        self.assertEqual(data["total_completed"], 0)

        # Check material_opened has_study logic
        self.assertTrue(data["days"][3]["has_study"])
        self.assertIn("material_opened", data["days"][3]["activities"])
        self.assertEqual(data["days"][3]["activities"]["material_opened"]["amount"], 3)

    def test_mixed_records(self):
        """Database with mixed tasks and activities on the same and different days."""
        mon = date(2026, 10, 5)  # Both task + activity
        wed = date(2026, 10, 7)  # Only task
        fri = date(2026, 10, 9)  # Only activity
        sat = date(2026, 10, 10) # Both

        # Mon: 1 task + 20 min shadowing
        self.insert_task("Mon Task", due_date=mon, completed=1, completed_at=mon)
        self.insert_activity("shadowing_minutes", 20, "分钟", logged_on=mon)

        # Wed: 2 tasks
        self.insert_task("Wed Task 1", due_date=wed, completed=1, completed_at=wed)
        self.insert_task("Wed Task 2", due_date=wed, completed=1, completed_at=wed)

        # Fri: 40 vocab triage
        self.insert_activity("vocab_triaged", 40, "个", logged_on=fri)

        # Sat: 1 task + 15 vocab drilled
        self.insert_task("Sat Task", due_date=sat, completed=1, completed_at=sat)
        self.insert_activity("vocab_drilled", 15, "个", logged_on=sat)

        data = self.db.weekly_review_data(mon)
        # Mon, Wed, Fri, Sat = 4 studied days (no double counting on Mon or Sat)
        self.assertEqual(data["week_days_studied"], 4)
        self.assertEqual(data["totals"]["tasks"], 4)
        self.assertEqual(data["totals"]["shadowing_minutes"], 20)
        self.assertEqual(data["totals"]["vocab_triaged"], 40)
        self.assertEqual(data["totals"]["vocab_drilled"], 15)

        # Verify no double counting
        studied_count = sum(1 for d in data["days"] if d["has_study"])
        self.assertEqual(data["week_days_studied"], studied_count)


class TestMathematicalInvariant(BaseDBStressTest):
    """Test mathematical invariant: assert data['week_days_studied'] == db.week_progress().

    Also asserts that each day's day['has_study'] matches db._has_study(day_date).
    """

    def _assert_invariants(self, test_date):
        """Helper to assert invariant between weekly_review_data and week_progress / _has_study."""
        # 1. Day-level equivalence with _has_study
        data = self.db.weekly_review_data(test_date)
        for d in data["days"]:
            day_obj = date.fromisoformat(d["date"])
            self.assertEqual(
                d["has_study"],
                self.db._has_study(day_obj),
                f"Discrepancy on {d['date']}: day['has_study']={d['has_study']} vs _has_study={self.db._has_study(day_obj)}"
            )

        # 2. Week progress invariant when date.today() aligns with test_date
        with patch("core.database.date") as mock_date:
            mock_date.today.return_value = test_date
            mock_date.fromisoformat = date.fromisoformat
            mock_date.side_effect = lambda *args, **kwargs: date(*args, **kwargs)

            wp = self.db.week_progress()
            wrd = self.db.weekly_review_data(test_date)
            self.assertEqual(
                wrd["week_days_studied"],
                wp,
                f"Invariant violation on {test_date}: week_days_studied={wrd['week_days_studied']} vs week_progress={wp}"
            )

    def test_invariant_empty_db(self):
        """Invariant on empty DB across various weekdays."""
        for target in [date(2026, 10, 5), date(2026, 10, 7), date(2026, 10, 11)]:
            self._assert_invariants(target)

    def test_invariant_tasks_only(self):
        """Invariant on tasks-only DB."""
        target = date(2026, 10, 6)
        self.insert_task("Task", due_date=target, completed=1, completed_at=target)
        self._assert_invariants(target)

    def test_invariant_activities_only(self):
        """Invariant on activities-only DB."""
        target = date(2026, 10, 6)
        self.insert_activity("shadowing_minutes", 30, "分钟", logged_on=target)
        self._assert_invariants(target)

    def test_invariant_mixed_full_week(self):
        """Invariant across all 7 days with various activities."""
        monday = date(2026, 10, 5)
        for offset in range(7):
            d = monday + timedelta(days=offset)
            if offset % 2 == 0:
                self.insert_task(f"Task {offset}", due_date=d, completed=1, completed_at=d)
            if offset % 3 == 0:
                self.insert_activity("vocab_triaged", 10 * offset, "个", logged_on=d)

        for offset in range(7):
            self._assert_invariants(monday + timedelta(days=offset))

    def test_invariant_month_end_crossing(self):
        """Invariant across month end (2026-09-28 to 2026-10-04)."""
        d1 = date(2026, 9, 30)
        d2 = date(2026, 10, 1)
        self.insert_activity("shadowing_minutes", 20, "分钟", logged_on=d1)
        self.insert_task("Task", due_date=d2, completed=1, completed_at=d2)
        self._assert_invariants(d1)
        self._assert_invariants(d2)

    def test_invariant_year_end_crossing(self):
        """Invariant across year end (2025-12-29 to 2026-01-04)."""
        d1 = date(2025, 12, 31)
        d2 = date(2026, 1, 1)
        self.insert_activity("shadowing_minutes", 20, "分钟", logged_on=d1)
        self.insert_activity("vocab_drilled", 15, "个", logged_on=d2)
        self._assert_invariants(d1)
        self._assert_invariants(d2)


class TestAccumulationCorrectness(BaseDBStressTest):
    """Stress-test accumulation of totals: shadowing, vocab triage, vocab drills, tasks."""

    def test_high_volume_accumulation(self):
        """Stress test with high volume of events."""
        mon = date(2026, 10, 5)

        # 50 shadowing events on Monday (each 2 mins -> total 100)
        for _ in range(50):
            self.insert_activity("shadowing_minutes", 2, "分钟", logged_on=mon)

        # 30 vocab triage events across Tuesday and Wednesday (total 300)
        tue = date(2026, 10, 6)
        wed = date(2026, 10, 7)
        for _ in range(15):
            self.insert_activity("vocab_triaged", 10, "个", logged_on=tue)
            self.insert_activity("vocab_triaged", 10, "个", logged_on=wed)

        # 20 vocab drilled events on Thursday (total 200)
        thu = date(2026, 10, 8)
        for _ in range(20):
            self.insert_activity("vocab_drilled", 10, "个", logged_on=thu)

        # 30 tasks completed across Friday, Saturday, Sunday (5 on Fri, 10 on Sat, 15 on Sun)
        for offset, day in enumerate([date(2026, 10, 9), date(2026, 10, 10), date(2026, 10, 11)]):
            for i in range(5 + offset * 5):
                self.insert_task(f"Task {offset}_{i}", due_date=day, completed=1, completed_at=day)

        data = self.db.weekly_review_data(mon)
        self.assertEqual(data["totals"]["shadowing_minutes"], 100)
        self.assertEqual(data["totals"]["vocab_triaged"], 300)
        self.assertEqual(data["totals"]["vocab_drilled"], 200)
        self.assertEqual(data["totals"]["tasks"], 30)
        self.assertEqual(data["week_days_studied"], 7)

    def test_unit_normalization(self):
        """Test unit handling: 'times' normalized to '次', defaults applied."""
        mon = date(2026, 10, 5)
        self.insert_activity("material_opened", 5, "times", logged_on=mon)
        data = self.db.weekly_review_data(mon)
        day_acts = data["days"][0]["activities"]
        self.assertEqual(day_acts["material_opened"]["unit"], "次")
        self.assertEqual(day_acts["material_opened"]["amount"], 5)


class TestAdversarialEdgeCases(BaseDBStressTest):
    """Adversarial stress tests designed to expose edge cases, latent bugs, and contract violations."""

    def test_contract_schema_compliance(self):
        """Verify all keys and types match the PROJECT.md interface contract."""
        data = self.db.weekly_review_data(date(2026, 10, 6))
        self.assertIsInstance(data["monday"], str)
        self.assertIsInstance(data["sunday"], str)
        self.assertEqual(len(data["monday"]), 10)  # "YYYY-MM-DD"
        self.assertEqual(len(data["sunday"]), 10)
        self.assertIsInstance(data["days"], list)
        self.assertEqual(len(data["days"]), 7)
        self.assertIsInstance(data["week_days_studied"], int)
        self.assertIsInstance(data["week_total_days"], int)
        self.assertEqual(data["week_total_days"], 7)
        self.assertIsInstance(data["streak"], int)
        self.assertIsInstance(data["total_completed"], int)
        self.assertIsInstance(data["totals"], dict)
        for k in ("shadowing_minutes", "vocab_triaged", "vocab_drilled", "tasks"):
            self.assertIn(k, data["totals"])
            self.assertIsInstance(data["totals"][k], int)

        for d in data["days"]:
            self.assertIsInstance(d["date"], str)
            self.assertIsInstance(d["short_date"], str)
            self.assertIsInstance(d["weekday"], str)
            self.assertIsInstance(d["is_today"], bool)
            self.assertIsInstance(d["is_future"], bool)
            self.assertIsInstance(d["has_study"], bool)
            self.assertIsInstance(d["activities"], dict)
            self.assertIsInstance(d["tasks_completed"], int)

    def test_target_date_as_datetime_finding(self):
        """CHALLENGE FINDING: Passing datetime.datetime produces non-contract ISO format with time.

        If target_date is datetime(2026, 10, 6, 12, 0), monday will be '2026-10-05T12:00:00'
        instead of '2026-10-05', causing lexicographical query mismatch in SQLite.
        """
        now = datetime(2026, 10, 6, 15, 30, 0)
        data = self.db.weekly_review_data(now)
        # Check if contract "YYYY-MM-DD" is maintained or broken
        # In current implementation, monday has length > 10 because of timestamp
        is_broken = len(data["monday"]) > 10
        self.assertTrue(is_broken, "Observed behavior: datetime target_date retains time component.")

    def test_uncompleted_task_with_completed_at_finding(self):
        """CHALLENGE FINDING: Discrepancy between _has_study() and weekly_review_data()
        when a task has completed = 0 but completed_at is set.

        _has_study() queries 'WHERE completed_at = ?' without 'completed = 1'.
        weekly_review_data() queries 'WHERE completed = 1 AND completed_at BETWEEN ? AND ?'.
        """
        today = date.today().isoformat()
        self.insert_task("Inconsistent Task", due_date=today, completed=0, completed_at=today)

        has_study_result = self.db._has_study(date.today())
        data = self.db.weekly_review_data()
        today_data = next(d for d in data["days"] if d["date"] == today)

        # Discrepancy observed:
        self.assertTrue(has_study_result)
        self.assertFalse(today_data["has_study"])
        self.assertNotEqual(data["week_days_studied"], self.db.week_progress())

    def test_zero_amount_activity_log(self):
        """Activity log with amount = 0 still registers has_study = True."""
        today = date.today()
        self.insert_activity("shadowing_minutes", 0, "分钟", logged_on=today)

        data = self.db.weekly_review_data(today)
        today_data = next(d for d in data["days"] if d["date"] == today.isoformat())
        self.assertTrue(today_data["has_study"])
        self.assertEqual(data["totals"]["shadowing_minutes"], 0)


def run_tests():
    suite = unittest.TestLoader().loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return result


if __name__ == "__main__":
    result = run_tests()
    sys.exit(0 if result.wasSuccessful() else 1)
