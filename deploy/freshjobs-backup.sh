#!/usr/bin/env bash
# Nightly: a consistent copy of the live database (SQLite's online backup, safe while the backend writes),
# compressed, keeping the newest 3. Installed by setup-server.sh as /usr/local/bin/freshjobs-backup.
set -euo pipefail
DATA=/home/ubuntu/freshjobs-data
OUT="$DATA/backups/jobs-$(date +%Y%m%d).db"
sqlite3 "$DATA/jobs.db" ".backup '$OUT'"
gzip -f "$OUT"
ls -1t "$DATA"/backups/jobs-*.db.gz | tail -n +4 | xargs -r rm --
