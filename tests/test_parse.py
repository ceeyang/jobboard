import json
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import importlib.util

import pytest

from jobboard import db, fetch, notify, parse

# the Telegram client is an optional extra: CI (and anyone using only the public sources) does not have it
needs_telegram = pytest.mark.skipif(importlib.util.find_spec("cli_anything") is None, reason="Telegram client not installed")

FLUTTER = """远程Flutter客户端工程师
薪资：面议

岗位职责
负责短剧AppFlutter客户端的持续迭代
任职要求
2 年以上Flutter开发经验。联系 @hr_flutter 或 jobs@acme.io  https://t.me/hrbot"""
IOS_IM = """海外远程、禁止人在国内远程
✅招聘：ios im大牛
✅工资：30k起 面议，需要视频面试，单休，入职不用提交个人信息
✅联系TG: @XJWBW888"""
JAVA = "🏡 AstroX #CEX\n待招岗位：#Java工程师 #技术\n薪酬福利：USD 4000 - 8000 / 月 5年Java后端 Spring Boot"
BONIU = "#免费广告\n\n招聘 菲律宾技术-Android\n\n🔗详情: https://boneo.io/jobs/69\n官方频道: @boneo 官方人员: @boniuom"


def test_flutter_scores_highest_with_contacts():
    a = parse.analyze(FLUTTER)
    assert a["score"] >= 12 and "flutter" in a["tags"] and a["remote"]
    assert a["title"].startswith("远程Flutter")
    assert a["contacts"]["telegram"] == ["@hr_flutter"]
    assert a["contacts"]["email"] == ["jobs@acme.io"]


def test_email_is_not_reported_as_handle():
    assert parse.contacts("mail me: fiona.w@bydfi.com")["telegram"] == []


def test_ios_flagged_risky():
    a = parse.analyze(IOS_IM)
    assert "ios" in a["tags"] and a["score"] >= 7
    assert any("个人信息" in r for r in a["risks"])
    assert a["salary"] and "30k" in a["salary"]


def test_unrelated_job_scores_zero():
    assert parse.analyze(JAVA)["score"] == 0


def test_web3_is_not_frontend_and_app_alone_is_not_mobile():
    assert parse.role_match("Web3 operations manager, app growth")[0] == 0


def test_channel_boilerplate_handles_ignored():
    a = parse.analyze(BONIU)
    assert a["contacts"]["telegram"] == []
    assert "@boneo" not in a["contacts"]["telegram"]
    assert "android" in a["tags"]


def test_same_post_two_channels_same_fingerprint():
    assert parse.fingerprint(FLUTTER) == parse.fingerprint(FLUTTER.replace("\n", "  \n") + "  ")
    assert parse.fingerprint(FLUTTER) != parse.fingerprint(IOS_IM)


class FakeClient:
    def __init__(self, dialogs):
        self.dialogs = dialogs
        self.calls = []

    def iter_dialogs(self):
        return iter(self.dialogs)

    def iter_messages(self, ent, limit, min_id):
        self.calls.append((ent.name, min_id))
        return iter(sorted([m for m in ent.msgs if m.id > min_id], key=lambda m: -m.id)[:limit])

    def send_message(self, to, text, **kw):
        self.sent = (to, text)


def _dialog(cid, name, msgs):
    ent = NS(name=name, msgs=msgs)
    return NS(id=cid, name=name, entity=ent, is_group=False, is_channel=True)


def _msg(i, text):
    return NS(id=i, message=text, date=datetime.now(timezone.utc))


@needs_telegram
def test_fetch_dedupes_across_channels_and_is_incremental(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "HOME", tmp_path)
    con = db.connect(tmp_path / "t.db")
    c = FakeClient([_dialog(1, "A", [_msg(1, FLUTTER), _msg(2, "short")]),
                    _dialog(2, "B", [_msg(7, FLUTTER), _msg(8, IOS_IM)])])
    s = fetch.run(client=c, con=con)
    assert s["new_jobs"] == 2 and s["chats"] == 2
    row = con.execute("SELECT sources FROM jobs ORDER BY id").fetchone()
    assert {x["chat"] for x in json.loads(row["sources"])} == {"A", "B"}
    s2 = fetch.run(client=c, con=con)
    assert s2["new_jobs"] == 0 and {m for _, m in c.calls[-2:]} == {2, 8}  # resumed from last id


