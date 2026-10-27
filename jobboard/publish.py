"""Turn the job database into a static, public-safe site (GitHub Pages).
What is published: title, company, location, salary, tags, a <=600 character excerpt and the links back to the
original posting. What is never published: contact handles/e-mails, full texts, your statuses and notes, anything from
Telegram. A final self-check fails the build if an e-mail or @handle is still present."""
import html
import json
import os
import re
import shutil
import time
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from jobboard import db, health, mode, parse, profile_config, roles

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


# ---- site configuration (where the site lives) ---------------------------------------------------------------
def site_config(use_file: bool = True) -> dict:
    """{"site_url": "https://host/path/", "sites_base": "https://host"}: lets the feeds carry absolute links and the overview link
    to the role sites. Environment variables win over config/site.json (a role site must not inherit the engine's own URL)."""
    cfg: dict = {}
    f = ROOT.parent / "config" / "site.json"
    if use_file and f.exists():
        try:
            cfg = json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            cfg = {}
    for key, env in (("site_url", "JOBBOARD_SITE_URL"), ("sites_base", "JOBBOARD_SITES_BASE")):
        if os.environ.get(env):
            cfg[key] = os.environ[env]
    return cfg


# ---- the overview page: numbers computed once at export time ----------------------------------------------------
STATS_NOTE = "统计只反映本站收集到的来源和时间窗口内的岗位，不代表整个招聘市场；各来源的回填深度不同，趋势仅供参考。"


def _eff_ts(j: dict, now_ts: float) -> float:
    """When it was posted, as a number. Sources that only know the day (stored as 00:00 UTC) count as 'some time that day'."""
    t = db._ts(j["posted_at"]) or 0.0
    return min(t + 86399, now_ts) if str(j["posted_at"]).endswith("T00:00:00+00:00") else t


def _families(cfg: dict | None, sites_base: str | None) -> list[dict] | None:
    """For the aggregate profile: group its roles into the role families that have their own site."""
    if not cfg or cfg.get("name") != "all-tech":
        return None
    from jobboard import orgkit
    out = []
    for name in orgkit.PROFILE_PRESETS:
        pr = profile_config.validate(profile_config.load_preset(name))
        label = re.sub(r"\s*(岗位)?看板$", "", pr["site"]["title"])
        out.append({"key": name, "label": label, "keys": {r["key"] for r in pr["roles"]}, "url": f"{sites_base.rstrip('/')}/{name}/" if sites_base else None})
    return out


def _groups(cfg: dict | None, chips: list[dict], sites_base: str | None) -> list[dict]:
    """The things a visitor can subscribe to / filter by: role families on the aggregate site, the profile's own roles elsewhere."""
    return _families(cfg, sites_base) or [{"key": c["key"], "label": c["label"], "keys": {c["key"]}, "url": None} for c in chips]


