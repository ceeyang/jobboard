"""Profiles: what a site looks for is data (config/profile.json + presets), not code."""
import json

import pytest

from jobboard import cli, db, parse, profile, profile_config as pc, publish, roles


@pytest.fixture(autouse=True)
def clean(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "HOME", tmp_path / "home")
    pc.reset()
    yield
    pc.reset()


def use(name):
    return pc.apply(pc.validate(pc.load_preset(name)))


def test_every_preset_is_valid_and_listed():
    names = pc.presets()
    assert {"frontend-mobile", "frontend", "mobile", "fullstack", "backend", "data-ai", "devops", "qa", "security", "web3", "game",
            "product", "design", "management", "all-tech"} <= set(names)
    for n in names:
        cfg = pc.validate(pc.load_preset(n))
        assert cfg["roles"] and cfg["site"]["title"], n


# (preset, titles that must be kept, titles that must NOT be kept)
CASES = [
    ("backend", ["Senior Backend Engineer (Go)", "Java Developer", "后端开发工程师", "Python Engineer - Payments", "Rust Engineer", "Node.js Developer"],
     ["Senior Frontend Engineer", "iOS Engineer", "Sales Manager", "Product Designer", "QA Engineer", "Flutter Developer"]),
    ("frontend", ["Senior Frontend Engineer", "React Developer", "前端开发工程师", "Vue.js Engineer", "Angular Engineer"],
     ["Backend Engineer", "Java Developer", "Data Scientist", "DevOps Engineer", "iOS Engineer", "Senior .NET Full-stack Developer"]),
    ("mobile", ["Flutter Developer", "Senior iOS Engineer", "Android Developer (Kotlin)", "React Native Engineer", "鸿蒙开发工程师", "Mobile Engineer"],
     ["Backend Engineer", "Frontend Engineer", "Data Engineer", "Product Manager"]),
    ("data-ai", ["Machine Learning Engineer", "Data Engineer", "Data Scientist", "AI Engineer (LLM)", "算法工程师"],
     ["Frontend Engineer", "iOS Engineer", "Sales Manager", "QA Engineer"]),
    ("devops", ["DevOps Engineer", "Site Reliability Engineer", "Platform Engineer", "运维工程师"], ["Frontend Engineer", "Data Scientist", "Product Manager"]),
    ("qa", ["QA Engineer", "SDET", "Test Automation Engineer", "测试开发工程师"], ["Backend Engineer", "Sales"]),
    ("security", ["Security Engineer", "Application Security Engineer", "Penetration Tester", "安全工程师"], ["Frontend Engineer", "Sales"]),
    ("web3", ["Solidity Engineer", "Smart Contract Developer", "Blockchain Engineer", "区块链开发"], ["Frontend Engineer"]),
    ("product", ["Product Manager", "Senior Product Manager, Growth", "产品经理"], ["Backend Engineer", "Sales Manager"]),
    ("design", ["Product Designer", "UX Designer", "UI/UX Designer", "设计师"], ["Backend Engineer", "Sales Manager"]),
    ("management", ["Engineering Manager", "Tech Lead", "Staff Engineer", "技术总监"], ["Sales Manager", "Product Manager"]),
    ("fullstack", ["Full Stack Engineer", "Fullstack Developer (Node/React)", "全栈工程师"], ["Backend Engineer", "Data Scientist", "Product Designer"]),
    ("game", ["Unity Developer", "Unreal Engine Engineer", "Game Developer", "游戏客户端开发"], ["Backend Engineer", "Sales"]),
    ("all-tech", ["Senior Backend Engineer", "Flutter Developer", "Data Scientist", "DevOps Engineer", "Product Manager", "Security Engineer"],
     ["Sales Manager", "Recruiter", "Accountant"]),
]


@pytest.mark.parametrize("preset,keep,drop", CASES, ids=[c[0] for c in CASES])
def test_preset_keeps_its_jobs_and_drops_the_others(preset, keep, drop):
    use(preset)
    assert [t for t in keep if pc.classify(t)[0] < 5] == []
    assert [t for t in drop if pc.classify(t)[0] >= 5] == []


def test_original_profile_is_reproduced_by_frontend_mobile():
    """The preset that stands in for the project's original hard-coded profile must score like the defaults."""
    titles = ["Senior Flutter Developer", "iOS Engineer", "Android Engineer", "React Native Developer", "Frontend Engineer", "Senior back-end Engineer",
              "Technical Product Manager", "Senior .NET Full-stack Developer", "Software Engineer", "Mobile Developer (iOS/Android)"]
    before = [parse.role_match(t, "", True) for t in titles]
    use("frontend-mobile")
    after = [parse.role_match(t, "", True) for t in titles]
    assert [s for s, _ in after] == [s for s, _ in before]


