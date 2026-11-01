#!/bin/bash
# Dispatcher gauntlet. A fake cmux records every call, so this runs without cmux and without a GUI.
set -uo pipefail
ROOT="$(cd -P "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
T="$(mktemp -d -t cmux-dataview-dispatch)"
trap 'kill "$(cat "$T/cache/server.json" 2>/dev/null | /usr/bin/python3 -c "import json,sys;print(json.load(sys.stdin)[\"pid\"])" 2>/dev/null)" 2>/dev/null; rm -rf "$T"' EXIT
mkdir -p "$T/files" "$T/cfg"
export CMUX_DATAVIEW_CONFIG="$T/cfg" CMUX_DATAVIEW_CACHE="$T/cache" CMUX_DATAVIEW_LOG="$T/log"
export CMUX_DATAVIEW_CMUX="$T/fakecmux" FAKE="$T"

cat > "$T/fakecmux" <<'EOF'
#!/bin/bash
echo "$*" >> "$FAKE/calls"
case "$1" in
  identify) echo '{"focused":{"window_ref":"window:7","workspace_ref":"workspace:9"}}' ;;
  list-panels)
    if [ -s "$FAKE/viewer_url" ]; then echo '{"surfaces":[{"type":"terminal","ref":"surface:1"},{"type":"browser","ref":"surface:2"}]}'
    else echo '{"surfaces":[{"type":"terminal","ref":"surface:1"}]}'; fi ;;
  browser)
    case "$*" in
      *" url") cat "$FAKE/viewer_url" 2>/dev/null ;;
      *open-split*) echo "$3" > "$FAKE/viewer_url" ;;
      *navigate*) echo "$5" > "$FAKE/viewer_url" ;;
    esac ;;
  open) [ -n "${FAKE_OPEN_FAILS:-}" ] && exit 1 ;;
esac
exit 0
EOF
chmod +x "$T/fakecmux"

pass=0 fail=0
ok() { pass=$((pass + 1)); printf '  ok    %s\n' "$1"; }
bad() { fail=$((fail + 1)); printf '  FAIL  %s\n' "$1"; [ -n "${2:-}" ] && printf '        %s\n' "$2"; }
run() { : > "$T/calls"; env -i HOME="$HOME" PATH=/usr/bin:/bin CMUX_DATAVIEW_CONFIG="$CMUX_DATAVIEW_CONFIG" \
  CMUX_DATAVIEW_CACHE="$CMUX_DATAVIEW_CACHE" CMUX_DATAVIEW_LOG="$CMUX_DATAVIEW_LOG" CMUX_DATAVIEW_CMUX="$CMUX_DATAVIEW_CMUX" \
  FAKE="$FAKE" FAKE_OPEN_FAILS="${FAKE_OPEN_FAILS:-}" /bin/sh -c "\"$ROOT/bin/cmux-dataview\" \"\$1\"" sh "$1"; }
called() { grep -q -- "$1" "$T/calls"; }

printf 'a,b\n1,2\n' > "$T/files/plain.csv"
printf 'a,b\n1,2\n' > "$T/files/it's a file.csv"
printf 'a,b\n1,2\n' > "$T/files/Ümlaut données.CSV"
printf 'a,b\n1,2\n' > "$T/files/odd:name.csv"
printf '# hi\n' > "$T/files/readme.md"
printf 'x\n' > "$T/files/noext"

echo "cmux-dataview dispatch gauntlet"

run "$T/files/plain.csv"; rc=$?
[ $rc -eq 0 ] && called "browser open-split http://127.0.0.1:" && called "--workspace workspace:9" && called "--window window:7" \
  && ok "csv opens a browser split in the focused workspace and window" || bad "csv open-split" "rc=$rc $(cat "$T/calls")"

run "$T/files/plain.csv"; rc=$?
[ $rc -eq 0 ] && called "browser --surface surface:2 navigate" && ! called "open-split" \
  && ok "a second double-click switches the existing pane instead of stacking splits" || bad "reuse pane" "$(cat "$T/calls")"

