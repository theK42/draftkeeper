"""Sync one document's history through sync folders (thumb drive, Proton Drive, ...).

Layout inside a sync folder::

    Draftkeeper/
      draftkeeper-sync.json             folder id + name
      documents/<doc id>/
        <machine id>.bundle             that machine's full history (a git bundle)
        <machine id>.json               machine name, document title, last update

Every machine writes only its own files, so two machines never write the
same file and cloud sync clients never create conflict copies. Bundles are
written to a temp name and renamed, so an unplugged drive or a half-synced
file never leaves a broken bundle in place (a half-downloaded one from a
sync client is just skipped until it's complete).

Reconciling after reading other machines' bundles:

- theirs is behind ours, or the same: nothing to do
- ours is behind theirs: fast-forward (their newer saves become ours)
- both have new saves: a *merge* commit whose content is the newer side's
  version; the other side's edits stay in the history. (Phase 5 will show
  them as tracked changes instead.) The merge commit is deterministic (same
  parents, content, time and message), so two machines that combine the same
  two histories produce the identical commit and don't diverge again.
"""

import json
import os
import time
from dataclasses import dataclass, field
from typing import List, Optional

from dulwich.objects import Commit, Tree
from dulwich.pack import write_pack_data

from .packing import pack_records
from .store import BRANCH, DOC_FILENAME, FILE_MODE, KIND_MERGE, DocumentStore, _parse_commit

BUNDLE_HEADER = b"# v2 git bundle\n"
REMOTES = b"refs/remotes/"
PUBLISHED_MARKER = "draftkeeper-published"


@dataclass
class Event:
    kind: str            # "fast_forward" or "merged"
    machine: str         # the other machine's name
    head: str            # new local head
    winner_machine: str = ""
    winner_time: int = 0
    local_won: bool = False  # for merges: this computer's version was kept


@dataclass
class SyncReport:
    synced: List[str] = field(default_factory=list)   # folder names reached
    errors: List[str] = field(default_factory=list)   # "folder: problem"
    events: List[Event] = field(default_factory=list)

    @property
    def head_moved(self) -> bool:
        return bool(self.events)


def doc_dir(folder_path: str, doc_id: str) -> str:
    return os.path.join(folder_path, "documents", doc_id)


# -- bundles ------------------------------------------------------------------


def read_bundle_head(path: str) -> Optional[bytes]:
    with open(path, "rb") as f:
        return _read_header(f)


def _read_header(f) -> Optional[bytes]:
    if f.readline() != BUNDLE_HEADER:
        raise ValueError("not a git v2 bundle")
    head = None
    while True:
        line = f.readline()
        if not line:
            raise ValueError("truncated bundle header")
        if line == b"\n":
            return head
        if line.startswith(b"-"):
            raise ValueError("incremental bundles are not supported")
        sha, ref = line.rstrip(b"\n").split(b" ", 1)
        if ref == BRANCH:
            head = sha


def write_bundle(store: DocumentStore, path: str) -> bool:
    """Write the full history to ``path`` (atomically). False if unchanged/empty."""
    head = store.head()
    if head is None:
        return False
    if os.path.exists(path):
        try:
            if read_bundle_head(path) == head:
                return False
        except (OSError, ValueError):
            pass

    repo = store.repo
    ordered, seen = [], set()
    for entry in reversed(list(repo.get_walker(include=[head]))):  # oldest first
        c = entry.commit
        blob = repo[c.tree][DOC_FILENAME][1]
        for oid, is_blob in ((blob, True), (c.tree, False), (c.id, False)):
            if oid not in seen:
                seen.add(oid)
                ordered.append((oid, is_blob))

    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(BUNDLE_HEADER)
        f.write(head + b" " + BRANCH + b"\n\n")
        write_pack_data(f.write, pack_records(repo.object_store, ordered), num_records=len(ordered))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return True


def fetch_bundle(store: DocumentStore, path: str) -> Optional[bytes]:
    """Add a bundle's objects to ``store``. Returns its head."""
    store_ = store.repo.object_store
    with open(path, "rb") as f:
        head = _read_header(f)
        if head is None or head in store_:
            return head
        pack = f.read()
    out, commit, abort = store_.add_pack()
    try:
        out.write(pack)
        commit()  # indexes the pack; fails on truncated/corrupt data
    except BaseException:
        try:
            abort()
        except Exception:
            pass
        raise
    if head not in store_:
        raise ValueError("bundle did not contain its head commit")
    return head


# -- reconciling ----------------------------------------------------------------


