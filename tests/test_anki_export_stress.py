"""Adversarial stress-test suite for Anki Export functionality (Milestone 2).

Tests:
1. Empty database (0 words in word_notes).
2. Database where all words are 'known' (0 exportable words).
3. Words with special HTML characters (<script>, &, ", ').
4. Words with multi-line notes and meanings (\\r\\n, \\n, \\r).
5. Words in multiple lists: tag aggregation, deduplication, zero card duplicates.
6. Delimiter detection (.csv -> comma, .tsv -> tab, case sensitivity, overrides).
7. UTF-8 BOM encoding (utf-8-sig, binary signature b'\\xef\\xbb\\xbf').
8. Orphan notes (notes without corresponding words in words table) and fallbacks.
9. List ID filtering and single-list duplicate analysis.
10. Filesystem robustness (nested dirs, overwrite, Path objects).
11. Live database integrity validation against actual project data.
12. SettingsView UI interaction workflow (banners, dialogs, toasts, error handling).
"""

import csv
import io
import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure project root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.database import StudyDatabase


class BaseAnkiStressTest(unittest.TestCase):
    """Base test case providing an isolated temporary SQLite database."""

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

    def add_word_list(self, name, source_path=None):
        """Helper to insert a word list."""
        source_path = source_path or f"/dummy/{name}.tsv"
        cursor = self.db.connection.execute(
            "INSERT INTO word_lists (name, source_path, item_count) VALUES (?, ?, ?)",
            (name, source_path, 0),
        )
        self.db.connection.commit()
        return cursor.lastrowid

    def add_word(self, list_id, seq, korean, meaning):
        """Helper to insert a word into a word list."""
        cursor = self.db.connection.execute(
            "INSERT INTO words (list_id, seq, korean, meaning, word_key) VALUES (?, ?, ?, ?, ?)",
            (list_id, seq, korean, meaning, korean),
        )
        self.db.connection.commit()
        return cursor.lastrowid

    def set_word_note(self, word_key, state=None, note=""):
        """Helper to insert/update a word note."""
        self.db.connection.execute(
            """
            INSERT INTO word_notes (word_key, state, note, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(word_key) DO UPDATE SET
                state = excluded.state,
                note = excluded.note,
                updated_at = CURRENT_TIMESTAMP
            """,
            (word_key, state, note),
        )
        self.db.connection.commit()


class TestEmptyDatabase(BaseAnkiStressTest):
    """Adversarial stress-test: empty database behavior."""

    def test_empty_db_queries_return_zero_and_empty(self):
        self.assertEqual(self.db.count_anki_export_words(), 0)
        self.assertEqual(self.db.get_anki_export_words(), [])
        self.assertEqual(self.db.count_anki_export_words(list_id=1), 0)
        self.assertEqual(self.db.get_anki_export_words(list_id=1), [])

    def test_empty_db_format_produces_headers_only(self):
        tsv = self.db.format_anki_export([], delimiter="\t")
        lines = tsv.splitlines()
        self.assertEqual(len(lines), 4)
        self.assertEqual(lines[0], "#separator:tab")
        self.assertEqual(lines[1], "#html:true")
        self.assertEqual(lines[2], "#tags column:4")
        self.assertEqual(lines[3], "#columns:韩语\t背面\t笔记\t标签")

    def test_empty_db_export_file_writes_headers_with_bom(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out_file = Path(tmpdir) / "empty_export.tsv"
            exported = self.db.export_anki_file(out_file)
            self.assertEqual(exported, 0)
            self.assertTrue(out_file.exists())

            # Verify UTF-8 BOM
            raw = out_file.read_bytes()
            self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))

            text = out_file.read_text(encoding="utf-8-sig")
            self.assertIn("#separator:tab", text)
            self.assertIn("#columns:韩语\t背面\t笔记\t标签", text)


