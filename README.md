# cmux-dataview

Double-click a CSV in cmux and get a real table next to your terminal. Sort it, search it, filter it, and scroll through millions of rows without leaving cmux.

> Licence. Source-available, not open source. Free for personal evaluation with credit. Business use, redistribution and derivative works, including AI-made clones, need written permission. See [LICENSE](LICENSE). For a commercial licence or setup help, reach out via [next8n.com](https://next8n.com).

## What you get

- **A table where you expected one.** Double-clicking a `.csv`, `.tsv`, `.json` or `.jsonl` in the cmux file tree opens a sortable, searchable table in a split, instead of raw text.
- **Big files that stay fast.** A 145 MB, 2.5 million row TSV opens in about a second and scrolls end to end. Rows load as you scroll.
- **Search across the whole file, not the screen.** Search, column filters, sort and column stats run over every row. On that 2.5 million row file a search takes about 3 seconds.
- **The view follows the file.** Save the CSV in another pane and the table refreshes by itself.
- **The format guessed for you.** It detects the delimiter (comma, semicolon, tab, pipe), German decimals like `1.234,56`, and the encoding (UTF-8, UTF-16 Excel exports, Windows cp1252). It also notices when a first row is data rather than headers. Every guess can be overridden in Settings.
- **Exports that will not bite you in Excel.** "Export for Excel" neutralises cells that a spreadsheet would run as formulas.
- **Nothing else changes.** Markdown, code and every other file still open in the normal cmux preview.

## From install to your first table

1. Clone the repo and run the installer.
   ```bash
   git clone <this repo> ~/repositories/cmux-dataview
   ~/repositories/cmux-dataview/install.sh
   ```
2. In cmux, open the file tree in the right sidebar and double-click any `.csv`.
3. A table opens in a split. Type in the search box, or right-click a column header for its stats.

The installer backs up `~/.config/cmux/cmux.json` first, then runs `cmux-dataview --doctor`, which checks every link in the chain.

## How it works

cmux has no plugin API for file viewers yet (upstream issue 7337). It does let one command handle file-tree double-clicks, through `fileExplorer.doubleClickAction` set to `preferredEditor`. cmux-dataview installs itself as that command and acts as a dispatcher.

```
double-click ─► cmux-dataview ─┬─ data file ──► local helper ──► table in a cmux browser split
                               └─ other file ─► normal cmux preview (or your previous editor)
```

The helper is a small Python server on 127.0.0.1 with a random token. It indexes the file by byte offset, so it never holds a large file in memory, and it stops itself after 30 idle minutes. Full design in [docs/DESIGN.md](docs/DESIGN.md).

## Settings

Click **Settings** in the viewer, or run `cmux-dataview --config`. Every option is in [docs/SETTINGS.md](docs/SETTINGS.md). Highlights.

| Setting | What it does |
|---|---|
| Large files | Preview the first rows (default), ask each time, or always load everything |
| File types shown as tables | Add `log` or `txt` if you keep data in those |
| Other files | Send non-data files to the cmux preview, the macOS default app, or your previous editor |
| Delimiter, header, number format | Override any guess |

## Commands

| Command | Does |
|---|---|
| `cmux-dataview <file>` | Open a file as a table, the same as a double-click |
| `cmux-dataview --doctor` | Check the setup |
| `cmux-dataview --config` | Open the settings file |
| `cmux-dataview --stop` | Stop the helper |
| `./uninstall.sh` | Remove it and restore your previous cmux settings |

The installer also adds two Command Palette actions, Data View settings and Data View doctor.

## Requirements

- macOS with [cmux](https://github.com/manaflow-ai/cmux)
- Python 3.9 or newer at `/usr/bin/python3`, from the Xcode Command Line Tools (`xcode-select --install`)

No other dependencies, nothing to download at runtime, and no network access beyond 127.0.0.1.

## Known limits

- Remote file explorers (SSH) always use the built-in cmux preview. cmux does not call the dispatcher there.
- `.json` files are loaded whole, up to 200 MB. JSON Lines and delimited files have no size limit.
- If you restart cmux, an open table tab from before shows "expired". Double-click the file again.

## Project layout

```
bin/cmux-dataview          the dispatcher cmux runs on double-click
lib/dataview_dispatch.py   routing, helper start-up, pane placement
lib/dataview_server.py     the local helper (indexing, paging, search, export)
lib/dataview_render.py     parsing helpers (encodings, delimiters, JSON shapes, types)
lib/dataview_config.py     settings with defaults and validation
lib/dataview_install.py    safe cmux.json install and uninstall
lib/viewer.html            the table UI
tests/                     core tests and the dispatcher tests
```

## Tests

```bash
/usr/bin/python3 -I tests/test_core.py   # parsing, queries, server security, export, settings
bash tests/test_dispatch.sh              # every double-click path, with a fake cmux
```

## Licence

Source-available. See [LICENSE](LICENSE). Copyright Mirza Iqbal.
