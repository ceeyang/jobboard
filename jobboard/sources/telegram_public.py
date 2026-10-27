"""Public Telegram channels read through their web preview (https://t.me/s/<name>): no login, no account, nothing of
yours is involved, so it also runs in the cloud. Channels without a web preview (private groups, invite-only groups,
channels that switched it off) simply cannot be read this way and are skipped and reported.
Which channels: config/telegram_channels.txt (one username per line). Only posts from those channels are ever allowed on
the public site (see mode.py); the posts keep their link back to t.me/<channel>/<id>."""
import html
import re
import time
import zlib

from jobboard import db, mode, parse
from jobboard.sources import http
from jobboard.sources.adapters import plain

FIRST_PAGES, NEXT_PAGES, GAP = 3, 4, 1.5      # pages of 20 posts: first run goes back a little, later runs only fetch what is new
MIN_TEXT = 15                                  # same rule as the Telethon reader: tiny posts are not job ads
MIN_SCORE = 1                                  # posts that match none of the profile's roles are not stored (keeps the public database small)


def chat_id_for(username: str) -> int:
    """Public channels have no numeric id here: derive a stable one outside the real channel range."""
    return -1_000_000_000_000 - zlib.crc32(username.lower().encode())


def parse_page(page: str) -> tuple[str, list[dict]]:
    """-> (channel title, posts). Media-only posts come back with text ''."""
    title = html.unescape((re.search(r'<meta property="og:title" content="([^"]*)"', page) or [0, ""])[1]).strip()
    posts = []
    for chunk in page.split("tgme_widget_message_wrap")[1:]:
        m = re.search(r'data-post="([^"/]+)/(\d+)"', chunk)
        if not m:
            continue
        body = re.search(r'tgme_widget_message_text[^>]*>(.*?)</div>', chunk, re.S)
        when = re.search(r'<time[^>]*datetime="([^"]+)"', chunk)
        posts.append({"id": int(m.group(2)), "text": plain(body.group(1)).strip() if body else "",
                      "date": when.group(1) if when else None})
    return title, posts


def read_channel(con, username: str, get_text, sleep) -> dict:
    cid = chat_id_for(username)
    row = con.execute("SELECT last_id FROM state WHERE chat_id=?", (cid,)).fetchone()
    last = row["last_id"] if row else 0
    url, title, newest, new, seen_any = f"https://t.me/s/{username}", username, last, 0, False
    for _ in range(NEXT_PAGES if last else FIRST_PAGES):
        title_p, posts = parse_page(get_text(url))
        if not posts:
            break
        seen_any, title = True, title_p or title
        for p in posts:
            newest = max(newest, p["id"])
            if p["id"] <= last or len(p["text"]) < MIN_TEXT or not p["date"]:
                continue
            a = parse.analyze(p["text"])
            if a["score"] < MIN_SCORE:        # chatter, ads for something else: the public database keeps only what matches a role
                continue
            new += db.upsert(con, a, p["text"], {"chat": title, "chat_id": cid, "msg_id": p["id"], "date": p["date"],
                                                 "url": f"https://t.me/{username}/{p['id']}"})
        oldest = min(p["id"] for p in posts)
        if oldest <= last + 1 or oldest <= 1:
            break
        url = f"https://t.me/s/{username}?before={oldest}"
        sleep(GAP)
    if not seen_any:
        return {"status": "no-preview"}
    con.execute("INSERT INTO chats(chat_id,username,title) VALUES(?,?,?) "
                "ON CONFLICT(chat_id) DO UPDATE SET username=excluded.username, title=excluded.title", (cid, username, title))
    con.execute("INSERT INTO state(chat_id,last_id,title) VALUES(?,?,?) "
                "ON CONFLICT(chat_id) DO UPDATE SET last_id=excluded.last_id, title=excluded.title", (cid, newest, title))
    con.commit()
    return {"status": "ok", "new": new}


def run(con=None, channels: list[str] | None = None, get_text=None, sleep=time.sleep, now=time.time) -> dict:
    con = con or db.connect()
    get_text = get_text or (lambda u: http.get_text(u, headers={"User-Agent": "Mozilla/5.0 (jobboard; personal job search)"}))
    names = channels if channels is not None else mode.public_telegram_channels()
    stats = {"channels": 0, "new_jobs": 0, "no_preview": [], "failed": []}
    for i, username in enumerate(names):
        try:
            r = read_channel(con, username, get_text, sleep)
        except http.RateLimited:
            stats["failed"].append(f"{username}: rate limited, stopped")
            break
        except Exception as exc:          # one odd channel (bad HTML, timeout, ...) must not stop the others
            con.rollback()
            stats["failed"].append(f"{username}: {type(exc).__name__}")
            continue
        finally:
            if i + 1 < len(names):
                sleep(GAP)
        if r["status"] == "ok":
            stats["channels"] += 1
            stats["new_jobs"] += r["new"]
        else:
            stats["no_preview"].append(username)
    err = f"{len(stats['failed'])} failed: {stats['failed'][0]}" if stats["failed"] and not stats["channels"] else None
    db.record_source(con, "Telegram (public)", stats["channels"], error=err, now=now())
    con.commit()
    return stats
