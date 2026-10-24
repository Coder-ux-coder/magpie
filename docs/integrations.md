# Integrations

Ways to get notes into Magpie and reminders out of it, beyond the app itself. Magpie only
answers on its own computer, so everything here works from that computer, or through a
service Magpie reaches out to (email, Telegram, ntfy, webhooks).

- [Pop-ups and email](#pop-ups-and-email)
- [Telegram](#telegram)
- [Claude Desktop and Claude Code (MCP)](#claude-desktop-and-claude-code-mcp)
- [Notification channels](#notification-channels)
- [HTTP API](#http-api)

Settings that go in `magpie.env` (Magpie's folder) are explained in
[settings](configuration.md#in-magpieenv); quit Magpie and open it again after changing
that file.

## Pop-ups and email

The two simplest ways to get reminders, both in **Settings → Reminders** (or on the welcome
screen), with nothing to install:

- **Pop-ups:** every reminder pops up in the corner of the screen with Done, Snooze and Open,
  whether Magpie's window is open or not. *Show a test pop-up* checks it.
- **Email**, for your phone: enter your address and an *app password* (Gmail, Outlook, Yahoo
  and iCloud make one in their security settings; the form links to each). Magpie fills in
  the mail server, checks the login before saving, and sends a test. Each reminder arrives
  as *“⏰ Call James about the budget · due today 5:00 PM”*, so it shows up on your phone
  like any other email. Another mail server? Open *Mail server settings* in the form, or set
  `MAGPIE_SMTP_*` in `magpie.env`.

## Telegram

Telegram is the most convenient way to use Magpie from a phone: you send notes by text or
voice, and reminders arrive as messages with Done / +1 hour / Tomorrow buttons. Magpie
fetches your messages from Telegram, so nothing has to reach your computer.

1. In Telegram, talk to [@BotFather](https://t.me/BotFather), send `/newbot` and follow the
   prompts. Copy the token it gives you.
2. Add `MAGPIE_TELEGRAM_BOT_TOKEN=<token>` to `magpie.env`, then quit Magpie and open it
   again.
3. In Magpie, open **Settings → Reminders → Connect Telegram** and tap **Open @yourbot** (or
   send the bot the `/start 123456` message shown). The code works for 15 minutes and only
   once.

Your bot then only answers you; anyone else who finds it gets a polite refusal.

**Voice messages** are written down by a speech service of your own: choose one, and paste
its key, in **Settings → Voice → Writing down what you say → A speech service of your own**
(OpenAI, Groq, Deepgram, ElevenLabs, AssemblyAI, Gladia, or any server with the OpenAI
`/audio/transcriptions` endpoint). Or set one in `magpie.env`, which is used when none is
chosen there:

```dotenv
MAGPIE_STT_API_KEY=gsk_...                    # Groq: fast, with a free tier
MAGPIE_STT_BASE_URL=https://api.groq.com/openai/v1
MAGPIE_STT_MODEL=whisper-large-v3
```

**Commands:** `/today`, `/upcoming`, `/search budget`, `/ask what did I decide about rent?`,
`/digest`, `/week`, `/help`. Forwarded messages are saved with who sent them, and links are
reviewed and the review is sent back as a reply. Telegram works while Magpie is running.

## Claude Desktop and Claude Code (MCP)

Magpie includes a [Model Context Protocol](https://modelcontextprotocol.io) server, so
Claude can search your notes, add notes and reminders, and check what's coming up. It runs
on your computer and talks to Magpie through its API.

It needs [uv](https://docs.astral.sh/uv/) (or install `magpie-notes[mcp]` with pip and run
`magpie mcp`), and Magpie's API token from **Settings → Integrations**.

**Claude Desktop:** add this to `claude_desktop_config.json` (Settings → Developer → Edit
Config) and restart Claude Desktop:

```json
{
  "mcpServers": {
    "magpie": {
      "command": "uvx",
      "args": ["--from", "magpie-notes[mcp] @ git+https://github.com/Coder-ux-coder/magpie", "magpie", "mcp"],
      "env": {
        "MAGPIE_URL": "http://127.0.0.1:8000",
        "MAGPIE_API_TOKEN": "mgp_your_token"
      }
    }
  }
}
```

**Claude Code:**

```bash
claude mcp add magpie \
  -e MAGPIE_URL=http://127.0.0.1:8000 \
  -e MAGPIE_API_TOKEN=mgp_your_token \
  -- uvx --from "magpie-notes[mcp] @ git+https://github.com/Coder-ux-coder/magpie" magpie mcp
```

Tools: `add_note`, `search_notes`, `recent_notes`, `get_note`, `upcoming_reminders`,
`complete_reminder`, `snooze_reminder`. Try asking Claude *“What's on my Magpie list for
this week?”* or *“Add a note in Magpie to renew my passport next month.”* (Magpie's address
is in `running.json` in its folder, if 8000 was taken.)

## Notification channels

Reminders and digests go to every channel that is set up and switched on in Settings →
Reminders. Use **Test** next to each one to check it.

| Channel | Set up | Buttons |
| --- | --- | --- |
| Pop-ups | On by default | Done, Snooze, Open |
| Email | Settings → Reminders → Email (or `MAGPIE_SMTP_*` and `MAGPIE_EMAIL_TO`) | — |
| Telegram | `MAGPIE_TELEGRAM_BOT_TOKEN`, then connect in Settings | Done, +1 hour, Tomorrow |
| [ntfy](https://ntfy.sh) | `MAGPIE_NTFY_TOPIC` (a long, secret name), subscribe in the ntfy app | — |
| Webhook | `MAGPIE_WEBHOOK_URL`, `MAGPIE_WEBHOOK_FORMAT` (`json`, `slack`, `discord`) | — |

The `json` webhook format posts:

```json
{
  "event": "reminder",
  "title": "Call James about the budget",
  "body": "Budget call\nDue today 5:00 PM.",
  "text": "Call James about the budget\n\nBudget call\nDue today 5:00 PM.",
  "url": null,
  "note_id": 12,
  "reminder_id": 34,
  "due": "today 5:00 PM",
  "actions": {}
}
```

`event` is `reminder`, `digest` or `test`; `due` is set for reminders only. `url` and
`actions` (one-tap Done and Snooze links) stay empty, because Magpie isn't reachable from
other devices.

## HTTP API

Interactive documentation is at `/api/docs` (for example
`http://127.0.0.1:8000/api/docs`). From the same computer no token is needed, but `POST`,
`PUT` and `DELETE` need the header `X-Magpie: 1`; from scripts you can also use
`Authorization: Bearer <API token>` (Settings → Integrations).

```bash
URL=http://127.0.0.1:8000

# Add a note and wait for the AI's reply (usually a few seconds)
curl -s "$URL/api/notes?wait=true" -H "X-Magpie: 1" -H "Content-Type: application/json" \
  -d '{"text": "remind me to call James at 5 pm", "source": "api"}'
# {"note": {"id": 12, "title": "Call James", ...}, "reply": "Saved. I'll remind you at 5:00 PM."}

# Search
curl -s "$URL/api/notes?q=budget"

# Upcoming reminders
curl -s "$URL/api/reminders"

# Snooze one
curl -s -X POST "$URL/api/reminders/34/snooze" -H "X-Magpie: 1" \
  -H "Content-Type: application/json" -d '{"minutes": 30}'
```

`source` is a short label shown with the note (`api`, `shortcut`, …). Add `category_id` to
put the note in one of your categories, and `captured_in` (up to 60 characters) to record
where it was written. Notes made offline can pass `captured_at` (ISO 8601) so “in 20
minutes” counts from when you said it, and a random `client_id` (8 to 64 letters, digits,
`-` or `_`): sending the same one again returns the note already saved instead of making a
copy, so retries are safe.

Errors come back as JSON with a readable `detail` sentence, for example
`{"detail": "text: The note is empty."}`.
