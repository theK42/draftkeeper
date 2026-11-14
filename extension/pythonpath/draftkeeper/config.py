"""Per-machine settings: this machine's identity and the sync folders.

Stored in ``<data dir>/config.json``::

    {"machine_id": "3f2a...", "machine_name": "Kates-MacBook",
     "sync_folders": [{"id": "...", "name": "Thumb drive", "path": "/Volumes/STICK/Draftkeeper"}]}

A sync folder is any folder containing ``draftkeeper-sync.json`` (its id and
name). Configured paths are tried first; if a folder isn't there (a thumb
drive that's unplugged, or mounted under a different drive letter), mounted
volumes are scanned for a ``Draftkeeper`` folder with the same id.
"""

import json
import os
import sys
import uuid
from dataclasses import dataclass
from typing import List, Optional

from . import paths

MARKER = "draftkeeper-sync.json"
FOLDER_NAME = "Draftkeeper"


@dataclass
class SyncFolder:
    id: str
    name: str
    path: str  # where it was last found


def _config_path() -> str:
    return os.path.join(paths.data_dir(), "config.json")


def _atomic_write_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


class Config:
    def __init__(self):
        self.data = {}
        try:
            with open(_config_path(), encoding="utf-8") as f:
                self.data = json.load(f)
        except (OSError, ValueError):
            pass
        changed = False
        if not self.data.get("machine_id"):
            self.data["machine_id"] = uuid.uuid4().hex
            changed = True
        if not self.data.get("machine_name"):
            self.data["machine_name"] = paths.machine_name()
            changed = True
        self.data.setdefault("sync_folders", [])
        if changed:
            self.save()

    def save(self) -> None:
        _atomic_write_json(_config_path(), self.data)

    @property
    def machine_id(self) -> str:
        return self.data["machine_id"]

    @property
    def machine_name(self) -> str:
        return self.data["machine_name"]

    @property
    def folders(self) -> List[SyncFolder]:
        return [SyncFolder(f["id"], f["name"], f["path"]) for f in self.data["sync_folders"]]

    def add_folder(self, chosen_path: str, name: str) -> SyncFolder:
        """Set up ``chosen_path`` as a sync folder (uses/creates a Draftkeeper subfolder)."""
        path = chosen_path
        if not os.path.exists(os.path.join(path, MARKER)) and os.path.basename(
                os.path.normpath(path)) != FOLDER_NAME:
            path = os.path.join(path, FOLDER_NAME)
        os.makedirs(path, exist_ok=True)
        marker = read_marker(path)
        if marker is None:
            marker = {"format": 1, "id": uuid.uuid4().hex, "name": name}
            _atomic_write_json(os.path.join(path, MARKER), marker)
        folder = SyncFolder(marker["id"], name, path)
        self.data["sync_folders"] = [f for f in self.data["sync_folders"] if f["id"] != folder.id]
        self.data["sync_folders"].append({"id": folder.id, "name": name, "path": path})
        self.save()
        return folder

    def remove_folder(self, folder_id: str) -> None:
        self.data["sync_folders"] = [f for f in self.data["sync_folders"] if f["id"] != folder_id]
        self.save()

    def reachable(self, scan=None) -> List[SyncFolder]:
        """Configured folders that can be reached right now (with current paths)."""
        found, missing = [], []
        for f in self.folders:
            m = read_marker(f.path)
            if m is not None and m.get("id") == f.id:
                found.append(f)
            else:
                missing.append(f)
        if missing:
            by_id = {}
            for candidate in (scan or scan_volumes)():
                m = read_marker(candidate)
                if m is not None:
                    by_id.setdefault(m.get("id"), candidate)
            moved = False
            for f in missing:
                if f.id in by_id:
                    f.path = by_id[f.id]
                    found.append(f)
                    moved = True
            if moved:  # remember the new location (e.g. a different drive letter)
                for entry in self.data["sync_folders"]:
                    for f in found:
                        if entry["id"] == f.id:
                            entry["path"] = f.path
                self.save()
        return found


def read_marker(path: str) -> Optional[dict]:
    try:
        with open(os.path.join(path, MARKER), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) and data.get("id") else None
    except (OSError, ValueError):
        return None


def scan_volumes() -> List[str]:
    """Candidate ``Draftkeeper`` folders at the top of mounted volumes."""
    roots = []
    if sys.platform == "darwin":
        roots = _listdir_paths("/Volumes")
    elif sys.platform == "win32":
        roots = _windows_drives()
    else:
        user = os.environ.get("USER", "")
        for base in ("/media/" + user, "/run/media/" + user, "/media", "/mnt"):
            roots += _listdir_paths(base)
    return [os.path.join(r, FOLDER_NAME) for r in roots
            if os.path.isdir(os.path.join(r, FOLDER_NAME))]


def _listdir_paths(base: str) -> List[str]:
    try:
        return [os.path.join(base, n) for n in os.listdir(base)]
    except OSError:
        return []


def _windows_drives() -> List[str]:
    import ctypes
    kernel32 = ctypes.windll.kernel32
    # Don't pop up "insert a disk" dialogs for empty card readers.
    old = kernel32.SetErrorMode(0x0001 | 0x8000)  # SEM_FAILCRITICALERRORS | SEM_NOOPENFILEERRORBOX
    try:
        mask = kernel32.GetLogicalDrives()
        drives = []
        for i in range(26):
            if mask & (1 << i):
                root = "%s:\\" % chr(65 + i)
                kind = kernel32.GetDriveTypeW(root)
                if kind in (2, 3):  # DRIVE_REMOVABLE, DRIVE_FIXED (USB disks often report fixed)
                    drives.append(root)
        return drives
    finally:
        kernel32.SetErrorMode(old)
