"""Tests for the Crossref and CNKI (export-file) sources."""

import datetime
import importlib.util
import sys
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "zotero" / "scripts" / "zotero.py"
SPEC = importlib.util.spec_from_file_location("zotero_skill_sources", MODULE_PATH)
zotero = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = zotero
SPEC.loader.exec_module(zotero)

REFWORKS_TEXT = """RT Journal Article
SR 1
A1 张三; 李四
T1 基于深度学习的运动想象脑电解码综述
JF 计算机学报
YR 2023
VO 46
IS 7
SP 1201
EP 1218
DO 10.11897/SP.J.1016.2023.01201
AB 本文综述了运动想象脑电解码的深度学习方法。
K1 脑机接口; 运动想象; 深度学习

RT Journal Article
A1 王五
T1 轻量化卷积网络在嵌入式分拣系统中的部署
JF 仪器仪表学报
YR 2024
VO 45
IS 3
SP 88
EP 95
"""

ENDNOTE_TEXT = """%0 Journal Article
%A 赵六
%A 孙七
%T 多尺度卷积Transformer的脑电情绪识别
%J 自动化学报
%D 2024
%V 50
%N 2
%P 301-315
%R 10.16383/j.aas.c230123
%X 提出一种多尺度卷积Transformer模型。
%K 情绪识别; Transformer

%0 Journal Article
%T 面向遮挡场景的机器人抓取检测
%J 机器人
%D 2022
%V 44
%N 6
%P 700-710
"""


class CnkiFormatTests(unittest.TestCase):
    def test_detects_both_export_dialects(self):
        self.assertEqual(zotero.detect_cnki_format(REFWORKS_TEXT), "refworks")
        self.assertEqual(zotero.detect_cnki_format(ENDNOTE_TEXT), "endnote")
        self.assertEqual(zotero.detect_cnki_format("just some prose"), "")

    def test_refworks_fields_are_mapped(self):
        records = zotero.parse_refworks(REFWORKS_TEXT)
        self.assertEqual(len(records), 2)
        first = records[0]
        self.assertEqual(first["title"], "基于深度学习的运动想象脑电解码综述")
        self.assertEqual(first["journal"], "计算机学报")
        self.assertEqual(first["authors"], ["张三", "李四"])
        self.assertEqual(first["year"], "2023")
        self.assertEqual(first["volume"], "46")
        self.assertEqual(first["issue"], "7")
        self.assertEqual(first["pages"], "1201-1218")
        self.assertEqual(first["doi"], "10.11897/SP.J.1016.2023.01201")
        self.assertIn("运动想象", first["keywords"])

    def test_endnote_collects_repeated_authors(self):
        records = zotero.parse_endnote(ENDNOTE_TEXT)
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["authors"], ["赵六", "孙七"])
        self.assertEqual(records[0]["pages"], "301-315")
        self.assertEqual(records[1]["journal"], "机器人")

    def test_cnki_records_picks_the_right_parser(self):
        fmt, records = zotero.cnki_records(ENDNOTE_TEXT)
        self.assertEqual(fmt, "endnote")
        self.assertEqual(len(records), 2)

    def test_search_url_carries_field_and_database(self):
        url = zotero.cnki_search_url("运动想象 脑电")
        self.assertIn("kns.cnki.net", url)
        self.assertIn("kw=", url)
        self.assertIn("dbcode=CJFQ", url)
        topic_url = zotero.cnki_search_url("运动想象", field="篇名")
        self.assertIn("korder=TI", topic_url)
        default_url = zotero.cnki_search_url("运动想象", field="主题")
        self.assertNotIn("korder", default_url)
        every = zotero.cnki_search_url("运动想象", journals_only=False)
        self.assertNotIn("dbcode", every)

    def test_candidate_defaults_to_journal_date_and_chinese(self):
        record = zotero.parse_refworks(REFWORKS_TEXT)[0]
        candidate = zotero.cnki_candidate(record, 3)
        self.assertEqual(candidate["published"], "2023-01-01")
        self.assertEqual(candidate["language"], "zh")
        self.assertEqual(candidate["number"], "7")
        self.assertEqual(candidate["doi"], "10.11897/sp.j.1016.2023.01201")

    def test_bibtex_entry_keeps_volume_pages_and_language(self):
        record = zotero.parse_refworks(REFWORKS_TEXT)[0]
        entry = zotero.build_bibtex_entry(zotero.cnki_candidate(record), 0)
        self.assertIn("volume = {46}", entry)
        self.assertIn("number = {7}", entry)
        self.assertIn("pages = {1201-1218}", entry)
        self.assertIn("language = {zh}", entry)
        self.assertIn("keywords = {脑机接口; 运动想象; 深度学习}", entry)
        self.assertIn("journal = {计算机学报}", entry)

    def test_chinese_names_still_produce_ascii_keys(self):
        record = zotero.parse_refworks(REFWORKS_TEXT)[0]
        key = zotero.bibtex_key_for(zotero.cnki_candidate(record), 0)
        self.assertTrue(key.startswith("cn"), key)
        self.assertRegex(key, r"^[a-z0-9]+$")

    def test_index_suffix_stays_within_letters(self):
        record = zotero.parse_refworks(REFWORKS_TEXT)[0]
        key = zotero.bibtex_key_for(zotero.cnki_candidate(record), 40)
        self.assertTrue(key.endswith("z"), key)


