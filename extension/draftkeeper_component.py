"""UNO component registration for the Draftkeeper extension.

Two services:
  com.draftkeeper.Commands  - toolbar/menu commands (XJobExecutor.trigger)
  com.draftkeeper.EventJob  - document events: save, open, close (bound in Jobs.xcu)

LibreOffice adds this extension's ``pythonpath/`` folder to sys.path.
Errors are logged and swallowed: Draftkeeper must never break a save.
"""

import traceback

import unohelper
from com.sun.star.task import XJob, XJobExecutor

from draftkeeper import office

office.setup_logging()


def _env(args):
    """(event name, document) from a job's arguments."""
    event, model = None, None
    for nv in args:
        if nv.Name == "Environment":
            for e in nv.Value:
                if e.Name == "EventName":
                    event = e.Value
                elif e.Name == "Model":
                    model = e.Value
    return event, model


class Commands(unohelper.Base, XJobExecutor):
    def __init__(self, ctx):
        self.ctx = ctx

    def trigger(self, command):
        doc = None
        try:
            desktop = self.ctx.ServiceManager.createInstanceWithContext(
                "com.sun.star.frame.Desktop", self.ctx)
            doc = desktop.getCurrentComponent()
            if command == "restorepoint":
                office.new_restore_point(self.ctx, doc)
            elif command == "versions":
                office.show_versions(self.ctx, doc)
            elif command == "import":
                office.import_versions(self.ctx, doc)
            elif command == "syncnow":
                office.sync_now(self.ctx, doc)
            elif command == "addsync":
                office.add_sync_folder(self.ctx, doc)
        except Exception:
            office.log.error("command %s failed:\n%s", command, traceback.format_exc())
            try:
                office.make_ui(self.ctx, doc).message(
                    "Something went wrong. Details are in the Draftkeeper log.")
            except Exception:
                pass


class EventJob(unohelper.Base, XJob):
    """Document events bound in Jobs.xcu: save, open, close."""

    def __init__(self, ctx):
        self.ctx = ctx

    def execute(self, args):
        event, doc = _env(args)
        try:
            if event in ("OnSaveDone", "OnSaveAsDone"):
                office.on_save_done(self.ctx, doc)
            elif event == "OnLoad":
                office.on_load(self.ctx, doc)
            elif event == "OnPrepareUnload":
                office.on_close(self.ctx, doc)
        except Exception:
            office.log.error("%s handler failed:\n%s", event, traceback.format_exc())
        return None


g_ImplementationHelper = unohelper.ImplementationHelper()
g_ImplementationHelper.addImplementation(
    Commands, "com.draftkeeper.Commands", ("com.sun.star.task.Job",))
g_ImplementationHelper.addImplementation(
    EventJob, "com.draftkeeper.EventJob", ("com.sun.star.task.Job",))
