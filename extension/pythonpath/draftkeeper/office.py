"""Everything that talks to LibreOffice documents (UNO). Only importable inside LibreOffice.

UI goes through ``make_ui(ctx, doc)`` so tests can substitute a scripted UI.
"""

import datetime
import logging
import os
import tempfile
import uuid

import uno
from com.sun.star.beans import PropertyValue

from . import paths
from .locations import ASK, MOVED, Locations, is_tracked_format
from .normalize import normalize
from .store import (KIND_AUTO, KIND_IMPORTED, KIND_MANUAL, DocumentStore,
                    NewVersion, version_list)

ID_PROPERTY = "Draftkeeper ID"
FODT_FILTER = "OpenDocument Text Flat XML"
REMOVEABLE = 128  # com.sun.star.beans.PropertyAttribute.REMOVEABLE

log = logging.getLogger("draftkeeper")


def make_ui(ctx, doc):
    from .ui import UI
    return UI(ctx, doc)


def setup_logging() -> None:
    if log.handlers:
        return
    os.makedirs(paths.data_dir(), exist_ok=True)
    handler = logging.FileHandler(os.path.join(paths.data_dir(), "draftkeeper.log"), encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)


def prop(name, value):
    p = PropertyValue()
    p.Name, p.Value = name, value
    return p


def fmt_time(ts: int) -> str:
    d = datetime.datetime.fromtimestamp(ts)
    return d.strftime("%a %b %d, %Y, %I:%M %p").replace(" 0", " ")


# -- document helpers -------------------------------------------------------


def is_writer(doc) -> bool:
    return doc is not None and hasattr(doc, "supportsService") and doc.supportsService(
        "com.sun.star.text.TextDocument"
    )


def get_doc_id(doc):
    udp = doc.getDocumentProperties().getUserDefinedProperties()
    if udp.getPropertySetInfo().hasPropertyByName(ID_PROPERTY):
        return str(udp.getPropertyValue(ID_PROPERTY))
    return None


def set_doc_id(doc, doc_id=None) -> str:
    doc_id = doc_id or str(uuid.uuid4())
    udp = doc.getDocumentProperties().getUserDefinedProperties()
    if udp.getPropertySetInfo().hasPropertyByName(ID_PROPERTY):
        udp.setPropertyValue(ID_PROPERTY, doc_id)
    else:
        udp.addProperty(ID_PROPERTY, REMOVEABLE, doc_id)
    return doc_id


def doc_path(doc):
    url = doc.getURL()
    return uno.fileUrlToSystemPath(url) if url else None


def doc_title(doc) -> str:
    path = doc_path(doc)
    if path:
        return os.path.basename(path)
    return doc.getTitle() if hasattr(doc, "getTitle") else "Untitled"


def export_fodt(doc) -> bytes:
    """Current in-memory state of ``doc`` as Flat ODT, without touching its file."""
    fd, tmp = tempfile.mkstemp(suffix=".fodt", prefix="draftkeeper-")
    os.close(fd)
    try:
        doc.storeToURL(uno.systemPathToFileUrl(tmp), (prop("FilterName", FODT_FILTER),))
        with open(tmp, "rb") as f:
            return f.read()
    finally:
        _remove(tmp)


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def open_store(doc_id: str) -> DocumentStore:
    return DocumentStore(paths.repo_dir(doc_id))


def take_snapshot(doc, kind: str, label: str = "", extra=None):
    doc_id = get_doc_id(doc)
    if not doc_id:
        return None
    content = normalize(export_fodt(doc))
    store = open_store(doc_id)
    try:
        commit = store.snapshot(content, kind=kind, label=label, machine=paths.machine_name(),
                                extra=extra)
        _write_info(doc, doc_id)
        log.info("snapshot %s kind=%s doc=%s -> %s", doc_title(doc), kind, doc_id, commit)
        return commit
    finally:
        store.close()


