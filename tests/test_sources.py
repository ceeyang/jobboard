import json

import pytest

from jobboard import db, parse
from jobboard.sources import adapters, http, run

REMOTEOK = [{"legal": "terms"},
            {"id": 1, "url": "https://remoteok.com/remote-jobs/1", "position": "Senior Flutter Developer",
             "company": "Acme", "tags": ["flutter", "dart", "mobile"], "location": "", "salary_min": 90000,
             "salary_max": 130000, "description": "<p>Build <b>apps</b></p><ul><li>Dart</li></ul>",
             "date": "2026-10-22T12:00:00+00:00"},
            {"id": 2, "url": "https://remoteok.com/remote-jobs/2", "position": "Data Engineer", "company": "Foo",
             "tags": ["python"], "location": "USA only", "description": "ETL", "date": "2026-10-22T12:00:00+00:00"}]
WWR = """<rss><channel><item><title>Acme: Senior Flutter Developer</title><region>Anywhere in the World</region>
<category>Programming</category><description>&lt;p&gt;Same job on another site&lt;/p&gt;</description>
<pubDate>Thu, 22 Oct 2026 12:00:00 +0000</pubDate><link>https://weworkremotely.com/jobs/9</link></item></channel></rss>"""
HN_SEARCH = {"hits": [{"title": "Ask HN: Who is hiring? (October 2026)", "objectID": "7"}]}
HN_TREE = {"children": [
    {"id": 11, "text": "Initech | iOS Engineer | Remote | $150k<p>Swift, SwiftUI. Email jobs@initech.io", "created_at": "2026-10-01T10:00:00Z"},
    {"id": 12, "text": "Hooli | Office manager | Palo Alto", "created_at": "2026-10-01T10:00:00Z"}]}


def fake_http(monkeypatch, routes):
    def pick(url):
        for key, val in routes.items():
            if key in url:
                if isinstance(val, Exception):
                    raise val
                return val
        raise AssertionError(f"unexpected url {url}")
    monkeypatch.setattr(http, "get_json", pick)
    monkeypatch.setattr(http, "get_text", lambda u, timeout=25: pick(u))


def test_plain_strips_html():
    assert adapters.plain("<p>Hi&nbsp;<b>there</b></p><ul><li>A</li><li>B</li></ul>") == "Hi there\n- A\n- B"


def test_geo_flag():
    assert parse.geo_flag("Worldwide") == "worldwide"
    assert parse.geo_flag("Anywhere in the World") == "worldwide"
    assert parse.geo_flag("USA only") == "restricted"
    assert parse.geo_flag("Europe, UK") == "restricted"
    assert parse.geo_flag("") == "unknown"
    assert parse.geo_flag("Asia, Europe") == "worldwide"  # APAC-friendly wins


def test_remoteok_adapter(monkeypatch):
    fake_http(monkeypatch, {"remoteok.com/api": REMOTEOK})
    jobs = adapters.remoteok()
    assert len(jobs) == 2 and jobs[0]["salary"] == "$90k-130k/年" and jobs[0]["location"] == "Worldwide"


def test_site_tags_make_a_match_even_when_title_is_generic():
    item = {"id": 5, "url": "https://x/5", "title": "Software Engineer", "company": "C", "location": "Worldwide",
            "tags": ["flutter", "dart"], "salary": None, "text": "Join us", "date": "2026-10-22T00:00:00+00:00", "remote": True}
    a, text, src = run.to_job(item, "X", -9)
    assert a["score"] >= 10 and "flutter" in a["tags"] and a["geo"] == "worldwide" and src["url"] == "https://x/5"
    item["tags"] = ["python"]
    assert run.to_job(item, "X", -9)[0]["score"] == 0


def _score(title, tags, text="Join us. Remote."):
    item = {"id": 5, "url": "https://x/5", "title": title, "company": "C", "location": "Worldwide",
            "tags": tags, "salary": None, "text": text, "date": "2026-10-22T00:00:00+00:00", "remote": True}
    return run.to_job(item, "X", -9)[0]["score"]


def test_stuffed_site_tags_do_not_make_a_backend_job_a_match():
    stuffed = ["flutter", "ios", "android", "mobile", "frontend", "fullstack"]
    assert _score("Senior back-end Engineer", stuffed) < 5      # Lemon.io pattern
    assert _score("Graphic Designer & Video Editor", ["mobile"]) < 5
    assert _score("Technical Product Manager", stuffed) < 5
    assert _score("Product Engineering Lead", ["mobile", "frontend"]) < 5  # generic tags can't rescue
    assert _score("Software Engineer", ["flutter", "dart"]) >= 10          # specific tag rescues generic title
    assert _score("Senior Flutter Developer", []) >= 10
    assert _score("Mobile Developer (iOS/Android)", []) >= 9


def test_wrong_stack_fullstack_is_not_recommended():
    assert _score("Senior .NET Full-stack Developer", []) < 5
    assert _score("Senior React Full-stack Developer", []) >= 5
    assert _score("Fullstack Engineer (Node/React)", []) >= 5