def test_reset_restores_the_defaults():
    original = (profile.NEG_TITLE, profile.STACK_MISMATCH, profile.GENERIC_DEV_TITLE)
    use("backend")
    assert profile.NEG_TITLE != original[0] and pc.current()["site"]["title"] == "后端岗位看板"
    pc.reset()
    assert (profile.NEG_TITLE, profile.STACK_MISMATCH, profile.GENERIC_DEV_TITLE) == original and pc.current() is None


@pytest.mark.parametrize("patch,message", [
    ({"roles": []}, "roles must be a list"),
    ({"roles": [{"key": "Bad Key", "label": "x", "keywords": ["abc"]}]}, "role key"),
    ({"roles": [{"key": "a", "label": "x", "keywords": ["a"]}]}, "at least 2 characters"),
    ({"roles": [{"key": "a", "label": "x", "keywords": ["abc"], "weight": 11}]}, "weight must be from 1 to 10"),
    ({"roles": [{"key": "a", "label": "x", "keywords": ["abc"]}, {"key": "a", "label": "y", "keywords": ["abc"]}]}, "appears twice"),
    ({"specific_roles": ["nope"]}, "unknown role key"),
    ({"exclude_title": ["(unclosed"]}, "not a valid regular expression"),
    ({"search_terms": 99}, "from 1 to 40"),
    ({"site": {"title": "x" * 41}}, "limited to 40"),
])
def test_validation_explains_what_is_wrong(patch, message):
    cfg = {"roles": [{"key": "ok", "label": "OK", "keywords": ["okay"], "weight": 5}], **patch}
    with pytest.raises(ValueError, match=message):
        pc.validate(cfg)


def test_minimal_profile_gets_sensible_defaults():
    cfg = pc.validate({"roles": [{"key": "go", "label": "Go", "keywords": ["golang"]}]})
    assert cfg["roles"][0]["weight"] == 6 and cfg["site"]["title"] == "岗位看板" and cfg["search_terms"] == 6 and cfg["exclude_title"] == []
    pc.apply(cfg)
    assert pc.classify("Senior Golang Developer")[0] >= 5 and pc.classify("Sales Manager")[0] == 0


def test_init_writes_a_profile_and_refuses_to_overwrite(tmp_path, capsys):
    out = tmp_path / "my" / "profile.json"
    cli.main(["init", "--preset", "backend", "--title", "我的后端看板", "--out", str(out)])
    cfg = json.loads(out.read_text(encoding="utf-8"))
    assert cfg["site"]["title"] == "我的后端看板" and len(cfg["roles"]) == 10 and "jobboard profile --try" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        cli.main(["init", "--preset", "backend", "--out", str(out)])
    cli.main(["init", "--preset", "mobile", "--out", str(out), "--force"])
    assert json.loads(out.read_text(encoding="utf-8"))["site"]["title"] == "移动端岗位看板"
    with pytest.raises(SystemExit):
        cli.main(["init", "--preset", "no-such-preset", "--out", str(tmp_path / "x.json")])


def test_profile_command_scores_titles(tmp_path, capsys):
    out = tmp_path / "p.json"
    cli.main(["init", "--preset", "backend", "--out", str(out)])
    capsys.readouterr()
    cli.main(["profile", "--path", str(out), "--try", "Senior Go Engineer", "Sales Manager"])
    text = capsys.readouterr().out
    assert "KEEP" in text and "Senior Go Engineer" in text and "skip" in text and "后端岗位看板" in text


def test_search_terms_follow_the_profile():
    use("backend")
    terms = roles.search_terms(db.connect(), 6)
    assert terms and all(t.isascii() for t in terms) and "flutter" not in terms
    pc.reset()
    use("all-tech")
    assert len(roles.search_terms(db.connect(), 6)) > 6                    # the aggregate profile asks for more search words


def test_export_uses_the_profile_title_and_role_buttons(tmp_path):
    use("backend")
    con = db.connect()
    meta = publish.export(con, tmp_path / "site")
    assert meta["site"]["title"] == "后端岗位看板"
    page = (tmp_path / "site/index.html").read_text(encoding="utf-8")
    assert "<title>后端岗位看板</title>" in page and '<span class="bname">后端岗位看板</span>' in page
    chips = json.loads((tmp_path / "site/data/roles.json").read_text(encoding="utf-8"))["roles"]
    assert {c["key"] for c in chips} >= {"backend", "java", "go", "python"} and all(c["enabled"] for c in chips)


