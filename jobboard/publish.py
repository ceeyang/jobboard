"""Turn the job database into a static, public-safe site (GitHub Pages).
What is published: title, company, location, salary, tags, a <=600 character excerpt and the links back to the
original posting. What is never published: contact handles/e-mails, full texts, your statuses and notes, anything from
Telegram. A final self-check fails the build if an e-mail or @handle is still present."""
import html
import json
import re
import shutil
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jobboard import db, mode, parse, profile_config, roles

WINDOW_DAYS, EXCERPT, CHUNK = 45, 600, 400     # jobs per data file: the page shows the first one at once and loads the rest in the background
ROOT = Path(__file__).parent
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_HANDLE = re.compile(r"(?<![\w/@])@[A-Za-z][A-Za-z0-9_]{3,31}\b")
_TME = re.compile(r"(?:https?://)?t\.me/[^\s)>\]】\"']+", re.I)


def redact(s: str | None) -> str:
    s = _TME.sub("[telegram]", s or "")
    return _HANDLE.sub("[@]", _EMAIL.sub("[email]", s))


def excerpt(text: str | None) -> str:
    t = redact(text).strip()
    return t if len(t) <= EXCERPT else t[:EXCERPT].rsplit(" ", 1)[0].rstrip() + "…"


def _first_seen(r) -> str:
    """The board's "NEW" badge means first seen < 24 h ago. On a freshly built public site that would be everything,
    so use the posting's own date when it is older than our first sighting."""
    seen = (r["first_seen"] or "").replace(" ", "T")
    seen = seen + "+00:00" if seen and "+" not in seen and not seen.endswith("Z") else seen   # sqlite CURRENT_TIMESTAMP is UTC
    a, b = db._ts(r["posted_at"]), db._ts(seen)
    return r["posted_at"] if a is not None and (b is None or a < b) else r["first_seen"]


def numeric_id(fingerprint: str) -> int:
    """Stable, JS-safe integer id (the board compares ids as numbers; 12 hex digits < 2^53)."""
    return int(fingerprint[:12], 16)


def is_telegram(chat_id: int) -> bool:
    return chat_id > 0 or chat_id <= -100          # job sites use tiny fake ids -1..-99


def _public_links(sources: list[dict]) -> list[str]:
    return list(dict.fromkeys(s["url"] for s in sources if s.get("url")))[:5]


def scrub_db(con) -> int:
    """Rewrite stored rows so that the database itself (committed to a public branch) holds only excerpts."""
    n = 0
    for r in con.execute("SELECT id, text, sources FROM jobs").fetchall():
        links = _public_links(public_sources(json.loads(r["sources"])))
        con.execute("UPDATE jobs SET text=?, contacts=?, note='' WHERE id=?",
                    (excerpt(r["text"]), json.dumps({"telegram": [], "email": [], "links": links}), r["id"]))
        n += 1
    con.commit()
    return n


def public_sources(sources: list[dict]) -> list[dict]:
    """Only sources allowed on the public site: never Telegram, LinkedIn, keyed APIs ... (see mode.py)."""
    return [s for s in sources if s.get("url") and (mode.is_public_telegram_source(s) if is_telegram(s["chat_id"])
                                                    else mode.allowed(s["chat"], "public"))]


def job_to_public(r) -> dict | None:
    sources = public_sources(json.loads(r["sources"]))
    if not sources:
        return None
    posted, last = db.post_times(r["posted_at"], sources)
    return {"id": numeric_id(r["fingerprint"]), "title": redact(r["title"]), "company": redact(r["company"]),
            "location": r["location"], "geo": r["geo"] if r["geo"] not in (None, "", "unknown") else parse.geo_from_text(r["text"]), "salary": r["salary"], "remote": bool(r["remote"]),
            "score": r["score"], "tags": json.loads(r["tags"]), "risks": json.loads(r["risks"]), "visa": r["visa"] or "",
            "posted_at": posted, "last_posted": last, "first_seen": _first_seen(r), "status": "new", "text": excerpt(r["text"]), "structured": r["company"] is not None, "hint": r["hint"] or "",
            "contacts": {"telegram": [], "email": [], "links": _public_links(sources)},
            "sources": [{"chat": s["chat"], "chat_id": s["chat_id"], "msg_id": s["msg_id"], "date": s["date"], "url": s["url"],
                         "channel_url": None, "kind": mode.kind(s["chat"], s["chat_id"])} for s in sources]}


def _count_chats(jobs: list[dict]) -> dict[int, int]:
    counts: dict[int, int] = {}
    for j in jobs:
        if j["score"] >= 5:
            for cid in {s["chat_id"] for s in j["sources"] if s["kind"] == "telegram"}:
                counts[cid] = counts.get(cid, 0) + 1
    return counts


