# Changelog

## [0.2.0] - 2026-11-01

### Added
- Double-clicking a `.mmd` or `.mermaid` file opens the source next to a live diagram, editable and saveable. Before, it opened as plain text.
- Double-clicking an `.excalidraw` file opens the full Excalidraw editor in the split, with save back to the file. Before, it opened as raw JSON.
- Double-clicking a `.sketch` file shows the preview image saved inside it, read only. Before, cmux could not show it at all.
- `cmux-dataview --setup-diagrams` builds the offline editors once into the vendor folder.
- Saving a diagram checks for changes on disk and asks before overwriting.
- A `diagrams` setting turns the diagram view on or off.

## [0.1.1] - 2026-11-01

### Changed
- Column type badges read `abc`, `123`, `1.5` and `date`. Before, the type name was cut to three letters, so text showed as "tex".
- Column stats round the mean to two decimals. Before, it showed four.
- The helper stops after 10 idle minutes instead of 30, so it hands its memory back to macOS sooner.

### Added
- README walkthrough with screenshots of every feature, made with fake demo data in `docs/demo`.

## [0.1.0] - 2026-11-01

### Added
- Double-clicking a CSV, TSV, JSON or JSON Lines file in the cmux file tree opens a table in a cmux browser split. Before, cmux showed the raw text in its generic preview, because cmux has no way to register a viewer for a file type.
- A local helper indexes files by byte offset and streams rows as you scroll, so a 2.5 million row, 145 MB file opens in about a second instead of freezing a page that held all of it.
- Search, column filters, sort and column stats run across the whole file, not only the loaded rows.
- Large files open as a preview of the first rows by default, with a "Load the whole file" button. A setting switches this to a dialog on each double-click.
- A Settings panel in the viewer, backed by `~/.config/cmux-dataview/config.json`. Unknown keys are kept, so the file can be extended.
- Live reload when the file changes on disk.
- Export CSV, and Export for Excel that neutralises formula cells (CSV injection).
- Reveal in Finder and Open in default app, from the viewer.
- An installer that backs up cmux.json, keeps your previous `app.preferredEditor`, and an uninstaller that restores only the values it set.
- `--doctor`, `--config` and `--stop` commands, and two Command Palette actions.
