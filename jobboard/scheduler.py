"""Settings + the decisions of the built-in scheduler (kept pure so they can be tested)."""
import json
import re
from datetime import datetime

DEFAULTS = {
    "refresh_minutes": 60,      # 0 = off (manual refresh only)
    "digest_enabled": False,    # opt-in: it sends one message to your own Saved Messages
    "digest_time": "09:00",
    "last_fetch_started": 0.0,
    "last_digest_date": "",
    "saved_searches": [],
    "linkedin_locations": ["Remote"],   # places the LinkedIn guest search looks in
}
MIN_REFRESH, MAX_REFRESH = 5, 1440
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def load(con) -> dict:
    out = dict(DEFAULTS)
    for r in con.execute("SELECT key, value FROM settings"):
        if r["key"] in DEFAULTS:
            out[r["key"]] = json.loads(r["value"])
    return out


def save(con, patch: dict) -> None:
    for k, v in patch.items():
        con.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (k, json.dumps(v, ensure_ascii=False)))
    con.commit()


def validate(patch: dict) -> dict:
    """Only user-editable keys, with clear errors. Scheduler bookkeeping can't be set from outside."""
    out = {}
    if "refresh_minutes" in patch:
        try:
            m = int(patch["refresh_minutes"])
        except (TypeError, ValueError):
            raise ValueError("刷新间隔必须是整数分钟")
        if m != 0 and not MIN_REFRESH <= m <= MAX_REFRESH:
            raise ValueError(f"刷新间隔需为 0（关闭）或 {MIN_REFRESH}–{MAX_REFRESH} 分钟")
        out["refresh_minutes"] = m
    if "digest_enabled" in patch:
        out["digest_enabled"] = bool(patch["digest_enabled"])
    if "digest_time" in patch:
        if not isinstance(patch["digest_time"], str) or not _HHMM.match(patch["digest_time"]):
            raise ValueError("摘要时间格式应为 HH:MM，例如 09:00")
        out["digest_time"] = patch["digest_time"]
    if "linkedin_locations" in patch:
        places = patch["linkedin_locations"]
        if not isinstance(places, list) or not 1 <= len(places) <= 5 \
                or not all(isinstance(p, str) and 1 <= len(p.strip()) <= 60 for p in places):
            raise ValueError("LinkedIn 地点需要 1–5 个，每个不超过 60 个字符")
        out["linkedin_locations"] = [p.strip() for p in places]
    if "saved_searches" in patch:
        items = patch["saved_searches"]
        if not isinstance(items, list) or len(items) > 50:
            raise ValueError("常用搜索最多 50 条")
        clean = []
        for it in items:
            name, q = str((it or {}).get("name", "")).strip(), str((it or {}).get("q", "")).strip()
            if not name or not q:
                raise ValueError("常用搜索需要名称和搜索词")
            clean.append({"name": name[:30], "q": q[:200]})
        out["saved_searches"] = clean
    return out


def fetch_due(s: dict, now: float, running: bool) -> bool:
    m = s["refresh_minutes"]
    return m > 0 and not running and now - s["last_fetch_started"] >= m * 60


def next_fetch_at(s: dict) -> float | None:
    return s["last_fetch_started"] + s["refresh_minutes"] * 60 if s["refresh_minutes"] > 0 else None


def digest_due(s: dict, now: datetime) -> bool:
    return (s["digest_enabled"] and s["last_digest_date"] != now.strftime("%Y-%m-%d")
            and now.strftime("%H:%M") >= s["digest_time"])
