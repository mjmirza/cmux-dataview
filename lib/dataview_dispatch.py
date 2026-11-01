#!/usr/bin/env python3
"""Everything a double-click does, in one python process. Each extra python start costs ~0.2s.
Exit 0 handled, 10 not a data file (bash falls back), 1 failed (bash falls back and notifies)."""

import json
import os
import re
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import dataview_config as C  # noqa: E402

CMUX = os.environ.get(
    "CMUX_DATAVIEW_CMUX", "/Applications/cmux.app/Contents/Resources/bin/cmux"
)
CACHE = os.environ.get(
    "CMUX_DATAVIEW_CACHE", os.path.expanduser("~/Library/Caches/cmux-dataview")
)
LOG = os.environ.get(
    "CMUX_DATAVIEW_LOG", os.path.expanduser("~/Library/Logs/cmux-dataview.log")
)
STATE = os.path.join(CACHE, "server.json")
NOT_DATA, FAILED = 10, 1
VIEWER_URL = re.compile(r"^http://127\.0\.0\.1:\d+/v/[0-9a-f]{16}\?t=")


def log(msg):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except OSError:
        return


def cmux(*args, timeout=10):
    env = dict(os.environ, CMUX_QUIET="1")
    try:
        out = subprocess.run(
            [CMUX] + list(args),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log("cmux %s failed: %s" % (args[0], exc))
        return None
    return out.stdout if out.returncode == 0 else None


def resolve(arg):
    """Terminal Cmd-click may append :line or :line:col. Strip it only if the raw path is not a file."""
    if os.path.exists(arg):
        return arg
    m = re.match(r"^(.+?):\d+(?::\d+)?$", arg)  # lazy, so path:12:3 strips both numbers
    if m and os.path.exists(m.group(1)):
        return m.group(1)
    return arg


def read_state():
    try:
        with open(STATE, encoding="utf-8") as fh:
            st = json.load(fh)
        os.kill(int(st["pid"]), 0)
        return st
    except (OSError, ValueError, KeyError, TypeError):
        return None


def request(st, path, body=None, timeout=30):
    url = "http://127.0.0.1:%d%s" % (st["port"], path)
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"X-Token": st["token"], "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def ensure_server():
    st = read_state()
    if st:
        try:
            request(st, "/api/ping", timeout=2)
            return st
        except OSError:
            log("helper did not answer, restarting")
    os.makedirs(CACHE, mode=0o700, exist_ok=True)
    if os.path.exists(STATE):
        os.remove(STATE)
    log("starting helper")
    with open(LOG, "a") as logfh:
        subprocess.Popen(
            [
                sys.executable,
                "-I",
                os.path.join(HERE, "dataview_server.py"),
                "serve",
                STATE,
            ],
            stdin=subprocess.DEVNULL,
            stdout=logfh,
            stderr=logfh,
            start_new_session=True,
        )
    for _ in range(60):
        time.sleep(0.05)
        st = read_state()
        if st:
            try:
                request(st, "/api/ping", timeout=1)
                return st
            except OSError:
                continue
    raise RuntimeError("helper did not start")


def ask_limit(path, size, rows):
    script = [
        "on run argv",
        'set r to display dialog ((item 1 of argv) & " is " & (item 2 of argv) & " MB. How much should cmux-dataview load?") '
        'buttons {"Cancel", "Whole file", "First " & (item 3 of argv) & " rows"} default button 3 '
        'with title "cmux-dataview" giving up after 120',
        'if gave up of r then return "Cancel"',
        "return button returned of r",
        "end run",
    ]
    args = ["/usr/bin/osascript"]
    for line in script:
        args += ["-e", line]
    args += [os.path.basename(path), str(size // 1048576), format(rows, ",")]
    try:
        answer = subprocess.run(
            args, capture_output=True, text=True, timeout=130
        ).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        answer = "Cancel"
    if answer == "Whole file":
        return None
    if answer.startswith("First"):
        return rows
    return "cancel"


def choose_limit(path, cfg):
    big = cfg["bigFile"]
    size = os.path.getsize(path)
    if size <= big["thresholdMB"] * 1048576 or big["behaviour"] == "full":
        return None
    if big["behaviour"] == "ask":
        return ask_limit(path, size, int(big["previewRows"]))
    return int(big["previewRows"])


def focused():
    out = cmux("identify", "--json")
    try:
        f = json.loads(out or "{}").get("focused") or {}
    except ValueError:
        f = {}
    return f.get("window_ref") or "", f.get("workspace_ref") or ""


def viewer_surface(win, ws, base):
    """A browser surface in this workspace already showing our helper, so we switch it in place."""
    args = (
        ["list-panels", "--json"]
        + (["--workspace", ws] if ws else [])
        + (["--window", win] if win else [])
    )
    try:
        surfaces = json.loads(cmux(*args) or "{}").get("surfaces", [])
    except ValueError:
        return None
    for s in surfaces:
        if s.get("type") != "browser" or not s.get("ref"):
            continue
        url = (cmux("browser", "--surface", s["ref"], "url") or "").strip()
        # Any port counts, so a pane left from a helper that has since restarted gets reused too.
        if url.startswith(base + "/v/") or VIEWER_URL.match(url):
            return s["ref"]
    return None


def show(url, base, cfg):
    win, ws = focused()
    focus = "true" if cfg["focusOnOpen"] else "false"
    ref = viewer_surface(win, ws, base)
    if ref:
        log("switch %s in %s" % (ref, ws or "selected workspace"))
        ok = cmux("browser", "--surface", ref, "navigate", url) is not None
        if ok and cfg["focusOnOpen"]:
            cmux("focus-panel", "--panel", ref, *(["--workspace", ws] if ws else []))
        return ok
    log("open split in %s" % (ws or "selected workspace"))
    args = ["browser", "open-split", url, "--focus", focus]
    if ws:
        args += ["--workspace", ws]
    if win:
        args += ["--window", win]
    return cmux(*args) is not None


def dispatch(arg):
    path = resolve(arg)
    cfg = C.load()
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    if not os.path.isfile(path) or ext not in cfg["extensions"]:
        print(
            cfg["otherFiles"]
        )  # the bash wrapper routes the file with this, saving a second python start
        return NOT_DATA
    if not os.access(path, os.R_OK):
        log("unreadable: %s" % path)
        return FAILED
    limit = choose_limit(path, cfg)
    if limit == "cancel":
        log("cancelled: %s" % path)
        return 0
    lock = os.path.join(CACHE, "open.lock")
    os.makedirs(CACHE, mode=0o700, exist_ok=True)
    try:
        os.mkdir(lock)
    except FileExistsError:
        # Two fast double-clicks should give one split. A lock older than a minute is stale.
        if time.time() - os.path.getmtime(lock) < 60:
            log("already opening: %s" % path)
            return 0
    try:
        st = ensure_server()
        url = request(st, "/api/open", {"path": path, "limit": limit})["url"]
        ok = show(url, "http://127.0.0.1:%d" % st["port"], cfg)
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        log("dispatch failed for %s: %s" % (path, exc))
        ok = False
    finally:
        try:
            os.rmdir(lock)
        except OSError:
            log("lock already released")
    return 0 if ok else FAILED


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "dispatch":
        print("usage: dataview_dispatch.py dispatch <path>", file=sys.stderr)
        sys.exit(2)
    sys.exit(dispatch(sys.argv[2]))
