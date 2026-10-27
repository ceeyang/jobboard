"""More open job feeds that need no account: Hacker News job posts (YC companies), the Python Software Foundation job
board, Jobspresso and LaraJobs. Verified 2026-10-28 (all answered with current postings).
python.org publishes no date in its feed: the first time we see a posting counts as its date (the database keeps the earliest)."""
import re
import xml.etree.ElementTree as ET

from jobboard.sources import http
from jobboard.sources.adapters import _iso, plain

_ROLE = re.compile(r"engineer|developer|designer|scientist|manager|devops|\bsre\b|analyst|architect|researcher|lead|intern", re.I)
NS = {"dc": "http://purl.org/dc/elements/1.1/", "job": "https://larajobs.com"}


def hn_jobs(get_json=None):
    """The 'jobs' front-page section: companies (mostly YC) announcing that they hire. Titles are 'Acme (YC W24) Is Hiring ...'."""
    hits = (get_json or http.get_json)("https://hn.algolia.com/api/v1/search_by_date?tags=job&hitsPerPage=60")["hits"]
    out = []
    for h in hits:
        title = (h.get("title") or "").strip()
        if not title:
            continue
        m = re.match(r"^(.*?)\s+(?:is|are)\s+hiring\b[:\s]*(.*)$", title, re.I)
        company = re.sub(r"\s*\((?:YC|Y Combinator)[^)]*\)", "", m.group(1)).strip() if m else ""
        rest = m.group(2).strip(" -–—:") if m else ""
        loc = re.match(r"^in\s+(.+)$", rest, re.I)
        role = rest if rest and not loc and _ROLE.search(rest) else title      # 'Is Hiring in Austin, TX' names no role: keep the whole line
        item_url = f"https://news.ycombinator.com/item?id={h['objectID']}"
        out.append({"id": h["objectID"], "url": h.get("url") or item_url, "title": role[:120], "company": company,
                    "location": loc.group(1) if loc else "", "tags": ["YC"] if "YC" in title else [], "salary": None, "text": title,
                    "date": _iso(h.get("created_at")), "remote": bool(re.search(r"remote", title, re.I))})
    return out


def python_org(get_text=None):
    root = ET.fromstring((get_text or http.get_text)("https://www.python.org/jobs/feed/rss/"))
    out = []
    for it in root.iterfind("channel/item"):
        link = it.findtext("link")
        title = (it.findtext("title") or "").strip()
        if not link or not title:
            continue
        role, _, company = title.rpartition(", ")
        text = plain(it.findtext("description"))
        first = text.splitlines()[0] if text else ""
        out.append({"id": link, "url": link, "title": role or title, "company": company if role else "",
                    "location": first[:80] if len(first) < 80 else "", "tags": ["Python"], "salary": None, "text": text,
                    "date": _iso(it.findtext("pubDate")), "remote": bool(re.search(r"remote", first, re.I))})
    return out


def jobspresso(get_text=None):
    root = ET.fromstring((get_text or http.get_text)("https://jobspresso.co/feed/?post_type=job_listing"))
    out = []
    for it in root.iterfind("channel/item"):
        link = it.findtext("link")
        if not link:
            continue
        who = it.findtext("dc:creator", namespaces=NS) or ""        # 'Docker<br>⚲&nbsp;United States, Canada'
        company, _, loc = re.sub(r"&nbsp;|\xa0", " ", who).partition("<br>")
        out.append({"id": link, "url": link, "title": (it.findtext("title") or "").strip(), "company": company.strip(),
                    "location": loc.replace("⚲", "").strip(), "tags": [], "salary": None, "text": plain(it.findtext("description")),
                    "date": _iso(it.findtext("pubDate")), "remote": True})
    return out


def larajobs(get_text=None):
    root = ET.fromstring((get_text or http.get_text)("https://larajobs.com/feed"))
    out = []
    for it in root.iterfind("channel/item"):
        link = it.findtext("link")
        if not link:
            continue
        g = lambda tag: next((c.text or "" for c in it if c.tag.endswith("}" + tag) or c.tag == tag), "")
        loc = g("location")
        out.append({"id": link, "url": link, "title": (it.findtext("title") or "").strip(), "company": g("company") or "",
                    "location": loc, "tags": [t.strip() for t in g("tags").split(",") if t.strip()], "salary": g("salary") or None,
                    "text": plain(it.findtext("description")), "date": _iso(it.findtext("pubDate")),
                    "remote": bool(re.search(r"remote", loc, re.I))})
    return out


# name, fake id (-50..-59: unused ranges), min seconds between runs, adapter
SOURCES = [
    ("HN Jobs",          -50, 3 * 3600, hn_jobs),
    ("Python.org Jobs",  -51, 6 * 3600, python_org),
    ("Jobspresso",       -52, 3 * 3600, jobspresso),
    ("LaraJobs",         -53, 6 * 3600, larajobs),
]
