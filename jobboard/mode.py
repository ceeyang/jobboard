"""Which sources may feed the PUBLIC site (GitHub Pages). The local board uses everything; the public one is a
reduced edition: only sources whose terms allow showing title + short excerpt + link back, and nothing that needs a
personal login or key.
Left out of the public site on purpose:
  LinkedIn            automated access is against its terms -> personal use only
  Adzuna, USAJOBS     need personal API keys whose terms usually forbid redistribution
  The Muse, Arbeitnow terms not checked for re-publication (kept local)
  Germany (BA), Canada Job Bank  no explicit open-data licence found (kept local)
  Telegram            private groups / logged-in session never leave the machine"""
PUBLIC_SOURCES = {
    "RemoteOK", "Remotive", "We Work Remotely", "Himalayas", "Jobicy", "Working Nomads", "HN Who's Hiring",
    "Greenhouse", "Lever", "Ashby", "Recruitee", "Personio", "Career pages",     # employers' own public boards
    "Sweden (AF)",                                                              # JobTech open data
    "EURES (EU)",                                                               # EU Commission service
}


GOV_SOURCES = {"Germany (BA)", "Sweden (AF)", "EURES (EU)", "Canada Job Bank", "USAJOBS"}   # shown as their own group


def kind(chat: str, chat_id: int) -> str:
    """'gov' = government job service, 'site' = other job site (tiny fake ids -1..-99), else 'telegram'."""
    if chat in GOV_SOURCES:
        return "gov"
    return "site" if -100 < chat_id < 0 else "telegram"


def allowed(source_name: str, mode: str | None) -> bool:
    return mode != "public" or source_name in PUBLIC_SOURCES
