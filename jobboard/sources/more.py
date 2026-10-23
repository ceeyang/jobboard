"""Batch 2 + USAJOBS: Adzuna, The Muse, Arbeitnow (public APIs) and USAJOBS (federal jobs).
Keys come from the environment; a missing key skips the source instead of failing the run:
  ADZUNA_APP_ID / ADZUNA_APP_KEY   (free, https://developer.adzuna.com, ~250 calls/day)
  USAJOBS_KEY / USAJOBS_EMAIL      (free, https://developer.usajobs.gov)
Terms: keep the source name + original link on every job (the board does) and do not redistribute."""
import os
import urllib.parse

from jobboard import db, roles
from jobboard.sources import http
from jobboard.sources.adapters import _iso, _money, plain

ADZUNA_COUNTRIES = ("us", "gb", "ca", "au", "de", "sg")   # keeps one run to ~6 x queries calls (daily quota 250)


def _terms(limit: int = 4) -> list[str]:
    return roles.search_terms(db.connect(), limit) or ["flutter", "ios", "android"]


def adzuna(get_json=None):
    app_id, key = os.environ.get("ADZUNA_APP_ID"), os.environ.get("ADZUNA_APP_KEY")
    if not (app_id and key):
        raise http.Skip("set ADZUNA_APP_ID and ADZUNA_APP_KEY (free key)")
    get_json = get_json or http.get_json
    out, seen = [], set()
    for cc in ADZUNA_COUNTRIES:
        for term in _terms():
            q = urllib.parse.urlencode({"app_id": app_id, "app_key": key, "results_per_page": 50, "what": term,
                                        "max_days_old": 30, "sort_by": "date", "content-type": "application/json"})
            for j in get_json(f"https://api.adzuna.com/v1/api/jobs/{cc}/search/1?{q}").get("results", []):
                if j["id"] in seen:
                    continue
                seen.add(j["id"])
                loc = (j.get("location") or {}).get("display_name") or ""
                out.append({"id": f"az{j['id']}", "url": j["redirect_url"], "title": plain(j["title"]),
                            "company": (j.get("company") or {}).get("display_name") or "", "location": loc,
                            "tags": [(j.get("category") or {}).get("label") or ""],
                            "salary": _money(j.get("salary_min"), j.get("salary_max"), {"gb": "GBP", "de": "EUR"}.get(cc, "USD")),
                            "text": plain(j.get("description")), "date": _iso(j.get("created")),
                            "remote": "remote" in (j["title"] + loc + (j.get("description") or "")).lower()})
    return out


def themuse(pages: int = 3, get_json=None):
    get_json = get_json or http.get_json
    out = []
    for page in range(pages):
        d = get_json("https://www.themuse.com/api/public/jobs?" + urllib.parse.urlencode(
            [("page", page), ("category", "Software Engineering"), ("category", "Design and UX"), ("descending", "true")]))
        for j in d.get("results", []):
            locs = ", ".join(x["name"] for x in j.get("locations") or [])
            out.append({"id": f"mu{j['id']}", "url": (j.get("refs") or {}).get("landing_page"), "title": plain(j["name"]),
                        "company": (j.get("company") or {}).get("name") or "", "location": locs,
                        "tags": [c["name"] for c in j.get("categories") or []] + [lv["name"] for lv in j.get("levels") or []],
                        "salary": None, "text": plain(j.get("contents")), "date": _iso(j.get("publication_date")),
                        "remote": "flexible" in locs.lower() or "remote" in locs.lower()})
        if page + 1 >= d.get("page_count", 0):
            break
    return [x for x in out if x["url"]]


def arbeitnow(pages: int = 3, get_json=None):
    get_json = get_json or http.get_json
    out = []
    for page in range(1, pages + 1):
        d = get_json(f"https://www.arbeitnow.com/api/job-board-api?page={page}")
        for j in d.get("data", []):
            out.append({"id": f"an{j['slug']}", "url": j["url"], "title": plain(j["title"]), "company": j.get("company_name"),
                        "location": j.get("location") or "", "tags": (j.get("tags") or []) + (j.get("job_types") or []),
                        "salary": None, "text": plain(j.get("description")), "date": _iso(j.get("created_at")),
                        "remote": bool(j.get("remote"))})
        if not (d.get("links") or {}).get("next"):
            break
    return out


def usajobs(get_json=None):
    key, email = os.environ.get("USAJOBS_KEY"), os.environ.get("USAJOBS_EMAIL")
    if not (key and email):
        raise http.Skip("set USAJOBS_KEY and USAJOBS_EMAIL (free key)")
    get_json = get_json or http.get_json
    hdr = {"Host": "data.usajobs.gov", "User-Agent": email, "Authorization-Key": key}
    out, seen = [], set()
    for term in _terms():
        q = urllib.parse.urlencode({"Keyword": term, "DatePosted": 30, "ResultsPerPage": 50})
        for it in get_json(f"https://data.usajobs.gov/api/search?{q}", headers=hdr)["SearchResult"]["SearchResultItems"]:
            d = it["MatchedObjectDescriptor"]
            if d["PositionID"] in seen:
                continue
            seen.add(d["PositionID"])
            pay = (d.get("PositionRemuneration") or [{}])[0]
            det = (d.get("UserArea") or {}).get("Details") or {}
            out.append({"id": f"us{d['PositionID']}", "url": d["PositionURI"], "title": d["PositionTitle"],
                        "company": d.get("OrganizationName") or "", "location": d.get("PositionLocationDisplay") or "",
                        "tags": [], "salary": _money(float(pay["MinimumRange"]), float(pay["MaximumRange"]), "USD")
                        if pay.get("MinimumRange") else None,
                        "text": plain((det.get("JobSummary") or "") + "\n" + (d.get("QualificationSummary") or "")),
                        "date": _iso(d.get("PublicationStartDate")), "remote": bool(det.get("RemoteIndicator"))})
    return out


SOURCES = [
    ("Adzuna",     -15, 6 * 3600, adzuna),
    ("The Muse",   -16, 3 * 3600, themuse),
    ("Arbeitnow",  -17, 3 * 3600, arbeitnow),
    ("USAJOBS",    -18, 6 * 3600, usajobs),
]
