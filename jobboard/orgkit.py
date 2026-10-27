"""Material for a GitHub organisation that hosts ONE aggregate site (the engine: it collects jobs for every common tech role)
plus ONE very light repository per profile (front-end, back-end, mobile ...). The profile repositories do not scrape anything:
they read the aggregate site's published data and re-filter it with their own `config/profile.json`.

    jobboard org-render --org devopenings --out /tmp/org     # writes engine/, <preset>/ ... and landing/ + a manifest

scripts/deploy_org.sh pushes the result with `gh`. Rendering is plain file generation so it can be tested without GitHub."""
import html
import json
import shutil
import subprocess
from pathlib import Path

from jobboard import profile_config

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates" / "profile-repo"
ENGINE_REPO = "jobboard"
AGGREGATE_PRESET = "all-tech"
# presets that get their own repository (the personal "frontend-mobile" and the aggregate itself do not)
PROFILE_PRESETS = ["frontend", "mobile", "fullstack", "backend", "data-ai", "devops", "qa", "security", "web3", "game", "product",
                   "design", "management"]


def pages_url(org: str, repo: str) -> str:
    return f"https://{org}.github.io/{repo}"


def _fill(text: str, values: dict) -> str:
    for k, v in values.items():
        text = text.replace(f"__{k}__", v)
    return text


def render_profile_repo(dest: Path, preset: str, org: str, engine_repo: str = ENGINE_REPO, title: str | None = None) -> dict:
    cfg = profile_config.load_preset(preset)
    cfg["name"] = preset
    cfg["site"] = {**cfg.get("site", {})}
    if title:
        cfg["site"]["title"] = title
    profile_config.validate(cfg)
    values = {"ENGINE_REPO": f"{org}/{engine_repo}", "FEED_URL": f"{pages_url(org, engine_repo)}/data", "TITLE": cfg["site"]["title"],
              "DESCRIPTION": cfg["site"].get("description", ""), "PRESET": preset, "AGGREGATE_URL": pages_url(org, engine_repo) + "/",
              "SITE_URL": pages_url(org, preset) + "/"}
    dest = Path(dest)
    for src in TEMPLATE.rglob("*"):
        if src.is_file():
            target = dest / src.relative_to(TEMPLATE)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(_fill(src.read_text(encoding="utf-8"), values), encoding="utf-8")
    (dest / "config").mkdir(exist_ok=True)
    (dest / "config/profile.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return {"repo": preset, "preset": preset, "title": cfg["site"]["title"], "description": cfg["site"].get("description", ""),
            "url": pages_url(org, preset) + "/", "roles": len(cfg["roles"])}


def prepare_engine(dest: Path, profile: str = AGGREGATE_PRESET, org: str | None = None, engine_repo: str = ENGINE_REPO) -> dict:
    """The engine repository = this project's files (tracked, plus new ones that .gitignore does not exclude), with the aggregate profile as its config/profile.json."""
    dest = Path(dest)
    files = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=ROOT, capture_output=True, text=True,
                           check=True).stdout.splitlines()
    for f in files:
        src = ROOT / f
        if src.is_file():
            (dest / f).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest / f)
    readme = dest / "README.md"
    if org and readme.exists():     # the copied README talks about the original author's own site: point it at this organisation instead
        lines = readme.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if line.startswith("**Public site:"):
                lines[i] = (f"**Aggregate site: {pages_url(org, engine_repo)}/** - every common tech role in one place; the role sites "
                            f"(front-end, back-end, mobile, ...) are listed at https://{org}.github.io/ and read this site's data feed.")
                break
        readme.write_text("\n".join(lines) + "\n", encoding="utf-8")
    cfg = profile_config.load_preset(profile)
    cfg["name"] = profile
    profile_config.validate(cfg)
    (dest / "config").mkdir(parents=True, exist_ok=True)
    if org:    # where this site lives: absolute links in the feeds, and the overview links to the role sites
        (dest / "config/site.json").write_text(json.dumps({"site_url": pages_url(org, engine_repo) + "/", "sites_base": f"https://{org}.github.io"}, indent=1) + "\n", encoding="utf-8")
    (dest / "config/profile.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return {"repo": ENGINE_REPO, "preset": profile, "title": cfg["site"]["title"], "description": cfg["site"].get("description", ""),
            "files": len(files), "roles": len(cfg["roles"])}


