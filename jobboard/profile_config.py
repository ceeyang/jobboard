"""A profile says what a site is about: the roles to look for (keywords + weight), what to leave out, and the site text.
It lives in config/profile.json - start from a preset with `jobboard init --preset backend` and edit the keywords.

    {"site": {"title": "...", "description": "..."},
     "roles": [{"key": "go", "label": "Go", "keywords": ["golang", "go developer"], "weight": 8}],
     "specific_roles": ["go"],          # a site tag naming one of these roles may rescue a generic title ("Software Engineer")
     "generic_title": "engineer|developer|...",   # what counts as a generic developer title
     "exclude_title": ["front-?end", "sales"],    # titles matching these never get rescued by tags
     "stack_mismatch": [], "stack_safe_roles": [], "stack_safe_title": "",   # optional: cap the score when the title names another stack
     "search_terms": 6,                 # how many English keywords sources with a search box (EURES, Sweden, ...) are asked for
     "levels": {"recommended": 5, "strict": 10}}   # the score at which the page's "recommended" and "strict" buttons cut (a site with many roles scores higher: raise them)

Everything is plain data; no code changes are needed to point a site at another kind of job."""
import json
import os
import re
from pathlib import Path

from jobboard import profile, roles

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "config" / "profile.json"
PRESET_DIR = ROOT / "config" / "presets"
NEVER = r"(?!)"                       # a regex that matches nothing
_KEYS = ("SPECIFIC_TAGS", "GENERIC_DEV_TITLE", "NEG_TITLE", "STACK_MISMATCH", "STACK_SAFE_ROLES", "STACK_SAFE_TITLE")
_ORIGINAL = {k: getattr(profile, k) for k in _KEYS}
_current: dict | None = None


def presets() -> list[str]:
    return sorted(p.stem for p in PRESET_DIR.glob("*.json"))


def load_preset(name: str) -> dict:
    p = PRESET_DIR / f"{name}.json"
    if not p.exists():
        raise ValueError(f"unknown preset {name!r}; available: {', '.join(presets())}")
    return json.loads(p.read_text(encoding="utf-8"))


def _regex_list(cfg: dict, key: str) -> list[str]:
    items = cfg.get(key) or []
    if not isinstance(items, list) or not all(isinstance(x, str) and x for x in items):
        raise ValueError(f"{key} must be a list of non-empty regular expressions")
    for x in items:
        try:
            re.compile(x)
        except re.error as exc:
            raise ValueError(f"{key}: {x!r} is not a valid regular expression ({exc})")
    return items


