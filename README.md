# FreshJobs

A personal job feed for an international student looking for work in the US. It checks employers' own job boards every few minutes, so new roles show up within minutes instead of hours later on LinkedIn. It hides reposts, expired jobs and jobs that rule out visa sponsorship, and it shows every employer's H-1B history.

**Current size (Nov 2026):** about 25,700 sources and 1.56 million open jobs, with 10,000–20,000 new jobs a day.

## Start it (everyday use)

Double-click **`start-freshjobs.bat`** in the project folder. It:
- starts whichever of the backend and frontend isn't already running, each in its own window;
- opens the feed at http://localhost:5173.

The backend window keeps the laptop awake while it is open. Close it to stop polling and let the laptop sleep again.

After a restart, the backend needs about 2 minutes before the feed loads, while it opens the database and rebuilds its search index if needed.

### If it doesn't start

| Symptom | Likely cause | Fix |
|---|---|---|
| "Can't reach the backend" / a 500 under the search bar, and the start script waits forever | The database file is damaged (this happened once, after a blue-screen crash mid-write) | Check the backend window for "database disk image is malformed". If you see it, ask Claude to recover the database (see CLAUDE.md, Gotchas). |
| Backend window says "unable to open database file" | `DATABASE_URL` in `.env` points to a folder that doesn't exist, or one under `AppData` | Keep the database at `C:\Users\prath\FreshJobs\jobs.db`, never in OneDrive or `AppData` |
| Feed opens but is empty or slow right after a start | The search index is still being rebuilt | Wait a few minutes |

## What it collects

- **Company job boards:** Greenhouse, Lever, Ashby, Workday, SmartRecruiters, Workable, Oracle, iCIMS, Eightfold, SuccessFactors, Rippling, BambooHR, Recruitee, Breezy, Pinpoint, Gem and Polymer.
- **Workable's own job search:** every Workable employer's newest US jobs, every few minutes.
- **JazzHR:** about 110,000 jobs at thousands of small US companies, read from JazzHR's public sitemaps.
- **Big-tech career sites:** Amazon, Microsoft, Google and Apple.
- **Job APIs:** The Muse, Remotive, RemoteOK, Adzuna (disabled: trial key used up), and the SimplifyJobs new-grad and internship lists.
- **Community GitHub job lists:** about 5,300 job boards from an MIT-licensed dataset, plus the speedyapply AI/SWE new-grad lists. These are synced every 12 hours.
- **Startups:** every hiring Y Combinator company (each role shows its visa policy) and Hacker News "Who is hiring?" threads.
- **Google Jobs (via JSearch):** postings that exist only on LinkedIn, Indeed or Glassdoor.
- **Careers pages with no job board:** paste any careers page on the Sources page.
- **Grows by itself:** when a job on a multi-company site links to a company board we don't track yet, that board is added automatically (up to 200 per check). Employers seen on job APIs are also probed for their own boards.
- **LinkedIn & more tab:** one-click LinkedIn searches (past hour, Easy Apply, hiring posts), plus Indeed, Glassdoor, Google Jobs and X. LinkedIn itself can't be collected.

LinkedIn, Indeed, Glassdoor and Meta's careers site are never scraped, against their terms. Their postings come in through Google Jobs instead.

## Using the feed

- **For you:** the feed opens on your roles, set in Settings or the "Your roles" bar. That bar suggests related roles, with job counts, that you can add.
- **Search:** typing a search looks through all jobs.
  - Related roles are included by default, so "data engineer" also finds data-platform and analytics-platform roles. Click a related-role chip to switch it off.
  - Exact matches always come first. The count line says how many results have the exact title.
  - Turn on **Exact title** in the search bar to search only what you typed.
- **Fresh:** every job shows when it was **posted** and when we first **found** it. Sort by either, or filter by **Date posted** or **Found by us**. The strip above the list shows how many jobs came in during the last hour, 6 hours and 24 hours.
- **Filters:** Country, State and City pills; experience level; remote. Changes inside a filter take effect when you press **Apply** (Cancel or Esc drops them), and the results load in the background while you choose, so Apply is usually instant. Every pill has a ✕ and a Reset button, and "Reset all" goes back to United States. **For you** and **All jobs** keep their own filters and sort.
- **Hidden by default:** reposts, the same job on several sites, old jobs re-listed as new, and expired jobs (closed jobs are re-checked automatically).
- **Says it sponsors:** a green badge on jobs whose posting says it sponsors visas ("We do sponsor visas!").
- **Shown, but flagged:** jobs that rule out sponsorship or OPT/CPT carry a red badge (checked by hand against real postings: it catches about 9 in 10). Some employers can still be persuaded, so they stay in the feed. **Visa → Hide jobs that rule out sponsorship or OPT/CPT** removes them.
- **Repeated jobs:** to see them, open **More → Show repeated jobs** and tick the kinds you want:
  - **Repost:** the company posted the same job before.
  - **Duplicate:** the same job is also open on another site.
  - **Resurfaced:** an old job re-listed as new. Its badge shows the original date, such as "Resurfaced · from Sep 30".
  - The job's detail panel explains why it was flagged and links to the original.