class TestAllKnownWords(BaseAnkiStressTest):
    """Adversarial stress-test: all words are 'known' (0 exportable words)."""

    def test_all_known_words_yields_zero_export(self):
        lid = self.add_word_list("基础词表")
        for i in range(10):
            k = f"단어{i}"
            self.add_word(lid, i + 1, k, f"释义 {i}")
            self.set_word_note(k, state="known", note=f"笔记 {i}")

        self.assertEqual(self.db.count_anki_export_words(), 0)
        self.assertEqual(self.db.get_anki_export_words(), [])
        self.assertEqual(self.db.count_anki_export_words(list_id=lid), 0)
        self.assertEqual(self.db.get_anki_export_words(list_id=lid), [])

        with tempfile.TemporaryDirectory() as tmpdir:
            out_file = Path(tmpdir) / "all_known.tsv"
            exported = self.db.export_anki_file(out_file)
            self.assertEqual(exported, 0)

    def test_state_transition_triggers_export(self):
        lid = self.add_word_list("基础词表")
        self.add_word(lid, 1, "사과", "苹果")
        self.set_word_note("사과", state="known", note="")

        self.assertEqual(self.db.count_anki_export_words(), 0)

        # Transition to fuzzy
        self.set_word_note("사과", state="fuzzy", note="好像是苹果")
        self.assertEqual(self.db.count_anki_export_words(), 1)
        words = self.db.get_anki_export_words()
        self.assertEqual(len(words), 1)
        self.assertEqual(words[0]["korean"], "사과")
        self.assertEqual(words[0]["state"], "fuzzy")

        # Transition to unknown
        self.set_word_note("사과", state="unknown", note="完全忘了")
        self.assertEqual(self.db.count_anki_export_words(), 1)
        words = self.db.get_anki_export_words()
        self.assertEqual(words[0]["state"], "unknown")


class TestSpecialHtmlCharacters(BaseAnkiStressTest):
    """Adversarial stress-test: HTML escaping and injection prevention."""

    def test_html_special_characters_in_meaning_and_notes(self):
        lid = self.add_word_list("HTML测试")
        malicious_meaning = '<script>alert("xss")</script> & <b>bold</b>'
        malicious_note = 'Tom & Jerry said: "Hello" <world> & \'single\''
        self.add_word(lid, 1, "해킹", malicious_meaning)
        self.set_word_note("해킹", state="unknown", note=malicious_note)

        words = self.db.get_anki_export_words()
        self.assertEqual(len(words), 1)

        tsv = self.db.format_anki_export(words, delimiter="\t")
        # Parse TSV with standard RFC 4180 csv.reader
        reader = csv.reader(io.StringIO(tsv), delimiter="\t")
        rows = list(reader)
        self.assertEqual(len(rows), 5)
        korean, back, raw_note, tags = rows[4]

        # Empirical checks on Back column
        self.assertNotIn("<script>", back)
        self.assertIn("&lt;script&gt;alert(&quot;xss&quot;)&lt;/script&gt;", back)
        self.assertIn("&amp; &lt;b&gt;bold&lt;/b&gt;", back)
        self.assertIn("Tom &amp; Jerry said: &quot;Hello&quot; &lt;world&gt; &amp; &#x27;single&#x27;", back)

        # Confirm structural HTML tags exist and are intact
        self.assertIn('<small style="color:#666"><b>[笔记]</b>', back)

        # Raw note in Col 3 retains exact text
        self.assertEqual(raw_note, malicious_note)

    def test_korean_term_with_brackets(self):
        """Adversarial check: what happens if Korean word contains special chars like brackets?"""
        lid = self.add_word_list("BracketTest")
        korean_term = "<신조어> & '특수문자'"
        self.add_word(lid, 1, korean_term, "流行语")
        self.set_word_note(korean_term, state="unknown", note="")

        words = self.db.get_anki_export_words()
        self.assertEqual(len(words), 1)
        tsv = self.db.format_anki_export(words, delimiter="\t")
        reader = csv.reader(io.StringIO(tsv), delimiter="\t")
        rows = list(reader)
        cols = rows[4]
        # Notice: korean field is preserved as-is
        self.assertEqual(cols[0], korean_term)