def compute_stats(jobs: list[dict], now: datetime, cfg: dict | None, chips: list[dict], level: int, sites_base: str | None = None) -> dict:
    now_ts = now.timestamp()
    eff = {j["id"]: _eff_ts(j, now_ts) for j in jobs}
    n = len(jobs)
    week = sum(now_ts - eff[j["id"]] < 7 * 86400 for j in jobs)
    prev = sum(7 * 86400 <= now_ts - eff[j["id"]] < 14 * 86400 for j in jobs)
    totals = {"jobs": n, "new_24h": sum(now_ts - eff[j["id"]] < 86400 for j in jobs), "new_7d": week, "prev_7d": prev,
              "remote_pct": round(100 * sum(1 for j in jobs if j["remote"]) / n) if n else 0,
              "visa_yes": sum(1 for j in jobs if j["visa"] == "yes"), "sources": len({s["chat"] for j in jobs for s in j["sources"]})}
    fams = _families(cfg, sites_base)
    if fams:
        items = [{"key": f["key"], "label": f["label"], "url": f["url"], "count": sum(1 for j in jobs if f["keys"] & set(j["tags"]))} for f in fams]
        dist = {"title": "方向分布", "kind": "families", "items": sorted(items, key=lambda i: -i["count"])}
    else:
        label = {c["key"]: c["label"] for c in chips}
        cnt: dict[str, int] = {}
        for j in jobs:
            for t in set(j["tags"]):
                cnt[t] = cnt.get(t, 0) + 1
        dist = {"title": "方向分布", "kind": "roles", "items": [{"key": k, "label": label.get(k, k), "url": None, "count": v}
                                                              for k, v in sorted(cnt.items(), key=lambda kv: -kv[1])[:14]]}
    today = now.date()
    daily = [{"date": (today - timedelta(days=d)).isoformat(),
              "count": sum(1 for j in jobs if datetime.fromtimestamp(eff[j["id"]], timezone.utc).date() == today - timedelta(days=d))} for d in range(13, -1, -1)]
    label_of = {c["key"]: c["label"] for c in chips}
    picks, per_role, per_company = [], {}, set()
    for window in (2, 7):      # the last two days first; widen if a quiet period leaves fewer than eight
        for j in sorted((j for j in jobs if j["score"] >= level and now_ts - eff[j["id"]] < window * 86400 and j["id"] not in {p["id"] for p in picks}),
                        key=lambda j: (-j["score"], -eff[j["id"]])):
            role, comp = (j["tags"] or ["-"])[0], (j["company"] or "").lower()
            if per_role.get(role, 0) >= 2 or (comp and comp in per_company):
                continue                                    # variety: at most two per role, one per company
            picks.append({"id": j["id"], "title": j["title"], "company": j["company"], "location": j["location"], "remote": j["remote"],
                          "visa": j["visa"], "score": j["score"], "posted_at": j["posted_at"], "url": j["sources"][0]["url"],
                          "source": j["sources"][0]["chat"], "tags": [label_of.get(t, t) for t in dict.fromkeys(j["tags"])][:3]})
            per_role[role] = per_role.get(role, 0) + 1
            per_company.add(comp)
            if len(picks) == 8:
                break
        if len(picks) == 8:
            break
    by_company: dict[str, list[dict]] = {}
    for j in jobs:
        c = (j["company"] or "").strip()
        if j["visa"] == "yes" and c and "/" not in c and c != j["sources"][0]["chat"]:
            by_company.setdefault(c, []).append(j)
    employers = [{"company": c, "count": len(js), "jobs": [{"id": x["id"], "title": x["title"], "url": x["sources"][0]["url"]} for x in
                                                          sorted(js, key=lambda x: -eff[x["id"]])[:3]]}
                 for c, js in sorted(by_company.items(), key=lambda kv: (-len(kv[1]), kv[0].lower()))[:15]]
    kind_label = {"site": "招聘网站", "gov": "政府就业服务", "github": "GitHub 招聘仓库", "telegram": "Telegram 频道"}
    mix = [{"key": k, "label": kind_label[k], "count": sum(1 for j in jobs if any(s["kind"] == k for s in j["sources"]))} for k in kind_label]
    return {"generated_at": now.isoformat(), "totals": totals, "distribution": dist, "daily": daily, "picks": picks,
            "visa_employers": employers, "source_mix": [m for m in mix if m["count"]], "note": STATS_NOTE}


# ---- feeds ----------------------------------------------------------------------------------------------------------
def write_feeds(out: Path, jobs: list[dict], now: datetime, site: dict, site_url: str, level: int, limit: int = 100, rel: str = "feed") -> int:
    now_ts = now.timestamp()
    items = sorted((j for j in jobs if j["score"] >= level), key=lambda j: -_eff_ts(j, now_ts))[:limit]
    base = site_url or "./"
    title = lambda j: f"{j['company']}: {j['title']}" if j["company"] and j["company"] != j["title"] else j["title"]
    rss = ['<?xml version="1.0" encoding="UTF-8"?>', '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom"><channel>',
           f"<title>{xml_escape(site['title'])}</title><link>{xml_escape(base)}</link>",
           f"<description>{xml_escape(site.get('description') or site['title'])}</description><language>zh-CN</language>",
           f"<lastBuildDate>{format_datetime(now)}</lastBuildDate>"]
    if site_url:
        rss.append(f'<atom:link href="{xml_escape(site_url)}{rel}.xml" rel="self" type="application/rss+xml"/>')
    for j in items:
        when = datetime.fromtimestamp(_eff_ts(j, now_ts), timezone.utc)
        rss.append(f"<item><title>{xml_escape(title(j))}</title><link>{xml_escape(j['sources'][0]['url'])}</link>"
                   f'<guid isPermaLink="false">{j["id"]}</guid><pubDate>{format_datetime(when)}</pubDate>'
                   f"<description>{xml_escape(j['text'])}</description>"
                   + "".join(f"<category>{xml_escape(t)}</category>" for t in dict.fromkeys(j["tags"])) + "</item>")
    rss.append("</channel></rss>")
    (out / f"{rel}.xml").parent.mkdir(parents=True, exist_ok=True)
    (out / f"{rel}.xml").write_text("\n".join(rss), encoding="utf-8")
    feed = {"version": "https://jsonfeed.org/version/1.1", "title": site["title"], "home_page_url": base,
            "description": site.get("description") or site["title"], "language": "zh-CN",
            **({"feed_url": site_url + f"{rel}.json"} if site_url else {}),
            "items": [{"id": str(j["id"]), "url": j["sources"][0]["url"], "title": title(j), "content_text": j["text"],
                       "date_published": datetime.fromtimestamp(_eff_ts(j, now_ts), timezone.utc).isoformat(), "tags": list(dict.fromkeys(j["tags"]))}
                      for j in items]}
    (out / f"{rel}.json").write_text(json.dumps(feed, ensure_ascii=False), encoding="utf-8")
    return len(items)


