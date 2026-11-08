"""UNO component registration for the Draftkeeper extension.

Two services:
  com.draftkeeper.Commands  - toolbar/menu commands (XJobExecutor.trigger)
  com.draftkeeper.SaveJob   - runs after every save (bound in Jobs.xcu)

LibreOffice adds this extension's ``pythonpath/`` folder to sys.path.
Errors are logged and swallowed: Draftkeeper must never break a save.
"""

import traceback

import unohelper
from com.sun.star.task import XJob, XJobExecutor

from draftkeeper import office

office.setup_logging()


def _doc_from_env(args):
    for nv in args:
        if nv.Name == "Environment":
            for e in nv.Value:
                if e.Name == "Model":
                    return e.Value
    return None


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
        except Exception:
            office.log.error("command %s failed:\n%s", command, traceback.format_exc())
            try:
                office.make_ui(self.ctx, doc).message(
                    "Something went wrong. Details are in the Draftkeeper log.")
            except Exception:
                pass


class SaveJob(unohelper.Base, XJob):
    def __init__(self, ctx):
        self.ctx = ctx

    def execute(self, args):
        try:
            office.on_save_done(self.ctx, _doc_from_env(args))
        except Exception:
            office.log.error("save snapshot failed:\n%s", traceback.format_exc())
        return None


g_ImplementationHelper = unohelper.ImplementationHelper()
g_ImplementationHelper.addImplementation(
    Commands, "com.draftkeeper.Commands", ("com.sun.star.task.Job",))
g_ImplementationHelper.addImplementation(
    SaveJob, "com.draftkeeper.SaveJob", ("com.sun.star.task.Job",))