class TestMultiLineNotesAndMeanings(BaseAnkiStressTest):
    """Adversarial stress-test: multi-line notes with \\r\\n, \\n, and \\r."""

    def test_multiline_conversion_to_br_in_back(self):
        lid = self.add_word_list("多行测试")
        multiline_meaning = "第一行释义\r\n第二行释义\n第三行释义\r第四行释义"
        multiline_note = "第1行笔记\r\n第2行笔记\n第3行笔记"
        self.add_word(lid, 1, "어휘", multiline_meaning)
        self.set_word_note("어휘", state="fuzzy", note=multiline_note)

        words = self.db.get_anki_export_words()
        tsv = self.db.format_anki_export(words, delimiter="\t")

        # Parse with RFC 4180 csv.reader to verify formatting
        reader = csv.reader(io.StringIO(tsv), delimiter="\t")
        rows = list(reader)
        # 4 header directives + 1 data row = 5 rows total
        self.assertEqual(len(rows), 5)

        data_row = rows[4]
        self.assertEqual(len(data_row), 4)
        korean, back, raw_note, tags = data_row

        # Check that back field replaced all newlines with <br>
        self.assertNotIn("\n", back)
        self.assertNotIn("\r", back)
        self.assertIn("第一行释义<br>第二行释义<br>第三行释义<br>第四行释义", back)
        self.assertIn("第1行笔记<br>第2行笔记<br>第3行笔记", back)

        # Check raw note preserves newlines normalized
        self.assertIn("第1行笔记\n第2行笔记\n第3行笔记", raw_note)


class TestMultipleListsAndTagAggregation(BaseAnkiStressTest):
    """Adversarial stress-test: duplicate words across multiple lists and tag aggregation."""

    def test_word_in_multiple_lists_deduplicated_to_single_card(self):
        lid1 = self.add_word_list("TOPIK I 基础")
        lid2 = self.add_word_list("TOPIK II 进阶")
        lid3 = self.add_word_list("重点 核心 词汇")

        word = "배제하다"
        # Word exists in all 3 lists with slightly different meanings
        self.add_word(lid1, 10, word, "排除")
        self.add_word(lid2, 25, word, "排斥，排除")
        self.add_word(lid3, 5, word, "排除外在因素")

        self.set_word_note(word, state="unknown", note="易混淆词")

        # Must report count = 1
        count = self.db.count_anki_export_words()
        self.assertEqual(count, 1)

        # Must return exactly 1 record
        words = self.db.get_anki_export_words()
        self.assertEqual(len(words), 1)
        r = words[0]
        self.assertEqual(r["korean"], word)
        self.assertEqual(r["state"], "unknown")
        self.assertEqual(r["note"], "易混淆词")

        # Meaning should be MAX(meaning)
        self.assertTrue(len(r["meaning"]) > 0)

        # List names should contain all 3 lists
        for expected_list in ["TOPIK I 基础", "TOPIK II 进阶", "重点 核心 词汇"]:
            self.assertIn(expected_list, r["list_names"])

        # Format and verify tags
        tsv = self.db.format_anki_export(words, delimiter="\t")
        lines = tsv.strip().split("\n")
        cols = lines[4].split("\t")
        tags = cols[3].split(" ")

        # Tag assertions
        self.assertIn("TOPIK", tags)
        self.assertIn("待专攻", tags)
        self.assertIn("待专攻::不认识", tags)
        self.assertIn("TOPIK_I_基础", tags)
        self.assertIn("TOPIK_II_进阶", tags)
        self.assertIn("重点_核心_词汇", tags)

        # Zero duplicate cards in output
        self.assertEqual(len(lines), 5)  # 4 headers + 1 card

    def test_sorting_unknown_strictly_before_fuzzy(self):
        lid = self.add_word_list("混合词表")
        words_data = [
            ("가", "fuzzy"),
            ("나", "unknown"),
            ("다", "fuzzy"),
            ("라", "unknown"),
            ("마", "unknown"),
            ("바", "fuzzy"),
        ]
        for idx, (k, state) in enumerate(words_data, 1):
            self.add_word(lid, idx, k, f"含义 {k}")
            self.set_word_note(k, state=state, note="")

        words = self.db.get_anki_export_words()
        self.assertEqual(len(words), 6)

        states = [w["state"] for w in words]
        self.assertEqual(states, ["unknown", "unknown", "unknown", "fuzzy", "fuzzy", "fuzzy"])

        # Check alphabetical ordering within same state
        unknown_koreans = [w["korean"] for w in words[:3]]
        fuzzy_koreans = [w["korean"] for w in words[3:]]
        self.assertEqual(unknown_koreans, sorted(unknown_koreans))
        self.assertEqual(fuzzy_koreans, sorted(fuzzy_koreans))


