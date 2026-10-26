"""
Recorded HTTP for connector tests: every call a connector makes goes through
connectors/http.py, whose `send` is swapped for Recorded.send. It answers from
JSON fixtures in tests/fixtures/connectors/<provider>/<name>.json (and from
exchanges a test adds), and fails the test on any request it has no answer for,
so no test ever reaches the network. Sleeping is replaced by a fake clock that
just moves forward, so retries and rate buckets cost no time and can be measured.

A fixture file:
    {"hosts": {"docs.example.com": "93.184.216.34"},     # DNS for url_guard (optional)
     "exchanges": [
        {"request": {"method": "GET", "url": "https://.../files",
                     "params": {"pageToken": "t1"},       # these must be among the query
                     "json": {"filter": {...}},           # these keys must be in the body
                     "headers": {"If-None-Match": "\\"v1\\""}},
         "response": {"status": 200, "headers": {...},
                      "json": {...} | "text": "..." | "body_b64": "..."},
         "repeat": true}                                   # answer more than once
     ]}
Exchanges are matched in order; each answers once unless it repeats.
"""

import base64
import copy
import io
import json
import pathlib
import socket
from urllib.parse import parse_qsl, urlsplit, urlunsplit

import requests
from requests.structures import CaseInsensitiveDict

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "connectors"
PUBLIC_IP = "93.184.216.34"


def response(status=200, json_body=None, text=None, body=None, headers=None, url=""):
    r = requests.Response()
    r.status_code = status
    r.headers = CaseInsensitiveDict(headers or {})
    if json_body is not None:
        body = json.dumps(json_body).encode()
        r.headers.setdefault("Content-Type", "application/json")
    elif text is not None:
        body = text.encode()
    body = body or b""
    r._content = body
    r._content_consumed = True
    r.raw = io.BytesIO(body)
    r.encoding = "utf-8"
    r.url = url
    return r


def _subset(want, got):
    """Every key of `want` is in `got` with that value; a key ending in "~" only
    needs its value to be a substring ({"q~": "'f1' in parents"})."""
    if isinstance(want, dict):
        if not isinstance(got, dict):
            return False
        for k, v in want.items():
            if k.endswith("~"):
                if str(v) not in str(got.get(k[:-1], "")):
                    return False
            elif k not in got or not _subset(v, got[k]):
                return False
        return True
    return want == got


class Recorded:
    def __init__(self, monkeypatch, *fixtures):
        from connectors import http
        import url_guard
        self.exchanges, self.calls, self.hosts = [], [], {}
        self.now = 1000.0
        self.slept = []
        for name in fixtures:
            self.load(name)
        monkeypatch.setattr(http, "send", self.send)
        monkeypatch.setattr(http, "sleep", self.sleep)
        monkeypatch.setattr(http, "clock", lambda: self.now)
        monkeypatch.setattr(http, "_buckets", {})
        monkeypatch.setattr(url_guard, "_resolve", self.resolve)

    def load(self, name):
        data = json.loads((FIXTURES / f"{name}.json").read_text())
        self.hosts.update(data.get("hosts") or {})
        for ex in data["exchanges"]:
            self.exchanges.append(dict(copy.deepcopy(ex), used=False))
        return self

    def add(self, method, url, status=200, json_body=None, text=None, body=None, headers=None,
            params=None, match_json=None, req_headers=None, repeat=False):
        self.exchanges.append({"request": {"method": method, "url": url, "params": params,
                                           "json": match_json, "headers": req_headers},
                               "response": {"status": status, "json": json_body, "text": text,
                                            "body": body, "headers": headers},
                               "repeat": repeat, "used": False})
        return self

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += max(0.0, seconds)

    def resolve(self, host, port):
        ip = self.hosts.get(host, PUBLIC_IP)
        family = socket.AF_INET6 if ":" in ip else socket.AF_INET
        return [(family, socket.SOCK_STREAM, 6, "", (ip, port))]

    def send(self, method, url, params=None, json=None, data=None, headers=None, **kwargs):
        parts = urlsplit(url)
        bare = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
        query = dict(parse_qsl(parts.query))
        query.update({k: str(v) for k, v in (params or {}).items()})
        self.calls.append({"method": method, "url": bare, "params": query, "json": json,
                           "data": data, "headers": dict(headers or {})})
        for ex in self.exchanges:
            want = ex["request"]
            if ex["used"] and not ex.get("repeat"):
                continue
            if want["method"] != method or want["url"] != bare:
                continue
            if want.get("params") and not _subset({k: str(v) for k, v in want["params"].items()},
                                                  query):
                continue
            if want.get("json") and not _subset(want["json"], json):
                continue
            if want.get("headers") and not _subset(want["headers"], dict(headers or {})):
                continue
            ex["used"] = True
            res = ex["response"]
            body = res.get("body")
            if res.get("body_b64"):
                body = base64.b64decode(res["body_b64"])
            return response(res.get("status", 200), res.get("json"), res.get("text"), body,
                            res.get("headers"), url)
        raise AssertionError(f"no recorded answer for {method} {url} params={query} json={json}")

    def unused(self):
        return [ex["request"] for ex in self.exchanges if not ex["used"] and not ex.get("repeat")]

    def requests_to(self, fragment):
        return [c for c in self.calls if fragment in c["url"]]


