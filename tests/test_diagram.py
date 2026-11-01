"""Tests for diagram files. Run with /usr/bin/python3 -I tests/test_diagram.py"""

import os
import struct
import sys
import tempfile
import unittest
import zipfile
import zlib

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib")
)
import dataview_diagram as DG  # noqa: E402


def tiny_png():
    raw = b"\x00\x10\x80\x30"

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


class DiagramTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.d = DG.Diagrams()

    def path(self, name, data):
        p = os.path.join(self.tmp, name)
        with open(p, "wb") as fh:
            fh.write(data)
        return p

    def test_kind_detection(self):
        self.assertEqual(DG.kind_for("a.MMD"), "mermaid")
        self.assertEqual(DG.kind_for("a.mermaid"), "mermaid")
        self.assertEqual(DG.kind_for("b.excalidraw"), "excalidraw")
        self.assertEqual(DG.kind_for("c.sketch"), "sketch")
        self.assertIsNone(DG.kind_for("d.csv"))
        self.assertIsNone(DG.kind_for("e.drawio"))

    def test_register_rejects_missing_and_unknown(self):
        with self.assertRaises(DG.DiagramError):
            self.d.register(os.path.join(self.tmp, "nope.mmd"))
        with self.assertRaises(DG.DiagramError):
            self.d.register(self.path("x.csv", b"a,b\n"))

    def test_read_and_save_round_trip(self):
        p = self.path("f.mmd", "flowchart LR\n  A --> B\n".encode())
        did = self.d.register(p)
        info = self.d.read(did)
        self.assertTrue(info["editable"])
        self.assertIn("A --> B", info["text"])
        self.d.save(did, "flowchart LR\n  ü --> ß\n", info["mtime"])
        with open(p, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "flowchart LR\n  ü --> ß\n")

    def test_stale_save_is_a_conflict_unless_forced(self):
        p = self.path("g.mmd", b"flowchart LR\n")
        did = self.d.register(p)
        old = self.d.read(did)["mtime"]
        os.utime(p, (old + 10, old + 10))
        with self.assertRaises(DG.ConflictError):
            self.d.save(did, "changed", old)
        self.d.save(did, "forced", old, force=True)
        with open(p, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "forced")

    def test_save_keeps_file_mode_and_leaves_no_temp_files(self):
        p = self.path("h.excalidraw", b"{}")
        os.chmod(p, 0o640)
        did = self.d.register(p)
        self.d.save(did, '{"type":"excalidraw"}', self.d.read(did)["mtime"])
        self.assertEqual(os.stat(p).st_mode & 0o777, 0o640)
        self.assertEqual(sorted(os.listdir(self.tmp)), ["h.excalidraw"])

    def test_sketch_is_read_only_and_preview_is_extracted(self):
        png = tiny_png()
        p = os.path.join(self.tmp, "s.sketch")
        with zipfile.ZipFile(p, "w") as zf:
            zf.writestr("document.json", "{}")
            zf.writestr(DG.SKETCH_PREVIEW, png)
        did = self.d.register(p)
        info = self.d.read(did)
        self.assertFalse(info["editable"])
        self.assertTrue(info["hasPreview"])
        self.assertEqual(DG.sketch_preview(p), png)
        with self.assertRaises(DG.DiagramError):
            self.d.save(did, "x", info["mtime"])

    def test_sketch_without_preview_or_not_a_zip(self):
        p = os.path.join(self.tmp, "n.sketch")
        with zipfile.ZipFile(p, "w") as zf:
            zf.writestr("document.json", "{}")
        self.assertFalse(DG.sketch_has_preview(p))
        with self.assertRaises(DG.DiagramError):
            DG.sketch_preview(p)
        bad = self.path("bad.sketch", b"not a zip")
        self.assertFalse(DG.sketch_has_preview(bad))
        with self.assertRaises(DG.DiagramError):
            DG.sketch_preview(bad)

    def test_unknown_id_expires(self):
        with self.assertRaises(KeyError):
            self.d.read("0" * 16)

    def test_vendor_file_refuses_paths_outside_vendor(self):
        vendor = tempfile.mkdtemp()
        with open(os.path.join(vendor, "ok.js"), "w") as fh:
            fh.write("1")
        old = DG.VENDOR
        DG.VENDOR = vendor
        try:
            self.assertEqual(DG.vendor_file("ok.js")[0], b"1")
            self.assertIsNone(DG.vendor_file("../" + os.path.basename(self.tmp)))
            self.assertIsNone(DG.vendor_file("/etc/hosts"))
            self.assertIsNone(DG.vendor_file("missing.js"))
        finally:
            DG.VENDOR = old


if __name__ == "__main__":
    unittest.main(verbosity=1)