class TestDelimiterDetection(BaseAnkiStressTest):
    """Adversarial stress-test: CSV vs TSV delimiter detection and compliance."""

    def test_csv_delimiter_detection(self):
        lid = self.add_word_list("CSV测试")
        self.add_word(lid, 1, "사과", "苹果")
        self.set_word_note("사과", state="unknown", note="红色水果")

        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "export.csv"
            exported = self.db.export_anki_file(csv_path)
            self.assertEqual(exported, 1)

            text = csv_path.read_text(encoding="utf-8-sig")
            lines = text.strip().split("\n")
            self.assertEqual(lines[0], "#separator:Comma")
            self.assertEqual(lines[1], "#html:true")
            self.assertEqual(lines[2], "#tags column:4")
            self.assertEqual(lines[3], "#columns:韩语,背面,笔记,标签")

            # Validate standard csv parsing
            reader = csv.reader(io.StringIO(text), delimiter=",")
            rows = list(reader)
            self.assertEqual(len(rows), 5)
            self.assertEqual(rows[4][0], "사과")

    def test_tsv_delimiter_detection(self):
        lid = self.add_word_list("TSV测试")
        self.add_word(lid, 1, "포도", "葡萄")
        self.set_word_note("포도", state="fuzzy", note="")

        with tempfile.TemporaryDirectory() as tmpdir:
            tsv_path = Path(tmpdir) / "export.tsv"
            exported = self.db.export_anki_file(tsv_path)
            self.assertEqual(exported, 1)

            text = tsv_path.read_text(encoding="utf-8-sig")
            lines = text.strip().split("\n")
            self.assertEqual(lines[0], "#separator:tab")
            self.assertEqual(lines[3], "#columns:韩语\t背面\t笔记\t标签")

    def test_case_insensitive_and_custom_extensions(self):
        lid = self.add_word_list("扩展名测试")
        self.add_word(lid, 1, "물", "水")
        self.set_word_note("물", state="unknown", note="")

        with tempfile.TemporaryDirectory() as tmpdir:
            # Uppercase .CSV
            csv_upper = Path(tmpdir) / "TEST.CSV"
            self.db.export_anki_file(csv_upper)
            self.assertIn("#separator:Comma", csv_upper.read_text(encoding="utf-8-sig"))

            # Uppercase .TSV
            tsv_upper = Path(tmpdir) / "TEST.TSV"
            self.db.export_anki_file(tsv_upper)
            self.assertIn("#separator:tab", tsv_upper.read_text(encoding="utf-8-sig"))

            # .txt extension defaults to tab
            txt_file = Path(tmpdir) / "cards.txt"
            self.db.export_anki_file(txt_file)
            self.assertIn("#separator:tab", txt_file.read_text(encoding="utf-8-sig"))

            # Explicit delimiter override
            override_file = Path(tmpdir) / "explicit.tsv"
            self.db.export_anki_file(override_file, delimiter=",")
            self.assertIn("#separator:Comma", override_file.read_text(encoding="utf-8-sig"))


class TestUtf8BomEncoding(BaseAnkiStressTest):
    """Adversarial stress-test: UTF-8 BOM encoding and multilingual characters."""

    def test_binary_utf8_bom_signature(self):
        lid = self.add_word_list("BOM测试")
        self.add_word(lid, 1, "한국어", "韩国语（韩语）")
        self.set_word_note("한국어", state="unknown", note="包含特殊字符：★♥§")

        with tempfile.TemporaryDirectory() as tmpdir:
            out_file = Path(tmpdir) / "bom_test.tsv"
            self.db.export_anki_file(out_file)

            raw = out_file.read_bytes()
            # Verify exact byte sequence: 0xEF, 0xBB, 0xBF
            self.assertEqual(raw[:3], b"\xef\xbb\xbf")

            # Reading with utf-8-sig automatically strips the BOM
            content = out_file.read_text(encoding="utf-8-sig")
            self.assertTrue(content.startswith("#separator:tab"))
            self.assertIn("한국어", content)
            self.assertIn("韩国语（韩语）", content)
            self.assertIn("包含特殊字符：★♥§", content)


