# Integrations

Ways to get notes into Magpie and reminders out of it, beyond the dashboard.

- [Pop-ups and email](#pop-ups-and-email)
- [Telegram](#telegram)
- [iPhone and iPad: “Hey Siri, notes on”](#iphone-and-ipad-hey-siri-notes-on)
- [Android](#android)
- [Any browser: hands-free, bookmarklet, share target](#any-browser)
- [Calendar feed](#calendar-feed)
- [Claude Desktop and Claude Code (MCP)](#claude-desktop-and-claude-code-mcp)
- [Notification channels](#notification-channels)
- [HTTP API](#http-api)

Most integrations need your **API token** (Settings → Integrations) and, from outside your
home network, a public HTTPS address for Magpie (see [deployment](deployment.md)).

## Pop-ups and email

The two simplest ways to get reminders, both set up in **Settings → Reminders** (or on the
welcome screen), with nothing to install:

- **Pop-ups:** press **Turn on pop-ups here** on each computer or phone and allow
  notifications. A reminder then pops up even when Magpie's window is in the background,
  with Done and Snooze buttons. Browsers allow this on `http://localhost` and on HTTPS
  addresses; on an iPhone or iPad, add Magpie to the Home Screen first.
- **Email:** enter your address and an *app password* (Gmail, Outlook, Yahoo and iCloud make
  one in their security settings; the form links to each). Magpie fills in the mail server,
  checks the login before saving, and sends a test. Each reminder arrives as
  *“⏰ Call James about the budget · due today 5:00 PM”*, so it shows up on your phone like
  any other email. Another mail server? Open *Mail server settings* in the form, or set
  `MAGPIE_SMTP_*` (see [configuration](configuration.md#notifications)).

## Telegram

Telegram is the most convenient way to use Magpie on a phone: you send notes by text or
voice, and reminders arrive as messages with Done / +1 hour / Tomorrow buttons. Magpie
polls Telegram, so this works even when your server has no public address.

1. In Telegram, talk to [@BotFather](https://t.me/BotFather), send `/newbot` and follow the
   prompts. Copy the token it gives you.
2. Set `MAGPIE_TELEGRAM_BOT_TOKEN=<token>` in `.env` and restart Magpie.
3. In the dashboard, open **Settings → Reminders → Connect Telegram** and tap
   **Open @yourbot** (or send the bot the `/start 123456` message shown). The code works
   for 15 minutes and only once.

Your bot then only answers you; anyone else who finds it gets a polite refusal.

**Voice messages** are transcribed on the server, so they need a speech-to-text key:

```dotenv
MAGPIE_STT_API_KEY=sk-...                     # OpenAI
MAGPIE_STT_BASE_URL=https://api.openai.com/v1
MAGPIE_STT_MODEL=whisper-1
```

Groq is a fast, cheap alternative (`MAGPIE_STT_BASE_URL=https://api.groq.com/openai/v1`,
`MAGPIE_STT_MODEL=whisper-large-v3-turbo`), and any self-hosted server that implements
the OpenAI `/audio/transcriptions` endpoint works too.

**Commands:** `/today`, `/upcoming`, `/search budget`, `/ask what did I decide about rent?`,
`/digest`, `/week`, `/help`. Forwarded messages are saved with who sent them, and links are
reviewed and the review is sent back as a reply.

## iPhone and iPad: “Hey Siri, notes on”

Safari can't listen in the background, but a Shortcut can: Siri takes your dictation and
sends it to Magpie, then reads Claude's reply back to you.

1. Open the **Shortcuts** app and tap **+** to make a new shortcut. Name it **Notes on**.
2. Add the action **Dictate Text**. Set *Language* to English and *Stop Listening* to
   *After Pause*.
3. Add **Get Contents of URL**:
   - URL: `https://your-magpie.example.com/api/notes?wait=true`
   - Method: **POST**
   - Headers: `Authorization` = `Bearer mgp_your_token`
   - Request Body: **JSON**, with fields `text` = *Dictated Text* (the variable) and
     `source` = `siri`
4. Add **Get Dictionary Value**: key `reply`, from *Contents of URL*.
5. Add **Speak Text** with the *Dictionary Value* (or **Show Notification** if you prefer).

Now say **“Hey Siri, notes on”**, speak your note, and Siri answers with something like
*“Saved. I'll remind you at 5:00 PM to call James.”* You can also add the shortcut to your
Home Screen or the Action Button.

**Save links from the share sheet:** make a second shortcut that *Receives URLs and Text
from the Share Sheet* and sends `{"text": <Shortcut Input>, "source": "shortcut"}` to the
same URL. Then share any page to it to get a review.

Web push works on iOS 16.4 and later once Magpie is added to the Home Screen
(Safari → Share → Add to Home Screen); turn it on in Settings → Reminders.

## Android

- **Install the app:** open Magpie in Chrome, then menu → **Install app** (or *Add to Home
  screen*). Long-press the icon for a **New voice note** shortcut.
- **Share to Magpie:** once installed, Magpie appears in Android's share sheet. Share a
  YouTube video, an article or any text to it, press **Save**, and get a review of any link.
- **Telegram:** hold the mic button in your Magpie bot chat to send a voice note.
- **Voice from the home screen without opening Magpie:** apps such as
  [HTTP Shortcuts](https://http-shortcuts.rmy.ch/) can ask for voice input and POST it to
  `/api/notes?wait=true` with your token, showing the reply as a toast.

Browser push notifications work in Chrome on Android; turn them on in Settings →
Notifications.

## Any browser

**The voice bar.** Magpie listens for **“notes on”** as soon as it opens (once you've
allowed the microphone). Say it, talk while your words appear in the bar, and say
**“notes off”** (or press Save, or just stop talking for a few seconds) to save the note.
“Cancel note”, Cancel or Esc discards it, with Undo. Change the words, the accent and the
silence timeout from the bar's options or Settings → Voice. This uses the browser's
built-in speech recognition (Chrome, Edge, Safari); Chrome and Edge send the audio to their
makers' speech services. In other browsers, or if you prefer, set `MAGPIE_STT_API_KEY` and
turn on *Use the server's speech-to-text*.

The microphone only works over HTTPS (or on `localhost`). Browsers may stop listening in a
background tab, so keep Magpie open in its own window, or use **Pop out** (Chrome and Edge)
to keep the bar in a small window on top of your other programs. Only one Magpie window
listens at a time.

**Keyboard.** Press <kbd>?</kbd> in the dashboard for all shortcuts: <kbd>m</kbd> toggles
the mic, <kbd>Alt</kbd>+<kbd>N</kbd> does it even while typing, <kbd>n</kbd> types a note,
<kbd>/</kbd> searches, <kbd>j</kbd>/<kbd>k</kbd> move through notes.

**Bookmarklet.** Settings → Integrations has a **Save to Magpie** button: drag it to your
bookmarks bar. Clicking it on any page opens a small window with the page (and any text
you selected) ready to save; add a few words if you like and press **Save**.

**Share target.** On Android and Chromebooks, the installed app accepts shares. Any tool can
also open `https://your-magpie/share?url=…&title=…&text=…` to prepare a note. Shared content
is only saved when you press **Save**, so other websites can't add notes for you.

## Calendar feed

Settings → Integrations shows a private calendar link
(`https://your-magpie/calendar/<secret>.ics`) with your upcoming reminders and the last two
weeks of past ones. Repeating reminders repeat in the calendar too.

- **Google Calendar:** Settings → Add calendar → **From URL**.
- **Apple Calendar:** File → **New Calendar Subscription**.
- **Outlook:** Add calendar → **Subscribe from web**.

Calendar apps refresh subscriptions on their own schedule (Google can take several hours),
so use the calendar for an overview and Telegram or push for the reminder itself. Anyone
with the link can see your reminders; use **New link** to revoke it.

## Claude Desktop and Claude Code (MCP)

Magpie includes a [Model Context Protocol](https://modelcontextprotocol.io) server, so
Claude can search your notes, add notes and reminders, and check what's coming up. It
runs on your computer and talks to your Magpie server through the API.

It needs [uv](https://docs.astral.sh/uv/) (or install `magpie-notes[mcp]` with pip and
run `magpie mcp`).

**Claude Desktop:** add this to `claude_desktop_config.json` (Settings → Developer → Edit
Config) and restart Claude Desktop:

```json
{
  "mcpServers": {
    "magpie": {
      "command": "uvx",
      "args": ["--from", "magpie-notes[mcp] @ git+https://github.com/Coder-ux-coder/magpie", "magpie", "mcp"],
      "env": {
        "MAGPIE_URL": "https://your-magpie.example.com",
        "MAGPIE_API_TOKEN": "mgp_your_token"
      }
    }
  }
}
```

**Claude Code:**

```bash
claude mcp add magpie \
  -e MAGPIE_URL=https://your-magpie.example.com \
  -e MAGPIE_API_TOKEN=mgp_your_token \
  -- uvx --from "magpie-notes[mcp] @ git+https://github.com/Coder-ux-coder/magpie" magpie mcp
```

Tools: `add_note`, `search_notes`, `recent_notes`, `get_note`, `upcoming_reminders`,
`complete_reminder`, `snooze_reminder`. Try asking Claude *“What's on my Magpie list for
this week?”* or *“Add a note in Magpie to renew my passport next month.”*

## Notification channels

Reminders and digests go to every channel that is set up and switched on in Settings →
Reminders. Use **Test** next to each one to check it.

| Channel | Set up | Buttons |
| --- | --- | --- |
| Telegram | `MAGPIE_TELEGRAM_BOT_TOKEN`, then connect in Settings | Done, +1 hour, Tomorrow, Open |
| Pop-ups (browser push) | Settings → Reminders → *Turn on pop-ups here* | Done, Snooze |
| [ntfy](https://ntfy.sh) | `MAGPIE_NTFY_TOPIC` (a long, secret name), subscribe in the ntfy app | Done, Snooze |
| Email | Settings → Reminders → Email (or `MAGPIE_SMTP_*` and `MAGPIE_EMAIL_TO`) | Links to Done and Snooze |
| Webhook | `MAGPIE_WEBHOOK_URL`, `MAGPIE_WEBHOOK_FORMAT` (`json`, `slack`, `discord`) | — |

Buttons outside Telegram use signed one-time links, which need `MAGPIE_BASE_URL` set to an
address your phone can reach. Opening such a link shows a confirmation page first, so link
previews and email scanners can't press the button for you.

The `json` webhook format posts:

```json
{
  "event": "reminder",
  "title": "Call James about the budget",
  "body": "Budget call\nDue today 5:00 PM.",
  "text": "Call James about the budget\n\nBudget call\nDue today 5:00 PM.",
  "url": "https://your-magpie/#/note/12",
  "note_id": 12,
  "reminder_id": 34,
  "due": "today 5:00 PM",
  "actions": {"done": "https://your-magpie/a/…", "snooze": "https://your-magpie/a/…"}
}
```

`event` is `reminder`, `digest` or `test`; `due` is set for reminders only.

## HTTP API

Interactive documentation lives at `/api/docs` on your server. Authenticate with
`Authorization: Bearer <API token>`.

```bash
TOKEN=mgp_your_token
URL=https://your-magpie.example.com

# Add a note and wait for the AI's reply (usually a few seconds)
curl -s "$URL/api/notes?wait=true" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"text": "remind me to call James at 5 pm", "source": "api"}'
# {"note": {"id": 12, "title": "Call James", ...}, "reply": "Saved. I'll remind you at 5:00 PM."}

# Search
curl -s "$URL/api/notes?q=budget" -H "Authorization: Bearer $TOKEN"

# Upcoming reminders
curl -s "$URL/api/reminders" -H "Authorization: Bearer $TOKEN"

# Snooze one
curl -s -X POST "$URL/api/reminders/34/snooze" -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -d '{"minutes": 30}'

# Upload a recording (needs MAGPIE_STT_API_KEY)
curl -s "$URL/api/notes/audio?wait=true" -H "Authorization: Bearer $TOKEN" \
  -F file=@memo.m4a -F source=api
```

`source` is a short label shown with the note (`api`, `siri`, `shortcut`, …). Add
`category_id` to put the note in one of your categories, and `captured_in` (up to 60
characters) to record where it was written. Notes made
offline can pass `captured_at` (ISO 8601) so “in 20 minutes” counts from when you said it,
and a random `client_id` (8 to 64 letters, digits, `-` or `_`): sending the same one again
returns the note already saved instead of making a copy, so retries are safe.

Errors come back as JSON with a readable `detail` sentence, for example
`{"detail": "text: The note is empty."}`.