class CrossrefMappingTests(unittest.TestCase):
    ITEM = {
        "DOI": "10.1016/J.NEUCOM.2023.05.012",
        "title": ["A lightweight detector for cluttered bin picking"],
        "container-title": ["Neurocomputing"],
        "author": [
            {"family": "Zhang", "given": "San"},
            {"family": "Li", "given": "Si"},
        ],
        "issued": {"date-parts": [[2023, 5, 17]]},
        "is-referenced-by-count": 42,
        "abstract": "<jats:p>We propose a <jats:italic>lightweight</jats:italic> detector.</jats:p>",
        "type": "journal-article",
    }

    def test_item_maps_to_openalex_shape(self):
        work = zotero.crossref_item_to_work(self.ITEM)
        self.assertEqual(work["_source"], "crossref")
        self.assertEqual(work["title"], "A lightweight detector for cluttered bin picking")
        self.assertEqual(work["doi"], "10.1016/j.neucom.2023.05.012")
        self.assertEqual(work["publication_date"], "2023-05-17")
        self.assertEqual(work["cited_by_count"], 42)
        self.assertEqual(work["primary_location"]["source"]["display_name"], "Neurocomputing")
        self.assertEqual(
            [entry["author"]["display_name"] for entry in work["authorships"]],
            ["Zhang, San", "Li, Si"],
        )
        self.assertEqual(work["abstract"], "We propose a lightweight detector.")
        self.assertIsNone(work["abstract_inverted_index"])

    def test_score_uses_the_crossref_weights(self):
        work = zotero.crossref_item_to_work(self.ITEM)
        candidate = zotero.score_candidate(
            work,
            {},
            zotero.CROSSREF_WEIGHTS,
            zotero.date.fromisoformat("2023-06-01")
            if hasattr(zotero, "date")
            else datetime.date.fromisoformat("2023-06-01"),
        )
        self.assertGreater(candidate["score"], 0)
        self.assertEqual(candidate["abstract"], "We propose a lightweight detector.")
        self.assertIn("2023", candidate["published"])

    def test_crossref_weights_zero_out_missing_metrics(self):
        self.assertEqual(zotero.CROSSREF_WEIGHTS["venue"], 0.0)
        self.assertEqual(zotero.CROSSREF_WEIGHTS["relevance"], 0.0)
        self.assertTrue(zotero.CROSSREF_WEIGHTS["citations"] > 0)
        self.assertTrue(zotero.CROSSREF_WEIGHTS["recency"] > 0)


class SourceSelectionTests(unittest.TestCase):
    def test_gather_candidates_without_sources_returns_nothing(self):
        self.assertEqual(zotero.gather_candidates(["x"], "2024-01-01", "2024-12-31", sources=()), [])


if __name__ == "__main__":
    unittest.main()
