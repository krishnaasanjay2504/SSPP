#!/usr/bin/env bash
# Pull the latest code from GitHub and restart SSPP.   sudo bash /opt/sspp/deploy/update.sh
set -euo pipefail
APP_DIR=/opt/sspp
sudo -u sspp git -C "$APP_DIR" pull --ff-only
sudo -u sspp "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt" -q
set -a; . /etc/sspp/sspp.env; set +a
sudo -u sspp --preserve-env "$APP_DIR/.venv/bin/python" "$APP_DIR/db/init_db.py"   # adds any new tables
systemctl restart sspp
sleep 3 && systemctl --no-pager --lines=5 status sspp
