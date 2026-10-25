"""One adapter per job site. Each returns a list of normalized dicts:
   id, url, title, company, location, salary, tags(list), text(plain description), date(ISO), remote(bool)
Sites' terms: keep the source name + original link on every job (the board does) and don't redistribute."""
import html
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from jobboard.sources import http


def plain(s: str | None) -> str:
    s = s or ""
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", s)
    s = re.sub(r"(?i)<\s*(br|/p|p(?=[\s>/])|/div|/li|/h\d|/tr)[^>]*>", "\n", s)
    s = re.sub(r"(?i)<li[^>]*>", "- ", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = "\n".join(re.sub(r"[ \t\xa0]+", " ", ln).strip() for ln in s.splitlines())
    return re.sub(r"\n\s*\n+", "\n", s).strip()


def _money(lo, hi, cur="USD", period="year") -> str | None:
    if not lo and not hi:
        return None
    f = lambda v: f"{v / 1000:g}k" if v and v >= 1000 else str(v)
    sym = {"USD": "$", "EUR": "€", "GBP": "£"}.get(cur, cur + " ")
    rng = f"{sym}{f(lo)}" + (f"-{f(hi)}" if hi and hi != lo else "")
    return f"{rng}/{ {'year':'年','month':'月','hour':'时'}.get(period, period) }"


def _iso(v) -> str:
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(v, timezone.utc).isoformat()
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        from email.utils import parsedate_to_datetime
        try:
            d = parsedate_to_datetime(str(v))
        except (TypeError, ValueError):
            return datetime.now(timezone.utc).isoformat()
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).isoformat()   # always UTC: sorts and filters right


def remoteok():
    data = http.get_json("https://remoteok.com/api")
    return [{"id": j["id"], "url": j["url"], "title": j["position"], "company": j.get("company"),
             "location": j.get("location") or "Worldwide", "tags": j.get("tags") or [],
             "salary": _money(j.get("salary_min"), j.get("salary_max")), "text": plain(j.get("description")),
             "date": _iso(j.get("date") or j.get("epoch")), "remote": True}
            for j in data if isinstance(j, dict) and j.get("position")]


def remotive():  # max ~4 requests/day per their terms; jobs are delayed 24h by them
    data = http.get_json("https://remotive.com/api/remote-jobs?category=software-dev&limit=150")
    return [{"id": j["id"], "url": j["url"], "title": j["title"], "company": j.get("company_name"),
             "location": j.get("candidate_required_location") or "", "tags": j.get("tags") or [],
             "salary": j.get("salary") or None, "text": plain(j.get("description")),
             "date": _iso(j.get("publication_date")), "remote": True} for j in data.get("jobs", [])]


def himalayas(pages: int = 4):
    out, cursor = [], None
    for _ in range(pages):
        d = http.get_json("https://himalayas.app/jobs/api?limit=50" + (f"&cursor={cursor}" if cursor else ""))
        for j in d.get("jobs", []):
            loc = ", ".join(j.get("locationRestrictions") or []) or "Worldwide"
            out.append({"id": j.get("guid") or j["applicationLink"], "url": j["applicationLink"], "title": j["title"],
                        "company": j.get("companyName"), "location": loc,
                        "tags": (j.get("categories") or []) + (j.get("parentCategories") or []),
                        "salary": _money(j.get("minSalary"), j.get("maxSalary"), j.get("currency") or "USD",
                                         (j.get("salaryPeriod") or "year").lower()),
                        "text": plain(j.get("description") or j.get("excerpt")),
                        "date": _iso(j.get("pubDate")), "remote": True})
        cursor = d.get("nextCursor")
        if not cursor:
            break
    return out


def jobicy():
    out, seen = [], set()
    for q in ("industry=dev&count=50", "tag=flutter&count=50", "tag=mobile&count=50"):
        for j in http.get_json(f"https://jobicy.com/api/v2/remote-jobs?{q}").get("jobs", []):
            if j["id"] in seen:
                continue
            seen.add(j["id"])
            geo = j.get("jobGeo")
            out.append({"id": j["id"], "url": j["url"], "title": html.unescape(j["jobTitle"]),
                        "company": html.unescape(j.get("companyName") or ""),
                        "location": ", ".join(geo) if isinstance(geo, list) else (geo or ""),
                        "tags": (j.get("jobIndustry") or []) if isinstance(j.get("jobIndustry"), list) else [],
                        "salary": None, "text": plain(j.get("jobDescription") or j.get("jobExcerpt")),
                        "date": _iso(j.get("pubDate")), "remote": True})
    return out


