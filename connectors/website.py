"""
connectors/website.py  —  crawl a public website (docs, help centre) into the index.

An admin gives one or more start addresses: a page, or a sitemap (an address
ending in .xml: a <urlset>, or a <sitemapindex> of them). From a page the
crawler follows links on the SAME SITE only (scheme, host and port of that
start address), up to max_depth links away and max_pages pages in all; from a
sitemap it takes the pages listed there (same site), without following links.

Every run crawls again, politely and cheaply:
  - robots.txt is obeyed (RFC 9309): disallowed pages aren't fetched, a
    Crawl-delay slows us down further; a robots.txt that can't be reached
    because of a server error stops the run (nothing is deleted);
  - at most RATE requests a second per site;
  - conditional GETs: a page's ETag / Last-Modified is kept, and a 304 Not
    Modified costs no download and no re-indexing (its links are remembered);
  - a page that answers 404 / 410, or isn't reached any more, is deleted when
    the crawl completes; a crawl that stopped at max_pages deletes nothing.
  - pages that say <meta name="robots" content="noindex"> are not kept;
    "nofollow" stops link following from them.

SSRF: start addresses must be https and resolve to public IP addresses only,
and that's checked again before EVERY request, redirects included (url_guard;
CONNECTOR_ALLOW_PRIVATE_HOSTS lists host:port pairs allowed for a local test
site, which may then use http). Redirects are followed by hand, at most
MAX_REDIRECTS, and only within the same site.

Text comes from connectors/extract.py (HTML with navigation, headers, footers
and scripts removed). Every page is workspace-wide at the source: who may ask
is the connection's access level.
"""

import hashlib
import json
import logging
import re
import xml.etree.ElementTree as StdElementTree
from urllib import robotparser
from urllib.parse import urldefrag, urljoin, urlsplit, urlunsplit

from defusedxml import ElementTree

import integrations_store as store
from connectors import extract, http
from connectors.base import (Connector, ConnectorError, Cursor, FormError, Item, LimitReached,
                             Transient, Upsert)

log = logging.getLogger("uvicorn.error")

PROVIDER = "website"
USER_AGENT = "KnowledgeAssistantBot/1.0 (+https://github.com/Cloudastra-Tech/knowlegebase)"
RATE = 1.0                     # requests a second per site
MAX_REDIRECTS = 5
MAX_BYTES = 5 * 1024 * 1024    # a page bigger than this is skipped
DEFAULT_PAGES, MAX_PAGES = 200, 2000
DEFAULT_DEPTH, MAX_DEPTH = 3, 10
MAX_SITEMAPS = 50
MAX_START_URLS = 20
_SKIP_EXT = re.compile(r"\.(png|jpe?g|gif|svg|webp|ico|css|js|mjs|json|zip|gz|tgz|rar|7z|mp3|"
                       r"mp4|mov|avi|webm|woff2?|ttf|eot|exe|dmg|pkg|deb|rpm|iso|pdf|docx?|"
                       r"xlsx?|pptx?)$", re.I)


def canonical(url):
    """A URL without its fragment, with the scheme and host in lower case and
    the default port dropped: the same page is always the same item."""
    url = urldefrag(url.strip())[0]
    p = urlsplit(url)
    host = (p.hostname or "").lower()
    port = p.port
    netloc = host if port in (None, 80 if p.scheme == "http" else 443) else f"{host}:{port}"
    return urlunsplit((p.scheme.lower(), netloc, p.path or "/", p.query, ""))


def site_of(url):
    p = urlsplit(url)
    return (p.scheme.lower(), (p.hostname or "").lower(),
            p.port or (443 if p.scheme.lower() == "https" else 80))


def _version(response, body):
    etag, modified = response.headers.get("ETag"), response.headers.get("Last-Modified")
    if etag or modified:
        return json.dumps({"etag": etag, "last_modified": modified}, sort_keys=True)
    return json.dumps({"sha256": hashlib.sha256(body).hexdigest()})


def _limits(settings):
    def clamp(value, default, top):
        try:
            return max(1, min(int(value), top))
        except (TypeError, ValueError):
            return default
    return (clamp(settings.get("max_pages"), DEFAULT_PAGES, MAX_PAGES),
            clamp(settings.get("max_depth"), DEFAULT_DEPTH, MAX_DEPTH))


def check_start_url(url):
    """A start address as typed -> its canonical form, or FormError."""
    url = (url or "").strip()
    if not url:
        raise FormError("Enter the address of a page or a sitemap.")
    if "://" not in url:
        url = "https://" + url
    url = canonical(url)
    try:
        http.guard_public(url)
    except ConnectorError as e:
        raise FormError(str(e)) from None
    return url