run "$T/files/it's a file.csv"; [ $? -eq 0 ] && ok "apostrophe and space in the file name" || bad "apostrophe name"
run "$T/files/Ümlaut données.CSV"; [ $? -eq 0 ] && ok "unicode name and uppercase extension" || bad "unicode name"
run "$T/files/odd:name.csv"; rc=$?; [ $rc -eq 0 ] && called navigate && ok "a real colon in the file name is kept" || bad "colon name" "rc=$rc calls=$(tr "\n" " " < "$T/calls") log=$(tail -2 "$T/log" | tr "\n" " ")"
run "$T/files/plain.csv:12:3"; [ $? -eq 0 ] && called navigate && ok "terminal Cmd-click path:line:col is stripped" || bad "line suffix" "$(cat "$T/log" | tail -2)"

run "$T/files/readme.md"; rc=$?
[ $rc -eq 0 ] && called "open $T/files/readme.md" && ! called "browser" && ok "non-data files go back to the cmux preview" || bad "md fallback" "rc=$rc $(cat "$T/calls")"

run "$T/files/noext"; [ $? -eq 0 ] && called "open $T/files/noext" && ok "a file with no extension goes to the preview" || bad "noext"

FAKE_OPEN_FAILS=1 run "$T/files/readme.md"; [ $? -ne 0 ] && ok "if the preview fails, a nonzero exit lets cmux use the default app" || bad "preview failure exit code"

run "$T/files/missing.csv"; [ $? -ne 0 ] && ok "a missing file exits nonzero and never opens a pane" || bad "missing file"
called "open-split" && bad "missing file opened a split"

printf '{"otherFiles": "defaultApp"}\n' > "$T/cfg/config.json"
run "$T/files/readme.md"; rc=$?
[ $rc -ne 0 ] && ! called "open " && ok "otherFiles=defaultApp hands non-data files to macOS" || bad "defaultApp" "rc=$rc"

printf 'echo prior-editor-ran "$@" > %q/prior.out\n' "$T" > "$T/prior.sh"
printf 'sh %q' "$T/prior.sh" > "$T/cfg/prior-editor"
printf '{"otherFiles": "priorEditor"}\n' > "$T/cfg/config.json"
run "$T/files/readme.md:4"; rc=$?
grep -q "prior-editor-ran $T/files/readme.md:4" "$T/prior.out" 2>/dev/null && ok "priorEditor receives the original argument, line suffix included" || bad "priorEditor" "rc=$rc"

printf '{"extensions": ["csv", "log"]}\n' > "$T/cfg/config.json"
printf 'a,b\n1,2\n' > "$T/files/data.log"
run "$T/files/data.log"; rc=$?; [ $rc -eq 0 ] && called navigate && ok "extensions from settings are honoured" || bad "custom extension" "rc=$rc calls=$(tr "\n" " " < "$T/calls") log=$(tail -2 "$T/log" | tr "\n" " ")"
printf '{"extensions": ["tsv"]}\n' > "$T/cfg/config.json"
run "$T/files/plain.csv"; called "open $T/files/plain.csv" && ok "a type removed from settings falls back to the preview" || bad "removed extension"
rm -f "$T/cfg/config.json"

mkdir "$T/cache/open.lock" 2>/dev/null
run "$T/files/plain.csv"; [ $? -eq 0 ] && ! called "browser" && ok "a concurrent double-click does not open a second pane" || bad "lock"
rmdir "$T/cache/open.lock"

/usr/bin/python3 -I -c "import os,time; os.utime('$T/cache', None)"
echo '{"pid": 999999, "port": 1, "token": "x"}' > "$T/cache/server.json"
run "$T/files/plain.csv"; [ $? -eq 0 ] && ok "a stale helper state file is replaced by a fresh helper" || bad "stale state" "$(tail -3 "$T/log")"

"$ROOT/bin/cmux-dataview" --version >/dev/null && ok "--version" || bad "--version"
"$ROOT/bin/cmux-dataview" >/dev/null 2>&1; [ $? -eq 2 ] && ok "no arguments prints usage and exits 2" || bad "usage"

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
