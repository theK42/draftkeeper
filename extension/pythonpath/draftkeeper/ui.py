"""Dialogs and notifications, built with plain UNO controls (no .xdl files)."""

import uno
import unohelper
from com.sun.star.awt import XActionListener, XItemListener

OK, CANCEL = 1, 2  # com.sun.star.awt.PushButtonType


class UI:
    def __init__(self, ctx, doc):
        self.ctx = ctx
        self.doc = doc
        self.smgr = ctx.ServiceManager
        self.toolkit = self.smgr.createInstanceWithContext("com.sun.star.awt.Toolkit", ctx)
        try:
            self.window = doc.getCurrentController().getFrame().getContainerWindow()
        except Exception:
            self.window = None

    # -- simple boxes ----------------------------------------------------

    def _box(self, kind, buttons, text):
        box_type = uno.Enum("com.sun.star.awt.MessageBoxType", kind)
        box = self.toolkit.createMessageBox(self.window, box_type, buttons, "Draftkeeper", text)
        return box.execute()

    def message(self, text: str) -> None:
        self._box("INFOBOX", 1, text)  # BUTTONS_OK

    def confirm(self, text: str) -> bool:
        return self._box("QUERYBOX", 3, text) == 2  # BUTTONS_YES_NO -> YES

    def notify(self, text: str, doc=None) -> None:
        """Non-blocking infobar at the top of the document; falls back to a box."""
        try:
            ctrl = (doc or self.doc).getCurrentController()
            try:
                ctrl.removeInfobar("draftkeeper")
            except Exception:
                pass
            ctrl.appendInfobar("draftkeeper", "Draftkeeper", text, 1, (), True)  # 1 = SUCCESS
        except Exception:
            self.message(text)

    # -- dialog building -------------------------------------------------

    def _dialog(self, title, width, height):
        model = self.smgr.createInstanceWithContext("com.sun.star.awt.UnoControlDialogModel", self.ctx)
        model.Title = title
        model.Width, model.Height = width, height
        return model

    @staticmethod
    def _add(model, kind, name, **props):
        m = model.createInstance("com.sun.star.awt.UnoControl%sModel" % kind)
        for k, v in props.items():
            setattr(m, k, v)
        model.insertByName(name, m)
        return m

    def _run(self, model, setup=None):
        dialog = self.smgr.createInstanceWithContext("com.sun.star.awt.UnoControlDialog", self.ctx)
        dialog.setModel(model)
        dialog.createPeer(self.toolkit, self.window)
        try:
            result = setup(dialog) if setup else None
            code = dialog.execute()
            return code, dialog, result
        except Exception:
            dialog.dispose()
            raise

    # -- dialogs -----------------------------------------------------------

    def ask(self, prompt: str, default: str = "", title: str = "New Restore Point"):
        """Single-line text input. Returns the text, or None if cancelled."""
        model = self._dialog(title, 220, 62)
        self._add(model, "FixedText", "prompt", PositionX=8, PositionY=8, Width=204, Height=10, Label=prompt)
        self._add(model, "Edit", "label", PositionX=8, PositionY=20, Width=204, Height=14, Text=default)
        self._add(model, "Button", "ok", PositionX=110, PositionY=42, Width=48, Height=14, Label="Save",
                  PushButtonType=OK, DefaultButton=True)
        self._add(model, "Button", "cancel", PositionX=164, PositionY=42, Width=48, Height=14,
                  Label="Cancel", PushButtonType=CANCEL)

        def setup(dialog):
            edit = dialog.getControl("label")
            edit.setSelection(uno.createUnoStruct("com.sun.star.awt.Selection", 0, len(default)))
            edit.setFocus()

        code, dialog, _ = self._run(model, setup)
        try:
            return dialog.getControl("label").getText().strip() if code == 1 else None
        finally:
            dialog.dispose()

    def choose(self, text: str, first: str, second: str, title: str = "Draftkeeper") -> bool:
        """Two-button question. True for ``first``; False for ``second`` or Escape."""
        lines = text.count("\n") + 1 + len(text) // 70
        height = 10 * lines + 8
        model = self._dialog(title, 260, height + 34)
        self._add(model, "FixedText", "text", PositionX=8, PositionY=8, Width=244, Height=height,
                  Label=text, MultiLine=True)
        self._add(model, "Button", "first", PositionX=60, PositionY=height + 14, Width=94, Height=14,
                  Label=first, PushButtonType=OK, DefaultButton=True)
        self._add(model, "Button", "second", PositionX=158, PositionY=height + 14, Width=94, Height=14,
                  Label=second, PushButtonType=CANCEL)
        code, dialog, _ = self._run(model)
        dialog.dispose()
        return code == 1

    def pick_version(self, header: str, load_rows):
        """Version list. ``load_rows(every_save) -> [(text, key)]``. Returns a key or None."""
        model = self._dialog("Versions", 300, 214)
        self._add(model, "FixedText", "header", PositionX=8, PositionY=6, Width=284, Height=10, Label=header)
        self._add(model, "ListBox", "list", PositionX=8, PositionY=20, Width=284, Height=156, Border=1)
        self._add(model, "CheckBox", "all", PositionX=8, PositionY=182, Width=120, Height=10,
                  Label="Show every save")
        self._add(model, "Button", "open", PositionX=150, PositionY=194, Width=78, Height=14,
                  Label="Open as Copy", PushButtonType=OK, DefaultButton=True)
        self._add(model, "Button", "close", PositionX=232, PositionY=194, Width=60, Height=14,
                  Label="Close", PushButtonType=CANCEL)

        state = {"keys": []}

        def fill(dialog, every_save):
            rows = load_rows(every_save)
            lb = dialog.getControl("list")
            lb.removeItems(0, lb.getItemCount())
            lb.addItems(tuple(t for t, _ in rows), 0)
            state["keys"] = [k for _, k in rows]
            if rows:
                lb.selectItemPos(0, True)

        class Toggle(unohelper.Base, XItemListener):
            def __init__(self, dialog):
                self.dialog = dialog

            def itemStateChanged(self, event):
                fill(self.dialog, self.dialog.getControl("all").getState() == 1)

            def disposing(self, event):
                pass

        class DoubleClick(unohelper.Base, XActionListener):
            def __init__(self, dialog):
                self.dialog = dialog

            def actionPerformed(self, event):
                self.dialog.endDialog(1)

            def disposing(self, event):
                pass

        def setup(dialog):
            fill(dialog, False)
            dialog.getControl("all").addItemListener(Toggle(dialog))
            dialog.getControl("list").addActionListener(DoubleClick(dialog))

        code, dialog, _ = self._run(model, setup)
        try:
            pos = dialog.getControl("list").getSelectedItemPos()
            if code == 1 and 0 <= pos < len(state["keys"]):
                return state["keys"][pos]
            return None
        finally:
            dialog.dispose()

    def pick_folder(self, title: str):
        """Folder picker. Returns a system path or None."""
        picker = self.smgr.createInstanceWithContext("com.sun.star.ui.dialogs.FolderPicker", self.ctx)
        picker.setTitle(title)
        if picker.execute() != 1:
            return None
        return uno.fileUrlToSystemPath(picker.getDirectory())

    def pick_files(self, title: str):
        """Multi-select file picker for documents. Returns system paths."""
        picker = self.smgr.createInstanceWithArgumentsAndContext(
            "com.sun.star.ui.dialogs.FilePicker", (0,), self.ctx)  # TemplateDescription.FILEOPEN_SIMPLE
        picker.setTitle(title)
        picker.setMultiSelectionMode(True)
        picker.appendFilter("Documents (*.odt, *.fodt, *.docx, *.doc, *.rtf)", "*.odt;*.fodt;*.docx;*.doc;*.rtf")
        picker.appendFilter("All files", "*.*")
        picker.setCurrentFilter("Documents (*.odt, *.fodt, *.docx, *.doc, *.rtf)")
        if picker.execute() != 1:
            return []
        try:
            urls = picker.getSelectedFiles()
        except Exception:
            urls = picker.getFiles()
            if len(urls) > 1:  # old API: folder URL, then names
                base = urls[0].rstrip("/") + "/"
                urls = [base + n for n in urls[1:]]
        return [uno.fileUrlToSystemPath(u) for u in urls]
