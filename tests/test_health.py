"""Source health, blocklist, about page and per-group feeds. No network."""
import json
import time
from datetime import datetime, timedelta, timezone

import pytest

from jobboard import cli, db, health, mode, parse, profile, publish


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "HOME", tmp_path / "home")
    profile.set_rules(None)
    yield db.connect()
    profile.set_rules(None)


def add(con, title, source, url, company="Acme"):
    a = parse.analyze("Flutter developer")
    a.update(title=title, company=company, fingerprint=parse.fingerprint(title + company))
    db.upsert(con, a, "Flutter developer", {"chat": source, "chat_id": -1, "msg_id": 1, "url": url,
                                           "date": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()})
    con.commit()


def state(con, name):
    return health.judge(dict(con.execute("SELECT * FROM source_state WHERE name=?", (name,)).fetchone()), time.time())[0]


def test_errors_escalate_and_a_success_recovers(isolated):
    for _ in range(2):
        db.record_source(isolated, "X", 0, error="boom")
    assert state(isolated, "X") == "warn"
    db.record_source(isolated, "X", 0, error="boom")
    assert state(isolated, "X") == "alert"
    db.record_source(isolated, "X", 5)
    assert state(isolated, "X") == "ok"


def test_empty_runs_after_success_alert_but_never_worked_is_only_never(isolated):
    db.record_source(isolated, "Used", 4)
    for _ in range(6):
        db.record_source(isolated, "Used", 0)
    assert state(isolated, "Used") == "alert"
    for _ in range(3):
        db.record_source(isolated, "Dud", 0)
    assert state(isolated, "Dud") == "never"
    assert health.summary(health.status_rows(isolated, time.time()))["alert"] is True


def test_health_command_exit_code(isolated, capsys):
    db.record_source(isolated, "Ok", 3)
    isolated.commit()
    cli.main(["health"])
    for _ in range(3):
        db.record_source(isolated, "Bad", 0, error="down")
    isolated.commit()
    with pytest.raises(SystemExit) as e:
        cli.main(["health"])
    assert e.value.code == 1


def test_blocklist_hides_jobs_everywhere(isolated, tmp_path, monkeypatch):
    add(isolated, "Flutter Dev A", "RemoteOK", "https://remoteok.com/a", "Keep Inc")
    add(isolated, "Flutter Dev B", "RemoteOK", "https://remoteok.com/b", "Gone Ltd")
    bl = tmp_path / "bl.json"
    bl.write_text(json.dumps({"companies": ["gone ltd"]}), encoding="utf-8")
    monkeypatch.setenv("JOBBOARD_BLOCKLIST", str(bl))
    publish.export(isolated, tmp_path / "site")
    titles = [j["title"] for j in json.loads((tmp_path / "site/data/jobs.json").read_text())["jobs"]]
    assert titles == ["Flutter Dev A"]


def test_about_and_status_pages_are_written(isolated, tmp_path):
    add(isolated, "Flutter Dev", "RemoteOK", "https://remoteok.com/a")
    db.record_source(isolated, "RemoteOK", 1)
    for _ in range(3):
        db.record_source(isolated, "Broken <b>", 0, error="down")
    isolated.commit()
    publish.export(isolated, tmp_path / "site")
    about = (tmp_path / "site/about.html").read_text(encoding="utf-8")
    assert "RemoteOK" in about and "异常" in about and "<b>" not in about.split("Broken")[1][:8] and "不准点" in about
    st = json.loads((tmp_path / "site/status.json").read_text())
    assert st["alert"] is True and (tmp_path / "site/page.css").exists()
    assert "hr@" not in about


def test_each_group_gets_its_own_feed(isolated, tmp_path):
    add(isolated, "Flutter Dev", "RemoteOK", "https://remoteok.com/a")
    publish.export(isolated, tmp_path / "site")
    stats = json.loads((tmp_path / "site/data/stats.json").read_text())
    assert stats["feeds"]
    for f in stats["feeds"]:
        assert (tmp_path / f"site/feeds/{f['key']}.xml").exists() and (tmp_path / f"site/feeds/{f['key']}.json").exists()
