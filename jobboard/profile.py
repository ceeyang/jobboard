"""What counts as a match for you. Edit freely - it is plain data."""

# (tag, regex, weight). Highest-weight tags decide the score.
# \b treats CJK as word chars, so "iOS开发" would not match \bios\b. Use ASCII-letter guards.
A, Z = r"(?<![A-Za-z])", r"(?![A-Za-z])"
ROLE_RULES = [
    ("flutter",   rf"flutter|{A}dart{Z}",                                       10),
    ("ios",       rf"{A}ios{Z}|{A}swift(?!ly)|objective-?c|iphone",                 7),
    ("android",   r"android|kotlin",                                          7),
    ("mobile",    r"移动端|客户端|app\s*开发|跨端|跨平台|react\s*native|mobile", 6),
    ("frontend",  rf"前端|front-?end|{A}react{Z}|{A}vue{Z}|typescript|{A}h5{Z}|小程序",   5),
    ("fullstack", r"全栈|full-?stack",                                         4),
]
REMOTE_RE = r"远程|remote|居家|wfh|在家"
# Roles that look related but aren't: only subtract when no positive tag fired.
IGNORE_CONTACTS = {"@boneo", "@boniush365", "@boniuom", "@boniuzp", "@telegram"}

RISK_RULES = [
    ("不收个人信息/无需背景核查", r"不用提交个人信息|无需.{0,6}个人信息|免.{0,4}背景"),
    ("博彩/灰产行业", rf"博彩|体育直播|德州|包网|盘口|彩票|代收代付|支付通道|赌|赛事|娱乐城|{A}igaming{Z}|casino"),
    ("要求先付费", r"押金|保证金|培训费|先付|入职费"),
    ("限制在国内", r"禁止人在国内"),
]

# --- Job sites (structured postings): titles are reliable, tags are often stuffed -------------
SPECIFIC_TAGS = {"flutter", "ios", "android"}          # a site tag may only rescue a generic title with these
GENERIC_DEV_TITLE = r"engineer|developer|programmer|engineering|\bswe\b|software"
NEG_TITLE = (r"back-?end|data (?:engineer|scientist|analyst)|devops|\bsre\b|site reliability|machine learning|\bml\b|"
             r"product (?:manager|owner|lead)|designer|account (?:executive|manager)|sales|marketing|recruit|support|"
             r"accountant|finance|\bqa\b|tester|testing|security|customer|operations|content|writer|legal|analyst")
STACK_MISMATCH = r"\.net|\bc#|\bjava\b|\bphp\b|python|ruby|golang|\bgo\b|\brust\b|elixir|solidity|scala|\bc\+\+"


# A title that names another stack is capped (STACK_MISMATCH) unless it also names one of "your" roles / words.
STACK_SAFE_ROLES = {"flutter", "ios", "android", "mobile"}
STACK_SAFE_TITLE = r"react|vue|typescript|node|front-?end"


# Runtime override: the web app / CLI load the user's roles from the database (see roles.py).
_active: list | None = None


def set_rules(rules: list | None) -> None:
    global _active
    _active = rules


def rules() -> list:
    return _active if _active is not None else ROLE_RULES
