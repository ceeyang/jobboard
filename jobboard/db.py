import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_HOME = Path.home() / ".local/share/jobboard"
HOME = Path(os.environ.get("JOBBOARD_HOME", DEFAULT_HOME))
SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs(
  id INTEGER PRIMARY KEY, fingerprint TEXT UNIQUE, title TEXT, salary TEXT, text TEXT,
  score INTEGER, tags TEXT, risks TEXT, remote INTEGER, contacts TEXT,
  sources TEXT,            -- JSON list of {chat, chat_id, msg_id, date}
  posted_at TEXT, first_seen TEXT DEFAULT CURRENT_TIMESTAMP,
  status TEXT DEFAULT 'new', notified INTEGER DEFAULT 0, note TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS source_state(name TEXT PRIMARY KEY, last_run REAL, last_count INTEGER, last_error TEXT);
CREATE TABLE IF NOT EXISTS chats(chat_id INTEGER PRIMARY KEY, username TEXT, title TEXT);
CREATE TABLE IF NOT EXISTS state(chat_id INTEGER PRIMARY KEY, last_id INTEGER, title TEXT);
CREATE TABLE IF NOT EXISTS roles(
  id INTEGER PRIMARY KEY, key TEXT UNIQUE, label TEXT NOT NULL, keywords TEXT NOT NULL,
  weight INTEGER NOT NULL DEFAULT 6, enabled INTEGER NOT NULL DEFAULT 1, position INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS companies(   -- boards the ATS adapters walk; ats='web' rows are career pages (JSON-LD)
  ats TEXT NOT NULL, slug TEXT NOT NULL, name TEXT, active INTEGER NOT NULL DEFAULT 1,
  fails INTEGER NOT NULL DEFAULT 0, last_ok TEXT, PRIMARY KEY(ats, slug));
CREATE INDEX IF NOT EXISTS jobs_score ON jobs(score DESC, posted_at DESC);
"""
SINCE_DAYS = {"1d": 1, "3d": 3, "7d": 7, "30d": 30}   # board time-range buttons
MAX_FAILS = 5                                         # consecutive failures before a company board is deactivated


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    p = Path(path) if path else HOME / "jobs.db"
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p, timeout=30)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    have = {r[1] for r in con.execute("PRAGMA table_info(jobs)")}
    for col, ddl in (("company", "TEXT"), ("location", "TEXT"), ("geo", "TEXT DEFAULT 'unknown'"), ("hint", "TEXT DEFAULT ''"), ("visa", "TEXT DEFAULT ''")):
        if col not in have:  # lightweight migration for databases created before web sources existed
            con.execute(f"ALTER TABLE jobs ADD COLUMN {col} {ddl}")
    con.commit()
    return con


def upsert(con, a: dict, text: str, src: dict) -> bool:
    """Insert a job or merge another source into an existing one. True if new."""
    row = con.execute("SELECT id, sources FROM jobs WHERE fingerprint=?", (a["fingerprint"],)).fetchone()
    if row:
        sources = json.loads(row["sources"])
        if not any(s["chat_id"] == src["chat_id"] and s["msg_id"] == src["msg_id"] for s in sources):
            sources.append(src)
            con.execute("UPDATE jobs SET sources=? WHERE id=?", (json.dumps(sources, ensure_ascii=False), row["id"]))
        # a job is as old as its earliest sighting, so "last day" never shows a re-listed old posting
        dates = [d for d in (s.get("date") for s in sources) if _ts(d) is not None]
        if dates:
            con.execute("UPDATE jobs SET posted_at=? WHERE id=?", (min(dates, key=_ts), row["id"]))
        if a.get("visa") == "yes":   # one source saying "employer sponsors" is enough
            con.execute("UPDATE jobs SET visa='yes' WHERE id=?", (row["id"],))
        return False
    con.execute(
        "INSERT INTO jobs(fingerprint,title,salary,text,score,tags,risks,remote,contacts,sources,posted_at,"
        "company,location,geo,hint,visa) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (a["fingerprint"], a["title"], a["salary"], text, a["score"],
         json.dumps(a["tags"]), json.dumps(a["risks"], ensure_ascii=False), int(a["remote"]),
         json.dumps(a["contacts"], ensure_ascii=False), json.dumps([src], ensure_ascii=False), src["date"],
         a.get("company"), a.get("location"), a.get("geo", "unknown"), a.get("hint", ""), a.get("visa", "")))
    return True


def _ts(iso: str | None) -> float | None:
    try:
        d = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp()


def since_cutoff(since: str | None, now: datetime | None = None) -> str | None:
    """'1d'|'3d'|'7d'|'30d' -> ISO cutoff; None/'' = no limit. Unknown values raise ValueError."""
    if not since:
        return None
    if since not in SINCE_DAYS:
        raise ValueError(f"since must be one of {list(SINCE_DAYS)}")
    return ((now or datetime.now(timezone.utc)) - timedelta(days=SINCE_DAYS[since])).isoformat()


def list_jobs(con, min_score: int = 1, limit: int = 500, since: str | None = None):
    cutoff = since_cutoff(since)
    if cutoff is None:
        return con.execute("SELECT * FROM jobs WHERE score>=? ORDER BY posted_at DESC LIMIT ?", (min_score, limit)).fetchall()
    return con.execute("SELECT * FROM jobs WHERE score>=? AND julianday(posted_at)>=julianday(?) "
                       "ORDER BY posted_at DESC LIMIT ?", (min_score, cutoff, limit)).fetchall()


# ---- company registry (ATS boards) ------------------------------------------------------------------
def company_add(con, ats: str, slug: str, name: str | None = None) -> bool:
    """True if newly added. A deactivated board is revived when it is added again."""
    slug = slug.lower() if ats != "web" else slug
    existed = con.execute("SELECT 1 FROM companies WHERE ats=? AND slug=?", (ats, slug)).fetchone() is not None
    con.execute("INSERT INTO companies(ats,slug,name) VALUES(?,?,?) ON CONFLICT(ats,slug) DO UPDATE SET "
                "active=1, fails=0, name=COALESCE(excluded.name, name)", (ats, slug, name))
    return not existed


def companies(con, ats: str) -> list[sqlite3.Row]:
    return con.execute("SELECT * FROM companies WHERE ats=? AND active=1 ORDER BY slug", (ats,)).fetchall()


def company_result(con, ats: str, slug: str, ok: bool, name: str | None = None) -> None:
    if ok:
        con.execute("UPDATE companies SET fails=0, last_ok=?, name=COALESCE(?,name) WHERE ats=? AND slug=?",
                    (datetime.now(timezone.utc).isoformat(), name, ats, slug))
    else:
        con.execute("UPDATE companies SET fails=fails+1, active=CASE WHEN fails+1>=? THEN 0 ELSE 1 END "
                    "WHERE ats=? AND slug=?", (MAX_FAILS, ats, slug))


def reanalyze(con) -> int:
    """Recompute derived fields from stored text after editing profile.py / parse.py."""
    from jobboard import parse
    n = 0
    for r in con.execute("SELECT id, text, hint, title, company, remote, contacts, location, visa FROM jobs").fetchall():
        web = r["company"] is not None  # structured sources: keep their title/remote/apply link
        a = parse.analyze(r["text"], r["hint"] or "", structured=web)
        old = json.loads(r["contacts"])
        if web:
            a["contacts"]["links"] = list(dict.fromkeys(old.get("links", [])[:1] + a["contacts"]["links"]))[:5]
        visa = "yes" if r["visa"] == "yes" else a["visa"]   # an official flag (e.g. Canada LMIA) survives re-scoring
        con.execute("UPDATE jobs SET title=COALESCE(?,title),salary=COALESCE(?,salary),score=?,tags=?,risks=?,remote=?,"
                    "contacts=?,geo=?,visa=? WHERE id=?",
                    (None if web else a["title"], None if web else a["salary"], a["score"], json.dumps(a["tags"]),
                     json.dumps(a["risks"], ensure_ascii=False), r["remote"] if web else int(a["remote"]),
                     json.dumps(a["contacts"], ensure_ascii=False),
                     parse.geo_flag(r["location"]) if web else "unknown", visa, r["id"]))
        n += 1
    con.commit()
    return n
