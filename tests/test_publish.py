"""The public site must never leak contacts, full texts, private sources or personal state."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from jobboard import cli, db, mode, parse, profile, publish


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "HOME", tmp_path / "home")
    profile.set_rules(None)
    yield db.connect()
    profile.set_rules(None)


def add(con, title, text, sources, days_ago=1, fp=None):
    a = parse.analyze(text)
    a.update(title=title, company="Acme", fingerprint=parse.fingerprint(fp or title))
    for i, (chat, chat_id, url) in enumerate(sources):
        date = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
        src = {"chat": chat, "chat_id": chat_id, "msg_id": i + 1, "date": date, **({"url": url} if url else {})}
        db.upsert(con, a, text, src)
    con.commit()


LONG = "Flutter developer wanted. Contact hr@example.com or @some_recruiter or t.me/some_recruiter. " + "x " * 600


def test_export_keeps_only_public_sources_and_no_contacts(isolated, tmp_path):
    add(isolated, "Flutter Dev A", LONG, [("RemoteOK", -1, "https://remoteok.com/a"), ("LinkedIn", -19, "https://linkedin.com/jobs/view/1")])
    add(isolated, "Flutter Dev B", LONG, [("LinkedIn", -19, "https://linkedin.com/jobs/view/2")])
    add(isolated, "Flutter Dev C", LONG, [("某招聘频道", -1001234567890, None)])
    add(isolated, "Flutter Dev D", LONG, [("Adzuna", -15, "https://adzuna.com/x")])
    meta = publish.export(isolated, tmp_path / "site")
    jobs = json.loads((tmp_path / "site/data/jobs.json").read_text(encoding="utf-8"))["jobs"]
    assert meta["jobs"] == 1 and [j["title"] for j in jobs] == ["Flutter Dev A"]
    j = jobs[0]
    assert [s["chat"] for s in j["sources"]] == ["RemoteOK"] and j["contacts"] == {"telegram": [], "email": [], "links": ["https://remoteok.com/a"]}
    assert len(j["text"]) <= 601 and "hr@example.com" not in j["text"] and "@some_recruiter" not in j["text"] and "t.me" not in j["text"]
    assert isinstance(j["id"], int) and j["id"] < 2 ** 53 and j["status"] == "new"
    blob = (tmp_path / "site/data/jobs.json").read_text(encoding="utf-8")
    assert "linkedin" not in blob.lower() and "adzuna" not in blob.lower()


def test_ids_are_stable_and_window_applies(isolated, tmp_path):
    add(isolated, "Flutter Dev New", "Flutter developer", [("RemoteOK", -1, "https://r/1")], days_ago=2)
    add(isolated, "Flutter Dev Old", "Flutter developer", [("RemoteOK", -1, "https://r/2")], days_ago=60)
    publish.export(isolated, tmp_path / "a")
    publish.export(isolated, tmp_path / "b")
    a = json.loads((tmp_path / "a/data/jobs.json").read_text())["jobs"]
    b = json.loads((tmp_path / "b/data/jobs.json").read_text())["jobs"]
    assert [j["title"] for j in a] == ["Flutter Dev New"] and a[0]["id"] == b[0]["id"]


def test_site_files_are_written_and_patched(isolated, tmp_path):
    add(isolated, "Flutter Dev", "Flutter developer", [("RemoteOK", -1, "https://r/1")])
    publish.export(isolated, tmp_path / "site")
    page = (tmp_path / "site/index.html").read_text(encoding="utf-8")
    assert page.index('src="shim.js"') < page.index("const STATUS=") and "JB_STATIC.label()" in page
    assert (tmp_path / "site/shim.js").exists() and (tmp_path / "site/.nojekyll").exists()
    assert {"jobs", "channels", "roles", "meta", "stats"} == {p.stem for p in (tmp_path / "site/data").glob("*.json")}
    assert (tmp_path / "site/overview.js").exists() and (tmp_path / "site/feed.xml").exists() and (tmp_path / "site/feed.json").exists()


def test_scrub_db_leaves_only_excerpts_and_links(isolated):
    add(isolated, "Flutter Dev", LONG, [("RemoteOK", -1, "https://r/1"), ("某频道", -1001234567890, None)])
    isolated.execute("UPDATE jobs SET note='my private note'")
    publish.scrub_db(isolated)
    r = isolated.execute("SELECT text, contacts, note FROM jobs").fetchone()
    assert len(r["text"]) <= 601 and "hr@example.com" not in r["text"] and r["note"] == ""
    assert json.loads(r["contacts"]) == {"telegram": [], "email": [], "links": ["https://r/1"]}


def test_redact():
    assert publish.redact("mail me: a.b+c@x.co, ping @abc_def or https://t.me/abc_def now") == "mail me: [email], ping [@] or [telegram] now"
    assert publish.redact("see https://example.com/@user/post") == "see https://example.com/@user/post"   # not a handle


def test_public_whitelist():
    assert mode.allowed("RemoteOK", "public") and not mode.allowed("LinkedIn", "public") and not mode.allowed("Adzuna", "public")
    assert mode.allowed("LinkedIn", None) and mode.allowed("LinkedIn", "private")


def test_public_fetch_refuses_to_touch_the_real_database(monkeypatch):
    monkeypatch.setattr(db, "HOME", db.DEFAULT_HOME)
    with pytest.raises(SystemExit):
        cli.main(["fetch", "--mode", "public", "--no-web"])


def test_source_kinds_and_new_badge_on_the_public_site(isolated, tmp_path):
    assert mode.kind("EURES (EU)", -23) == "gov" and mode.kind("RemoteOK", -1) == "site" and mode.kind("某频道", -1001234567890) == "telegram"
    add(isolated, "Flutter Dev", "Flutter developer", [("EURES (EU)", -23, "https://eures/1")], days_ago=9)
    isolated.execute("UPDATE jobs SET first_seen=datetime('now')")     # first sighting = just now (fresh public database)
    publish.export(isolated, tmp_path / "site")
    j = json.loads((tmp_path / "site/data/jobs.json").read_text())["jobs"][0]
    assert j["sources"][0]["kind"] == "gov"
    assert j["first_seen"] == j["posted_at"]            # 9 days old: must not show as "new today"


@pytest.mark.parametrize("text,expected", [
    ("Remote, US only", "restricted"), ("Candidates must be located in the United States", "restricted"),
    ("must be based in the UK", "restricted"), ("You must be authorized to work in the US", "restricted"),
    ("EU-based residents only", "restricted"), ("Remote, EU time zones", "restricted"), ("仅限美国居民", "restricted"),
    ("Fully remote, work from anywhere", "unknown"), ("We are a US company hiring worldwide", "unknown"),
    ("Our office in Europe, remote friendly", "unknown"), ("", "unknown")])
def test_region_restriction_is_read_from_the_text(text, expected):
    assert parse.geo_from_text(text) == expected


def test_geo_from_text_reaches_export_and_new_jobs(isolated, tmp_path):
    from jobboard.sources import run
    item = {"id": 1, "url": "https://x/1", "title": "Flutter Developer", "company": "Acme", "location": "", "tags": [], "salary": None,
            "text": "Fully remote but US only.", "date": datetime.now(timezone.utc).isoformat(), "remote": True}
    a, t, s = run.to_job(item, "RemoteOK", -1)
    assert a["geo"] == "restricted"
    db.upsert(isolated, a, t, s)
    isolated.execute("UPDATE jobs SET geo='unknown'")          # an old row stored before this rule existed
    isolated.commit()
    publish.export(isolated, tmp_path / "site")
    assert json.loads((tmp_path / "site/data/jobs.json").read_text())["jobs"][0]["geo"] == "restricted"


# ---- posted time: UTC everywhere, honest ordering, first vs last appearance ------------------------------
def test_dates_are_normalised_to_utc():
    from jobboard.sources import adapters
    assert adapters._iso("2026-10-22T09:16:15-04:00") == "2026-10-22T13:16:15+00:00"
    assert adapters._iso("Thu, 22 Oct 2026 12:00:00 +0200") == "2026-10-22T10:00:00+00:00"
    assert adapters._iso("2026-10-21") == "2026-10-21T00:00:00+00:00" and adapters._iso(1792800000).endswith("+00:00")
    assert db.utc_iso("2026-10-22T09:16:15-04:00") == "2026-10-22T13:16:15+00:00" and db.utc_iso("garbage") == "garbage"


def put(con, title, date, chat_id=-1, msg=1, fp=None, chat="RemoteOK"):
    a = parse.analyze(f"{title}\nAcme", structured=True)
    a.update(title=title, company="Acme", fingerprint=parse.fingerprint(fp or title))
    db.upsert(con, a, f"{title}\nAcme", {"chat": chat, "chat_id": chat_id, "msg_id": msg, "date": date, "url": f"https://r/{msg}"})
    con.commit()


def test_jobs_sort_by_real_time_not_by_text(isolated):
    # 10:00-04:00 is 14:00Z, later than 12:00Z although its text sorts earlier ("10" < "12")
    put(isolated, "Flutter Dev early", "2026-10-22T12:00:00+00:00", msg=1)
    put(isolated, "Flutter Dev late", "2026-10-22T10:00:00-04:00", msg=2)
    assert [r["title"] for r in db.list_jobs(isolated)] == ["Flutter Dev late", "Flutter Dev early"]
    assert all(r["posted_at"].endswith("+00:00") for r in isolated.execute("SELECT posted_at FROM jobs"))


def test_reposted_job_shows_first_and_last_appearance(isolated, tmp_path):
    now = datetime.now(timezone.utc)
    put(isolated, "Flutter Dev", (now - timedelta(days=10)).isoformat(), msg=1, fp="same")
    put(isolated, "Flutter Dev", (now - timedelta(days=1)).isoformat(), chat_id=-2, msg=2, fp="same", chat="Remotive")   # posted again yesterday
    publish.export(isolated, tmp_path / "site")
    j = json.loads((tmp_path / "site/data/jobs.json").read_text())["jobs"][0]
    assert j["posted_at"] < j["last_posted"] and j["last_posted"].endswith("+00:00")
    assert db.post_times(j["posted_at"], [{"date": j["posted_at"]}, {"date": (now - timedelta(days=9.9)).isoformat()}])[1] is None   # <1 day apart: no second date


# ---- paged data files ------------------------------------------------------------------------------------
def test_export_is_split_into_chunks_best_matches_first(isolated, tmp_path, monkeypatch):
    monkeypatch.setattr(publish, "CHUNK", 2)
    for i, title in enumerate(["Flutter Dev A", "Flutter Dev B", "Android Dev C", "Senior Software Engineer D", "iOS Dev E"], 1):
        put(isolated, title, f"2026-10-2{i}T10:00:00+00:00", msg=i)
    site = tmp_path / "site"
    (site / "data").mkdir(parents=True)
    (site / "data/jobs-9.json").write_text("{}")                    # a leftover from an earlier, bigger export
    publish.export(isolated, site)
    head = json.loads((site / "data/jobs.json").read_text(encoding="utf-8"))
    assert head["chunk_size"] == 2 and head["chunks"] == len(list(site.glob("data/jobs-*.json"))) + 1 and not (site / "data/jobs-9.json").exists()
    rest = [json.loads((site / f"data/jobs-{i}.json").read_text(encoding="utf-8"))["jobs"] for i in range(1, head["chunks"])]
    everything = head["jobs"] + [j for c in rest for j in c]
    assert head["total"] == len(everything) and len({j["id"] for j in everything}) == len(everything)
    assert len(head["jobs"]) == 2 and all(len(c) <= 2 for c in rest)
    scores = [j["score"] for j in everything]
    assert scores == sorted(scores, reverse=True)                  # best matches come first, so the first chunk is what the default view needs
    assert json.loads((site / "data/meta.json").read_text(encoding="utf-8"))["chunks"] == head["chunks"]


def test_prune_drops_only_old_jobs(isolated):
    now = datetime.now(timezone.utc)
    put(isolated, "Flutter Dev old", (now - timedelta(days=90)).isoformat(), msg=1)
    put(isolated, "Flutter Dev recent", (now - timedelta(days=5)).isoformat(), msg=2)
    assert db.prune(isolated, 60) == 1
    assert [r["title"] for r in isolated.execute("SELECT title FROM jobs")] == ["Flutter Dev recent"]


# ---- overview numbers, feeds, levels ----------------------------------------------------------------------------
import xml.etree.ElementTree as ET
from jobboard import profile_config as pc


def fake_job(i, title, tags, hours_ago, now, score=12, company=None, visa="", remote=True, location="Remote"):
    posted = (now - timedelta(hours=hours_ago)).isoformat()
    return {"id": i, "title": title, "company": company if company is not None else f"Co{i}", "location": location, "geo": "unknown", "salary": None,
            "remote": remote, "score": score, "tags": tags, "risks": [], "visa": visa, "posted_at": posted, "last_posted": None, "first_seen": posted,
            "status": "new", "text": f"{title} at Co{i}. Remote friendly.", "structured": True, "hint": "",
            "contacts": {"telegram": [], "email": [], "links": [f"https://x/{i}"]},
            "sources": [{"chat": "RemoteOK", "chat_id": -1, "msg_id": i, "date": posted, "url": f"https://x/{i}", "channel_url": None, "kind": "site"}]}


NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)


def test_overview_numbers():
    jobs = [fake_job(1, "A", ["backend"], 2, NOW), fake_job(2, "B", ["backend"], 30, NOW, remote=False), fake_job(3, "C", ["go"], 24 * 8, NOW),
            fake_job(4, "D", ["go"], 24 * 9, NOW, visa="yes", company="Acme"), fake_job(5, "E", ["go"], 24 * 3, NOW, visa="yes", company="Acme")]
    st = publish.compute_stats(jobs, NOW, None, [{"key": "backend", "label": "后端"}, {"key": "go", "label": "Go"}], 5)
    t = st["totals"]
    assert t["jobs"] == 5 and t["new_24h"] == 1 and t["new_7d"] == 3 and t["prev_7d"] == 2 and t["remote_pct"] == 80 and t["visa_yes"] == 2
    assert [d["count"] for d in st["daily"]][-1] == 1 and len(st["daily"]) == 14 and sum(d["count"] for d in st["daily"]) == 5
    assert {i["label"]: i["count"] for i in st["distribution"]["items"]} == {"后端": 2, "Go": 3}
    assert st["visa_employers"][0]["company"] == "Acme" and st["visa_employers"][0]["count"] == 2 and len(st["visa_employers"][0]["jobs"]) == 2


def test_picks_are_recent_varied_and_best_first():
    jobs = [fake_job(i, f"Backend Dev {i}", ["backend"], 1 + i, NOW, score=14 - i % 3) for i in range(1, 7)]      # six backend jobs
    jobs += [fake_job(10, "Flutter Dev", ["flutter"], 5, NOW, score=10), fake_job(11, "Old Dev", ["flutter"], 24 * 5, NOW, score=15),
             fake_job(12, "Same Co", ["ios"], 3, NOW, score=12, company="Co1")]
    picks = publish.compute_stats(jobs, NOW, None, [], 5)["picks"]
    titles = [p["title"] for p in picks]
    assert sum(t.startswith("Backend") for t in titles) == 2                     # at most two per role
    assert len({p["company"] for p in picks}) == len(picks)                      # one per company
    assert "Flutter Dev" in titles and len(picks) <= 8 and titles[-1] == "Old Dev"   # the last-two-days jobs first, older ones only fill up
    fresh = picks[:-1]                                                          # the old job only fills up, so compare the recent ones
    assert fresh[0]["score"] == max(p["score"] for p in fresh)
    quiet = publish.compute_stats([fake_job(1, "Only old", ["go"], 24 * 5, NOW)], NOW, None, [], 5)["picks"]
    assert [p["title"] for p in quiet] == ["Only old"]                          # a quiet week widens the window instead of showing nothing


def test_aggregate_overview_groups_roles_into_families_with_links():
    cfg = pc.validate(pc.load_preset("all-tech"))
    jobs = [fake_job(1, "Go Dev", ["go", "backend"], 3, NOW), fake_job(2, "Flutter Dev", ["flutter"], 3, NOW), fake_job(3, "React Dev", ["react"], 3, NOW)]
    st = publish.compute_stats(jobs, NOW, cfg, pc.roles_for_ui(cfg), 12, "https://devopenings.github.io")
    fam = {i["key"]: i for i in st["distribution"]["items"]}
    assert st["distribution"]["kind"] == "families" and fam["backend"]["count"] == 1 and fam["mobile"]["count"] == 1 and fam["frontend"]["count"] == 1
    assert fam["backend"]["url"] == "https://devopenings.github.io/backend/" and fam["backend"]["label"] == "后端"


def test_feeds_are_valid_and_carry_no_contacts(isolated, tmp_path):
    put(isolated, "Flutter Dev <b> & co", "2026-10-24T10:00:00+00:00", msg=1)
    put(isolated, "Android Dev", "2026-10-25T09:00:00+00:00", msg=2)
    isolated.execute("UPDATE jobs SET score=12")
    isolated.commit()
    publish.export(isolated, tmp_path / "site", now=datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc))
    items = ET.parse(tmp_path / "site/feed.xml").getroot().findall("channel/item")
    assert [i.findtext("title") for i in items] == ["Acme: Android Dev", "Acme: Flutter Dev <b> & co"]                 # newest first, markup survives escaping
    assert items[0].findtext("link") == "https://r/2" and items[0].find("guid").get("isPermaLink") == "false"
    jf = json.loads((tmp_path / "site/feed.json").read_text(encoding="utf-8"))
    assert jf["version"].startswith("https://jsonfeed.org/version/1") and [i["url"] for i in jf["items"]] == ["https://r/2", "https://r/1"]
    assert "rel=\"alternate\" type=\"application/rss+xml\"" in (tmp_path / "site/index.html").read_text(encoding="utf-8")


def test_feed_uses_the_configured_site_url(isolated, tmp_path, monkeypatch):
    monkeypatch.setenv("JOBBOARD_SITE_URL", "https://example.org/jobs")
    put(isolated, "Flutter Dev", "2026-10-24T10:00:00+00:00", msg=1)
    isolated.execute("UPDATE jobs SET score=12")
    isolated.commit()
    publish.export(isolated, tmp_path / "site", now=datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc))
    xml = (tmp_path / "site/feed.xml").read_text(encoding="utf-8")
    assert "<link>https://example.org/jobs/</link>" in xml and 'href="https://example.org/jobs/feed.xml"' in xml
    assert json.loads((tmp_path / "site/feed.json").read_text(encoding="utf-8"))["feed_url"] == "https://example.org/jobs/feed.json"


def test_levels_are_written_into_the_page(isolated, tmp_path):
    cfg = pc.validate(pc.load_preset("all-tech"))
    pc.apply(cfg)
    publish.export(isolated, tmp_path / "site")
    page = (tmp_path / "site/index.html").read_text(encoding="utf-8")
    assert 'const LEVELS=[[1,"宽松"],[12,"推荐"],[14,"严格"]];' in page and 'status:"all",min:12,since:""' in page
    pc.reset()
    publish.export(isolated, tmp_path / "plain")
    assert 'const LEVELS=[[1,"宽松"],[5,"推荐"],[10,"严格"]];' in (tmp_path / "plain/index.html").read_text(encoding="utf-8")


def test_stats_never_contain_contacts(isolated, tmp_path):
    put(isolated, "Flutter Dev", "2026-10-24T10:00:00+00:00", msg=1)
    isolated.execute("UPDATE jobs SET score=12, text='Mail hr@example.com or @some_recruiter', visa='yes'")
    isolated.commit()
    publish.export(isolated, tmp_path / "site", now=datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc))
    blob = (tmp_path / "site/data/stats.json").read_text(encoding="utf-8") + (tmp_path / "site/feed.xml").read_text(encoding="utf-8") + (tmp_path / "site/feed.json").read_text(encoding="utf-8")
    assert "hr@example.com" not in blob and "some_recruiter" not in blob
