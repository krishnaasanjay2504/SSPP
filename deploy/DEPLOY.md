# Hosting SSPP free on Oracle Cloud (Always Free)

You get an always-on Ubuntu VM that runs both the app and Oracle Database 23ai Free, at no cost.
Allow about 30 minutes. The setup script does all the server work.

## 1. Create the Oracle Cloud account
1. Sign up at https://signup.cloud.oracle.com. A card is required for identity verification only; Always Free resources are never charged.
2. Pick a **home region** close to your users (e.g. India South – Hyderabad or India West – Mumbai). You cannot change it later.

## 2. Create the VM
Console → **Compute → Instances → Create instance**

| Setting | Value |
|---|---|
| Name | `sspp` |
| Image | **Canonical Ubuntu 24.04** |
| Shape | **Ampere → VM.Standard.A1.Flex**, 2 OCPU, 12 GB memory (Always Free covers up to 4 OCPU / 24 GB) |
| Networking | Create new VCN + public subnet, **Assign a public IPv4 address** = yes |
| SSH keys | **Generate a key pair for me** → download the private key (`.key` file) |
| Boot volume | default (47 GB) is enough |

> If you see "Out of capacity" for A1, try another availability domain, or retry later. The small AMD
> `VM.Standard.E2.1.Micro` (1 GB RAM) is too small for Oracle Database.

Click **Create** and note the **Public IP address**.

## 3. Open web ports in the cloud firewall
Instance page → **Subnet** link → **Security Lists** → *Default Security List* → **Add Ingress Rules**, twice:

| Source CIDR | IP protocol | Destination port |
|---|---|---|
| `0.0.0.0/0` | TCP | `80` |
| `0.0.0.0/0` | TCP | `443` |

(The setup script opens the same ports in the VM's own firewall.)

## 4. Run the installer
From Windows PowerShell on your PC:

```powershell
ssh -i C:\path\to\ssh-key.key ubuntu@<PUBLIC_IP>
```

On the VM:

```bash
curl -fsSL https://raw.githubusercontent.com/krishnaasanjay2504/SSPP/main/deploy/setup.sh -o setup.sh
sudo bash setup.sh
```

It asks for:
- **admin password**: the new password for `admin@sspp.edu`. All other demo accounts get random passwords, so the passwords published in the README stop working. To keep them working (a demo site), run `sudo KEEP_DEMO_LOGINS=1 bash setup.sh` instead.
- **domain**: leave blank for now (HTTP on the IP address), or see step 6.

When it finishes it prints the URL: open `http://<PUBLIC_IP>`.

> If the repository is **private**, the VM cannot download it anonymously. Either make the repo public, or run
> `sudo REPO_URL=https://<github-token>@github.com/krishnaasanjay2504/SSPP.git bash setup.sh` with a
> read-only fine-grained personal access token.

## 5. Day-to-day

| Task | Command (on the VM) |
|---|---|
| Deploy new code pushed to GitHub | `sudo bash /opt/sspp/deploy/update.sh` |
| App logs | `journalctl -u sspp -f` |
| Restart app | `sudo systemctl restart sspp` |
| Change settings (SMTP, AI key, …) | `sudo nano /etc/sspp/sspp.env` then restart |
| Database logs | `sudo docker logs sspp-oracle` |
| Back up the database | `sudo docker exec sspp-oracle bash -c 'expdp system/$ORACLE_PASSWORD@FREEPDB1 schemas=SSPP directory=DATA_PUMP_DIR dumpfile=sspp.dmp'` |

Users whose demo passwords were locked can be given new ones by the admin under **Admin → Users → Reset password**.

## 6. Free domain + HTTPS (recommended)
1. Get a free subdomain at https://www.duckdns.org (sign in, create e.g. `mycollege-sspp`, set it to the VM's public IP).
2. On the VM: `sudo DOMAIN=mycollege-sspp.duckdns.org CERT_EMAIL=you@example.com bash /opt/sspp/deploy/setup.sh`

Certbot then installs a Let's Encrypt certificate, redirects HTTP to HTTPS and renews automatically.

## Security defaults on the server
- Runs as an unprivileged `sspp` user; the database listens on localhost only, as a dedicated `SSPP` schema (not SYSTEM).
- Random `SECRET_KEY` and database passwords are generated into `/etc/sspp/sspp.env` (mode 600).
- **Coding practice "Run/Submit" is disabled** (`CODE_EXEC_ENABLED=0`) because it executes user code on the server. Enable it only if you trust your users.
- Demo login hints are hidden from the login page (`SHOW_DEMO_LOGINS=0`).
- Oracle may reclaim Always Free VMs that stay almost completely idle for 7 days; normal site use prevents this. Upgrading the account to Pay-As-You-Go (still free for Always Free resources) removes that rule.
