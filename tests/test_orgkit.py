"""The files for a GitHub organisation: aggregate engine + one light repository per profile + landing page. No GitHub needed."""
import json
import re
import subprocess

import pytest

from jobboard import cli, orgkit, profile_config as pc


@pytest.fixture(autouse=True)
def clean():
    pc.reset()
    yield
    pc.reset()


def test_profile_repo_is_complete_and_has_no_placeholders_left(tmp_path):
    info = orgkit.render_profile_repo(tmp_path / "backend", "backend", "devopenings")
    files = {p.relative_to(tmp_path / "backend").as_posix() for p in (tmp_path / "backend").rglob("*") if p.is_file()}
    assert {".github/workflows/update.yml", "README.md", ".gitignore", "config/profile.json"} <= files
    for f in files:
        assert not re.search(r"__[A-Z_]+__", (tmp_path / "backend" / f).read_text(encoding="utf-8")), f
    wf = (tmp_path / "backend/.github/workflows/update.yml").read_text(encoding="utf-8")
    assert "ENGINE_REPO: devopenings/jobboard" in wf and "FEED_URL: https://devopenings.github.io/jobboard/data" in wf
    assert "BEST EFFORT" in wf and "4-8 hours" in wf                                       # the schedule is explained where people will look
    assert "不准点" in (tmp_path / "backend/README.md").read_text(encoding="utf-8")
    cfg = pc.validate(json.loads((tmp_path / "backend/config/profile.json").read_text(encoding="utf-8")))
    assert cfg["name"] == "backend" and info["url"] == "https://devopenings.github.io/backend/" and info["roles"] == len(cfg["roles"])


def test_workflow_pins_actions_and_reads_only(tmp_path):
    orgkit.render_profile_repo(tmp_path / "x", "mobile", "o")
    wf = (tmp_path / "x/.github/workflows/update.yml").read_text(encoding="utf-8")
    uses = re.findall(r"uses: (\S+)", wf)
    assert uses and all(re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", u) for u in uses)             # every action pinned to a commit
    assert "contents: read" in wf and "contents: write" not in wf and "set -o pipefail" in wf


def test_custom_title(tmp_path):
    info = orgkit.render_profile_repo(tmp_path / "x", "backend", "o", title="Go 后端")
    assert info["title"] == "Go 后端" and json.loads((tmp_path / "x/config/profile.json").read_text(encoding="utf-8"))["site"]["title"] == "Go 后端"


def test_landing_page_links_everything(tmp_path):
    profiles = [orgkit.render_profile_repo(tmp_path / p, p, "devopenings") for p in ("backend", "mobile")]
    orgkit.render_landing(tmp_path / "landing", "devopenings", profiles, {"title": "技术岗位聚合看板", "description": "全部方向"})
    page = (tmp_path / "landing/index.html").read_text(encoding="utf-8")
    for url in ("https://devopenings.github.io/jobboard/", "https://devopenings.github.io/backend/", "https://devopenings.github.io/mobile/"):
        assert f'href="{url}"' in page
    assert "不准点" in page and (tmp_path / "landing/.nojekyll").exists()


def test_titles_with_markup_are_escaped(tmp_path):
    orgkit.render_landing(tmp_path / "l", "o", [{"title": "<b>x</b>", "description": "a & b", "url": "https://o.github.io/x/"}], {"title": "T", "description": "D"})
    page = (tmp_path / "l/index.html").read_text(encoding="utf-8")
    assert "<b>x</b>" not in page and "&lt;b&gt;x&lt;/b&gt;" in page and "a &amp; b" in page


def test_render_all(tmp_path, capsys):
    cli.main(["org-render", "--org", "devopenings", "--out", str(tmp_path), "--presets", "backend", "frontend"])
    m = json.loads(capsys.readouterr().out)
    assert m["org"] == "devopenings" and [p["repo"] for p in m["profiles"]] == ["backend", "frontend"]
    assert m["landing"]["repo"] == "devopenings.github.io" and m["engine"]["preset"] == "all-tech"
    engine = json.loads((tmp_path / "engine/config/profile.json").read_text(encoding="utf-8"))
    assert engine["name"] == "all-tech" and len(engine["roles"]) > 30                       # the engine collects for every role
    for must in ("jobboard/cli.py", "jobboard/publish.py", ".github/workflows/update.yml", "config/presets/backend.json",
                 "config/telegram_channels.txt", "templates/profile-repo/README.md"):
        assert (tmp_path / "engine" / must).exists(), must
    assert not list((tmp_path / "engine").rglob("*.db")) and not list((tmp_path / "engine").rglob("*.session"))
    assert (tmp_path / "manifest.json").exists()


def test_all_common_roles_have_a_repository():
    assert set(orgkit.PROFILE_PRESETS) <= set(pc.presets()) and "frontend-mobile" not in orgkit.PROFILE_PRESETS
    assert {"frontend", "backend", "mobile", "fullstack", "data-ai", "devops", "qa", "security"} <= set(orgkit.PROFILE_PRESETS)


def test_engine_repo_can_be_the_org_named_repository(tmp_path, capsys):
    cli.main(["org-render", "--org", "devopenings", "--engine-repo", "devopenings", "--out", str(tmp_path), "--presets", "backend"])
    m = json.loads(capsys.readouterr().out)
    assert m["engine"]["url"] == "https://devopenings.github.io/devopenings/"
    wf = (tmp_path / "backend/.github/workflows/update.yml").read_text(encoding="utf-8")
    assert "ENGINE_REPO: devopenings/devopenings" in wf and "FEED_URL: https://devopenings.github.io/devopenings/data" in wf
    prof = (tmp_path / "orgprofile/profile/README.md").read_text(encoding="utf-8")
    assert "https://devopenings.github.io/backend/" in prof and "不准点" in prof and "devopenings/devopenings/blob/main/docs/FORK.md" in prof
    assert m["org_profile"]["repo"] == ".github"


def test_engine_readme_points_at_the_organisation_not_the_original_author(tmp_path, capsys):
    cli.main(["org-render", "--org", "devopenings", "--engine-repo", "devopenings", "--out", str(tmp_path), "--presets", "backend"])
    capsys.readouterr()
    readme = (tmp_path / "engine/README.md").read_text(encoding="utf-8")
    assert "**Aggregate site: https://devopenings.github.io/devopenings/**" in readme and "ceeyang.com" not in readme.split("\n## ")[0]


def test_sites_know_their_own_address(tmp_path, capsys):
    cli.main(["org-render", "--org", "devopenings", "--engine-repo", "devopenings", "--out", str(tmp_path), "--presets", "backend"])
    capsys.readouterr()
    assert json.loads((tmp_path / "engine/config/site.json").read_text()) == {"site_url": "https://devopenings.github.io/devopenings/",
                                                                           "sites_base": "https://devopenings.github.io"}
    assert "JOBBOARD_SITE_URL: https://devopenings.github.io/backend/" in (tmp_path / "backend/.github/workflows/update.yml").read_text(encoding="utf-8")
