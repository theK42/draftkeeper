"""History store for one document: a bare git repository managed with dulwich.

Each snapshot is a commit whose tree holds a single file, ``document.fodt``
(normalized Flat ODT). Nothing here depends on LibreOffice, so it can be
tested with plain Python.

Commit messages look like::

    Before cutting chapter 12

    Draftkeeper-Kind: manual
    Draftkeeper-Machine: Kates-MacBook

``Kind`` is ``auto`` (made on save), ``manual`` (a named restore point) or
``imported`` (an older file brought in; the label is its filename).
Any other ``Draftkeeper-*`` trailers are kept in ``Snapshot.extra``.
"""

import datetime
import os
import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

from dulwich.objects import Blob, Commit, Tree
from dulwich.repo import Repo

DOC_FILENAME = b"document.fodt"
BRANCH = b"refs/heads/main"
FILE_MODE = 0o100644

KIND_AUTO = "auto"
KIND_MANUAL = "manual"
KIND_IMPORTED = "imported"
NAMED_KINDS = (KIND_MANUAL, KIND_IMPORTED)

TRAILER_PREFIX = "Draftkeeper-"

# Pack loose objects into a delta-compressed pack after this many commits.
GC_EVERY = 25


@dataclass
class Snapshot:
    commit_id: str
    timestamp: int  # seconds since epoch, UTC
    kind: str
    label: str
    machine: str
    extra: Dict[str, str] = field(default_factory=dict)

    @property
    def is_named(self) -> bool:
        return self.kind in NAMED_KINDS


@dataclass
class NewVersion:
    """A version to add with ``insert_versions``."""
    timestamp: int
    content: bytes
    kind: str = KIND_IMPORTED
    label: str = ""
    machine: str = ""


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
        extra: Optional[Dict[str, str]] = None,
    ) -> Optional[str]:
        """Record ``content`` as a new snapshot on top of the history.

        Auto snapshots whose content matches the latest snapshot are skipped
        (returns None). Named restore points are always recorded, even when
        nothing changed, so the label is kept.
        """
        parent = self.head()
        blob = Blob.from_string(content)

        if parent is not None and kind == KIND_AUTO:
            if self._blob_of(parent) == blob.id:
                return None

        when = int(time.time()) if when is None else when
        commit = self._commit(blob, parent, kind, label, machine, when, extra or {})
        self.repo.refs.set_if_equals(BRANCH, parent, commit.id)
        self._maybe_compact()
        return commit.id.decode("ascii")

    def insert_versions(self, versions: Iterable[NewVersion]) -> int:
        """Add versions at their own timestamps, which may predate existing history.

        The history is rebuilt in date order (commit ids change; fine while a
        repo exists on one machine only). Versions identical to their
        predecessor are kept only if named. Returns how many were added.
        """
        existing = []
        head = self.head()
        if head is not None:
            for entry in self.repo.get_walker(include=[head]):
                c = entry.commit
                s = _parse_commit(c)
                existing.append((s.timestamp, 0, self.repo[self._blob_of(c.id)].data,
                                 s.kind, s.label, s.machine, s.extra))
        new = [(v.timestamp, 1, v.content, v.kind, v.label, v.machine, {}) for v in versions]
        if not new:
            return 0

        # Stable order: by time; at equal times, existing history first.
        merged = sorted(existing + new, key=lambda r: (r[0], r[1]))
        parent, prev_blob, added = None, None, 0
        for when, is_new, content, kind, label, machine, extra in merged:
            blob = Blob.from_string(content)
            if is_new and blob.id == prev_blob and kind not in NAMED_KINDS:
                continue
            commit = self._commit(blob, parent, kind, label, machine, when, extra)
            parent, prev_blob = commit.id, blob.id
            added += is_new
        self.repo.refs[BRANCH] = parent
        self._maybe_compact()
        return added

    def _commit(self, blob, parent, kind, label, machine, when, extra) -> Commit:
        tree = Tree()
        tree.add(DOC_FILENAME, FILE_MODE, blob.id)

        if not label:
            label = "Restore point" if kind == KIND_MANUAL else "Saved"
        trailers = [("Kind", kind), ("Machine", machine)] + sorted(extra.items())
        message = label.strip().replace("\n", " ") + "\n\n" + "".join(
            "%s%s: %s\n" % (TRAILER_PREFIX, k, v) for k, v in trailers)

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
        if blob.id not in store:
            store.add_object(blob)
        store.add_object(tree)
        store.add_object(commit)
        return commit

    def _blob_of(self, commit_id: bytes) -> bytes:
        tree = self.repo[self.repo[commit_id].tree]
        return tree[DOC_FILENAME][1]

    def _maybe_compact(self) -> None:
        if self.repo.object_store.count_loose_objects() >= GC_EVERY * 3:
            self.compact()

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
        return [_parse_commit(e.commit) for e in self.repo.get_walker(include=[head])]

    def read(self, commit_id: str) -> bytes:
        return self.repo[self._blob_of(commit_id.encode("ascii"))].data

    def close(self) -> None:
        self.repo.close()


def _parse_commit(commit: Commit) -> Snapshot:
    text = commit.message.decode("utf-8", "replace")
    label = text.split("\n", 1)[0]
    kind, machine, extra = KIND_AUTO, "", {}
    for line in text.splitlines()[1:]:
        if line.startswith(TRAILER_PREFIX) and ":" in line:
            key, value = line[len(TRAILER_PREFIX):].split(":", 1)
            value = value.strip()
            if key == "Kind":
                kind = value
            elif key == "Machine":
                machine = value
            else:
                extra[key] = value
    return Snapshot(
        commit_id=commit.id.decode("ascii"),
        timestamp=commit.commit_time,
        kind=kind,
        label=label,
        machine=machine,
        extra=extra,
    )


def version_list(snapshots: List[Snapshot], every_save: bool = False) -> List[Snapshot]:
    """What the version list shows, newest first.

    By default: every named version (restore points, imports) plus the last
    save of each local calendar day, which is the "daily restore point".
    With ``every_save``: everything.
    """
    if every_save:
        return list(snapshots)
    out, seen_days = [], set()
    for s in snapshots:  # newest first, so the first auto seen per day is the day's last
        day = datetime.datetime.fromtimestamp(s.timestamp).date()
        if s.is_named:
            out.append(s)
        elif day not in seen_days:
            out.append(s)
        seen_days.add(day)
    return out
