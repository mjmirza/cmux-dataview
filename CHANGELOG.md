# Changelog

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
