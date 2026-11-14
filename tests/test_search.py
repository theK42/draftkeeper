"""Search index and timeline tests (no LibreOffice). Run: python3 -m unittest discover tests"""

import os
import random
import shutil
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "extension", "pythonpath"))
sys.path.insert(0, HERE)

from draftkeeper.normalize import normalize  # noqa: E402
from draftkeeper.search import INDEX_FILE, SearchIndex, fold, timeline, update  # noqa: E402
from draftkeeper.store import KIND_MANUAL, DocumentStore  # noqa: E402
from test_core import make_fodt, novel_paragraphs  # noqa: E402

T0 = 1_760_000_000


def doc(*paras):
    return normalize(make_fodt(["<text:p>%s</text:p>" % p for p in paras]))


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="dk-search-")
        self.store = DocumentStore(os.path.join(self.dir, "doc.git"))

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.dir)

    def search(self, query):
        index = SearchIndex(self.store.repo_dir)
        update(index, self.store)
        hist = self.store.history()
        hits = index.blobs_with(index.matching_paragraphs(query))
        return index, hist, timeline(query, hist, hits)

    def test_fold_matches_quotes_dashes_case_and_spaces(self):
        self.assertEqual(fold("Don’t  “GO” — now"), fold("don't \"go\" - now"))

    def test_subplot_appears_then_is_removed(self):
        self.store.snapshot(doc("Chapter one.", "The rain fell."), when=T0)
        self.store.snapshot(doc("Chapter one.", "Marguerite’s letter arrived."), when=T0 + 100)
        self.store.snapshot(doc("Chapter one.", "Marguerite's letter arrived.", "More."),
                            kind=KIND_MANUAL, label="Sent to agent", when=T0 + 200)
        self.store.snapshot(doc("Chapter one.", "The rain fell again."), when=T0 + 300)
        index, hist, t = self.search("marguerite's LETTER")
        self.assertEqual((t.total, t.matches), (4, 2))
        self.assertEqual(t.first.timestamp, T0 + 100)
        self.assertEqual(t.last.label, "Sent to agent")
        self.assertFalse(t.in_current)
        self.assertEqual(t.gone_since.timestamp, T0 + 300)
        self.assertEqual([(s.timestamp, k) for s, k in t.changes],
                         [(T0 + 100, "appears"), (T0 + 300, "removed")])
        self.assertIn("Marguerite’s letter", index.excerpt(t.first.blob, "marguerite's letter"))

    def test_still_present_and_not_found(self):
        self.store.snapshot(doc("Alpha beta."), when=T0)
        self.store.snapshot(doc("Alpha beta gamma."), when=T0 + 10)
        _, _, t = self.search("alpha")
        self.assertTrue(t.in_current)
        self.assertIsNone(t.gone_since)
        _, _, t = self.search("zebra")
        self.assertEqual(t.matches, 0)
        self.assertIsNone(t.first)

    def test_index_is_incremental_and_rebuilt_if_corrupt(self):
        self.store.snapshot(doc("One."), when=T0)
        index = SearchIndex(self.store.repo_dir)
        self.assertEqual(update(index, self.store), 1)
        self.store.snapshot(doc("One.", "Two."), when=T0 + 1)
        index = SearchIndex(self.store.repo_dir)
        self.assertEqual(update(index, self.store), 1)  # only the new version
        with open(os.path.join(self.store.repo_dir, INDEX_FILE), "wb") as f:
            f.write(b"garbage")
        index = SearchIndex(self.store.repo_dir)
        self.assertEqual(update(index, self.store), 2)  # rebuilt
        self.assertEqual(len(index.blobs_with(index.matching_paragraphs("two"))), 1)

    def test_novel_sized_history_search_is_fast(self):
        rng = random.Random(11)
        paras = novel_paragraphs(1600)
        paras[800] = "<text:p>Marguerite hid the letter under the floorboard.</text:p>"
        for i in range(300):
            for _ in range(3):
                j = rng.randrange(len(paras))
                if j != 800:
                    paras[j] = novel_paragraphs(1, seed=rng.random())[0]
            if i == 250:
                paras[800] = "<text:p>The floorboard was bare.</text:p>"
            self.store.snapshot(normalize(make_fodt(paras, cycles=i)), when=T0 + i * 60)
        index = SearchIndex(self.store.repo_dir)
        start = time.time()
        update(index, self.store)
        build = time.time() - start
        start = time.time()
        index = SearchIndex(self.store.repo_dir)  # load from disk, like a fresh search
        update(index, self.store)
        hist = self.store.history()
        t = timeline("hid the letter", hist, index.blobs_with(index.matching_paragraphs("hid the letter")))
        query = time.time() - start
        size = os.path.getsize(os.path.join(self.store.repo_dir, INDEX_FILE))
        print("\n  search: 300 versions, %d unique paragraphs; index %.1f MB; first build %.1f s; "
              "load + search %.0f ms" % (len(index.texts), size / 1e6, build, query * 1000))
        self.assertEqual(t.matches, 250)
        self.assertEqual(t.gone_since.timestamp, T0 + 250 * 60)
        self.assertLess(query, 2.0)


class BrowserTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="dk-browser-")
        self.store = DocumentStore(os.path.join(self.dir, "doc.git"))
        import datetime
        self.day = lambda d: int(datetime.datetime(2026, 3, d, 12).timestamp())
        self.store.snapshot(doc("Opening."), when=self.day(1))
        self.store.snapshot(doc("Opening.", "Marguerite waits."), when=self.day(2))
        self.store.snapshot(doc("Opening.", "Marguerite waits.", "Rain."), kind=KIND_MANUAL,
                            label="Sent to agent", when=self.day(3))
        self.store.snapshot(doc("Opening.", "Rain."), when=self.day(4))
        self.store.snapshot(doc("Opening.", "Rain.", "Marguerite returns."), when=self.day(5))
        from draftkeeper.browser import VersionsModel
        self.model = VersionsModel(self.store, "Novel.odt")

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.dir)

    def test_search_summary_and_marked_rows(self):
        summary, rows = self.model.rows("marguerite", every_save=False)
        self.assertIn("is in 3 of 5 saved versions", summary)
        self.assertIn("still in the current version", summary)
        self.assertIn("came back once", summary)
        texts = [t for t, _ in rows]
        self.assertTrue(texts[0].startswith("\u25b2 First appears here"))   # Mar 5, came back
        self.assertTrue(texts[1].startswith("\u25bc Removed here"))         # Mar 4
        self.assertIn("Sent to agent", texts[2])
        self.assertTrue(texts[-1].startswith("\u25b2 First appears here"))  # Mar 2
        self.assertEqual(self.model.preview(rows[2][1], "marguerite"), "Marguerite waits.")
        self.assertEqual(self.model.preview(rows[1][1], "marguerite"), "(Not in this version.)")

    def test_removed_phrase_summary(self):
        summary, _ = self.model.rows("waits", every_save=True)
        self.assertIn("Last seen Mar 3, 2026 (\u201cSent to agent\u201d); removed", summary)

    def test_date_range(self):
        import datetime
        _, rows = self.model.rows("", every_save=True, date_from=datetime.date(2026, 3, 2),
                                  date_to=datetime.date(2026, 3, 3))
        self.assertEqual(len(rows), 2)
        summary, rows = self.model.rows("", every_save=True, date_from=datetime.date(2027, 1, 1))
        self.assertEqual(rows, [])
        self.assertIn("No versions in that date range", summary)


if __name__ == "__main__":
    unittest.main()
