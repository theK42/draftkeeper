"""Two-machine sync tests (no LibreOffice). Run: python3 -m unittest discover tests"""

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "extension", "pythonpath"))

from draftkeeper import config as cfgmod  # noqa: E402
from draftkeeper.config import Config, SyncFolder  # noqa: E402
from draftkeeper.store import KIND_MERGE, DocumentStore, NewVersion  # noqa: E402
from draftkeeper.sync import (doc_dir, fetch_bundle, is_shared, read_bundle_head,  # noqa: E402
                              sync_document, write_bundle)

DOC = "doc-1"
T0 = 1_760_000_000


class Machine:
    def __init__(self, root, name):
        self.name, self.id = name, name.lower() + "-id"
        self.store = DocumentStore(os.path.join(root, name, DOC + ".git"))

    def save(self, text, when, **kw):
        return self.store.snapshot(("<p>%s</p>\n" % text).encode(), machine=self.name, when=when, **kw)

    def sync(self, *folders):
        return sync_document(self.store, DOC, list(folders), self.id, self.name, "Novel.odt")

    def text(self):
        return self.store.read(self.store.head().decode())

    def close(self):
        self.store.close()


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="dk-sync-")
        self.drive = SyncFolder("drive", "Thumb drive", os.path.join(self.root, "drive"))
        os.makedirs(self.drive.path)
        self.mac = Machine(self.root, "Mac")
        self.pc = Machine(self.root, "PC")

    def tearDown(self):
        self.mac.close()
        self.pc.close()
        shutil.rmtree(self.root)

    def test_new_machine_gets_full_history(self):
        self.mac.save("one", T0)
        self.mac.save("two", T0 + 60)
        self.assertEqual(self.mac.sync(self.drive).synced, ["Thumb drive"])
        report = self.pc.sync(self.drive)
        self.assertEqual([e.kind for e in report.events], ["fast_forward"])
        self.assertEqual(report.events[0].machine, "Mac")
        self.assertEqual(self.pc.text(), b"<p>two</p>\n")
        self.assertEqual(len(self.pc.store.history()), 2)

    def test_back_and_forth_fast_forwards(self):
        self.mac.save("one", T0)
        self.mac.sync(self.drive)
        self.pc.sync(self.drive)
        self.pc.save("two (pc)", T0 + 100)
        self.pc.sync(self.drive)
        report = self.mac.sync(self.drive)
        self.assertEqual([e.kind for e in report.events], ["fast_forward"])
        self.assertEqual(self.mac.text(), b"<p>two (pc)</p>\n")
        self.assertEqual(self.mac.store.head(), self.pc.store.head())
        self.assertEqual(self.mac.sync(self.drive).events, [])  # nothing new

    def test_divergence_keeps_newer_and_both_histories(self):
        self.mac.save("one", T0)
        self.mac.sync(self.drive)
        self.pc.sync(self.drive)
        self.mac.save("mac edit", T0 + 100)
        self.pc.save("pc edit (newer)", T0 + 200)
        self.mac.sync(self.drive)
        report = self.pc.sync(self.drive)
        self.assertEqual([e.kind for e in report.events], ["merged"])
        self.assertEqual(report.events[0].winner_machine, "PC")
        self.assertTrue(report.events[0].local_won)
        self.assertEqual(self.pc.text(), b"<p>pc edit (newer)</p>\n")
        labels = [(h.kind, h.machine) for h in self.pc.store.history()]
        self.assertEqual(labels[0], (KIND_MERGE, ""))
        self.assertIn(("auto", "Mac"), labels)
        self.assertIn(("auto", "PC"), labels)
        # The Mac then fast-forwards to the merge
        self.assertEqual([e.kind for e in self.mac.sync(self.drive).events], ["fast_forward"])
        self.assertEqual(self.mac.store.head(), self.pc.store.head())

    def test_independent_merges_are_identical(self):
        self.mac.save("one", T0)
        self.mac.sync(self.drive)
        self.pc.sync(self.drive)
        self.mac.save("mac edit", T0 + 100)
        self.pc.save("pc edit", T0 + 200)
        # Each publishes, then each merges without seeing the other's merge.
        self.mac.sync(self.drive)
        self.pc.sync(self.drive)   # PC merges and publishes its merge
        mac_store_head_before = self.mac.store.head()
        # Simulate the Mac merging from the PC's *pre-merge* bundle (e.g. a second
        # folder that hasn't synced yet): merge locally from PC's edit commit.
        from draftkeeper.sync import deterministic_merge
        pc_edit = self.pc.store.repo[self.pc.store.head()].parents
        pc_edit = [p for p in pc_edit if p != mac_store_head_before][0]
        fetch_bundle(self.mac.store, os.path.join(doc_dir(self.drive.path, DOC), "pc-id.bundle"))
        mac_merge, _ = deterministic_merge(self.mac.store, mac_store_head_before, pc_edit)
        self.assertEqual(mac_merge, self.pc.store.head())

    def test_bundle_is_valid_git_and_rewritten_only_on_change(self):
        for i in range(30):
            self.mac.save("line %d\n" % i * 40, T0 + i)
        self.mac.store.compact()
        self.mac.save("after compact", T0 + 99)
        path = os.path.join(self.root, "x.bundle")
        self.assertTrue(write_bundle(self.mac.store, path))
        self.assertFalse(write_bundle(self.mac.store, path))
        self.assertEqual(read_bundle_head(path), self.mac.store.head())
        if shutil.which("git"):
            r = subprocess.run(["git", "init", "-q", "--bare", os.path.join(self.root, "v.git")])
            r = subprocess.run(["git", "--git-dir", os.path.join(self.root, "v.git"), "bundle",
                                "verify", path], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_broken_bundle_is_skipped(self):
        self.mac.save("one", T0)
        self.mac.sync(self.drive)
        d = doc_dir(self.drive.path, DOC)
        with open(os.path.join(d, "mac-id.bundle"), "rb") as f:
            data = f.read()
        with open(os.path.join(d, "mac-id.bundle"), "wb") as f:
            f.write(data[: len(data) // 2])  # half-downloaded by a sync client
        report = self.pc.sync(self.drive)
        self.assertEqual(report.events, [])
        self.assertTrue(report.errors)
        self.assertIsNone(self.pc.store.head())

    def test_shared_after_sync(self):
        self.mac.save("one", T0)
        self.assertFalse(is_shared(self.mac.store))
        self.mac.sync(self.drive)
        self.assertTrue(is_shared(self.mac.store))
        self.pc.sync(self.drive)
        self.assertTrue(is_shared(self.pc.store))

    def test_bundle_write_is_fast_for_long_history(self):
        import random
        sys.path.insert(0, HERE)
        from test_core import make_fodt, novel_paragraphs
        from draftkeeper.normalize import normalize
        rng = random.Random(3)
        paras = novel_paragraphs(1600)
        for i in range(200):
            paras[rng.randrange(len(paras))] = novel_paragraphs(1, seed=rng.random())[0]
            self.mac.store.snapshot(normalize(make_fodt(paras, cycles=i)), when=T0 + i * 60)
        path = os.path.join(self.root, "big.bundle")
        start = time.time()
        write_bundle(self.mac.store, path)
        first = time.time() - start
        self.mac.store.snapshot(normalize(make_fodt(paras[:-1], cycles=999)), when=T0 + 99999)
        start = time.time()
        write_bundle(self.mac.store, path)
        again = time.time() - start
        size = os.path.getsize(path)
        print("\n  bundle: 201 versions -> %.2f MB; write %.0f ms, after one more save %.0f ms"
              % (size / 1e6, first * 1000, again * 1000))
        self.assertLess(size, 3_000_000)
        self.assertLess(again, 5.0)
        fetch_bundle(self.pc.store, path)  # and it reads back
        self.pc.store.repo.refs[b"refs/heads/main"] = read_bundle_head(path)
        self.assertEqual(len(self.pc.store.history()), 201)


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="dk-cfg-")
        self.old = os.environ.get("DRAFTKEEPER_HOME")
        os.environ["DRAFTKEEPER_HOME"] = os.path.join(self.root, "home")

    def tearDown(self):
        if self.old is None:
            os.environ.pop("DRAFTKEEPER_HOME", None)
        else:
            os.environ["DRAFTKEEPER_HOME"] = self.old
        shutil.rmtree(self.root)

    def test_machine_identity_is_stable(self):
        a = Config()
        self.assertEqual(Config().machine_id, a.machine_id)

    def test_add_folder_and_find_it_after_drive_letter_change(self):
        cfg = Config()
        stick = os.path.join(self.root, "E")
        os.makedirs(stick)
        folder = cfg.add_folder(stick, "Thumb drive")
        self.assertEqual(folder.path, os.path.join(stick, "Draftkeeper"))
        self.assertEqual([f.name for f in cfg.reachable(scan=lambda: [])], ["Thumb drive"])
        # Drive comes back as F: instead of E:
        moved = os.path.join(self.root, "F")
        shutil.move(stick, moved)
        self.assertEqual(Config().reachable(scan=lambda: []), [])
        found = Config().reachable(scan=lambda: [os.path.join(moved, "Draftkeeper")])
        self.assertEqual([f.path for f in found], [os.path.join(moved, "Draftkeeper")])
        self.assertEqual(Config().folders[0].path, os.path.join(moved, "Draftkeeper"))  # remembered

    def test_adding_same_folder_twice_keeps_one(self):
        cfg = Config()
        p = os.path.join(self.root, "Proton Drive")
        os.makedirs(p)
        a = cfg.add_folder(p, "Proton Drive")
        b = cfg.add_folder(os.path.join(p, "Draftkeeper"), "Proton Drive")
        self.assertEqual(a.id, b.id)
        self.assertEqual(len(Config().folders), 1)


if __name__ == "__main__":
    unittest.main()
