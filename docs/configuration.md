# Configuration

Magpie has two kinds of settings:

- **Server settings** are environment variables (usually in `.env`). They hold secrets and
  infrastructure and are read at start-up, so restart Magpie after changing them.
- **Preferences** are edited in the dashboard (Settings) and stored in the database.
  Some have a server-setting default, such as the time zone.

Empty values are treated as unset. [`.env.example`](../.env.example) lists everything with
comments. You rarely need to edit `.env` by hand: the Docker setup (`sh setup.sh`,
`setup-docker.cmd` or `magpie setup`) writes it for you without asking anything. The AI,
email, pop-ups and your categories are all set up in Magpie itself: the welcome screen walks
a new user through them, and Settings has them all.

**The Windows app** needs none of this. It keeps its notes in `%LOCALAPPDATA%\Magpie\data`,
its own small settings in `desktop.json` there (start with Windows, listening, the port),
and reads the same variables from `%LOCALAPPDATA%\Magpie\magpie.env` if that file exists
(the settings it brought over from Docker are written there). It always listens on
`127.0.0.1` with no password, whatever the file says. The tray menu turns listening and
starting with Windows on and off; the command word, stop word and silence timeout are the
dashboard's voice settings. Two variables are for the app itself: `MAGPIE_DESKTOP_HOME`
moves its folder, and `MAGPIE_VOSK_MODEL` points at another
[Vosk](https://alphacephei.com/vosk/models) speech model folder.

Installed copies update themselves (see [Updates](#updates-docker)), so every new setting
comes with a safe default and an existing `.env` keeps working unchanged.

A mistyped value never stops Magpie quietly: an unknown time zone falls back to UTC with a
warning in the log, and a value that can't be read at all (for example `MAGPIE_PORT=eighty`)
makes `magpie` exit with a message that names the variable.

## Server settings

### Basics

| Variable | Default | Meaning |
| --- | --- | --- |
| `MAGPIE_PASSWORD` | | Only for a server that other devices reach: the dashboard then asks for this password. Without it (the default) there's no password, and Magpie only answers on its own computer (`localhost`); the Docker setup publishes the port on `127.0.0.1` only. |
| `MAGPIE_LOGIN` | | Leave it unset. `true` makes up a password (shown by `magpie password`) when `MAGPIE_PASSWORD` isn't set; `false` turns a password off. A password made up by a version before 0.4 goes away by itself, except where other devices can reach Magpie (`MAGPIE_BIND` other than `127.0.0.1`, `MAGPIE_DOMAIN`, or a `MAGPIE_BASE_URL` that isn't `localhost`): there it stays until the setup runs again, which sets `false`. |
| `MAGPIE_TIMEZONE` | `UTC` | Default time zone (IANA name) for new installs. The dashboard follows your computer's time zone after that (Settings → Time). |
| `MAGPIE_BASE_URL` | | Public address, e.g. `https://notes.example.com`. Enables links and one-tap buttons in notifications, and secure cookies when it starts with `https://`. |
| `MAGPIE_DATA_DIR` | `data` (`/data` in Docker) | Where the database lives. |
| `MAGPIE_HOST` | `127.0.0.1` (`0.0.0.0` in Docker) | Address to listen on. |
| `MAGPIE_PORT` | `8000` | Port to listen on. |
| `MAGPIE_LOG_LEVEL` | `info` | `debug`, `info`, `warning` or `error`. |

### AI

Usually chosen in **Settings → AI** (or on the welcome screen): Claude, ChatGPT, Codex
(signed in on this computer, no key), Gemini, OpenRouter, Ollama on your own computer, any
OpenAI-compatible service, or no AI at all.
Keys pasted there are checked first, kept on your server and never shown again. Set these
only to fix the choice on the server.

| Variable | Default | Meaning |
| --- | --- | --- |
| `MAGPIE_BRAIN` | chosen in Magpie | Fixes the AI: `claude`, `openai`, `codex` (Codex installed and signed in where Magpie runs; no key), `gemini`, `openrouter`, `ollama`, `compatible` (any OpenAI-compatible service), `basic` (no AI: simple rules file notes, and reviews are off) or `fake` (an offline stand-in for demos and tests). When set, Settings → AI shows it but can't change it. |
| `ANTHROPIC_API_KEY` | | Claude API key. A key set here wins over one pasted in Magpie. Without any key, notes are saved and filed once one is added. |
| `MAGPIE_AI_API_KEY` | | API key for the service `MAGPIE_BRAIN` names, when it isn't Claude. |
| `MAGPIE_AI_BASE_URL` | the service's | Its OpenAI-compatible address, ending in `/v1`. Needed for `compatible`; Ollama defaults to `http://localhost:11434/v1` (`http://host.docker.internal:11434/v1` in Docker). |
| `MAGPIE_MODEL` | `claude-haiku-5-5` for Claude | The model. Haiku is fast and low-cost; `claude-sonnet-5-5` and `claude-opus-5-5` are more capable. Other services start with their own default (for example `gpt-5-mini`; Codex uses its own). |
| `MAGPIE_CODEX_PATH` | `codex` on the PATH | Where the Codex program is, when it isn't on the PATH. |

The rest apply to Claude only:

| Variable | Default | Meaning |
| --- | --- | --- |
| `MAGPIE_EFFORT` | per task | Force one effort level (`low` to `max`) for all calls. By default filing and digests use `low`; reviews, Ask and weekly reviews use `medium`. |
| `MAGPIE_WEB_SEARCH` | `true` | Allow Claude's web search to find things you mention without a link. Can also be switched off in Settings. |
| `MAGPIE_FALLBACKS` | `true` | If Claude declines a request, retry it on Anthropic's recommended fallback model (models that have one). |
| `ANTHROPIC_BASE_URL` | | Read by the Anthropic SDK, for gateways and proxies. |

### Speech-to-text on the server

Used for Telegram voice messages, browsers without speech recognition, and the
*Use the server's speech-to-text* option.

| Variable | Default | Meaning |
| --- | --- | --- |
| `MAGPIE_STT_API_KEY` | | Turns server transcription on. |
| `MAGPIE_STT_BASE_URL` | `https://api.openai.com/v1` | Any OpenAI-compatible API (OpenAI, Groq, a local Whisper server). |
| `MAGPIE_STT_MODEL` | `whisper-1` | Model name for that service. |
| `MAGPIE_STT_LANGUAGE` | `en` | Language of the recordings (ISO 639-1). Magpie expects English. |

### Notifications

Pop-ups and email need no server settings: turn pop-ups on in **Settings → Reminders** on
each computer or phone, and set up email there with your address and an app password
(Gmail, Outlook, Yahoo and iCloud are filled in for you). The `MAGPIE_SMTP_*` settings below
are for other mail servers, and win over the dashboard when set.

| Variable | Default | Meaning |
| --- | --- | --- |
| `MAGPIE_TELEGRAM_BOT_TOKEN` | | Bot token from @BotFather. |
| `MAGPIE_TELEGRAM_CHAT_ID` | | Optional: your chat id, instead of pairing from the dashboard. |
| `MAGPIE_NTFY_TOPIC` | | ntfy topic; use a long, unguessable name. |
| `MAGPIE_NTFY_SERVER` | `https://ntfy.sh` | Your own ntfy server, if you run one. |
| `MAGPIE_NTFY_TOKEN` | | Access token for protected topics. |
| `MAGPIE_SMTP_HOST` | | Mail server. |
| `MAGPIE_SMTP_PORT` | `587` | |
| `MAGPIE_SMTP_SECURITY` | `starttls` | `starttls`, `ssl` or `none`. |
| `MAGPIE_SMTP_USERNAME` / `MAGPIE_SMTP_PASSWORD` | | Login. |
| `MAGPIE_SMTP_FROM` | the username | Sender address. |
| `MAGPIE_EMAIL_TO` | | Where to send reminders and digests. |
| `MAGPIE_WEBHOOK_URL` | | Slack, Discord or any JSON endpoint. |
| `MAGPIE_WEBHOOK_FORMAT` | `json` | `json`, `slack` or `discord`. |
| `MAGPIE_VAPID_SUBJECT` | `mailto:magpie@example.com` | Contact sent to browser push services. |

### Security

| Variable | Default | Meaning |
| --- | --- | --- |
| `MAGPIE_API_TOKEN` | generated | Bearer token for the API, Shortcuts and MCP. When generated, rotate it in Settings → Integrations. |
| `MAGPIE_SECRET_KEY` | generated | Signs session cookies and one-tap action links. Changing it logs everyone out and invalidates old links. |
| `MAGPIE_ALLOW_PRIVATE_URLS` | `false` | Allow fetching links on private networks (only if you trust everything you save). |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | Proxies whose `X-Forwarded-*` headers are trusted (read by Uvicorn). |

Generated secrets are stored in the database, so they survive restarts.

### Updates (Docker)

Read by `docker-compose.yml`, not by Magpie itself. A small helper container checks for a
new image every hour, restarts Magpie on it, and goes back to the previous version if the
new one doesn't start. Your notes live in the `magpie-data` volume and are never touched.
Open dashboards notice the update and load the new version.

| Variable | Default | Meaning |
| --- | --- | --- |
| `MAGPIE_AUTO_UPDATE` | `true` | `false` stops automatic updates (update with `docker compose pull && docker compose up -d`). |
| `MAGPIE_VERSION` | `latest` | Stay on one version: every build is also tagged `sha-<commit>`, and releases `0.3.0`, `0.3`. |
| `MAGPIE_IMAGE` | `ghcr.io/coder-ux-coder/magpie` | Where the image comes from. |
| `MAGPIE_UPDATE_CHECK_EVERY` | `3600` | Seconds between checks. |
| `MAGPIE_UPDATE_FIRST_CHECK_AFTER` | `300` | Seconds after start-up before the first check. |
| `MAGPIE_BIND` | `127.0.0.1` | Who can open Magpie: `127.0.0.1` is this computer only; `0.0.0.0` lets other devices on your network in (only with `MAGPIE_PASSWORD` set). |
| `MAGPIE_HOST_PORT` | `8000` | The port on this computer, if 8000 is taken. |
| `DOCKER_SOCKET` | `/var/run/docker.sock` | Docker's socket, for the update helper (Podman and rootless Docker use another path). |

## Preferences (dashboard)

| Setting | Default | Meaning |
| --- | --- | --- |
| Welcome screen | shown once | Choose an AI, reminders, your command word and categories. Opens again from Settings → Privacy & account. |
| AI | Claude Haiku 5.5 | Which AI files your notes, its model, and its key or address. *No AI* files notes with simple rules. |
| Email | off | Your address and an app password; Magpie checks the login before saving and sends a test. Every reminder arrives as an email such as “⏰ Call James · due today 10:00 AM”. |
| Pop-ups | on with the app | The Windows app shows every reminder in the corner of the screen, with Done, Snooze and Open. In a browser (Docker): per computer or phone, a notification even when Magpie's window is in the background. |
| Categories | none | Your own, each with a colour, in the order you choose (drag, or the arrows). The AI files notes into them; a note written in a category's tab stays there; drag a note onto a tab to move it. |
| Learn from my changes | on | When you move a note to another category or change its kind, Magpie remembers and files similar notes your way (the last 40 changes; you can forget any of them). |
| Proactivity | Balanced | *Quiet*: only reminders you ask for. *Balanced*: also before clear deadlines and appointments. *Proactive*: suggests reminders for anything actionable. |
| About you | | Free text the AI reads with every note: working hours, names, family. Up to 2,000 characters. |
| Your instructions | | Standing instructions the AI follows when filing notes, writing digests and answering questions: “tag shop notes #shop”, “remind me 30 minutes before meetings”, “use British spelling”. Up to 2,000 characters. |
| Web search | on | Find videos and articles mentioned without a link. |
| Time zone | `MAGPIE_TIMEZONE`, else your browser's | Used for every reminder and digest. On a fresh install the dashboard picks up your device's time zone the first time you open it. |
| Your morning | 09:00 | What “tomorrow morning” and the Tomorrow button mean. |
| Snooze for | 1 hour | Length of every Snooze button: dashboard, Telegram, push, ntfy and email (5 minutes to 1 day). Snoozing a reminder that isn't due yet moves it later, never earlier. |
| Your accent | Automatic | Which English the browser listens for: UK, US, Australia, Canada, Ireland, New Zealand or South Africa. *Automatic* uses the browser's English, or UK English if the browser is set to another language. Read-aloud uses the same accent. |
| Writing down what you say (app) | Accurate | How the app writes each voice note down: *Accurate* (Whisper small, a 480 MB download), *Most accurate* (Whisper large turbo, 1.6 GB, slower; best with strong accents) or *Quick* (the words shown as you speak). Whisper runs on the computer; no audio leaves it. `MAGPIE_WHISPER_MODEL` can point the app at a model folder of its own. |
| Wake / stop phrase | “notes on” / “notes off” | Any phrase you like. |
| Silence timeout | 8 s | Save a voice note after this much silence (0 = only when you say the stop phrase). |
| Channels | all on | Switch each notification channel on or off. |
| Daily digest | 08:00 | Empty turns it off. |
| Weekly review | Sunday 18:00 | Day *Off* turns it off. |

Some settings belong to each device and are saved in its browser:

| Setting | Default | Meaning |
| --- | --- | --- |
| Start listening when Magpie opens | on | Listen for the command word as soon as Magpie opens, once you've allowed the microphone (the voice bar asks once). |
| Listen for the command word | on when allowed | Hands-free: say the command word to start a note. |
| Let me check voice notes before saving | off | A voice note waits in the voice bar until you press Save. |
| Follow this computer's time zone | on | A computer (not a phone) switches Magpie's time zone to its own when that changes, so reminders keep matching your clock. |
| Theme, text size, contrast, motion, read-aloud | system | Display options. |
