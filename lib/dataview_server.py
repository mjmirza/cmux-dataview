#!/usr/bin/env python3
"""Local data server for cmux-dataview. 127.0.0.1 only, token-gated, stops when idle.
Indexes files by byte offset and runs search, filter and sort across the whole file. See docs/DESIGN.md."""

import array
import codecs
import csv
import hashlib
import io
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import dataview_config as C  # noqa: E402
import dataview_render as R  # noqa: E402

VERSION = R.VERSION
CACHE_DIR = os.environ.get(
    "CMUX_DATAVIEW_CACHE", os.path.expanduser("~/Library/Caches/cmux-dataview")
)
SAMPLE_ROWS = 5000
MAX_PAGE = 2000
SCAN_CHECK = 2000
DELIMS = {"comma": ",", "semicolon": ";", "tab": "\t", "pipe": "|"}
DELIM_NAMES = {v: k for k, v in DELIMS.items()}


def log(msg):
    sys.stderr.write("%s server %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    sys.stderr.flush()


class Superseded(Exception):
    """A newer query arrived for the same file, so this scan stops early."""


# ------------------------------------------------------------------ value helpers


def number_parser(stats, locale_pref):
    """Return a str -> float|None function matching how the column was typed."""
    locale = stats.get("locale_code") or "en"
    if locale_pref in ("en", "de"):
        locale = locale_pref
    conv = R.to_de if locale == "de" else R.to_en

    def parse(v):
        c = R.strip_currency(v)
        if not c:
            return None
        try:
            n = conv(c)
        except ValueError:
            return None
        return None if n != n or n in (float("inf"), float("-inf")) else n

    return parse


def date_key(v):
    m = re.match(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})", v)
    if m:
        return "%s-%02d-%02d%s" % (
            m.group(3),
            int(m.group(2)),
            int(m.group(1)),
            v[m.end() :],
        )
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})", v)
    if m:
        return "%s-%02d-%02d%s" % (
            m.group(3),
            int(m.group(1)),
            int(m.group(2)),
            v[m.end() :],
        )
    return v.replace("/", "-")


def compile_filter(expr, col, parse_num):
    """Same grammar as the viewer. Text, =exact, !not, empty, !empty, and for numbers > < >= <= = != a..b."""
    expr = (expr or "").strip()
    if not expr:
        return None
    low = expr.lower()
    if low == "empty":
        return lambda row: row[col].strip() == ""
    if low == "!empty":
        return lambda row: row[col].strip() != ""
    if parse_num:
        rng = re.match(r"^(-?[\d.]+)\s*\.\.\s*(-?[\d.]+)$", expr)
        if rng:
            a, b = float(rng.group(1)), float(rng.group(2))

            def in_range(row):
                n = parse_num(row[col])
                return n is not None and a <= n <= b

            return in_range
        m = re.match(r"^(>=|<=|>|<|=|!=)\s*(-?[\d.,]+)$", expr)
        if m:
            x, op = float(m.group(2).replace(",", "")), m.group(1)
            ops = {
                ">": x.__lt__,
                "<": x.__gt__,
                ">=": x.__le__,
                "<=": x.__ge__,
                "=": x.__eq__,
                "!=": x.__ne__,
            }
            fn = ops[op]

            def compare(row):
                n = parse_num(row[col])
                return n is not None and fn(n)

            return compare
    if expr[0] == "=":
        exact = low[1:]
        return lambda row: row[col].lower() == exact
    if expr[0] == "!":
        neg = low[1:]
        return lambda row: neg not in row[col].lower()
    return lambda row: low in row[col].lower()


# ------------------------------------------------------------------ sources


