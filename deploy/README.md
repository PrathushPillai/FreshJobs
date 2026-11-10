# Running FreshJobs on the server

The server is Oracle Cloud's Always Free tier: Ampere A1 (ARM), 4 OCPUs, 24 GB RAM, Ubuntu 24.04, a 150 GB
boot volume. Caddy serves the built frontend and proxies `/api` to the backend, with HTTPS from Let's Encrypt.

| Where | What |
|---|---|
| `/home/ubuntu/freshjobs` | the code (sent by `deploy.sh`) and its `.env` |
| `/home/ubuntu/freshjobs-data/jobs.db` | the database |
| `/home/ubuntu/freshjobs-data/models` | the resume-match model, downloaded on first use |
| `/home/ubuntu/freshjobs-data/backups` | nightly compressed copies, the newest 3 |
| `freshjobs-backend` (systemd) | the API, pollers and alerts; restarts by itself |
| `caddy` (systemd) | HTTPS, the frontend and `/api` |

## First time

1. Create the VM (see above), open TCP 80 and 443 in its subnet's security list, and point a DNS name at its
   public IP (DuckDNS works, for free).
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
