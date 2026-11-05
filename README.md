# FreshJobs

A personal job feed that checks employers' own job boards every few minutes, so you see new roles within minutes instead of hours later on LinkedIn. Sources:
- **Company job boards:** Greenhouse, Lever, Ashby, Workday, SmartRecruiters, Workable, Oracle, iCIMS, Eightfold, SuccessFactors, Rippling, BambooHR, Recruitee, Breezy and Pinpoint.
- **Big-tech career sites:** Amazon, Microsoft, Google and Apple.
- **Job APIs:** The Muse, Remotive, RemoteOK and Adzuna.
- **SimplifyJobs lists:** the community new-grad and internship lists on GitHub.
- **Careers pages with no job board:** paste any company careers page on the Sources page. If no ATS is behind it, the jobs are read from the page itself.
- **LinkedIn & more tab:** one-click LinkedIn searches already filtered to the past hour, Easy Apply only, entry level, and recruiters' "hiring" posts, plus Indeed, Glassdoor, Google Jobs and X.
- **Startups:** every Y Combinator startup that's hiring in the US or remotely (with each role's visa policy), plus Hacker News "Who is hiring?" posts, which often appear nowhere else.
- **Auto-harvesting:** when an aggregator job links to a company board we don't track yet, that board is added automatically (up to 100 per check), so the company's future jobs come straight from the source.
- **Google Jobs:** brings in LinkedIn, Indeed and Glassdoor postings.

- **Any role:** search anything. Related roles are included, so "data analyst" also finds BI analysts, analytics engineers, data scientists and more.
- **Fresh:** every job shows when it was **posted** and when we first **found** it. You can sort by either, and filter by **Date posted** (past 24 hours, 3 days, week…) or by **Found by us** (past hour, 6 hours…).
- **No junk:** reposts, the same job listed on several sites, old jobs re-listed as new, and expired jobs are flagged and hidden.
- **Filters:** country, state and city; experience level (entry, mid, senior, or "asks ≤ N years"); remote.
- **Visa-aware:** jobs whose descriptions rule out sponsorship ("will not sponsor", "must be a US citizen", "security clearance"…) are hidden by default.
- **Tracker:** mark jobs as applied or saved, then move them through assessment, interviewing, offer and so on, with notes and a contact for each.
- **Recruiter finder:** one-click LinkedIn and Google searches for each job's recruiters and "hiring" posts. With a Hunter.io key, it also finds the company's recruiter emails and email format.
- **Email alerts:** one email per check, listing new jobs that match your alert keywords.
- **H-1B sponsors:** every job shows how many certified H-1B filings its employer made (US Department of Labor data). Filter to "H-1B sponsors only", or sort by most filings.
- **Add any careers page:** paste a company's careers link on the Sources page, and the app finds the job board behind it.
- **Grows by itself:** employers seen on job APIs are checked for their own Greenhouse, Lever or Ashby board, and added if one is found.

## Run it locally (Windows)

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

Open http://localhost:5173. The backend checks every company when it starts and then every `POLL_MINUTES` after that.

The **first** check of a company records its existing jobs as a baseline: they're shown, but they don't trigger alerts. From the next check on, anything new counts as fresh.

Or run both with Docker: `docker compose up --build`.

## Email alerts

1. Copy `.env.example` to `.env` (in the project root).
2. Fill in the SMTP settings. For Gmail, create an [App Password](https://myaccount.google.com/apppasswords) (this needs 2-step verification) and use it as `SMTP_PASSWORD`.
3. Restart the backend.

## H-1B sponsor data

1. In your browser, download the latest **LCA Disclosure Data** .xlsx from https://www.dol.gov/agencies/eta/foreign-labor/performance. Scripted downloads are blocked.
2. Save it in `backend/data/h1b/`.
3. Import it, then find job boards for the top sponsors:
   ```bash
   cd backend
   .venv/Scripts/python.exe -m app.h1b import data/h1b/LCA_Disclosure_Data_FY2026_Q3.xlsx
   .venv/Scripts/python.exe -m app.h1b discover --top 3000
   ```

## Optional free API keys

Add these to `.env`, then restart the backend:

- **Adzuna** (`ADZUNA_APP_ID`, `ADZUNA_APP_KEY`): jobs from thousands more employers. Sign up at https://developer.adzuna.com/
- **Google Jobs via JSearch** (`JSEARCH_API_KEY`): postings that exist only on LinkedIn, Indeed or Glassdoor. Get a free key (200 requests a month) at https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch, then edit the saved searches (`ats: jsearch`) in `backend/data/companies.yaml`.
- **Hunter.io** (`HUNTER_API_KEY`): recruiter email lookup, 25 free searches a month, cached for 30 days per company. Sign up at https://hunter.io/api-keys

## Adding companies

Add a company from the **Sources** page, or append it to [backend/data/companies.yaml](backend/data/companies.yaml). To find boards for a list of company names automatically, run:

```bash
cd backend
.venv/Scripts/python.exe -m app.pipeline.discovery --file data/discover_seed.txt
```

| ATS             | Slug                                                                   |
|-----------------|------------------------------------------------------------------------|
| greenhouse      | `<slug>` from `boards.greenhouse.io/<slug>`                            |
| lever           | `<slug>` from `jobs.lever.co/<slug>`                                   |
| ashby           | `<slug>` from `jobs.ashbyhq.com/<slug>`                                |
| workday         | `tenant\|wdN\|site` from `<tenant>.<wdN>.myworkdayjobs.com/<site>`      |
| smartrecruiters | company id from `jobs.smartrecruiters.com/<slug>` (case-sensitive)     |
| workable        | `<slug>` from `apply.workable.com/<slug>`                              |

Workday and SmartRecruiters run in a separate "slow" lane, because they need one request per job. The first Workday scan takes about 30 minutes; after that, each check only reads the newest jobs.

## Useful commands

```bash
cd backend
.venv/Scripts/python -m pytest                      # tests
.venv/Scripts/python -m app.pipeline.poller --once  # run one check without the web server
```

## Layout

- `backend/app/sources/`: one adapter per ATS, each returning a common `NormalizedJob`
- `backend/app/pipeline/`: poller, repost detection, location, experience and sponsorship parsing, related-role search, discovery
- `backend/app/notify/email.py`: alert emails
- `backend/app/recruiters.py`: recruiter search links and Hunter.io lookup
- `backend/app/main.py`: the REST API (`/api/jobs`, `/api/applications`, `/api/facets/locations`, `/api/companies`, `/api/prefs`, `/api/status`, `/api/poll`)
- `frontend/src/`: the React app (Feed, Tracker, Sources, Settings)

## Roadmap

1. ~~Core: Greenhouse, Lever and Ashby, polling, repost and expiry detection, feed, email~~
2. ~~Any-role search, related roles, location and experience filters, tracker, recruiter finder, Workday, SmartRecruiters, Workable, job APIs, discovery~~
3. ~~H-1B sponsor data, big-company career sites, Google Jobs (LinkedIn/Indeed), add-by-careers-URL~~
4. Chatbot (Claude) that answers questions about the collected jobs
5. Cloud deploy (so polling runs while your laptop is off), resume matching
