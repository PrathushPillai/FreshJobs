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
   - Machine `e2-standard-2` (2 vCPU, 8 GB), about $49/month. `e2-medium` (4 GB, about $25) also works but
     leaves less memory for caching the database.
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
| `/home/ubuntu/freshjobs-data/backups` | nightly compressed copies, the newest 3 |
| `freshjobs-backend` (systemd) | the API, pollers and alerts; restarts by itself |
| `caddy` (systemd) | HTTPS, the frontend and `/api` |

## First time

1. Create the VM (see above), with ports 80 and 443 open in the cloud's firewall, and point a DNS name at its
   public IP (DuckDNS works, for free). Check SSH: `ssh -i ~/.ssh/freshjobs_oracle ubuntu@<ip>`.
2. From the laptop: `FRESHJOBS_HOST=ubuntu@<ip> deploy/deploy.sh` (it copies the code; the restart at the end
   fails the first time because the service isn't installed yet, which is fine).
3. On the server: `bash ~/freshjobs/deploy/setup-server.sh <domain>`.
4. Put the server's `.env` in `~/freshjobs/.env` (the laptop's, changed as below), then copy the database
   (below) and `sudo systemctl start freshjobs-backend`.

Server `.env` differences from the laptop's:

```
DATABASE_URL=sqlite:////home/ubuntu/freshjobs-data/jobs.db
PUBLIC_URL=https://<domain>
SECURE_COOKIES=true
API_DOCS=false
CORS_ORIGINS=["https://<domain>"]
```

## Copying the database

Make a consistent copy on the laptop with SQLite's online backup (safe while the backend runs), then stream it
compressed over SSH:

```
backend/.venv/Scripts/python.exe -c "import sqlite3; s=sqlite3.connect(r'C:/Users/prath/FreshJobs/jobs.db'); d=sqlite3.connect(r'C:/Users/prath/FreshJobs/jobs-copy.db'); s.backup(d); d.close()"
ssh -C -i ~/.ssh/freshjobs_oracle ubuntu@<ip> 'cat > ~/freshjobs-data/jobs.db' < C:/Users/prath/FreshJobs/jobs-copy.db
```

Then delete `jobs-copy.db` on the laptop, and stop the laptop's backend so two copies don't both poll.

## Updating

Run the tests and the build, then `FRESHJOBS_HOST=ubuntu@<ip> deploy/deploy.sh`. Logs: `journalctl -u
freshjobs-backend -f` on the server.
