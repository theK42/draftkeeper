"""Smoke test of the built extension inside a headless LibreOffice.

Run with any Python 3, with LibreOffice closed:
  python3 tests/office_smoke.py

LibreOffice 26.x only lets its bundled Python run when LibreOffice itself
launches it (a macOS launch constraint), so the checks run as a Python macro
inside soffice, and report back through a JSON file.

Uses a throwaway LibreOffice profile and DRAFTKEEPER_HOME, so neither your
real profile nor your real history is touched.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import quote
from urllib.request import pathname2url

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
APP = "/Applications/LibreOffice.app/Contents/MacOS"
OXT = os.path.join(ROOT, "dist", "Draftkeeper.oxt")

MACRO = r'''
import json, os, sys, time, traceback
import uno
from com.sun.star.beans import PropertyValue

import unohelper
from com.sun.star.ucb import XCommandEnvironment, XProgressHandler
from com.sun.star.task import XInteractionHandler, XInteractionApprove

class _Approve(unohelper.Base, XCommandEnvironment, XInteractionHandler, XProgressHandler):
    """Says yes to install prompts, as the user would by clicking OK."""
    def getInteractionHandler(self): return self
    def getProgressHandler(self): return self
    def handle(self, request):
        for c in request.getContinuations():
            if c.queryInterface(uno.getTypeByName("com.sun.star.task.XInteractionApprove")):
                c.select()
                return
    def push(self, status): pass
    def update(self, status): pass
    def pop(self): pass

def install(*args):
    """Install the .oxt through LibreOffice's own Extension Manager, like a double-click."""
    out = os.environ["DK_SMOKE_RESULTS"] + ".install"
    try:
        ctx = XSCRIPTCONTEXT.getComponentContext()
        mgr = ctx.getValueByName("/singletons/com.sun.star.deployment.ExtensionManager")
        mgr.addExtension(uno.systemPathToFileUrl(os.environ["DK_SMOKE_OXT"]), (), "user", None, _Approve())
        names = [e[1].getIdentifier().Value for e in mgr.getAllExtensions(None, _Approve()) if e[1]]
        msg = "ok " + ",".join(names)
    except Exception as e:
        msg = traceback.format_exc()
        cause = getattr(e, "Cause", None)
        while cause is not None:
            msg += "\ncaused by: %r %s" % (type(cause).__name__, getattr(cause, "Message", cause))
            cause = getattr(cause, "Cause", None) if hasattr(cause, "Cause") else None
    with open(out, "w") as f:
        f.write(msg)
    XSCRIPTCONTEXT.getDesktop().terminate()

