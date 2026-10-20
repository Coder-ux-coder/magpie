# Configuration

Magpie has two kinds of settings:

- **Server settings** are environment variables (usually in `.env`). They hold secrets and
  infrastructure and are read at start-up, so restart Magpie after changing them.
- **Preferences** are edited in the dashboard (Settings) and stored in the database.
  Some have a server-setting default, such as the time zone.

Empty values are treated as unset. [`.env.example`](../.env.example) lists everything with
comments. You rarely need to edit `.env` by hand: the setup (`setup.cmd` on Windows,
`sh setup.sh` on Mac and Linux, or `magpie setup`) asks for the few settings that matter and
writes the file for you, and the Claude API key can also be pasted in the dashboard.

A mistyped value never stops Magpie quietly: an unknown time zone falls back to UTC with a
warning in the log, and a value that can't be read at all (for example `MAGPIE_PORT=eighty`)
makes `magpie` exit with a message that names the variable.

## Server settings

### Basics

| Variable | Default | Meaning |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | | Claude API key. You can paste it in **Settings → Claude** instead; a key set here always wins. Without one, notes are saved and filed once a key is added. |
| `MAGPIE_PASSWORD` | generated | Dashboard password. If unset, one is generated, logged at start-up and shown by `magpie password`. |
| `MAGPIE_TIMEZONE` | `UTC` | Default time zone (IANA name) for new installs. Change it later in Settings. |
| `MAGPIE_BASE_URL` | | Public address, e.g. `https://notes.example.com`. Enables links and one-tap buttons in notifications, and secure cookies when it starts with `https://`. |
| `MAGPIE_DATA_DIR` | `data` (`/data` in Docker) | Where the database lives. |
| `MAGPIE_HOST` | `127.0.0.1` (`0.0.0.0` in Docker) | Address to listen on. |
| `MAGPIE_PORT` | `8000` | Port to listen on. |
| `MAGPIE_LOG_LEVEL` | `info` | `debug`, `info`, `warning` or `error`. |

### Claude

| Variable | Default | Meaning |
| --- | --- | --- |
| `MAGPIE_MODEL` | `claude-opus-5-5` | Claude model for every task. `claude-sonnet-5-5` and `claude-haiku-5-5` cost less. |
| `MAGPIE_EFFORT` | per task | Force one effort level (`low` to `max`) for all calls. By default filing and digests use `low`; reviews, Ask and weekly reviews use `medium`. |
| `MAGPIE_WEB_SEARCH` | `true` | Allow Claude's web search to find things you mention without a link. Can also be switched off per user in Settings. |
| `MAGPIE_FALLBACKS` | `true` | If Claude declines a request, retry it on Anthropic's recommended fallback model. |
| `MAGPIE_BRAIN` | `claude` | `fake` swaps Claude for a simple offline stand-in (demos, development, tests). |
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

## Preferences (dashboard)

| Setting | Default | Meaning |
| --- | --- | --- |
| Claude API key | | Paste it here instead of setting `ANTHROPIC_API_KEY`. Magpie checks it with Anthropic, keeps it on your server and never shows it again. |
| Proactivity | Balanced | *Quiet*: only reminders you ask for. *Balanced*: also before clear deadlines and appointments. *Proactive*: suggests reminders for anything actionable. |
| About you | | Free text Claude reads with every note: working hours, names, family. Up to 2,000 characters. |
| Your rules for Claude | | Standing instructions Claude follows when filing notes, writing digests and answering questions: “tag shop notes #shop”, “remind me 30 minutes before meetings”, “use British spelling”. Up to 2,000 characters. |
| Web search | on | Find videos and articles mentioned without a link. |
| Time zone | `MAGPIE_TIMEZONE`, else your browser's | Used for every reminder and digest. On a fresh install the dashboard picks up your device's time zone the first time you open it. |
| Your morning | 09:00 | What “tomorrow morning” and the Tomorrow button mean. |
| Snooze for | 1 hour | Length of every Snooze button: dashboard, Telegram, push, ntfy and email (5 minutes to 1 day). Snoozing a reminder that isn't due yet moves it later, never earlier. |
| Your accent | Automatic | Which English the browser listens for: UK, US, Australia, Canada, Ireland, New Zealand or South Africa. *Automatic* uses the browser's English, or UK English if the browser is set to another language. Read-aloud uses the same accent. |
| Wake / stop phrase | “notes on” / “notes off” | Any phrase you like. |
| Silence timeout | 8 s | Save a voice note after this much silence (0 = only when you say the stop phrase). |
| Channels | all on | Switch each notification channel on or off. |
| Daily digest | 08:00 | Empty turns it off. |
| Weekly review | Sunday 18:00 | Day *Off* turns it off. |

Display options (theme, text size, contrast, motion, read-aloud, checking voice notes
before saving) are saved per device in the browser.
