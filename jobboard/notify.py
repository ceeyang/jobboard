"""Daily digest sent to your own Telegram 'Saved Messages'."""
import json

from jobboard import links

MIN_SCORE = 5
MAX_ITEMS = 20  # a daily message people actually read; the rest stays on the board
BOARD_URL = "http://127.0.0.1:8765"


def pending(con, min_score: int = MIN_SCORE):
    return con.execute(
        "SELECT * FROM jobs WHERE notified=0 AND status='new' AND score>=? ORDER BY score DESC, posted_at DESC",
        (min_score,)).fetchall()


def format_digest(rows, names: dict | None = None) -> list[str]:
    if not rows:
        return []
    total, rows = len(rows), rows[:MAX_ITEMS]
    lines = [f"📋 今日岗位 {total} 条（匹配 Flutter/iOS/Android/前端），按匹配度显示前 {len(rows)} 条\n"]
    for r in rows:
        c = json.loads(r["contacts"])
        srcs = json.loads(r["sources"])
        src = ", ".join(dict.fromkeys(s["chat"] for s in srcs))
        url = next((u for u in (s.get("url") or links.post_url(s["chat_id"], (names or {}).get(s["chat_id"]), s["msg_id"])
                                for s in srcs) if u), None)
        who = " ".join(c["telegram"] + c["email"]) or (c["links"][0] if c["links"] else "（正文无联系方式）")
        risk = json.loads(r["risks"])
        co = f" @ {r['company']}" if r["company"] else ""
        geo = {"restricted": "  🚧限地区", "worldwide": "  🌍全球"}.get(r["geo"], "")
        lines.append(f"• {r['title']}{co}  {r['salary'] or ''}{'  🏠远程' if r['remote'] else ''}{geo}\n"
                     f"  📣 {src}\n  👤 {who}" + (f"\n  🔗 {url}" if url and url != who else "") + (f"\n  ⚠️ {'；'.join(risk)}" if risk else ""))
    if total > len(rows):
        lines.append(f"\n…另有 {total - len(rows)} 条，见看板")
    lines.append(f"\n看板：{BOARD_URL}")
    chunks, cur = [], ""
    for ln in lines:  # Telegram caps messages at 4096 chars
        if len(cur) + len(ln) + 1 > 3800:
            chunks.append(cur)
            cur = ""
        cur += ln + "\n"
    return chunks + [cur]


def send(con, client=None, min_score: int = MIN_SCORE) -> int:
    from cli_anything.telegram.utils import telegram_backend as backend
    rows = pending(con, min_score)
    chunks = format_digest(rows, _names(con))
    if not chunks:
        return 0
    own = client is None
    client = client or backend.connected_client()
    try:
        for ch in chunks:
            client.send_message("me", ch, link_preview=False)
    finally:
        if own:
            client.disconnect()
    con.executemany("UPDATE jobs SET notified=1 WHERE id=?", [(r["id"],) for r in rows])
    con.commit()
    return len(rows)


def _names(con) -> dict:
    return {r["chat_id"]: r["username"] for r in con.execute("SELECT chat_id, username FROM chats")}
