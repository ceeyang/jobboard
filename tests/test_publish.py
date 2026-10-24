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
    assert {"jobs", "channels", "roles", "meta"} == {p.stem for p in (tmp_path / "site/data").glob("*.json")}


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