@needs_telegram
def test_digest_only_unnotified_matches(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "HOME", tmp_path)
    con = db.connect(tmp_path / "t.db")
    fetch.run(client=FakeClient([_dialog(1, "A", [_msg(1, FLUTTER), _msg(2, JAVA * 2)])]), con=con)
    c = FakeClient([])
    assert notify.send(con, client=c) == 1
    assert "Flutter" in c.sent[1] and "@hr_flutter" in c.sent[1] and c.sent[0] == "me"
    assert notify.send(con, client=c) == 0  # no repeat


SEEKER = "#求职 #全职 #开发 Daniel 工作经历: 3 years+ in Web3 Role: #React·Engineer #前端 #Flutter"
JAVA_FE = "招聘岗位：Java后端工程师\n要求：熟悉Spring Boot，配合前端和测试完成联调，了解React更佳"


def test_job_seekers_are_not_jobs():
    assert parse.is_seeker(SEEKER) and parse.analyze(SEEKER)["score"] == 0
    assert not parse.is_seeker("#招聘 #求职者友好 Flutter工程师")


def test_body_only_mention_does_not_make_a_match():
    assert parse.analyze(JAVA_FE)["score"] < 5


def test_title_mention_beats_body_mention():
    assert parse.analyze("Flutter工程师\n负责App开发")["score"] > parse.analyze(JAVA_FE)["score"]
    assert parse.analyze("招聘 iOS开发\n远程")["score"] >= 9


def test_reanalyze_updates_rows(tmp_path):
    con = db.connect(tmp_path / "r.db")
    db.upsert(con, parse.analyze(SEEKER), SEEKER, {"chat": "x", "chat_id": 1, "msg_id": 1, "date": "2026-10-23"})
    con.execute("UPDATE jobs SET score=99")
    assert db.reanalyze(con) == 1
    assert con.execute("SELECT score FROM jobs").fetchone()[0] == 0


def test_skill_list_in_body_never_matches():
    t = "服务端负责人\n工作职责：微服务架构\n加分项：了解Flutter、iOS、Android、前端、React，远程办公"
    assert parse.analyze(t)["score"] < 5


def test_titles_prefer_role_line_over_generic_header():
    assert parse.analyze(BONIU)["title"] == "菲律宾技术-Android"
    dj = "#招聘\n🏡 AstroX #CEX\n📚  待招岗位：#前端工程师（高级） #技术\n要求 React"
    assert "前端工程师" in parse.analyze(dj)["title"]
    assert parse.analyze(FLUTTER)["title"].startswith("远程Flutter")


def test_post_links():
    from jobboard import links
    assert links.post_url(-1001, "pubchan", 42) == "https://t.me/pubchan/42"
    assert links.post_url(-1003573581425, None, 264) == "https://t.me/c/3573581425/264"
    assert links.channel_url(-1003573581425, None) == "https://t.me/c/3573581425"
    assert links.post_url(-123456, None, 1) is None  # basic group: no deep link exists


def test_digest_is_capped_and_ordered_by_match(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "HOME", tmp_path)
    con = db.connect(tmp_path / "t.db")
    for i in range(25):
        t = f"Flutter Developer {i}\nremote role at Company{i}"
        db.upsert(con, parse.analyze(t), t, {"chat": "C", "chat_id": 1, "msg_id": i, "date": "2026-10-23"})
    chunks = notify.format_digest(notify.pending(con))
    body = "\n".join(chunks)
    assert body.count("• ") == notify.MAX_ITEMS and "另有 5 条" in body
