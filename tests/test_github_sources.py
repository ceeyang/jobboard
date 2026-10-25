"""The "GitHub" source group: jobs kept in public repositories. No network."""
import json

import pytest

from jobboard import db, mode, profile, publish
from jobboard.sources import github, run


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "HOME", tmp_path / "home")
    profile.set_rules(None)
    yield db.connect()
    profile.set_rules(None)


@pytest.mark.parametrize("title,expected", [
    ("[Remoto] Full Stack Developer Pleno na Rotha IA", ("Remoto", "Full Stack Developer Pleno", "Rotha IA")),
    ("[Joinville/SC] [Híbrido] Back-end Developer at Acme Corp", ("Joinville/SC, Híbrido", "Back-end Developer", "Acme Corp")),
    ("[Remoto] Mobile Engineer (Strider USD)", ("Remoto", "Mobile Engineer (Strider USD)", "")),
    ("Flutter Developer", ("", "Flutter Developer", "")),
    ("[São Paulo] Flutter Developer na XP Inc", ("São Paulo", "Flutter Developer", "XP Inc")),
])
def test_parse_issue_title(title, expected):
    assert github.parse_issue_title(title) == expected


def issue(n, title, body="", labels=(), pr=False):
    d = {"number": n, "title": title, "body": body, "labels": [{"name": x} for x in labels], "created_at": "2026-10-20T10:00:00Z",
         "html_url": f"https://github.com/o/vagas/issues/{n}"}
    if pr:
        d["pull_request"] = {}
    return d


def test_issue_board(isolated):
    batch = [issue(1, "[Remoto] Flutter Developer na Rotha IA", "Stack: Flutter.<br>Candidate: hr@example.com", ["PJ", "Remoto"]),
             issue(2, "[São Paulo] iOS Engineer", "Presencial", ["CLT"]), issue(3, "Update README", pr=True)]
    calls = []
    items = github.issues("o/vagas", lambda url: (calls.append(url), batch)[1])
    assert len(calls) == 1 and [i["id"] for i in items] == ["o/vagas#1", "o/vagas#2"]            # PR skipped, one page was enough
    a, b = items
    assert a["title"] == "Flutter Developer" and a["company"] == "Rotha IA" and a["location"] == "Remoto" and a["remote"]
    assert a["tags"] == ["PJ", "Remoto"] and a["url"].endswith("/issues/1") and a["date"].startswith("2026-10-20")
    assert b["company"] == "o/vagas" and not b["remote"]                                          # no company in the title: fall back to the repo


def test_issue_board_reads_a_second_page_when_the_first_is_full(isolated):
    full = [issue(i, f"[Remoto] Flutter Developer {i}") for i in range(100)]
    pages = []
    items = github.issues("o/vagas", lambda url: (pages.append(url), full if url.endswith("&page=1") else [issue(500, "[Remoto] iOS Engineer")])[1])
    assert len(pages) == 2 and len(items) == 101


REBASE = {"jobs": [
    {"number": 7, "state": "open", "title": "[远程] 加密公司诚聘#Flutter开发 薪水$3000", "company": "加密公司", "location": "远程", "salary": "$3000",
     "remote": True, "summary": "做钱包 App", "responsibilities": "写 Flutter", "requirements": "3 年经验", "employment_type": "full-time",
     "work_mode": "远程", "created_at": "2026-10-22T07:59:45Z", "url": "https://github.com/rebase-network/who-is-hiring/issues/7",
     "contact_channels": ["email:hr@example.com", "telegram:@xxbteo"]},
    {"number": 8, "state": "closed", "title": "x", "url": "u"}]}


def test_rebase_ignores_contacts_and_closed_jobs(isolated):
    (it,) = github.rebase(lambda url: REBASE)
    assert it["title"] == "加密公司诚聘#Flutter开发 薪水$3000" and it["company"] == "加密公司" and it["remote"] and it["salary"] == "$3000"
    assert "hr@example.com" not in json.dumps(it) and "xxbteo" not in json.dumps(it) and "Flutter" in it["text"]


SIMPLIFY = [
    {"id": "a", "active": True, "is_visible": True, "title": "Mobile Developer Intern", "company_name": "Acme", "url": "https://acme/1",
     "locations": ["NYC", "Remote in USA", "SF", "LA"], "date_posted": 1792800000, "sponsorship": "Offers Sponsorship",
     "category": "Software", "terms": ["Summer 2027"], "degrees": ["Bachelor's"]},
    {"id": "b", "active": False, "is_visible": True, "title": "iOS Engineer", "company_name": "Old", "url": "https://old", "locations": []},
    {"id": "c", "active": True, "is_visible": True, "title": "Hardware Engineer", "company_name": "X", "url": "https://x", "locations": []},
    {"id": "d", "active": True, "is_visible": True, "title": "Android Developer", "company_name": "Y", "url": "https://y/1", "locations": ["Austin"],
     "date_posted": 1792800000, "sponsorship": "Does Not Offer Sponsorship", "category": "Software"}]


def test_simplify_listings(isolated):
    items = {i["company"]: i for i in github.simplify("SimplifyJobs/New-Grad-Positions", lambda url: SIMPLIFY)}
    assert set(items) == {"Acme", "Y"}                                                            # inactive and non-matching titles dropped
    assert items["Acme"]["visa"] == "yes" and items["Acme"]["remote"] and items["Acme"]["location"] == "NYC, Remote in USA, SF +1"
    assert items["Y"]["visa"] == "" and "No visa sponsorship" in items["Y"]["text"]
    assert run.to_job(items["Y"], "x", -9)[0]["visa"] == ""                                       # an explicit refusal stays a refusal


def test_sources_and_kind():
    names = [s[0] for s in github.sources()]
    assert names[0] == "GitHub: Rebase who-is-hiring" and "GitHub: frontendbr/vagas" in names and "GitHub: SimplifyJobs New-Grad" in names
    ids = [s[1] for s in run.all_sources()]
    assert len(set(ids)) == len(ids) and all(-100 < i < 0 for i in ids)
    assert mode.kind("GitHub: frontendbr/vagas", -33) == "github" and mode.allowed("GitHub: anything", "public")
    public = [s[0] for s in run.all_sources("public")]
    assert "LinkedIn" not in public and "GitHub: frontendbr/vagas" in public


def test_public_export_has_github_group_and_no_contacts(isolated, tmp_path):
    body = "Send your CV to boss@example.com or ping @some_recruiter. Remote."
    items = github.issues("o/vagas", lambda url: [issue(1, "[Remoto] Flutter Developer na Rotha IA", body)])
    a, t, s = run.to_job(items[0], "GitHub: o/vagas", -33)
    db.upsert(isolated, a, t, s)
    isolated.commit()
    publish.scrub_db(isolated)
    publish.export(isolated, tmp_path / "site")
    jobs = json.loads((tmp_path / "site/data/jobs.json").read_text(encoding="utf-8"))["jobs"]
    assert jobs[0]["sources"][0]["kind"] == "github" and jobs[0]["sources"][0]["chat"] == "GitHub: o/vagas"
    blob = json.dumps(jobs)
    assert "boss@example.com" not in blob and "some_recruiter" not in blob and jobs[0]["sources"][0]["url"].endswith("/issues/1")
