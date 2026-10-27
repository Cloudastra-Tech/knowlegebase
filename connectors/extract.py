"""
connectors/extract.py  —  bytes -> plain text, for every connector and for uploads.

    extract(data, mime="", name="")   the text of a .docx, .pptx, .xlsx, .html,
                                      .md, .txt, .csv or .pdf (picked by mime
                                      type, else by the name's extension)
    html_page(data, base_url)         an HTML page's text, title and links (the
                                      website connector follows the links)
    kind_of(mime, name)               which of those it is, or None

Pure Python on purpose: Office files are zip archives of XML, read with the
standard library (zipfile) through defusedxml, which refuses XML entity tricks
(billion laughs, external entities). PDFs use pypdf, already used for indexing.
Every reader is bounded: a zip whose parts unpack to more than MAX_UNPACKED is
refused (a zip bomb), and the text is cut at MAX_CHARS.

HTML loses its boilerplate (scripts, styles, navigation, headers, footers,
asides, forms); if the page marks its <main> or <article>, only that is kept.
"""

import datetime
import io
import posixpath
import re
import zipfile
from html.parser import HTMLParser
from urllib.parse import urldefrag, urljoin

from defusedxml import ElementTree

MAX_UNPACKED = 64 * 1024 * 1024          # bytes of XML read out of one Office file
MAX_CHARS = 2_000_000                    # characters of text kept per document
MAX_SHEET_ROWS = 20_000


class Unsupported(ValueError):
    """Not a type we can read (or not what its type says)."""


MIMES = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "text/html": "html",
    "application/xhtml+xml": "html",
    "text/markdown": "md",
    "text/x-markdown": "md",
    "text/plain": "txt",
    "text/csv": "txt",
}
EXTENSIONS = {".pdf": "pdf", ".docx": "docx", ".pptx": "pptx", ".xlsx": "xlsx",
              ".html": "html", ".htm": "html", ".md": "md", ".markdown": "md",
              ".txt": "txt", ".csv": "txt"}


def kind_of(mime="", name=""):
    """"pdf", "docx", "pptx", "xlsx", "html", "md" or "txt"; None if unsupported."""
    kind = MIMES.get((mime or "").split(";")[0].strip().lower())
    if kind:
        return kind
    ext = posixpath.splitext((name or "").lower())[1]
    return EXTENSIONS.get(ext)


def extract(data, mime="", name=""):
    """The plain text of `data` (bytes, or str for text types). Raises Unsupported."""
    kind = kind_of(mime, name)
    if kind is None:
        raise Unsupported(f"can't read {mime or name or 'this type'}")
    if isinstance(data, str):
        if kind not in ("html", "md", "txt"):
            raise Unsupported(f"{kind} needs bytes")
        text = data
    elif kind in ("html", "md", "txt"):
        text = decode(data)
    else:
        text = {"pdf": _pdf, "docx": _docx, "pptx": _pptx, "xlsx": _xlsx}[kind](data)
    if kind == "html":
        text = html_page(text).text
    return _tidy(text)[:MAX_CHARS]


def decode(data):
    """Bytes -> str: UTF-8 (with or without a BOM), else the detected encoding."""
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        import chardet
        guess = chardet.detect(data[:200_000]).get("encoding") or "latin-1"
        try:
            return data.decode(guess, errors="replace")
        except LookupError:
            return data.decode("latin-1", errors="replace")


def _tidy(text):
    """Trailing spaces off, runs of blank lines down to one."""
    text = "\n".join(line.rstrip() for line in text.replace("\r\n", "\n").split("\n"))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# ---- PDF ---------------------------------------------------------------------------
def _pdf(data):
    from pypdf import PdfReader
    try:
        reader = PdfReader(io.BytesIO(data))
        pages = []
        for page in reader.pages:
            pages.append(page.extract_text() or "")
            if sum(map(len, pages)) > MAX_CHARS:
                break
    except Exception as e:                  # pypdf raises many kinds for a broken file
        raise Unsupported(f"not a readable PDF ({type(e).__name__})") from None
    return "\n\n".join(pages)


# ---- Office (zip of XML) -----------------------------------------------------------
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PR = "{http://schemas.openxmlformats.org/package/2006/relationships}"


class _Zip:
    """An Office file's parts, refusing archives that unpack to too much."""

    def __init__(self, data):
        try:
            self.zip = zipfile.ZipFile(io.BytesIO(data))
        except (zipfile.BadZipFile, ValueError):
            raise Unsupported("not an Office file (not a zip archive)") from None
        self.names = set(self.zip.namelist())
        self.read_bytes = 0

    def xml(self, name):
        if name not in self.names:
            return None
        info = self.zip.getinfo(name)
        self.read_bytes += info.file_size
        if self.read_bytes > MAX_UNPACKED:
            raise Unsupported("the file unpacks to too much data")
        try:
            with self.zip.open(info) as f:
                raw = f.read(info.file_size + 1)
            return ElementTree.fromstring(raw)
        except Exception as e:              # ParseError, defusedxml's refusals, bad zip
            raise Unsupported(f"unreadable part {name} ({type(e).__name__})") from None


