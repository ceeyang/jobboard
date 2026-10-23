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
