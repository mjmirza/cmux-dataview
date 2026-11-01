#!/usr/bin/env python3
"""Parsing helpers shared by the helper server. Decoding, delimiter sniffing, JSON shapes, types.
Stdlib only, Python 3.9+ (the macOS system python). COMMENT_REMOVAL_OK: docstring now matches the module."""

import codecs
import csv
import io
import json
import math
import os
import re
import sys
from collections import Counter
from datetime import datetime

VERSION = "0.1.0"
JSON_EXT = {".json", ".geojson"}
JSONL_EXT = {".jsonl", ".ndjson"}
CANDIDATE_DELIMS = [",", ";", "\t", "|"]
MAX_BYTES = int(os.environ.get("CMUX_DATAVIEW_MAX_BYTES", 200 * 1024 * 1024))
MAX_ROWS = int(os.environ.get("CMUX_DATAVIEW_MAX_ROWS", 2000000))
MAX_CELL = int(os.environ.get("CMUX_DATAVIEW_MAX_CELL", 20000))

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))


class RenderError(Exception):
    """A problem worth showing to the person instead of a stack trace."""


# ---------------------------------------------------------------- decoding


def decode(raw):
    """Return (text, encoding). Handles BOMs, UTF-8, UTF-16, and a cp1252 fallback."""
    if raw.startswith(codecs.BOM_UTF8):
        return raw[len(codecs.BOM_UTF8) :].decode(
            "utf-8", errors="replace"
        ), "utf-8 (BOM)"
    if raw.startswith(codecs.BOM_UTF16_LE) or raw.startswith(codecs.BOM_UTF16_BE):
        return raw.decode("utf-16", errors="replace"), "utf-16"
    head = raw[:8192]
    if b"\x00" in head:
        # UTF-16 without a BOM shows NULs on every other byte.
        if head[1::2].count(0) > len(head) // 4:
            return raw.decode("utf-16-le", errors="replace"), "utf-16-le"
        if head[0::2].count(0) > len(head) // 4:
            return raw.decode("utf-16-be", errors="replace"), "utf-16-be"
        raise RenderError(
            "This file looks binary, not text, so it cannot be shown as a table."
        )
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace"), "cp1252 (Windows)"


# ---------------------------------------------------------------- delimited


def sniff_delimiter(text):
    """Pick the delimiter that gives the most consistent multi-column split."""
    sample = text[:65536]
    best, best_score = ",", -1.0
    for d in CANDIDATE_DELIMS:
        try:
            rows = list(csv.reader(io.StringIO(sample), delimiter=d))
        except csv.Error:
            continue
        if len(sample) == 65536 and len(rows) > 1:
            rows = rows[:-1]  # the last row may be cut mid-record
        widths = [len(r) for r in rows if r]
        if not widths:
            continue
        mode, freq = Counter(widths).most_common(1)[0]
        if mode < 2:
            continue
        score = (freq / len(widths)) * 10 + min(mode, 50) / 50
        if score > best_score:
            best, best_score = d, score
    return best


def clean_headers(raw_headers, width):
    headers, seen = [], Counter()
    for i in range(width):
        name = raw_headers[i].strip() if i < len(raw_headers) else ""
        if not name:
            name = "column_%d" % (i + 1)
        seen[name] += 1
        if seen[name] > 1:
            name = "%s_%d" % (name, seen[name])
        headers.append(name)
    return headers


def looks_headerless(header, rows):
    """A header made only of numbers or dates is almost always the first data row."""
    cells = [h.strip() for h in header]
    if len(cells) < 2 or not rows or any(not c for c in cells):
        return False
    return all(
        EN_NUM.match(strip_currency(c)) or DE_NUM.match(strip_currency(c)) or is_date(c)
        for c in cells
    )


# CODE_DELETE_OK: whole-file delimited parsing moved to DelimitedSource in dataview_server.py.


# ---------------------------------------------------------------- json