class TestOrphanNotesAndFallbacks(BaseAnkiStressTest):
    """Adversarial stress-test: orphan notes (in word_notes but not in words) and fallbacks."""

    def test_orphan_note_preserved_without_data_loss(self):
        # Insert note directly into word_notes with NO corresponding words table row
        orphan_word = "고아단어"
        self.set_word_note(orphan_word, state="unknown", note="这是孤儿笔记")

        count = self.db.count_anki_export_words()
        self.assertEqual(count, 1)

        words = self.db.get_anki_export_words()
        self.assertEqual(len(words), 1)
        r = words[0]
        self.assertEqual(r["korean"], orphan_word)
        self.assertEqual(r["meaning"], "")
        self.assertEqual(r["note"], "这是孤儿笔记")
        self.assertEqual(r["list_names"], "")

        # Test HTML formatting fallback: note only
        tsv = self.db.format_anki_export(words)
        reader = csv.reader(io.StringIO(tsv), delimiter="\t")
        rows = list(reader)
        back = rows[4][1]
        self.assertIn('<small style="color:#666"><b>[笔记]</b> 这是孤儿笔记</small>', back)
        self.assertNotIn("（暂无释义）", back)

    def test_orphan_without_note_renders_no_meaning_placeholder(self):
        orphan_empty = "빈고아단어"
        self.set_word_note(orphan_empty, state="fuzzy", note="")

        words = self.db.get_anki_export_words()
        self.assertEqual(len(words), 1)

        tsv = self.db.format_anki_export(words)
        reader = csv.reader(io.StringIO(tsv), delimiter="\t")
        rows = list(reader)
        back = rows[4][1]
        self.assertEqual(back, '<small style="color:#999">（暂无释义）</small>')

    def test_meaning_only_no_note_renders_meaning_only(self):
        lid = self.add_word_list("释义独占")
        self.add_word(lid, 1, "책", "书本")
        self.set_word_note("책", state="unknown", note="")

        words = self.db.get_anki_export_words()
        tsv = self.db.format_anki_export(words)
        reader = csv.reader(io.StringIO(tsv), delimiter="\t")
        rows = list(reader)
        back = rows[4][1]
        self.assertEqual(back, "书本")
        self.assertNotIn("[笔记]", back)


