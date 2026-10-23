"""LinkedIn through its public, logged-out `jobs-guest` pages. No account, no cookies, nothing of yours is sent.
Personal low-volume use only: it is an undocumented interface (it can change or throttle at any time) and automated
access is against LinkedIn's terms, so this stays gentle: 2 pages per query, a pause of several seconds between
requests, a cap on description downloads per run, and it stops at the first 429.
Queries = your enabled roles' English keywords x setting `linkedin_locations` (default ["Remote"])."""
import html as _html
import json
import random
import re
import time
import urllib.parse

from jobboard import db, roles
from jobboard.sources import http
from jobboard.sources.adapters import _iso, plain
from jobboard.sources.ats import wanted

BASE = "https://www.linkedin.com/jobs-guest/jobs/api"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/130.0 Safari/537.36", "Accept-Language": "en-US,en;q=0.9"}
WINDOW = "r604800"        # f_TPR: posted within the last week (r86400 = day, r2592000 = month)
PAGES, MAX_DETAILS, GAP, SEEN_KEEP = 2, 40, 3.0, 5000


def _text(pattern: str, chunk: str) -> str:
    m = re.search(pattern, chunk, re.S)
    return _html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(1)))).strip() if m else ""


def parse_cards(page: str) -> list[dict]:
    out = []
    for chunk in page.split("<li>")[1:]:
        m = re.search(r'urn:li:jobPosting:(\d+)', chunk)
        if not m:
            continue
        out.append({"id": m.group(1), "title": _text(r'base-search-card__title[^>]*>(.*?)</h3>', chunk),
                    "company": _text(r'base-search-card__subtitle.*?<a[^>]*>(.*?)</a>', chunk)
                    or _text(r'base-search-card__subtitle[^>]*>(.*?)</h4>', chunk),
                    "location": _text(r'job-search-card__location[^>]*>(.*?)</span>', chunk),
                    "date": (re.search(r'datetime="([^"]+)"', chunk) or [None, None])[1]})
    return out


def parse_detail(page: str) -> dict:
    m = re.search(r'show-more-less-html__markup[^>]*>(.*?)</div>', page, re.S)
    crit = [_html.unescape(x).strip() for x in re.findall(r'description__job-criteria-text[^>]*>\s*([^<]+?)\s*<', page)]
    return {"text": plain(m.group(1)) if m else "", "criteria": crit}


def _settings(con) -> tuple[list[str], list[str]]:
    got = {r["key"]: json.loads(r["value"]) for r in con.execute(
        "SELECT key, value FROM settings WHERE key IN ('linkedin_locations','linkedin_seen')")}
    return got.get("linkedin_locations") or ["Remote"], got.get("linkedin_seen") or []


def linkedin(get_text=None, sleep=time.sleep, con=None):
    con = con or db.connect()
    get = get_text or (lambda u: http.get_text(u, headers=HEADERS))
    terms, (places, seen_list) = roles.search_terms(con, 4), _settings(con)
    if not terms:
        raise http.Skip("no English role keywords to search for")
    seen, out, details = set(seen_list), [], 0
    pause = lambda: sleep(GAP + random.uniform(0, 1.5))
    try:
        for term in terms:
            for place in places:
                start = 0
                for _ in range(PAGES):
                    q = urllib.parse.urlencode({"keywords": term, "location": place, "f_TPR": WINDOW, "start": start})
                    cards = parse_cards(get(f"{BASE}/seeMoreJobPostings/search?{q}"))
                    pause()
                    if not cards:
                        break
                    start += len(cards)
                    for c in cards:
                        if c["id"] in seen or not wanted(c["title"]):
                            continue
                        if details >= MAX_DETAILS:
                            continue            # picked up on a later run (it is not marked seen)
                        d = parse_detail(get(f"{BASE}/jobPosting/{c['id']}"))
                        details += 1
                        pause()
                        seen.add(c["id"])
                        out.append({"id": f"li{c['id']}", "url": f"https://www.linkedin.com/jobs/view/{c['id']}",
                                    "title": c["title"], "company": c["company"], "location": c["location"],
                                    "tags": d["criteria"], "salary": None, "text": d["text"],
                                    "date": _iso(c["date"] or time.time()),
                                    "remote": "remote" in (c["location"] + c["title"]).lower()})
    except http.RateLimited:
        if not out:
            raise
    finally:
        con.execute("INSERT INTO settings(key,value) VALUES('linkedin_seen',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (json.dumps(sorted(seen)[-SEEN_KEEP:]),))
        con.commit()
    return out


SOURCES = [("LinkedIn", -19, 6 * 3600, linkedin)]