def workingnomads():
    return [{"id": j["url"], "url": j["url"], "title": j["title"], "company": j.get("company_name"),
             "location": j.get("location") or "", "tags": (j.get("tags") or "").split(",") if isinstance(j.get("tags"), str) else (j.get("tags") or []),
             "salary": None, "text": plain(j.get("description")), "date": _iso(j.get("pub_date")), "remote": True}
            for j in http.get_json("https://www.workingnomads.com/api/exposed_jobs/")]


def weworkremotely():
    out, seen = [], set()
    for cat in ("remote-programming-jobs", "remote-front-end-programming-jobs", "remote-full-stack-programming-jobs"):
        root = ET.fromstring(http.get_text(f"https://weworkremotely.com/categories/{cat}.rss"))
        for it in root.iterfind("channel/item"):
            link = it.findtext("link") or it.findtext("guid")
            if not link or link in seen:
                continue
            seen.add(link)
            title = it.findtext("title") or ""
            company, _, role = title.partition(": ")
            out.append({"id": link, "url": link, "title": role or title, "company": company if role else "",
                        "location": it.findtext("region") or "", "tags": [it.findtext("category") or ""],
                        "salary": None, "text": plain(it.findtext("description")),
                        "date": _iso(it.findtext("pubDate")), "remote": True})
    return out


_ROLEISH = re.compile(r"engineer|developer|programmer|\bios\b|android|flutter|swift|kotlin|mobile|front-?end|full-?stack|"
                      r"react|vue|\bdart\b|\bswe\b|architect|lead|manager|designer|devops|\bsre\b|scientist|analyst", re.I)


def _hn_title(first: str) -> tuple[str, str]:
    """HN posts start 'Company | Role | Location | REMOTE | url'. Keep company and the role segments."""
    segs = [x.strip() for x in first.split("|") if x.strip()]
    if len(segs) < 2:
        return "", first[:90]
    roles = [x for x in segs[1:] if _ROLEISH.search(x) and not x.lower().startswith("http")]
    company = re.split(r"\s*\(|\s+https?://", segs[0])[0].strip()
    return company[:60], (" · ".join(roles[:3]) or segs[1])[:90]


def hn_whos_hiring():
    """Latest monthly 'Ask HN: Who is hiring?' thread; every top-level comment is a posting."""
    hits = http.get_json("https://hn.algolia.com/api/v1/search_by_date?tags=story,author_whoishiring&hitsPerPage=6")["hits"]
    thread = next(h for h in hits if "who is hiring" in h["title"].lower())
    tree = http.get_json(f"https://hn.algolia.com/api/v1/items/{thread['objectID']}")
    out = []
    for c in tree.get("children", []):
        text = plain(c.get("text"))
        if not text:
            continue
        first = text.splitlines()[0]
        company, title = _hn_title(first)
        out.append({"id": c["id"], "url": f"https://news.ycombinator.com/item?id={c['id']}", "title": title,
                    "company": company, "location": "", "tags": [],
                    "salary": None, "text": text, "date": _iso(c.get("created_at")),
                    "remote": bool(re.search(r"remote", first, re.I))})
    return out


# name, fake chat_id (negative, outside the -100... channel range), min seconds between runs, adapter
SOURCES = [
    ("RemoteOK",         -1, 3300,      remoteok),
    ("Remotive",         -2, 6 * 3600,  remotive),      # their terms: <= 4 requests/day
    ("We Work Remotely", -3, 3300,      weworkremotely),
    ("Himalayas",        -4, 3300,      himalayas),
    ("Jobicy",           -5, 3300,      jobicy),
    ("Working Nomads",   -6, 3300,      workingnomads),
    ("HN Who's Hiring",  -7, 6 * 3600,  hn_whos_hiring),
]
