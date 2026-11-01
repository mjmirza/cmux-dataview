#!/usr/bin/env python3
"""Install or remove cmux-dataview in ~/.config/cmux/cmux.json. Backs up first, and only
removes values that still belong to us, so a later hand edit is never clobbered."""

import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import dataview_config as C  # noqa: E402

CMUX_JSON = os.environ.get(
    "CMUX_DATAVIEW_CMUX_JSON", os.path.expanduser("~/.config/cmux/cmux.json")
)
CMUX = os.environ.get(
    "CMUX_DATAVIEW_CMUX", "/Applications/cmux.app/Contents/Resources/bin/cmux"
)
STATE = os.path.join(C.CONFIG_DIR, "install-state.json")
PRIOR = os.path.join(C.CONFIG_DIR, "prior-editor")
ACTIONS = {
    "dataview.settings": {
        "type": "command",
        "title": "Data View settings",
        "subtitle": "Open the cmux-dataview settings file",
        "command": "cmux-dataview --config",
        "target": "newTabInCurrentPane",
        "keywords": ["csv", "table", "data", "settings"],
    },
    "dataview.doctor": {
        "type": "command",
        "title": "Data View doctor",
        "subtitle": "Check that CSV double-click is wired up",
        "command": "cmux-dataview --doctor",
        "target": "newTabInCurrentPane",
        "keywords": ["csv", "table", "data", "doctor"],
    },
}


def quote(path):
    # cmux runs: /bin/sh -c "<preferredEditor> '<file>'". A path with spaces must be quoted here.
    return (
        "'" + path.replace("'", "'\\''") + "'"
        if any(c in path for c in " '\"$`\\")
        else path
    )


def load_cmux_json():
    if not os.path.exists(CMUX_JSON):
        return {}
    with open(CMUX_JSON, encoding="utf-8") as fh:
        text = fh.read()
    try:
        data = json.loads(text) if text.strip() else {}
    except ValueError:
        raise SystemExit(
            "cmux.json is not plain JSON (it may contain comments), so it was left untouched.\n"
            "Set these two keys by hand, then run cmux reload-config:\n"
            '  "fileExplorer": {"doubleClickAction": "preferredEditor"}\n'
            '  "app": {"preferredEditor": "%s"}' % entry_command()
        )
    if not isinstance(data, dict):
        raise SystemExit(
            "cmux.json does not contain a JSON object, so it was left untouched."
        )
    return data


def write_atomic(path, data, mode=0o600):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.%d.tmp" % (path, os.getpid())
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def backup():
    if not os.path.exists(CMUX_JSON):
        return None
    dest = "%s.bak-%s" % (CMUX_JSON, time.strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(CMUX_JSON, dest)
    return dest


def entry_command():
    return quote(os.path.realpath(os.path.join(HERE, "..", "bin", "cmux-dataview")))


def reload_cmux():
    try:
        subprocess.run(
            [CMUX, "reload-config"], capture_output=True, timeout=15, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        print("Could not reach cmux. Run cmux reload-config yourself.")


def link_cli():
    target = os.path.expanduser("~/.local/bin/cmux-dataview")
    src = os.path.realpath(os.path.join(HERE, "..", "bin", "cmux-dataview"))
    if not os.path.isdir(os.path.dirname(target)):
        return None
    if os.path.islink(target) or not os.path.exists(target):
        if os.path.islink(target):
            os.remove(target)
        os.symlink(src, target)
        return target
    return None


def install():
    data = load_cmux_json()
    saved = backup()
    app = data.setdefault("app", {})
    explorer = data.setdefault("fileExplorer", {})
    actions = data.setdefault("actions", {})
    ours = entry_command()
    prior_editor = app.get("preferredEditor", "")
    state = {
        "installedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "command": ours,
        "priorDoubleClick": explorer.get("doubleClickAction"),
        "priorEditor": prior_editor,
        "addedActions": [k for k in ACTIONS if k not in actions],
    }
    if os.path.exists(STATE):
        with open(STATE, encoding="utf-8") as fh:
            old = json.load(fh)
        # Reinstalling must keep the values from before the very first install.
        state["priorDoubleClick"] = old.get("priorDoubleClick")
        state["priorEditor"] = old.get("priorEditor", "")
        state["addedActions"] = sorted(
            set(old.get("addedActions", [])) | set(state["addedActions"])
        )
    if prior_editor and "bin/cmux-dataview" not in prior_editor:
        os.makedirs(C.CONFIG_DIR, exist_ok=True)
        with open(PRIOR, "w", encoding="utf-8") as fh:
            fh.write(prior_editor + "\n")
    app["preferredEditor"] = ours
    explorer["doubleClickAction"] = "preferredEditor"
    for k, v in ACTIONS.items():
        # Absolute path, because the palette runs commands without ~/.local/bin on PATH.
        actions.setdefault(
            k, dict(v, command=v["command"].replace("cmux-dataview", ours, 1))
        )
    write_atomic(CMUX_JSON, data)
    write_atomic(STATE, state)
    C.save({})
    linked = link_cli()
    reload_cmux()
    print("Installed. Double-click a CSV in the cmux file tree to try it.")
    print("  cmux.json backup  %s" % (saved or "none, the file did not exist"))
    print("  settings          %s" % C.CONFIG_PATH)
    if linked:
        print("  command           %s" % linked)
    if prior_editor and "bin/cmux-dataview" not in prior_editor:
        print(
            "  your previous preferredEditor is kept and used for non-data files when otherFiles is priorEditor"
        )
    return 0


def uninstall():
    data = load_cmux_json()
    state = {}
    if os.path.exists(STATE):
        with open(STATE, encoding="utf-8") as fh:
            state = json.load(fh)
    saved = backup()
    app, explorer, actions = (
        data.get("app", {}),
        data.get("fileExplorer", {}),
        data.get("actions", {}),
    )
    if "bin/cmux-dataview" in str(app.get("preferredEditor", "")):
        if state.get("priorEditor"):
            app["preferredEditor"] = state["priorEditor"]
        else:
            app.pop("preferredEditor", None)
    if (
        explorer.get("doubleClickAction") == "preferredEditor"
        and state.get("priorDoubleClick") != "preferredEditor"
    ):
        # Restore what double-click did before we installed. Absent before means absent again.
        if state.get("priorDoubleClick"):
            explorer["doubleClickAction"] = state["priorDoubleClick"]
        else:
            explorer.pop("doubleClickAction", None)
    for k in state.get("addedActions", []):
        if isinstance(actions.get(k), dict) and actions[k].get("title") == ACTIONS.get(
            k, {}
        ).get("title"):
            actions.pop(k, None)
    for key in ("app", "fileExplorer", "actions"):
        if key in data and not data[key]:
            data.pop(key)
    write_atomic(CMUX_JSON, data)
    if os.path.exists(STATE):
        os.remove(STATE)
    link = os.path.expanduser("~/.local/bin/cmux-dataview")
    if os.path.islink(link) and "cmux-dataview" in os.path.realpath(link):
        os.remove(link)
    reload_cmux()
    print("Removed. Double-click is back to the cmux preview.")
    print("  cmux.json backup  %s" % (saved or "none"))
    print(
        "  your settings at %s were kept. Delete that folder to remove them too."
        % C.CONFIG_DIR
    )
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "install":
        sys.exit(install())
    if cmd == "uninstall":
        sys.exit(uninstall())
    print("usage: dataview_install.py install | uninstall", file=sys.stderr)
    sys.exit(2)
