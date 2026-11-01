# cmux-dataview design

<!-- freshness: anchor -->

## The problem

Double-clicking a CSV in the cmux file explorer opens the generic file preview, which shows raw text. There is no table, no sort, no filter. Large files are worse. cmux has no plugin API for custom file viewers. Upstream issue 7337 asks for exactly this, and names csv-as-table as a use case.

## The one hook cmux gives us

The setting `fileExplorer.doubleClickAction` accepts `preview`, `defaultEditor` or `preferredEditor`. With `preferredEditor`, cmux runs the command below from its GUI process, with stdio sent to /dev/null.

```
/bin/sh -c "<app.preferredEditor> '<path>'"
```

The path is single-quoted with `posixShellSingleQuoted` (verified in `PreferredEditorService.swift`), so apostrophes in file names are safe. A nonzero exit makes cmux open the macOS default app instead. The same command runs for Return on a file-explorer search result, and for terminal Cmd-click on unsupported files, where the argument can carry a line and column suffix.

So cmux-dataview installs itself as `app.preferredEditor` and becomes a dispatcher for every file. Data files get the table viewer. Everything else goes back to the built-in cmux preview, or to the editor the person had configured before.

## Architecture

```
double-click / Return / Cmd-click
        |
        v
bin/cmux-dataview <path>            (bash, absolute paths, works from a bare GUI env)
        |
        +- not a data file ----------> prior editor, else `cmux open` (built-in preview)
        +- large file + behaviour=ask > macOS dialog: first N rows / whole file / cancel
        +- find focused window + workspace (cmux identify --json)
        +- start or reuse the singleton server (lib/dataview_server.py)
        +- reuse the viewer pane in that workspace, else open a browser split
                |
                v
        http://127.0.0.1:<port>/v/<id>?t=<token>
        viewer.html fetches pages, polls for file changes, saves settings
```

### Why a local server instead of one embedded HTML file

- **Size.** Embedding hundreds of MB of JSON in one page freezes the browser. The server indexes byte offsets and serves rows in pages.
- **Whole-file search.** Search and filters run on the server across every row, not only the rows already loaded.
- **Live reload.** The viewer polls the file version (mtime and size) and refreshes when the file changes. This matches the built-in markdown viewer and the cmux-excalidraw prior art.
- **Settings.** The viewer can read and write the config file.

The embedded single-file renderer stays as a fallback (`--render`), used when the server cannot start.

### Server security

- Binds 127.0.0.1 only, never 0.0.0.0.
- Every request needs a random per-launch token. Without it, any local web page could read your files.
- Serves only files the dispatcher registered, by opaque id, never by a path taken from the URL.
- Exits after `idleMinutes` with no requests.
- The viewer page carries a strict Content Security Policy.

## Large files

Behaviour is configurable with `bigFile.behaviour`.

- **preview** (default). Opens instantly with the first `pageSize` rows. "Load next" and "Load all" buttons. Search and filters scan the whole file.
- **ask**. A macOS dialog on files above `bigFile.thresholdMB`.

Rows past the loaded window are never silently dropped. The header always shows rows loaded against the total, and a banner says when the view is partial.

## Settings section

Config lives in `~/.config/cmux-dataview/config.json`. It is editable three ways. The gear panel in the viewer, `cmux-dataview --config` (opens the file), or by hand. Unknown keys are kept, so the file can be extended. See `docs/SETTINGS.md` for every key.

## Findings from cmux issues

| Issue | What it tells us | What we do |
|---|---|---|
| 7337, config file handlers | No supported way to claim file types yet. csv-as-table is named | Use the `preferredEditor` route now. Move to `filePatterns` when it ships |
| 3943, extension pane type | Panes are terminal or browser only | Render in a browser split |
| 6754, browser downloads | Blob downloads were unreliable in cmux browser panes | Export goes through the server and saves to ~/Downloads directly |
| 11134, surface.list file paths | Preview surfaces do not expose their file path | Match our own pane by URL, scoped to one workspace |
| 7060, Shift bypass | People want a per-click escape to the native app | Viewer has an open-in-default-app button |
| 5052, right-click options | People want more open-with choices | Viewer has reveal-in-Finder and open-in-default-app |
| cmux-excalidraw | Singleton server, in-place pane switch, mtime polling work well | Same pattern |

## Known limits

- Remote file explorers always use the built-in preview. cmux never calls the dispatcher there.
- Taking over `app.preferredEditor` also changes terminal Cmd-click on unsupported files. The dispatcher hands those to the prior editor or the default app, so behaviour stays the same.
- Requires a working `/usr/bin/python3`. On a Mac without Command Line Tools this is a stub, and `--doctor` says so.

## Reviews

The first design was attacked by a second model (Codex). Accepted points were the python stub, cache privacy, CSV formula injection on export, leading-zero IDs and long integers, headerless CSVs, render temp-file races, focused-workspace targeting, and the CSP. Rejected with evidence were shell injection through the path (cmux single-quotes it) and the prior editor not being chained (it is).
