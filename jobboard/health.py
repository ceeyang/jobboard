"""Is every source still doing its job? A source can break silently (a site changes its HTML, an endpoint disappears) and the
rest of the pipeline keeps publishing stale data without anything turning red. This module turns the per-source history kept in
`source_state` into a verdict that the status page shows and the workflow can fail on.

    ok      works
    warn    worth a look (failed or came back empty a couple of times, or has not succeeded for a day)
    alert   broken: repeated errors, or it used to return jobs and has been empty for many runs
    never   has run but has never returned a single job (a source that was added but does not work)

GitHub starts scheduled runs every 4-8 hours, so "runs in a row" translates to roughly: 3 runs = 12-24 h, 6 runs = 1-2 days."""
from datetime import datetime, timezone

ERROR_WARN, ERROR_ALERT = 1, 3
ZERO_WARN, ZERO_ALERT = 3, 6
STALE_WARN_H, STALE_ALERT_H = 30, 72


def _iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts else None


def judge(row: dict, now: float) -> tuple[str, str]:
    """-> (state, plain-language reason) for one source_state row."""
    err, zero, mx = row.get("error_runs") or 0, row.get("zero_runs") or 0, row.get("max_count") or 0
    last_ok = row.get("last_ok")
    age_h = (now - last_ok) / 3600 if last_ok else None
    if err >= ERROR_ALERT:
        return "alert", f"连续 {err} 次运行出错：{(row.get('last_error') or '')[:80]}"
    if mx and zero >= ZERO_ALERT:
        return "alert", f"以前能抓到岗位，已连续 {zero} 次运行返回 0 条"
    if age_h is not None and age_h >= STALE_ALERT_H:
        return "alert", f"已 {age_h / 24:.0f} 天没有成功运行"
    if not mx and (row.get("last_run") or 0) and zero >= ZERO_WARN:
        return "never", "运行正常但从未抓到过任何岗位（可能没有配置内容或已失效）"
    if err >= ERROR_WARN:
        return "warn", f"最近一次运行出错：{(row.get('last_error') or '')[:80]}"
    if mx and zero >= ZERO_WARN:
        return "warn", f"连续 {zero} 次运行返回 0 条"
    if age_h is not None and age_h >= STALE_WARN_H:
        return "warn", f"已 {age_h:.0f} 小时没有成功运行"
    return "ok", ""


def status_rows(con, now: float) -> list[dict]:
    """The per-source section of meta.json / status.json."""
    out = []
    for r in con.execute("SELECT * FROM source_state ORDER BY name"):
        row = dict(r)
        state, why = judge(row, now)
        out.append({"name": row["name"], "last_count": row["last_count"] or 0, "ok": not row["last_error"], "error": (row["last_error"] or "")[:120],
                    "state": state, "why": why, "last_ok": _iso(row.get("last_ok")), "zero_runs": row.get("zero_runs") or 0,
                    "error_runs": row.get("error_runs") or 0, "max_count": row.get("max_count") or 0})
    return out


def summary(rows: list[dict]) -> dict:
    """What the workflow prints and decides on: any alert makes the run fail (after the site has been deployed)."""
    rows = [{"state": "ok", "why": "", **r} for r in rows]       # a feed built before the health check existed has no verdicts
    by = {s: [r for r in rows if r["state"] == s] for s in ("alert", "warn", "never", "ok")}
    return {"alert": bool(by["alert"]), "counts": {k: len(v) for k, v in by.items()},
            "problems": [{"name": r["name"], "state": r["state"], "why": r["why"]} for k in ("alert", "warn", "never") for r in by[k]]}
