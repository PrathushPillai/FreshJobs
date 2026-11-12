# Running FreshJobs on the server

Any Ubuntu 24.04 VM works, x86 or ARM, with at least 4 GB of RAM (8 GB is better: the 13 GB database reads from
the page cache) and an SSD disk of 100 GB or more. Caddy serves the built frontend and proxies `/api` to the
backend, with HTTPS from Let's Encrypt. Every path assumes the user `ubuntu`.

## Google Cloud (first home, from 2026-11: the $300 / 90-day free trial)

The plan: Google Cloud while the trial lasts, then AWS's free plan or Hetzner (or Oracle Always Free, if its
support approves the account). Moving is the same steps on the next server.

In the Google Cloud console (the user does these):
1. Start the free trial (a card is needed for identity; the trial never charges it unless you upgrade).
2. Compute Engine → VM instances → Create instance:
   - Region `us-central1` (Iowa) or `us-east1` (South Carolina), any zone.
   - Machine `e2-standard-4` (4 vCPU, 16 GB), about $98/month. The first server was `e2-standard-2` (2 vCPU,
     8 GB, about $49), and its CPU was the limit during the US daytime: polls saved only part of the due boards,
     so boards had to be checked less often. It was upgraded on 2026-11-12 (stop the VM, Edit → Machine type,
     start; the static IP and disk stay). It can be downgraded the same way.
   - Boot disk: Ubuntu 24.04 LTS (x86/64), **Balanced persistent disk** (an SSD; the standard HDD disk makes
     SQLite crawl), 100 GB, about $10/month.
   - Firewall: tick "Allow HTTP traffic" and "Allow HTTPS traffic".
   - Security → Manage access → Add item: paste the laptop's `~/.ssh/freshjobs_oracle.pub` with the user name
     changed to `ubuntu` at the end of the line (`ssh-ed25519 AAAA... ubuntu`). Google creates that user.
3. VPC network → IP addresses: make the VM's external IP **static** (Reserve), so it survives a restart.
4. Point a DNS name at that IP (DuckDNS is free).

Roughly $60/month in all, so the $300 lasts the whole 90 days. When the trial ends (90 days or $300), the VM
stops, and its disk is deleted 30 days later unless the account is upgraded: move about a week before.

## Oracle Cloud (Always Free, if its support approves the account)

Ampere A1 (ARM), 4 OCPUs, 24 GB RAM, Ubuntu 24.04, a 150 GB boot volume; open TCP 80 and 443 in the subnet's
security list.

| Where | What |
|---|---|
| `/home/ubuntu/freshjobs` | the code (sent by `deploy.sh`) and its `.env` |
| `/home/ubuntu/freshjobs-data/jobs.db` | the database |
| `/home/ubuntu/freshjobs-data/models` | the resume-match model, downloaded on first use |
| `/home/ubuntu/freshjobs-data/backups` | nightly compressed copies (09:15 UTC: 4:15 am Eastern, 1:15 am Pacific, the quietest hour for US postings; lowest CPU and disk priority, SQLite's online backup in one step, so it never restarts while the pollers write (the sqlite3 shell's `.backup` restarted all day on 2026-11-12 and kept the server busy for 8 hours); a full `PRAGMA quick_check` of the copy on Sundays, a quick read check other nights; `flock` keeps two from running at once), the newest 3; output in `journalctl -t freshjobs-backup` |
| `freshjobs-backend` (systemd) | the API, pollers and alerts; restarts by itself |
| `caddy` (systemd) | HTTPS, the frontend and `/api` |

## First time

1. Create the VM (see above), with ports 80 and 443 open in the cloud's firewall, and point a DNS name at its
   public IP (DuckDNS works, for free). Check SSH: `ssh -i ~/.ssh/freshjobs_oracle ubuntu@<ip>`.
2. From the laptop: `FRESHJOBS_HOST=ubuntu@<ip> deploy/deploy.sh`. On a fresh VM only the copying part works:
   `python3-venv` isn't installed yet, so it stops at "pip: No such file or directory". That's fine.
3. On the server: `rm -rf ~/freshjobs/backend/.venv && bash ~/freshjobs/deploy/setup-server.sh <domain>`.
4. Put the server's `.env` in `~/freshjobs/.env` (the laptop's, changed as below; `chmod 600`). Set
   `SCHEDULER_ENABLED=false` at first, so the server polls nothing until you switch over.
5. Build the venv on the server **without starting the service**: `cd ~/freshjobs/backend && python3 -m venv .venv
   && .venv/bin/pip install -r requirements.txt`. Don't run `deploy.sh` again yet: its restart would start the
   backend before the database is there, and it would create an empty `jobs.db` whose `-wal`/`-shm` files could
   damage the real one when it's copied in.
6. Copy the database (below), check it, then `sudo systemctl start freshjobs-backend`. It answers within a few
   minutes; run `scripts/check_portal.py --base https://<domain> --frontend https://<domain>` from the laptop.
7. To switch over: set `SCHEDULER_ENABLED=true` in the server's `.env`, `sudo systemctl restart freshjobs-backend`,
   and stop the laptop's backend (close its window). Only one copy may poll.

Server `.env` differences from the laptop's:

```
DATABASE_URL=sqlite:////home/ubuntu/freshjobs-data/jobs.db
PUBLIC_URL=https://<domain>
SECURE_COOKIES=true
API_DOCS=false
CORS_ORIGINS=["https://<domain>"]
```

## Copying the database

Make a consistent copy on the laptop with SQLite's online backup (safe while the backend runs; 8 minutes for
15 GB), then stream it compressed over SSH, checksumming both ends, into a temporary name:

```
backend/.venv/Scripts/python.exe -c "import sqlite3; s=sqlite3.connect('file:C:/Users/prath/FreshJobs/jobs.db?mode=ro', uri=True); d=sqlite3.connect('C:/Users/prath/FreshJobs/jobs-copy.db'); s.backup(d); d.execute('PRAGMA journal_mode=DELETE'); d.close()"
cat C:/Users/prath/FreshJobs/jobs-copy.db | tee >(sha256sum) | gzip -1 | ssh -i ~/.ssh/freshjobs_oracle ubuntu@<ip> 'bash -c "gunzip | tee >(sha256sum >&2) > ~/freshjobs-data/jobs.db.part"'
```

The two checksums must match. On the server, `sqlite3 -readonly ~/freshjobs-data/jobs.db.part "PRAGMA quick_check"`
must print `ok` (25 minutes on Google Cloud's 100 GB balanced disk); then `mv jobs.db.part jobs.db`. Copy
`workday_state.json` and `autodiscover.json` from `C:/Users/prath/FreshJobs` to `~/freshjobs-data/` too: without the
first, every Workday board's next scan is a full one. Delete `jobs-copy.db` on the laptop afterwards.

On 2026-11-11 the 15.1 GB copy uploaded in 17 minutes (about 15 MB/s compressed).

## Updating

Run the tests and the build, then `FRESHJOBS_HOST=ubuntu@<ip> deploy/deploy.sh`. On the server the tests need no
changes: `tests/conftest.py` overrides the server-only settings (`SECURE_COOKIES`, `PUBLIC_URL`, `CORS_ORIGINS`). Logs: `journalctl -u
freshjobs-backend -f` on the server.
