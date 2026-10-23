"""Per-company ATS boards: Greenhouse, Lever, Ashby, Recruitee, Personio.
The company list lives in the `companies` table (filled by `jobboard discover`). Each adapter walks its active
companies with a few threads and one global request pace, and keeps only postings whose TITLE matches an enabled role
(the full list of a big employer is megabytes; we do not store or even download what you would never look at).
Every board is public JSON/XML published by the employer itself; jobs keep the employer's own apply link."""
import html
import urllib.error
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from jobboard import db, parse
from jobboard.sources import http
from jobboard.sources.adapters import _iso, _money, plain

WORKERS, GAP = 4, 0.25          # 4 threads, one request starts every 0.25 s overall
GONE = (404, 410)               # only these HTTP codes mean "this board no longer exists"


def wanted(title: str) -> bool:
    """Title-only prefilter using the currently active roles."""
    return parse.role_match(title or "", "", structured=True)[0] >= 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _name(row) -> str:
    return row["name"] or row["slug"].replace("-", " ").replace("_", " ").title()


def _walk(ats: str, fetch_board, con=None, pacer=None, workers: int = WORKERS) -> list[dict]:
    """fetch_board(row, pacer) -> list of items. A 404 counts against the company, other errors do not
    (being offline must not wipe the registry). If every board failed, say so instead of returning nothing."""
    con = con or db.connect()
    rows = db.companies(con, ats)
    pacer = pacer or http.Pacer(GAP)

    def one(row):
        try:
            pacer.wait()
            return row, fetch_board(row, pacer), None
        except Exception as exc:  # collected below
            return row, None, exc

    items, errors, limited = [], [], False
    with ThreadPoolExecutor(workers) as ex:
        for row, got, exc in ex.map(one, rows):
            if exc is None:
                db.company_result(con, ats, row["slug"], True)
                items += got
            elif isinstance(exc, urllib.error.HTTPError) and exc.code in GONE:
                db.company_result(con, ats, row["slug"], False)
            else:
                limited = limited or isinstance(exc, http.RateLimited)
                errors.append(f"{row['slug']}: {type(exc).__name__}")
    con.commit()
    if limited and not items:
        raise http.RateLimited(f"{ats} rate limited")
    if rows and not items and len(errors) == len(rows):
        raise RuntimeError(f"all {len(rows)} {ats} boards failed ({errors[0]})")
    return items


def _remote(*parts) -> bool:
    return any("remote" in (p or "").lower() for p in parts)


# ---- Greenhouse -------------------------------------------------------------------------------------
def _gh_board(row, pacer, base="https://boards-api.greenhouse.io/v1/boards"):
    slug, out = row["slug"], []
    for j in http.get_json(f"{base}/{slug}/jobs").get("jobs", []):
        if not wanted(j.get("title")):
            continue
        try:
            pacer.wait()
            d = http.get_json(f"{base}/{slug}/jobs/{j['id']}?pay_transparency=true")
        except urllib.error.HTTPError:
            d = {}
        pay = (d.get("pay_input_ranges") or [{}])[0]
        loc = (j.get("location") or {}).get("name") or ""
        out.append({"id": f"gh{j['id']}", "url": j["absolute_url"], "title": j["title"].strip(),
                    "company": j.get("company_name") or _name(row), "location": loc, "tags": [],
                    "salary": _money((pay.get("min_cents") or 0) // 100 or None, (pay.get("max_cents") or 0) // 100 or None,
                                     pay.get("currency_type") or "USD") if pay.get("min_cents") or pay.get("max_cents") else None,
                    "text": plain(html.unescape(d.get("content") or "")),   # Greenhouse HTML is itself entity-escaped
                    "date": _iso(j.get("first_published") or _now()), "remote": _remote(loc, j["title"])})
    return out


# ---- Lever ------------------------------------------------------------------------------------------
def _lever_board(row, pacer):
    out = []
    for j in http.get_json(f"https://api.lever.co/v0/postings/{row['slug']}?mode=json"):
        if not wanted(j.get("text")):
            continue
        cat = j.get("categories") or {}
        loc = ", ".join(cat.get("allLocations") or [cat.get("location") or ""]).strip(", ")
        sal = j.get("salaryRange") or {}
        out.append({"id": f"lv{j['id']}", "url": j.get("hostedUrl") or j["applyUrl"], "title": j["text"].strip(),
                    "company": _name(row), "location": loc, "tags": [t for t in (cat.get("team"), cat.get("commitment")) if t],
                    "salary": _money(sal.get("min"), sal.get("max"), sal.get("currency") or "USD",
                                     (sal.get("interval") or "year").split("-")[-1]) if sal else None,
                    "text": (j.get("descriptionPlain") or plain(j.get("description"))) + "\n" + (j.get("additionalPlain") or ""),
                    "date": _iso(j["createdAt"] / 1000 if j.get("createdAt") else _now()),
                    "remote": j.get("workplaceType") == "remote" or _remote(loc)})
    return out