def _write_info(doc, doc_id: str) -> None:
    # Human-readable breadcrumb next to the repo: which file this history is for.
    info = os.path.join(paths.repo_dir(doc_id), "draftkeeper-info.txt")
    with open(info, "w", encoding="utf-8") as f:
        f.write("title: %s\nlast_path: %s\nmachine: %s\n" % (
            doc_title(doc), doc.getURL(), paths.machine_name()))


# -- saving + fork detection --------------------------------------------------

_busy = set()  # doc ids we are re-saving ourselves; their save events are ours


def on_save_done(ctx, doc) -> None:
    if not is_writer(doc):
        return
    doc_id = get_doc_id(doc)
    if not doc_id or doc_id in _busy:
        return
    path = doc_path(doc)
    if path and is_tracked_format(path):
        locs = Locations(paths.repo_dir(doc_id), paths.machine_name())
        result = locs.check(path)
        if result == ASK:
            others = [p for p in locs.known if os.path.exists(p)]
            if ask_separate(make_ui(ctx, doc), doc, others[0]):
                split_off(doc, doc_id, others[0])
                return
            locs.remember(path)
        elif result == MOVED:
            locs.replace(path)
        else:
            locs.remember(path)
    take_snapshot(doc, KIND_AUTO)


def ask_separate(ui, doc, other_path) -> bool:
    other = os.path.basename(other_path)
    here = doc_title(doc)
    return ui.choose(
        "“%s” started as a copy of “%s”, and both files still exist.\n\n"
        "Is this a separate document (for example, a chapter you're working on), "
        "or the same document in another place?" % (here, other),
        "Separate document", "Same document",
        title="Separate document?")


def split_off(doc, old_id: str, origin_path: str) -> str:
    """Give ``doc`` its own ID and history, noting where it came from."""
    new_id = set_doc_id(doc)
    _busy.add(new_id)
    try:
        doc.store()  # persist the new ID; our own save event is ignored via _busy
    finally:
        _busy.discard(new_id)
    Locations(paths.repo_dir(new_id), paths.machine_name()).remember(doc_path(doc))
    take_snapshot(doc, KIND_MANUAL, "Started as a copy of " + os.path.basename(origin_path),
                  extra={"Derived-From": old_id})
    log.info("split %s off from %s as %s", doc_title(doc), old_id, new_id)
    return new_id


# -- commands -------------------------------------------------------------------


def ensure_tracked(ui, doc) -> bool:
    """Turn history on for ``doc`` if needed (asks first). True if tracked afterwards."""
    if not is_writer(doc):
        ui.message("Draftkeeper works with Writer documents.")
        return False
    if get_doc_id(doc):
        return True
    if not doc.hasLocation() or doc.isReadonly():
        ui.message("Please save this document first (File › Save), then try again.")
        return False
    if not ui.confirm(
        "Start keeping history for “%s”?\n\n"
        "From now on, every time you save, a snapshot is kept automatically. "
        "Use the Restore Point button any time to make a named restore point." % doc_title(doc)
    ):
        return False
    set_doc_id(doc)
    doc.store()  # persists the ID; the save hook records the first snapshot
    take_snapshot(doc, KIND_MANUAL, "History started")
    ui.notify("History is on for this document.")
    return True


def new_restore_point(ctx, doc) -> None:
    ui = make_ui(ctx, doc)
    was_tracked = is_writer(doc) and get_doc_id(doc)
    if not ensure_tracked(ui, doc) or not was_tracked:
        return
    default = "Restore point – " + datetime.datetime.now().strftime("%b %d, %Y")
    label = ui.ask("Name this restore point:", default)
    if label is None:
        return
    take_snapshot(doc, KIND_MANUAL, label or default)
    ui.notify("Restore point saved: " + (label or default))


def describe(s, every_save: bool) -> str:
    when = fmt_time(s.timestamp)
    where = "  ·  " + s.machine if s.machine else ""
    if s.kind == KIND_MANUAL:
        return "★ %s  —  %s%s" % (s.label, when, where)
    if s.kind == KIND_IMPORTED:
        return "◆ %s  —  %s (imported)" % (s.label, when)
    return "%s  —  %s%s" % ("Saved" if every_save else "End of day", when, where)


