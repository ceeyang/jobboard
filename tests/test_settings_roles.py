import json
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from jobboard import db, parse, profile, roles, scheduler, web


@pytest.fixture(autouse=True)
def reset_rules():
    yield
    profile.set_rules(None)   # roles are process-global; never leak between tests


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "HOME", tmp_path)
    con = db.connect()
    for i, t in enumerate(["远程Flutter工程师\n联系 @a_hr", "鸿蒙开发工程师\n远程办公 HarmonyOS", "Java后端工程师\n远程"]):
        db.upsert(con, parse.analyze(t), t, {"chat": "C", "chat_id": -1001, "msg_id": i, "date": "2026-10-23T00:00:00+00:00"})
    con.commit()
    return TestClient(web.app)


# ---------- keywords -> regex ----------
def test_ascii_keywords_are_guarded_but_cjk_neighbours_are_fine():
    import re
    rx = re.compile(roles.kw_regex(["ios"]), re.I)
    assert rx.search("iOS开发") and rx.search("senior ios dev") and not rx.search("bios update")
    rn = re.compile(roles.kw_regex(["react native"]), re.I)
    assert rn.search("React Native") and rn.search("reactnative") and not rn.search("native react")
    assert re.search(roles.kw_regex(["鸿蒙"]), "鸿蒙开发")


def test_default_roles_reproduce_the_built_in_matching(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "HOME", tmp_path)
    roles.activate(db.connect())
    assert parse.analyze("远程Flutter客户端工程师")["score"] >= 12
    assert "ios" in parse.analyze("招聘 iOS开发\n远程")["tags"]
    assert parse.analyze("Java 后端工程师")["score"] == 0


@pytest.mark.parametrize("label,kw,w,msg", [
    ("", "abc", 5, "名称"), ("x" * 30, "abc", 5, "名称"), ("A", "", 5, "关键词"),
    ("A", "a", 5, "2 个字符"), ("A", "abc", 0, "1–10"), ("A", "abc", "x", "1–10"), ("A", "abc", 11, "1–10")])
def test_role_validation(label, kw, w, msg):
    with pytest.raises(ValueError, match=msg):
        roles.validate(label, kw, w)


# ---------- roles API ----------
def test_add_role_rescores_existing_jobs(client):
    before = {j["title"]: j["score"] for j in client.get("/api/jobs?min_score=0").json()["jobs"]}
    assert before["鸿蒙开发工程师"] == 0
    r = client.post("/api/roles", json={"label": "鸿蒙", "keywords": "鸿蒙, harmonyos", "weight": 8})
    assert r.status_code == 200 and r.json()["rescored"] == 3
    after = {j["title"]: j for j in client.get("/api/jobs?min_score=0").json()["jobs"]}
    assert after["鸿蒙开发工程师"]["score"] >= 8 and any(t.startswith("r") for t in after["鸿蒙开发工程师"]["tags"])
    assert after["远程Flutter工程师"]["score"] >= 10          # existing roles untouched


def test_toggle_edit_delete_role(client):
    rid = client.post("/api/roles", json={"label": "鸿蒙", "keywords": "鸿蒙", "weight": 8}).json()["id"]
    score = lambda: {j["title"]: j["score"] for j in client.get("/api/jobs?min_score=0").json()["jobs"]}["鸿蒙开发工程师"]
    assert score() >= 8
    client.put(f"/api/roles/{rid}", json={"enabled": False})
    assert score() == 0                                           # disabled -> stops matching
    client.put(f"/api/roles/{rid}", json={"enabled": True})
    r = client.put(f"/api/roles/{rid}", json={"label": "HarmonyOS", "keywords": "鸿蒙开发, harmonyos", "weight": 9})
    assert r.status_code == 200 and score() >= 9
    # a keyword that only appears in the body never reaches the recommended threshold (same rule as built-in roles)
    client.put(f"/api/roles/{rid}", json={"label": "HarmonyOS", "keywords": "harmonyos", "weight": 9})
    assert score() < 5
    client.put(f"/api/roles/{rid}", json={"label": "HarmonyOS", "keywords": "鸿蒙开发, harmonyos", "weight": 9})
    assert client.delete(f"/api/roles/{rid}").status_code == 200 and score() == 0
    assert client.delete(f"/api/roles/{rid}").status_code == 404