def is_ancestor(store: DocumentStore, ancestor: bytes, descendant: bytes) -> bool:
    if ancestor == descendant:
        return True
    return any(e.commit.id == ancestor for e in store.repo.get_walker(include=[descendant]))


def remote_heads(store: DocumentStore):
    refs = store.repo.refs
    return {k[len(REMOTES):].split(b"/")[0].decode(): refs[k]
            for k in refs.keys() if k.startswith(REMOTES)}


def reconcile(store: DocumentStore, machine_names) -> List[Event]:
    events = []
    for machine_id, theirs in sorted(remote_heads(store).items()):
        name = machine_names.get(machine_id, machine_id[:8])
        ours = store.head()
        if ours is not None and is_ancestor(store, theirs, ours):
            continue
        if ours is None or is_ancestor(store, ours, theirs):
            store.repo.refs[BRANCH] = theirs
            events.append(Event("fast_forward", name, theirs.decode()))
            continue
        merge, winner = deterministic_merge(store, ours, theirs)
        store.repo.refs[BRANCH] = merge
        events.append(Event("merged", name, merge.decode(),
                            winner_machine=winner.machine, winner_time=winner.timestamp,
                            local_won=winner.commit_id == ours.decode()))
    return events


def deterministic_merge(store: DocumentStore, a: bytes, b: bytes):
    repo = store.repo
    ca, cb = repo[a], repo[b]
    win, lose = sorted([ca, cb], key=lambda c: (c.commit_time, c.id), reverse=True)
    w, l = _parse_commit(win), _parse_commit(lose)

    tree = Tree()
    tree.add(DOC_FILENAME, FILE_MODE, repo[win.tree][DOC_FILENAME][1])
    commit = Commit()
    commit.tree = tree.id
    commit.parents = sorted([a, b])
    commit.author = commit.committer = b"Draftkeeper <draftkeeper@localhost>"
    commit.author_time = commit.commit_time = max(ca.commit_time, cb.commit_time)
    commit.author_timezone = commit.commit_timezone = 0
    commit.encoding = b"UTF-8"
    commit.message = (
        "Combined edits from two computers\n\n"
        "Draftkeeper-Kind: %s\nDraftkeeper-Machine: \n"
        "Draftkeeper-Kept: %s\nDraftkeeper-Other: %s\n" % (KIND_MERGE, w.machine, l.machine)
    ).encode("utf-8")
    repo.object_store.add_object(tree)
    repo.object_store.add_object(commit)
    return commit.id, w


# -- one document, all reachable folders ----------------------------------------------


def sync_document(store: DocumentStore, doc_id: str, folders, machine_id: str,
                  machine_name: str, title: str) -> SyncReport:
    report = SyncReport()
    names = {}
    reached = []

    for folder in folders:
        d = doc_dir(folder.path, doc_id)
        try:
            entries = sorted(os.listdir(d)) if os.path.isdir(d) else []
        except OSError as e:
            report.errors.append("%s: %s" % (folder.name, e))
            continue
        reached.append(folder)
        for name in entries:
            if not name.endswith(".bundle") or name == machine_id + ".bundle":
                continue
            other = name[:-len(".bundle")]
            try:
                head = fetch_bundle(store, os.path.join(d, name))
            except Exception as e:  # half-synced or corrupt file: skip it this time
                report.errors.append("%s: couldn't read %s (%s)" % (folder.name, name, e))
                continue
            if head is not None:
                store.repo.refs[REMOTES + other.encode() + b"/main"] = head
            names.setdefault(other, _machine_name(d, other))

    report.events = reconcile(store, names)

    for folder in reached:
        d = doc_dir(folder.path, doc_id)
        try:
            os.makedirs(d, exist_ok=True)
            if write_bundle(store, os.path.join(d, machine_id + ".bundle")):
                _write_json(os.path.join(d, machine_id + ".json"), {
                    "machine_name": machine_name, "title": title, "updated": int(time.time())})
            report.synced.append(folder.name)
        except OSError as e:
            report.errors.append("%s: %s" % (folder.name, e))

    if report.synced:
        open(os.path.join(store.repo_dir, PUBLISHED_MARKER), "w").close()
    return report


def is_shared(store: DocumentStore) -> bool:
    """True once this history has left this machine (rewriting it would cause divergence)."""
    return bool(remote_heads(store)) or os.path.exists(os.path.join(store.repo_dir, PUBLISHED_MARKER))


def _machine_name(d: str, machine_id: str) -> str:
    try:
        with open(os.path.join(d, machine_id + ".json"), encoding="utf-8") as f:
            return json.load(f).get("machine_name") or machine_id[:8]
    except (OSError, ValueError):
        return machine_id[:8]


def _write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, path)
