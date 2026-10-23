"""Government job services. Only interfaces that are public and need no account:
  Germany  Bundesagentur für Arbeit "Jobsuche" (the API behind arbeitsagentur.de; fixed public key header)
  Sweden   Arbetsförmedlingen / JobTech "JobSearch" (open data, no key)
  EU       EURES job-vehicle search (the API behind the EURES portal, ~30 countries)
  Canada   Job Bank search pages (robots.txt asks for a 5 s crawl delay - respected). Jobs whose employer applied for
           an LMIA (permission to hire a foreign worker) come back with visa='yes'.
Not covered, on purpose: Australia (Workforce Australia / APS Jobs publish no API or open feed; only third-party
scrapers exist), France Travail (needs a free OAuth client - see more.py style), UK Find a job (no public API).
All of them are searched with your enabled roles' English keywords; titles are prefiltered by role like the ATS sources."""
import base64
import html
import re
import time
import urllib.parse
from datetime import datetime, timezone

from jobboard import db, roles
from jobboard.sources import http
from jobboard.sources.adapters import _iso, plain
from jobboard.sources.ats import wanted

DE_HEADERS = {"X-API-Key": "jobboerse-jobsuche", "Accept": "application/json"}   # public key used by the official website
CA_GAP = 5.0                                                                      # Job Bank robots.txt Crawl-delay
EURES_COUNTRIES = {"AT": "Austria", "BE": "Belgium", "BG": "Bulgaria", "HR": "Croatia", "CY": "Cyprus", "CZ": "Czechia",
                   "DK": "Denmark", "EE": "Estonia", "FI": "Finland", "FR": "France", "DE": "Germany", "GR": "Greece",
                   "HU": "Hungary", "IS": "Iceland", "IE": "Ireland", "IT": "Italy", "LV": "Latvia", "LI": "Liechtenstein",
                   "LT": "Lithuania", "LU": "Luxembourg", "MT": "Malta", "NL": "Netherlands", "NO": "Norway", "PL": "Poland",
                   "PT": "Portugal", "RO": "Romania", "SK": "Slovakia", "SI": "Slovenia", "ES": "Spain", "SE": "Sweden",
                   "CH": "Switzerland"}


def _terms(n: int = 6) -> list[str]:
    return roles.search_terms(db.connect(), n) or ["flutter", "ios", "android"]


def germany(get_json=None):
    gj = get_json or (lambda u: http.get_json(u, headers=DE_HEADERS))
    out, seen = [], set()
    for term in _terms():
        q = urllib.parse.urlencode({"was": term, "veroeffentlichtseit": 28, "size": 50, "page": 1, "angebotsart": 1})
        for j in gj(f"https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v6/jobs?{q}").get("ergebnisliste", []):
            ref, title = j.get("referenznummer"), j.get("stellenangebotsTitel") or ""
            if not ref or ref in seen or not wanted(title):
                continue
            seen.add(ref)
            try:
                d = gj("https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v4/jobdetails/"
                       + base64.b64encode(ref.encode()).decode())
            except Exception:
                d = {}
            loc = ", ".join(dict.fromkeys(x.get("adresse", {}).get("ort", "") for x in j.get("stellenlokationen", [])
                                          if x.get("adresse", {}).get("ort")))
            out.append({"id": f"de{ref}", "url": f"https://www.arbeitsagentur.de/jobsuche/jobdetail/{ref}", "title": title,
                        "company": j.get("firma") or d.get("firma") or "", "location": (loc + ", Germany").strip(", "),
                        "tags": [t for t in (j.get("hauptberuf"),) if t], "salary": None,
                        "text": plain(html.unescape(d.get("stellenangebotsBeschreibung") or "")),
                        "date": _iso(j.get("datumErsteVeroeffentlichung") or d.get("datumErsteVeroeffentlichung")),
                        "remote": bool(d.get("homeofficemoeglich") or j.get("homeofficemoeglich"))})
    return out


def sweden(get_json=None):
    gj = get_json or http.get_json
    out, seen = [], set()
    for term in _terms():
        q = urllib.parse.urlencode({"q": term, "limit": 50, "sort": "pubdate-desc"})
        for h in gj(f"https://jobsearch.api.jobtechdev.se/search?{q}").get("hits", []):
            if h["id"] in seen or not wanted(h.get("headline")):
                continue
            seen.add(h["id"])
            wa = h.get("workplace_address") or {}
            loc = ", ".join(x for x in (wa.get("municipality"), wa.get("region"), wa.get("country") or "Sweden") if x)
            body = (h.get("description") or {}).get("text") or ""
            out.append({"id": f"se{h['id']}", "url": h.get("webpage_url") or f"https://arbetsformedlingen.se/platsbanken/annonser/{h['id']}",
                        "title": h["headline"].strip(), "company": (h.get("employer") or {}).get("name") or "",
                        "location": loc, "tags": [(h.get("occupation") or {}).get("label") or ""],
                        "salary": h.get("salary_description") or None, "text": plain(body), "date": _iso(h.get("publication_date")),
                        "remote": bool(re.search(r"remote|distans|hemarbete", body, re.I))})
    return out


