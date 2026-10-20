# Deployment

Magpie is one process (web app, scheduler and Telegram bot together) and one SQLite file.
It runs happily on a small VPS, a home server or a Raspberry Pi 4/5, using well under
200 MB of memory.

- [Docker Compose (recommended)](#docker-compose)
- [HTTPS](#https)
- [Without Docker](#without-docker)
- [Backups](#backups)
- [Updating](#updating)
- [Troubleshooting](#troubleshooting)

## Docker Compose

```bash
git clone https://github.com/Coder-ux-coder/magpie.git
cd magpie
sh setup.sh          # on Windows: double-click setup.cmd
```

The setup script builds the image, asks for your Claude API key, a password and your time
zone, writes them to `.env`, starts Magpie and opens it. Run it again to change the answers.
New to Docker? See [getting started](getting-started.md).

To do it by hand instead, copy `.env.example` to `.env`, set at least these, and start it:

```dotenv
ANTHROPIC_API_KEY=sk-ant-...      # or paste it later in Settings → Claude
MAGPIE_PASSWORD=a-long-password
MAGPIE_TIMEZONE=Europe/London
```

```bash
docker compose up -d
docker compose logs -f magpie      # watch it start
```

Open <http://localhost:8000> (or `http://<server-ip>:8000`). Notes live in the
`magpie-data` Docker volume, so they survive restarts and upgrades.

Prefer a prebuilt image? Replace `build: .` in `docker-compose.yml` with
`image: ghcr.io/coder-ux-coder/magpie:latest` (published for x86-64 and ARM64 by the
*Publish Docker image* workflow).

Useful commands:

```bash
docker compose exec magpie magpie doctor      # check the configuration
docker compose exec magpie magpie password    # show the generated password, if you didn't set one
docker compose exec magpie magpie backup      # safe database backup into the volume
docker compose exec magpie magpie add "test note from the server"
```

## HTTPS

You need HTTPS to use the microphone on phones (browsers block it on plain HTTP except on
`localhost`), for Siri Shortcuts away from home, and for browser push. Pick one option,
then set `MAGPIE_BASE_URL` to the HTTPS address and restart Magpie so notification
buttons and links point to the right place.

### Option 1: built-in Caddy (you have a domain)

1. Point a DNS record (for example `notes.example.com`) at your server, and open ports 80
   and 443.
2. In `.env`:
   ```dotenv
   MAGPIE_DOMAIN=notes.example.com
   MAGPIE_BASE_URL=https://notes.example.com
   ```
3. `docker compose --profile https up -d`

Caddy fetches and renews the certificate automatically. You can then remove the
`8000:8000` port mapping from `docker-compose.yml` so Magpie is only reachable through Caddy.

### Option 2: Cloudflare Tunnel (no open ports)

With a domain on Cloudflare, install
[cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/)
and route a hostname to `http://localhost:8000`. For a quick test without a domain,
`cloudflared tunnel --url http://localhost:8000` prints a temporary `trycloudflare.com`
address.

### Option 3: Tailscale (private to your devices)

Install [Tailscale](https://tailscale.com) on the server and your phone, then run
`tailscale serve --bg 8000` on the server. Magpie is then at
`https://<machine>.<tailnet>.ts.net`, reachable only from your own devices. That is ideal
for a personal notebook. Telegram reminders keep working because Magpie connects out to
Telegram.

### Option 4: your own reverse proxy

Proxy everything to `http://127.0.0.1:8000`. For nginx:

```nginx
server {
    server_name notes.example.com;
    client_max_body_size 30m;            # audio uploads
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;          # waiting for Claude
    }
    # listen 443 ssl; ... (for example managed by certbot)
}
```

If the proxy runs on another machine or container, set `FORWARDED_ALLOW_IPS` to its
address so Magpie sees real client addresses.

## Without Docker

Python 3.11 or newer:

```bash
python -m venv /opt/magpie/.venv
/opt/magpie/.venv/bin/pip install "magpie-notes @ git+https://github.com/Coder-ux-coder/magpie"
cp .env.example /opt/magpie/.env          # edit it; set MAGPIE_DATA_DIR=/opt/magpie/data
cd /opt/magpie && .venv/bin/magpie serve  # reads .env from the current directory
```

Magpie listens on `127.0.0.1:8000` by default; set `MAGPIE_HOST=0.0.0.0` to accept
connections from other machines.

To run it as a service with systemd, create `/etc/systemd/system/magpie.service`:

```ini
[Unit]
Description=Magpie voice notes
After=network-online.target
Wants=network-online.target

[Service]
User=magpie
WorkingDirectory=/opt/magpie
EnvironmentFile=/opt/magpie/.env
ExecStart=/opt/magpie/.venv/bin/magpie serve
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo useradd --system --home /opt/magpie magpie && sudo chown -R magpie /opt/magpie
sudo systemctl enable --now magpie
```

## Backups

Everything is in the data folder (`/data` in Docker): `magpie.db` holds notes, reminders,
settings and generated secrets.

- `magpie backup [path]` copies the database safely while Magpie runs. In Docker:
  ```bash
  docker compose exec magpie magpie backup
  docker compose cp magpie:/data ./magpie-backup     # copy the folder off the server
  ```
- **Settings → Your data → Export JSON** downloads every note; **Import** restores it into
  any Magpie (duplicates are skipped). **Export Markdown** is easy to read anywhere.

To restore a database backup, stop Magpie, replace `magpie.db` (and delete any
`magpie.db-wal` and `magpie.db-shm` files next to it), then start Magpie again.

## Updating

```bash
cd magpie
git pull
docker compose up -d --build
```

Database changes are applied automatically on start-up. Back up first if you're cautious.

## Troubleshooting

Start with `magpie doctor` (in Docker: `docker compose exec magpie magpie doctor`).

| Problem | Fix |
| --- | --- |
| The mic button is greyed out or nothing happens | Use HTTPS (or `localhost`) and allow the microphone. Firefox has no built-in speech recognition: set `MAGPIE_STT_API_KEY` or use Chrome, Edge or Safari. |
| Hands-free stops listening | Browsers may pause recognition in background tabs and after errors; keep the tab open, or use Telegram / Siri on phones. |
| Notes stay “Waiting for Claude” | No working API key. Paste one in Settings → Claude (or fix `ANTHROPIC_API_KEY` and restart); waiting notes are processed automatically. |
| Magpie won't start | It prints what's wrong: a setting it can't read (it names the variable), a data folder it can't write to (in Docker the volume must be writable by uid 1000), or a damaged database (restore a backup). |
| Reminder times are off by hours | Set your time zone in Settings → Time (or `MAGPIE_TIMEZONE`). |
| No reminders arrive | Settings → Notifications: make sure a channel is set up and switched on, and press **Test**. |
| Telegram bot doesn't answer | Check the token with `magpie doctor`. Only one program can poll a bot: stop other copies of Magpie using the same token. |
| Telegram voice notes fail | Voice needs `MAGPIE_STT_API_KEY`. |
| Done/Snooze buttons in push, ntfy or email don't work | Set `MAGPIE_BASE_URL` to an address your phone can reach. |
| A link review failed | Some sites block automated access. Magpie then asks Claude to fetch the page; if that fails too, open the note and press **Try again** later. Links to private network addresses are never fetched. |
| Forgot the password | Set `MAGPIE_PASSWORD` and restart, or run `magpie password` if it was generated. |
