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
- **Filters:** Country, State and City pills; experience level; remote. Every pill has a ✕ and a Reset button, and "Reset all" goes back to United States.
- **Hidden by default:** reposts, the same job on several sites, old jobs re-listed as new, expired jobs (closed jobs are re-checked automatically), and jobs that rule out sponsorship.
- **Repeated jobs:** to see them, open **More → Show repeated jobs** and tick the kinds you want:
  - **Repost:** the company posted the same job before.
  - **Duplicate:** the same job is also open on another site.
  - **Resurfaced:** an old job re-listed as new. Its badge shows the original date, such as "Resurfaced · from Sep 30".
  - The job's detail panel explains why it was flagged and links to the original.
- **H-1B sponsors:** every job shows its employer's certified H-1B filings from Department of Labor data. You can filter to sponsors only or sort by filings.
- **Tracker:** save or mark jobs as applied, then move them through each stage, with notes and a contact.
- **Recruiter finder:** LinkedIn and Google searches for each job's recruiters, plus a Hunter.io email lookup when that key is set.
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

1. In your browser, download the latest **LCA Disclosure Data** .xlsx from https://www.dol.gov/agencies/eta/foreign-labor/performance. Scripted downloads are blocked.
2. Save it in `backend/data/h1b/`.
3. Import it (about 7 minutes), then find job boards for the top sponsors:
   ```bash
   cd backend
   .venv/Scripts/python.exe -m app.h1b import data/h1b/LCA_Disclosure_Data_FY2026_Q3.xlsx
   .venv/Scripts/python.exe -m app.h1b discover --top 3000
   ```

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
- `frontend/src/`: the React app (Feed, LinkedIn & more, Tracker, Sources, Settings)

[CLAUDE.md](CLAUDE.md) has the full technical details and every known gotcha.

## Roadmap

1. ~~Core: Greenhouse, Lever and Ashby, polling, repost and expiry detection, feed, email~~
2. ~~Any-role search, related roles, location and experience filters, tracker, recruiter finder, Workday, SmartRecruiters, Workable, job APIs, discovery~~
3. ~~H-1B sponsor data, big-company career sites, Google Jobs, add-by-careers-URL~~
4. ~~Startups (YC, HN), JazzHR, Gem, Polymer, GitHub job lists, exact-title search, faster feed~~
5. Laptop stability: lighter polling on battery, daily database backup (proposed, not built)
6. Chatbot (Claude) that answers questions about the collected jobs
7. Cloud deploy (so polling runs while your laptop is off), resume matching
