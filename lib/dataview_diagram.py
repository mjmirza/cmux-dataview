"""Diagram files (Mermaid, Excalidraw, Sketch) for the helper. Reads, saves and serves the offline editor code."""

import hashlib
import mimetypes
import os
import tempfile
import threading
import zipfile

VENDOR = os.environ.get(
    "CMUX_DATAVIEW_VENDOR",
    os.path.expanduser("~/Library/Application Support/cmux-dataview/vendor"),
)
KINDS = {
    "mmd": "mermaid",
    "mermaid": "mermaid",
    "excalidraw": "excalidraw",
    "sketch": "sketch",
}
EDITABLE = {"mermaid", "excalidraw"}
MAX_TEXT = 50 * 1024 * 1024
SKETCH_PREVIEW = "previews/preview.png"


class DiagramError(Exception):
    pass


def kind_for(path):
    return KINDS.get(os.path.splitext(path)[1].lower().lstrip("."))


class Diagrams:
    def __init__(self):
        self.items = {}
        self.lock = threading.Lock()

    def register(self, path):
        real = os.path.realpath(path)
        kind = kind_for(real)
        if not kind:
            raise DiagramError("Not a diagram file: %s" % path)
        if not os.path.isfile(real):
            raise DiagramError("File not found: %s" % path)
        did = hashlib.sha256(("d:" + real).encode()).hexdigest()[:16]
        with self.lock:
            self.items[did] = {"path": real, "kind": kind}
        return did

    def get(self, did):
        with self.lock:
            item = self.items.get(did)
        if item is None:
            raise KeyError("This view has expired. Double-click the file again.")
        return item

    def read(self, did):
        item = self.get(did)
        path, kind = item["path"], item["kind"]
        st = os.stat(path)
        info = {
            "kind": kind,
            "name": os.path.basename(path),
            "file": path,
            "mtime": st.st_mtime,
            "size": st.st_size,
            "editable": kind in EDITABLE,
            "vendorReady": kind == "sketch" or vendor_ready(kind),
        }
        if kind == "sketch":
            info["hasPreview"] = sketch_has_preview(path)
            return info
        if st.st_size > MAX_TEXT:
            raise DiagramError("File is larger than 50 MB")
        with open(path, "rb") as fh:
            info["text"] = fh.read().decode("utf-8", errors="replace")
        return info

    def save(self, did, text, mtime, force=False):
        item = self.get(did)
        if item["kind"] not in EDITABLE:
            raise DiagramError("This file type is read only")
        if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_TEXT:
            raise DiagramError("Nothing to save, or the content is larger than 50 MB")
        path = item["path"]
        current = os.stat(path).st_mtime
        if not force and mtime is not None and abs(current - float(mtime)) > 1e-6:
            raise ConflictError("The file changed on disk after you opened it")
        folder = os.path.dirname(path)
        fd, tmp = tempfile.mkstemp(prefix=".dataview-", dir=folder)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
                fh.write(text)
            os.chmod(tmp, os.stat(path).st_mode & 0o777)
            os.replace(tmp, path)
        except OSError:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise
        return os.stat(path).st_mtime


class ConflictError(DiagramError):
    pass


def vendor_ready(kind):
    need = {"mermaid": "mermaid.min.js", "excalidraw": "excalidraw/excalidraw.js"}[kind]
    return os.path.isfile(os.path.join(VENDOR, need))


def sketch_has_preview(path):
    try:
        with zipfile.ZipFile(path) as zf:
            return SKETCH_PREVIEW in zf.namelist()
    except (zipfile.BadZipFile, OSError):
        return False


def sketch_preview(path):
    try:
        with zipfile.ZipFile(path) as zf:
            return zf.read(SKETCH_PREVIEW)
    except KeyError as exc:
        raise DiagramError("This Sketch file has no saved preview image") from exc
    except (zipfile.BadZipFile, OSError) as exc:
        raise DiagramError("Not a readable Sketch file") from exc


def vendor_file(rel):
    """Return (bytes, content type) for a file under the vendor folder, refusing anything outside it."""
    base = os.path.realpath(VENDOR)
    full = os.path.realpath(os.path.join(base, rel))
    if not full.startswith(base + os.sep) or not os.path.isfile(full):
        return None
    ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
    if full.endswith(".woff2"):
        ctype = "font/woff2"
    with open(full, "rb") as fh:
        return fh.read(), ctype
