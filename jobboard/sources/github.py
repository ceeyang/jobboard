"""Job postings that live in public GitHub repositories (the "GitHub" group).
Three shapes, all readable without an account:
  * Rebase "who-is-hiring": every job is an issue; the community site publishes a normalised JSON of them
    (https://hire.rebase.network/jobs.normalized.json). Contact channels in it are deliberately ignored.
  * issue boards (config/github_job_repos.txt): one open issue = one job, e.g. frontendbr/vagas. Read through the REST
    API (GITHUB_TOKEN is used when present: 5000 requests/hour instead of 60).
  * SimplifyJobs listing files: structured early-career roles with a real "sponsorship" field (feeds the visa filter).
Each source keeps its link back to the issue / posting. Contact details in issue bodies stay in the local database only;
the public site shows an excerpt without them (see publish.py)."""
import gzip
import json
import os
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from jobboard.sources import http
from jobboard.sources.adapters import _iso, plain
from jobboard.sources.ats import wanted

PREFIX = "GitHub: "
CONFIG = Path(__file__).resolve().parents[2] / "config" / "github_job_repos.txt"
REBASE_URL = "https://hire.rebase.network/jobs.normalized.json"
SIMPLIFY = {"SimplifyJobs New-Grad": "SimplifyJobs/New-Grad-Positions", "SimplifyJobs Internships": "SimplifyJobs/Summer2027-Internships"}
BODY_MAX = 4000


def issue_repos() -> list[str]:
    try:
        lines = CONFIG.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return list(dict.fromkeys(x.strip() for x in lines if x.strip() and not x.strip().startswith("#") and "/" in x))


def _headers() -> dict:
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if os.environ.get("GITHUB_TOKEN"):
        h["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    return h


def _get_gz(url: str, timeout: int = 90):
    """Big JSON files (13 MB): ask for gzip, it is ~10x smaller."""
    req = urllib.request.Request(url, headers={"User-Agent": http.UA, "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        return json.loads((gzip.decompress(raw) if r.headers.get("Content-Encoding") == "gzip" else raw).decode("utf-8", "replace"))


# ---- Rebase who-is-hiring -----------------------------------------------------------------------------
def rebase(get_json=None):
    data = (get_json or http.get_json)(REBASE_URL)
    out = []
    for j in data.get("jobs", []):
        if j.get("state") != "open" or not j.get("title"):
            continue
        body = "\n".join(x for x in (j.get("summary"), j.get("responsibilities"), j.get("requirements")) if x)
        title = re.sub(r"^\s*\[[^\]]*\]\s*", "", j["title"]).strip() or j["title"]      # the "[远程]" prefix is the location
        out.append({"id": f"rb{j['number']}", "url": j["url"], "title": title, "company": (j.get("company") or "").strip() or "Rebase",
                    "location": j.get("location") or "", "tags": [t for t in (j.get("employment_type"), j.get("work_mode")) if t],
                    "salary": j.get("salary") or None, "text": body, "date": _iso(j.get("created_at")),
                    "remote": bool(j.get("remote"))})
    return out


# ---- issue boards (one issue = one job) ---------------------------------------------------------------
_COMPANY = re.compile(r"\s(?:na|no|em|at|@|—|–|-|\|)\s+([^\[\]()|]{2,40})\s*$", re.I)


def parse_issue_title(title: str) -> tuple[str, str, str]:
    """'[Remoto] Full Stack Developer Pleno na Rotha IA' -> ('Remoto', 'Full Stack Developer Pleno', 'Rotha IA')."""
    loc = ""
    m = re.match(r"^\s*((?:\[[^\]]+\]\s*)+)(.*)$", title)
    if m:
        loc = ", ".join(x.strip() for x in re.findall(r"\[([^\]]+)\]", m.group(1)))
        title = m.group(2)
    c = _COMPANY.search(title)
    company = c.group(1).strip(" .") if c else ""
    if c and company:
        title = title[:c.start()]
    return loc, title.strip(" -–—|") or title.strip(), company


def issues(repo: str, get_json=None):
    gj = get_json or (lambda u: http.get_json(u, headers=_headers()))
    out = []
    for page in (1, 2):
        batch = gj(f"https://api.github.com/repos/{repo}/issues?state=open&sort=created&direction=desc&per_page=100&page={page}")
        for i in batch:
            if "pull_request" in i:          # the issues endpoint also lists pull requests
                continue
            loc, title, company = parse_issue_title(i["title"])
            labels = [lb["name"] for lb in i.get("labels", []) if isinstance(lb, dict)]
            body = (i.get("body") or "")[:BODY_MAX]
            out.append({"id": f"{repo}#{i['number']}", "url": i["html_url"], "title": title, "company": company or repo,
                        "location": loc, "tags": labels, "salary": None, "text": plain(body), "date": _iso(i["created_at"]),
                        "remote": bool(re.search(r"remot[eo]|distribu|anywhere", " ".join([loc] + labels + [i["title"]]), re.I))})
        if len(batch) < 100:
            break
    return out


# ---- SimplifyJobs structured listings -----------------------------------------------------------------
def simplify(repo: str, get_json=None):
    data = (get_json or _get_gz)(f"https://raw.githubusercontent.com/{repo}/dev/.github/scripts/listings.json")
    notes = {"Offers Sponsorship": "The employer offers visa sponsorship.", "Does Not Offer Sponsorship": "No visa sponsorship is offered.",
             "U.S. Citizenship is Required": "U.S. citizenship is required."}
    out = []
    for x in data:
        if not x.get("active") or x.get("is_visible") is False or not wanted(x.get("title")):
            continue
        locs = x.get("locations") or []
        spons = x.get("sponsorship") or ""
        out.append({"id": f"sj{x['id']}", "url": x["url"], "title": x["title"].strip(), "company": x.get("company_name") or "",
                    "location": ", ".join(locs[:3]) + (f" +{len(locs) - 3}" if len(locs) > 3 else ""),
                    "tags": [t for t in [x.get("category")] + list(x.get("terms") or []) if t], "salary": None,
                    "text": f"{x['title']} at {x.get('company_name')}. {notes.get(spons, '')} Degrees: {', '.join(x.get('degrees') or [])}.".strip(),
                    "date": _iso(x.get("date_posted") or datetime.now(timezone.utc).timestamp()),
                    "remote": any("remote" in loc.lower() for loc in locs),
                    "visa": "yes" if spons == "Offers Sponsorship" else ""})
    return out


def sources() -> list:
    """(name, fake id, min seconds between runs, adapter) for every GitHub source; ids -30.. stay in the 'site' id range."""
    out = [(PREFIX + "Rebase who-is-hiring", -30, 3 * 3600, rebase)]
    for i, (label, repo) in enumerate(SIMPLIFY.items()):
        out.append((PREFIX + label, -31 - i, 6 * 3600, lambda repo=repo: simplify(repo)))
    for i, repo in enumerate(issue_repos()[:12]):
        out.append((PREFIX + repo, -33 - i, 3 * 3600, lambda repo=repo: issues(repo)))
    return out
