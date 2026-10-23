from fastapi.testclient import TestClient

from jobboard import db, parse, web


def _seed(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "HOME", tmp_path)
    con = db.connect()
    t = "远程Flutter客户端工程师\n薪资：面议\n联系 @hr_flutter"
    db.upsert(con, parse.analyze(t), t, {"chat": "C", "chat_id": 1, "msg_id": 1, "date": "2026-10-23T08:00:00+00:00"})
    con.commit()
    return TestClient(web.app)


def test_page_and_jobs_api(tmp_path, monkeypatch):
    c = _seed(tmp_path, monkeypatch)
    assert "岗位看板" in c.get("/").text
    j = c.get("/api/jobs").json()["jobs"]
    assert len(j) == 1 and j[0]["contacts"]["telegram"] == ["@hr_flutter"] and j[0]["tags"][0] == "flutter"


def test_status_update_and_validation(tmp_path, monkeypatch):
    c = _seed(tmp_path, monkeypatch)
    jid = c.get("/api/jobs").json()["jobs"][0]["id"]
    assert c.post(f"/api/jobs/{jid}/status", json={"status": "applied"}).status_code == 200
    assert c.get("/api/jobs").json()["jobs"][0]["status"] == "applied"
    assert c.post(f"/api/jobs/{jid}/status", json={"status": "bogus"}).status_code == 400
    assert c.post("/api/jobs/999/status", json={"status": "new"}).status_code == 404


def test_sources_carry_links(tmp_path, monkeypatch):
    c = _seed(tmp_path, monkeypatch)
    con = db.connect()
    con.execute("INSERT INTO chats(chat_id, username, title) VALUES(1, 'pubchan', 'C')")
    con.commit()
    s = c.get("/api/jobs").json()["jobs"][0]["sources"][0]
    assert s["url"] == "https://t.me/pubchan/1" and s["channel_url"] == "https://t.me/pubchan"


def test_web_job_source_keeps_its_own_url_and_company(tmp_path, monkeypatch):
    from jobboard.sources import run as web
    c = _seed(tmp_path, monkeypatch)
    item = {"id": 9, "url": "https://remoteok.com/remote-jobs/9", "title": "Senior Flutter Developer", "company": "Acme",
            "location": "USA only", "tags": ["flutter"], "salary": "$90k-130k/年", "text": "Build apps", "remote": True,
            "date": "2026-10-23T00:00:00+00:00"}
    con = db.connect()
    a, text, src = web.to_job(item, "RemoteOK", -1)
    db.upsert(con, a, text, src); con.commit()
    job = next(j for j in c.get("/api/jobs").json()["jobs"] if j["company"] == "Acme")
    assert job["sources"][0]["url"] == "https://remoteok.com/remote-jobs/9"
    assert job["geo"] == "restricted" and job["location"] == "USA only" and job["salary"] == "$90k-130k/年"


def test_chats_api_lists_channels_with_links(tmp_path, monkeypatch):
    c = _seed(tmp_path, monkeypatch)
    con = db.connect()
    con.execute("INSERT INTO chats(chat_id, username, title) VALUES(1, 'pubchan', 'C')")
    con.execute("INSERT INTO chats(chat_id, username, title) VALUES(-123456, NULL, 'Old basic group')")
    con.commit()
    chats = {x["title"]: x for x in c.get("/api/chats").json()["chats"]}
    assert chats["C"]["url"] == "https://t.me/pubchan" and chats["C"]["jobs"] == 1
    assert chats["Old basic group"]["url"] is None  # can't be linked; shown as plain text


def test_sources_are_tagged_site_or_telegram(tmp_path, monkeypatch):
    from jobboard.sources import run as web
    c = _seed(tmp_path, monkeypatch)
    item = {"id": 9, "url": "https://remoteok.com/9", "title": "Flutter Developer", "company": "Acme", "location": "",
            "tags": [], "salary": None, "text": "x", "remote": True, "date": "2026-10-23T00:00:00+00:00"}
    con = db.connect()
    a, text, src = web.to_job(item, "RemoteOK", -1)
    db.upsert(con, a, text, src); con.commit()
    kinds = {s["chat"]: s["kind"] for j in c.get("/api/jobs").json()["jobs"] for s in j["sources"]}
    assert kinds == {"C": "telegram", "RemoteOK": "site"}
