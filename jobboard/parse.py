import hashlib
import re

from jobboard import profile

_EMOJI_TAG = re.compile(r"[#＃]\S+|[^\w\s一-鿿@:：/.\-+|·（）()\[\]【】,，$k万U]", re.I)
_HANDLE = re.compile(r"(?<![\w/])@[A-Za-z][A-Za-z0-9_]{4,31}\b")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_URL = re.compile(r"https?://[^\s)>\]】]+|(?:t\.me|boneo\.io)/[^\s)>\]】]+", re.I)
_SALARY = re.compile(
    r"(?:薪[资酬][^\n：:]{0,4}[：:]?\s*|工资[：:]?\s*|💰\s*)?"
    r"((?:USD|\$|U|¥)?\s?\d[\d.,]*\s?[kK千wW万]?\s?[-~–至到]\s?\d[\d.,]*\s?[kKUu千wW万]?(?:\s?USDT|\s?[Uu]|\s?/\s?(?:月|年|month|year))?"
    r"|\d+\s?[kK]\s?起|面议)")


_GENERIC = re.compile(r"^(?:#?(?:免费|付费)广告|#?招聘|🏡|公司直招)", re.I)
_ROLE_VALUE = re.compile(r"^\W*(?:#?招聘(?:岗位)?|待招岗位|诚招|急招|岗位|职位|role)\s*[：:]?\s*(.+)$", re.I)


def clean_title(text: str) -> str:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if lines and _GENERIC.search(lines[0]) or (lines and len(re.sub(r"\W", "", lines[0])) < 3):
        for ln in lines[1:8]:
            m = _ROLE_VALUE.match(ln)
            if m and len(m.group(1)) > 2:
                role = re.sub(r"\s*[#＃]\S+", lambda x: " " + x.group(0)[1:], m.group(1))
                role = re.sub(r"[^\w\s\u4e00-\u9fff\-+/·()（）]", " ", role)
                company = re.match(r"^\W*🏡?\s*([^\s#]+)", lines[0]) if "🏡" in lines[0] else None
                prefix = f"{company.group(1)} · " if company else ""
                return (prefix + " ".join(role.split()))[:70]
    return _first_line_title(text)


def _first_line_title(text: str) -> str:
    for line in text.splitlines():
        line = re.sub(r"^[^\w一-鿿]+", "", line).strip()
        line = re.sub(r"^(?:招聘岗位?|招聘|岗位|职位)\s*[：:｜|]?\s*", "", line)
        line = _EMOJI_TAG.sub("", line).strip(" -:：|｜")
        if len(line) >= 3:
            return line[:70]
    return text.strip()[:70]


def contacts(text: str) -> dict:
    emails = sorted(set(_EMAIL.findall(text)))
    handles = [h for h in dict.fromkeys(_HANDLE.findall(text))
               if h.lower() not in profile.IGNORE_CONTACTS
               and not any(h[1:] in e for e in emails)]
    urls = [u.rstrip(".,") for u in dict.fromkeys(_URL.findall(text))]
    return {"telegram": handles, "email": emails, "links": urls[:5]}


def salary(text: str) -> str | None:
    m = _SALARY.search(text)
    return m.group(1).strip() if m else None


_SEEKER = re.compile(
    r"#求职|求职意向|工作经历|期望(?:薪资|岗位)|(?<![A-Za-z])resume(?![A-Za-z])|个人优势|"
    r"(?<![A-Za-z])I(?:'m| am) (?:an? )?(?:[\w-]+ ){0,3}(?:developer|engineer|designer|programmer)(?![A-Za-z])|"
    r"(?<![A-Za-z])I(?:'m| am)?\b[^.\n]{0,40}(?:looking for|seeking|open to) (?:a |new )?(?:work|job|role|position|gig|opportunit)|"
    r"available for (?:hire|work|freelance)|hire me", re.I)
# A line that *names* the role ("✅招聘：ios im"), not "岗位要求/岗位职责" section headers or their bullets.
_ROLE_LINE = re.compile(r"^\W*(?:#?招聘|待招|诚招|急招|岗位(?!要求|职责|描述)|职位(?!要求|描述)|role\b|position\b|💼)", re.I)