class Source:
    """Shared state and the query engine. Subclasses provide row access."""

    def __init__(self, path, limit, cfg):
        self.path = path
        self.name = os.path.basename(path)
        self.limit = limit
        self.cfg = cfg
        st = os.stat(path)
        self.size, self.mtime = st.st_size, st.st_mtime
        self.columns = []
        self.warnings = []
        self.notes = {}
        self.status = "indexing"
        self.progress = 0.0
        self.error = None
        self.truncated = False
        self.generation = 0
        self.lock = threading.Lock()
        self.queries = {}
        self.stats_cache = {}
        self.parsers = []
        self.query_progress = 0.0

    def finish_columns(self, headers, sample):
        locale_pref = self.cfg.get("numberLocale", "auto")
        cols, parsers = [], []
        for i, h in enumerate(headers):
            values = [r[i] if i < len(r) else "" for r in sample]
            kind, _nums, stats = R.infer_column(values)
            if stats.get("locale") == "German decimals":
                stats["locale_code"] = "de"
            numeric = kind in ("integer", "number")
            if locale_pref in ("en", "de") and kind == "text":
                # A forced locale can turn a column the sniffer saw as text into numbers.
                probe = number_parser({"locale_code": locale_pref}, locale_pref)
                present = [v for v in values if v.strip()]
                if present and all(probe(v) is not None for v in present):
                    kind, numeric = "number", True
            cols.append({"name": h, "type": kind})
            parsers.append(number_parser(stats, locale_pref) if numeric else None)
        self.columns, self.parsers = cols, parsers

    def meta(self):
        return {
            "name": self.name,
            "file": self.path,
            "size": self.size,
            "mtime": self.mtime,
            "status": self.status,
            "progress": round(self.progress, 4),
            "error": self.error,
            "total": self.total(),
            "truncated": self.truncated,
            "limit": self.limit,
            "columns": [
                {
                    "name": c["name"],
                    "type": c["type"],
                    "numeric": self.parsers[i] is not None,
                }
                for i, c in enumerate(self.columns)
            ],
            "warnings": list(self.warnings),
            "notes": dict(self.notes),
            "queryProgress": round(self.query_progress, 4),
            "version": VERSION,
        }

    def wait_ready(self, timeout=600):
        end = time.time() + timeout
        while self.status == "indexing" and time.time() < end:
            time.sleep(0.05)

    def query(self, spec):
        """Run a whole-file search, filter and sort. Returns a query id and the match count."""
        q = (spec.get("q") or "").strip().lower()
        filters = {
            str(k): v
            for k, v in (spec.get("filters") or {}).items()
            if isinstance(v, str) and v.strip()
        }
        sort = spec.get("sort") if isinstance(spec.get("sort"), dict) else None
        cols = spec.get("cols")
        visible = [
            c
            for c in (cols if isinstance(cols, list) else range(len(self.columns)))
            if isinstance(c, int) and 0 <= c < len(self.columns)
        ]
        if not q and not filters and not sort:
            return {"qid": "", "count": self.total()}
        key = json.dumps([q, sorted(filters.items()), sort, visible], sort_keys=True)
        with self.lock:
            if key in self.queries:
                return self.queries[key]
            self.generation += 1
            gen = self.generation
        self.wait_ready()
        fns = []
        for k, expr in filters.items():
            col = int(k)
            if 0 <= col < len(self.columns):
                fn = compile_filter(expr, col, self.parsers[col])
                if fn:
                    fns.append(fn)

        total = max(1, self.total())

        def check(rid=0):
            self.query_progress = rid / total
            if self.generation != gen:
                raise Superseded()

        ids = array.array("Q")
        keys = []
        scol = sort.get("col") if sort else None
        sdir = -1 if sort and sort.get("dir") == -1 else 1
        if not isinstance(scol, int) or not 0 <= scol < len(self.columns):
            scol = None
        sparse = self.parsers[scol] if scol is not None else None
        sdate = scol is not None and self.columns[scol]["type"] == "date"
        for rid, row in self.iter_rows(check, needle=q or None):
            if fns and not all(f(row) for f in fns):
                continue
            if q and not any(q in row[c].lower() for c in visible):
                continue
            ids.append(rid)
            if scol is not None:
                v = row[scol]
                if sparse:
                    keys.append(sparse(v))
                else:
                    keys.append(date_key(v) if sdate else v.casefold())
        if scol is not None:
            present = [i for i in range(len(ids)) if keys[i] not in (None, "")]
            missing = [i for i in range(len(ids)) if keys[i] in (None, "")]
            present.sort(key=lambda i: keys[i], reverse=(sdir == -1))
            ids = array.array("Q", [ids[i] for i in present + missing])
        qid = hashlib.sha256(key.encode()).hexdigest()[:12]
        result = {"qid": qid, "count": len(ids)}
        with self.lock:
            if len(self.queries) > 16:
                self.queries.clear()
            self.queries[key] = result
            self.queries[qid] = ids
        return result

    def page(self, qid, start, end):
        start = max(0, start)
        end = min(end, start + MAX_PAGE)
        if qid:
            ids = self.queries.get(qid)
            if ids is None:
                raise KeyError("query expired")
            want = list(ids[start:end])
        else:
            want = list(range(start, min(end, self.total())))
        rows = self.get_rows(want)
        return [[rid] + row for rid, row in zip(want, rows)]

    def column_stats(self, col):
        if col in self.stats_cache:
            return self.stats_cache[col]
        self.wait_ready()
        parse = self.parsers[col]
        dates = self.columns[col]["type"] == "date"
        empty = count = lengths = ncount = 0
        uniq, uniq_capped, top = set(), False, Counter()
        nmin = nmax = first = last = None
        nsum = 0.0
        for _rid, row in self.iter_rows(lambda *_: None):
            v = row[col]
            count += 1
            if not v.strip():
                empty += 1
                continue
            if not uniq_capped:
                uniq.add(v)
                uniq_capped = len(uniq) > 1000000
            if len(top) < 200000 or v in top:
                top[v] += 1
            lengths += len(v)
            if parse:
                n = parse(v)
                if n is not None:
                    ncount += 1
                    nsum += n
                    nmin = n if nmin is None or n < nmin else nmin
                    nmax = n if nmax is None or n > nmax else nmax
            if dates:
                k = date_key(v)
                first = k if first is None or k < first else first
                last = k if last is None or k > last else last
        present = count - empty
        out = {
            "rows": count,
            "empty": empty,
            "unique": len(uniq),
            "uniqueCapped": uniq_capped,
            "top": [[k[:80], c] for k, c in top.most_common(5)],
            "type": self.columns[col]["type"],
        }
        if present:
            out["avgLength"] = lengths / present
        if ncount:
            out.update(min=nmin, max=nmax, sum=nsum, mean=nsum / ncount, numeric=ncount)
        if first is not None:
            out.update(first=first, last=last)
        self.stats_cache[col] = out
        return out


