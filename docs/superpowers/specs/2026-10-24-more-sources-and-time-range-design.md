# More job sources + time range (design)

Approved in chat 2026-10-24 (user asked for fully automatic implementation afterwards).

## Intent
Personal job search only; data stays local; never republished. Add more sources to the existing
`adapters -> run.to_job -> db.upsert -> score -> board` pipeline and a time-range filter.

## Sources
| Batch | Sources | Notes |
|---|---|---|
| 1 | Greenhouse, Lever, Ashby, Recruitee, Personio (per-company public JSON/XML), USAJOBS | company list = `companies` table; USAJOBS needs `USAJOBS_KEY` + `USAJOBS_EMAIL` env |
| 2 | Adzuna (`ADZUNA_APP_ID`/`ADZUNA_APP_KEY`), The Muse, Arbeitnow | missing key -> source reported as skipped, not an error |
| 3 | JobPosting JSON-LD from career pages | pages come from `companies` rows with ats=`web` |
| LinkedIn | unauthenticated `jobs-guest` endpoints, no login | keywords = enabled roles, locations from setting `linkedin_locations` (default `Remote`), 1-3 pages, throttled, 429 backoff |

## Company registry
`companies(ats, slug, name, active, fails, last_ok)`. ATS adapters walk active rows with a small thread pool and a
global request pace. Rows failing 5 times in a row are deactivated. `jobboard discover` adds rows; a candidate is
only stored if the endpoint answers with a valid board. Discovery channels: ATS links already in stored jobs,
a user file (`--file`), built-in seed, Common Crawl index (best effort, service is flaky).
Greenhouse: list without content, title prefilter with the active roles, detail request only for matches.

## Time range
`/api/jobs?since=1d|3d|7d|30d` filters on `posted_at` (SQL `julianday`). Board gets four buttons (default: all).
`posted_at` becomes the earliest source date when sources are merged. Where a site gives only an update time
(Greenhouse without `first_published`) the first-seen time is used instead.

## Out of scope
Logged-in LinkedIn scraping, Chinese platforms, sharing data.

## Testing
Saved sample responses, no network: parsers, registry, discovery validation, throttling/backoff, since filter,
merge-earliest-date. Live smoke tests are run by hand once and reported.
