#!/usr/bin/env python3
"""Parser, server and query tests. Run with /usr/bin/python3 -I tests/test_core.py (stdlib only)."""

import codecs
import json
import os
import random
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "lib"))
TMP = tempfile.mkdtemp(prefix="cmux-dataview-test-")
os.environ["CMUX_DATAVIEW_CONFIG"] = os.path.join(TMP, "cfg")
os.environ["CMUX_DATAVIEW_CACHE"] = os.path.join(TMP, "cache")

import dataview_config as C  # noqa: E402
import dataview_render as R  # noqa: E402
import dataview_server as S  # noqa: E402

CFG = C.load()


def write(name, data, mode="w", encoding="utf-8"):
    path = os.path.join(TMP, name)
    if "b" in mode:
        with open(path, mode) as fh:
            fh.write(data)
    else:
        with open(path, mode, encoding=encoding, newline="") as fh:
            fh.write(data)
    return path


def source(path, limit=None, cfg=None):
    src = S.open_source(path, limit, cfg or CFG)
    src.wait_ready()
    return src


def all_rows(src):
    return src.get_rows(list(range(src.total())))


class Parsing(unittest.TestCase):
    def test_basic_csv(self):
        src = source(write("a.csv", "name,amount\nalice,10\nbob,20\n"))
        self.assertEqual([c["name"] for c in src.columns], ["name", "amount"])
        self.assertEqual(all_rows(src), [["alice", "10"], ["bob", "20"]])
        self.assertEqual(src.columns[1]["type"], "integer")

    def test_utf8_bom(self):
        src = source(
            write("bom.csv", codecs.BOM_UTF8 + "id,name\n1,Jürgen\n".encode(), "wb")
        )
        self.assertEqual(
            src.columns[0]["name"], "id"
        )  # the BOM must not leak into the first header
        self.assertEqual(all_rows(src)[0][1], "Jürgen")

    def test_semicolon_german_excel(self):
        src = source(write("de.csv", "Name;Betrag\nA;1.234,56\nB;7,50\nC;100\n"))
        self.assertEqual(src.notes["delimiter"], "semicolon")
        self.assertEqual(src.columns[1]["type"], "number")
        self.assertAlmostEqual(src.parsers[1]("1.234,56"), 1234.56)

    def test_quoted_newlines_and_quotes(self):
        text = 'id,note\n1,"line one\nline two"\n2,"he said ""hi"""\n3,plain\n'
        src = source(write("q.csv", text))
        self.assertEqual(src.total(), 3)
        self.assertEqual(all_rows(src)[0][1], "line one\nline two")
        self.assertEqual(all_rows(src)[1][1], 'he said "hi"')
        self.assertTrue(src.multiline)

    def test_ragged_rows(self):
        src = source(write("rag.csv", "a,b,c\n1,2\n1,2,3,4\n"))
        rows = all_rows(src)
        # Width grows to the widest row in the sample, so no cell is dropped and short rows pad.
        self.assertEqual([c["name"] for c in src.columns], ["a", "b", "c", "column_4"])
        self.assertEqual(rows, [["1", "2", "", ""], ["1", "2", "3", "4"]])

    def test_headerless(self):
        src = source(write("nohead.csv", "1,2,3\n4,5,6\n7,8,9\n"))
        self.assertEqual(src.total(), 3)
        self.assertEqual(src.columns[0]["name"], "column_1")

    def test_header_override(self):
        cfg = dict(CFG, header="yes")
        src = source(write("forced.csv", "1,2\n3,4\n"), cfg=cfg)
        self.assertEqual([c["name"] for c in src.columns], ["1", "2"])

    def test_duplicate_and_blank_headers(self):
        src = source(write("dup.csv", "x,x,,x\n1,2,3,4\n"))
        self.assertEqual(
            [c["name"] for c in src.columns], ["x", "x_2", "column_3", "x_3"]
        )

    def test_cp1252_fallback(self):
        src = source(write("win.csv", "name\nM\xfcller\n".encode("cp1252"), "wb"))
        self.assertEqual(all_rows(src)[0][0], "Müller")
        self.assertIn("cp1252", src.notes["encoding"])

    def test_utf16_excel_export(self):
        data = codecs.BOM_UTF16_LE + "a\tb\n1\tÄ\n".encode("utf-16-le")
        src = source(write("u16.tsv", data, "wb"))
        self.assertEqual(all_rows(src), [["1", "Ä"]])

    def test_binary_rejected(self):
        with self.assertRaises(R.RenderError):
            S.open_source(write("bin.csv", b"\x00\x01\x02\xff" * 200, "wb"), None, CFG)

    def test_empty_and_header_only(self):
        self.assertEqual(source(write("empty.csv", "")).total(), 0)
        src = source(write("hdr.csv", "a,b\n"))
        self.assertEqual(src.total(), 0)
        self.assertEqual(len(src.columns), 2)

    def test_crlf_and_no_trailing_newline(self):
        src = source(write("crlf.csv", "a,b\r\n1,2\r\n3,4"))
        self.assertEqual(all_rows(src), [["1", "2"], ["3", "4"]])

    def test_blank_lines_skipped(self):
        src = source(write("blank.csv", "a\n1\n\n2\n\n"))
        self.assertEqual(all_rows(src), [["1"], ["2"]])

    def test_leading_zeros_and_long_ints_stay_text(self):
        src = source(
            write(
                "ids.csv",
                "zip,big\n01234,12345678901234567890\n04321,98765432109876543210\n",
            )
        )
        self.assertEqual(src.columns[0]["type"], "text")
        self.assertEqual(src.columns[1]["type"], "text")

    def test_row_limit(self):
        path = write("many.csv", "n\n" + "".join("%d\n" % i for i in range(1000)))
        src = source(path, limit=100)
        self.assertEqual(src.total(), 100)
        self.assertTrue(src.truncated)

    def test_json_shapes(self):
        for name, payload, cols in [
            (
                "arr.json",
                [{"a": 1, "b": {"c": 2}}, {"a": 3, "d": True}],
                ["a", "b.c", "d"],
            ),
            ("wrapped.json", {"data": [{"x": 1}, {"x": 2}], "meta": {"n": 2}}, ["x"]),
            ("columnar.json", {"x": [1, 2], "y": ["a", "b"]}, ["x", "y"]),
            ("scalar.json", [1, 2, 3], ["value"]),
            ("object.json", {"k1": "v1", "k2": "v2"}, ["key", "value"]),
        ]:
            src = source(write(name, json.dumps(payload)))
            self.assertEqual([c["name"] for c in src.columns], cols, name)

    def test_jsonl_with_bad_line(self):
        src = source(write("l.jsonl", '{"a":1}\nnot json\n{"a":2}\n'))
        self.assertEqual(src.total(), 2)
        self.assertTrue(any("skipped" in w for w in src.warnings))

    def test_json_that_is_really_jsonl(self):
        src = source(write("lines.json", '{"a":1}\n{"a":2}\n'))
        self.assertEqual(src.total(), 2)