def is_seeker(text: str) -> bool:
    """A person offering themselves (DeJob 求职 posts, HN 'I am a developer…'), not an opening."""
    head = "\n".join(text.splitlines()[:6])  # a real posting may say "we're not looking for…" later on
    return bool(_SEEKER.search(head)) and "#招聘" not in text


def _head(text: str) -> str:
    """Title area: the first lines plus any line that names the role."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    keep = lines[:1] + [ln for ln in lines[1:] if _ROLE_LINE.search(ln) and len(ln) < 120]
    return "\n".join(keep)


def role_match(text: str, hint: str = "", structured: bool = False) -> tuple[int, list[str]]:
    """Telegram posts: role words in the title/role lines count fully, body-only mentions half.
    Job sites (structured=True): only the title line counts fully; site tags/body are half weight,
    except that a flutter/ios/android tag may rescue a generic "Software Engineer" title."""
    if is_seeker(text):
        return 0, []
    head = text.splitlines()[0] if structured and text.strip() else _head(text)
    if not structured:
        head += "\n" + hint
    rules = profile.rules()
    full = [(w, t) for t, rx, w in rules if re.search(rx, head, re.I)]
    rest = [(w // 2, t) for t, rx, w in rules
            if t not in {x for _, x in full} and re.search(rx, text + "\n" + hint, re.I)]
    if structured and not full and re.search(profile.GENERIC_DEV_TITLE, head, re.I) \
            and not re.search(profile.NEG_TITLE, head, re.I):
        rescue = [(w, t) for t, rx, w in rules if t in profile.SPECIFIC_TAGS and re.search(rx, hint, re.I)]
        if rescue:
            full = [max(rescue)]
    hits = sorted(full + rest, reverse=True)
    if not hits:
        return 0, []
    if not full:  # mentioned only in body/tags: never reaches the recommended threshold
        return min(hits[0][0], 4), list(dict.fromkeys(t for _, t in hits))
    score = hits[0][0] + min(3, len(hits) - 1)
    if structured and re.search(profile.STACK_MISMATCH, head, re.I) \
            and not ({t for _, t in full} & profile.STACK_SAFE_ROLES) \
            and not re.search(profile.STACK_SAFE_TITLE, head, re.I):
        return min(score, 4), list(dict.fromkeys(t for _, t in hits))  # e.g. ".NET Full-stack": not your stack
    if re.search(profile.REMOTE_RE, text, re.I):
        score += 2
    return score, list(dict.fromkeys(t for _, t in hits))


_VISA_NO = re.compile(
    r"(?:no|not|unable to|cannot|can't|can ?not|without|don'?t|do not|won'?t|will not|unfortunately)\s+(?:\w+\s+){0,4}?(?:visa\s+)?sponsor"
    r"|sponsorship\s+(?:is\s+)?not\s+(?:available|offered|provided)|not\s+(?:able|eligible)\s+to\s+sponsor"
    r"|(?:must|need to)\s+(?:already\s+)?(?:have|hold|possess)[^.\n]{0,50}(?:right|authori[sz]ation|permit|eligib)[^.\n]{0,20}work"
    r"|work authori[sz]ation\s+(?:is\s+)?required|不提供(?:签证|工作签证|担保)|不(?:办理|包办)签证|无法提供签证|不支持.{0,6}签证", re.I)
_VISA_YES = re.compile(
    r"visa[\s-]*sponsor|sponsor(?:s|ing|ship)?\b[^.\n]{0,40}\b(?:visa|work permit)|visa (?:support|assistance|provided|included)"
    r"|(?:work (?:visa|permit)|visa)[^.\n]{0,30}(?:provided|sponsor|assist|support|arranged|handled)"
    r"|we (?:will )?(?:can )?sponsor|sponsorship (?:available|offered|provided|possible)|blue card|skilled worker visa"
    r"|\blmia\b|h-?1b (?:sponsor|transfer|support)|visumsponsoring|visum(?:s)?unterst|aufenthaltstitel[^.\n]{0,30}(?:unterst|übernehm)"
    r"|签证(?:担保|支持|办理|协助)|办理(?:工作)?签证|提供(?:工作)?签证|包办签证|协助办理签证|担保签证|visa\s*(?:包|办理|支持)", re.I)
_NEGATED_BEFORE = re.compile(r"(?:\bno\b|\bnot\b|n't\b|\bwithout\b|\bnever\b|\bunable\b|\bcannot\b|\bnor\b)\s*(?:\w+\s+){0,6}$", re.I)      # a comma or full stop ends the reach of the negation
_RELOCATION = re.compile(r"relocation|relocate|umzug|搬迁|安家费|落地支持", re.I)


def visa_flag(text: str) -> str:
    """'yes' = the posting says the employer sponsors / arranges a visa; 'maybe' = only relocation support; '' = nothing.
    An explicit refusal ("no visa sponsorship", "must have the right to work") always wins."""
    if not text:
        return ""
    if _VISA_NO.search(text):
        return ""
    for m in _VISA_YES.finditer(text):
        if not _NEGATED_BEFORE.search(text[max(0, m.start() - 48):m.start()]):   # "We do not provide visa assistance" is a no
            return "yes"
    for m in _RELOCATION.finditer(text):
        if not _NEGATED_BEFORE.search(text[max(0, m.start() - 48):m.start()]):
            return "maybe"
    return ""


def risks(text: str) -> list[str]:
    return [name for name, rx in profile.RISK_RULES if re.search(rx, text, re.I)]


def fingerprint(text: str) -> str:
    """Same post copied across channels (different boilerplate) collapses to one key."""
    body = re.sub(r"[\W_]+", "", text.lower())[:300]
    return hashlib.sha1(body.encode()).hexdigest()


_WORLD = re.compile(r"worldwide|anywhere|global|international|apac|asia|china|hong\s*kong|any\s*location|all\s*countries|world", re.I)
_RESTRICT = re.compile(
    r"\b(?:us|usa|u\.s\.|united states|uk|u\.k\.|united kingdom|canada|europe|eu|emea|germany|france|spain|portugal|"
    r"poland|netherlands|ireland|australia|latam|latin america|brazil|mexico|india|north america|americas)\b", re.I)


def geo_flag(location: str | None) -> str:
    """Can someone in China/HK plausibly apply? 'worldwide' | 'restricted' | 'unknown'."""
    loc = (location or "").strip()
    if not loc:
        return "unknown"
    if _WORLD.search(loc):
        return "worldwide"
    return "restricted" if _RESTRICT.search(loc) else "unknown"


_REGION = (r"(?:us|usa|u\.s\.a?\.?|united states|america|uk|u\.k\.|united kingdom|canada|europe|european union|eu|emea|germany|"
           r"india|brazil|latam|latin america|north america|australia)")
_RESTRICT_TEXT = re.compile(
    rf"\b{_REGION}[ \-]+(?:only|based|residents?|citizens?|time ?zones?)\b"
    rf"|\b(?:only|exclusively)\s+(?:for\s+|to\s+)?(?:candidates\s+|applicants\s+)?(?:in|from|based in|located in)\s+(?:the\s+)?{_REGION}\b"
    rf"|\b(?:must|need to|have to|should)\s+(?:currently\s+)?(?:be\s+)?(?:located|based|reside|resident|living|live)\s+(?:in|within)\s+(?:the\s+)?{_REGION}\b"
    rf"|\b(?:authori[sz]ed|eligible|legally entitled|right)\s+to\s+work\s+in\s+(?:the\s+)?{_REGION}\b"
    r"|仅限(?:美国|欧洲|欧美|国内|大陆|境内|北美)|限(?:美国|欧美|国内|大陆)|只招(?:美国|欧洲|国内)|需(?:在|位于)(?:美国|欧洲|国内)", re.I)


def geo_from_text(text: str | None) -> str:
    """For postings without a location field (Telegram, HN, ATS free text): 'restricted' when the text itself says
    "US only", "must be located in the UK", "authorized to work in the US" ...; otherwise 'unknown'."""
    return "restricted" if text and _RESTRICT_TEXT.search(text) else "unknown"


def analyze(text: str, hint: str = "", structured: bool = False) -> dict:
    score, tags = role_match(text, hint, structured)
    return {
        "title": clean_title(text), "salary": salary(text), "contacts": contacts(text),
        "score": score, "tags": tags, "risks": risks(text),
        "remote": bool(re.search(profile.REMOTE_RE, text, re.I)), "fingerprint": fingerprint(text),
        "geo": "unknown", "company": None, "location": None, "hint": hint, "visa": visa_flag(text),
    }