def _paragraphs(root, para_tag, text_tag, tab_tag=None, break_tag=None):
    out = []
    for p in root.iter(para_tag):
        parts = []
        for el in p.iter():
            if el.tag == text_tag and el.text:
                parts.append(el.text)
            elif el.tag in (tab_tag, break_tag):
                parts.append("\t" if el.tag == tab_tag else "\n")
        out.append("".join(parts))
    return out


def _docx(data):
    z = _Zip(data)
    root = z.xml("word/document.xml")
    if root is None:
        raise Unsupported("not a Word document")
    body = root.find(f"{W}body")
    lines = []
    for block in (body if body is not None else []):
        if block.tag == f"{W}p":
            style = block.find(f"{W}pPr/{W}pStyle")
            text = "".join(_paragraphs(block, f"{W}p", f"{W}t", f"{W}tab", f"{W}br"))
            level = re.match(r"Heading(\d)", (style.get(f"{W}val") or "") if style is not None
                             else "")
            lines.append(("#" * int(level.group(1)) + " " + text) if level and text else text)
        elif block.tag == f"{W}tbl":
            for row in block.iter(f"{W}tr"):
                cells = [" ".join(_paragraphs(c, f"{W}p", f"{W}t", f"{W}tab")).strip()
                         for c in row.iter(f"{W}tc")]
                lines.append(" | ".join(cells))
            lines.append("")
    for part in sorted(n for n in z.names if re.match(r"word/(footnotes|endnotes)\.xml$", n)):
        lines += _paragraphs(z.xml(part), f"{W}p", f"{W}t")
    return "\n".join(lines)


def _rels(z, part):
    """{relationship id: target path} of an Office part."""
    folder, name = posixpath.split(part)
    root = z.xml(posixpath.join(folder, "_rels", name + ".rels"))
    if root is None:
        return {}
    return {r.get("Id"): posixpath.normpath(posixpath.join(folder, r.get("Target", "")))
            for r in root.iter(f"{PR}Relationship")}


def _pptx(data):
    z = _Zip(data)
    pres = z.xml("ppt/presentation.xml")
    if pres is None:
        raise Unsupported("not a PowerPoint file")
    rels = _rels(z, "ppt/presentation.xml")
    slides = [rels.get(s.get(f"{R}id")) for s in pres.iter(
        "{http://schemas.openxmlformats.org/presentationml/2006/main}sldId")]
    out = []
    for n, slide in enumerate([s for s in slides if s], 1):
        root = z.xml(slide)
        if root is None:
            continue
        text = [t for t in _paragraphs(root, f"{A}p", f"{A}t", None, f"{A}br") if t.strip()]
        notes = next((t for t in _rels(z, slide).values() if "notesSlide" in t), None)
        if notes and (nroot := z.xml(notes)) is not None:
            spoken = [t for t in _paragraphs(nroot, f"{A}p", f"{A}t") if t.strip()
                      and not t.strip().isdigit()]
            if spoken:
                text += ["Notes: " + " ".join(spoken)]
        out.append(f"## Slide {n}\n" + "\n".join(text))
    return "\n\n".join(out)


# Built-in number formats that are dates (ECMA-376 18.8.30).
_DATE_FORMATS = set(range(14, 23)) | {45, 46, 47}


def _xlsx(data):
    z = _Zip(data)
    book = z.xml("xl/workbook.xml")
    if book is None:
        raise Unsupported("not an Excel file")
    shared = []
    root = z.xml("xl/sharedStrings.xml")
    if root is not None:
        for si in root.iter(f"{S}si"):
            shared.append("".join(t.text or "" for t in si.iter(f"{S}t")))
    date_styles = set()
    styles = z.xml("xl/styles.xml")
    if styles is not None:
        custom = {int(f.get("numFmtId")) for f in styles.iter(f"{S}numFmt")
                  if re.search(r"[dmy]", re.sub(r'"[^"]*"|\[[^\]]*\]', "", f.get("formatCode", "")),
                               re.I)}
        xfs = styles.find(f"{S}cellXfs")
        for n, xf in enumerate(xfs if xfs is not None else []):
            fmt = int(xf.get("numFmtId", 0))
            if fmt in _DATE_FORMATS or fmt in custom:
                date_styles.add(n)
    rels = _rels(z, "xl/workbook.xml")
    out = []
    for sheet in book.iter(f"{S}sheet"):
        part = rels.get(sheet.get(f"{R}id"))
        root = z.xml(part) if part else None
        if root is None:
            continue
        rows = []
        for row in root.iter(f"{S}row"):
            cells = [_cell(c, shared, date_styles) for c in row.iter(f"{S}c")]
            if any(cells):
                rows.append(" | ".join(cells))
            if len(rows) >= MAX_SHEET_ROWS:
                break
        out.append(f"## {sheet.get('name', 'Sheet')}\n" + "\n".join(rows))
    return "\n\n".join(out)