_CSS = """:root{--bg:#fff;--fg:#14161f;--mut:#667;--card:#f6f7fb;--line:#e3e5ee;--ac:#5b5bf0}
@media(prefers-color-scheme:dark){:root{--bg:#0e1017;--fg:#e9ebf5;--mut:#9aa0b8;--card:#171a25;--line:#262a3a;--ac:#8c8cff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.6 system-ui,-apple-system,"PingFang SC","Noto Sans SC",sans-serif}
main{max-width:980px;margin:0 auto;padding:40px 16px 64px}h1{font-size:28px;margin:0 0 6px}p.lead{color:var(--mut);margin:0 0 28px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:14px}
a.card{display:block;padding:16px 18px;background:var(--card);border:1px solid var(--line);border-radius:14px;color:inherit;text-decoration:none}
a.card:hover{border-color:var(--ac)}a.card b{display:block;font-size:17px;margin-bottom:4px}a.card span{color:var(--mut);font-size:14px}
a.card.all{grid-column:1/-1;border-color:var(--ac)}footer{margin-top:34px;color:var(--mut);font-size:13px}"""


def render_landing(dest: Path, org: str, profiles: list[dict], aggregate: dict, engine_repo: str = ENGINE_REPO) -> None:
    e = html.escape
    cards = [f'<a class="card all" href="{e(pages_url(org, engine_repo))}/"><b>{e(aggregate["title"])}</b>'
             f'<span>{e(aggregate["description"])}（全部方向，岗位最多）</span></a>']
    cards += [f'<a class="card" href="{e(p["url"])}"><b>{e(p["title"])}</b><span>{e(p["description"])}</span></a>' for p in profiles]
    page = (f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{e(org)} · 技术岗位看板</title><style>{_CSS}</style></head><body><main>'
            f'<h1>技术岗位看板</h1><p class="lead">按方向分开的公开岗位看板，数据来自远程招聘站、企业招聘页、政府就业服务、GitHub 招聘仓库和公开的 Telegram 频道。'
            f'只显示摘要和原帖链接。</p><div class="grid">{"".join(cards)}</div>'
            f'<footer>自动更新，但 GitHub 的定时触发不准点（近期通常每 4–8 小时一次），每个站点右上角会显示上次更新时间。'
            f'每个方向都是一个独立仓库，只需一份关键词配置，欢迎 fork：<a href="https://github.com/{e(org)}/{e(engine_repo)}">{e(org)}/{e(engine_repo)}</a></footer>'
            f'</main></body></html>')
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "index.html").write_text(page, encoding="utf-8")
    (dest / ".nojekyll").write_text("")


def render_org_profile(dest: Path, org: str, profiles: list[dict], aggregate: dict, engine_repo: str = ENGINE_REPO) -> None:
    """The text shown at github.com/<org> (it comes from the special repository `.github`, file profile/README.md)."""
    rows = "\n".join(f"| [{p['title']}]({p['url']}) | {p['description']} |" for p in profiles)
    text = (f"# 技术岗位看板\n\n按方向分开的公开岗位看板：数据来自远程招聘站、企业招聘页、政府就业服务、GitHub 招聘仓库和公开的 Telegram 频道，"
            f"只显示摘要和原帖链接。\n\n**[{aggregate['title']}]({pages_url(org, engine_repo)}/)**（全部方向）　·　"
            f"[首页](https://{org}.github.io/)\n\n| 方向 | 说明 |\n|---|---|\n{rows}\n\n"
            f"**自动更新，但不准点**：GitHub 的定时触发是尽力而为（实测近期每 4–8 小时一次），每个站点右上角会显示上次更新时间。\n\n"
            f"想要自己的方向？每个站点就是一份关键词配置，见 [部署指南](https://github.com/{org}/{engine_repo}/blob/main/docs/FORK.md)。\n")
    (Path(dest) / "profile").mkdir(parents=True, exist_ok=True)
    (Path(dest) / "profile/README.md").write_text(text, encoding="utf-8")


def render_all(out: Path, org: str, engine_repo: str = ENGINE_REPO, presets: list[str] | None = None) -> dict:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    engine = prepare_engine(out / "engine", org=org, engine_repo=engine_repo)
    profiles = [render_profile_repo(out / p, p, org, engine_repo) for p in (presets or PROFILE_PRESETS)]
    render_landing(out / "landing", org, profiles, engine, engine_repo)
    render_org_profile(out / "orgprofile", org, profiles, engine, engine_repo)
    manifest = {"org": org, "engine": {**engine, "dir": "engine", "url": pages_url(org, engine_repo) + "/"},
                "profiles": [{**p, "dir": p["repo"]} for p in profiles],
                "landing": {"repo": f"{org}.github.io", "dir": "landing", "url": f"https://{org}.github.io/"},
                "org_profile": {"repo": ".github", "dir": "orgprofile"}}
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return manifest
