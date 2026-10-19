# Security

## Reporting a vulnerability

Please **don't** open a public issue. Report it privately through
[GitHub security advisories](https://github.com/Coder-ux-coder/magpie/security/advisories/new)
with steps to reproduce and the version affected. You should hear back within a week.

## How Magpie protects your notes

Magpie is designed to be run by one person on their own server.

- **Login:** the dashboard uses a password (`MAGPIE_PASSWORD`, or a generated one) and a
  signed, HTTP-only session cookie that is marked `Secure` when `MAGPIE_BASE_URL` uses
  HTTPS. Repeated wrong passwords are rate-limited.
- **API:** integrations use a bearer token that you can rotate in Settings.
- **CSRF:** state-changing requests made with the session cookie must send an `X-Magpie`
  header, which other websites can't add.
- **One-tap links** in notifications are signed, limited to one action on one reminder,
  expire after 14 days, and only act on `POST` (opening the link shows a confirmation page).
- **Shared content** (the share target and bookmarklet) is only saved after you press
  Save, so other websites can't add notes to your Magpie.
- **Fetching links:** Magpie refuses to fetch URLs that resolve to private, loopback or
  link-local addresses, including through redirects, unless `MAGPIE_ALLOW_PRIVATE_URLS` is
  set.
- **Web app:** a strict Content-Security-Policy, no inline scripts, and no `innerHTML` with
  note content.
- **Prompt injection:** pages and forwarded messages are treated as untrusted data, and
  Claude's reviews have no tools that can change anything.
- **Container:** runs as an unprivileged user; data lives in one volume.

## Running it safely

- Use a long password, and put Magpie behind HTTPS before exposing it to the internet
  (or keep it private with Tailscale).
- Treat the calendar feed URL and API token like passwords; rotate them in Settings if
  they leak.
- Back up the data folder; it also contains the generated secrets.

## Third parties

Note text and saved pages are sent to the Claude API. Audio goes to your browser's speech
service or your speech-to-text provider. Messages go to the notification channels you set
up. See the Privacy section of the [README](README.md#privacy).