class Queries(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rnd = random.Random(7)  # seeded, so a failure is replayable
        cities = ["berlin", "munich", "Köln", "hamburg", ""]
        rows = [
            [
                str(i),
                rnd.choice(cities),
                "%.2f" % (rnd.random() * 1000),
                "2026-%02d-%02d" % (rnd.randint(1, 12), rnd.randint(1, 28)),
            ]
            for i in range(3000)
        ]
        cls.rows = rows
        text = "id,city,amount,date\n" + "".join(",".join(r) + "\n" for r in rows)
        cls.src = source(write("q.csv", text))

    def run_q(self, spec):
        res = self.src.query(dict(spec))
        if not res["qid"]:
            return list(range(res["count"]))
        return list(self.src.queries[res["qid"]])

    def test_global_search_matches_bruteforce(self):
        got = self.run_q({"q": "MUN"})
        want = [
            i for i, r in enumerate(self.rows) if any("mun" in v.lower() for v in r)
        ]
        self.assertEqual(got, want)

    def test_unicode_search(self):
        got = self.run_q({"q": "köln"})
        want = [i for i, r in enumerate(self.rows) if r[1] == "Köln"]
        self.assertEqual(got, want)

    def test_numeric_filters(self):
        for expr, fn in [
            ("> 500", lambda n: n > 500),
            ("<=10", lambda n: n <= 10),
            ("100..200", lambda n: 100 <= n <= 200),
        ]:
            got = self.run_q({"filters": {"2": expr}})
            want = [i for i, r in enumerate(self.rows) if fn(float(r[2]))]
            self.assertEqual(got, want, expr)

    def test_text_filter_grammar(self):
        self.assertEqual(
            self.run_q({"filters": {"1": "=berlin"}}),
            [i for i, r in enumerate(self.rows) if r[1] == "berlin"],
        )
        self.assertEqual(
            self.run_q({"filters": {"1": "!berlin"}}),
            [i for i, r in enumerate(self.rows) if "berlin" not in r[1].lower()],
        )
        self.assertEqual(
            self.run_q({"filters": {"1": "empty"}}),
            [i for i, r in enumerate(self.rows) if r[1] == ""],
        )

    def test_sort_numeric_desc_is_total_order(self):
        got = self.run_q({"sort": {"col": 2, "dir": -1}})
        self.assertEqual(
            sorted(got), list(range(len(self.rows)))
        )  # a permutation, nothing lost
        vals = [float(self.rows[i][2]) for i in got]
        self.assertEqual(vals, sorted(vals, reverse=True))

    def test_sort_puts_empty_last(self):
        got = self.run_q({"sort": {"col": 1, "dir": 1}})
        empties = [p for p, i in enumerate(got) if self.rows[i][1] == ""]
        self.assertTrue(
            empties
            and min(empties) > max(p for p, i in enumerate(got) if self.rows[i][1])
        )

    def test_prefilter_equals_full_parse(self):
        # The raw-line shortcut must give exactly what parsing every row gives.
        self.src.multiline = True
        slow = self.run_q({"q": "ham", "filters": {"2": "> 1"}})
        self.src.multiline = False
        self.src.queries.clear()
        fast = self.run_q({"q": "ham", "filters": {"2": "> 1"}})
        self.assertEqual(slow, fast)

    def test_page_and_expired_query(self):
        res = self.src.query({"q": "berlin"})
        page = self.src.page(res["qid"], 0, 5)
        self.assertEqual(len(page), min(5, res["count"]))
        with self.assertRaises(KeyError):
            self.src.page("nope", 0, 5)


class Server(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.state_path = os.path.join(TMP, "cache", "server.json")
        threading.Thread(target=S.serve, args=(cls.state_path,), daemon=True).start()
        for _ in range(100):
            if os.path.exists(cls.state_path):
                break
            time.sleep(0.05)
        with open(cls.state_path) as fh:
            cls.st = json.load(fh)
        cls.base = "http://127.0.0.1:%d" % cls.st["port"]

    def call(self, path, body=None, token=None, host=None):
        headers = {
            "X-Token": self.st["token"] if token is None else token,
            "Content-Type": "application/json",
        }
        if host:
            headers["Host"] = host
        req = urllib.request.Request(
            self.base + path,
            data=None if body is None else json.dumps(body).encode(),
            headers=headers,
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def test_token_required(self):
        self.assertEqual(self.call("/api/ping", token="wrong")[0], 403)
        self.assertEqual(self.call("/api/ping")[0], 200)

    def test_dns_rebinding_host_rejected(self):
        self.assertEqual(
            self.call("/api/ping", host="evil.example:%d" % self.st["port"])[0], 403
        )

    def test_open_meta_rows_flow(self):
        path = write("srv.csv", "a,b\n1,x\n2,y\n")
        code, res = self.call("/api/open", {"path": path})
        self.assertEqual(code, 200)
        sid = res["id"]
        for _ in range(50):
            code, meta = self.call("/api/meta?id=" + sid)
            if meta["status"] == "ready":
                break
            time.sleep(0.05)
        self.assertEqual(meta["total"], 2)
        code, rows = self.call("/api/rows?id=%s&start=0&end=10" % sid)
        self.assertEqual(rows["rows"], [[0, "1", "x"], [1, "2", "y"]])

    def test_unknown_id_is_expired_not_crash(self):
        self.assertEqual(self.call("/api/meta?id=deadbeef")[0], 410)

    def test_missing_file(self):
        self.assertEqual(
            self.call("/api/open", {"path": os.path.join(TMP, "nope.csv")})[0], 400
        )

    def test_bad_file_reports_error(self):
        path = write("bad.json", "{not json")
        _, res = self.call("/api/open", {"path": path})
        _, meta = self.call("/api/meta?id=" + res["id"])
        self.assertEqual(meta["status"], "error")
        self.assertIn("could not be parsed", meta["error"])

    def test_live_reload(self):
        path = write("live.csv", "a\n1\n")
        _, res = self.call("/api/open", {"path": path})
        time.sleep(0.2)
        with open(path, "a") as fh:
            fh.write("2\n3\n")
        os.utime(path, (time.time() + 5, time.time() + 5))
        for _ in range(50):
            _, meta = self.call("/api/meta?id=" + res["id"])
            if meta["status"] == "ready" and meta["total"] == 3:
                break
            time.sleep(0.05)
        self.assertEqual(meta["total"], 3)

    def test_export_excel_neutralises_formulas(self):
        path = write("evil.csv", 'name\n=HYPERLINK("http://x")\n+1\nsafe\n')
        _, res = self.call("/api/open", {"path": path})
        time.sleep(0.2)
        C.save({"exportDir": os.path.join(TMP, "exports")})
        code, out = self.call("/api/export", {"id": res["id"], "mode": "excel"})
        self.assertEqual(code, 200)
        with open(out["path"], encoding="utf-8-sig") as fh:
            text = fh.read()
        self.assertIn("'=HYPERLINK", text)
        self.assertIn("'+1", text)

    def test_reveal_outside_export_dir_refused(self):
        path = write("r.csv", "a\n1\n")
        _, res = self.call("/api/open", {"path": path})
        code, _ = self.call(
            "/api/action",
            {"id": res["id"], "action": "revealExport", "path": "/etc/passwd"},
        )
        self.assertEqual(code, 400)

    def test_settings_validated_and_unknown_keys_kept(self):
        with open(C.CONFIG_PATH, "w") as fh:
            json.dump({"myExtension": {"keep": True}, "theme": "purple"}, fh)
        _, res = self.call("/api/settings")
        self.assertEqual(res["settings"]["theme"], "light")
        self.call("/api/settings", {"settings": {"density": "compact"}})
        with open(C.CONFIG_PATH) as fh:
            saved = json.load(fh)
        self.assertEqual(saved["myExtension"], {"keep": True})
        self.assertEqual(saved["density"], "compact")


if __name__ == "__main__":
    try:
        unittest.main(verbosity=1)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
