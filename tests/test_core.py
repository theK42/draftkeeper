"""Tests for the LibreOffice-independent core: normalize + store.

Run from the project root:  python3 -m unittest discover tests
"""

import os
import random
import shutil
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "extension", "pythonpath"))

from draftkeeper.normalize import extract_text, normalize  # noqa: E402
from draftkeeper.locations import ASK, KNOWN, MOVED, Locations  # noqa: E402
from draftkeeper.store import (KIND_AUTO, KIND_IMPORTED, KIND_MANUAL,  # noqa: E402
                               DocumentStore, NewVersion, version_list)

HEADER = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<office:document xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
    'xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" '
    'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
    'xmlns:meta="urn:oasis:names:tc:opendocument:xmlns:meta:1.0" '
    'xmlns:dc="http://purl.org/dc/elements/1.1/" '
    'xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0" '
    'xmlns:officeooo="http://openoffice.org/2009/office" '
    'office:version="1.3" office:mimetype="application/vnd.oasis.opendocument.text">'
    "<office:meta><dc:title>The Novel</dc:title><meta:editing-cycles>%d</meta:editing-cycles>"
    '<meta:user-defined meta:name="Draftkeeper ID">abc</meta:user-defined></office:meta>'
    '<office:automatic-styles><style:style style:name="T1" style:family="text">'
    '<style:text-properties fo:font-style="italic" officeooo:rsid="%s"/></style:style>'
    '<style:style style:name="P1" style:family="paragraph" style:parent-style-name="Standard">'
    '<style:text-properties officeooo:paragraph-rsid="%s"/></style:style></office:automatic-styles>'
    "<office:body><office:text>"
)
FOOTER = "</office:text></office:body></office:document>"

SAMPLE_PARAS = [
    '<text:h text:outline-level="1">Chapter <text:span text:style-name="T1">One</text:span></text:h>',
    '<text:p text:style-name="P1">It was a <text:span text:style-name="T1">dark</text:span> and '
    "stormy night;<text:s text:c=\"2\"/>the rain fell<text:tab/>in torrents.</text:p>",
    "<text:p>Marguerite   said\nnothing.</text:p>",
    "<text:p/>",
    "<text:list><text:list-item><text:p>First item</text:p></text:list-item></text:list>",
]


def make_fodt(paras, cycles=1, rsid=None):
    rsid = rsid or "%06x" % random.randrange(1 << 24)
    return (HEADER % (cycles, rsid, rsid) + "".join(paras) + FOOTER).encode("utf-8")


WORDS = ("the of and a to in was he she it her his that said with as had for on at by "
         "Marguerite rain door window letter night morning garden silence").split()


def novel_paragraphs(n, seed=1):
    rng = random.Random(seed)
    return ["<text:p>%s.</text:p>" % " ".join(rng.choice(WORDS) for _ in range(rng.randint(20, 120)))
            for _ in range(n)]


