"""History store for one document: a bare git repository managed with dulwich.

Each snapshot is a commit whose tree holds a single file, ``document.fodt``
(normalized Flat ODT). Nothing here depends on LibreOffice, so it can be
tested with plain Python.

Commit messages look like::

    Before cutting chapter 12

    Draftkeeper-Kind: manual
    Draftkeeper-Machine: Kates-MacBook

``Kind`` is ``auto`` (made on save) or ``manual`` (a named restore point).
"""

import os
import time
from dataclasses import dataclass
from typing import List, Optional

from dulwich.objects import Blob, Commit, Tree
from dulwich.repo import Repo

DOC_FILENAME = b"document.fodt"
BRANCH = b"refs/heads/main"
FILE_MODE = 0o100644

KIND_AUTO = "auto"
KIND_MANUAL = "manual"

# Pack loose objects into a delta-compressed pack after this many commits.
GC_EVERY = 25


@dataclass
class Snapshot:
    commit_id: str
    timestamp: int  # seconds since epoch, UTC
    kind: str
    label: str
    machine: str


class DocumentStore:
    def __init__(self, repo_dir: str):
        self.repo_dir = repo_dir
        if os.path.isdir(os.path.join(repo_dir, "objects")):
            self.repo = Repo(repo_dir)
        else:
            os.makedirs(repo_dir, exist_ok=True)
            self.repo = Repo.init_bare(repo_dir)
            self.repo.refs.set_symbolic_ref(b"HEAD", BRANCH)

    # -- writing ---------------------------------------------------------

    def head(self) -> Optional[bytes]:
        try:
            return self.repo.refs[BRANCH]
        except KeyError:
            return None

    def snapshot(
        self,
        content: bytes,
        kind: str = KIND_AUTO,
        label: str = "",
        machine: str = "",
        when: Optional[int] = None,
    ) -> Optional[str]:
        """Record ``content`` as a new snapshot.

        Auto snapshots whose content matches the latest snapshot are skipped
        (returns None). Manual restore points are always recorded, even when
        nothing changed, so the label is kept.
        """
        parent = self.head()
        blob = Blob.from_string(content)

        if parent is not None and kind == KIND_AUTO:
            parent_tree = self.repo[self.repo[parent].tree]
            if parent_tree[DOC_FILENAME][1] == blob.id:
                return None

        tree = Tree()
        tree.add(DOC_FILENAME, FILE_MODE, blob.id)

        if not label:
            label = "Restore point" if kind == KIND_MANUAL else "Saved"
        message = "%s\n\nDraftkeeper-Kind: %s\nDraftkeeper-Machine: %s\n" % (
            label.strip(),
            kind,
            machine,
        )

        when = int(time.time()) if when is None else when
        tz = time.localtime(when).tm_gmtoff
        identity = ("Draftkeeper (%s) <draftkeeper@localhost>" % (machine or "unknown")).encode()

        commit = Commit()
        commit.tree = tree.id
        commit.parents = [parent] if parent is not None else []
        commit.author = commit.committer = identity
        commit.author_time = commit.commit_time = when
        commit.author_timezone = commit.commit_timezone = tz
        commit.encoding = b"UTF-8"
        commit.message = message.encode("utf-8")

        store = self.repo.object_store
        store.add_object(blob)
        store.add_object(tree)
        store.add_object(commit)
        self.repo.refs.set_if_equals(BRANCH, parent, commit.id)

        if store.count_loose_objects() >= GC_EVERY * 3:
            self.compact()
        return commit.id.decode("ascii")

    def compact(self) -> None:
        """Pack loose objects so repeated snapshots are stored as deltas."""
        from .packing import pack_loose_objects

        pack_loose_objects(self.repo.object_store)

    # -- reading ---------------------------------------------------------

    def history(self) -> List[Snapshot]:
        """All snapshots, newest first."""
        head = self.head()
        if head is None:
            return []
        out = []
        for entry in self.repo.get_walker(include=[head]):
            out.append(_parse_commit(entry.commit))
        return out

    def read(self, commit_id: str) -> bytes:
        commit = self.repo[commit_id.encode("ascii")]
        tree = self.repo[commit.tree]
        return self.repo[tree[DOC_FILENAME][1]].data

    def close(self) -> None:
        self.repo.close()


def _parse_commit(commit: Commit) -> Snapshot:
    text = commit.message.decode("utf-8", "replace")
    label = text.split("\n", 1)[0]
    kind, machine = KIND_AUTO, ""
    for line in text.splitlines():
        if line.startswith("Draftkeeper-Kind:"):
            kind = line.split(":", 1)[1].strip()
        elif line.startswith("Draftkeeper-Machine:"):
            machine = line.split(":", 1)[1].strip()
    return Snapshot(
        commit_id=commit.id.decode("ascii"),
        timestamp=commit.commit_time,
        kind=kind,
        label=label,
        machine=machine,
    )
