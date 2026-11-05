# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

FreshJobs is a personal job feed for an international student looking for work in the US. It polls every few minutes, so new postings show up long before LinkedIn picks them up. Its sources:
- Workable's own job search (`sources/workable_search.py`, every Workable employer's US jobs newest first, so the per-board Workable adapter only runs every 3 h).
- companies' own ATS boards: Greenhouse, Lever, Ashby, Workday, SmartRecruiters, Workable, Oracle HCM, iCIMS, Eightfold, SuccessFactors, Rippling, BambooHR, Recruitee, Breezy and Pinpoint.
- big-tech career sites: Amazon, Microsoft, Google and Apple.
- job APIs: The Muse, Remotive, RemoteOK and Adzuna, plus the SimplifyJobs GitHub lists (new grad, internships).
- startups: every hiring Y Combinator company's "Work at a Startup" page (synced daily from yc-oss, `pipeline/startups.py`; postings carry a visa policy), and Hacker News "Who is hiring?" threads.
- Google for Jobs (via JSearch), for postings that exist only on LinkedIn, Indeed or Glassdoor.
- companies' own careers pages with no ATS behind them (`sources/website.py`: JSON-LD JobPosting, then per-job links, then inline role sections).
- The **LinkedIn & more** tab (`Elsewhere.tsx`) only builds pre-filtered search links (LinkedIn jobs past hour or Easy Apply, LinkedIn hiring posts, Indeed, Glassdoor, Google Jobs, X), because LinkedIn can't be collected.
- **Search and filters:** any role can be searched, with related-role expansion, filters for location (country, state, city) and experience, and sorting by newest found or newest posted.
- **Hidden by default:** reposts, cross-source duplicates, old jobs re-listed as new ("resurfaced"), expired jobs, and jobs that rule out visa sponsorship.
- **Other features:** an application tracker, a recruiter finder (search links plus Hunter.io lookup), and email alerts.
- **H-1B sponsor data:** every job shows its employer's certified H-1B filings, imported from the DOL's LCA disclosure file (`app/h1b.py`).
- **Not built yet:** a Claude chatbot with tools over the jobs DB, and a cloud deploy.

Scraping LinkedIn, Indeed, Glassdoor or Meta's careers site is deliberately out of scope (it's against their terms, and Meta's robots.txt forbids it); their postings come in through the Google Jobs API instead. Never bypass bot protection: dol.gov blocks scripted downloads, so the user downloads the LCA file in a browser.

## Commands

Windows machine. The default `python` is 3.8, so always use the backend venv (built with `py -3.12`).

Backend (run from `backend/`):
```bash
.venv/Scripts/python.exe -m uvicorn app.main:app --port 8000      # API + background pollers
.venv/Scripts/python.exe -m app.pipeline.poller --once            # one poll of every source, no web server
.venv/Scripts/python.exe -m app.pipeline.discovery --file data/discover_seed.txt   # find boards for company names
.venv/Scripts/python.exe -m app.h1b import data/h1b/<LCA_Disclosure_Data_*.xlsx>   # ~7 min; then counts refresh
.venv/Scripts/python.exe -m app.h1b discover --top 3000          # find boards for the biggest sponsors
.venv/Scripts/python.exe -m app.pipeline.startups                # add hiring YC startups now (also runs daily)
.venv/Scripts/python.exe -m app.pipeline.bulk_discovery hn|websites|vc|commoncrawl   # mass board discovery (see module doc)
.venv/Scripts/python.exe -m app.pipeline.bulk_discovery fix-workday   # repair Workday sources named after a file (robots.txt)
.venv/Scripts/python.exe -m pytest                                # all tests
.venv/Scripts/python.exe -m pytest tests/test_api.py::test_tracker_flow   # single test
```

Frontend (run from `frontend/`; Vite proxies `/api` to `API_URL`, default `http://localhost:8000`):
```bash
npm run dev      # http://localhost:5173
npm run build    # tsc -b + vite build (this is the type check)
npx eslint src
```

