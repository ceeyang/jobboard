"""Batch 3: schema.org JobPosting JSON-LD found on employer career pages (the markup companies publish for Google for Jobs).
Pages come from `companies` rows with ats='web' (slug = the career-page URL). A page can be a single posting or a
listing: for a listing we follow up to MAX_LINKS same-site links that look like job pages."""
import html
import json
import re
import urllib.parse

from jobboard.sources import http
from jobboard.sources.adapters import _iso, _money, plain
from jobboard.sources.ats import _now, _remote, _walk, wanted

MAX_LINKS = 25
_SCRIPT = re.compile(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.S | re.I)
_JOBLINK = re.compile(r'href=["\']([^"\'#]*(?:/jobs?/|/careers?/|/positions?/|/openings?/|/vacanc)[^"\'#]*)["\']', re.I)


def _flatten(node):
    if isinstance(node, list):
        for x in node:
            yield from _flatten(x)
    elif isinstance(node, dict):
        yield node
        for k in ("@graph", "itemListElement", "item", "mainEntity"):
            if k in node:
                yield from _flatten(node[k])


def extract_postings(page: str) -> list[dict]:
    out = []
    for raw in _SCRIPT.findall(page):
        try:
            data = json.loads(raw.strip())
        except ValueError:
            continue
        for n in _flatten(data):
            t = n.get("@type")
            if t == "JobPosting" or (isinstance(t, list) and "JobPosting" in t):
                out.append(n)
    return out


def _s(v) -> str:
    if isinstance(v, dict):
        return _s(v.get("name") or v.get("@value") or "")
    return str(v or "").strip()


def _location(p: dict) -> str:
    locs = p.get("jobLocation") or []
    parts = []
    for loc in locs if isinstance(locs, list) else [locs]:
        a = (loc or {}).get("address") or {}
        if isinstance(a, str):
            parts.append(a)
        else:
            parts.append(", ".join(x for x in (_s(a.get("addressLocality")), _s(a.get("addressRegion")), _s(a.get("addressCountry"))) if x))
    return "; ".join(x for x in parts if x)


def to_item(p: dict, page_url: str, fallback_company: str) -> dict | None:
    title = _s(p.get("title"))
    if not title:
        return None
    desc = p.get("description") or ""
    if "&lt;" in desc and "<" not in desc:
        desc = html.unescape(desc)
    base = p.get("baseSalary") or {}
    sal = base.get("value") if isinstance(base, dict) else None
    sal = sal if isinstance(sal, dict) else {}
    unit = (_s(sal.get("unitText")) or "YEAR").lower()
    ident = p.get("identifier")
    loc = _location(p)
    url = _s(p.get("url")) or page_url
    return {"id": _s(ident.get("value") if isinstance(ident, dict) else ident) or url,
            "url": url, "title": title, "company": _s(p.get("hiringOrganization")) or fallback_company, "location": loc,
            "tags": [t for t in (_s(p.get("employmentType")), _s(p.get("industry"))) if t],
            "salary": _money(sal.get("minValue") or sal.get("value"), sal.get("maxValue"),
                             _s(base.get("currency")) or "USD", {"hour": "hour", "month": "month"}.get(unit, "year"))
            if sal.get("minValue") or sal.get("value") else None,
            "text": plain(desc), "date": _iso(p.get("datePosted") or _now()),
            "remote": "TELECOMMUTE" in _s(p.get("jobLocationType")).upper() or _remote(loc, title)}


def job_links(page: str, base: str) -> list[str]:
    host, root = urllib.parse.urlparse(base).netloc, base.rstrip("/") + "/"
    links = []
    for href in re.findall(r'href=["\']([^"\'#]+)["\']', page):
        u = urllib.parse.urljoin(base, html.unescape(href)).split("?")[0]
        # a job page = looks like one (/jobs/, /careers/ ...) or sits one level under the listing page
        if urllib.parse.urlparse(u).netloc != host or u in (base, root) or u in links:
            continue
        if _JOBLINK.search(f'href="{u}"') or (u.startswith(root) and "/" not in u[len(root):].strip("/")):
            links.append(u)
    return links[:MAX_LINKS]


def _page_board(row, pacer, get_text=None):
    get_text = get_text or http.get_text
    url, name = row["slug"], row["name"] or urllib.parse.urlparse(row["slug"]).netloc
    page = get_text(url)
    posts = [(p, url) for p in extract_postings(page)]
    if not posts:                       # a listing page: look at the job pages it links to
        for link in job_links(page, url):
            pacer.wait()
            try:
                posts += [(p, link) for p in extract_postings(get_text(link))]
            except Exception:           # one dead link must not hide the others
                continue
    items = (to_item(p, u, name) for p, u in posts)
    return [i for i in items if i and wanted(i["title"])]


def jsonld():
    return _walk("web", _page_board)


SOURCES = [("Career pages", -20, 6 * 3600, jsonld)]
