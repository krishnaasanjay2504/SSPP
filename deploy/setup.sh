#!/usr/bin/env bash
# One-shot installer for SSPP on an Oracle Cloud "Always Free" Ubuntu 24.04 VM
# (Ampere A1 / arm64 or AMD x86_64).
#
#   curl -fsSL https://raw.githubusercontent.com/krishnaasanjay2504/SSPP/main/deploy/setup.sh -o setup.sh
#   sudo bash setup.sh
#
# Installs: Docker + Oracle Database 23ai Free (container), Python venv, the app as a
# systemd service (waitress on 127.0.0.1:5000), nginx on port 80, optional HTTPS.
# Safe to re-run: existing database, secrets and data are kept.
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/krishnaasanjay2504/SSPP.git}"
APP_DIR=/opt/sspp
ENV_FILE=/etc/sspp/sspp.env
DB_CONTAINER=sspp-oracle
DB_IMAGE="${DB_IMAGE:-gvenzl/oracle-free:23-slim}"
TIMEZONE="${TIMEZONE:-Asia/Kolkata}"

[ "$(id -u)" -eq 0 ] || { echo "Run with sudo: sudo bash setup.sh"; exit 1; }
log() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }

# ---------- questions (or pass as env vars) ----------
if [ -z "${ADMIN_PASSWORD:-}" ] && [ ! -f "$ENV_FILE" ]; then
  read -rsp "New password for admin@sspp.edu (min 10 chars): " ADMIN_PASSWORD; echo
fi
if [ -z "${DOMAIN+x}" ]; then
  read -rp "Domain name pointing to this server for HTTPS (blank = HTTP only): " DOMAIN
fi
if [ -n "${DOMAIN:-}" ] && [ -z "${CERT_EMAIL:-}" ]; then
  read -rp "Email for Let's Encrypt expiry notices: " CERT_EMAIL
fi
KEEP_DEMO_LOGINS="${KEEP_DEMO_LOGINS:-0}"   # 1 = keep the published demo passwords working

# ---------- system packages ----------
log "Installing system packages"
timedatectl set-timezone "$TIMEZONE" || true
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y docker.io git nginx python3 python3-venv python3-dev build-essential \
  pkg-config libcairo2-dev iptables-persistent openssl curl
systemctl enable --now docker

# ---------- firewall (Oracle images block everything except SSH) ----------
log "Opening ports 80 and 443"
for p in 80 443; do
  iptables -C INPUT -p tcp --dport "$p" -m state --state NEW -j ACCEPT 2>/dev/null \
    || iptables -I INPUT 1 -p tcp --dport "$p" -m state --state NEW -j ACCEPT
done
netfilter-persistent save

# ---------- secrets ----------
mkdir -p /etc/sspp
if [ ! -f "$ENV_FILE" ]; then
  log "Generating secrets in $ENV_FILE"
  ORA_SYS_PW="Sys$(openssl rand -hex 12)"
  cat > "$ENV_FILE" <<EOF
# SSPP runtime settings (read by systemd). Edit, then: sudo systemctl restart sspp
ORACLE_SYS_PASSWORD=$ORA_SYS_PW
DB_USER=SSPP
DB_PASSWORD=Sspp$(openssl rand -hex 12)
DB_HOST=127.0.0.1
DB_PORT=1521
DB_SERVICE=FREEPDB1
SECRET_KEY=$(openssl rand -hex 32)
HOST=127.0.0.1
PORT=5000
BEHIND_PROXY=1
SESSION_COOKIE_SECURE=0
SHOW_DEMO_LOGINS=$KEEP_DEMO_LOGINS
CODE_EXEC_ENABLED=0
AI_MODE=auto
TZ=$TIMEZONE
# ANTHROPIC_API_KEY=
# SMTP_HOST=  SMTP_PORT=587  SMTP_USER=  SMTP_PASSWORD=  SMTP_SENDER=
EOF
  chmod 600 "$ENV_FILE"
  FIRST_INSTALL=1
else
  FIRST_INSTALL=0
fi
set -a; . "$ENV_FILE"; set +a

# ---------- Oracle Database 23ai Free ----------
if ! docker ps -a --format '{{.Names}}' | grep -qx "$DB_CONTAINER"; then
  log "Starting Oracle Database 23ai Free container (first start takes a few minutes)"
  docker volume create sspp-oradata >/dev/null
  docker run -d --name "$DB_CONTAINER" --restart unless-stopped \
    -p 127.0.0.1:1521:1521 \
    -e ORACLE_PASSWORD="$ORACLE_SYS_PASSWORD" \
    -e APP_USER="$DB_USER" -e APP_USER_PASSWORD="$DB_PASSWORD" \
    -e TZ="$TIMEZONE" \
    -v sspp-oradata:/opt/oracle/oradata \
    "$DB_IMAGE"
