"""Fill the `companies` registry. A candidate is stored only after its public endpoint answered with a real board.
Channels (all optional, combine freely):
  db           ATS links already inside stored jobs (HN threads, apply links of remote boards ...) - free and relevant
  file         any text file; every ATS link or `ats:slug` line in it is picked up, other http(s) lines become career pages
  seed         a short built-in list of well-known employers
  commoncrawl  Common Crawl's URL index for the ATS hosts (best effort, the public service is often overloaded)"""
import json
import re
import time
import urllib.error
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

from jobboard import db
from jobboard.sources import http, jsonld

_URLS = [
    ("greenhouse", re.compile(r"(?:job-)?boards(?:\.eu)?\.greenhouse\.io/(?:embed/job_board\?for=)?([a-z0-9][a-z0-9_-]{1,60})", re.I)),
    ("lever", re.compile(r"jobs\.lever\.co/([a-z0-9][a-z0-9_-]{1,60})", re.I)),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([a-z0-9][a-z0-9%._-]{1,60})", re.I)),
    ("recruitee", re.compile(r"([a-z0-9][a-z0-9-]{1,60})\.recruitee\.com", re.I)),
    ("personio", re.compile(r"([a-z0-9][a-z0-9-]{1,60})\.jobs\.personio\.(?:de|com)", re.I)),
]
_SLUG = re.compile(r"[a-z0-9][a-z0-9._-]{0,60}")      # what can be part of a board URL; spaces etc. are text noise
_NOT_SLUGS = {"embed", "v1", "api", "www", "jobs", "careers", "boards", "static", "assets", "images", "img", "cdn", "app"}
_PAIR = re.compile(r"^\s*(greenhouse|lever|ashby|recruitee|personio)\s*[:=,]\s*([\w.-]+)\s*$", re.I | re.M)
SEED = {
    "greenhouse": ["stripe", "airbnb", "dropbox", "coinbase", "discord", "reddit", "pinterest", "robinhood", "instacart",
                   "twilio", "cloudflare", "datadog", "gitlab", "okta", "squarespace", "grammarly", "duolingo", "asana"],
    "lever": ["palantir", "spotify", "plaid", "kraken", "binance", "welocalize", "outreach", "mistral", "zoox"],
    "ashby": ["ramp", "notion", "linear", "openai", "retool", "vercel", "deel", "supabase", "cursor", "replit"],
    "recruitee": ["bunq", "tbo", "tiktok"],
    "personio": ["personio", "celonis"],
}
CC_HOSTS = {"greenhouse": ["boards.greenhouse.io", "job-boards.greenhouse.io"], "lever": ["jobs.lever.co"],
            "ashby": ["jobs.ashbyhq.com"]}


def extract(text: str) -> set[tuple[str, str]]:
    found = {(a.lower(), s.lower()) for a, s in _PAIR.findall(text or "")}
    for ats, rx in _URLS:
        for m in rx.findall(text or ""):
            slug = urllib.parse.unquote(m).lower().strip("/.-_")
            if slug and slug not in _NOT_SLUGS and _SLUG.fullmatch(slug):
                found.add((ats, slug))
    return found


def from_db(con) -> set[tuple[str, str]]:
    found = set()
    for r in con.execute("SELECT text, contacts, sources FROM jobs"):
        found |= extract(f"{r['text']}\n{r['contacts']}\n{r['sources']}")
    return found


def from_file(path: str) -> set[tuple[str, str]]:
    text = open(path, encoding="utf-8").read()
    found = extract(text)
    for line in text.splitlines():
        u = line.strip()
        if u.startswith(("http://", "https://")) and not any(rx.search(u) for _, rx in _URLS):
            found.add(("web", u))
    return found


def seed() -> set[tuple[str, str]]:
    return {(a, s) for a, slugs in SEED.items() for s in slugs}


def commoncrawl(get_text=None, sleep=time.sleep, limit: int = 5000) -> set[tuple[str, str]]:
    get_text = get_text or (lambda u: http.get_text(u, timeout=90))
    try:
        crawl = json.loads(get_text("https://index.commoncrawl.org/collinfo.json"))[0]["id"]
    except Exception:
        return set()
    found = set()
    for ats, hosts in CC_HOSTS.items():
        for host in hosts:
            for attempt in range(3):
                try:
                    body = get_text(f"https://index.commoncrawl.org/{crawl}-index?"
                                    + urllib.parse.urlencode({"url": f"{host}/*", "output": "json", "fl": "url", "limit": limit}))
                    for line in body.splitlines():
                        try:
                            found |= extract(json.loads(line)["url"])
                        except (ValueError, KeyError):
                            continue
                    break
                except Exception:
                    sleep(3 * (attempt + 1))
    return found


def probe(ats: str, slug: str, get_json=None, get_text=None) -> tuple[bool, str | None]:
    """One cheap request: does this board exist? Returns (ok, company name if the API tells it)."""
    gj, gt = get_json or http.get_json, get_text or http.get_text
    try:
        if ats == "greenhouse":
            d = gj(f"https://boards-api.greenhouse.io/v1/boards/{slug}")
            return bool(d.get("name")), d.get("name")
        if ats == "lever":
            return isinstance(gj(f"https://api.lever.co/v0/postings/{slug}?mode=json&limit=1"), list), None
        if ats == "ashby":
            return "jobs" in gj(f"https://api.ashbyhq.com/posting-api/job-board/{slug}"), None
        if ats == "recruitee":
            return "offers" in gj(f"https://{slug}.recruitee.com/api/offers/"), None
        if ats == "personio":
            return "<workzag-jobs" in gt(f"https://{slug}.jobs.personio.de/xml?language=en")[:600], None
        if ats == "web":
            page = gt(slug)
            return bool(jsonld.extract_postings(page) or jsonld.job_links(page, slug)), None
    except Exception:   # any network/parse problem (InvalidURL, IncompleteRead, odd JSON ...) just means "not a usable board"
        return False, None
    return False, None


def run(con=None, channels=("db", "seed", "commoncrawl"), file: str | None = None, max_probe: int = 2000,
        probe_fn=probe, pace: float = 0.25, workers: int = 4) -> dict:
    con = con or db.connect()
    cands: set[tuple[str, str]] = set()
    per_channel = {}
    for ch in channels:
        got = {"db": lambda: from_db(con), "seed": seed, "commoncrawl": commoncrawl,
               "file": lambda: from_file(file) if file else set()}[ch]()
        per_channel[ch] = len(got)
        cands |= got
    known = {(r["ats"], r["slug"]) for r in con.execute("SELECT ats, slug FROM companies")}
    todo = sorted(c for c in cands if c not in known)[:max_probe]
    pacer = http.Pacer(pace)

    def check(c):
        pacer.wait()
        return c, probe_fn(*c)

    added = invalid = 0
    try:
        with ThreadPoolExecutor(workers) as ex:
            for (ats, slug), (ok, name) in ex.map(check, todo):
                if ok:
                    added += db.company_add(con, ats, slug, name)
                    db.company_result(con, ats, slug, True, name)
                else:
                    invalid += 1
        con.commit()
    except BaseException:
        con.rollback()      # a failed discovery must not keep the database locked for the steps after it
        raise
    total = {r["ats"]: r["n"] for r in con.execute("SELECT ats, COUNT(*) n FROM companies WHERE active=1 GROUP BY ats")}
    return {"candidates": per_channel, "probed": len(todo), "added": added, "invalid": invalid, "registry": total}