def write_site(out: str | Path, jobs: list[dict], *, now: datetime, window_days: int, site: dict, chats: list[dict],
               chips: list[dict], state: list[dict], extra_meta: dict | None = None) -> dict:
    """Write the static site (page, shim, paged data files). Both `export` (from the database) and `build` (from another
    site's feed) end here, so the privacy self-check below applies to everything that is ever published."""
    out = Path(out)
    (out / "data").mkdir(parents=True, exist_ok=True)
    # best matches first: the first chunk is what the default view (recommended level, "most relevant") needs
    jobs = sorted(jobs, key=lambda j: (-j["score"], -(db._ts(j["posted_at"]) or 0)))
    chunks = [jobs[i:i + CHUNK] for i in range(0, len(jobs), CHUNK)] or [[]]
    for stale in (out / "data").glob("jobs-*.json"):
        stale.unlink()
    blob = json.dumps({"jobs": jobs}, ensure_ascii=False)
    leaks = [m.group(0) for m in (_EMAIL.search(blob), _HANDLE.search(re.sub(r"https?://\S+", "", blob))) if m]
    if leaks:
        raise RuntimeError(f"refusing to publish: contact data found in export ({leaks[0]!r})")
    head = {"generated_at": now.isoformat(), "total": len(jobs), "chunks": len(chunks), "chunk_size": CHUNK, "jobs": chunks[0]}
    (out / "data/jobs.json").write_text(json.dumps(head, ensure_ascii=False), encoding="utf-8")      # index + first chunk
    for i, c in enumerate(chunks[1:], 1):
        (out / f"data/jobs-{i}.json").write_text(json.dumps({"jobs": c}, ensure_ascii=False), encoding="utf-8")
    counts = _count_chats(jobs)
    chats = sorted(({**c, "jobs": counts.get(c["chat_id"], 0)} for c in chats), key=lambda c: (-c["jobs"], c["title"]))
    (out / "data/channels.json").write_text(json.dumps({"chats": chats}, ensure_ascii=False), encoding="utf-8")
    (out / "data/roles.json").write_text(json.dumps({"roles": chips}, ensure_ascii=False), encoding="utf-8")
    meta = {"generated_at": now.isoformat(), "jobs": len(jobs), "chunks": len(chunks), "window_days": window_days, "sources": state,
            "site": site, **(extra_meta or {})}
    (out / "data/meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    page = (ROOT / "templates/app.html").read_text(encoding="utf-8")
    page = page.replace("<script>\nconst STATUS=", '<script src="shim.js"></script>\n<script>\nconst STATUS=', 1)
    page = page.replace("function nextRunText(m,next){", "function nextRunText(m,next){if(window.JB_STATIC)return window.JB_STATIC.label();", 1)
    title_html = html.escape(site["title"])
    page = page.replace("<title>岗位看板</title>", f"<title>{title_html}</title>", 1)
    page = page.replace('<span class="bname">岗位看板</span>', f'<span class="bname">{title_html}</span>', 1)
    if 'src="shim.js"' not in page or "JB_STATIC" not in page:
        raise RuntimeError("app.html changed: the static-site patch points no longer match")
    (out / "index.html").write_text(page, encoding="utf-8")
    shutil.copy(ROOT / "static/shim.js", out / "shim.js")
    (out / ".nojekyll").write_text("")
    return meta


def export(con, out: str | Path, now: datetime | None = None, window_days: int = WINDOW_DAYS) -> dict:
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=window_days)).isoformat()
    jobs = []
    for r in con.execute("SELECT * FROM jobs WHERE score>=1 AND julianday(posted_at)>=julianday(?) ORDER BY julianday(posted_at) DESC", (cutoff,)):
        j = job_to_public(r)
        if j:
            jobs.append(j)
    listed = set(mode.public_telegram_channels())
    chats = [{"chat_id": r["chat_id"], "title": r["title"] or r["username"], "username": r["username"], "url": f"https://t.me/{r['username']}"}
             for r in con.execute("SELECT chat_id, username, title FROM chats") if (r["username"] or "").lower() in listed]
    cfg = profile_config.current()
    chips = profile_config.roles_for_ui(cfg) if cfg else roles.list_roles(con)     # the profile decides which role buttons exist
    state = [{"name": r["name"], "last_count": r["last_count"], "ok": not r["last_error"], "error": (r["last_error"] or "")[:120]}
             for r in con.execute("SELECT name, last_count, last_error FROM source_state ORDER BY name")]
    site = (cfg or {}).get("site") or {"title": "岗位看板", "description": ""}
    return write_site(out, jobs, now=now, window_days=window_days, site=site, chats=chats, chips=chips, state=state)


# ---- build a site from another site's feed (what the profile repositories do) ---------------------------------
def _read(base: str, name: str):
    """base is a URL (https://host/path/data) or a local folder; returns the parsed JSON file."""
    if re.match(r"https?://", base):
        from jobboard.sources import http
        last = None
        for attempt in range(3):
            try:
                return http.get_json(f"{base.rstrip('/')}/{name}", timeout=60)
            except Exception as exc:        # Pages can be a few seconds behind right after a deploy
                last = exc
                time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"cannot read the feed file {name} from {base}: {last}")
    return json.loads((Path(base) / name).read_text(encoding="utf-8"))


def read_feed(base: str) -> dict:
    head = _read(base, "jobs.json")
    jobs = list(head["jobs"])
    for i in range(1, head.get("chunks", 1)):
        jobs += _read(base, f"jobs-{i}.json")["jobs"]
    if len(jobs) != head.get("total", len(jobs)):
        raise RuntimeError(f"incomplete feed: {len(jobs)} of {head['total']} jobs")
    return {"jobs": jobs, "generated_at": head["generated_at"], "channels": _read(base, "channels.json")["chats"], "meta": _read(base, "meta.json")}


def build_from_feed(base: str, out: str | Path, cfg: dict, now: datetime | None = None) -> dict:
    """Re-score the feed's jobs with this profile (its keywords, its exclusions) and keep the ones that fit."""
    feed = read_feed(base)
    kept = []
    for j in feed["jobs"]:
        a = parse.analyze(j["text"], j.get("hint", ""), structured=j.get("structured", True))
        if a["score"] >= 1:
            kept.append({**j, "score": a["score"], "tags": a["tags"]})
    meta = feed["meta"]
    return write_site(out, kept, now=datetime.fromisoformat(feed["generated_at"]), window_days=meta.get("window_days", WINDOW_DAYS),
                      site=cfg["site"], chats=feed["channels"], chips=profile_config.roles_for_ui(cfg), state=meta.get("sources", []),
                      extra_meta={"feed": {"jobs": len(feed["jobs"]), "kept": len(kept), "built_at": (now or datetime.now(timezone.utc)).isoformat()}})
