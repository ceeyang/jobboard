import asyncio
import json
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from jobboard import db, links, mode, parse, roles, scheduler


@asynccontextmanager
async def lifespan(_app):
    roles.activate(db.connect())            # the user's roles drive scoring everywhere
    task = asyncio.create_task(_scheduler_loop())
    yield
    task.cancel()


app = FastAPI(title="Job Board", lifespan=lifespan)
PAGE = Path(__file__).parent / "templates" / "app.html"
STATUSES = ["new", "interested", "applied", "replied", "ignored"]
_proc: subprocess.Popen | None = None


def _decode(r, usernames: dict | None = None) -> dict:
    d = dict(r)
    for k in ("tags", "risks", "contacts", "sources"):
        d[k] = json.loads(d[k])
    d["posted_at"], d["last_posted"] = db.post_times(d.get("posted_at"), d["sources"])
    if (d.get("geo") or "unknown") == "unknown":   # location field empty (Telegram, HN ...): maybe the text says "US only"
        d["geo"] = parse.geo_from_text(d.get("text"))
    for s in d["sources"]:
        u = (usernames or {}).get(s["chat_id"])
        s["url"] = s.get("url") or links.post_url(s["chat_id"], u, s["msg_id"])  # job sites carry their own URL
        s["channel_url"] = links.channel_url(s["chat_id"], u)
        s["kind"] = mode.kind(s["chat"], s["chat_id"])  # job sites use tiny fake ids; government services are their own group
    return d


def usernames(con) -> dict:
    return {r["chat_id"]: r["username"] for r in con.execute("SELECT chat_id, username FROM chats")}


@app.get("/", response_class=HTMLResponse)
def index():
    return PAGE.read_text(encoding="utf-8")


@app.get("/api/jobs")
def jobs(min_score: int = 1, limit: int = 500, since: str | None = None):
    """since: 1d | 3d | 7d | 30d keeps only jobs first posted within that window (omit for everything)."""
    con = db.connect()
    try:
        rows = db.list_jobs(con, min_score, limit, since)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    names = usernames(con)
    return {"jobs": [_decode(r, names) for r in rows], "sync": _sync(con)}


def _sync(con) -> dict:
    last = con.execute("SELECT MAX(first_seen) FROM jobs").fetchone()[0]
    s = scheduler.load(con)
    return {"running": _running(), "last_job_seen": last,
            "failed": bool(_proc and _proc.poll() not in (None, 0)),
            "refresh_minutes": s["refresh_minutes"], "next_fetch_at": scheduler.next_fetch_at(s)}


@app.get("/api/chats")
def chats():
    """Subscribed groups/channels with their public link and how many matching jobs each produced."""
    con = db.connect()
    counts: dict[int, int] = {}
    for (src, score) in con.execute("SELECT sources, score FROM jobs WHERE score>=5"):
        for cid in {x["chat_id"] for x in json.loads(src)}:
            counts[cid] = counts.get(cid, 0) + 1
    out = [{"chat_id": r["chat_id"], "title": r["title"], "username": r["username"],
            "url": links.channel_url(r["chat_id"], r["username"]), "jobs": counts.get(r["chat_id"], 0)}
           for r in con.execute("SELECT chat_id, username, title FROM chats")]
    return {"chats": sorted(out, key=lambda c: (-c["jobs"], c["title"] or ""))}


@app.get("/api/sync")
def sync():
    return _sync(db.connect())


@app.post("/api/jobs/{job_id}/status")
def set_status(job_id: int, body: dict = Body(...)):
    status = body.get("status")
    if status not in STATUSES:
        raise HTTPException(400, f"status must be one of {STATUSES}")
    con = db.connect()
    cur = con.execute("UPDATE jobs SET status=? WHERE id=?", (status, job_id))
    con.commit()
    if not cur.rowcount:
        raise HTTPException(404, "no such job")
    return {"id": job_id, "status": status}


def _running() -> bool:
    return bool(_proc and _proc.poll() is None)


