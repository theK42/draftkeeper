"""What the Versions window shows: rows, search summary, preview. No LibreOffice needed."""

import datetime
from typing import List, Optional, Tuple

from .search import SearchIndex, lock_for, timeline, update
from .store import KIND_IMPORTED, KIND_MANUAL, KIND_MERGE, version_list


def fmt_time(ts: int) -> str:
    d = datetime.datetime.fromtimestamp(ts)
    return d.strftime("%a %b %d, %Y, %I:%M %p").replace(" 0", " ")


def fmt_day(ts: int) -> str:
    return datetime.datetime.fromtimestamp(ts).strftime("%b %d, %Y").replace(" 0", " ")


def describe(s, every_save: bool) -> str:
    when = fmt_time(s.timestamp)
    where = "  ·  " + s.machine if s.machine else ""
    if s.kind == KIND_MERGE:
        return "⇄ Combined edits from two computers (kept %s's)  —  %s" % (
            s.extra.get("Kept") or "the newer", when)
    if s.kind == KIND_MANUAL:
        return "★ %s  —  %s%s" % (s.label, when, where)
    if s.kind == KIND_IMPORTED:
        return "◆ %s  —  %s (imported)" % (s.label, when)
    return "%s  —  %s%s" % ("Saved" if every_save else "End of day", when, where)


def short(s) -> str:
    """A version in a sentence: its name if it has one, else its date."""
    if s.kind in (KIND_MANUAL, KIND_IMPORTED):
        return "%s (“%s”)" % (fmt_day(s.timestamp), s.label)
    return fmt_time(s.timestamp)


def summarize(t) -> str:
    q = "“%s”" % t.query
    if t.matches == 0:
        return "%s isn't in any saved version." % q
    lines = ["%s is in %d of %d saved versions. First appeared %s." % (
        q, t.matches, t.total, short(t.first))]
    if t.in_current:
        lines.append("It's still in the current version.")
    else:
        lines.append("Last seen %s; removed %s." % (short(t.last), short(t.gone_since)))
    comebacks = sum(1 for _, k in t.changes if k == "appears") - 1
    if comebacks > 0:
        lines[-1] += " (It was removed and came back %s.)" % (
            "once" if comebacks == 1 else "%d times" % comebacks)
    return "\n".join(lines)


class VersionsModel:
    """Content provider for ui.browse_versions."""

    def __init__(self, store, title: str):
        self.store = store
        self.history = store.history()
        named = sum(1 for s in self.history if s.is_named)
        self.header = "%s: %d saved versions, %d named." % (title, len(self.history), named)
        self._index: Optional[SearchIndex] = None

    def index(self) -> SearchIndex:
        if self._index is None:
            with lock_for(self.store.repo_dir):
                self._index = SearchIndex(self.store.repo_dir)
                update(self._index, self.store)
        return self._index

    def rows(self, query: str, every_save: bool, date_from=None, date_to=None) -> Tuple[str, List]:
        if query:
            index = self.index()
            hits = index.blobs_with(index.matching_paragraphs(query))
            t = timeline(query, self.history, hits)
            marks = {s.commit_id: k for s, k in t.changes}
            shown = {s.commit_id for s in version_list(self.history, every_save)}
            picked = [s for s in self.history
                      if s.commit_id in marks or (s.blob in hits and s.commit_id in shown)]
            summary = summarize(t)
        else:
            marks, picked, summary = {}, version_list(self.history, every_save), ""
            if not every_save:
                summary = ("Named versions and the last save of each day. "
                           "Tick “Show every save” for all.")

        def in_range(s):
            day = datetime.datetime.fromtimestamp(s.timestamp).date()
            return (date_from is None or day >= date_from) and (date_to is None or day <= date_to)

        rows = []
        for s in picked:
            if not in_range(s):
                continue
            text = describe(s, every_save or bool(query))
            mark = marks.get(s.commit_id)
            if mark == "appears":
                text = "▲ First appears here: " + text
            elif mark == "removed":
                text = "▼ Removed here: " + text
            rows.append((text, s))
        if (date_from or date_to) and not rows:
            summary = (summary + "\n" if summary else "") + "No versions in that date range."
        return summary, rows

    def preview(self, s, query: str) -> str:
        if query:
            excerpt = self.index().excerpt(s.blob, query)
            if excerpt:
                return excerpt
            return "(Not in this version.)"
        if s.is_named:
            return "%s — %s" % (s.label, fmt_time(s.timestamp))
        return "Select a version, then Open as Copy or Compare with Current."