def eures(post_json=None):
    post = post_json or http.post_json
    out, seen = [], set()
    for term in _terms():
        body = {"resultsPerPage": 50, "page": 1, "sortSearch": "BEST_MATCH",
                "keywords": [{"keyword": term, "specificSearchCode": "TITLE"}], "publicationPeriod": "LAST_MONTH",
                "occupationUris": [], "skillUris": [], "requiredExperienceCodes": [], "positionScheduleCodes": [],
                "sectorCodes": [], "educationAndQualificationLevelCodes": [], "positionOfferingCodes": [], "locationCodes": [],
                "euresFlagCodes": [], "otherBenefitsCodes": [], "requiredLanguages": [], "minNumberPost": None}
        for j in post("https://europa.eu/eures/api/jv-searchengine/public/jv-search/search", body).get("jvs", []):
            if j["id"] in seen or not wanted(j.get("title")):
                continue
            seen.add(j["id"])
            countries = [EURES_COUNTRIES.get(c, c) for c in (j.get("locationMap") or {})]
            out.append({"id": f"eu{j['id']}", "url": f"https://europa.eu/eures/portal/jv-se/jv-details/{j['id']}?lang=en",
                        "title": j["title"].strip(), "company": (j.get("employer") or {}).get("name") or "",
                        "location": ", ".join(countries), "tags": [], "salary": None, "text": plain(j.get("description")),
                        "date": _iso(int(j["creationDate"]) / 1000 if j.get("creationDate") else None),
                        "remote": bool(re.search(r"remote|work from home|home ?office|télétravail|thuiswerk", j.get("description") or "", re.I))})
    return out


_CA_DATE = re.compile(r"([A-Z][a-z]+ \d{1,2}, \d{4})")


def _ca_cards(page: str) -> list[dict]:
    cards = []
    for m in re.finditer(r'<article id="article-(\d+)".*?</article>', page, re.S):
        a = m.group(0)

        def txt(pattern):
            found = re.search(pattern, a, re.S)
            return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", found.group(1)))).strip() if found else ""
        title = txt(r'class="noctitle">(.*?)</span>')
        if not title:
            continue
        d = _CA_DATE.search(txt(r'<li class="date">(.*?)</li>'))
        cards.append({"id": m.group(1), "title": title, "company": txt(r'<li class="business">(.*?)</li>'),
                      "location": re.sub(r"^Location\s*", "", txt(r'<li class="location">(.*?)</li>')),
                      "salary": re.sub(r"^Salary:?\s*", "", txt(r'<li class="salary">(.*?)</li>')) or None,
                      "date": datetime.strptime(d.group(1), "%B %d, %Y").replace(tzinfo=timezone.utc).isoformat() if d else None,
                      "remote": bool(re.search(r'class="telework">\s*(?:Remote|Hybrid)', a))})
    return cards


def canada(get_text=None, sleep=time.sleep):
    get = get_text or (lambda u: http.get_text(u))
    seen, lmia = {}, set()
    # Job Bank titles follow Canadian occupation names, so framework words (flutter, swift) find nothing there
    for term in list(dict.fromkeys(["mobile app developer", "android developer", "ios developer", "front-end developer"] + _terms(2))):
        for flag in (False, True):          # second pass = only jobs whose employer requested an LMIA
            q = {"searchstring": term, "sort": "D", "fsrc": 16}
            if flag:
                q["fskl"] = 101010
            for c in _ca_cards(get("https://www.jobbank.gc.ca/jobsearch/jobsearch?" + urllib.parse.urlencode(q))):
                if flag:
                    lmia.add(c["id"])
                if c["id"] not in seen and wanted(c["title"]):
                    seen[c["id"]] = c
            sleep(CA_GAP)
    return [{"id": f"ca{cid}", "url": f"https://www.jobbank.gc.ca/jobsearch/jobposting/{cid}", "title": c["title"],
             "company": c["company"], "location": (c["location"] + ", Canada").strip(", "), "tags": [], "salary": c["salary"],
             "text": f"{c['title']} - {c['company']} - {c['location']}. Salary: {c['salary'] or 'not stated'}.",
             "date": _iso(c["date"] or datetime.now(timezone.utc).isoformat()), "remote": c["remote"],
             "visa": "yes" if cid in lmia else ""} for cid, c in seen.items()]


SOURCES = [
    ("Germany (BA)",    -21, 6 * 3600, germany),
    ("Sweden (AF)",     -22, 6 * 3600, sweden),
    ("EURES (EU)",      -23, 6 * 3600, eures),
    ("Canada Job Bank", -24, 12 * 3600, canada),
]
