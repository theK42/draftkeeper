# Draftkeeper roadmap

Agreed 2026-11-07. Update this file when plans change, so they don't drift silently.

## Goals (from the original brief)

- Manual restore points, plus automatic history (at least daily)
- Full local history on both computers and/or the thumb drive
- Backup to the cloud or a local server
- Find versions by date and by text search
- Almost never a merge conflict; if one is unavoidable, a dead-simple resolution
- Embedded in LibreOffice (an extension, not a fork)

Platforms: macOS (Apple Silicon) and Windows now; Linux later, untested for now.

## Phase 1: capture ✅ (verified on macOS)

- Document ID embedded in the file's custom properties (survives copies and renames)
- Snapshot on every save of a tracked document; saves with no edits are skipped
- Restore Point button with her own label
- One local bare git repo per document; line-delta packing keeps history small
- History Info (basic stand-in for the history browser)

**Daily restore point** is covered by snapshots on save. The version list shows
the last save of each day alongside named restore points. There is no separate timer.

## Phase 2: ready for her to use on one machine ✅ (macOS; light testing on Windows)

- Version list: named restore points and end-of-day versions (optionally every save),
  with "Open as copy"
- Fork detection: a Save As or Finder copy that carries the novel's ID gets asked once
  "same novel, or a separate document?" (chapter working copies become separate docs,
  linked to the novel)
- Import her existing "final final" files as backdated history, labeled by filename
  (files whose text is already in the history are skipped)
- Tested lightly on her Windows PC; worked

Until Phase 3, she should use Draftkeeper on **one machine only**. Separate
histories on two machines can be combined later, but it's extra work.

## Phase 3: sync and backup (built; needs real two-machine use)

- Generic *sync folders*: thumb drive and Proton Drive (off-site, end-to-end encrypted)
  use the same code; one git bundle per machine per document, written atomically
- Divergence stopgap: newer side's text stays current, other side's edits kept in history
- When she opens a document: "your other computer has newer work. Load it first?"

## Phase 4: history browser and search (built; GUI checks pending)

- The Versions window became the browser (a dialog, not a sidebar: most of the value for
  far less work). It replaced History Info back in Phase 2.
- Full-text search across every version: case-, quote- and dash-insensitive; summary of
  where a phrase first appeared, was last seen, and when it was removed (or that it's still
  there); the list marks "First appears here" / "Removed here"; preview shows the match
- Search index: each distinct paragraph stored once, versions as paragraph lists; a local
  cache per document (no SQLite, so nothing depends on the Windows build's Python),
  updated in the background after saves, syncs and imports
- Date range filter (From / to)
- Compare with Current: a copy of the current text with changes since the chosen version
  shown as tracked changes (LibreOffice's Compare Document). Phase 5 builds on this.

## Phase 5: divergence handling

- When two machines diverge, keep the newest as main and show the other machine's
  differences as tracked changes (Compare Document) for her to accept or reject
- Moves earlier if the thumb drive stays her only sync path

## Later / maybe

- A real sidebar panel for history (instead of the Versions window)
- "Work on this chapter separately" / "Put it back" round-trip
- Linux support and testing
- Background helper to sync while LibreOffice is closed
