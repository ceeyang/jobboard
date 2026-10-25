"""User-managed roles ("常见岗位"): a name, comma-separated keywords and a weight.
Keywords become the regexes that role_match() uses; the defaults reproduce profile.ROLE_RULES."""
import re

from jobboard import profile

DEFAULT_ROLES = [  # key, label, keywords, weight
    ("flutter", "Flutter", "flutter, dart", 10),
    ("ios", "iOS", "ios, swift, swiftui, objective-c, iphone", 7),
    ("android", "Android", "android, kotlin", 7),
    ("mobile", "移动端", "移动端, 客户端, app开发, app 开发, 跨端, 跨平台, react native, mobile", 6),
    ("frontend", "前端", "前端, front-end, frontend, react, vue, typescript, h5, 小程序", 5),
    ("fullstack", "全栈", "全栈, full-stack, fullstack, full stack", 4),
]
_ASCII_WORD = re.compile(r"[A-Za-z0-9 .+#_-]+")


def split_keywords(text: str) -> list[str]:
    return [k.strip() for k in re.split(r"[,，;；\n]+", text or "") if k.strip()]


def kw_regex(keywords: list[str]) -> str:
    """ASCII keywords get letter guards ("ios" must not match "bios"; CJK neighbours are fine)."""
    parts = []
    for k in keywords:
        e = re.sub(r"(?:\\ )+", r"\\s*", re.escape(k))
        if _ASCII_WORD.fullmatch(k):
            e = rf"{profile.A}{e}{profile.Z}"
        parts.append(e)
    return "|".join(parts)


def seed(con) -> None:
    if con.execute("SELECT COUNT(*) FROM roles").fetchone()[0]:
        return
    for i, (key, label, kws, w) in enumerate(DEFAULT_ROLES):
        con.execute("INSERT INTO roles(key,label,keywords,weight,position) VALUES(?,?,?,?,?)", (key, label, kws, w, i))
    con.commit()


def list_roles(con) -> list[dict]:
    seed(con)
    return [dict(r) for r in con.execute("SELECT * FROM roles ORDER BY position, id")]


def validate(label: str, keywords: str, weight) -> tuple[str, str, int]:
    label = (label or "").strip()
    if not label or len(label) > 24:
        raise ValueError("岗位名称不能为空，且不超过 24 个字符")
    kws = split_keywords(keywords)
    if not kws:
        raise ValueError("至少填写一个关键词（用逗号分隔）")
    if any(len(k) < 2 for k in kws):
        raise ValueError("关键词至少 2 个字符，单个字符会匹配到太多无关内容")
    try:
        w = int(weight)
    except (TypeError, ValueError):
        raise ValueError("重要度必须是 1–10 的整数")
    if not 1 <= w <= 10:
        raise ValueError("重要度必须是 1–10 的整数")
    return label, ", ".join(kws), w


def search_terms(con, limit: int = 6) -> list[str]:
    """Keywords for sources that need a search query (LinkedIn, Adzuna, USAJOBS): the first two ASCII keywords of
    each enabled role, heaviest role first. Short/CJK keywords are useless as an outside search."""
    from jobboard import profile_config
    cfg = profile_config.current()
    rows = ([{**r, "keywords": ", ".join(r["keywords"]), "enabled": 1} for r in cfg["roles"]] if cfg
            else [r for r in list_roles(con) if r["enabled"]])
    if cfg:
        limit = max(limit, cfg.get("search_terms", 6))
    out: list[str] = []
    for r in sorted(rows, key=lambda r: -r["weight"]):
        taken = 0
        for k in split_keywords(r["keywords"]):
            if _ASCII_WORD.fullmatch(k) and len(k) >= 3 and k.lower() not in {x.lower() for x in out}:
                out.append(k)
                taken += 1
                if taken == 2:
                    break
    return out[:limit]


def activate(con) -> list:
    """Load enabled roles into the parser. Call once at start-up and after every change."""
    rules = [(r["key"], kw_regex(split_keywords(r["keywords"])), r["weight"])
             for r in list_roles(con) if r["enabled"] and split_keywords(r["keywords"])]
    profile.set_rules(rules)
    return rules