def validate(cfg: dict) -> dict:
    """Normalise a profile and explain what is wrong in plain words."""
    if not isinstance(cfg, dict):
        raise ValueError("the profile must be a JSON object")
    site = cfg.get("site") or {}
    title, desc = str(site.get("title") or "岗位看板").strip(), str(site.get("description") or "").strip()
    if len(title) > 40 or len(desc) > 240:
        raise ValueError("site.title is limited to 40 characters and site.description to 240")
    rs = cfg.get("roles")
    if not isinstance(rs, list) or not 1 <= len(rs) <= 80:
        raise ValueError("roles must be a list with 1 to 80 entries")
    out, seen = [], set()
    for r in rs:
        r = r or {}
        key, label = str(r.get("key", "")).strip(), str(r.get("label", "")).strip()
        kws = [str(k).strip() for k in r.get("keywords") or [] if str(k).strip()]
        if not re.fullmatch(r"[a-z0-9_-]{1,30}", key):
            raise ValueError(f"role key {key!r}: use lowercase letters, digits, - or _ (max 30)")
        if key in seen:
            raise ValueError(f"role key {key!r} appears twice")
        if not label or len(label) > 24:
            raise ValueError(f"role {key!r}: label must be 1-24 characters")
        if not kws or any(len(k) < 2 for k in kws):
            raise ValueError(f"role {key!r}: needs at least one keyword, each at least 2 characters (a single letter matches far too much)")
        try:
            w = int(r.get("weight", 6))
        except (TypeError, ValueError):
            raise ValueError(f"role {key!r}: weight must be a whole number from 1 to 10")
        if not 1 <= w <= 10:
            raise ValueError(f"role {key!r}: weight must be from 1 to 10")
        seen.add(key)
        out.append({"key": key, "label": label, "keywords": kws, "weight": w})
    for k in ("specific_roles", "stack_safe_roles"):
        bad = [x for x in (cfg.get(k) or []) if x not in seen]
        if bad:
            raise ValueError(f"{k}: unknown role key(s) {bad}")
    generic = str(cfg.get("generic_title") or r"engineer|developer|programmer|engineering|\bswe\b|software")
    safe_title = str(cfg.get("stack_safe_title") or "")
    for name, rx in (("generic_title", generic), ("stack_safe_title", safe_title)):
        try:
            re.compile(rx)
        except re.error as exc:
            raise ValueError(f"{name} is not a valid regular expression ({exc})")
    try:
        n_terms = int(cfg.get("search_terms", 6))
    except (TypeError, ValueError):
        raise ValueError("search_terms must be a whole number from 1 to 40")
    if not 1 <= n_terms <= 40:
        raise ValueError("search_terms must be a whole number from 1 to 40")
    lv = cfg.get("levels") or {}
    try:
        rec, strict = int(lv.get("recommended", 5)), int(lv.get("strict", 10))
    except (TypeError, ValueError):
        raise ValueError("levels.recommended and levels.strict must be whole numbers")
    if not 2 <= rec < strict <= 20:
        raise ValueError("levels: need 2 <= recommended < strict <= 20 (the page's buttons are 'loose' = 1, 'recommended', 'strict')")
    return {"name": str(cfg.get("name") or ""), "site": {"title": title, "description": desc}, "roles": out, "search_terms": n_terms,
            "levels": {"recommended": rec, "strict": strict},
            "specific_roles": list(cfg.get("specific_roles") or []), "generic_title": generic,
            "exclude_title": _regex_list(cfg, "exclude_title"), "stack_mismatch": _regex_list(cfg, "stack_mismatch"),
            "stack_safe_roles": list(cfg.get("stack_safe_roles") or []), "stack_safe_title": safe_title}


def load(path: str | Path | None = None) -> dict | None:
    p = Path(path) if path else Path(os.environ.get("JOBBOARD_PROFILE") or PATH)      # JOBBOARD_PROFILE: use another file without touching config/
    return validate(json.loads(p.read_text(encoding="utf-8"))) if p.exists() else None


def apply(cfg: dict) -> dict:
    """Make the whole matching engine (parse.py, ATS prefilters, search terms) work for this profile."""
    global _current
    profile.set_rules([(r["key"], roles.kw_regex(r["keywords"]), r["weight"]) for r in cfg["roles"]])
    profile.SPECIFIC_TAGS = set(cfg["specific_roles"])
    profile.GENERIC_DEV_TITLE = cfg["generic_title"]
    profile.NEG_TITLE = "|".join(cfg["exclude_title"]) or NEVER
    profile.STACK_MISMATCH = "|".join(cfg["stack_mismatch"]) or NEVER
    profile.STACK_SAFE_ROLES = set(cfg["stack_safe_roles"])
    profile.STACK_SAFE_TITLE = cfg["stack_safe_title"] or NEVER
    _current = cfg
    return cfg


def activate(path: str | Path | None = None) -> dict | None:
    cfg = load(path)
    return apply(cfg) if cfg else None


def reset() -> None:
    """Back to the built-in defaults (used by tests and by commands that keep their roles in the database)."""
    global _current
    for k, v in _ORIGINAL.items():
        setattr(profile, k, v)
    profile.set_rules(None)
    _current = None


def current() -> dict | None:
    return _current


def roles_for_ui(cfg: dict) -> list[dict]:
    """The same shape the board's role buttons get from the database."""
    return [{"id": i + 1, "key": r["key"], "label": r["label"], "keywords": ", ".join(r["keywords"]), "weight": r["weight"],
             "enabled": 1, "position": i} for i, r in enumerate(cfg["roles"])]


def classify(title: str, text: str = "", hint: str = "", structured: bool = True) -> tuple[int, list[str]]:
    """Score one posting under the active profile (what `jobboard profile --try` prints)."""
    from jobboard import parse
    return parse.role_match(f"{title}\n{text}".strip(), hint, structured)