- **H-1B sponsors:** every job shows its employer's certified H-1B filings from Department of Labor data. You can filter to sponsors only or sort by filings.
- **Company logos:** jobs show the employer's real logo when its website can be identified (Apple's apple, Amazon's arrow…); otherwise coloured initials.
- **H-1B for this role:** the job panel shows how many H-1Bs the employer filed for this kind of role, with the median offered salary and the main cities. For example: "1,240 filings as Software Developers · median $148k · mostly in Seattle". It also lists the employer's other sponsored occupations.
- **OPT, CPT and E-Verify:**
  - **OPT/CPT welcome:** the posting says F-1 students on OPT, STEM OPT or CPT can apply.
  - **No OPT/CPT:** the posting rules them out. Like "No sponsorship" jobs, they stay in the feed unless you hide them from the Visa pill.
  - **E-Verify:** the employer is in USCIS's E-Verify list (or its posting says it uses E-Verify), which a STEM OPT employer must be. **E-Verify ended** means every one of its accounts has been closed. **Visa → Only E-Verify employers** filters to them.
- **H-1B approvals:** the job panel shows how many H-1B petitions USCIS approved and denied for the employer in FY2024–FY2026, including "new" workers, those new to H-1B as a new grad would be.
- **Tracker:** save or mark jobs as applied, then move them through each stage, with notes and a contact.
- **Recruiter finder:** LinkedIn and Google searches for each job's recruiters, plus a Hunter.io email lookup when that key is set. **🔎 Hiring posts on LinkedIn**, next to Apply, finds recent posts saying they're hiring for that role at that company.
- **Resume match:** upload your resume in **My profile** (PDF, Word or text) and every job gets a 0–100 match with the reasons. It runs on your own machine. PDFs whose words come out joined together are repaired automatically.
- **Email alerts:** one email per check, listing new jobs that match your roles.

## Effect on the laptop

Measured on 2026-11-06 with the full database:

| | CPU (16 cores) | Memory | Disk |
|---|---|---|---|
| Backend | 6–9% | 1–1.6 GB | reads 4–40 MB/s, writes under 6 MB/s while a check runs |
| Frontend | about 0% | under 60 MB | none |

- **Storage:**
  - Database about 9 GB, in `C:\Users\prath\FreshJobs\`.
  - Python environment 114 MB, Node packages 120 MB, downloaded data 241 MB.
- **GPU:** not used.
- **Network:** checks run every 5 minutes, with many small requests in parallel.
- **Keep-awake:** the backend window stops Windows from sleeping. Closing that window turns this off.
- **Crashes:** the laptop has had driver blue screens since August 2026, before the portal existed. The portal doesn't cause them, but its constant disk activity makes a crash mid-write more likely to damage the database. The suggested fixes (BIOS and driver updates, freeing disk space, lighter "laptop mode" polling, daily backups, a cloud server) are not applied yet.

## Setup from scratch (Windows)

Backend (Python 3.12):

```bash
cd backend
py -3.12 -m venv .venv
.venv/Scripts/pip install -r requirements.txt
.venv/Scripts/python -m uvicorn app.main:app --port 8000
```

Frontend (Node 20+), in a second terminal:

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173. Or run both with Docker: `docker compose up --build`.

The **first** check of a company records its existing jobs as a baseline. They're shown but don't trigger alerts, and anything new after that counts as fresh.

## Settings (`.env` in the project root)

Copy `.env.example` to `.env`, fill it in, and restart the backend.

- **`DATABASE_URL`:** where the database lives, here `sqlite:///C:/Users/prath/FreshJobs/jobs.db`.
  - Keep it out of OneDrive: syncing a 9 GB file slows everything.
  - Keep it out of `AppData`: the Claude app redirects that folder, so the start script can't see it.
