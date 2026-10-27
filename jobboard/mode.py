"""Which sources may feed the PUBLIC site (GitHub Pages). The local board uses everything; the public one is a
reduced edition: only sources whose terms allow showing title + short excerpt + link back, and nothing that needs a
personal login or key.
Left out of the public site on purpose:
  LinkedIn            automated access is against its terms -> personal use only
  Adzuna, USAJOBS     need personal API keys whose terms usually forbid redistribution
  The Muse, Arbeitnow terms not checked for re-publication (kept local)
  Germany (BA), Canada Job Bank  no explicit open-data licence found (kept local)
  Telegram (private)  private groups / the logged-in session never leave the machine; only channels listed in
                      config/telegram_channels.txt and read through their public web preview are published"""
import json
import os
import re
from pathlib import Path

PUBLIC_SOURCES = {
    "RemoteOK", "Remotive", "We Work Remotely", "Himalayas", "Jobicy", "Working Nomads", "HN Who's Hiring",
    "HN Jobs", "Python.org Jobs", "Jobspresso", "LaraJobs",                    # open RSS / JSON feeds
    "Greenhouse", "Lever", "Ashby", "Recruitee", "Personio", "Career pages",     # employers' own public boards
    "Sweden (AF)",                                                              # JobTech open data
    "EURES (EU)",                                                               # EU Commission service
    "Telegram (public)",                                                        # listed public channels, web preview only
}


CONFIG = Path(__file__).resolve().parents[1] / "config" / "telegram_channels.txt"
_T_ME_POST = re.compile(r"https://t\.me/([A-Za-z0-9_]+)/\d+$")


BLOCKLIST = Path(__file__).resolve().parents[1] / "config" / "blocklist.json"


def blocklist() -> dict:
    """config/blocklist.json (or the file named by JOBBOARD_BLOCKLIST): things that must never appear on the public site, e.g. after a
    removal request. {"channels": ["username"], "companies": ["Acme"], "domains": ["example.com"], "job_ids": [123]}"""
    try:
        raw = json.loads(Path(os.environ.get("JOBBOARD_BLOCKLIST") or BLOCKLIST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    low = lambda k: {str(x).strip().lower() for x in raw.get(k) or [] if str(x).strip()}
    return {"channels": low("channels"), "companies": low("companies"), "domains": low("domains"),
            "job_ids": {int(x) for x in raw.get("job_ids") or [] if str(x).lstrip("-").isdigit()}}


def is_blocked(job: dict, bl: dict | None = None) -> bool:
    """A public-job dict (as exported) is blocked when its id, company or any source link matches the blocklist."""
    bl = bl or blocklist()
    if job.get("id") in bl["job_ids"] or (job.get("company") or "").strip().lower() in bl["companies"]:
        return True
    for s in job.get("sources", []):
        url = s.get("url") or ""
        m = _T_ME_POST.match(url)
        if m and m.group(1).lower() in bl["channels"]:
            return True
        host = re.sub(r"^https?://([^/:?#]+).*$", r"\1", url).lower()
        if any(host == d or host.endswith("." + d) for d in bl["domains"]):
            return True
    return False


def public_telegram_channels() -> list[str]:
    """Usernames from config/telegram_channels.txt: the only Telegram channels allowed on the public site (minus the blocked ones)."""
    try:
        lines = CONFIG.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    blocked = blocklist()["channels"]
    return [x for x in dict.fromkeys(x.strip().lower() for x in lines if x.strip() and not x.strip().startswith("#")) if x not in blocked]


def is_public_telegram_source(src: dict) -> bool:
    """A post read from a listed public channel through its web preview (it carries its t.me link).
    Posts read with a personal Telegram login have no such link and never qualify."""
    m = _T_ME_POST.match(src.get("url") or "")
    return bool(m) and m.group(1).lower() in set(public_telegram_channels())


GITHUB_PREFIX = "GitHub: "
GOV_SOURCES = {"Germany (BA)", "Sweden (AF)", "EURES (EU)", "Canada Job Bank", "USAJOBS"}   # shown as their own group


def kind(chat: str, chat_id: int) -> str:
    """'gov' = government job service, 'github' = jobs kept in public GitHub repositories, 'site' = other job site
    (tiny fake ids -1..-99), else 'telegram'."""
    if chat in GOV_SOURCES:
        return "gov"
    if chat.startswith(GITHUB_PREFIX):
        return "github"
    return "site" if -100 < chat_id < 0 else "telegram"


def allowed(source_name: str, mode: str | None) -> bool:
    return mode != "public" or source_name in PUBLIC_SOURCES or source_name.startswith(GITHUB_PREFIX)
