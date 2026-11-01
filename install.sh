#!/bin/bash
# Wire cmux-dataview into cmux. Safe to run again. Backs up cmux.json first.
set -euo pipefail
here="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if ! /usr/bin/xcode-select -p >/dev/null 2>&1; then
  echo "cmux-dataview needs Python 3 from the Xcode Command Line Tools. Run: xcode-select --install"; exit 1
fi
chmod +x "$here/bin/cmux-dataview"
/usr/bin/python3 -I "$here/lib/dataview_install.py" install
"$here/bin/cmux-dataview" --doctor || true
