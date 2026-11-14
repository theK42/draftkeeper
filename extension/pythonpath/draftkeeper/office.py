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
from .config import Config
from .locations import ASK, MOVED, Locations, is_tracked_format
from .sync import is_ancestor, is_shared, sync_document
from .normalize import normalize, text_key
from .store import (KIND_AUTO, KIND_IMPORTED, KIND_MANUAL, KIND_MERGE, DocumentStore,
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


from .browser import VersionsModel, describe, fmt_time  # noqa: E402



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
        commit = store.snapshot(content, kind=kind, label=label, machine=Config().machine_name,
                                extra=extra)
        _write_info(doc, doc_id)
        path = doc_path(doc)
        if path and not doc.isModified():  # the file on disk now matches the head
            head = store.head()
            if head is not None:
                _locations(doc_id).set_file_commit(path, head.decode())
        log.info("snapshot %s kind=%s doc=%s -> %s", doc_title(doc), kind, doc_id, commit)
        if commit:
            index_later(doc_id)
        return commit
    finally:
        store.close()


def _locations(doc_id: str) -> Locations:
    return Locations(paths.repo_dir(doc_id), Config().machine_id)


def _write_info(doc, doc_id: str) -> None:
    # Human-readable breadcrumb next to the repo: which file this history is for.
    info = os.path.join(paths.repo_dir(doc_id), "draftkeeper-info.txt")
    with open(info, "w", encoding="utf-8") as f:
        f.write("title: %s\nlast_path: %s\nmachine: %s\n" % (
            doc_title(doc), doc.getURL(), Config().machine_name))


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
        locs = _locations(doc_id)
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
    report = sync_doc(doc_id, doc_title(doc))
    if report and report.head_moved:
        after_sync_changes(ctx, doc, doc_id, report)


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
    _locations(new_id).remember(doc_path(doc))
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
    sync_doc(get_doc_id(doc), doc_title(doc))


def show_versions(ctx, doc):
    """The Versions window. Returns the document it opened (a copy), if any."""
    ui = make_ui(ctx, doc)
    if not is_writer(doc) or not get_doc_id(doc):
        ui.message("History is not on for this document yet.\n"
                   "Click \u201cRestore Point\u201d to turn it on.")
        return
    store = open_store(get_doc_id(doc))
    try:
        model = VersionsModel(store, doc_title(doc))
        chosen = ui.browse_versions(model.header, model)
        if chosen is None:
            return None
        action, snap = chosen
        if action == "compare":
            return compare_with_current(ctx, doc, store, snap)
        return open_copy(ctx, store, snap, doc_title(doc))
    finally:
        store.close()


def compare_with_current(ctx, doc, store, snap):
    """A copy of the current text, with changes since ``snap`` shown as tracked changes."""
    old_fd, old_path = tempfile.mkstemp(suffix=".fodt", prefix="draftkeeper-old-")
    with os.fdopen(old_fd, "wb") as f:
        f.write(store.read(snap.commit_id))
    cur_fd, cur_path = tempfile.mkstemp(suffix=".fodt", prefix="draftkeeper-current-")
    os.close(cur_fd)
    try:
        doc.storeToURL(uno.systemPathToFileUrl(cur_path), (prop("FilterName", FODT_FILTER),))
        copy = load_internal(ctx, cur_path, (prop("AsTemplate", True),))
        dispatcher = ctx.ServiceManager.createInstanceWithContext("com.sun.star.frame.DispatchHelper", ctx)
        dispatcher.executeDispatch(
            copy.getCurrentController().getFrame(), ".uno:CompareDocuments", "", 0,
            (prop("URL", uno.systemPathToFileUrl(old_path)), prop("NoAcceptDialog", True)))
    finally:
        _remove(old_path)
        _remove(cur_path)
    what = "\u201c%s\u201d, " % snap.label if snap.is_named else ""
    make_ui(ctx, copy).notify(
        "Changes since %s%s are shown as tracked changes (Edit \u203a Track Changes \u203a Manage). "
        "This is a copy; your document hasn't changed." % (what, fmt_time(snap.timestamp)), doc=copy)
    return copy


def open_copy(ctx, store, snap, title: str):
    """Open ``snap`` as a new, untitled document. Returns the new document."""
    content = store.read(snap.commit_id)
    fd, tmp = tempfile.mkstemp(suffix=".fodt", prefix="draftkeeper-version-")
    with os.fdopen(fd, "wb") as f:
        f.write(content)
    try:
        copy = load_internal(ctx, tmp, (prop("AsTemplate", True),))
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
    store = open_store(get_doc_id(doc))
    try:
        shared = is_shared(store)
    finally:
        store.close()
    if shared:
        ui.message("Older versions can only be imported before a document is synced to "
                   "another computer or sync folder, and this one already has been.\n\n"
                   "(Importing rewrites the history's order, which would make the computers "
                   "disagree.)")
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
            other = load_internal(ctx, path, (prop("Hidden", True), prop("ReadOnly", True)))
            try:
                content = normalize(export_fodt(other))
            finally:
                other.close(True)
            versions.append(NewVersion(when, content, KIND_IMPORTED, os.path.basename(path),
                                       Config().machine_name))
        except Exception:
            log.exception("import failed for %s", path)
            failed.append(os.path.basename(path))

    store = open_store(get_doc_id(doc))
    try:
        added = store.insert_versions(versions, key=text_key)
    finally:
        store.close()
    skipped = len(versions) - added
    log.info("imported %d versions into %s (%d duplicates skipped)", added, get_doc_id(doc), skipped)
    if added:
        index_later(get_doc_id(doc))

    if added == 1:
        note = "Imported 1 older version."
    elif added:
        note = "Imported %d older versions." % added
    else:
        note = "Nothing new to import."
    if skipped == 1:
        note += " 1 file was already in the history (same text), so it was skipped."
    elif skipped:
        note += " %d files were already in the history (same text), so they were skipped." % skipped
    if failed:
        ui.message(note + "\n\nThese files couldn't be opened:\n" + "\n".join(failed))
    elif added:
        ui.notify(note + " See them under Versions.")
    else:
        ui.message(note)
    return added


# -- sync ---------------------------------------------------------------------------

_internal = [0]  # >0 while we load documents ourselves (no open-time checks for those)


def load_internal(ctx, path, props):
    desktop = ctx.ServiceManager.createInstanceWithContext("com.sun.star.frame.Desktop", ctx)
    _internal[0] += 1
    try:
        return desktop.loadComponentFromURL(uno.systemPathToFileUrl(path), "_blank", 0, props)
    finally:
        _internal[0] -= 1


def run_later(ctx, fn):
    """Run ``fn`` on the main thread after the current event finishes."""
    import unohelper
    from com.sun.star.awt import XCallback

    class Later(unohelper.Base, XCallback):
        def notify(self, data):
            try:
                fn()
            except Exception:
                log.exception("deferred action failed")

    ctx.ServiceManager.createInstanceWithContext(
        "com.sun.star.awt.AsyncCallback", ctx).addCallback(Later(), None)


def index_later(doc_id: str) -> None:
    """Index new versions for search in a background thread (pure Python, no UNO calls)."""
    import threading
    from .search import SearchIndex, lock_for, update

    def work():
        try:
            store = open_store(doc_id)
            try:
                with lock_for(store.repo_dir):
                    update(SearchIndex(store.repo_dir), store)
            finally:
                store.close()
        except Exception:
            log.exception("background indexing failed for %s", doc_id)

    threading.Thread(target=work, name="draftkeeper-index", daemon=True).start()


def sync_doc(doc_id: str, title: str):
    """Sync one document with every reachable sync folder. None if there are none."""
    cfg = Config()
    folders = cfg.reachable()
    if not folders:
        return None
    store = open_store(doc_id)
    try:
        report = sync_document(store, doc_id, folders, cfg.machine_id, cfg.machine_name, title)
    finally:
        store.close()
    log.info("sync %s: synced=%s events=%s errors=%s", doc_id, report.synced,
             [(e.kind, e.machine) for e in report.events], report.errors)
    if report.head_moved:
        index_later(doc_id)
    return report


def newer_version(doc, doc_id: str):
    """If history has newer work than this file, the newest Snapshot; else None."""
    path = doc_path(doc)
    store = open_store(doc_id)
    try:
        head = store.head()
        if head is None or not path:
            return None
        locs = _locations(doc_id)
        saved_as = locs.file_commit(path)
        if saved_as == head.decode():
            return None
        if saved_as and saved_as.encode() in store.repo.object_store:
            if store._blob_of(saved_as.encode()) == store._blob_of(head):
                locs.set_file_commit(path, head.decode())  # e.g. a merge that kept this text
                return None
            is_older = is_ancestor(store, saved_as.encode(), head)
        else:
            # A file this machine hasn't saved (e.g. copied from the thumb drive):
            # compare its words with recent versions.
            key = text_key(normalize(export_fodt(doc)))
            history = store.history()
            if key == text_key(store.read(history[0].commit_id)):
                locs.set_file_commit(path, history[0].commit_id)
                return None
            is_older = any(key == text_key(store.read(s.commit_id)) for s in history[1:50])
        return store.history()[0] if is_older else None
    finally:
        store.close()


def on_load(ctx, doc) -> None:
    if _internal[0] or not is_writer(doc) or not doc.hasLocation():
        return
    doc_id = get_doc_id(doc)
    if not doc_id:
        return
    sync_doc(doc_id, doc_title(doc))
    offer_newer(ctx, doc, doc_id)


def offer_newer(ctx, doc, doc_id: str) -> None:
    newer = newer_version(doc, doc_id)
    if newer is None:
        return
    who = newer.extra.get("Kept") or newer.machine or "another computer"
    if make_ui(ctx, doc).choose(
        "There's newer work on “%s” than this file has: saved on %s, %s.\n\n"
        "Open the newer version? (This file will be updated to match. "
        "Nothing is lost: the version you have now stays in Versions.)"
        % (doc_title(doc), who, fmt_time(newer.timestamp)),
        "Open newer version", "Keep this one", title="Newer version available"
    ):
        run_later(ctx, lambda: open_newer(ctx, doc, doc_id))


def open_newer(ctx, doc, doc_id: str):
    """Replace ``doc``'s file with the newest version and reopen it. Returns the new doc."""
    path = doc_path(doc)
    args = {a.Name: a.Value for a in doc.getArgs()}
    filter_name = args.get("FilterName") or "writer8"
    store = open_store(doc_id)
    try:
        head = store.head().decode()
        content = store.read(head)
    finally:
        store.close()
    doc.close(True)

    fd, tmp = tempfile.mkstemp(suffix=".fodt", prefix="draftkeeper-newer-")
    with os.fdopen(fd, "wb") as f:
        f.write(content)
    try:
        newer = load_internal(ctx, tmp, (prop("Hidden", True),))
        try:
            newer.storeToURL(uno.systemPathToFileUrl(path),
                             (prop("FilterName", filter_name), prop("Overwrite", True)))
        finally:
            newer.close(True)
    finally:
        _remove(tmp)
    _locations(doc_id).set_file_commit(path, head)
    reopened = load_internal(ctx, path, ())
    make_ui(ctx, reopened).notify("Opened the newest version.", doc=reopened)
    log.info("replaced %s with newer version %s", path, head)
    return reopened


def after_sync_changes(ctx, doc, doc_id: str, report) -> None:
    """After a save-time sync moved the history: tell her, and offer the newer text if needed."""
    for e in report.events:
        if e.kind == "merged":
            if e.local_won:
                make_ui(ctx, doc).notify(
                    "%s also had edits that weren't in this file. Your version here is kept as "
                    "the current one; %s's edits are saved in Versions." % (e.machine, e.machine))
                _locations(doc_id).set_file_commit(doc_path(doc), e.head)
            else:
                offer_newer(ctx, doc, doc_id)
            return
    offer_newer(ctx, doc, doc_id)


def on_close(ctx, doc) -> None:
    if _internal[0] or not is_writer(doc):
        return
    doc_id = get_doc_id(doc)
    if doc_id:
        sync_doc(doc_id, doc_title(doc))


def describe_report(report, cfg) -> str:
    lines = []
    if report.synced:
        lines.append("Synced with: " + ", ".join(report.synced) + ".")
    reached = set(report.synced)
    missing = [f.name for f in cfg.folders if f.name not in reached]
    if missing:
        lines.append("Not reachable right now: " + ", ".join(missing) + ".")
    for e in report.events:
        if e.kind == "fast_forward":
            lines.append("Brought in newer saves from %s." % e.machine)
        else:
            lines.append("Combined edits from %s; kept %s's version as current."
                         % (e.machine, e.winner_machine))
    for err in report.errors:
        lines.append("Problem: " + err)
    return "\n".join(lines) or "Nothing to sync."


def sync_now(ctx, doc) -> None:
    ui = make_ui(ctx, doc)
    cfg = Config()
    if not cfg.folders:
        ui.message("No sync folders yet. Use Draftkeeper › Add Sync Folder… first.")
        return
    if not is_writer(doc) or not get_doc_id(doc):
        ui.message("History is not on for this document yet.\n"
                   "Click “Restore Point” to turn it on.")
        return
    doc_id = get_doc_id(doc)
    report = sync_doc(doc_id, doc_title(doc))
    if report is None:
        ui.message("None of your sync folders can be reached right now (%s)."
                   % ", ".join(f.name for f in cfg.folders))
        return
    ui.message(describe_report(report, Config()))
    if report.head_moved and not doc.isModified():
        offer_newer(ctx, doc, doc_id)


def add_sync_folder(ctx, doc, chosen=None, name=None):
    ui = make_ui(ctx, doc)
    if chosen is None:
        chosen = ui.pick_folder("Choose a sync folder (thumb drive, Proton Drive, ...)")
        if not chosen:
            return None
    if name is None:
        name = ui.ask("Name for this sync folder:", guess_folder_name(chosen), title="Add Sync Folder")
        if name is None:
            return None
    folder = Config().add_folder(chosen, name or guess_folder_name(chosen))
    msg = "Added “%s” (%s).\nDraftkeeper syncs there whenever it's reachable." % (
        folder.name, folder.path)
    if is_writer(doc) and get_doc_id(doc):
        report = sync_doc(get_doc_id(doc), doc_title(doc))
        if report:
            msg += "\n\n" + describe_report(report, Config())
    ui.message(msg)
    return folder


def guess_folder_name(path: str) -> str:
    low = path.lower()
    if "proton" in low:
        return "Proton Drive"
    parts = os.path.normpath(path).split(os.sep)
    if len(parts) > 2 and parts[1] == "Volumes":
        return parts[2]  # macOS volume name, e.g. the thumb drive's label
    if len(path) >= 2 and path[1] == ":":
        return "Drive %s:" % path[0].upper()
    return os.path.basename(os.path.normpath(path)) or path