def _cell(c, shared, date_styles):
    kind = c.get("t")
    if kind == "inlineStr":
        return "".join(t.text or "" for t in c.iter(f"{S}t"))
    v = c.find(f"{S}v")
    value = v.text if v is not None and v.text is not None else ""
    if kind == "s":
        try:
            return shared[int(value)]
        except (ValueError, IndexError):
            return ""
    if kind == "b":
        return "TRUE" if value == "1" else "FALSE"
    if kind in (None, "n") and value and int(c.get("s", 0) or 0) in date_styles:
        try:
            day = datetime.datetime(1899, 12, 30) + datetime.timedelta(days=float(value))
            return day.strftime("%Y-%m-%d" if day.time() == datetime.time() else "%Y-%m-%d %H:%M")
        except (ValueError, OverflowError):
            return value
    if kind in (None, "n") and value.endswith(".0"):
        return value[:-2]
    return value


# ---- HTML --------------------------------------------------------------------------
class HtmlPage:
    def __init__(self, text="", title="", links=(), noindex=False, nofollow=False):
        self.text, self.title, self.links = text, title, list(links)
        self.noindex, self.nofollow = noindex, nofollow


_SKIP = {"script", "style", "noscript", "template", "svg", "nav", "footer", "header",
         "aside", "form", "iframe", "button", "select", "canvas", "head"}
_BLOCK = {"p", "div", "section", "article", "main", "br", "li", "table", "ul", "ol",
          "h1", "h2", "h3", "h4", "h5", "h6", "pre", "blockquote", "dd", "dt", "hr",
          "figcaption"}
_VOID = {"br", "hr", "img", "input", "meta", "link", "area", "base", "col", "embed",
         "source", "track", "wbr"}


class _Html(HTMLParser):
    def __init__(self, base_url):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.skip = 0                       # depth inside boilerplate
        self.main = 0                       # depth inside <main> / <article>
        self.saw_main = False
        self.all, self.in_main = [], []
        self.title, self.in_title = "", False
        self.links, self.noindex, self.nofollow = [], False, False

    def _out(self, text):
        self.all.append(text)
        if self.main:
            self.in_main.append(text)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "title":
            self.in_title = True
        elif tag == "meta" and (a.get("name") or "").lower() in ("robots", "kbbot"):
            content = (a.get("content") or "").lower()
            self.noindex |= "noindex" in content or "none" in content
            self.nofollow |= "nofollow" in content or "none" in content
        elif tag == "a" and a.get("href") and "nofollow" not in (a.get("rel") or "").lower():
            href = urldefrag(urljoin(self.base_url or "", a["href"].strip()))[0]
            self.links.append(href)
        if tag in _VOID:
            if tag in ("br", "hr") and not self.skip:
                self._out("\n")
            return
        if tag in _SKIP:
            self.skip += 1
        elif tag in ("main", "article"):
            self.main += 1
            self.saw_main = True
        if self.skip:
            return
        if tag in _BLOCK:
            self._out("\n")
        elif tag == "tr":
            # One line per row and no blank line between rows: chunking.py reads
            # consecutive "a | b" lines as a table (cut between rows, header repeated).
            self._out("\n")
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._out("#" * int(tag[1]) + " ")
        elif tag == "li":
            self._out("- ")
        elif tag in ("td", "th"):
            self._out(" | ")

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        if tag in _VOID:
            return
        if tag in _SKIP:
            self.skip = max(0, self.skip - 1)
        elif tag in ("main", "article"):
            self.main = max(0, self.main - 1)
        elif tag in _BLOCK and not self.skip:
            self._out("\n")

    def handle_data(self, data):
        if self.in_title:
            self.title += data
        elif not self.skip:
            self._out(data)


def html_page(data, base_url=None):
    """An HTML page as HtmlPage: text (boilerplate removed), title, absolute links,
    and whether its robots meta says noindex / nofollow."""
    text = decode(data) if isinstance(data, bytes) else str(data or "")
    parser = _Html(base_url)
    try:
        parser.feed(text)
        parser.close()
    except Exception:                       # HTMLParser is lenient; stay so
        pass
    body = "".join(parser.in_main if parser.saw_main and "".join(parser.in_main).strip()
                   else parser.all)
    lines = [re.sub(r"[ \t ]+", " ", line).strip() for line in body.split("\n")]
    lines = [re.sub(r"^\|\s*", "", line) for line in lines]
    return HtmlPage(_tidy("\n".join(lines)), re.sub(r"\s+", " ", parser.title).strip(),
                    dict.fromkeys(parser.links), parser.noindex, parser.nofollow)