def start_fetch(con) -> bool:
    """Run the fetch in its own process: Telethon's session must not be shared across threads."""
    global _proc
    if _running():
        return False
    scheduler.save(con, {"last_fetch_started": time.time()})
    _proc = subprocess.Popen([sys.executable, "-m", "jobboard.cli", "fetch"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return True


@app.post("/api/refresh")
def refresh():
    started = start_fetch(db.connect())
    return {"started": True} if started else {"started": False, "reason": "already running"}


def _tick(now_ts: float | None = None, now_dt: datetime | None = None) -> dict:
    """One scheduler step: start an automatic fetch / the daily digest when they are due."""
    con = db.connect()
    s = scheduler.load(con)
    did = {}
    if scheduler.fetch_due(s, now_ts if now_ts is not None else time.time(), _running()):
        did["fetch"] = start_fetch(con)
    now = now_dt or datetime.now()
    if scheduler.digest_due(s, now):
        scheduler.save(con, {"last_digest_date": now.strftime("%Y-%m-%d")})  # first: never send twice
        subprocess.Popen([sys.executable, "-m", "jobboard.cli", "digest"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        did["digest"] = True
    return did


async def _scheduler_loop():
    await asyncio.sleep(5)
    while True:
        try:
            await asyncio.to_thread(_tick)
        except Exception:  # a bad tick must never kill the loop
            pass
        await asyncio.sleep(20)


@app.get("/api/settings")
def get_settings():
    s = scheduler.load(db.connect())
    return {**s, "next_fetch_at": scheduler.next_fetch_at(s), "min_refresh": scheduler.MIN_REFRESH}


@app.put("/api/settings")
def put_settings(body: dict = Body(...)):
    try:
        patch = scheduler.validate(body)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    scheduler.save(db.connect(), patch)
    return get_settings()


def _reanalyze() -> int:
    con = db.connect()
    roles.activate(con)
    return db.reanalyze(con)


@app.get("/api/roles")
def get_roles():
    return {"roles": roles.list_roles(db.connect())}


def _role_body(body: dict):
    try:
        return roles.validate(body.get("label"), body.get("keywords"), body.get("weight", 6))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/roles")
def add_role(body: dict = Body(...)):
    label, kws, w = _role_body(body)
    con = db.connect()
    roles.seed(con)
    if con.execute("SELECT 1 FROM roles WHERE label=?", (label,)).fetchone():
        raise HTTPException(400, f"已存在名为「{label}」的岗位")
    pos = con.execute("SELECT COALESCE(MAX(position),0)+1 FROM roles").fetchone()[0]
    cur = con.execute("INSERT INTO roles(key,label,keywords,weight,position) VALUES(?,?,?,?,?)",
                      (f"tmp{pos}", label, kws, w, pos))
    con.execute("UPDATE roles SET key=? WHERE id=?", (f"r{cur.lastrowid}", cur.lastrowid))
    con.commit()
    return {"id": cur.lastrowid, "rescored": _reanalyze(), "roles": roles.list_roles(con)}


@app.put("/api/roles/{role_id}")
def edit_role(role_id: int, body: dict = Body(...)):
    con = db.connect()
    if not con.execute("SELECT 1 FROM roles WHERE id=?", (role_id,)).fetchone():
        raise HTTPException(404, "no such role")
    if set(body) == {"enabled"}:   # quick on/off toggle
        con.execute("UPDATE roles SET enabled=? WHERE id=?", (int(bool(body["enabled"])), role_id))
    else:
        label, kws, w = _role_body(body)
        if con.execute("SELECT 1 FROM roles WHERE label=? AND id<>?", (label, role_id)).fetchone():
            raise HTTPException(400, f"已存在名为「{label}」的岗位")
        con.execute("UPDATE roles SET label=?, keywords=?, weight=? WHERE id=?", (label, kws, w, role_id))
    con.commit()
    return {"rescored": _reanalyze(), "roles": roles.list_roles(con)}


@app.delete("/api/roles/{role_id}")
def delete_role(role_id: int):
    con = db.connect()
    if not con.execute("DELETE FROM roles WHERE id=?", (role_id,)).rowcount:
        raise HTTPException(404, "no such role")
    con.commit()
    return {"rescored": _reanalyze(), "roles": roles.list_roles(con)}
