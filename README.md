# Draftkeeper

Restore points and full history for LibreOffice Writer documents, stored as git.

## What it does

See [ROADMAP.md](ROADMAP.md) for phases. Implemented so far:

- **Restore Point** toolbar button (also *Tools › Add-Ons › Draftkeeper*).
  - First click on a document turns history on: writes a `Draftkeeper ID` into
    *File › Properties › Custom Properties* so every copy of the file is recognized.
  - After that, it asks for a label ("Sent to agent") and records a named restore point.
- **Every save** of a tracked document records a snapshot automatically
  (saves with no edits are skipped). Untracked documents are never touched.
- **Versions** button: named restore points, imported files, and the last save of each
  day ("daily restore point"); tick *Show every save* for all; optional From/to dates.
  - **Find:** search every version for a word or phrase (ignores case, curly vs straight
    quotes, dash styles). The summary says where it first appeared, when it was last seen and
    removed, or that it's still there; rows are marked *First appears here* / *Removed here*;
    the preview shows the matching passage.
  - **Open as Copy** (or double-click) opens that version as a new untitled document. The
    copy keeps the ID, so saving it over the original continues the history (a restore).
  - **Compare with Current** opens a copy of the current text with the changes since that
    version shown as tracked changes.
  - The search index (`search.idx` next to each repo) is a local cache, updated in the
    background and rebuilt automatically if missing.
- **Fork detection**: when a tracked document is saved at a new path while its known
  copy still exists, it asks once: *Separate document* (new ID and history, noting
  `Derived-From`) or *Same document* (path remembered, history shared). If every known
  copy is gone, it's treated as a move. Paths are kept per machine in `locations.json`.
  Only `.odt`/`.fodt` paths count, so Save As `.docx` exports don't ask.
- **Import Older Versions…** (menu): pick old files ("Novel final FINAL.odt", .docx too);
  they're added underneath the existing history, dated by file modification time and
  labeled with the file name.

- **Sync** through any number of *sync folders* (a thumb drive, a Proton Drive folder, ...):
  *Draftkeeper › Add Sync Folder…* on each computer. Choosing a folder creates (or reuses) a
  `Draftkeeper` folder inside it, marked with `draftkeeper-sync.json`; picking the same
  thumb drive on the second computer finds the same folder. If a drive comes back under a
  different drive letter, mounted volumes are scanned for it.
  - Syncs on open, save, restore point and close, and with *Sync Now*. Only while
    LibreOffice is running.
  - Each computer writes only its own file per document:
    `documents/<doc id>/<machine id>.bundle` (a standard git bundle, written atomically),
    so cloud sync clients never see two writers.
  - **On open**, if there's newer work than the file has, she's asked
    *Open newer version* / *Keep this one*. Opening it rewrites the file with the newest version.
  - **If both computers edited** without syncing, the newer side's text becomes current and
    the other side's edits stay in history (shown as "Combined edits from two computers"
    in Versions). Phase 5 will offer them as tracked changes instead.
  - Import Older Versions is refused once a document has synced (it rewrites history).

### Setting up sync for her

1. On each computer: install the extension, then *Add Sync Folder…* and choose the Proton
   Drive folder (mark it "available offline" in Proton's app), and again for the thumb drive.
2. Turn history on for the novel on one computer, save, and let it sync.
3. On the other computer, open the novel (from the drive or Proton). Its history comes along.

History lives in one bare git repo per document:

- macOS: `~/Library/Application Support/Draftkeeper/documents/<id>.git`
- Windows: `%APPDATA%\Draftkeeper\documents\<id>.git`
- machine identity and sync folders: `config.json` in the same data folder
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

Install: download `Draftkeeper.oxt` from the
[latest release](https://github.com/theK42/draftkeeper/releases/latest) (or build it), then
double-click it, or *Tools › Extension Manager › Add*. Restart LibreOffice.

Updates: *Tools › Extension Manager › Check for Updates* offers the newest release
(installed copies read `update.xml` on main). To publish one, bump `<version>` in
`extension/description.xml`, commit, and run `python3 release.py`: it builds, writes
`update.xml`, tags, uploads the release, then pushes main.

Log file: `<data dir>/draftkeeper.log`.