def show_versions(ctx, doc) -> None:
    ui = make_ui(ctx, doc)
    if not is_writer(doc) or not get_doc_id(doc):
        ui.message("History is not on for this document yet.\n"
                   "Click “Restore Point” to turn it on.")
        return
    doc_id = get_doc_id(doc)
    store = open_store(doc_id)
    try:
        snaps = store.history()
        named = sum(1 for s in snaps if s.is_named)
        header = "%s: %d saved versions, %d named." % (doc_title(doc), len(snaps), named)

        def rows(every_save):
            return [(describe(s, every_save), s) for s in version_list(snaps, every_save)]

        chosen = ui.pick_version(header, rows)
        if chosen is not None:
            open_copy(ctx, store, chosen, doc_title(doc))
    finally:
        store.close()


def open_copy(ctx, store, snap, title: str):
    """Open ``snap`` as a new, untitled document. Returns the new document."""
    content = store.read(snap.commit_id)
    fd, tmp = tempfile.mkstemp(suffix=".fodt", prefix="draftkeeper-version-")
    with os.fdopen(fd, "wb") as f:
        f.write(content)
    try:
        desktop = ctx.ServiceManager.createInstanceWithContext("com.sun.star.frame.Desktop", ctx)
        copy = desktop.loadComponentFromURL(
            uno.systemPathToFileUrl(tmp), "_blank", 0, (prop("AsTemplate", True),))
    finally:
        _remove(tmp)
    # The copy keeps the document's ID on purpose: saving it over the original
    # continues the history (a restore); saving it elsewhere asks "separate?".
    what = "“%s”, " % snap.label if snap.is_named else ""
    make_ui(ctx, copy).notify(
        "This is a copy of %s as of %s%s. Your document hasn't changed. "
        "To keep this copy, use File › Save As." % (title, what, fmt_time(snap.timestamp)),
        doc=copy)
    return copy


def import_versions(ctx, doc, files=None) -> int:
    """Bring older files (e.g. "Novel final FINAL.odt") into the history, dated by file time."""
    ui = make_ui(ctx, doc)
    if not ensure_tracked(ui, doc):
        return 0
    if files is None:
        files = ui.pick_files("Choose older versions of “%s”" % doc_title(doc))
    own = os.path.normcase(doc_path(doc) or "")
    files = [f for f in files if os.path.normcase(f) != own]
    if not files:
        return 0

    dated = sorted((int(os.path.getmtime(f)), f) for f in files)
    first, last = fmt_time(dated[0][0]), fmt_time(dated[-1][0])
    if not ui.confirm(
        "Add %d older version%s to the history of “%s”?\n\n"
        "They'll be dated by each file's last-modified time (%s to %s) "
        "and labeled with the file name. The files themselves aren't changed."
        % (len(dated), "" if len(dated) == 1 else "s", doc_title(doc), first, last)
    ):
        return 0

    desktop = ctx.ServiceManager.createInstanceWithContext("com.sun.star.frame.Desktop", ctx)
    versions, failed = [], []
    for when, path in dated:
        try:
            other = desktop.loadComponentFromURL(
                uno.systemPathToFileUrl(path), "_blank", 0,
                (prop("Hidden", True), prop("ReadOnly", True)))
            try:
                content = normalize(export_fodt(other))
            finally:
                other.close(True)
            versions.append(NewVersion(when, content, KIND_IMPORTED, os.path.basename(path),
                                       paths.machine_name()))
        except Exception:
            log.exception("import failed for %s", path)
            failed.append(os.path.basename(path))

    store = open_store(get_doc_id(doc))
    try:
        added = store.insert_versions(versions)
    finally:
        store.close()
    log.info("imported %d versions into %s", added, get_doc_id(doc))
    note = "Imported %d older version%s." % (added, "" if added == 1 else "s")
    if failed:
        ui.message(note + "\n\nThese files couldn't be opened:\n" + "\n".join(failed))
    else:
        ui.notify(note + " See them under Versions.")
    return added
