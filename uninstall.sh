#!/bin/bash
# Remove cmux-dataview from cmux. Restores your previous settings and keeps a backup.
set -euo pipefail
here="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
"$here/bin/cmux-dataview" --stop || true
/usr/bin/python3 -I "$here/lib/dataview_install.py" uninstall