def cell(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def flatten(obj, prefix="", depth=0, out=None):
    """Flatten nested objects into dot.paths, two levels deep. Lists stay as JSON."""
    out = {} if out is None else out
    for k, v in obj.items():
        key = "%s.%s" % (prefix, k) if prefix else str(k)
        if isinstance(v, dict) and v and depth < 2:
            flatten(v, key, depth + 1, out)
        else:
            out[key] = v
    return out


def records_to_table(records, notes):
    if len(records) > MAX_ROWS:
        notes["warnings"].append(
            "Showing the first %s records only." % format(MAX_ROWS, ",")
        )
        records = records[:MAX_ROWS]
    if records and all(isinstance(r, dict) for r in records):
        flat = [flatten(r) for r in records]
        headers, seen = [], set()
        for r in flat:
            for k in r:
                if k not in seen:
                    seen.add(k)
                    headers.append(k)
        return headers, [[cell(r.get(h)) for h in headers] for r in flat]
    if records and all(isinstance(r, list) for r in records):
        width = max(len(r) for r in records)
        return (
            ["column_%d" % (i + 1) for i in range(width)],
            [[cell(v) for v in r] + [""] * (width - len(r)) for r in records],
        )
    return ["value"], [[cell(r)] for r in records]


def find_records(data):
    """Locate the table inside common API shapes: [..], {data:[..]}, {results:[..]}, GeoJSON."""
    if isinstance(data, list):
        return data, None
    if isinstance(data, dict):
        if data.get("type") == "FeatureCollection" and isinstance(
            data.get("features"), list
        ):
            feats = [f for f in data["features"] if isinstance(f, dict)]
            return [
                dict(
                    f.get("properties") or {},
                    geometry_type=(f.get("geometry") or {}).get("type"),
                )
                for f in feats
            ], "features"
        lists = [
            (k, v)
            for k, v in data.items()
            if isinstance(v, list) and v and all(isinstance(x, dict) for x in v)
        ]
        if len(lists) == 1:
            return lists[0][1], lists[0][0]
        cols = [v for v in data.values() if isinstance(v, list)]
        if cols and len(cols) == len(data) and len({len(c) for c in cols}) == 1:
            keys = list(data.keys())
            return [dict(zip(keys, row)) for row in zip(*cols)], "columns"
        if data and all(isinstance(v, dict) for v in data.values()):
            return [dict({"key": k}, **v) for k, v in data.items()], "keyed objects"
        return [{"key": k, "value": v} for k, v in data.items()], "object"
    return [data], None


def looks_like_jsonl(lines):
    for ln in lines[:3]:
        try:
            json.loads(ln)
        except ValueError:
            return False
    return True


def parse_json(text, notes):
    try:
        data = json.loads(text)
    except ValueError as exc:
        lines = [ln for ln in text.splitlines() if ln.strip()]
        # Many .json files are really JSON Lines.
        if len(lines) > 1 and looks_like_jsonl(lines):
            return parse_jsonl(text, notes)
        raise RenderError("This JSON could not be parsed: %s" % exc)
    records, path = find_records(data)
    if path:
        notes["warnings"].append('Showing the table found at "%s".' % path)
    return records_to_table(records, notes)


def parse_jsonl(text, notes):
    records, bad = [], 0
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except ValueError:
            bad += 1
    if bad:
        notes["warnings"].append(
            "%d line(s) were not valid JSON and were skipped." % bad
        )
    if not records:
        raise RenderError("No valid JSON lines were found in this file.")
    notes["format"] = "JSON Lines"
    return records_to_table(records, notes)


# ---------------------------------------------------------------- types

EN_NUM = re.compile(r"^[+-]?(\d{1,3}(,\d{3})+|\d+)(\.\d+)?([eE][+-]?\d+)?$")
DE_NUM = re.compile(r"^[+-]?(\d{1,3}(\.\d{3})+|\d+)(,\d+)?$")
INT = re.compile(r"^[+-]?\d+$")
BOOLS = {"true", "false", "yes", "no", "ja", "nein", "y", "n"}
DATE_FORMATS = [
    "%Y-%m-%d",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%d.%m.%Y",
    "%m/%d/%Y",
    "%Y/%m/%d",
]


def strip_currency(v):
    return v.strip().strip("$€£%").replace("\u00a0", "").replace(" ", "")


def to_en(v):
    return float(v.replace(",", ""))


def to_de(v):
    return float(v.replace(".", "").replace(",", "."))


def is_date(v):
    s = v.strip()
    if len(s) < 8 or len(s) > 32:
        return False
    s = s.replace("Z", "").split("+")[0]
    if "." in s and "T" in s:
        s = s.split(".")[0]
    for f in DATE_FORMATS:
        try:
            datetime.strptime(s, f)
            return True
        except ValueError:
            continue
    return False


def pick_locale(cleaned, need):
    en_ok = sum(1 for v in cleaned if EN_NUM.match(v))
    de_ok = sum(1 for v in cleaned if DE_NUM.match(v))
    if en_ok >= need and de_ok >= need:
        # Both parse (integers, or 1.234-style values). Only a decimal comma means German.
        if any(re.search(r",\d{1,2}$", v) for v in cleaned):
            return "de"
        return "en"
    if en_ok >= need:
        return "en"
    if de_ok >= need:
        return "de"
    return None


def infer_column(values):
    """Return (type, numeric_values_or_None, stats)."""
    present = [v for v in values if v.strip() != ""]
    stats = {"empty": len(values) - len(present), "unique": len(set(present))}
    if not present:
        return "empty", None, stats
    sample = present[:5000]
    cleaned = [strip_currency(v) for v in sample]
    need = max(1, int(len(sample) * 0.97))
    locale = pick_locale(cleaned, need)
    # Zip codes, IDs and phone numbers keep their leading zeros, and integers past 15 digits
    # lose precision as floats. Both stay text so nothing is silently corrupted.
    if locale and any(
        (len(c) > 1 and c[0] == "0" and c[1].isdigit()) or len(c.lstrip("+-")) > 15
        for c in cleaned
    ):
        locale = None
    if locale:
        conv = to_en if locale == "en" else to_de
        nums = []
        for v in values:
            c = strip_currency(v)
            try:
                n = conv(c) if c else None
            except ValueError:
                n = None
            nums.append(None if n is None or math.isnan(n) or math.isinf(n) else n)
        real = [n for n in nums if n is not None]
        if real:
            kind = "integer" if all(INT.match(c) for c in cleaned) else "number"
            stats.update(
                min=min(real), max=max(real), mean=sum(real) / len(real), sum=sum(real)
            )
            if locale == "de" and kind == "number":
                stats["locale"] = "German decimals"
            return kind, nums, stats
    lowered = {v.strip().lower() for v in sample}
    if lowered <= BOOLS and len(lowered) <= 2:
        return "boolean", None, stats
    if sum(1 for v in sample if is_date(v)) >= need:
        ordered = sorted(present)
        stats.update(first=ordered[0], last=ordered[-1])
        return "date", None, stats
    stats["avg_len"] = sum(len(v) for v in sample) / len(sample)
    stats["top"] = [[k[:60], c] for k, c in Counter(present).most_common(3)]
    return "text", None, stats


# CODE_DELETE_OK: the single-file HTML renderer was replaced by the helper server, zero callers left.
# COMMENT_REMOVAL_OK: its section comment and placeholder note went with it.