class DelimitedSource(Source):
    """CSV/TSV indexed by byte offset. Quote parity finds record ends across embedded newlines."""

    def __init__(self, path, limit, cfg):
        Source.__init__(self, path, limit, cfg)
        self.data_path = path
        self.starts = array.array("Q")
        self.ends = array.array("Q")
        self.skip = 0
        self.width = 0
        self.headers = []
        self.encoding = "utf-8"
        self.delim = ","
        self.newline = b"\n"
        self.base = 0
        self.ragged = 0
        self.multiline = False
        self._prepare()
        threading.Thread(target=self._index, daemon=True).start()

    def _prepare(self):
        with open(self.path, "rb") as fh:
            head = fh.read(65536)
        probe = head[:8192]
        utf16 = (
            head.startswith(codecs.BOM_UTF16_LE)
            or head.startswith(codecs.BOM_UTF16_BE)
            or (
                b"\x00" in probe
                and (
                    probe[1::2].count(0) > len(probe) // 4
                    or probe[0::2].count(0) > len(probe) // 4
                )
            )
        )
        if utf16:
            self.data_path = self._transcode_utf16()
            with open(self.data_path, "rb") as fh:
                head = fh.read(65536)
            self.notes["encoding"] = "utf-16 (converted)"
        elif b"\x00" in probe:
            raise R.RenderError(
                "This file looks binary, not text, so it cannot be shown as a table."
            )
        if head.startswith(codecs.BOM_UTF8):
            self.base = len(codecs.BOM_UTF8)
            self.notes.setdefault("encoding", "utf-8 (BOM)")
        sample = head[self.base :]
        try:
            # final=False tolerates a multibyte character cut at the end of the sample.
            codecs.getincrementaldecoder("utf-8")().decode(sample, final=False)
            self.notes.setdefault("encoding", "utf-8")
        except UnicodeDecodeError:
            self.encoding = "cp1252"
            self.notes["encoding"] = "cp1252 (Windows)"
            self.warnings.append(
                "This file is not valid UTF-8, so it was read as Windows cp1252. Accented characters may be off."
            )
        if b"\n" not in sample and b"\r" in sample:
            self.newline = b"\r"
        text = sample.decode(self.encoding, errors="replace")
        choice = self.cfg.get("delimiter", "auto")
        ext = os.path.splitext(self.path)[1].lower()
        if choice in DELIMS:
            self.delim = DELIMS[choice]
        elif ext in (".tsv", ".tab"):
            self.delim = "\t"
        elif ext == ".psv":
            self.delim = "|"
        else:
            self.delim = R.sniff_delimiter(text)
        self.notes["delimiter"] = DELIM_NAMES.get(self.delim, self.delim)
        self.notes["format"] = "TSV" if self.delim == "\t" else "Delimited text"

    def _transcode_utf16(self):
        os.makedirs(CACHE_DIR, mode=0o700, exist_ok=True)
        out = os.path.join(
            CACHE_DIR, hashlib.sha256(self.path.encode()).hexdigest()[:16] + ".utf8.txt"
        )
        with open(self.path, "rb") as src:
            head = src.read(4)
            src.seek(0)
            if head[:2] in (codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE):
                enc = "utf-16"
            else:
                enc = "utf-16-le" if head[1:2] == b"\x00" else "utf-16-be"
            reader = codecs.getreader(enc)(src, errors="replace")
            fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as dst:
                while True:
                    chunk = reader.read(1 << 20)
                    if not chunk:
                        break
                    dst.write(chunk)
        return out

    def _index(self):
        try:
            self._scan()
        except (
            Exception
        ) as exc:  # the viewer shows the message instead of spinning forever
            log("index failed for %s: %r" % (self.path, exc))
            self.error = "Indexing failed: %s" % exc
        finally:
            self.progress = 1.0
            if self.status == "indexing":
                self.status = "error" if self.error else "ready"

    def _scan(self):
        nl, quote = self.newline, b'"'
        size = os.path.getsize(self.data_path)
        cap = (
            None if self.limit is None else self.limit + 1
        )  # one extra for the header record
        columns_done = False
        with open(self.data_path, "rb") as fh:
            fh.seek(self.base)
            carry, buf_base, rec_start, parity = b"", self.base, self.base, 0
            while True:
                chunk = fh.read(8 << 20)
                if not chunk:
                    break
                buf = carry + chunk
                i = 0
                while True:
                    j = buf.find(nl, i)
                    if j < 0:
                        break
                    parity ^= buf.count(quote, i, j) & 1
                    if parity:
                        self.multiline = (
                            True  # a quoted value spans lines, so no raw-line shortcuts
                        )
                    else:
                        end = buf_base + j + 1
                        single_line = rec_start == buf_base + i
                        if not (single_line and buf[i:j].strip(b"\r") == b""):
                            self.starts.append(rec_start)
                            self.ends.append(end)
                        rec_start = end
                        if cap is not None and len(self.starts) >= cap:
                            self.truncated = True
                            break
                    i = j + 1
                if self.truncated:
                    break
                carry = buf[i:]
                buf_base += i
                self.progress = min(
                    0.999, (buf_base - self.base) / max(1, size - self.base)
                )
                if not columns_done and len(self.starts) > SAMPLE_ROWS:
                    self._columns()
                    columns_done = True
            if not self.truncated and carry.strip(b"\r\n") != b"":
                self.starts.append(rec_start)
                self.ends.append(buf_base + len(carry))
        if parity and not self.truncated:
            self.warnings.append(
                "The file ends inside a quoted value. The last row may be incomplete."
            )
        if not columns_done:
            self._columns()
        if self.truncated:
            self.warnings.append(
                "Showing the first %s rows. The setting bigFile.previewRows controls this."
                % format(self.limit, ",")
            )
        if self.ragged:
            self.warnings.append(
                "Some rows have more cells than the header. The extra cells were joined into the last column."
            )
        self.status = "ready"

    def _read_records(self, recs):
        out = []
        with open(self.data_path, "rb") as fh:
            for r in recs:
                fh.seek(self.starts[r])
                text = fh.read(self.ends[r] - self.starts[r]).decode(
                    self.encoding, errors="replace"
                )
                try:
                    rec = next(
                        csv.reader(io.StringIO(text, newline=""), delimiter=self.delim),
                        [],
                    )
                except csv.Error:
                    rec = [text.rstrip("\r\n")]
                out.append(rec)
        return out

    def _columns(self):
        n = min(len(self.starts), SAMPLE_ROWS + 1)
        recs = self._read_records(range(n)) if n else []
        if not recs:
            self.headers, self.width, self.columns, self.parsers = [], 0, [], []
            return
        header, data = recs[0], recs[1:]
        choice = self.cfg.get("header", "auto")
        if choice == "no" or (choice == "auto" and R.looks_headerless(header, data)):
            self.skip, data, header = 0, recs, []
            if choice == "auto":
                self.warnings.append(
                    "The first row looks like data, not column names, so columns are numbered instead."
                )
        else:
            self.skip = 1
        self.width = (
            max([len(header)] + [len(r) for r in data]) if data else len(header)
        )
        self.headers = R.clean_headers(header, self.width)
        self.finish_columns(self.headers, [self._shape(r) for r in data])

    def _shape(self, rec):
        w = self.width
        if len(rec) > w:
            self.ragged += 1
            rec = rec[: w - 1] + [self.delim.join(rec[w - 1 :])]
        elif len(rec) < w:
            rec = rec + [""] * (w - len(rec))
        return [
            v if len(v) <= R.MAX_CELL else v[: R.MAX_CELL] + " ... [truncated]"
            for v in rec
        ]

    def total(self):
        return max(0, len(self.starts) - self.skip) if self.columns else 0

    def get_rows(self, ids):
        return [
            self._shape(r) for r in self._read_records([i + self.skip for i in ids])
        ]

    def _fast(self, rec):
        # Scans skip the per-cell truncation that display needs, which is most of the cost.
        return rec if len(rec) == self.width else self._shape(rec)

    def iter_rows(self, check, needle=None):
        limit = self.total()
        # A raw-line pre-check is exact only when no record spans lines and the needle
        # cannot be hidden by CSV quoting.
        prefilter = (
            needle
            and not self.multiline
            and self.newline == b"\n"
            and '"' not in needle
            and self.delim not in needle
        )
        with open(self.data_path, "rb") as fh:
            fh.seek(self.base)
            text = io.TextIOWrapper(
                fh, encoding=self.encoding, errors="replace", newline=""
            )
            rid = -self.skip
            if prefilter:
                for line in text:
                    if not line.strip("\r\n"):
                        continue
                    if rid >= 0:
                        if rid >= limit:
                            break
                        if rid % SCAN_CHECK == 0:
                            check(rid)
                        if needle in line.lower():
                            rec = next(csv.reader([line], delimiter=self.delim), [])
                            yield rid, self._fast(rec)
                    rid += 1
                return
            for rec in csv.reader(text, delimiter=self.delim):
                if not rec:
                    continue
                if rid >= 0:
                    if rid >= limit:
                        break
                    if rid % SCAN_CHECK == 0:
                        check(rid)
                    yield rid, self._fast(rec)
                rid += 1