class NormalizeTests(unittest.TestCase):
    def test_text_is_preserved(self):
        raw = make_fodt(SAMPLE_PARAS)
        self.assertEqual(extract_text(raw), extract_text(normalize(raw)))
        self.assertIn("stormy night;  the rain fell\tin torrents.", extract_text(raw))  # text:s c=2

    def test_rsid_noise_removed(self):
        a = normalize(make_fodt(SAMPLE_PARAS, rsid="000001"))
        b = normalize(make_fodt(SAMPLE_PARAS, rsid="0abcde"))
        self.assertEqual(a, b)
        self.assertNotIn(b"rsid", a)

    def test_save_metadata_ignored(self):
        a = normalize(make_fodt(SAMPLE_PARAS, cycles=3, rsid="000001"))
        b = normalize(make_fodt(SAMPLE_PARAS, cycles=4, rsid="000001"))
        self.assertEqual(a, b)
        self.assertIn(b"<dc:title>The Novel</dc:title>", a)
        self.assertIn(b"Draftkeeper ID", a)

    def test_one_paragraph_per_line(self):
        out = normalize(make_fodt(SAMPLE_PARAS)).decode("utf-8")
        lines = out.splitlines()
        self.assertTrue(any(l.strip().startswith("<text:p text:style-name=\"P1\">It was") for l in lines))
        para_line = [l for l in lines if "stormy" in l][0]
        self.assertTrue(para_line.strip().endswith("</text:p>"))

    def test_prefixes_kept(self):
        out = normalize(make_fodt(SAMPLE_PARAS))
        self.assertIn(b"<office:document", out)
        self.assertIn(b"xmlns:text=", out)
        self.assertNotIn(b"ns0:", out)

    def test_idempotent(self):
        once = normalize(make_fodt(SAMPLE_PARAS))
        self.assertEqual(once, normalize(once))

    def test_whitespace_inside_paragraph_untouched(self):
        out = normalize(make_fodt(SAMPLE_PARAS)).decode("utf-8")
        self.assertIn("<text:p>Marguerite   said\nnothing.</text:p>", out)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="dk-test-")
        self.store = DocumentStore(os.path.join(self.dir, "doc.git"))

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.dir)

    def test_snapshot_and_read_back(self):
        c1 = self.store.snapshot(b"version one", machine="mac")
        c2 = self.store.snapshot(b"version two", kind=KIND_MANUAL, label="Sent to agent", machine="pc")
        hist = self.store.history()
        self.assertEqual([h.commit_id for h in hist], [c2, c1])
        self.assertEqual(hist[0].label, "Sent to agent")
        self.assertEqual(hist[0].kind, KIND_MANUAL)
        self.assertEqual(hist[0].machine, "pc")
        self.assertEqual(hist[1].kind, KIND_AUTO)
        self.assertEqual(self.store.read(c1), b"version one")

    def test_unchanged_auto_skipped_manual_kept(self):
        self.store.snapshot(b"same")
        self.assertIsNone(self.store.snapshot(b"same"))
        self.assertIsNotNone(self.store.snapshot(b"same", kind=KIND_MANUAL, label="Milestone"))
        self.assertEqual(len(self.store.history()), 2)

    def test_reopen(self):
        c = self.store.snapshot(b"persisted")
        self.store.close()
        self.store = DocumentStore(os.path.join(self.dir, "doc.git"))
        self.assertEqual(self.store.history()[0].commit_id, c)

    def test_novel_sized_history_stays_small(self):
        """200 saves of a ~100k-word novel, a few paragraphs edited each time."""
        rng = random.Random(7)
        paras = novel_paragraphs(1600)
        sizes, written, slowest = [], {}, 0.0
        start = time.time()
        for i in range(200):
            for _ in range(3):
                paras[rng.randrange(len(paras))] = novel_paragraphs(1, seed=rng.random())[0]
            if i % 20 == 0:
                paras.insert(rng.randrange(len(paras)), novel_paragraphs(1, seed=i)[0])
            doc = normalize(make_fodt(paras, cycles=i))
            sizes.append(len(doc))
            t = time.time()
            written[self.store.snapshot(doc, when=1_700_000_000 + i * 600)] = doc
            slowest = max(slowest, time.time() - t)
        elapsed = time.time() - start
        self.store.compact()

        repo_bytes = sum(os.path.getsize(os.path.join(r, f))
                         for r, _, fs in os.walk(self.store.repo_dir) for f in fs)
        words = sum(len(p.split()) for p in paras)
        print("\n  novel: %d words, %.2f MB fodt; 200 snapshots -> repo %.2f MB; "
              "%.0f ms/save avg (incl. normalize), slowest save %.0f ms"
              % (words, sizes[-1] / 1e6, repo_bytes / 1e6, elapsed / 200 * 1000, slowest * 1000))
        self.assertEqual(len(self.store.history()), 200)
        for commit_id, doc in written.items():
            self.assertEqual(self.store.read(commit_id), doc)
        if shutil.which("git"):
            import subprocess
            fsck = subprocess.run(["git", "--git-dir", self.store.repo_dir, "fsck", "--strict"],
                                  capture_output=True, text=True)
            self.assertEqual(fsck.returncode, 0, fsck.stdout + fsck.stderr)
        # Without deltas this would be ~200 x compressed size; demand real delta compression.
        self.assertLess(repo_bytes, 10 * sizes[-1])


