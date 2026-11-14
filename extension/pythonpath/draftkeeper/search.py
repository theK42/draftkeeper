"""Full-text search across every version of a document.

The index is a local cache next to the repo (``search.idx``), never synced,
rebuilt from history whenever it's missing or unreadable:

- every distinct paragraph is stored once (a novel's versions share almost
  all of their paragraphs), in original and "folded" form for matching
- every version (by its content's blob id) is a list of paragraph numbers

Matching is case-insensitive and treats curly and straight quotes, and the
different dashes, as the same, so "don't" finds "don’t". A phrase is
matched within a paragraph.

``timeline`` turns "which versions contain it" into the answer to "when
did this appear, and when did it disappear?".
"""

import array
import os
import pickle
import threading
import unicodedata
import zlib
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from .normalize import extract_text

INDEX_FILE = "search.idx"
FORMAT = 1

_FOLD = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"', "″": '"',
    "«": '"', "»": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "―": "-",
    " ": " ", " ": " ", " ": " ", "\t": " ",
})


def fold(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_FOLD).casefold()
    return " ".join(text.split())


_locks: Dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def lock_for(repo_dir: str) -> threading.Lock:
    """One lock per document: indexing may run in a background thread."""
    with _locks_guard:
        return _locks.setdefault(os.path.abspath(repo_dir), threading.Lock())


class SearchIndex:
    def __init__(self, repo_dir: str):
        self.path = os.path.join(repo_dir, INDEX_FILE)
        self.texts: List[str] = []
        self.folded: List[str] = []
        self.ids: Dict[str, int] = {}          # paragraph text -> id
        self.blobs: Dict[str, bytes] = {}      # blob hex -> zlib(array of paragraph ids)
        self._dirty = False
        try:
            with open(self.path, "rb") as f:
                data = pickle.load(f)
            if data.get("format") == FORMAT:
                self.texts, self.blobs = data["texts"], data["blobs"]
                self.folded = [fold(t) for t in self.texts]
                self.ids = {t: i for i, t in enumerate(self.texts)}
        except Exception:
            pass  # missing or unreadable: start fresh; update() rebuilds it

    def has(self, blob: str) -> bool:
        return blob in self.blobs

    def add(self, blob: str, content: bytes) -> None:
        ids = array.array("I")
        for para in extract_text(content).split("\n"):
            if not para.strip():
                continue
            pid = self.ids.get(para)
            if pid is None:
                pid = len(self.texts)
                self.texts.append(para)
                self.folded.append(fold(para))
                self.ids[para] = pid
            ids.append(pid)
        self.blobs[blob] = zlib.compress(ids.tobytes())
        self._dirty = True

    def save(self) -> None:
        if not self._dirty:
            return
        tmp = self.path + ".tmp"
        with open(tmp, "wb") as f:
            pickle.dump({"format": FORMAT, "texts": self.texts, "blobs": self.blobs}, f,
                        protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, self.path)
        self._dirty = False

    def paragraph_ids(self, blob: str) -> array.array:
        a = array.array("I")
        a.frombytes(zlib.decompress(self.blobs[blob]))
        return a

    def matching_paragraphs(self, query: str) -> Set[int]:
        q = fold(query)
        if not q:
            return set()
        return {i for i, t in enumerate(self.folded) if q in t}

    def blobs_with(self, para_ids: Set[int]) -> Set[str]:
        if not para_ids:
            return set()
        return {b for b in self.blobs if not para_ids.isdisjoint(self.paragraph_ids(b))}

    def excerpt(self, blob: str, query: str, width: int = 90) -> str:
        """The first paragraph in ``blob`` containing ``query``, trimmed around the match."""
        q = fold(query)
        if not q or blob not in self.blobs:
            return ""
        for pid in self.paragraph_ids(blob):
            folded = self.folded[pid]
            pos = folded.find(q)
            if pos < 0:
                continue
            text = " ".join(self.texts[pid].split())
            # Folding keeps lengths for everything we map, so positions line up closely.
            start = max(0, pos - width)
            end = min(len(text), pos + len(q) + width)
            return ("…" if start else "") + text[start:end].strip() + ("…" if end < len(text) else "")
        return ""


def update(index: SearchIndex, store) -> int:
    """Index every version in ``store`` that isn't indexed yet. Returns how many were added."""
    added = 0
    for snap in store.history():
        if snap.blob and not index.has(snap.blob):
            index.add(snap.blob, store.read(snap.commit_id))
            added += 1
    if added:
        index.save()
    return added


@dataclass
class Timeline:
    query: str
    total: int = 0                 # versions overall
    matches: int = 0               # versions containing the phrase
    first: Optional[object] = None  # oldest Snapshot containing it
    last: Optional[object] = None   # newest Snapshot containing it
    gone_since: Optional[object] = None  # first version without it after ``last`` (None if present now)
    in_current: bool = False
    changes: List[tuple] = field(default_factory=list)  # (Snapshot, "appears"|"removed"), oldest first


def timeline(query: str, history_newest_first, hit_blobs: Set[str]) -> Timeline:
    t = Timeline(query=query, total=len(history_newest_first))
    present_before = False
    for snap in reversed(history_newest_first):  # oldest first
        present = snap.blob in hit_blobs
        if present:
            t.matches += 1
            if t.first is None:
                t.first = snap
            t.last = snap
        if present != present_before:
            t.changes.append((snap, "appears" if present else "removed"))
        present_before = present
    t.in_current = bool(history_newest_first) and history_newest_first[0].blob in hit_blobs
    if t.last is not None and not t.in_current:
        removals = [s for s, kind in t.changes if kind == "removed"]
        t.gone_since = removals[-1] if removals else None
    return t