class MemorySource(Source):
    """JSON and JSON Lines, held in memory."""

    def __init__(self, path, limit, cfg):
        Source.__init__(self, path, limit, cfg)
        notes = {"warnings": [], "format": None, "delimiter": None, "encoding": None}
        with open(path, "rb") as fh:
            raw = fh.read()
        text, notes["encoding"] = R.decode(raw)
        if os.path.splitext(path)[1].lower() in R.JSONL_EXT:
            headers, rows = R.parse_jsonl(text, notes)
        else:
            notes["format"] = "JSON"
            headers, rows = R.parse_json(text, notes)
        if limit is not None and len(rows) > limit:
            rows = rows[:limit]
            self.truncated = True
            notes["warnings"].append(
                "Showing the first %s records." % format(limit, ",")
            )
        self.rows = [
            [
                v if len(v) <= R.MAX_CELL else v[: R.MAX_CELL] + " ... [truncated]"
                for v in r
            ]
            for r in rows
        ]
        self.warnings = notes.pop("warnings")
        self.notes = {k: v for k, v in notes.items() if v}
        self.finish_columns(headers, self.rows[:SAMPLE_ROWS])
        self.status = "ready"
        self.progress = 1.0

    def total(self):
        return len(self.rows)

    def get_rows(self, ids):
        return [self.rows[i] for i in ids if 0 <= i < len(self.rows)]

    def iter_rows(self, check, needle=None):
        for rid, row in enumerate(self.rows):
            if rid % SCAN_CHECK == 0:
                check(rid)
            yield rid, row