else
  docker start "$DB_CONTAINER" >/dev/null || true
fi
log "Waiting for the database to be ready"
for i in $(seq 1 120); do
  if docker logs "$DB_CONTAINER" 2>&1 | grep -q "DATABASE IS READY TO USE"; then echo "Database ready"; break; fi
  [ "$i" -eq 120 ] && { echo "Database did not become ready; check: docker logs $DB_CONTAINER"; exit 1; }
  sleep 5
done

# ---------- application ----------
log "Installing application into $APP_DIR"
id sspp >/dev/null 2>&1 || useradd --system --create-home --home-dir /var/lib/sspp --shell /usr/sbin/nologin sspp
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" pull --ff-only
else
  git clone "$REPO_URL" "$APP_DIR"
fi
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --upgrade pip -q
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt" -q
mkdir -p "$APP_DIR/uploads" "$APP_DIR/logs"
chown -R sspp:sspp "$APP_DIR"

log "Creating tables and demo data"
sudo -u sspp --preserve-env "$APP_DIR/.venv/bin/python" "$APP_DIR/db/init_db.py"
if [ "$FIRST_INSTALL" = 1 ] && [ -n "${ADMIN_PASSWORD:-}" ]; then
  LOCK=""; [ "$KEEP_DEMO_LOGINS" = 1 ] || LOCK="--lock-demo"
  sudo -u sspp --preserve-env "$APP_DIR/.venv/bin/python" "$APP_DIR/db/secure_accounts.py" \
    --admin-password "$ADMIN_PASSWORD" $LOCK
fi

# ---------- systemd service ----------
log "Installing systemd service"
cat > /etc/systemd/system/sspp.service <<EOF
[Unit]
Description=Smart Student Placement Portal
After=network-online.target docker.service
Wants=network-online.target

[Service]
User=sspp
Group=sspp
WorkingDirectory=$APP_DIR
EnvironmentFile=$ENV_FILE
ExecStartPre=/bin/bash -c 'for i in \$(seq 1 60); do (echo > /dev/tcp/127.0.0.1/1521) 2>/dev/null && exit 0; sleep 5; done; exit 1'
ExecStart=$APP_DIR/.venv/bin/python run.py
Restart=always
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable sspp
systemctl restart sspp

# ---------- nginx ----------
log "Configuring nginx"
SERVER_NAME="${DOMAIN:-_}"
cat > /etc/nginx/sites-available/sspp <<EOF
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name $SERVER_NAME;
    client_max_body_size 10m;

    location /static/ {
        alias $APP_DIR/static/;
        expires 7d;
    }
    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 120s;
    }
}
EOF
ln -sf /etc/nginx/sites-available/sspp /etc/nginx/sites-enabled/sspp
rm -f /etc/nginx/sites-enabled/default
chmod o+x /opt "$APP_DIR"
nginx -t && systemctl reload nginx

# ---------- HTTPS ----------
if [ -n "${DOMAIN:-}" ]; then
  log "Requesting HTTPS certificate for $DOMAIN"
  apt-get install -y certbot python3-certbot-nginx
  if certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "$CERT_EMAIL" --redirect; then
    sed -i 's/^SESSION_COOKIE_SECURE=.*/SESSION_COOKIE_SECURE=1/' "$ENV_FILE"
    systemctl restart sspp
  else
    echo "Certificate request failed (is $DOMAIN pointing at this server?). Site stays on HTTP; re-run later."
  fi
fi

# ---------- done ----------
sleep 3
PUBLIC_IP=$(curl -fsS https://ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')
if curl -fsS -o /dev/null http://127.0.0.1/; then STATUS="running"; else STATUS="NOT responding - see: journalctl -u sspp -n 50"; fi
log "SSPP is $STATUS"
echo "  URL:   ${DOMAIN:+https://$DOMAIN}${DOMAIN:-http://$PUBLIC_IP}"
echo "  Admin: admin@sspp.edu (the password you entered)"
echo "  Settings: $ENV_FILE   Logs: journalctl -u sspp -f   Update: sudo bash $APP_DIR/deploy/update.sh"