def test_run_merges_same_job_across_sites_and_skips_irrelevant(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "HOME", tmp_path)
    fake_http(monkeypatch, {"remoteok.com/api": REMOTEOK, "weworkremotely.com/categories": WWR})
    con = db.connect(tmp_path / "t.db")
    stats = run.run(con, sources=[("RemoteOK", -1, 0, adapters.remoteok), ("We Work Remotely", -3, 0, adapters.weworkremotely)])
    assert stats["RemoteOK"]["matched"] == 1 and stats["RemoteOK"]["new"] == 1  # Data Engineer dropped
    assert stats["We Work Remotely"]["new"] == 0  # merged into the RemoteOK row
    rows = con.execute("SELECT * FROM jobs").fetchall()
    assert len(rows) == 1
    srcs = json.loads(rows[0]["sources"])
    assert {s["chat"] for s in srcs} == {"RemoteOK", "We Work Remotely"} and all(s["url"].startswith("https://") for s in srcs)
    assert rows[0]["company"] == "Acme" and rows[0]["geo"] == "worldwide" and rows[0]["salary"]


def test_min_interval_and_force(tmp_path, monkeypatch):
    fake_http(monkeypatch, {"remoteok.com/api": REMOTEOK})
    con = db.connect(tmp_path / "t.db")
    src = [("RemoteOK", -1, 3600, adapters.remoteok)]
    t = [1000.0]
    assert "matched" in run.run(con, sources=src, now=lambda: t[0])["RemoteOK"]
    t[0] += 60
    assert run.run(con, sources=src, now=lambda: t[0])["RemoteOK"] == {"skipped": "ran recently"}
    assert "matched" in run.run(con, sources=src, now=lambda: t[0], force=True)["RemoteOK"]
    t[0] += 4000
    assert "matched" in run.run(con, sources=src, now=lambda: t[0])["RemoteOK"]


def test_one_failing_site_does_not_stop_others(tmp_path, monkeypatch):
    fake_http(monkeypatch, {"remoteok.com/api": REMOTEOK, "weworkremotely": OSError("boom")})
    con = db.connect(tmp_path / "t.db")
    stats = run.run(con, sources=[("WWR", -3, 0, adapters.weworkremotely), ("RemoteOK", -1, 0, adapters.remoteok)])
    assert "boom" in stats["WWR"]["error"] and stats["RemoteOK"]["new"] == 1
    assert "boom" in con.execute("SELECT last_error FROM source_state WHERE name='WWR'").fetchone()[0]
    # a failed site must be retried next run, not treated as recently run
    assert run.run(con, sources=[("WWR", -3, 3600, adapters.weworkremotely)])["WWR"].get("error")


def test_hn_thread(monkeypatch):
    fake_http(monkeypatch, {"search_by_date": HN_SEARCH, "items/7": HN_TREE})
    jobs = adapters.hn_whos_hiring()
    assert len(jobs) == 2 and jobs[0]["url"].endswith("id=11") and jobs[0]["remote"]
    a, _, _ = run.to_job(jobs[0], "HN", -7)
    assert "ios" in a["tags"] and "jobs@initech.io" in a["contacts"]["email"]
    assert run.to_job(jobs[1], "HN", -7)[0]["score"] == 0


def test_old_database_gets_migrated(tmp_path):
    import sqlite3
    p = tmp_path / "old.db"
    c = sqlite3.connect(p)
    c.executescript("CREATE TABLE jobs(id INTEGER PRIMARY KEY, fingerprint TEXT UNIQUE, title TEXT, salary TEXT, text TEXT,"
                    "score INTEGER, tags TEXT, risks TEXT, remote INTEGER, contacts TEXT, sources TEXT, posted_at TEXT,"
                    "first_seen TEXT, status TEXT DEFAULT 'new', notified INTEGER DEFAULT 0, note TEXT DEFAULT '');")
    c.commit(); c.close()
    cols = {r[1] for r in db.connect(p).execute("PRAGMA table_info(jobs)")}
    assert {"company", "location", "geo", "hint"} <= cols


def test_hn_title_keeps_company_and_role_segments():
    assert adapters._hn_title("Perry Street Software | Senior Mobile Developer (iOS) | https://x.io | REMOTE") == \
        ("Perry Street Software", "Senior Mobile Developer (iOS)")
    assert adapters._hn_title("Acme | Senior Engineer | iOS | Berlin") == ("Acme", "Senior Engineer · iOS")
    assert adapters._hn_title("Just a sentence without pipes")[1] == "Just a sentence without pipes"


def test_hn_job_seekers_are_not_jobs():
    for t in ("I am iOS developer with 13 years of experience. Available for hire.",
              "I'm a senior Flutter developer looking for work, remote only"):
        assert parse.analyze(t, structured=True)["score"] == 0
    assert parse.analyze("Acme | iOS Engineer | Remote\nWe are not looking for work-from-office people", structured=True)["score"] >= 5


def test_salary_is_never_guessed_from_description_prose():
    item = {"id": 5, "url": "https://x/5", "title": "Mobile Developer", "company": "C", "location": "USA",
            "tags": [], "salary": None, "text": "We are a team of 10 - 50 employees.", "remote": True,
            "date": "2026-10-22T00:00:00+00:00"}
    assert run.to_job(item, "X", -9)[0]["salary"] is None