# ---- Small generated Office files (for extract.py and connectors) --------------------
def _zip(files):
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in files.items():
            z.writestr(name, text)
    return buf.getvalue()


W_NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def make_docx(paragraphs, heading=None, table=None):
    body = ""
    if heading:
        body += (f'<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr>'
                 f'<w:r><w:t>{heading}</w:t></w:r></w:p>')
    body += "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    if table:
        rows = "".join("<w:tr>" + "".join(f"<w:tc><w:p><w:r><w:t>{c}</w:t></w:r></w:p></w:tc>"
                                          for c in row) + "</w:tr>" for row in table)
        body += f"<w:tbl>{rows}</w:tbl>"
    return _zip({"[Content_Types].xml": "<Types/>",
                 "word/document.xml": f"<w:document {W_NS}><w:body>{body}</w:body></w:document>"})


def make_pptx(slides, notes=None):
    p_ns = 'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
    a_ns = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
    r_ns = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    rel_ns = 'xmlns="http://schemas.openxmlformats.org/package/2006/relationships"'
    files = {"[Content_Types].xml": "<Types/>"}
    ids = "".join(f'<p:sldId id="{256 + n}" r:id="rId{n}"/>' for n in range(1, len(slides) + 1))
    files["ppt/presentation.xml"] = (f"<p:presentation {p_ns} {r_ns}><p:sldIdLst>{ids}"
                                     "</p:sldIdLst></p:presentation>")
    rels = "".join(f'<Relationship Id="rId{n}" Target="slides/slide{n}.xml"/>'
                   for n in range(1, len(slides) + 1))
    files["ppt/_rels/presentation.xml.rels"] = f"<Relationships {rel_ns}>{rels}</Relationships>"
    for n, lines in enumerate(slides, 1):
        paras = "".join(f"<a:p><a:r><a:t>{t}</a:t></a:r></a:p>" for t in lines)
        files[f"ppt/slides/slide{n}.xml"] = (f"<p:sld {p_ns} {a_ns}><p:cSld><p:spTree><p:sp>"
                                             f"<p:txBody>{paras}</p:txBody></p:sp></p:spTree>"
                                             "</p:cSld></p:sld>")
        if notes and n in notes:
            files[f"ppt/slides/_rels/slide{n}.xml.rels"] = (
                f'<Relationships {rel_ns}><Relationship Id="rId9" '
                f'Target="../notesSlides/notesSlide{n}.xml"/></Relationships>')
            files[f"ppt/notesSlides/notesSlide{n}.xml"] = (
                f"<p:notes {p_ns} {a_ns}><a:p><a:r><a:t>{notes[n]}</a:t></a:r></a:p></p:notes>")
    return _zip(files)


def make_xlsx(sheets):
    """sheets: {name: [[cell, ...], ...]}; str cells are shared strings, numbers stay
    numbers, ("date", serial) cells get a date style."""
    s_ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    r_ns = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    rel_ns = 'xmlns="http://schemas.openxmlformats.org/package/2006/relationships"'
    shared, files = [], {"[Content_Types].xml": "<Types/>"}
    sheet_tags, rels = "", ""
    for n, (name, rows) in enumerate(sheets.items(), 1):
        sheet_tags += f'<sheet name="{name}" sheetId="{n}" r:id="rId{n}"/>'
        rels += f'<Relationship Id="rId{n}" Target="worksheets/sheet{n}.xml"/>'
        xml_rows = ""
        for r, row in enumerate(rows, 1):
            cells = ""
            for value in row:
                if isinstance(value, tuple):
                    cells += f'<c s="1"><v>{value[1]}</v></c>'
                elif isinstance(value, str):
                    shared.append(value)
                    cells += f'<c t="s"><v>{len(shared) - 1}</v></c>'
                else:
                    cells += f"<c><v>{value}</v></c>"
            xml_rows += f'<row r="{r}">{cells}</row>'
        files[f"xl/worksheets/sheet{n}.xml"] = (f"<worksheet {s_ns}><sheetData>{xml_rows}"
                                                "</sheetData></worksheet>")
    files["xl/workbook.xml"] = f"<workbook {s_ns} {r_ns}><sheets>{sheet_tags}</sheets></workbook>"
    files["xl/_rels/workbook.xml.rels"] = f"<Relationships {rel_ns}>{rels}</Relationships>"
    files["xl/sharedStrings.xml"] = (f"<sst {s_ns}>" + "".join(f"<si><t>{s}</t></si>"
                                                             for s in shared) + "</sst>")
    files["xl/styles.xml"] = (f'<styleSheet {s_ns}><cellXfs><xf numFmtId="0"/>'
                              '<xf numFmtId="14"/></cellXfs></styleSheet>')
    return _zip(files)


def make_pdf(text):
    """A one-page PDF whose text layer is `text` (ASCII), built by hand."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
               b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
               b"/Resources << /Font << /F1 5 0 R >> >> >>",
               b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = b"%PDF-1.4\n", []
    for n, obj in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % n + obj + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1, xref)
    return out
