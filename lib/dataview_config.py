#!/usr/bin/env python3
"""Settings for cmux-dataview. One JSON file, defaults filled in on read, unknown keys kept."""

import json
import os
import sys

CONFIG_DIR = os.environ.get(
    "CMUX_DATAVIEW_CONFIG", os.path.expanduser("~/.config/cmux-dataview")
)
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

DEFAULTS = {
    "extensions": ["csv", "tsv", "tab", "psv", "json", "geojson", "jsonl", "ndjson"],
    "otherFiles": "preview",
    "theme": "light",
    "density": "comfortable",
    "numberLocale": "auto",
    "delimiter": "auto",
    "header": "auto",
    "bigFile": {"thresholdMB": 50, "behaviour": "preview", "previewRows": 50000},
    "idleMinutes": 10,
    "focusOnOpen": False,
    "exportDir": "~/Downloads",
    "liveReload": True,
}

# Allowed values for enum settings. The viewer and the dispatcher both validate against this.
CHOICES = {
    "otherFiles": ["preview", "defaultApp", "priorEditor"],
    "theme": ["light", "dark"],
    "density": ["compact", "comfortable"],
    "numberLocale": ["auto", "en", "de"],
    "delimiter": ["auto", "comma", "semicolon", "tab", "pipe"],
    "header": ["auto", "yes", "no"],
    "bigFile.behaviour": ["preview", "ask", "full"],
}


def _merge(base, extra):
    out = dict(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def _valid(cfg):
    """Replace any invalid value with the default, so a hand edit can never break opening files."""
    for key, allowed in CHOICES.items():
        parent, _, leaf = key.rpartition(".")
        holder = cfg.get(parent) if parent else cfg
        default_holder = DEFAULTS.get(parent) if parent else DEFAULTS
        if isinstance(holder, dict) and holder.get(leaf) not in allowed:
            holder[leaf] = default_holder[leaf]
    exts = cfg.get("extensions")
    if not isinstance(exts, list) or not all(isinstance(e, str) for e in exts):
        cfg["extensions"] = list(DEFAULTS["extensions"])
    else:
        cfg["extensions"] = sorted(
            {e.strip().lower().lstrip(".") for e in exts if e.strip()}
        )
    big = cfg.get("bigFile") if isinstance(cfg.get("bigFile"), dict) else {}
    for k, lo, hi in (("thresholdMB", 1, 100000), ("previewRows", 100, 10000000)):
        v = big.get(k)
        if not isinstance(v, (int, float)) or isinstance(v, bool) or not lo <= v <= hi:
            big[k] = DEFAULTS["bigFile"][k]
    cfg["bigFile"] = big
    idle = cfg.get("idleMinutes")
    if (
        not isinstance(idle, (int, float))
        or isinstance(idle, bool)
        or not 1 <= idle <= 1440
    ):
        cfg["idleMinutes"] = DEFAULTS["idleMinutes"]
    for k in ("focusOnOpen", "liveReload"):
        if not isinstance(cfg.get(k), bool):
            cfg[k] = DEFAULTS[k]
    if not isinstance(cfg.get("exportDir"), str) or not cfg["exportDir"].strip():
        cfg["exportDir"] = DEFAULTS["exportDir"]
    return cfg


def load():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            user = json.load(fh)
        if not isinstance(user, dict):
            user = {}
    except (OSError, ValueError):
        user = {}
    return _valid(_merge(DEFAULTS, user))


def save(changes):
    """Merge changes into the file on disk, keeping keys we do not know about."""
    try:
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            current = json.load(fh)
        if not isinstance(current, dict):
            current = {}
    except (OSError, ValueError):
        current = {}
    merged = _valid(_merge(_merge(DEFAULTS, current), changes))
    os.makedirs(CONFIG_DIR, exist_ok=True)
    tmp = "%s.%d.tmp" % (CONFIG_PATH, os.getpid())
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(merged, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, CONFIG_PATH)
    return merged


def main(argv):
    """`get <dotted.key>` prints one value for the bash dispatcher. `init` writes the defaults."""
    if len(argv) >= 3 and argv[1] == "get":
        value = load()
        for part in argv[2].split("."):
            value = value.get(part) if isinstance(value, dict) else None
        if isinstance(value, list):
            print(" ".join(value))
        elif isinstance(value, bool):
            print("true" if value else "false")
        elif value is not None:
            print(value)
        return 0
    if len(argv) >= 2 and argv[1] == "shell":
        # One call gives bash every setting, since each python start costs ~0.2s on a double-click.
        import shlex

        cfg = load()
        flat = {
            "extensions": " ".join(cfg["extensions"]),
            "otherFiles": cfg["otherFiles"],
            "focusOnOpen": "true" if cfg["focusOnOpen"] else "false",
            "bigThresholdMB": int(cfg["bigFile"]["thresholdMB"]),
            "bigBehaviour": cfg["bigFile"]["behaviour"],
            "bigPreviewRows": int(cfg["bigFile"]["previewRows"]),
        }
        for k, v in flat.items():
            print("CFG_%s=%s" % (k, shlex.quote(str(v))))
        return 0
    if len(argv) >= 2 and argv[1] == "init":
        if not os.path.exists(CONFIG_PATH):
            save({})
        print(CONFIG_PATH)
        return 0
    if len(argv) >= 2 and argv[1] == "path":
        print(CONFIG_PATH)
        return 0
    print("usage: dataview_config.py get <key> | init | path", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