def open_source(path, limit, cfg):
    ext = os.path.splitext(path)[1].lower()
    if ext in R.JSON_EXT or ext in R.JSONL_EXT:
        size = os.path.getsize(path)
        if size > R.MAX_BYTES:
            raise R.RenderError(
                "This JSON file is %.0f MB. JSON is loaded whole, so the limit is %.0f MB."
                % (size / 1e6, R.MAX_BYTES / 1e6)
            )
        return MemorySource(path, limit, cfg)
    return DelimitedSource(path, limit, cfg)


# ------------------------------------------------------------------ http


class State:
    def __init__(self, token, port):
        self.token = token
        self.port = port
        self.sources = {}
        self.errors = {}
        self.last_seen = time.time()
        self.lock = threading.Lock()

    def register(self, path, limit):
        real = os.path.realpath(path)
        if not os.path.isfile(real):
            raise R.RenderError("File not found: %s" % path)
        sid = hashlib.sha256(real.encode()).hexdigest()[:16]
        cfg = C.load()
        with self.lock:
            old = self.sources.get(sid)
            if (
                old is not None
                and old.limit == limit
                and old.cfg == cfg
                and not self.changed(old)
            ):
                return sid
        try:
            src = open_source(real, limit, cfg)
        except (R.RenderError, OSError, ValueError, RecursionError, MemoryError) as exc:
            with self.lock:
                self.sources.pop(sid, None)
                self.errors[sid] = {
                    "name": os.path.basename(real),
                    "file": real,
                    "error": str(exc),
                }
            return sid
        with self.lock:
            self.errors.pop(sid, None)
            self.sources[sid] = src
        return sid

    @staticmethod
    def changed(src):
        try:
            st = os.stat(src.path)
        except OSError:
            return True
        return st.st_mtime != src.mtime or st.st_size != src.size

    def get(self, sid):
        with self.lock:
            src = self.sources.get(sid)
        if src is None:
            return None
        if src.cfg.get("liveReload") and self.changed(src) and os.path.isfile(src.path):
            self.register(src.path, src.limit)
            with self.lock:
                src = self.sources.get(sid)
        return src


