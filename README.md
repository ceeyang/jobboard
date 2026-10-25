# jobboard

**Public site: https://ceeyang.com/jobboard/** (same as ceeyang.github.io/jobboard/, which redirects there) - rebuilt every hour by GitHub Actions (see "Public site" below).
The local edition (this README) additionally reads your private Telegram groups, LinkedIn and key-based APIs.

Reads your Telegram job groups/channels (via `cli-anything-telegram`'s Telethon session), finds postings that
match Flutter / iOS / Android / web, dedupes them across channels, and shows them in a local web board.

```bash
uv sync
uv run jobboard fetch            # Telegram channels + job sites (add --no-web / --no-telegram / --only RemoteOK / --force)
uv run jobboard serve            # board at http://127.0.0.1:8765
uv run jobboard digest --dry-run # preview the daily summary (drop --dry-run to send to Saved Messages)
uv run jobboard reanalyze        # re-score stored jobs after editing jobboard/profile.py
scripts/install_launchd.sh       # hourly fetch + 09:00 digest + board always on
```
Edit `jobboard/profile.py` to change roles, remote keywords, risk rules. Data: `~/.local/share/jobboard/jobs.db`.
Only `digest` writes to Telegram (one message to your own Saved Messages); nothing is ever sent to other people.

## Job sites (overseas, remote-friendly)
RemoteOK, Remotive, We Work Remotely, Himalayas, Jobicy, Working Nomads and Hacker News "Who is hiring?" are read through
their public APIs/RSS (`jobboard/sources/adapters.py`; add a site by writing one function and listing it in `SOURCES`).
- Per-site minimum interval (Remotive and HN: 6h - Remotive's terms allow ~4 requests/day); one failing site never stops the rest.
- Every job keeps its source name and original link, as the sites' terms require. Don't republish the data.
- Site tags are often keyword-stuffed, so only the job **title** counts fully (`profile.py`: SPECIFIC_TAGS / NEG_TITLE / STACK_MISMATCH).
- `geo` marks postings as 🌍 worldwide / 🚧 region-restricted (e.g. "USA only") / unknown; the board can hide restricted ones.
Not included on purpose: Indeed, BOSS/拉勾 and other login-walled or anti-bot sites.

## More sources (batch 1-3 + LinkedIn)
```bash
uv run jobboard discover                  # find company boards: links in stored jobs + built-in seed + Common Crawl
uv run jobboard discover --file my.txt    # your own list: ATS links, `lever:slug` lines, or career-page URLs
uv run jobboard fetch --only Greenhouse,LinkedIn --force
```
| Source | How | Needs |
|---|---|---|
| Greenhouse, Lever, Ashby, Recruitee, Personio | each employer's public board API, only titles that match your roles are kept | companies in the `companies` table (`discover` fills it; a board is stored only after it answered) |
| Career pages | schema.org `JobPosting` JSON-LD (single job pages, or a listing page whose job links are followed) | page URLs added with `discover --file` |
| Adzuna | search API, queries = your roles' English keywords | `ADZUNA_APP_ID`, `ADZUNA_APP_KEY` (free) |
| USAJOBS | federal jobs | `USAJOBS_KEY`, `USAJOBS_EMAIL` (free) |
| The Muse, Arbeitnow | public APIs | nothing |
| LinkedIn | logged-out `jobs-guest` pages, **no account, no cookies** | nothing; locations = setting `linkedin_locations` (default `["Remote"]`, edit via `PUT /api/settings`) |

- A source without its key shows as `skipped` in `fetch` output; it never counts as an error.
- `fetch` runs a cheap discovery first (links found in stored jobs; the seed list when the registry is empty).
  A board that returns 404 five fetches in a row is switched off; being offline never does that.
- **LinkedIn is for personal, low-volume use only.** It is an undocumented interface that can change or throttle at any
  time, and automated access is against LinkedIn's terms. It is kept gentle (2 pages per query, several seconds between
  requests, at most 40 description downloads per run, stops at the first HTTP 429, 6 h minimum interval). Don't publish
  or share what it collects. Using your logged-in account for this is deliberately not supported: the realistic risk is
  getting that account restricted.
- Public does not mean redistributable: every job keeps its source name and original link; the data stays on your machine.
- Re-scoring after editing roles (`reanalyze`) works for all new sources the same way.

## Government job services and visa sponsorship
| Source | Coverage | Notes |
|---|---|---|
| Germany (BA) | Bundesagentur für Arbeit "Jobsuche" | the API behind arbeitsagentur.de (public key header), last 28 days |
| Sweden (AF) | Arbetsförmedlingen / Platsbanken (JobTech open data) | no key |
| EURES (EU) | EU Commission job vacancy service, ~30 countries | the API behind the EURES portal; title search, last month |
| Canada Job Bank | jobbank.gc.ca search pages | obeys robots.txt (5 s delay); jobs whose employer requested an **LMIA** (= may hire a foreign worker) are flagged |

Australia is **not** covered: Workforce Australia and APS Jobs publish no API or open feed (only third-party scrapers exist).
France Travail needs a free OAuth client and is not wired up yet; UK "Find a job" has no public API.

**Visa sponsorship** is a flag on every job (`visa`: `yes` / `maybe` / empty) and a switch in the sidebar
("只看雇主担保签证"): `yes` when the posting says the employer sponsors or arranges a visa (English, German, Chinese phrases)
or the source says so officially (Canada LMIA); `maybe` = only relocation support. An explicit refusal ("no visa
sponsorship", "must have the right to work") always wins. It is a text heuristic - always confirm with the employer.

## GitHub group (jobs kept in public repositories)
Sidebar group **🐙 GitHub**, public edition included (excerpt + link to the issue, no contacts).
| Source | Shape | Notes |
|---|---|---|
| Rebase who-is-hiring (`rebase-network/who-is-hiring`) | every job is an issue; read from the community's normalised JSON `hire.rebase.network/jobs.normalized.json` (~900 open, Chinese, blockchain and others, many remote) | the JSON also lists contact channels: they are never read |
| `frontendbr/vagas`, `react-brasil/vagas`, `backend-br/vagas`, `androiddevbr/vagas`, `flutterbr/vagas` | one open issue = one job (Brazil, Portuguese; many remote roles paid in USD) | list in `config/github_job_repos.txt` (add `owner/repo`, max 9) |
| SimplifyJobs New-Grad / Summer2027 Internships | structured `listings.json` (13 MB, fetched gzipped) | early-career roles; its `sponsorship` field feeds the visa filter ("Offers Sponsorship" -> yes) |
Issues are read through the REST API; `GITHUB_TOKEN` (set automatically in the workflow) lifts the limit from 60 to 5000 requests/hour.
Repositories that are only *directories* (remoteintech/remote-jobs, tech-jobs-with-relocation, awesome-job-boards, open-source-jobs)
list companies or boards, not postings, so they are not sources; their company links are good input for `jobboard discover --file`.

## Global remote filter
The sidebar switch **🌍 只看全球远程** keeps only jobs that are remote, not restricted to a region and not tied to a city.
"Restricted" is read from the location field *and* from the text ("US only", "must be located in the UK", "authorized to
work in the US", "仅限美国" ...). Jobs with no location at all (most Telegram/HN posts) count as global unless their text
says otherwise. Run `jobboard reanalyze` once to apply the text rule to jobs stored earlier (the board and the public export
also apply it on the fly).

## Public site (GitHub Pages)
`.github/workflows/update.yml` runs every hour (best effort: GitHub may delay scheduled runs) and on demand:
`fetch --mode public` -> `export` -> Pages. State lives on the orphan `data` branch (one commit, replaced each run).
- **Reduced edition on purpose.** Only whitelisted sources (`jobboard/mode.py`): remote job boards, HN, employers' own ATS boards,
  Sweden, EURES, and the public Telegram channels listed in `config/telegram_channels.txt`. Never LinkedIn, Adzuna/USAJOBS,
  Germany, Canada, The Muse, Arbeitnow, and never anything read with your personal Telegram login.
- **Telegram on the public site** is read through each channel's public web preview (`https://t.me/s/<name>`, no login), so only
  channels with a preview work; private groups and invite-only groups are skipped automatically and reported in the run
  summary. Edit the list in `config/telegram_channels.txt` (it is public, together with the repository).
- **Excerpt + link only.** Per job: title, company, location, salary, a <=600 character excerpt and the link to the original.
  No contact handles/e-mails (a final check fails the build if one slips through), no full texts, no notes.
- **Read-only.** Statuses (interested/applied/...) and saved searches live in *your browser* only. Roles, refresh and digest
  are edited in the local edition.
- Change the whitelist or window in `jobboard/mode.py` / `publish.py` (`WINDOW_DAYS = 45`), then push.
- Pages must stay public on a free account; what is published is public to everybody, so keep the whitelist conservative.
- If GitHub pauses the schedule after 60 days without repository activity, re-enable it on the Actions page.

## How the posted time is decided
- Every date is stored and exported in **UTC** (`+00:00`); sorting and the 1d/3d/1w/1m filter compare real instants, not text.
- A job's date is the **earliest** date any of its sources reported (first appearance). If a source showed the same job again
  a day or more later (a Telegram channel re-posting an ad), the card adds "重发 X 前" and the detail panel shows both times.
- Sources that only give a **day** (SimplifyJobs, LinkedIn, Canada, Germany) are shown as "10/23 发布" instead of an invented hour,
  and count as "some time that day" for the filter and the ordering.

## Time range
The board has a **发布时间** filter: any / 1 day / 3 days / 1 week / 1 month. The API takes `?since=1d|3d|7d|30d`.
A job's date is the earliest date any of its sources reported, so a posting re-listed elsewhere does not look new.
Dates come from the source (Greenhouse `first_published`, Lever `createdAt`, Ashby `publishedAt`, ...); LinkedIn only
gives a day, so its jobs count from 00:00 UTC of that day.

## Settings (⚙ in the board)
- **Auto refresh**: off / 15 min … 24 h / custom (5–1440 min). Runs inside `jobboard serve`, so the board has to be running. Remotive and HN keep their own 6 h minimum.
- **Daily digest** (opt-in): sends the top matches to your Telegram "Saved Messages" at the time you choose, once per day.
- **Common roles**: name + comma-separated keywords + weight (1–10). Add from templates (React Native, 鸿蒙, 小程序, …), edit, disable or delete; existing jobs are re-scored immediately.
- **Search syntax**: space = AND, `a|b` = OR, `-term` = exclude, `"exact phrase"`. Save a search as a chip in the sidebar.
- Layout: sidebar ≈14vw (collapse with `b`), list : detail = 5 : 6 of the remaining width.
