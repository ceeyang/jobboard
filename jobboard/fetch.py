"""Pull new messages from every group/channel and store analyzed jobs. Read-only toward Telegram."""
import fcntl
import time
from datetime import datetime, timedelta, timezone

from jobboard import db, parse


def _lock():
    f = open(db.HOME / "fetch.lock", "w")
    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)  # raises if another fetch is running
    return f


def run(backfill_days: int = 2, per_chat_limit: int = 300, client=None, con=None) -> dict:
    from cli_anything.telegram.utils import telegram_backend as backend
    from cli_anything.telegram.core.serialize import message_to_dict

    con = con or db.connect()
    db.HOME.mkdir(parents=True, exist_ok=True)
    try:
        lock = _lock()
    except BlockingIOError:
        raise RuntimeError("Another fetch is already running.")
    own = client is None
    client = client or backend.connected_client()
    since = datetime.now(timezone.utc) - timedelta(days=backfill_days)
    stats = {"chats": 0, "messages": 0, "new_jobs": 0, "capped": []}
    try:
        for d in client.iter_dialogs():
            if not (d.is_group or d.is_channel):
                continue
            con.execute("INSERT INTO chats(chat_id,username,title) VALUES(?,?,?) "
                        "ON CONFLICT(chat_id) DO UPDATE SET username=excluded.username, title=excluded.title",
                        (d.id, getattr(d.entity, "username", None), d.name))
            st = con.execute("SELECT last_id FROM state WHERE chat_id=?", (d.id,)).fetchone()
            last_id = st["last_id"] if st else 0
            newest, n = last_id, 0
            for m in client.iter_messages(d.entity, limit=per_chat_limit, min_id=last_id):
                if not last_id and m.date < since:
                    break  # first run: only backfill a few days
                newest = max(newest, m.id)
                n += 1
                text = (m.message or "").strip()
                if len(text) < 15:
                    continue
                a = parse.analyze(text)
                if db.upsert(con, a, text, {"chat": d.name, "chat_id": d.id, "msg_id": m.id,
                                            "date": m.date.isoformat()}):
                    stats["new_jobs"] += 1
            if n >= per_chat_limit:
                stats["capped"].append(d.name)
            con.execute("INSERT INTO state(chat_id,last_id,title) VALUES(?,?,?) "
                        "ON CONFLICT(chat_id) DO UPDATE SET last_id=excluded.last_id, title=excluded.title",
                        (d.id, newest, d.name))
            con.commit()
            stats["chats"] += 1
            stats["messages"] += n
            time.sleep(0.4)  # be gentle: avoid FloodWait
    finally:
        if own:
            client.disconnect()
        lock.close()
    return stats