class Handler(BaseHTTPRequestHandler):
    server_version = "cmux-dataview/" + VERSION
    state = None  # set in serve()

    def log_message(self, _fmt, *_args):  # silence per-request logging
        return

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = (
            body
            if isinstance(body, bytes)
            else json.dumps(body, allow_nan=False, default=str).encode("utf-8")
        )
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(data)

    def _authorised(self, query):
        st = self.state
        host = (self.headers.get("Host") or "").lower()
        if host not in ("127.0.0.1:%d" % st.port, "localhost:%d" % st.port):
            return False  # DNS rebinding defence
        token = self.headers.get("X-Token") or (query.get("t") or [""])[0]
        return secrets.compare_digest(token, st.token)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 1 << 20:
            raise ValueError("request too large")
        data = json.loads((self.rfile.read(n) if n else b"{}").decode("utf-8") or "{}")
        if not isinstance(data, dict):
            raise ValueError("expected a JSON object")
        return data

    def _route(self, method):
        url = urlparse(self.path)
        query = parse_qs(url.query)
        self.state.last_seen = time.time()
        if not self._authorised(query):
            return self._send(403, {"error": "forbidden"})
        try:
            if method == "GET" and url.path.startswith("/v/"):
                with open(os.path.join(HERE, "viewer.html"), "rb") as fh:
                    return self._send(200, fh.read(), "text/html; charset=utf-8")
            name = "api_%s_%s" % (
                method.lower(),
                url.path.strip("/").replace("api/", "", 1).replace("/", "_"),
            )
            handler = getattr(self, name, None)
            if handler is None:
                return self._send(404, {"error": "not found"})
            return handler(query)
        except Superseded:
            return self._send(409, {"error": "superseded"})
        except KeyError as exc:
            return self._send(410, {"error": str(exc).strip("'\"")})
        except (ValueError, R.RenderError) as exc:
            return self._send(400, {"error": str(exc)})
        except BrokenPipeError:
            return None
        except (
            Exception
        ) as exc:  # report it, never let a request kill the thread silently
            log("error on %s %s: %r" % (method, url.path, exc))
            return self._send(500, {"error": "internal error: %s" % exc})

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def _source(self, params):
        sid = params.get("id")
        sid = sid[0] if isinstance(sid, list) else (sid or "")
        src = self.state.get(sid)
        if src is None:
            err = self.state.errors.get(sid)
            if err:
                raise R.RenderError(err["error"])
            raise KeyError("This view has expired. Double-click the file again.")
        return src

    def api_get_ping(self, query):
        return self._send(200, {"ok": True, "version": VERSION, "pid": os.getpid()})

    def api_post_open(self, query):
        body = self._body()
        limit = body.get("limit")
        limit = (
            int(limit)
            if isinstance(limit, (int, float))
            and not isinstance(limit, bool)
            and limit > 0
            else None
        )
        sid = self.state.register(str(body.get("path") or ""), limit)
        url = "http://127.0.0.1:%d/v/%s?t=%s" % (self.state.port, sid, self.state.token)
        return self._send(200, {"id": sid, "url": url})

    def api_get_meta(self, query):
        sid = (query.get("id") or [""])[0]
        err = self.state.errors.get(sid)
        if err and sid not in self.state.sources:
            return self._send(
                200,
                dict(err, status="error", columns=[], total=0, warnings=[], notes={}),
            )
        return self._send(200, self._source(query).meta())

    def api_post_query(self, query):
        body = self._body()
        return self._send(200, self._source(body).query(body))

    def api_get_rows(self, query):
        src = self._source(query)
        start = int((query.get("start") or ["0"])[0])
        end = int((query.get("end") or ["100"])[0])
        return self._send(
            200, {"rows": src.page((query.get("qid") or [""])[0], start, end)}
        )

    def api_get_stats(self, query):
        src = self._source(query)
        col = int((query.get("col") or ["0"])[0])
        if not 0 <= col < len(src.columns):
            raise ValueError("no such column")
        return self._send(200, src.column_stats(col))

    def api_post_export(self, query):
        body = self._body()
        src = self._source(body)
        mode = "excel" if body.get("mode") == "excel" else "raw"
        cols = [
            c
            for c in (body.get("cols") or range(len(src.columns)))
            if isinstance(c, int) and 0 <= c < len(src.columns)
        ]
        qid = body.get("qid") or ""
        target_dir = os.path.expanduser(C.load()["exportDir"])
        os.makedirs(target_dir, exist_ok=True)
        stem = os.path.splitext(src.name)[0] + (
            "-excel-safe" if mode == "excel" else "-filtered"
        )
        path, n = os.path.join(target_dir, stem + ".csv"), 2
        while os.path.exists(path):
            path, n = os.path.join(target_dir, "%s-%d.csv" % (stem, n)), n + 1

        def cell(v):
            # OWASP CSV injection guidance. Excel mode neutralises formula prefixes.
            return (
                "'" + v
                if mode == "excel" and v[:1] in ("=", "+", "-", "@", "\t", "\r")
                else v
            )

        count = 0
        with open(path, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh)
            w.writerow([cell(src.columns[c]["name"]) for c in cols])
            if qid:
                ids = src.queries.get(qid)
                if ids is None:
                    raise KeyError("query expired")
                for at in range(0, len(ids), MAX_PAGE):
                    for row in src.get_rows(list(ids[at : at + MAX_PAGE])):
                        w.writerow([cell(row[c]) for c in cols])
                        count += 1
            else:
                for _rid, row in src.iter_rows(lambda *_: None):
                    w.writerow([cell(row[c]) for c in cols])
                    count += 1
        return self._send(200, {"path": path, "rows": count})

    def api_post_action(self, query):
        body = self._body()
        src = self._source(body)
        action = body.get("action")
        target = src.path
        if action == "revealExport":
            export_dir = os.path.realpath(os.path.expanduser(C.load()["exportDir"]))
            target = body.get("path")
            if not isinstance(target, str) or not os.path.realpath(target).startswith(
                export_dir + os.sep
            ):
                raise ValueError("can only reveal files in the export folder")
        if action in ("reveal", "revealExport"):
            subprocess.run(["/usr/bin/open", "-R", "--", target], check=False)
        elif action == "openDefault":
            subprocess.run(["/usr/bin/open", "--", target], check=False)
        else:
            raise ValueError("unknown action")
        return self._send(200, {"ok": True})

    def api_get_settings(self, query):
        return self._send(
            200,
            {
                "settings": C.load(),
                "choices": C.CHOICES,
                "defaults": C.DEFAULTS,
                "path": C.CONFIG_PATH,
            },
        )

    def api_post_settings(self, query):
        changes = self._body().get("settings")
        if not isinstance(changes, dict):
            raise ValueError("settings must be an object")
        return self._send(200, {"settings": C.save(changes)})


def serve(state_path):
    os.makedirs(os.path.dirname(state_path), mode=0o700, exist_ok=True)
    token = secrets.token_urlsafe(24)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    httpd.daemon_threads = True
    port = httpd.server_address[1]
    Handler.state = State(token, port)
    tmp = "%s.%d.tmp" % (state_path, os.getpid())
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(
            {"port": port, "token": token, "pid": os.getpid(), "version": VERSION}, fh
        )
    os.replace(tmp, state_path)
    log("listening on 127.0.0.1:%d pid %d" % (port, os.getpid()))

    def watchdog():
        while True:
            time.sleep(20)
            idle = C.load()["idleMinutes"] * 60
            if time.time() - Handler.state.last_seen > idle:
                log("idle for %d min, stopping" % (idle // 60))
                try:
                    with open(state_path) as fh:
                        if json.load(fh).get("pid") == os.getpid():
                            os.remove(state_path)
                except (OSError, ValueError):
                    log("state file already gone")
                os._exit(0)

    threading.Thread(target=watchdog, daemon=True).start()
    httpd.serve_forever()


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "serve":
        serve(sys.argv[2])
    else:
        print("usage: dataview_server.py serve <state.json>", file=sys.stderr)
        sys.exit(2)