class _Crawl:
    """One run's fetching: robots.txt per site, the politeness bucket, redirects."""

    def __init__(self):
        self.robots = {}

    def _get(self, url, headers=None, ok=()):
        scheme, host, port = site_of(url)
        return http.request("GET", url, guard=http.guard_public, ok=ok,
                            bucket=http.bucket_key(PROVIDER, scheme, host, port),
                            rate=self.rate(url), burst=1, stream=True, timeout=20,
                            headers={"User-Agent": USER_AGENT, **(headers or {})})

    def rate(self, url):
        rp = self.robots.get(site_of(url))
        delay = rp.crawl_delay(USER_AGENT) if rp else None
        return min(RATE, 1 / float(delay)) if delay else RATE

    def allowed(self, url):
        site = site_of(url)
        if site not in self.robots:
            rp = robotparser.RobotFileParser()
            scheme, host, port = site
            robots_url = canonical(f"{scheme}://{host}:{port}/robots.txt")
            self.robots[site] = None
            try:
                r = self._get(robots_url, ok=(400, 401, 403, 404, 410, 429))
                if r.status_code >= 500:
                    raise Transient("robots.txt unreachable")
                body = self._read(r) if r.status_code == 200 else b""
                rp.parse(extract.decode(body or b"").splitlines())
            except http.NotFound:
                rp.parse([])
            self.robots[site] = rp
        rp = self.robots[site]
        return rp is None or rp.can_fetch(USER_AGENT, url)

    @staticmethod
    def _read(r):
        if int(r.headers.get("Content-Length") or 0) > MAX_BYTES:
            return None
        body = b""
        for chunk in r.iter_content(64 * 1024):
            body += chunk
            if len(body) > MAX_BYTES:
                return None
        return body

    @staticmethod
    def _conditional(version):
        """If-None-Match / If-Modified-Since for a page's stored version."""
        try:
            v = json.loads(version) if version else {}
        except ValueError:
            v = {}
        headers = {}
        if v.get("etag"):
            headers["If-None-Match"] = v["etag"]
        if v.get("last_modified"):
            headers["If-Modified-Since"] = v["last_modified"]
        return headers

    def fetch(self, url, version_of=lambda url: None):
        """(final url, response, body or None); body None also for a 304. Follows
        redirects within the site (each hop asks conditionally for what we have
        of that address); raises NotFound for one leaving it."""
        site = site_of(url)
        for _ in range(MAX_REDIRECTS + 1):
            r = self._get(url, self._conditional(version_of(url)), ok=(304,))
            if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("Location"):
                url = canonical(urljoin(url, r.headers["Location"]))
                if site_of(url) != site:
                    raise http.NotFound("redirected to another site")
                if not self.allowed(url):
                    raise http.NotFound("redirected to a page robots.txt disallows")
                continue
            if r.status_code == 304:
                return url, r, None
            return url, r, self._read(r)
        raise http.NotFound("too many redirects")


def _sitemap_urls(crawl, url, depth=0, budget=None):
    """[(page url, lastmod)] listed by a sitemap (following a sitemap index)."""
    budget = budget if budget is not None else [MAX_SITEMAPS]
    if budget[0] <= 0 or depth > 3:
        return []
    budget[0] -= 1
    _, _, body = crawl.fetch(url)
    try:
        root = ElementTree.fromstring(body or b"")
    except (StdElementTree.ParseError, ValueError) as e:
        raise ConnectorError(f"sitemap isn't XML ({type(e).__name__})") from None
    tag = root.tag.rsplit("}", 1)[-1]
    out = []
    for entry in root:
        loc = next((c.text for c in entry if c.tag.endswith("loc")), None)
        lastmod = next((c.text for c in entry if c.tag.endswith("lastmod")), None)
        if not loc:
            continue
        loc = canonical(urljoin(url, loc.strip()))
        if site_of(loc) != site_of(url):
            continue
        if tag == "sitemapindex":
            out += _sitemap_urls(crawl, loc, depth + 1, budget)
        else:
            out.append((loc, lastmod))
    return out


def _is_sitemap(url):
    return urlsplit(url).path.lower().endswith(".xml")