- **Email alerts:** SMTP settings. For Gmail, use an [App Password](https://myaccount.google.com/apppasswords).
- **Adzuna** (`ADZUNA_APP_ID`, `ADZUNA_APP_KEY`): https://developer.adzuna.com/
- **Google Jobs via JSearch** (`JSEARCH_API_KEY`): free plan, 200 requests a month. https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch
- **Hunter.io** (`HUNTER_API_KEY`): 25 free searches a month. https://hunter.io/api-keys

## H-1B sponsor data

All of these are downloaded in a browser; scripted downloads are blocked.

| Data | Where to get it | Save in |
|---|---|---|
| **DOL LCA filings** (H-1B by role and salary) | https://www.dol.gov/agencies/eta/foreign-labor/performance → LCA Programs. Each year's file is cumulative, so take the latest one per fiscal year (e.g. `FY2025_Q4`, `FY2026_Q3`). | `backend/data/h1b/` |
| **USCIS H-1B approvals and denials** | https://www.uscis.gov/tools/reports-and-studies/h-1b-employer-data-hub → Crosstab View → pick one Fiscal Year → Download → Crosstab → CSV, once per year | `backend/data/uscis_h1b/` |
| **E-Verify employers** | https://www.e-verify.gov/e-verify-employer-search. Set Date Enrolled to "Last 30 years" and keep Open and Terminated ticked, then Download → Crosstab. The full list is too large, so export one **Industry** at a time. | `backend/data/everify/` |

Then import them (the LCA step takes about 15 minutes):
```bash
cd backend
.venv/Scripts/python.exe -m app.h1b import data/h1b/LCA_Disclosure_Data_FY2025_Q4.xlsx data/h1b/LCA_Disclosure_Data_FY2026_Q3.xlsx
.venv/Scripts/python.exe -m app.uscis import data/uscis_h1b
.venv/Scripts/python.exe -m app.everify import data/everify
.venv/Scripts/python.exe -m app.h1b discover --top 3000     # optional: find job boards for the top sponsors
```
Restart the backend afterwards.

## Adding companies

Paste a careers link on the **Sources** page; the app finds the job board behind it. Or append it to [backend/data/companies.yaml](backend/data/companies.yaml):

| ATS             | Slug                                                                   |
|-----------------|------------------------------------------------------------------------|
| greenhouse      | `<slug>` from `boards.greenhouse.io/<slug>`                            |
| lever           | `<slug>` from `jobs.lever.co/<slug>`                                   |
| ashby           | `<slug>` from `jobs.ashbyhq.com/<slug>`                                |
| workday         | `tenant\|wdN\|site` from `<tenant>.<wdN>.myworkdayjobs.com/<site>`      |
| smartrecruiters | company id from `jobs.smartrecruiters.com/<slug>` (case-sensitive)     |
| workable        | `<slug>` from `apply.workable.com/<slug>`                              |

## Useful commands

```bash
cd backend
.venv/Scripts/python -m pytest                                  # tests
.venv/Scripts/python scripts/check_portal.py                    # check the running portal end to end (read-only)
.venv/Scripts/python -m app.pipeline.bulk_discovery niche --sector all --dry-run   # robotics and healthcare boards
.venv/Scripts/python -m app.pipeline.poller --once              # one check without the web server
.venv/Scripts/python -m app.pipeline.discovery --file data/discover_seed.txt   # find boards for company names
.venv/Scripts/python -m app.pipeline.startups                   # add hiring YC startups now
.venv/Scripts/python -m app.pipeline.startup_lists              # import the GitHub job lists now
```

## How it works

Checks run in three lanes at once, so a slow site never delays the fast ones:
- **fast:** most job boards, every 5 minutes;
- **slow:** sites that need one request per job, such as Workday, SmartRecruiters and big tech;
- **bulk:** Workable's ~4,400 boards, rate-limited by Workable.

Where things live:
- `backend/app/sources/`: one adapter per job site
- `backend/app/pipeline/`: polling, repost and duplicate detection, location, experience and sponsorship parsing, search, discovery and harvesting
- `backend/app/main.py`: the REST API
- `frontend/src/`: the React app (Feed, My profile, LinkedIn & more, Tracker, Sources, Settings)
- `backend/app/matching.py`, `backend/app/pipeline/scoring.py`: resume match scores

[CLAUDE.md](CLAUDE.md) has the full technical details and every known gotcha.

## Roadmap

1. ~~Core: Greenhouse, Lever and Ashby, polling, repost and expiry detection, feed, email~~
2. ~~Any-role search, related roles, location and experience filters, tracker, recruiter finder, Workday, SmartRecruiters, Workable, job APIs, discovery~~
3. ~~H-1B sponsor data, big-company career sites, Google Jobs, add-by-careers-URL~~
4. ~~Startups (YC, HN), JazzHR, Gem, Polymer, GitHub job lists, exact-title search, faster feed~~
5. ~~Resume match: upload a resume in My profile and every job gets a 0–100 score with the reasons (runs locally, no API key)~~
6. Laptop stability: lighter polling on battery, daily database backup (proposed, not built)
7. Chatbot (Claude) that answers questions about the collected jobs
8. Cloud deploy (so polling runs while your laptop is off), logins so each person has their own profile