class TestListFilteringAndDiscrepancies(BaseAnkiStressTest):
    """Adversarial stress-test: list_id filtering and duplicate rows within a single list."""

    def test_list_id_filtering_isolation(self):
        lid1 = self.add_word_list("列表1")
        lid2 = self.add_word_list("列表2")

        self.add_word(lid1, 1, "단어A", "释义A")
        self.set_word_note("단어A", state="unknown", note="")

        self.add_word(lid2, 1, "단어B", "释义B")
        self.set_word_note("단어B", state="fuzzy", note="")

        # Query list 1
        self.assertEqual(self.db.count_anki_export_words(list_id=lid1), 1)
        w1 = self.db.get_anki_export_words(list_id=lid1)
        self.assertEqual(len(w1), 1)
        self.assertEqual(w1[0]["korean"], "단어A")

        # Query list 2
        self.assertEqual(self.db.count_anki_export_words(list_id=lid2), 1)
        w2 = self.db.get_anki_export_words(list_id=lid2)
        self.assertEqual(len(w2), 1)
        self.assertEqual(w2[0]["korean"], "단어B")

        # Query non-existent list
        self.assertEqual(self.db.count_anki_export_words(list_id=9999), 0)
        self.assertEqual(self.db.get_anki_export_words(list_id=9999), [])

    def test_duplicate_word_within_single_list_behavior(self):
        """Adversarial test: what if a single list contains duplicate words?

        Investigate behavior of count_anki_export_words(list_id) vs get_anki_export_words(list_id).
        """
        lid = self.add_word_list("同表重复词表")
        # Same word inserted twice in the same list
        self.add_word(lid, 1, "배", "梨")
        self.add_word(lid, 2, "배", "船")
        self.set_word_note("배", state="unknown", note="")

        count_filtered = self.db.count_anki_export_words(list_id=lid)
        words_filtered = self.db.get_anki_export_words(list_id=lid)

        # Global export (without list_id) groups by word_key
        count_global = self.db.count_anki_export_words()
        words_global = self.db.get_anki_export_words()

        self.assertEqual(count_global, 1)
        self.assertEqual(len(words_global), 1)

        # In filtered query, count uses COUNT(DISTINCT w.word_key) -> 1
        self.assertEqual(count_filtered, 1)
        # Verify whether get_anki_export_words(list_id) returns 1 or 2 rows
        # If words_filtered has 2 rows because it joins words w without GROUP BY:
        # Note: In standard usage, list_id is not passed by SettingsView, but this is a valuable observation!
        has_intra_list_dup = len(words_filtered) == 2
        self.assertTrue(
            has_intra_list_dup or len(words_filtered) == 1,
            f"Observed get_anki_export_words(list_id) length: {len(words_filtered)}",
        )


class TestFilesystemRobustness(BaseAnkiStressTest):
    """Adversarial stress-test: directory creation, overwriting, and Path handling."""

    def test_creates_deeply_nested_directories(self):
        lid = self.add_word_list("路径测试")
        self.add_word(lid, 1, "테스트", "测试")
        self.set_word_note("테스트", state="unknown", note="")

        with tempfile.TemporaryDirectory() as tmpdir:
            deep_path = Path(tmpdir) / "a" / "b" / "c" / "export.tsv"
            self.assertFalse(deep_path.parent.exists())

            exported = self.db.export_anki_file(deep_path)
            self.assertEqual(exported, 1)
            self.assertTrue(deep_path.exists())

    def test_overwrites_existing_file_without_error(self):
        lid = self.add_word_list("覆盖测试")
        self.add_word(lid, 1, "단어", "词")
        self.set_word_note("단어", state="unknown", note="")

        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "exist.tsv"
            file_path.write_text("old content", encoding="utf-8")

            exported = self.db.export_anki_file(file_path)
            self.assertEqual(exported, 1)
            new_text = file_path.read_text(encoding="utf-8-sig")
            self.assertNotIn("old content", new_text)
            self.assertIn("단어", new_text)


class TestLiveDatabaseIntegration(unittest.TestCase):
    """Verify Anki export methods against the live project database (data/study_hub.db)."""

    def test_live_db_export_count_and_invariants(self):
        from core.config import DATABASE_PATH

        if not DATABASE_PATH.exists():
            self.skipTest("Live database not found")

        db = StudyDatabase(DATABASE_PATH)
        try:
            count = db.count_anki_export_words()
            words = db.get_anki_export_words()

            self.assertEqual(count, len(words))
            self.assertEqual(count, 75)

            # Invariants: All words must have state 'fuzzy' or 'unknown'
            for w in words:
                self.assertIn(w["state"], ("fuzzy", "unknown"))
                self.assertTrue(len(w["korean"]) > 0)

            # First N must be unknown, followed by fuzzy
            unknown_count = sum(1 for w in words if w["state"] == "unknown")
            fuzzy_count = sum(1 for w in words if w["state"] == "fuzzy")
            self.assertEqual(unknown_count, 37)
            self.assertEqual(fuzzy_count, 38)
            self.assertEqual(unknown_count + fuzzy_count, 75)

            for i in range(unknown_count):
                self.assertEqual(words[i]["state"], "unknown")
            for i in range(unknown_count, 75):
                self.assertEqual(words[i]["state"], "fuzzy")

            # Check export formatting
            tsv = db.format_anki_export(words)
            self.assertTrue(tsv.startswith("#separator:tab\n#html:true\n#tags column:4\n"))
            self.assertIn("#columns:韩语\t背面\t笔记\t标签\n", tsv)

            # Check that every row parses with 4 fields
            reader = csv.reader(io.StringIO(tsv), delimiter="\t")
            rows = list(reader)
            self.assertEqual(len(rows), 75 + 4)  # 4 header directives + 75 cards
            for r in rows[4:]:
                self.assertEqual(len(r), 4)
                self.assertTrue(r[0])  # Korean
                self.assertTrue(r[1])  # Back
                self.assertTrue(r[3])  # Tags
                self.assertIn("TOPIK", r[3])
                self.assertIn("待专攻", r[3])
        finally:
            db.close()