# ---- Ashby ------------------------------------------------------------------------------------------
def _ashby_board(row, pacer):
    out = []
    d = http.get_json(f"https://api.ashbyhq.com/posting-api/job-board/{row['slug']}?includeCompensation=true")
    for j in d.get("jobs", []):
        if j.get("isListed") is False or not wanted(j.get("title")):
            continue
        loc = ", ".join(x for x in [j.get("location")] + [s.get("location") for s in j.get("secondaryLocations") or []] if x)
        comp = (j.get("compensation") or {}).get("compensationTierSummary")
        out.append({"id": f"ab{j['id']}", "url": j.get("jobUrl") or j["applyUrl"], "title": j["title"].strip(),
                    "company": _name(row), "location": loc, "tags": [t for t in (j.get("department"), j.get("team")) if t],
                    "salary": comp or None,
                    "text": j.get("descriptionPlain") or plain(j.get("descriptionHtml")),
                    "date": _iso(j.get("publishedAt") or _now()),
                    "remote": bool(j.get("isRemote")) or j.get("workplaceType") == "Remote"})
    return out


# ---- Recruitee --------------------------------------------------------------------------------------
def _recruitee_board(row, pacer):
    out = []
    for o in http.get_json(f"https://{row['slug']}.recruitee.com/api/offers/").get("offers", []):
        if o.get("status") not in (None, "published") or not wanted(o.get("title")):
            continue
        loc = o.get("location") or ", ".join(x for x in (o.get("city"), o.get("country")) if x)
        out.append({"id": f"rc{o['id']}", "url": o.get("careers_url") or f"https://{row['slug']}.recruitee.com/o/{o.get('slug')}",
                    "title": o["title"].strip(), "company": o.get("company_name") or _name(row), "location": loc or "",
                    "tags": [t for t in (o.get("department"),) if t], "salary": None,
                    "text": plain((o.get("description") or "") + "\n" + (o.get("requirements") or "")),
                    "date": _iso(o.get("published_at") or o.get("created_at") or _now()),
                    "remote": bool(o.get("remote")) or _remote(loc, o["title"])})
    return out


# ---- Personio ---------------------------------------------------------------------------------------
def _personio_board(row, pacer):
    out = []
    root = ET.fromstring(http.get_text(f"https://{row['slug']}.jobs.personio.de/xml?language=en"))
    for p in root.iterfind("position"):
        title = (p.findtext("name") or "").strip()
        if not wanted(title):
            continue
        loc = ", ".join(x for x in [p.findtext("office")] + [o.text for o in p.iterfind("additionalOffices/office")] if x)
        desc = "\n".join(plain(d.findtext("value")) for d in p.iterfind("jobDescriptions/jobDescription"))
        out.append({"id": f"pn{p.findtext('id')}", "url": f"https://{row['slug']}.jobs.personio.de/job/{p.findtext('id')}",
                    "title": title, "company": p.findtext("subcompany") or _name(row), "location": loc,
                    "tags": [t for t in (p.findtext("department"), p.findtext("recruitingCategory")) if t], "salary": None,
                    "text": desc, "date": _iso(p.findtext("createdAt") or _now()), "remote": _remote(loc, title)})
    return out


BOARDS = {"greenhouse": _gh_board, "lever": _lever_board, "ashby": _ashby_board,
          "recruitee": _recruitee_board, "personio": _personio_board}


def _adapter(ats: str):
    return lambda: _walk(ats, BOARDS[ats])


# name, fake chat_id, min seconds between runs, adapter   (ids -10.. stay inside the "site" range -99..-1)
SOURCES = [
    ("Greenhouse", -10, 3 * 3600, _adapter("greenhouse")),
    ("Lever",      -11, 3 * 3600, _adapter("lever")),
    ("Ashby",      -12, 3 * 3600, _adapter("ashby")),
    ("Recruitee",  -13, 3 * 3600, _adapter("recruitee")),
    ("Personio",   -14, 3 * 3600, _adapter("personio")),
]