`start-freshjobs.bat` (repo root) starts whichever of the two isn't running, each in its own window, and opens the feed; the backend window (`scripts/run-backend.ps1`) keeps the PC awake while open. `.claude/launch.json` defines `backend` and `frontend` preview servers. `docker compose up --build` runs both.

## Architecture

Data flow: `sources/` adapters → `pipeline/poller.py` → SQLite → FastAPI (`app/main.py`) → React.

- **Adapters** (`app/sources/`) implement `Adapter.fetch(client, slug, *, full, known_ids) -> list[NormalizedJob]` and are registered in `ADAPTERS` in `app/sources/__init__.py`. Class attributes control how the poller treats each one:
  - `complete_feed`: False for aggregators that only list recent jobs (Simplify is an aggregator with a complete feed).
  - `aggregator`: True for multi-employer sources. Their employer names feed discovery, and their job links feed harvesting.
  - `min_interval_minutes`: provider rate limits.
  - `full_scan_every`: for sources that can do a cheap partial scan of the newest jobs.
  - `lane`: `"slow"` for one-request-per-job sources (Workday, SmartRecruiters).
  - `last_fetch_complete(slug)`: Workday returns False when a board hits its 2000-result cap.
  - `is_configured()`: Adzuna returns False without keys.
  - Aggregators set `NormalizedJob.company_name`. Adapters fetch per-job detail only for IDs not in `known_ids`.
  - A `companies` row is a *source*: a single employer's board, or an aggregator query (its `slug` holds the query string; see `data/companies.yaml`).