# ---- aggregate feed -> profile sites -----------------------------------------------------------------------
def _aggregate_feed(tmp_path):
    """An aggregate site's data folder: jobs of every kind, found with the all-tech profile."""
    use("all-tech")
    con = db.connect()
    titles = ["Senior Backend Engineer (Go)", "Staff Frontend Engineer (React)", "Flutter Developer", "Data Scientist", "DevOps Engineer",
              "Product Manager", "Java Developer", "Sales Manager"]
    for i, t in enumerate(titles, 1):
        text = f"{t}\nAcme · Remote\n{'remote, ' * 3}\nWrite to hr@example.com or @recruiter_x"
        a = parse.analyze(text, "", structured=True)
        a.update(title=t, company="Acme", fingerprint=parse.fingerprint(t), remote=True, location="Remote")
        db.upsert(con, a, text, {"chat": "RemoteOK", "chat_id": -1, "msg_id": i, "date": "2026-10-20T10:00:00+00:00", "url": f"https://r/{i}"})
    con.commit()
    publish.export(con, tmp_path / "agg")
    return str(tmp_path / "agg" / "data"), con


def test_profile_site_is_built_from_the_aggregate_feed(tmp_path):
    feed, _ = _aggregate_feed(tmp_path)
    pc.reset()
    cfg = pc.validate(pc.load_preset("backend"))
    pc.apply(cfg)
    meta = publish.build_from_feed(feed, tmp_path / "backend-site", cfg)
    jobs = json.loads((tmp_path / "backend-site/data/jobs.json").read_text(encoding="utf-8"))["jobs"]
    titles = {j["title"] for j in jobs if j["score"] >= 5}
    assert titles == {"Senior Backend Engineer (Go)", "Java Developer"}                       # this profile's jobs only
    assert meta["site"]["title"] == "后端岗位看板" and meta["feed"]["jobs"] == 7 and meta["feed"]["kept"] == len(jobs)
    go = next(j for j in jobs if "Go" in j["title"])
    assert {"backend", "go"} <= set(go["tags"])                                                # re-tagged with THIS profile's roles
    assert "hr@example.com" not in json.dumps(jobs) and "recruiter_x" not in json.dumps(jobs)
    chips = json.loads((tmp_path / "backend-site/data/roles.json").read_text(encoding="utf-8"))["roles"]
    assert "go" in {c["key"] for c in chips} and "flutter" not in {c["key"] for c in chips}


def test_two_profiles_share_one_feed(tmp_path):
    feed, _ = _aggregate_feed(tmp_path)
    got = {}
    for name in ("frontend", "mobile", "data-ai"):
        pc.reset()
        cfg = pc.validate(pc.load_preset(name))
        pc.apply(cfg)
        publish.build_from_feed(feed, tmp_path / name, cfg)
        got[name] = {j["title"] for j in json.loads((tmp_path / name / "data/jobs.json").read_text(encoding="utf-8"))["jobs"] if j["score"] >= 5}
    assert got == {"frontend": {"Staff Frontend Engineer (React)"}, "mobile": {"Flutter Developer"}, "data-ai": {"Data Scientist"}}


def test_incomplete_feed_is_refused(tmp_path):
    feed, _ = _aggregate_feed(tmp_path)
    head = json.loads((tmp_path / "agg/data/jobs.json").read_text(encoding="utf-8"))
    head["total"] += 5                                                                        # a chunk went missing
    (tmp_path / "agg/data/jobs.json").write_text(json.dumps(head), encoding="utf-8")
    pc.reset()
    cfg = pc.validate(pc.load_preset("backend"))
    with pytest.raises(RuntimeError, match="incomplete feed"):
        publish.build_from_feed(feed, tmp_path / "out", cfg)


def test_build_command(tmp_path, capsys):
    feed, _ = _aggregate_feed(tmp_path)
    pc.reset()
    prof = tmp_path / "profile.json"
    cli.main(["init", "--preset", "frontend", "--out", str(prof)])
    capsys.readouterr()
    cli.main(["build", "--feed", feed, "--profile", str(prof), "--out", str(tmp_path / "site")])
    out = json.loads(capsys.readouterr().out)
    assert out["site"]["title"] == "前端岗位看板" and (tmp_path / "site/index.html").exists()


def test_a_role_is_listed_once_even_if_title_and_site_tags_both_name_it():
    use("backend")
    score, tags = parse.role_match("Software Engineer AI Coding Tasks\nAskable", "dev python javascript golang java python", True)
    assert len(tags) == len(set(tags)) and "python" in tags


def test_levels_validation_and_defaults():
    base = {"roles": [{"key": "ok", "label": "OK", "keywords": ["okay"]}]}
    assert pc.validate(base)["levels"] == {"recommended": 5, "strict": 10}
    assert pc.validate(pc.load_preset("all-tech"))["levels"] == {"recommended": 12, "strict": 14}
    for bad in ({"recommended": 10, "strict": 5}, {"recommended": 1, "strict": 5}, {"recommended": 5, "strict": 25}, {"recommended": "x"}):
        with pytest.raises(ValueError, match="levels"):
            pc.validate({**base, "levels": bad})
