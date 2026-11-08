"""Where a tracked document's files live, per machine, for fork detection.

A document's ID travels with every copy of the file. When a tracked
document is saved somewhere new, we need to know whether it is still the
same novel (moved, renamed, opened from the thumb drive) or a new document
that happens to carry the ID (a Save As copy for working on one chapter).

Kept in ``locations.json`` next to the repo (not in git history, since it's
machine-local)::

    {"Kates-MacBook": ["/Users/kate/Novel.odt", "/Volumes/STICK/Novel.odt"]}
"""

import json
import os
from typing import Callable, Dict, List

FILENAME = "locations.json"

# Results of check()
KNOWN = "known"   # a path we've seen before for this document
MOVED = "moved"   # new path, and every known path is gone: treat as a move
ASK = "ask"       # new path while a known copy still exists: ask her

# Formats that count as "the working document". Saving a .docx export
# elsewhere shouldn't trigger the question.
TRACKED_EXTENSIONS = (".odt", ".fodt")


def is_tracked_format(path: str) -> bool:
    return path.lower().endswith(TRACKED_EXTENSIONS)


class Locations:
    def __init__(self, repo_dir: str, machine: str):
        self.path = os.path.join(repo_dir, FILENAME)
        self.machine = machine
        self.data: Dict[str, List[str]] = {}
        if os.path.exists(self.path):
            try:
                with open(self.path, encoding="utf-8") as f:
                    self.data = json.load(f)
            except (OSError, ValueError):
                self.data = {}

    @property
    def known(self) -> List[str]:
        return self.data.get(self.machine, [])

    def check(self, path: str, exists: Callable[[str], bool] = os.path.exists) -> str:
        known = [os.path.normcase(p) for p in self.known]
        if not known or os.path.normcase(path) in known:
            return KNOWN
        if not any(exists(p) for p in self.known):
            return MOVED
        return ASK

    def remember(self, path: str) -> None:
        paths = self.data.setdefault(self.machine, [])
        if os.path.normcase(path) not in [os.path.normcase(p) for p in paths]:
            paths.append(path)
        self._save()

    def replace(self, path: str) -> None:
        self.data[self.machine] = [path]
        self._save()

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=1)
        os.replace(tmp, self.path)