class Website(Connector):
    PROVIDER = PROVIDER
    LABEL = "Website"
    DESCRIPTION = "Public pages from a site or its sitemap"
    AUTH = "url"
    PICKER = "urls"
    PICKER_REQUIRED = True
    SETTINGS_KEY = "urls"
    UNIT = "pages"

    def connect_form(self, workspace, form, user):
        url = check_start_url(form.get("url"))
        max_pages, max_depth = _limits(form)
        row = store.get(workspace, PROVIDER)
        settings = dict((row or {}).get("settings") or {})
        urls = [u for u in settings.get("urls") or [] if u.get("id") != url]
        settings.update(urls=[{"id": url, "name": url, "kind": "url"}] + urls,
                        max_pages=max_pages, max_depth=max_depth)
        store.connect(workspace, PROVIDER, urlsplit(url).hostname, {}, user)
        store.update(workspace, PROVIDER, settings=settings)
        return urlsplit(url).hostname

    def check_containers(self, picked):
        """The URL list an admin saved: each one checked like a start address."""
        out = []
        for entry in picked[:MAX_START_URLS]:
            url = check_start_url(entry.get("id") or entry.get("name"))
            if url not in {o["id"] for o in out}:
                out.append({"id": url, "name": url, "kind": "url"})
        return out

    def list_containers(self, ctx, parent=None):
        return list((ctx.settings or {}).get("urls") or [])

    def changes(self, ctx, cursor):
        settings = ctx.settings or {}
        starts = [u["id"] for u in settings.get("urls") or []]
        max_pages, max_depth = _limits(settings)
        if ctx.max_items:
            max_pages = min(max_pages, ctx.max_items)
        if not starts:
            yield from ctx.missing(())
            yield Cursor(None, full=True)
            return
        crawl = _Crawl()
        queue, queued = [], set()
        for start in starts:
            if _is_sitemap(start):
                for url, _ in _sitemap_urls(crawl, start):
                    if url not in queued:
                        queue.append((url, max_depth, start))
                        queued.add(url)
            elif start not in queued:
                queue.append((start, 0, start))
                queued.add(start)
        seen, pages = set(), 0
        while queue:
            url, depth, start = queue.pop(0)
            if not crawl.allowed(url):
                continue
            if pages >= max_pages:
                yield LimitReached(max_pages)
                yield Cursor(None)                  # stopped early: nothing is deleted
                return
            known = ctx.items.get(url) or {}
            try:
                final, r, body = crawl.fetch(url, lambda u: ctx.known.get(u))
            except (http.NotFound, http.Forbidden, http.ConfigError) as e:
                if url == start and isinstance(e, http.ConfigError):
                    raise
                continue                            # gone (404 / 410) or refused: deleted below
            except (Transient, http.RateLimited):
                if url == start:
                    raise                           # the site is down: no deletes this run
                seen.add(url)                       # keep what we have of this page
                continue
            if final != url:
                if final in seen:
                    continue
                queued.add(final)
                url, known = final, ctx.items.get(final) or {}
            pages += 1
            if body is None and r.status_code == 304:
                seen.add(url)
                links = (known.get("attrs") or {}).get("links") or []
                yield Upsert(Item(source_id=url, title=known.get("title") or url, url=url,
                                  version=known.get("version") or "", kind="webpage",
                                  attrs=known.get("attrs")))
            elif body is None:
                continue                            # too big
            else:
                ctype = (r.headers.get("Content-Type") or "text/html").split(";")[0].strip().lower()
                if ctype in ("text/html", "application/xhtml+xml"):
                    page = extract.html_page(body, url)
                    if page.noindex:
                        continue
                    links = [] if page.nofollow else [canonical(link) for link in page.links
                                                      if link.startswith(("http://", "https://"))]
                    title, text = page.title or url, page.text
                elif ctype in ("text/plain", "text/markdown"):
                    links, title, text = [], url.rsplit("/", 1)[-1] or url, extract.decode(body)
                else:
                    continue                        # not a page we read
                links = [link for link in dict.fromkeys(links)
                         if site_of(link) == site_of(start) and not _SKIP_EXT.search(
                             urlsplit(link).path)][:500]
                seen.add(url)
                yield Upsert(Item(source_id=url, title=title, url=url, version=_version(r, body),
                                  kind="webpage", mime="text/plain", content=text,
                                  parent_id=start, acl={"public_web": True},
                                  attrs={"links": links, "depth": depth}))
            if depth < max_depth:
                for link in links:
                    if link not in queued:
                        queued.add(link)
                        queue.append((link, depth + 1, start))
        yield from ctx.missing(seen)
        yield Cursor(None, full=True)


CONNECTOR = Website()
