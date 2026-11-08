"""Where Draftkeeper keeps its data on each platform."""

import os
import platform
import socket
import sys

APP_NAME = "Draftkeeper"


def data_dir() -> str:
    override = os.environ.get("DRAFTKEEPER_HOME")
    if override:
        return override
    if sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    elif sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~\\AppData\\Roaming")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, APP_NAME)


def repo_dir(doc_id: str) -> str:
    return os.path.join(data_dir(), "documents", doc_id + ".git")


def machine_name() -> str:
    name = platform.node() or socket.gethostname() or "unknown"
    return name.split(".")[0]
