# Draftkeeper

Restore points and full history for LibreOffice Writer documents, stored as git.

## Phase 1 (this version)

- **Restore Point** toolbar button (also *Tools › Add-Ons › Draftkeeper*).
  - First click on a document turns history on: writes a `Draftkeeper ID` into
    *File › Properties › Custom Properties* so every copy of the file is recognized.
  - After that, it asks for a label ("Sent to agent") and records a named restore point.
- **Every save** of a tracked document records a snapshot automatically
  (identical content is skipped). That covers the "daily" restore point; the history
  UI (Phase 3) will group snapshots by day.
- *Draftkeeper › History Info…* shows counts and recent restore points.
- Untracked documents are never touched.

History lives in one bare git repo per document:

- macOS: `~/Library/Application Support/Draftkeeper/documents/<id>.git`
- Windows: `%APPDATA%\Draftkeeper\documents\<id>.git`
- override with `DRAFTKEEPER_HOME`

Each commit holds `document.fodt`: Flat ODT, normalized to one paragraph per line with
`rsid` noise removed. You can inspect it with ordinary git:
`git --git-dir <repo> log --format='%ci %s'`.

## Layout

```
extension/                    contents of the .oxt
  draftkeeper_component.py    UNO entry points (toolbar command, save job)
  Addons.xcu, Jobs.xcu        toolbar/menu + "run after save" wiring
  pythonpath/draftkeeper/     office.py (UNO), normalize.py, store.py, packing.py, paths.py
  pythonpath/dulwich/         vendored dulwich 0.24.10 (pure Python git)
tests/test_core.py            core tests, no LibreOffice needed
tests/roundtrip_check.py      real-LibreOffice check of normalize()
build.py                      builds dist/Draftkeeper.oxt
```

## Build, test, install

```
python3 -m unittest discover tests          # core, no LibreOffice needed
python3 build.py                            # -> dist/Draftkeeper.oxt
python3 tests/office_smoke.py               # installs into a throwaway profile, tests the save hook
python3 tests/roundtrip_check.py some.odt   # normalized snapshot keeps identical text
```

The last two need LibreOffice installed and closed. Notes from macOS:
- LibreOffice 26.x's bundled Python has a launch constraint: only LibreOffice may start it,
  so tests run as macros inside `soffice` rather than as external UNO clients.
- `unopkg add` can't register Python components here; install through LibreOffice itself
  (double-click the .oxt, or Extension Manager), which is what users do anyway.

Install: double-click `dist/Draftkeeper.oxt`, or *Tools › Extension Manager › Add*. Restart LibreOffice.
Log file: `<data dir>/draftkeeper.log`.
