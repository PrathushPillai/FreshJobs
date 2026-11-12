#!/usr/bin/env bash
# Nightly: a consistent copy of the live database (SQLite's online backup, safe while the backend writes),
# compressed, keeping the newest 3. Installed by setup-server.sh as /usr/local/bin/freshjobs-backup.
# Runs at the lowest CPU and disk priority: on the 2-vCPU server the pollers come first.
set -euo pipefail
DATA=/home/ubuntu/freshjobs-data
OUT="$DATA/backups/jobs-$(date +%Y%m%d).db"
NICE="nice -n 19 ionice -c 3"
rm -f "$OUT" "$OUT.gz.part"
$NICE sqlite3 "$DATA/jobs.db" ".backup '$OUT'"
# A copy that isn't a sound database is no backup: check it before keeping it.
[ "$($NICE sqlite3 -readonly "$OUT" 'PRAGMA quick_check;')" = "ok" ] || { echo "backup $OUT failed its check" >&2; rm -f "$OUT"; exit 1; }
$NICE gzip -1 -c "$OUT" > "$OUT.gz.part" && mv "$OUT.gz.part" "$OUT.gz" && rm -f "$OUT"
ls -1t "$DATA"/backups/jobs-*.db.gz | tail -n +4 | xargs -r rm --
echo "backup $OUT.gz $(du -h "$OUT.gz" | cut -f1)"
