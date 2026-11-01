# Settings

<!-- freshness: anchor -->

Settings live in `~/.config/cmux-dataview/config.json`. Change them in the viewer (Settings button), with `cmux-dataview --config`, or by hand. A value that is not allowed falls back to its default, so a typo never stops files from opening. Keys this list does not mention are kept untouched, so you can add your own.

| Key | Default | Allowed | What it does |
|---|---|---|---|
| `extensions` | csv, tsv, tab, psv, json, geojson, jsonl, ndjson | any list of extensions | File types shown as tables. Everything else goes to `otherFiles` |
| `otherFiles` | `preview` | `preview`, `defaultApp`, `priorEditor` | Where non-data files go. `priorEditor` uses the `app.preferredEditor` you had before installing |
| `theme` | `light` | `light`, `dark` | Viewer colours |
| `density` | `comfortable` | `compact`, `comfortable` | Row height |
| `numberLocale` | `auto` | `auto`, `en`, `de` | How numbers are read. `de` reads `1.234,56` as 1234.56 |
| `delimiter` | `auto` | `auto`, `comma`, `semicolon`, `tab`, `pipe` | Field separator. Auto sniffs the first 64 KB |
| `header` | `auto` | `auto`, `yes`, `no` | Whether the first row holds column names |
| `bigFile.thresholdMB` | `50` | 1 to 100000 | Files above this size count as large |
| `bigFile.behaviour` | `preview` | `preview`, `ask`, `full` | Large files open as a preview of the first rows, ask in a dialog, or always load whole |
| `bigFile.previewRows` | `50000` | 100 to 10000000 | Rows in a preview. The viewer offers "Load the whole file" |
| `liveReload` | `true` | `true`, `false` | Refresh the table when the file changes on disk |
| `focusOnOpen` | `false` | `true`, `false` | Move focus to the table when it opens |
| `exportDir` | `~/Downloads` | any folder | Where Export CSV and Export for Excel save |
| `idleMinutes` | `10` | 1 to 1440 | Stop the helper after this many idle minutes |

## Environment overrides

For testing or unusual setups.

| Variable | Default |
|---|---|
| `CMUX_DATAVIEW_CONFIG` | `~/.config/cmux-dataview` |
| `CMUX_DATAVIEW_CACHE` | `~/Library/Caches/cmux-dataview` |
| `CMUX_DATAVIEW_LOG` | `~/Library/Logs/cmux-dataview.log` |
| `CMUX_DATAVIEW_CMUX` | `/Applications/cmux.app/Contents/Resources/bin/cmux` |
| `CMUX_DATAVIEW_PYTHON` | `/usr/bin/python3` |
| `CMUX_DATAVIEW_MAX_BYTES` | 200 MB, the cap for `.json` files loaded whole |