def main(*args):
    results = []
    out = os.environ["DK_SMOKE_RESULTS"]
    work = os.environ["DK_SMOKE_WORK"]
    home = os.environ["DRAFTKEEPER_HOME"]
    sys.path.insert(0, os.environ["DK_SMOKE_PYTHONPATH"])
    def check(name, ok, detail=""):
        results.append({"name": name, "ok": bool(ok), "detail": detail})
    desktop = XSCRIPTCONTEXT.getDesktop()
    try:
        from draftkeeper.store import DocumentStore
        from draftkeeper.normalize import extract_text
        def count(repo):
            s = DocumentStore(repo)
            try:
                return len(s.history())
            finally:
                s.close()

        hidden = PropertyValue(); hidden.Name, hidden.Value = "Hidden", True
        doc = desktop.loadComponentFromURL("private:factory/swriter", "_blank", 0, (hidden,))
        text = doc.getText()
        text.setString("It was a dark and stormy night.")
        doc.storeAsURL(uno.systemPathToFileUrl(os.path.join(work, "Novel.odt")), ())
        docs = os.path.join(home, "documents")
        check("untracked save leaves no history", not os.path.isdir(docs) or not os.listdir(docs))

        doc.getDocumentProperties().getUserDefinedProperties().addProperty(
            "Draftkeeper ID", 128, "smoke-test-id")
        doc.store()
        repo = os.path.join(docs, "smoke-test-id.git")
        check("tracked save creates repo", os.path.isdir(repo))
        check("first snapshot recorded", os.path.isdir(repo) and count(repo) == 1)

        text.setString("It was a dark and stormy night.\nThe rain fell in torrents.")
        doc.store()
        n = count(repo)
        check("edit + save adds snapshot", n == 2, "count=%d" % n)
        s = DocumentStore(repo)
        latest = extract_text(s.read(s.history()[0].commit_id)); s.close()
        check("snapshot has new text", "The rain fell in torrents." in latest, latest)

        doc.store()
        n = count(repo)
        check("unchanged save adds nothing", n == 2, "count=%d" % n)

        # The normalized snapshot must open in LibreOffice with the same text.
        snap = os.path.join(work, "snapshot.fodt")
        s = DocumentStore(repo)
        open(snap, "wb").write(s.read(s.history()[0].commit_id)); s.close()
        reopened = desktop.loadComponentFromURL(uno.systemPathToFileUrl(snap), "_blank", 0, (hidden,))
        check("snapshot reopens with same text",
              reopened.getText().getString() == text.getString(),
              repr(reopened.getText().getString()))
        reopened.close(True)
        doc.close(True)
    except Exception:
        check("macro ran without error", False, traceback.format_exc())
    finally:
        with open(out, "w") as f:
            json.dump(results, f)
        desktop.terminate()
'''


def file_url(path):
    return "file://" + pathname2url(path)


def main():
    work = tempfile.mkdtemp(prefix="dk-smoke-")
    profile = os.path.join(work, "profile")
    results = os.path.join(work, "results.json")
    env = dict(os.environ,
               DRAFTKEEPER_HOME=os.path.join(work, "dkhome"),
               DK_SMOKE_RESULTS=results,
               DK_SMOKE_WORK=work,
               DK_SMOKE_OXT=OXT,
               DK_SMOKE_PYTHONPATH=os.path.join(ROOT, "extension", "pythonpath"))
    profile_arg = "-env:UserInstallation=" + file_url(profile)

    run = lambda *a: subprocess.run(a, env=env, capture_output=True, text=True, timeout=180)
    run(os.path.join(APP, "soffice"), "--headless", "--terminate_after_init", profile_arg)
    scripts = os.path.join(profile, "user", "Scripts", "python")
    os.makedirs(scripts, exist_ok=True)
    with open(os.path.join(scripts, "dk_smoke.py"), "w") as f:
        f.write(MACRO)

    def macro(name):
        url = "vnd.sun.star.script:dk_smoke.py$%s?language=Python&location=user" % name
        r = run(os.path.join(APP, "soffice"), "--headless", "--norestore", profile_arg, url)
        err = "\n".join(l for l in r.stderr.splitlines() if "Fontconfig" not in l).strip()
        if r.returncode or err:
            print("soffice (%s):" % name, r.returncode, err[:1000])

    macro("install")
    print("install:", open(results + ".install").read().strip()
          if os.path.exists(results + ".install") else "no result")
    macro("main")

    failed = True
    if os.path.exists(results):
        checks = json.load(open(results))
        for c in checks:
            print(("PASS " if c["ok"] else "FAIL ") + c["name"]
                  + ("" if c["ok"] else "\n     " + c["detail"].replace("\n", "\n     ")))
        failed = not checks or not all(c["ok"] for c in checks)
    else:
        print("macro produced no results (did it run?)")

    log = os.path.join(work, "dkhome", "draftkeeper.log")
    if os.path.exists(log):
        print("--- draftkeeper.log ---\n" + open(log).read().strip())
    if failed:
        print("FAILED; workdir kept at", work)
        sys.exit(1)
    shutil.rmtree(work)
    print("all smoke checks passed")


if __name__ == "__main__":
    main()