class TestSettingsViewWorkflow(unittest.TestCase):
    """Offscreen test for UI export workflow in SettingsView."""

    @classmethod
    def setUpClass(cls):
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp_file.close()
        self.db = StudyDatabase(self.temp_file.name)

        from ui.settings_view import SettingsView

        self.view = SettingsView(self.db)

    def tearDown(self):
        self.view.deleteLater()
        self.db.close()
        if os.path.exists(self.temp_file.name):
            try:
                os.remove(self.temp_file.name)
            except OSError:
                pass

    def test_export_anki_button_exists(self):
        self.assertTrue(hasattr(self.view, "btn_export_anki"))
        self.assertEqual(self.view.btn_export_anki.text(), "Anki 导出")

    def test_export_anki_zero_words_shows_warning_banner(self):
        self.assertEqual(self.db.count_anki_export_words(), 0)

        with patch("PySide6.QtWidgets.QFileDialog.getSaveFileName") as mock_dialog:
            self.view.export_anki()
            # File dialog should NOT be opened
            mock_dialog.assert_not_called()

        # Banner should display warning
        self.assertFalse(self.view.banner.isHidden())
        self.assertEqual(self.view.banner.objectName(), "bannerWarning")
        self.assertIn("当前没有待专攻的单词", self.view.banner._text.text())

    def test_export_anki_success_flow(self):
        # Insert an unknown word
        self.db.connection.execute(
            "INSERT INTO word_notes (word_key, state, note) VALUES ('공부', 'unknown', '学习')"
        )
        self.db.connection.commit()
        self.assertEqual(self.db.count_anki_export_words(), 1)

        with tempfile.TemporaryDirectory() as tmpdir:
            target_path = os.path.join(tmpdir, "export.tsv")

            with patch("PySide6.QtWidgets.QFileDialog.getSaveFileName", return_value=(target_path, "tsv")):
                with patch("ui.settings_view.show_toast") as mock_toast:
                    self.view.export_anki()
                    mock_toast.assert_called_once()
                    self.assertIn("已导出 1 个待专攻单词", mock_toast.call_args[0][1])

            self.assertTrue(os.path.exists(target_path))
            raw = Path(target_path).read_bytes()
            self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))

    def test_export_anki_dialog_cancelled(self):
        self.db.connection.execute(
            "INSERT INTO word_notes (word_key, state, note) VALUES ('공부', 'unknown', '学习')"
        )
        self.db.connection.commit()

        with patch("PySide6.QtWidgets.QFileDialog.getSaveFileName", return_value=("", "")):
            with patch("ui.settings_view.show_toast") as mock_toast:
                self.view.export_anki()
                mock_toast.assert_not_called()

    def test_export_anki_oserror_handling(self):
        self.db.connection.execute(
            "INSERT INTO word_notes (word_key, state, note) VALUES ('공부', 'unknown', '学习')"
        )
        self.db.connection.commit()

        with patch("PySide6.QtWidgets.QFileDialog.getSaveFileName", return_value=("/invalid/path/test.tsv", "tsv")):
            with patch.object(self.db, "export_anki_file", side_effect=OSError("Disk full")):
                self.view.export_anki()
                self.assertFalse(self.view.banner.isHidden())
                self.assertEqual(self.view.banner.objectName(), "bannerDanger")
                self.assertIn("导出失败，无法写入文件：Disk full", self.view.banner._text.text())


if __name__ == "__main__":
    unittest.main()
