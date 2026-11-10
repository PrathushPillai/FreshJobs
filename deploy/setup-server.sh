#!/usr/bin/env bash
# One-time setup of the FreshJobs server: Ubuntu 24.04 on Oracle Cloud's Always Free Ampere (ARM) VM.
# Run on the server as the "ubuntu" user:  bash setup-server.sh freshjobs-example.duckdns.org
#
# It installs Python, SQLite and Caddy (HTTPS with automatic Let's Encrypt certificates), opens ports 80/443
# in the server's own firewall (Oracle's Ubuntu image blocks them even when the cloud security list allows
# them), and installs the always-on backend service. Safe to run again.
set -euo pipefail
DOMAIN="${1:?usage: bash setup-server.sh <your domain, e.g. freshjobs-you.duckdns.org>}"
APP=/home/ubuntu/freshjobs
DATA=/home/ubuntu/freshjobs-data

echo "== packages"
sudo apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-venv python3-dev build-essential sqlite3 \
  debian-keyring debian-archive-keyring apt-transport-https curl gnupg iptables-persistent
if ! command -v caddy >/dev/null; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
  sudo apt-get update -y && sudo apt-get install -y caddy
fi

echo "== firewall: allow 80 and 443 (Oracle's image rejects everything but SSH by default)"
for port in 80 443; do
  sudo iptables -C INPUT -p tcp --dport "$port" -j ACCEPT 2>/dev/null || sudo iptables -I INPUT 5 -p tcp --dport "$port" -j ACCEPT
done
sudo netfilter-persistent save

echo "== folders"
mkdir -p "$APP" "$DATA/models" "$DATA/backups"

echo "== swap (4 GB), so a burst of memory never kills the backend"
if ! swapon --show | grep -q /swapfile; then
  sudo fallocate -l 4G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

echo "== backend service"
sudo cp "$APP/deploy/freshjobs-backend.service" /etc/systemd/system/freshjobs-backend.service
sudo systemctl daemon-reload
sudo systemctl enable freshjobs-backend

echo "== Caddy (HTTPS) for $DOMAIN"
sed "s/{{DOMAIN}}/$DOMAIN/g" "$APP/deploy/Caddyfile" | sudo tee /etc/caddy/Caddyfile >/dev/null
sudo chmod o+x /home/ubuntu  # Caddy serves the built frontend from the app folder
sudo systemctl enable caddy
sudo systemctl reload caddy || sudo systemctl restart caddy

echo "== nightly database backup (keeps 3)"
sudo cp "$APP/deploy/freshjobs-backup.sh" /usr/local/bin/freshjobs-backup && sudo chmod +x /usr/local/bin/freshjobs-backup
echo "15 4 * * * ubuntu /usr/local/bin/freshjobs-backup" | sudo tee /etc/cron.d/freshjobs-backup >/dev/null

echo "Done. Next, from the laptop: deploy/deploy.sh (code), then copy the database (deploy/README.md)."
