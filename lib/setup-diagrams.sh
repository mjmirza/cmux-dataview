#!/usr/bin/env bash
# One-time build of the offline diagram editors into the vendor folder. Safe to rerun.
set -uo pipefail
VENDOR="${CMUX_DATAVIEW_VENDOR:-$HOME/Library/Application Support/cmux-dataview/vendor}"
MERMAID_VER="12.2.0"; EXCALIDRAW_VER="0.18.1"; REACT_VER="19.3.1"; ESBUILD_VER="0.28.2"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

die() { echo "setup-diagrams: $*" >&2; exit 1; }
command -v npm >/dev/null || die "npm not found. Install Node.js first (brew install node)."

WORK="$(mktemp -d -t cmux-dataview-build)"; trap 'rm -rf "$WORK"' EXIT
cd "$WORK" || die "cannot enter $WORK"
echo '{"name":"cmux-dataview-diagrams","private":true}' > package.json
echo "Downloading mermaid $MERMAID_VER, excalidraw $EXCALIDRAW_VER, react $REACT_VER, esbuild $ESBUILD_VER"
npm install --silent --no-audit --no-fund --ignore-scripts \
  "mermaid@$MERMAID_VER" "@excalidraw/excalidraw@$EXCALIDRAW_VER" \
  "react@$REACT_VER" "react-dom@$REACT_VER" || die "npm install failed"
npm install --silent --no-audit --no-fund "esbuild@$ESBUILD_VER" || die "esbuild install failed"

cat > entry.jsx <<'EOF'
import React from "react";
import { createRoot } from "react-dom/client";
import { Excalidraw, serializeAsJSON } from "@excalidraw/excalidraw";
window.DataviewExcalidraw = { React, createRoot, Excalidraw, serializeAsJSON };
EOF
./node_modules/.bin/esbuild entry.jsx --bundle --minify --format=iife --loader:.js=jsx --conditions=production \
  --define:process.env.NODE_ENV='"production"' --outfile=out/excalidraw.js \
  --log-level=warning || die "excalidraw build failed"

NEW="$VENDOR.new"; rm -rf "$NEW"; mkdir -p "$NEW/excalidraw" || die "cannot create $NEW"
cp node_modules/mermaid/dist/mermaid.min.js "$NEW/mermaid.min.js" || die "mermaid bundle missing"
cp out/excalidraw.js "$NEW/excalidraw/excalidraw.js"
cp node_modules/@excalidraw/excalidraw/dist/prod/index.css "$NEW/excalidraw/excalidraw.css" || die "excalidraw css missing"
FONTS="node_modules/@excalidraw/excalidraw/dist/prod/fonts"
[ -d "$FONTS" ] && cp -R "$FONTS" "$NEW/excalidraw/fonts"
printf '{"mermaid":"%s","excalidraw":"%s","react":"%s"}\n' "$MERMAID_VER" "$EXCALIDRAW_VER" "$REACT_VER" > "$NEW/versions.json"
rm -rf "$VENDOR"; mv "$NEW" "$VENDOR" || die "cannot install into $VENDOR"
echo "Installed into $VENDOR ($(du -sh "$VENDOR" | cut -f1))"
