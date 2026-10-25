"""Public Telegram channels: reading through the web preview, and what may reach the public site."""
import json

import pytest

from jobboard import db, mode, parse, profile, publish
from jobboard.sources import http, telegram_public as tp


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "HOME", tmp_path / "home")
    profile.set_rules(None)
    yield db.connect()
    profile.set_rules(None)


def post(user, pid, text, date="2026-10-23T10:00:00+00:00"):
    body = f'<div class="tgme_widget_message_text js-message_text" dir="auto">{text}</div>' if text else ""
    return (f'<div class="tgme_widget_message_wrap js-widget_message_wrap"><div class="tgme_widget_message" data-post="{user}/{pid}">'
            f'{body}<a class="tgme_widget_message_date" href="https://t.me/{user}/{pid}"><time datetime="{date}" class="time">x</time></a></div></div>')


def page(user, title, posts):
    return f'<html><head><meta property="og:title" content="{title}"></head><body>{"".join(posts)}</body></html>'


JOB = "💼 Flutter Developer wanted<br/>Remote, $3k-5k<br/>Contact hr@example.com or @recruiter_x, https://t.me/recruiter_x"


def test_parse_page():
    title, posts = tp.parse_page(page("chan", "Chan &amp; Jobs", [post("chan", 10, JOB), post("chan", 11, ""), post("chan", 12, "short")]))
    assert title == "Chan & Jobs" and [p["id"] for p in posts] == [10, 11, 12]
    assert posts[0]["text"].startswith("💼 Flutter Developer wanted\nRemote") and posts[1]["text"] == ""


def test_read_stores_jobs_with_post_links_and_is_incremental(isolated):
    pages = {"https://t.me/s/chan": page("chan", "Chan", [post("chan", 10, JOB), post("chan", 11, ""), post("chan", 12, "short"),
                                                           post("chan", 13, "Android Engineer needed, remote friendly, apply now")])}
    got = []
    r = tp.run(isolated, ["chan"], lambda u: (got.append(u), pages.get(u, page("chan", "Chan", [])))[1], sleep=lambda s: None)
    assert r["channels"] == 1 and r["new_jobs"] == 2 and r["no_preview"] == []
    rows = isolated.execute("SELECT sources FROM jobs ORDER BY id").fetchall()
    src = json.loads(rows[0]["sources"])[0]
    assert src["url"] == "https://t.me/chan/10" and src["chat"] == "Chan" and src["chat_id"] == tp.chat_id_for("chan") < -1_000_000_000_000
    assert isolated.execute("SELECT last_id FROM state WHERE chat_id=?", (tp.chat_id_for("chan"),)).fetchone()[0] == 13
    assert isolated.execute("SELECT username, title FROM chats").fetchone()["username"] == "chan"
    again = tp.run(isolated, ["chan"], lambda u: pages.get(u, page("chan", "Chan", [])), sleep=lambda s: None)
    assert again["new_jobs"] == 0                                                                   # nothing new the second time


def test_walks_back_to_older_pages_on_first_run(isolated):
    p1 = page("c", "C", [post("c", i, f"Flutter Developer opening number {i}, remote") for i in range(30, 40)])
    p2 = page("c", "C", [post("c", i, f"iOS Engineer opening number {i}, remote") for i in range(20, 30)])
    pages = {"https://t.me/s/c": p1, "https://t.me/s/c?before=30": p2, "https://t.me/s/c?before=20": page("c", "C", [])}
    r = tp.run(isolated, ["c"], lambda u: pages[u], sleep=lambda s: None)
    assert r["new_jobs"] == 20


def test_channels_without_preview_or_failing_are_reported_not_fatal(isolated):
    def get(u):
        if "closed" in u:
            return "<html><body>If you have Telegram, you can contact</body></html>"
        if "down" in u:
            raise OSError("boom")
        return page("ok", "Ok", [post("ok", 5, JOB)])
    r = tp.run(isolated, ["closed", "down", "ok"], get, sleep=lambda s: None)
    assert r["no_preview"] == ["closed"] and len(r["failed"]) == 1 and r["channels"] == 1 and r["new_jobs"] == 1
    assert isolated.execute("SELECT last_error FROM source_state WHERE name='Telegram (public)'").fetchone()[0] is None


def test_rate_limit_stops_politely(isolated):
    def get(u):
        raise http.RateLimited("429")
    r = tp.run(isolated, ["a", "b"], get, sleep=lambda s: None)
    assert r["channels"] == 0 and "rate limited" in r["failed"][0] and len(r["failed"]) == 1


def test_config_lists_channels():
    names = mode.public_telegram_channels()
    assert "remotejobss" in names and "relocateme" in names and len(names) == len(set(names)) >= 25
    assert all(n == n.lower() and not n.startswith("#") for n in names)


def test_only_listed_public_channels_reach_the_public_site(isolated, tmp_path, monkeypatch):
    monkeypatch.setattr(mode, "public_telegram_channels", lambda: ["listed"])
    assert mode.is_public_telegram_source({"url": "https://t.me/listed/5"}) and mode.is_public_telegram_source({"url": "https://t.me/LISTED/5"})
    assert not mode.is_public_telegram_source({"url": "https://t.me/other/5"}) and not mode.is_public_telegram_source({"url": None})
    pages = {"https://t.me/s/listed": page("listed", "Listed", [post("listed", 5, JOB)]),
             "https://t.me/s/other": page("other", "Other", [post("other", 5, "Android developer wanted, remote, apply now please")])}
    tp.run(isolated, ["listed", "other"], lambda u: pages.get(u, page("x", "X", [])), sleep=lambda s: None)
    # a post that was read with a personal Telegram login (no t.me link) from the very same channel
    t = "Senior iOS developer, remote, contact @private_recruiter or boss@example.com"
    db.upsert(isolated, parse.analyze(t), t, {"chat": "Listed", "chat_id": -1001111111111, "msg_id": 9, "date": "2026-10-23T10:00:00+00:00"})
    isolated.commit()
    publish.export(isolated, tmp_path / "site")
    jobs = json.loads((tmp_path / "site/data/jobs.json").read_text(encoding="utf-8"))["jobs"]
    assert [j["sources"][0]["url"] for j in jobs] == ["https://t.me/listed/5"] and jobs[0]["sources"][0]["kind"] == "telegram"
    blob = json.dumps(jobs)
    for secret in ("hr@example.com", "recruiter_x", "private_recruiter", "boss@example.com", "other/5"):
        assert secret not in blob
    chans = json.loads((tmp_path / "site/data/channels.json").read_text(encoding="utf-8"))["chats"]
    assert [c["username"] for c in chans] == ["listed"] and chans[0]["url"] == "https://t.me/listed"


def test_posts_matching_no_role_are_not_stored(isolated):
    chat = page("chat", "Chat", [post("chat", 1, "Good morning everyone, what a lovely day for a long walk outside"),
                                  post("chat", 2, "Flutter Developer wanted, remote, apply by message please")])
    r = tp.run(isolated, ["chat"], lambda u: chat if u.endswith("/chat") else page("chat", "Chat", []), sleep=lambda s: None)
    assert r["new_jobs"] == 1 and isolated.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