def write_site(out: str | Path, jobs: list[dict], *, now: datetime, window_days: int, site: dict, chats: list[dict],
               chips: list[dict], state: list[dict], extra_meta: dict | None = None, cfg: dict | None = None,
               levels: dict | None = None, site_cfg: dict | None = None) -> dict:
    """Write the static site (page, shim, overview, feeds, paged data files). Both `export` (from the database) and `build`
    (from another site's feed) end here, so the privacy self-check below applies to everything that is ever published."""
    out = Path(out)
    (out / "data").mkdir(parents=True, exist_ok=True)
    levels = levels or {"recommended": 5, "strict": 10}
    site_cfg = site_cfg if site_cfg is not None else site_config()
    # best matches first: the first chunk is what the default view (recommended level, "most relevant") needs
    jobs = sorted(jobs, key=lambda j: (-j["score"], -(db._ts(j["posted_at"]) or 0)))
    chunks = [jobs[i:i + CHUNK] for i in range(0, len(jobs), CHUNK)] or [[]]
    for stale in (out / "data").glob("jobs-*.json"):
        stale.unlink()
    stats = compute_stats(jobs, now, cfg, chips, levels["recommended"], site_cfg.get("sites_base"))
    blob = json.dumps({"jobs": jobs, "stats": stats}, ensure_ascii=False)
    leaks = [m.group(0) for m in (_EMAIL.search(blob), _HANDLE.search(re.sub(r"https?://\S+", "", blob))) if m]
    if leaks:
        raise RuntimeError(f"refusing to publish: contact data found in export ({leaks[0]!r})")
    head = {"generated_at": now.isoformat(), "total": len(jobs), "chunks": len(chunks), "chunk_size": CHUNK, "jobs": chunks[0]}
    (out / "data/jobs.json").write_text(json.dumps(head, ensure_ascii=False), encoding="utf-8")      # index + first chunk
    for i, c in enumerate(chunks[1:], 1):
        (out / f"data/jobs-{i}.json").write_text(json.dumps({"jobs": c}, ensure_ascii=False), encoding="utf-8")
    site_url = (site_cfg.get("site_url") or "").rstrip("/") + "/" if site_cfg.get("site_url") else ""
    for stale in (out / "feeds").glob("*") if (out / "feeds").exists() else []:
        stale.unlink()
    groups = [g for g in _groups(cfg, chips, site_cfg.get("sites_base")) if any(g["keys"] & set(j["tags"]) for j in jobs)]
    for g in groups:      # one subscription per role family / role, so a visitor only follows what they care about
        write_feeds(out, [j for j in jobs if g["keys"] & set(j["tags"])], now, {**site, "title": f"{site['title']} · {g['label']}"},
                    site_url, levels["recommended"], limit=60, rel=f"feeds/{g['key']}")
    stats["feeds"] = [{"key": g["key"], "label": g["label"]} for g in groups]
    (out / "data/stats.json").write_text(json.dumps(stats, ensure_ascii=False), encoding="utf-8")
    feed_items = write_feeds(out, jobs, now, site, site_url, levels["recommended"])
    counts = _count_chats(jobs)
    chats = sorted(({**c, "jobs": counts.get(c["chat_id"], 0)} for c in chats), key=lambda c: (-c["jobs"], c["title"]))
    (out / "data/channels.json").write_text(json.dumps({"chats": chats}, ensure_ascii=False), encoding="utf-8")
    (out / "data/roles.json").write_text(json.dumps({"roles": chips}, ensure_ascii=False), encoding="utf-8")
    meta = {"generated_at": now.isoformat(), "jobs": len(jobs), "chunks": len(chunks), "window_days": window_days, "sources": state,
            "site": site, "levels": levels, "feed_items": feed_items, **(extra_meta or {})}
    (out / "data/meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    page = (ROOT / "templates/app.html").read_text(encoding="utf-8")
    page = page.replace("<script>\nconst STATUS=", '<script src="shim.js"></script>\n<script src="overview.js"></script>\n<script>\nconst STATUS=', 1)
    page = page.replace("function nextRunText(m,next){", "function nextRunText(m,next){if(window.JB_STATIC)return window.JB_STATIC.label();", 1)
    title_html = html.escape(site["title"])
    page = page.replace("<title>岗位看板</title>", f'<title>{title_html}</title>\n<link rel="alternate" type="application/rss+xml" title="{title_html}" href="feed.xml">', 1)
    page = page.replace('<span class="bname">岗位看板</span>', f'<span class="bname">{title_html}</span>', 1)
    rec, strict = levels["recommended"], levels["strict"]
    for old, new in (('const LEVELS=[[1,"宽松"],[5,"推荐"],[10,"严格"]];', f'const LEVELS=[[1,"宽松"],[{rec},"推荐"],[{strict},"严格"]];'),
                     ('status:"all",min:5,since:""', f'status:"all",min:{rec},since:""')):
        if old not in page:
            raise RuntimeError("app.html changed: the level patch points no longer match")
        page = page.replace(old, new, 1)
    if 'src="shim.js"' not in page or "JB_STATIC" not in page:
        raise RuntimeError("app.html changed: the static-site patch points no longer match")
    (out / "index.html").write_text(page, encoding="utf-8")
    (out / "about.html").write_text(about_html(site, state, now, site_cfg, levels), encoding="utf-8")
    (out / "status.json").write_text(json.dumps({"generated_at": now.isoformat(), **health.summary(state), "sources": state}, ensure_ascii=False), encoding="utf-8")
    shutil.copy(ROOT / "static/page.css", out / "page.css")
    shutil.copy(ROOT / "static/shim.js", out / "shim.js")
    shutil.copy(ROOT / "static/overview.js", out / "overview.js")
    (out / ".nojekyll").write_text("")
    return meta


_BADGE = {"ok": ("正常", "ok"), "warn": ("注意", "warn"), "alert": ("异常", "bad"), "never": ("从未抓到", "mute")}


def about_html(site: dict, state: list[dict], now: datetime, site_cfg: dict, levels: dict) -> str:
    """A plain page that answers the questions a visitor (or the owner) will have: where does this come from, is it healthy, how
    often does it update, how do I get something removed."""
    e = html.escape
    order = {"alert": 0, "warn": 1, "never": 2, "ok": 3}
    rows = []
    for r in sorted(state, key=lambda r: (order.get(r.get("state", "ok"), 3), r["name"].lower())):
        label, cls = _BADGE.get(r.get("state", "ok"), _BADGE["ok"])
        when = (r.get("last_ok") or "")[:16].replace("T", " ")
        rows.append(f'<tr><td>{e(r["name"])}</td><td><span class="pill {cls}">{label}</span></td><td>{e(when + " UTC" if when else "—")}</td>'
                    f'<td class="num">{r.get("last_count", 0)}</td><td>{e(r.get("why") or r.get("error") or "")}</td></tr>')
    summary = health.summary(state)["counts"]
    take = site_cfg.get("takedown_url")
    take_html = (f'<a href="{e(take)}" rel="noopener">在这里提交下架申请</a>' if take else "请通过本站所在代码仓库的 Issues 联系我们")
    return (f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>关于与数据状态 · {e(site["title"])}</title><link rel="stylesheet" href="page.css"></head><body><main class="doc">'
            f'<a class="back" href="./">← 返回 {e(site["title"])}</a><h1>关于本站与数据状态</h1>'
            f'<p class="lead">{e(site.get("description") or "")}</p>'
            f'<h2 id="status">数据状态</h2><p>共 {len(state)} 个来源：正常 {summary["ok"]}，注意 {summary["warn"]}，异常 {summary["alert"]}，从未抓到 {summary["never"]}。'
            f'本页生成于 {e(now.strftime("%Y-%m-%d %H:%M"))} UTC。</p>'
            f'<div class="tw"><table><thead><tr><th>来源</th><th>状态</th><th>最近成功</th><th class="num">最近条数</th><th>说明</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>'
            f'<h2 id="freq">更新频率</h2><p>数据由 GitHub Actions <b>自动更新</b>，但 GitHub 的定时触发<b>并不准点</b>：近期相邻两次运行相隔约 4–8 小时，'
            f'有时更久。方向站读取聚合站的数据，所以可能比聚合站再慢一个周期。页面右上角和本页都会显示数据的生成时间。</p>'
            f'<h2 id="data">数据与隐私</h2><ul><li>只显示岗位摘要（不超过 600 字）和指向原帖的链接，申请请到原帖。</li>'
            f'<li>不显示任何联系方式（邮箱、@用户名、t.me 链接），导出时有自动检查。</li>'
            f'<li>"推荐"的分数线是 {levels["recommended"]}，"严格"是 {levels["strict"]}；匹配是关键词判断，会有误差。</li>'
            f'<li>统计只反映本站收集到的来源和时间窗口内的岗位，不代表整个招聘市场。</li></ul>'
            f'<h2 id="takedown">下架与屏蔽</h2><p>如果你是某个频道、公司或岗位的所有者，希望它不再出现在这里：{take_html}，'
            f'写明频道名、公司名或岗位链接即可。我们维护一份屏蔽名单（频道、公司、网站、岗位 ID），名单中的内容会在下一次更新时从站点和订阅中消失。</p>'
            f'</main></body></html>')


def export(con, out: str | Path, now: datetime | None = None, window_days: int = WINDOW_DAYS) -> dict:
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=window_days)).isoformat()
    jobs = []
    bl = mode.blocklist()
    for r in con.execute("SELECT * FROM jobs WHERE score>=1 AND julianday(posted_at)>=julianday(?) ORDER BY julianday(posted_at) DESC", (cutoff,)):
        j = job_to_public(r)
        if j and not mode.is_blocked(j, bl):
            jobs.append(j)
    listed = set(mode.public_telegram_channels())
    chats = [{"chat_id": r["chat_id"], "title": r["title"] or r["username"], "username": r["username"], "url": f"https://t.me/{r['username']}"}
             for r in con.execute("SELECT chat_id, username, title FROM chats") if (r["username"] or "").lower() in listed]
    cfg = profile_config.current()
    chips = profile_config.roles_for_ui(cfg) if cfg else roles.list_roles(con)     # the profile decides which role buttons exist
    state = health.status_rows(con, now.timestamp())
    site = (cfg or {}).get("site") or {"title": "岗位看板", "description": ""}
    return write_site(out, jobs, now=now, window_days=window_days, site=site, chats=chats, chips=chips, state=state, cfg=cfg,
                      levels=(cfg or {}).get("levels"))


