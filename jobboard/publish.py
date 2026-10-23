"""Turn the job database into a static, public-safe site (GitHub Pages).
What is published: title, company, location, salary, tags, a <=600 character excerpt and the links back to the
original posting. What is never published: contact handles/e-mails, full texts, your statuses and notes, anything from
Telegram. A final self-check fails the build if an e-mail or @handle is still present."""
import json
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jobboard import mode, roles

WINDOW_DAYS, EXCERPT = 45, 600
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
    return [s for s in sources if not is_telegram(s["chat_id"]) and s.get("url") and mode.allowed(s["chat"], "public")]


def job_to_public(r) -> dict | None:
    sources = public_sources(json.loads(r["sources"]))
    if not sources:
        return None
    return {"id": numeric_id(r["fingerprint"]), "title": redact(r["title"]), "company": redact(r["company"]),
            "location": r["location"], "geo": r["geo"] or "unknown", "salary": r["salary"], "remote": bool(r["remote"]),
            "score": r["score"], "tags": json.loads(r["tags"]), "risks": json.loads(r["risks"]), "visa": r["visa"] or "",
            "posted_at": r["posted_at"], "first_seen": r["first_seen"], "status": "new", "text": excerpt(r["text"]),
            "contacts": {"telegram": [], "email": [], "links": _public_links(sources)},
            "sources": [{"chat": s["chat"], "chat_id": s["chat_id"], "msg_id": s["msg_id"], "date": s["date"], "url": s["url"],
                         "channel_url": None, "kind": "site"} for s in sources]}


def export(con, out: str | Path, now: datetime | None = None, window_days: int = WINDOW_DAYS) -> dict:
    out = Path(out)
    (out / "data").mkdir(parents=True, exist_ok=True)
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=window_days)).isoformat()
    jobs = []
    for r in con.execute("SELECT * FROM jobs WHERE score>=1 AND julianday(posted_at)>=julianday(?) ORDER BY posted_at DESC", (cutoff,)):
        j = job_to_public(r)
        if j:
            jobs.append(j)
    blob = json.dumps({"generated_at": now.isoformat(), "jobs": jobs}, ensure_ascii=False)
    leaks = [m.group(0) for m in (_EMAIL.search(blob), _HANDLE.search(re.sub(r"https?://\S+", "", blob))) if m]
    if leaks:
        raise RuntimeError(f"refusing to publish: contact data found in export ({leaks[0]!r})")
    (out / "data/jobs.json").write_text(blob, encoding="utf-8")
    (out / "data/channels.json").write_text('{"chats": []}', encoding="utf-8")
    (out / "data/roles.json").write_text(json.dumps({"roles": roles.list_roles(con)}, ensure_ascii=False), encoding="utf-8")
    state = [{"name": r["name"], "last_count": r["last_count"], "ok": not r["last_error"], "error": (r["last_error"] or "")[:120]}
             for r in con.execute("SELECT name, last_count, last_error FROM source_state ORDER BY name")]
    meta = {"generated_at": now.isoformat(), "jobs": len(jobs), "window_days": window_days, "sources": state}
    (out / "data/meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    page = (ROOT / "templates/app.html").read_text(encoding="utf-8")
    page = page.replace("<script>\nconst STATUS=", '<script src="shim.js"></script>\n<script>\nconst STATUS=', 1)
    page = page.replace("function nextRunText(m,next){", "function nextRunText(m,next){if(window.JB_STATIC)return window.JB_STATIC.label();", 1)
    if 'src="shim.js"' not in page or "JB_STATIC" not in page:
        raise RuntimeError("app.html changed: the static-site patch points no longer match")
    (out / "index.html").write_text(page, encoding="utf-8")
    shutil.copy(ROOT / "static/shim.js", out / "shim.js")
    (out / ".nojekyll").write_text("")
    return meta