- **Two polling lanes** (`app/main.py`): APScheduler runs `run_poll("fast")` and `run_poll("slow")` in threads, each with its own lock, so a long Workday scan never delays the fast boards. Only the fast lane, and startup, sync `data/companies.yaml` into the DB (add-only), which avoids double inserts. `POST /api/poll` ("Check now") runs the fast lane. Each lane uses one fetch semaphore per ATS and processes each source as soon as its fetch finishes (`asyncio.as_completed`), committing per source.
- **`process_company()`** (`pipeline/poller.py`) is the core reconcile step, and what the tests exercise directly.
  - **Baseline:** on a source's first poll (`poll_count == 0`), every job is marked `baseline=True, alerted=True`. Those jobs never alert, and their `first_seen_at` falls back to `posted_at`.
  - **Freshness:** for later polls, `first_seen_at` is our detection time. The "newest found" sort and the "found within" filter use it. `posted_at` is the source's own date.
  - `listed_at` (`normalize.listed_at`) is the best estimate of when a job went live, used by "Date posted" and "Most recently posted". It's the source's exact time when there is one. For date-only sources (Workday, BambooHR), it's our detection time if we caught the job within 2 days of that date. A baseline job with no date gets NULL: unknown, sorted last, and never "posted just now" (some Workday tenants omit `postedOn` from their list). Old rows are filled by SQL in `backfill()`.
  - After a gap in checking (server off, or a rarely polled source), `process_company` sets `listed_earliest` to the source's previous successful check (`Company.last_ok_at`) when it's more than `listed_exact_gap_minutes` old. `normalize.listed_window` then makes `listed_at` that window's start and `listed_until` its end, and the UI shows "posted 20 min–6 h ago" instead of "just now".
  - **Enrichment:** `poll_once` leaves up to `enrich_per_poll` description-less open jobs per source out of `known_ids`, so the adapter re-fetches their details. `process_company` then fills in the description, date and location, and re-runs `apply_flags` (no_sponsor, experience). Adapters get `first_scan=` explicitly, so an empty `known_ids` isn't mistaken for a first scan.
  - **Expiry:**
    - Complete feeds: a job missing for `missed_polls_to_close` (2) full scans in a row is closed.
    - Aggregators: a job is closed after `stale_days` unseen.
    - Partial scans, capped scans and failed fetches close nothing.
    - **Liveness** (`pipeline/liveness.py`): each fast poll opens the apply pages of up to `verify_per_poll` open jobs that can't close on their own (aggregator jobs, and jobs whose source hasn't fetched successfully in 24 h), each at most once a day, and closes the ones that are gone (404/410, Greenhouse's `?error=true`, "no longer accepting applications"). Workday pages are asked through the CXS API. Opening a job in the UI checks it too (`POST /api/jobs/{id}/verify`). LinkedIn, Indeed and Glassdoor URLs are never fetched.
  - **Repeats** are flagged `is_repost`, hidden by default, and never alert. Matching is by normalized employer (`company_key`), not by source, so a company-board job and its Adzuna copy match (`pipeline/dedupe.py`). Each kind gets its own `JobEvent` kind:
    - `repost`: same fingerprint or near-identical description.
    - `duplicate`: the same job, still open, from another source.
    - `resurfaced`: newly listed, but `posted_at` is older than `resurfaced_days`.
- **Derived fields** are set by `apply_flags()` at insert:
  - `company_key` and `fingerprint`
  - `seniority` (from the title)
  - `min_years` and `exp_level` (`pipeline/normalize.py`, the higher of title level and years asked)
  - `role_match` (alert keywords from UserPrefs) and `us_eligible`
  - `no_sponsor` (`pipeline/sponsorship.py`)
  - `JobLocation` rows from `pipeline/locations.py`, a hand-built gazetteer that understands `US-CA-Menlo Park`, `Fort Worth TX`, `NYC, Chicago` and similar formats.
  - `backfill()` recomputes everything for rows where `exp_level IS NULL`, and runs at startup. To re-derive all jobs after changing a parser, set `exp_level = NULL` and restart.
- **Search** (`GET /api/jobs`): `q` is expanded through the role families in `pipeline/search.py`.
  - On SQLite, titles are matched through the FTS5 table `jobs_fts` (external content, kept in sync by triggers created in `db._ensure_title_search`, which rebuilds it if the triggers are missing). Elsewhere it falls back to ILIKE scans.
  - Results are ranked in tiers before the chosen sort: exact phrase, then every typed word in any order (or the company name), then related roles.
  - The list counts and sorts on IDs only, then loads the page's rows. Selecting full rows (descriptions make `jobs` gigabytes) made a search take 6–10 s.
  - The two main sorts walk `ix_jobs_by_found` / `ix_jobs_by_listed` (equality on closed_at, is_repost, no_sponsor, then the sort column) and stop after a page; without them every request sorted ~800k rows (2 min). The count stops at `COUNT_CAP` (10,000; `total_capped` shows "10,000+").
  - `posted_within_hours` filters on `listed_at` (set whenever the source gives a date), and `since_hours` on `first_seen_at`.
  - Location filters: a country-only filter uses an EXISTS per job (`ix_job_locations_job_place`); a state or city uses `Job.id IN (...)` (`ix_job_locations_place_job`), since those match few jobs. Remote jobs with no parsed place still match a country-only filter.
  - The picker lists (`GET /api/facets/locations`) come from an in-memory snapshot of open-job counts per (country, state, city) (`main._place_counts`, one grouped pass over the covering indexes, ~3.5 s), refreshed at startup and after each fast poll. A COUNT(DISTINCT) per request took 65 s and left the State and City pickers empty. Tests reset `main._facets` in the `session` fixture.
- **H-1B** (`app/h1b.py`):
  - `aggregate()` counts certified H-1B LCAs per employer (VISA_CLASS == "H-1B", CASE_STATUS starting with "Certified") into `h1b_sponsors`.
  - `SponsorIndex` matches a job's employer to DOL legal names by token prefix, after dropping legal words (`_DROP`). A one-word name matches only generic suffixes (`_GENERIC`), so "Amazon" sums "Amazon.com Services" and "Amazon Web Services" but not "Virtual Staffing" for "Virtual".
  - `apply_flags()` sets `Job.h1b_filings` from a process-wide cached index (reset by `import_file`). After changing the matching, run `refresh_job_counts()` and restart the backend.
- **Careers-URL detection** (`pipeline/detect.py`): `detect_board(url)` maps an ATS URL to (ats, slug), and `detect_from_page()` fetches a company's own careers page and finds the embedded board. It's exposed as `POST /api/companies/from-url` (Sources page). It can't see boards rendered client-side without links (e.g. stripe.com/jobs).
- **Harvesting** (`pipeline/harvest.py`): every poll, aggregator job URLs go through `detect_board`, and up to `harvest_per_poll` untracked boards are added as companies (the most-linked first). This is the main source of coverage growth, with no network cost. Big-company singletons (amazon/microsoft/google/apple) are skipped, and boards are compared case-insensitively.
- **Bulk discovery** (`pipeline/bulk_discovery.py`, CLI): Workday sites of the top H-1B sponsors found by name (the CXS API answers 422 for an unknown tenant, 404 for a real tenant with the wrong site name, so the host is found first and then common or name-derived site names are tried; results cached in `probe_cache` as `wd:<key>`), boards linked from 12 months of HN hiring threads, boards behind hiring YC startups' websites, a16z portfolio names (sitemap) probed by name, and Common Crawl's URL index (often down with 504s, so it retries). Getro-hosted VC boards ask scrapers to use their paid API, and Consider-hosted ones need a CSRF token, so both are deliberately skipped.
- **Polling cost:** boards whose last poll had 0 open jobs or failed are polled every `idle_poll_minutes` (180). Greenhouse fetches the plain list (12× smaller) and gets details only for new IDs. Only the first scan uses `content=true`.
- **Discovery** (`pipeline/discovery.py`): new employer names from aggregator jobs (up to `discovery_per_poll` per poll) are probed for Greenhouse, Lever and Ashby boards. Greenhouse matches are verified against the board's company name. Every probe is cached in `probe_cache`, and companies found exist only in the DB, not in the YAML.
- **Alerts:** `is_alert_worthy()` (role keywords, US, not a repost or baseline, plus the prefs for sponsorship and seniority). At most one email per poll per lane.
- **Recruiters** (`app/recruiters.py`): LinkedIn and Google search links, plus an on-demand Hunter.io domain search (HR department first), cached 30 days in `recruiter_cache`.
- **Tracker:** the `applications` table copies the job's basics (it survives the job closing) or holds a manual entry. The `/api/applications` routes provide CRUD. Moving a status past `saved` stamps `applied_at`.
- **Config** (`app/config.py`): pydantic-settings reads the **repo-root** `.env`, not `backend/.env`. The user's `.env` sets `DATABASE_URL` to `%LOCALAPPDATA%\FreshJobs\jobs.db`: the repo lives in OneDrive, and syncing the multi-GB database slowed every query. Without it the default is `backend/jobs.db`. API keys: `ADZUNA_APP_ID`/`ADZUNA_APP_KEY`, `HUNTER_API_KEY`, and SMTP settings.
- **Frontend** (`frontend/src/`): TanStack Query plus Tailwind v4.
  - Tabs: Feed, Tracker, Sources and Settings.
  - The Feed is laid out like LinkedIn and Jobright: a search bar, a row of filter pills (`FilterPill`/`Choice` in `ui.tsx`), and on screens ≥1024px a list/detail split (`JobDetailContent`). Narrow screens open the `JobDetail` drawer instead.
  - Every pill with an `onClear` has a ✕ when active and a Reset button in its menu (always shown, greyed out at the default); Sort resets to "Most recently posted", and "Reset all" restores `DEFAULT_FILTERS` (United States).
  - Location is three pills, Country, State and City, each a searchable checklist (`FacetList` in `LocationFilter.tsx`). State and City need exactly one country; changing the country clears both, and changing states clears cities.
  - Feed filters persist in localStorage (`freshjobs.filters.v2`).
  - `App.tsx` invalidates every query when the fast lane's `polling` flips from true to false.

## Gotchas

- **Migrations are add-only and automatic:** `db._add_missing_columns()` runs ALTER TABLE ADD COLUMN for new nullable or scalar-default columns. Renames, type changes and drops need a real migration (Alembic isn't set up).
- **SQLite runs in WAL mode with a 30s busy timeout** (`app/db.py`), so both lanes and the API can work at once. Without it, you get "database is locked". With the API reading nonstop, automatic checkpoints never reset the WAL (it reached 1.7 GB and slowed every read), so `run_poll` runs `PRAGMA wal_checkpoint(TRUNCATE)` after each lane.
- **New indexes on big tables** are created at startup by `_add_missing_columns()`, which takes minutes on the multi-GB `jobs.db` and holds the write lock meanwhile.
- **Datetimes:** the custom `DateTime` TypeDecorator in `app/models.py` always returns tz-aware UTC. Never use naive datetimes.
- **Workday quirks** (`app/sources/workday.py`):
  - Some tenants pin "featured" jobs above the newest-first list. A full scan learns the offset where that block ends, kept in memory per slug, and is lost on restart, so the next partial scan becomes a full scan.
  - Boards with `total == 2000` are capped, so their scans are marked incomplete and never close jobs.
  - A tenant's first scan (empty `known_ids`) fetches details only for jobs posted in the last `BASELINE_DETAIL_DAYS`. Older baseline jobs are list-only (no description), so harvesting hundreds of tenants doesn't block the slow lane for hours.
- **Poller robustness:**
  - `_fetch` catches every exception per source. Once, an uncaught AttributeError from one adapter closed the shared httpx client and failed the whole lane.
  - `process_company(commit_every=200)` commits in batches, because a big board's first scan (Amazon, JPMorgan, Apple: thousands of jobs) otherwise held the SQLite write lock for minutes and produced "database is locked".
  - A save error rolls back only that source.
- **Big-company adapters** (`amazon.py`, `microsoft.py`, `google.py`, `apple.py`, `oracle_hcm.py`, `icims.py`, `eightfold.py`, `successfactors.py`; shared retry/JSON-LD helpers in `http_util.py`) are all in the slow lane.
  - Amazon caps offset+limit at 10k, so full scans page per category group.
  - Microsoft is an Eightfold PCSX tenant (a subclass of `EightfoldAdapter`).
  - Google has no JSON API; the adapter parses the `AF_initDataCallback ds:1` blob.
  - JSearch's endpoint is `/search-v2` (`/search` was retired). It returns 10 jobs per request, and the free plan is 200 requests a month, hence `JSEARCH_MIN_INTERVAL_MINUTES=480` with 2 saved searches.
- **Other sources:**
  - SmartRecruiters returns 200 with zero jobs for a wrong slug.
  - Workable returns 429 after a burst of requests (`max_concurrency = 2`, every 3 h; fresh jobs come from `workable_search`, whose API caps pages at 20 and pins "featured" jobs of any age first).
  - Common Crawl produced ~1,500 Workday "sites" named `robots.txt`; `detect_board` now rejects site names with a dot, and `fix-workday` disabled or renamed the existing ones.
  - Remotive's free feed is tiny and delayed 24 hours.
  - Adzuna descriptions are snippets, so its `no_sponsor` detection is weak.
  - The Muse needs `sort=publication_date`.
- **Tests:** `tests/conftest.py` swaps in an in-memory StaticPool engine before the app is imported, and the `session` fixture recreates the schema for each test. Adapter tests are respx-mocked (`tests/test_sources*.py`). API tests use FastAPI's `TestClient` (`tests/test_api.py`).
- **Testing alerts end to end:** delete a matching job (and its `JobEvent`s and `JobLocation`s) from a source that's past its baseline, then trigger a poll; the job comes back as new. For local email, point `.env` at a local SMTP sink (`python -m aiosmtpd -n -l localhost:1025`, with `SMTP_STARTTLS=false`).