# ---- build a site from another site's feed (what the profile repositories do) ---------------------------------
def _read(base: str, name: str):
    """base is a URL (https://host/path/data) or a local folder; returns the parsed JSON file."""
    if re.match(r"https?://", base):
        from jobboard.sources import http
        last = None
        for attempt in range(3):
            try:
                return http.get_json(f"{base.rstrip('/')}/{name}?t={int(time.time())}", timeout=60)   # ?t= defeats the CDN cache right after a deploy
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
    bl = mode.blocklist()
    for j in feed["jobs"]:
        if mode.is_blocked(j, bl):
            continue
        a = parse.analyze(j["text"], j.get("hint", ""), structured=j.get("structured", True))
        if a["score"] >= 1:
            kept.append({**j, "score": a["score"], "tags": a["tags"]})
    meta = feed["meta"]
    return write_site(out, kept, now=datetime.fromisoformat(feed["generated_at"]), window_days=meta.get("window_days", WINDOW_DAYS),
                      site=cfg["site"], chats=feed["channels"], chips=profile_config.roles_for_ui(cfg), state=meta.get("sources", []),
                      cfg=cfg, levels=cfg["levels"], site_cfg=site_config(use_file=False),
                      extra_meta={"feed": {"jobs": len(feed["jobs"]), "kept": len(kept), "built_at": (now or datetime.now(timezone.utc)).isoformat()}})
