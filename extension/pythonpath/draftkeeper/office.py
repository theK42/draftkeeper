"""Everything that talks to LibreOffice (UNO). Only importable inside LibreOffice."""

import datetime
import logging
import os
import tempfile
import uuid

import uno
from com.sun.star.beans import PropertyValue

from . import paths
from .normalize import normalize
from .store import KIND_AUTO, KIND_MANUAL, DocumentStore

ID_PROPERTY = "Draftkeeper ID"
FODT_FILTER = "OpenDocument Text Flat XML"
REMOVEABLE = 128  # com.sun.star.beans.PropertyAttribute.REMOVEABLE

log = logging.getLogger("draftkeeper")


def setup_logging() -> None:
    if log.handlers:
        return
    os.makedirs(paths.data_dir(), exist_ok=True)
    handler = logging.FileHandler(os.path.join(paths.data_dir(), "draftkeeper.log"), encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)


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


def set_doc_id(doc) -> str:
    doc_id = str(uuid.uuid4())
    udp = doc.getDocumentProperties().getUserDefinedProperties()
    udp.addProperty(ID_PROPERTY, REMOVEABLE, doc_id)
    return doc_id


def doc_title(doc) -> str:
    url = doc.getURL()
    if url:
        return os.path.basename(uno.fileUrlToSystemPath(url))
    return doc.getTitle() if hasattr(doc, "getTitle") else "Untitled"


def export_fodt(doc) -> bytes:
    """Current in-memory state of ``doc`` as Flat ODT, without touching its file."""
    fd, tmp = tempfile.mkstemp(suffix=".fodt", prefix="draftkeeper-")
    os.close(fd)
    try:
        prop = PropertyValue()
        prop.Name, prop.Value = "FilterName", FODT_FILTER
        doc.storeToURL(uno.systemPathToFileUrl(tmp), (prop,))
        with open(tmp, "rb") as f:
            return f.read()
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def take_snapshot(doc, kind: str, label: str = ""):
    doc_id = get_doc_id(doc)
    if not doc_id:
        return None
    content = normalize(export_fodt(doc))
    store = DocumentStore(paths.repo_dir(doc_id))
    try:
        commit = store.snapshot(content, kind=kind, label=label, machine=paths.machine_name())
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


# -- event + command entry points ------------------------------------------


def on_save_done(doc) -> None:
    if is_writer(doc) and get_doc_id(doc):
        take_snapshot(doc, KIND_AUTO)


def new_restore_point(ctx, doc) -> None:
    ui = UI(ctx, doc)
    if not is_writer(doc):
        ui.message("Draftkeeper works with Writer documents.")
        return

    if not get_doc_id(doc):
        if not doc.hasLocation() or doc.isReadonly():
            ui.message("Please save this document first (File › Save), then try again.")
            return
        if not ui.confirm(
            "Start keeping history for “%s”?\n\n"
            "From now on, every time you save, a snapshot is kept automatically. "
            "Use this button any time to make a named restore point." % doc_title(doc)
        ):
            return
        set_doc_id(doc)
        doc.store()  # persists the ID; the save hook records the first snapshot
        take_snapshot(doc, KIND_MANUAL, "History started")
        ui.notify("History is on for this document.")
        return

    default = "Restore point – " + datetime.datetime.now().strftime("%b %d, %Y")
    label = ui.ask("Name this restore point:", default)
    if label is None:
        return
    take_snapshot(doc, KIND_MANUAL, label or default)
    ui.notify("Restore point saved: " + (label or default))


def show_info(ctx, doc) -> None:
    ui = UI(ctx, doc)
    doc_id = get_doc_id(doc) if is_writer(doc) else None
    if not doc_id:
        ui.message("History is not on for this document yet.\nClick “Restore Point” to turn it on.")
        return
    store = DocumentStore(paths.repo_dir(doc_id))
    try:
        snaps = store.history()
    finally:
        store.close()
    manual = [s for s in snaps if s.kind == KIND_MANUAL]
    lines = ["%d snapshots, %d named restore points." % (len(snaps), len(manual)), ""]
    for s in manual[:10]:
        when = datetime.datetime.fromtimestamp(s.timestamp).strftime("%b %d, %Y %I:%M %p")
        lines.append("• %s  (%s, %s)" % (s.label, when, s.machine))
    lines += ["", "Stored in: " + paths.repo_dir(doc_id)]
    ui.message("\n".join(lines))


# -- small UI toolkit ---------------------------------------------------------


class UI:
    def __init__(self, ctx, doc):
        self.ctx = ctx
        self.doc = doc
        smgr = ctx.ServiceManager
        self.toolkit = smgr.createInstanceWithContext("com.sun.star.awt.Toolkit", ctx)
        try:
            self.window = doc.getCurrentController().getFrame().getContainerWindow()
        except Exception:
            self.window = None

    def _box(self, kind, buttons, text):
        box_type = uno.Enum("com.sun.star.awt.MessageBoxType", kind)
        box = self.toolkit.createMessageBox(self.window, box_type, buttons, "Draftkeeper", text)
        return box.execute()

    def message(self, text: str) -> None:
        self._box("INFOBOX", 1, text)  # BUTTONS_OK

    def confirm(self, text: str) -> bool:
        return self._box("QUERYBOX", 3, text) == 2  # BUTTONS_YES_NO -> YES

    def notify(self, text: str) -> None:
        """Non-blocking infobar at the top of the document; falls back to a box."""
        try:
            ctrl = self.doc.getCurrentController()
            ident = "draftkeeper"
            try:
                ctrl.removeInfobar(ident)
            except Exception:
                pass
            ctrl.appendInfobar(ident, "Draftkeeper", text, 1, (), True)  # 1 = InfobarType.SUCCESS
        except Exception:
            self.message(text)

    def ask(self, prompt: str, default: str = ""):
        """Single-line text input. Returns the text, or None if cancelled."""
        smgr = self.ctx.ServiceManager
        model = smgr.createInstanceWithContext("com.sun.star.awt.UnoControlDialogModel", self.ctx)
        model.Title = "New Restore Point"
        model.Width, model.Height = 220, 62

        def add(kind, name, **props):
            m = model.createInstance("com.sun.star.awt.UnoControl%sModel" % kind)
            for k, v in props.items():
                setattr(m, k, v)
            model.insertByName(name, m)

        add("FixedText", "prompt", PositionX=8, PositionY=8, Width=204, Height=10, Label=prompt)
        add("Edit", "label", PositionX=8, PositionY=20, Width=204, Height=14, Text=default)
        add("Button", "ok", PositionX=110, PositionY=42, Width=48, Height=14, Label="Save",
            PushButtonType=1, DefaultButton=True)  # OK
        add("Button", "cancel", PositionX=164, PositionY=42, Width=48, Height=14, Label="Cancel",
            PushButtonType=2)  # CANCEL

        dialog = smgr.createInstanceWithContext("com.sun.star.awt.UnoControlDialog", self.ctx)
        dialog.setModel(model)
        dialog.createPeer(self.toolkit, self.window)
        edit = dialog.getControl("label")
        edit.setSelection(uno.createUnoStruct("com.sun.star.awt.Selection", 0, len(default)))
        edit.setFocus()
        try:
            if dialog.execute() != 1:
                return None
            return edit.getText().strip()
        finally:
            dialog.dispose()
