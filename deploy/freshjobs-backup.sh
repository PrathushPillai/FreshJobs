#!/usr/bin/env bash
# Nightly: a consistent copy of the live database, compressed, keeping the newest 3. Installed by setup-server.sh as
# /usr/local/bin/freshjobs-backup. Runs at the lowest CPU and disk priority: the pollers come first.
#
# The copy is SQLite's online backup in ONE step (Python's Connection.backup with pages=-1): a single read snapshot,
# which in WAL mode never blocks the pollers and never starts over. The sqlite3 shell's ".backup" copies in small
# steps and starts again whenever the database changes in between: on 2026-11-12 the 09:15 UTC run was still going
# at 17:30, restarting all day while the pollers wrote, and kept the 2-vCPU server's CPU and disk busy.
set -euo pipefail
DATA=/home/ubuntu/freshjobs-data
OUT="$DATA/backups/jobs-$(date +%Y%m%d).db"
NICE="nice -n 19 ionice -c 3"
cleanup() { rm -f "$OUT" "$OUT-shm" "$OUT-wal" "$OUT-journal" "$OUT.gz.part"; }

# Never two at once (a slow run must not pile up behind the next night's).
exec 9>"$DATA/backups/.lock"
flock -n 9 || { echo "a backup is still running; skipped"; exit 0; }

cleanup
started=$(date +%s)
$NICE python3 - "$DATA/jobs.db" "$OUT" <<'PY'
import sqlite3, sys
src = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True, timeout=120)
dst = sqlite3.connect(sys.argv[2])
src.backup(dst, pages=-1)  # one step: one consistent snapshot, never restarted
dst.execute("PRAGMA journal_mode=DELETE")
dst.close()
src.close()
PY
# A copy that isn't a sound database is no backup. The full check reads all 15 GB (about 25 minutes on this disk),
# so it runs on Sundays; other nights check that the copy opens and its tables can be read.
if [ "$(date +%u)" = "7" ]; then
  check=$($NICE sqlite3 -readonly "$OUT" 'PRAGMA quick_check;')
else
  check=$($NICE sqlite3 -readonly "$OUT" 'SELECT CASE WHEN count(*) > 0 THEN "ok" END FROM companies;')
fi
[ "$check" = "ok" ] || { echo "backup $OUT failed its check: $check" >&2; cleanup; exit 1; }
$NICE gzip -1 -c "$OUT" > "$OUT.gz.part" && mv "$OUT.gz.part" "$OUT.gz" && rm -f "$OUT" "$OUT-shm" "$OUT-wal" "$OUT-journal"
ls -1t "$DATA"/backups/jobs-*.db.gz | tail -n +4 | xargs -r rm --
echo "backup $OUT.gz $(du -h "$OUT.gz" | cut -f1) in $(( ($(date +%s) - started) / 60 )) min"
