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

    def browse_versions(self, header: str, provider):
        """The Versions window: search, date range, list, preview, Open / Compare.

        ``provider`` supplies content:
          provider.rows(query, every_save, date_from, date_to) -> (summary, [(text, key)])
          provider.preview(key, query) -> str
        Dates are ``datetime.date`` or None. Returns ("open" | "compare", key) or None.
        """
        import datetime

        W = 340
        model = self._dialog("Versions", W, 268)
        add = lambda *a, **k: self._add(model, *a, **k)
        add("FixedText", "header", PositionX=8, PositionY=6, Width=W - 16, Height=10, Label=header)
        add("FixedText", "findlabel", PositionX=8, PositionY=22, Width=30, Height=10, Label="Find:")
        add("Edit", "query", PositionX=40, PositionY=20, Width=190, Height=14)
        add("Button", "find", PositionX=234, PositionY=20, Width=48, Height=14, Label="Search",
            DefaultButton=True)
        add("Button", "clear", PositionX=286, PositionY=20, Width=46, Height=14, Label="Clear")
        add("FixedText", "fromlabel", PositionX=8, PositionY=40, Width=30, Height=10, Label="From:")
        add("DateField", "from", PositionX=40, PositionY=38, Width=74, Height=14, Dropdown=True,
            StrictFormat=False)
        add("FixedText", "tolabel", PositionX=118, PositionY=40, Width=14, Height=10, Label="to:")
        add("DateField", "to", PositionX=134, PositionY=38, Width=74, Height=14, Dropdown=True,
            StrictFormat=False)
        add("CheckBox", "all", PositionX=216, PositionY=40, Width=116, Height=10, Label="Show every save")
        add("FixedText", "summary", PositionX=8, PositionY=58, Width=W - 16, Height=26, MultiLine=True)
        add("ListBox", "list", PositionX=8, PositionY=86, Width=W - 16, Height=118, Border=1)
        add("FixedText", "preview", PositionX=8, PositionY=208, Width=W - 16, Height=32, MultiLine=True)
        add("Button", "compare", PositionX=118, PositionY=246, Width=88, Height=14,
            Label="Compare with Current")
        add("Button", "open", PositionX=210, PositionY=246, Width=70, Height=14, Label="Open as Copy")
        add("Button", "close", PositionX=284, PositionY=246, Width=48, Height=14, Label="Close",
            PushButtonType=CANCEL)

        state = {"keys": [], "query": ""}

        def date_of(dialog, name):
            ctl = dialog.getControl(name)
            if ctl.isEmpty():
                return None
            d = ctl.getDate()
            try:
                return datetime.date(d.Year, d.Month, d.Day)
            except ValueError:
                return None

        def refresh_preview(dialog):
            pos = dialog.getControl("list").getSelectedItemPos()
            text = ""
            if 0 <= pos < len(state["keys"]):
                text = provider.preview(state["keys"][pos], state["query"])
            dialog.getControl("preview").setText(text)

        def fill(dialog):
            state["query"] = dialog.getControl("query").getText().strip()
            summary, rows = provider.rows(
                state["query"], dialog.getControl("all").getState() == 1,
                date_of(dialog, "from"), date_of(dialog, "to"))
            dialog.getControl("summary").setText(summary)
            lb = dialog.getControl("list")
            lb.removeItems(0, lb.getItemCount())
            lb.addItems(tuple(t for t, _ in rows), 0)
            state["keys"] = [k for _, k in rows]
            if rows:
                lb.selectItemPos(0, True)
            refresh_preview(dialog)

        def selected():
            pos = state["dialog"].getControl("list").getSelectedItemPos()
            return state["keys"][pos] if 0 <= pos < len(state["keys"]) else None

        class Action(unohelper.Base, XActionListener):
            def __init__(self, fn):
                self.fn = fn

            def actionPerformed(self, event):
                self.fn()

            def disposing(self, event):
                pass

        class Changed(unohelper.Base, XItemListener):
            def __init__(self, fn):
                self.fn = fn

            def itemStateChanged(self, event):
                self.fn()

            def disposing(self, event):
                pass

        def finish(code):
            if selected() is not None:
                state["dialog"].endDialog(code)

        def clear():
            d = state["dialog"]
            d.getControl("query").setText("")
            d.getControl("from").setEmpty()
            d.getControl("to").setEmpty()
            fill(d)

        # Test seam: lets the headless smoke test drive the real dialog's refresh logic.
        self._browser_hooks = {"fill": fill, "preview": refresh_preview}

        def setup(dialog):
            state["dialog"] = dialog
            dialog.getControl("from").setEmpty()
            dialog.getControl("to").setEmpty()
            fill(dialog)
            dialog.getControl("find").addActionListener(Action(lambda: fill(dialog)))
            dialog.getControl("clear").addActionListener(Action(clear))
            dialog.getControl("all").addItemListener(Changed(lambda: fill(dialog)))
            dialog.getControl("list").addItemListener(Changed(lambda: refresh_preview(dialog)))
            dialog.getControl("list").addActionListener(Action(lambda: finish(1)))  # double-click
            dialog.getControl("open").addActionListener(Action(lambda: finish(1)))
            dialog.getControl("compare").addActionListener(Action(lambda: finish(3)))
            dialog.getControl("query").setFocus()

        code, dialog, _ = self._run(model, setup)
        try:
            key = selected()
            if key is None:
                return None
            return {1: ("open", key), 3: ("compare", key)}.get(code)
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