def test_role_api_errors(client):
    assert client.post("/api/roles", json={"label": "A", "keywords": "", "weight": 5}).status_code == 400
    assert client.post("/api/roles", json={"label": "Flutter", "keywords": "flutter2", "weight": 5}).status_code == 400  # duplicate name
    assert client.put("/api/roles/9999", json={"enabled": True}).status_code == 404
    labels = [r["label"] for r in client.get("/api/roles").json()["roles"]]
    assert labels[:3] == ["Flutter", "iOS", "Android"]            # seeded defaults


# ---------- settings ----------
def test_settings_validation_and_roundtrip(client):
    assert client.get("/api/settings").json()["refresh_minutes"] == 60
    r = client.put("/api/settings", json={"refresh_minutes": 30, "digest_enabled": True, "digest_time": "08:30"})
    assert r.status_code == 200 and (r.json()["refresh_minutes"], r.json()["digest_time"]) == (30, "08:30")
    for bad in ({"refresh_minutes": 3}, {"refresh_minutes": 5000}, {"refresh_minutes": "abc"}, {"digest_time": "25:00"}, {"digest_time": "9:00"}):
        assert client.put("/api/settings", json=bad).status_code == 400, bad
    assert client.put("/api/settings", json={"refresh_minutes": 0}).json()["next_fetch_at"] is None   # 0 = off
    # bookkeeping keys can't be set from outside
    client.put("/api/settings", json={"last_fetch_started": 99999999999, "refresh_minutes": 15})
    assert scheduler.load(db.connect())["last_fetch_started"] == 0


def test_saved_searches(client):
    r = client.put("/api/settings", json={"saved_searches": [{"name": "远程 Flutter", "q": "flutter|dart 远程 -外包"}]})
    assert r.json()["saved_searches"] == [{"name": "远程 Flutter", "q": "flutter|dart 远程 -外包"}]
    assert client.put("/api/settings", json={"saved_searches": [{"name": "", "q": "x"}]}).status_code == 400
    assert client.put("/api/settings", json={"saved_searches": [{"name": "n", "q": "q"}] * 51}).status_code == 400


# ---------- scheduler decisions ----------
S0 = dict(scheduler.DEFAULTS)


def test_fetch_due():
    s = {**S0, "refresh_minutes": 30, "last_fetch_started": 1000.0}
    assert not scheduler.fetch_due(s, 1000 + 29 * 60, running=False)
    assert scheduler.fetch_due(s, 1000 + 30 * 60, running=False)
    assert not scheduler.fetch_due(s, 1000 + 99 * 60, running=True)          # never overlap
    assert not scheduler.fetch_due({**s, "refresh_minutes": 0}, 10**9, running=False)
    assert scheduler.next_fetch_at(s) == 1000 + 1800 and scheduler.next_fetch_at({**s, "refresh_minutes": 0}) is None


def test_digest_due_once_per_day():
    s = {**S0, "digest_enabled": True, "digest_time": "09:00", "last_digest_date": ""}
    assert not scheduler.digest_due(s, datetime(2026, 10, 24, 8, 59))
    assert scheduler.digest_due(s, datetime(2026, 10, 24, 9, 0))
    assert scheduler.digest_due(s, datetime(2026, 10, 24, 23, 0))            # still due if the server was off at 09:00
    assert not scheduler.digest_due({**s, "last_digest_date": "2026-10-24"}, datetime(2026, 10, 24, 12, 0))
    assert scheduler.digest_due({**s, "last_digest_date": "2026-10-24"}, datetime(2026, 10, 25, 9, 1))
    assert not scheduler.digest_due({**s, "digest_enabled": False}, datetime(2026, 10, 24, 12, 0))


def test_tick_starts_fetch_and_digest_exactly_once(client, monkeypatch):
    spawned = []

    class FakeProc:
        def poll(self): return None
    monkeypatch.setattr(web.subprocess, "Popen", lambda cmd, **kw: spawned.append(cmd[-1]) or FakeProc())
    monkeypatch.setattr(web, "_proc", None)
    client.put("/api/settings", json={"refresh_minutes": 15, "digest_enabled": True, "digest_time": "09:00"})
    first = web._tick(now_ts=10_000.0, now_dt=datetime(2026, 10, 24, 9, 5))
    assert first == {"fetch": True, "digest": True} and spawned == ["fetch", "digest"]
    again = web._tick(now_ts=10_001.0, now_dt=datetime(2026, 10, 24, 9, 6))   # fetch still running, digest already sent today
    assert again == {} and spawned == ["fetch", "digest"]
    assert scheduler.load(db.connect())["last_digest_date"] == "2026-10-24"
