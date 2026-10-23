# Deployment

Magpie is one process (web app, scheduler and Telegram bot together) and one SQLite file.
On Windows most people use **the Magpie app** ([getting started](getting-started.md#windows)),
which is that same process with listening, windows and a tray icon added. This guide is
for running Magpie as a server: in Docker on a Mac or Linux, or on a small VPS, a home
server or a Raspberry Pi 4/5, using well under 200 MB of memory.

- [The Windows app](#the-windows-app)
- [Docker Compose](#docker-compose)
- [HTTPS](#https)
- [Without Docker](#without-docker)
- [Backups](#backups)
- [Updates](#updates)
- [Troubleshooting](#troubleshooting)

## The Windows app

`MagpieSetup.exe` (from the [latest release](https://github.com/Coder-ux-coder/magpie/releases/latest))
installs Magpie for the current user in `%LOCALAPPDATA%\Programs\Magpie`, with no
administrator rights and no questions; `/VERYSILENT` installs it without showing anything.
Its notes and settings live in `%LOCALAPPDATA%\Magpie`, which updates and uninstalling
leave alone. It runs the server on `127.0.0.1` (port 8000, or a free one if that's taken)
with no password, starts with Windows (the `Magpie` value under
`HKCU\Software\Microsoft\Windows\CurrentVersion\Run`), and updates itself: every six
hours it reads `latest.json` from the latest GitHub release, downloads the new installer,
checks its size and SHA-256, and runs it silently when no note is being taken.

The **Windows app** workflow builds each version on Windows, tests the built app and the
installed one (a real window, and a note taken from a recording of Windows' own voice), and
publishes the release. To build it yourself on Windows: `pip install -e ".[desktop]"
pyinstaller`, download the
[speech model](https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip), set
`MAGPIE_VOSK_MODEL` to its folder, run `pyinstaller packaging/windows/magpie.spec`, then
`iscc /DAppVersion=<version> packaging/windows/Magpie.iss` (Inno Setup 6).

## Docker Compose

```bash
git clone https://github.com/Coder-ux-coder/magpie.git
cd magpie
sh setup.sh          # on Windows: double-click setup-docker.cmd
```

The setup script downloads Magpie (a ready-made image that GitHub builds from this
repository), writes `.env` without asking anything (your computer's time zone, no
password, the port on this computer only), starts Magpie with
[automatic updates](#updates), adds a Magpie icon (Desktop and Start menu on Windows,
Applications on a Mac, the applications menu on Linux) and opens Magpie in its own window,
where a welcome screen sets up the AI, reminders and categories. New to Docker? See
[getting started](getting-started.md).

**A server that other devices reach** (your phone on the Wi-Fi, or the internet behind
[HTTPS](#https)) needs a password: `magpie setup --password 'a long password'` (or
`sh setup.sh --password '…'`) writes it, with `MAGPIE_BIND=0.0.0.0`. To do it by hand
instead, copy `.env.example` to `.env`, set these, and start it:

```dotenv
MAGPIE_PASSWORD=a-long-password
MAGPIE_BIND=0.0.0.0               # let other devices in (only with a password)
MAGPIE_TIMEZONE=Europe/London
# ANTHROPIC_API_KEY=sk-ant-...    # optional: or choose the AI in Magpie's welcome screen
```

```bash
docker compose up -d
docker compose logs -f magpie      # watch it start
```

Open <http://localhost:8000>. Magpie's port listens only on this computer unless
`MAGPIE_BIND=0.0.0.0` is set, which lets in other devices at `http://<server-ip>:8000`;
set a password first (without one, Magpie refuses every address but `localhost` anyway). `MAGPIE_HOST_PORT` moves it off port 8000. Notes live in the
`magpie-data` Docker volume, so they survive restarts and updates.

The image is `ghcr.io/coder-ux-coder/magpie`, published for x86-64 and ARM64 by the
*Publish Docker image* workflow on every change. To build it from this folder instead
(developers):

```bash
docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build
```

Set `MAGPIE_AUTO_UPDATE=false` while you do, or the updates service replaces your build with
the published image.

Useful commands:

```bash
docker compose exec magpie magpie doctor      # check the configuration
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

Caddy fetches and renews the certificate automatically. Leave `MAGPIE_BIND` unset so
Magpie's own port stays private to the server and everyone comes in through Caddy.

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

## Updates

With Docker Compose, Magpie updates itself. The `updates` service (the official Docker
command-line image, running a short script from `docker-compose.yml`) checks for a new
image every hour. When there is one, it downloads it and restarts Magpie on it, which takes
a few seconds; database changes are applied on start-up, and your notes and settings are
kept. If the new version doesn't report itself healthy within five minutes, the updates
service puts the previous version back and skips that version until a newer one comes out.
It leaves Magpie alone while you've stopped it.

- **See what it did:** `docker compose logs updates` (one line per update, nothing
  otherwise).
- **Turn it off:** add `MAGPIE_AUTO_UPDATE=false` to `.env`, then `docker compose up -d`.
- **Stay on one version:** set `MAGPIE_VERSION` to an image tag, such as `sha-0e24363`
  (every build is tagged with its commit), then `docker compose up -d`. Remove it to follow
  the latest version again.
- **Update by hand:** `docker compose pull magpie && docker compose up -d`.
- **New settings or files** (a new `docker-compose.yml`, setup scripts or launchers) come
  with a new download of the folder or `git pull`; then run the setup again. Images never
  change your `.env`.

The updates service can control Docker on this computer (it needs to, to restart Magpie),
so it has access to the Docker socket. If you'd rather not allow that, delete the `updates`
service from `docker-compose.yml` and update by hand. With rootless Docker on Linux, the
setup records your socket's path as `DOCKER_SOCKET` in `.env`.

**Installed before automatic updates existed?** Those copies built Magpie on your computer
and can't update themselves. Update once by hand: download the new ZIP and extract it over
the old folder, choosing to replace the files (or `git pull`), then run the setup again.
Your notes, in the `magpie_magpie-data` volume, and your `.env` are kept (a password the old
version made up is dropped; Magpie then opens on this computer only). On Windows, installing
the app is easier: it brings the notes over by itself. See
[getting started](getting-started.md#installed-magpie-in-docker-before-automatic-updates).

Without Docker, update with `pip install --upgrade` and the same package address you
installed from, then restart Magpie. Back up first if you're cautious.

## Troubleshooting

Start with `magpie doctor` (in Docker: `docker compose exec magpie magpie doctor`).

| Problem | Fix |
| --- | --- |
| The mic button is greyed out or nothing happens | Use HTTPS (or `localhost`) and allow the microphone. Firefox has no built-in speech recognition: set `MAGPIE_STT_API_KEY` or use Chrome, Edge or Safari. |
| Hands-free stops listening | Browsers may pause recognition in background tabs and after errors; keep the tab open, or use Telegram / Siri on phones. |
| Notes stay waiting to be filed | No working AI. Choose one or paste its key in Settings → AI (or fix `ANTHROPIC_API_KEY` / `MAGPIE_AI_API_KEY` and restart); waiting notes are processed automatically. |
| Magpie won't start | It prints what's wrong: a setting it can't read (it names the variable), a data folder it can't write to (in Docker the volume must be writable by uid 1000), or a damaged database (restore a backup). |
| Reminder times are off by hours | Set your time zone in Settings → Time (or `MAGPIE_TIMEZONE`). |
| No reminders arrive | Settings → Reminders: make sure pop-ups or email (or another channel) are set up and switched on, and press **Test**. |
| Telegram bot doesn't answer | Check the token with `magpie doctor`. Only one program can poll a bot: stop other copies of Magpie using the same token. |
| Telegram voice notes fail | Voice needs `MAGPIE_STT_API_KEY`. |
| Done/Snooze buttons in push, ntfy or email don't work | Set `MAGPIE_BASE_URL` to an address your phone can reach. |
| A link review failed | Some sites block automated access. Magpie then asks Claude to fetch the page; if that fails too, open the note and press **Try again** later. Links to private network addresses are never fetched. |
| Forgot the password (a server) | Set a new `MAGPIE_PASSWORD` and restart; `magpie password` shows one Magpie made up. |
| Phones on the Wi-Fi can't open Magpie | It only listens on the server itself unless `.env` has `MAGPIE_BIND=0.0.0.0` and a password (`magpie setup --password '…'`). |
| Port 8000 is already in use | Set `MAGPIE_HOST_PORT=8080` in `.env` and run `docker compose up -d` (or the setup). |
| Magpie didn't update | `docker compose logs updates` says why: no internet, a version that didn't start, or updates turned off. Copies installed before automatic updates need [one update by hand](#updates). |