DAY = 86400
T0 = 1_760_000_000  # a fixed moment; tests below only care about relative times


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="dk-test-")
        self.store = DocumentStore(os.path.join(self.dir, "doc.git"))

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.dir)

    def test_older_versions_go_underneath(self):
        self.store.snapshot(b"today", kind=KIND_MANUAL, label="History started", when=T0)
        self.store.snapshot(b"today edited", when=T0 + 60)
        added = self.store.insert_versions([
            NewVersion(T0 - 30 * DAY, b"draft", label="Novel.odt"),
            NewVersion(T0 - 2 * DAY, b"final final", label="Novel final final.odt"),
        ])
        self.assertEqual(added, 2)
        hist = self.store.history()
        self.assertEqual([h.label for h in hist],
                         ["Saved", "History started", "Novel final final.odt", "Novel.odt"])
        self.assertEqual([h.kind for h in hist][2:], [KIND_IMPORTED, KIND_IMPORTED])
        self.assertEqual(self.store.read(hist[0].commit_id), b"today edited")
        self.assertEqual(self.store.read(hist[3].commit_id), b"draft")

    def test_import_into_empty_history_and_trailers_survive(self):
        self.store.snapshot(b"x", kind=KIND_MANUAL, label="Split", when=T0,
                            extra={"Derived-From": "abc"})
        self.store.insert_versions([NewVersion(T0 - DAY, b"old", label="old.odt")])
        hist = self.store.history()
        self.assertEqual(hist[0].extra, {"Derived-From": "abc"})
        self.assertEqual(len(hist), 2)


class VersionListTests(unittest.TestCase):
    def snap(self, ts, kind=KIND_AUTO, label="Saved"):
        from draftkeeper.store import Snapshot
        return Snapshot(commit_id=str(ts), timestamp=ts, kind=kind, label=label, machine="m")

    def test_named_plus_last_save_per_day(self):
        import datetime
        noon = int(datetime.datetime(2026, 11, 3, 12, 0).timestamp())
        snaps = [  # newest first
            self.snap(noon + DAY + 3600),                       # Nov 4, last save
            self.snap(noon + DAY),                              # Nov 4, earlier
            self.snap(noon + 7200, KIND_MANUAL, "Sent to agent"),  # Nov 3, named
            self.snap(noon + 3600),                             # Nov 3, auto (not end of day)
            self.snap(noon - DAY, KIND_IMPORTED, "final.odt"),  # Nov 2, imported
        ]
        shown = version_list(snaps)
        self.assertEqual([s.timestamp for s in shown], [noon + DAY + 3600, noon + 7200, noon - DAY])
        self.assertEqual(len(version_list(snaps, every_save=True)), 5)


class LocationTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="dk-test-")

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_fork_decisions(self):
        locs = Locations(self.dir, "mac")
        self.assertEqual(locs.check("/a/Novel.odt"), KNOWN)  # first sighting
        locs.remember("/a/Novel.odt")
        self.assertEqual(locs.check("/a/Novel.odt"), KNOWN)
        exists = {"/a/Novel.odt"}.__contains__
        self.assertEqual(locs.check("/a/Chapter 12.odt", exists), ASK)
        self.assertEqual(locs.check("/b/Novel.odt", lambda p: False), MOVED)
        locs.remember("/Volumes/STICK/Novel.odt")  # "same document"
        self.assertEqual(Locations(self.dir, "mac").check("/Volumes/STICK/Novel.odt", exists), KNOWN)
        self.assertEqual(Locations(self.dir, "pc").check("C:/Novel.odt"), KNOWN)  # per machine


if __name__ == "__main__":
    unittest.main()
