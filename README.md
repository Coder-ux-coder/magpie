<p align="center">
  <img src="src/magpie/web/static/icons/icon-192.png" width="96" height="96" alt="">
</p>

<h1 align="center">Magpie</h1>

<p align="center">
  <strong>Say “notes on”, talk, and Magpie does the rest.</strong><br>
  A self-hosted, voice-first notebook. The AI you choose files your notes, reminds you<br>
  when it matters, and writes short reviews of the videos and articles you save.
</p>

<p align="center">
  <a href="https://github.com/Coder-ux-coder/magpie/actions/workflows/ci.yml"><img src="https://github.com/Coder-ux-coder/magpie/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="MIT license"></a>
  <img src="https://img.shields.io/badge/python-3.11%2B-blue.svg" alt="Python 3.11+">
</p>

<p align="center">
  <img src="docs/images/dashboard.png" width="70%" alt="Magpie on a computer: colour-coded category tabs, filed notes, and the voice bar at the bottom showing words as they are spoken">
  <img src="docs/images/phone-review.png" width="23%" alt="A note on a phone with a review of an essay: verdict, rating, TL;DR and key points">
  <br><sub>Screenshots use the sample notes from <code>scripts/seed_demo.py</code>.</sub>
</p>

---

You think of things at the worst moments: driving, walking, half asleep. Magpie lets you
say them out loud and forget about them:

> *“Notes on. Remind me to call James at five about the budget. Notes off.”*

Magpie tidies up your words, gives the note a title, files it as a task in your **Work**
category, and at 5:00 PM a notification pops up on your computer, and an email arrives on
your phone: *“⏰ Call James about the budget · due today 5:00 PM”*. Say *“the electricity
bill is due on the 10th”* and it decides for itself to nudge you the evening before. Share
a YouTube video and you get a two-line verdict on whether it's worth your time.

## Features

**A voice bar that's always there**
- It sits at the bottom of every view. Say **“notes on”** (or press the mic, or just type),
  watch your words appear as you speak, fix anything, then say **“notes off”** or press
  **Save**. **Cancel** throws the note away, with **Undo**.
- Magpie starts listening for your command word when it opens. Choose your own command and
  stop words, switch listening off, or check each note before it's saved.
- **Pop out** keeps the bar in a small window on top of everything else (Chrome and Edge),
  and **Install** makes Magpie an app with its own window and icon: no typing
  `localhost:8000`.
- Elsewhere: share links to it on your phone, send **Telegram** text or voice messages, say
  **“Hey Siri, notes on”**, use a bookmarklet, the HTTP API, the command line
  (`magpie add "…"`) or the MCP server for Claude Desktop and Claude Code.

**Your categories, your colours**
- Make your own categories, such as Work, Home and Family, each in its own colour, in the
  order you like. They're tabs right next to the inbox.
- The AI files each note into the category that fits. A note you write while a category's
  tab is open stays there, and you can drag any note onto a tab (or onto Done or Archive).
- Every note remembers which tab you were on when you wrote it.
- **It learns your way:** when you move a note to another category or change its kind,
  Magpie remembers and files similar notes the same way. You can see and forget each lesson.

**Any AI, or none**
- **Claude Haiku 5.5** by default: fast and low-cost. Switch to Sonnet or Opus, or to
  **ChatGPT**, **Gemini**, **OpenRouter**, a free model on your own computer with
  **Ollama**, any OpenAI-compatible service, or **no AI at all** (simple rules file your
  notes).
- A welcome screen sets it all up the first time: your AI, reminders, your command word and
  your categories. Keys are checked before they're saved and stay on your computer.
- **Your instructions:** standing rules the AI follows, like “tag anything about the shop
  with #shop” or “remind me 30 minutes before meetings”. Titles are written for you, and
  you can change any of them.

**It decides when to remind you**

| You say | Magpie does |
| --- | --- |
| “remind me at 5 pm”, “every Monday at 9”, “in 20 minutes” | Schedules exactly that, on your computer's clock |
| “the bill is due on the 10th” | Suggests a reminder shortly before it matters, and says why |
| “idea: voice replies for reminders” | No ping; it shows up in tomorrow's digest |
| “had a great chat with Dad today” | Just files it |

Choose how proactive it is: *quiet*, *balanced* or *proactive*.

**Reminders where you'll see them**
- **Pop-ups** on your computer and phone, with Done and Snooze buttons, and **email** for
  your phone (just your address and an app password). Telegram, ntfy and Slack or Discord
  webhooks work too.
- A **daily digest** each morning and a **weekly review** of themes and open loops.
- **Ask your notes** anything: *“what did I decide about rent?”*
- A **calendar feed** for Google, Apple or Outlook Calendar.

**Reviews of what you save**
- YouTube videos (from the transcript), articles and PDFs get a TL;DR, key points, a verdict
  (*must see*, *worth it*, *skim*, *skip*), a rating and who it's for.
- With Claude, mention something without a link (*“that Veritasium video on the Monty Hall
  problem”*) and it finds it with web search first.

**Made for everyone**
- Works with a keyboard alone (press <kbd>?</kbd>) and with screen readers.
- Read-aloud replies, adjustable text size, high contrast, reduced motion, dark mode.
- Notes taken offline are kept on the device and synced later.

**Yours**
- Self-hosted: one small container and one SQLite file. Export everything as JSON or
  Markdown at any time.
- No password needed on your own computer (Magpie then only answers there). Set one to use
  it from your phone or other devices.
- **Updates itself:** a new version is picked up within the hour, your notes stay as they
  are, and if an update fails to start Magpie goes back to the version that worked.

## Quick start

Magpie runs on your own computer (Windows, Mac or Linux). You need
[Docker Desktop](https://www.docker.com/products/docker-desktop/) (free) installed and
running.

1. **Download Magpie:**
   [magpie-main.zip](https://github.com/Coder-ux-coder/magpie/archive/refs/heads/main.zip),
   and unzip it (Windows: right-click → *Extract All*).
2. **Run the setup:**
   - **Windows:** open the `magpie-main` folder and double-click **`setup.cmd`**.
   - **Mac or Linux:** in Terminal, `cd` into the folder and run `sh setup.sh`.
3. **Answer two questions** (a password, which you can skip, and your time zone). Magpie
   starts, adds a Magpie icon to your desktop and apps, and opens in its own window.
4. **The welcome screen** asks which AI to use, how to remind you, your command word and
   your categories. Then say “notes on”.

Magpie keeps running in the background, starts again whenever Docker starts, and keeps
itself up to date. New to all this? The **[step-by-step guide](docs/getting-started.md)**
walks through every click and fixes common problems.

**Already using an older Magpie?** Download the new ZIP, unzip it over your old folder
(choose *Replace* when asked), and run the setup again. Press Enter at the password
question to keep your password, or type `none` to stop using one. Your notes are kept.
This is needed once; after that Magpie updates itself.

<details>
<summary>Other ways to install</summary>

**With git:** `git clone https://github.com/Coder-ux-coder/magpie.git`, then run the setup
in the `magpie` folder as above.

**Docker without the script:** `docker compose up -d`, then open <http://localhost:8000>.
Without a `.env`, Magpie makes a password and prints it in `docker compose logs magpie`; put
`MAGPIE_LOGIN=false` in `.env` to use it without one on this computer. `.env.example` lists
every setting.

**Without Docker** (Python 3.11 or newer; run it from the same folder every time):

```bash
python3 -m pip install https://github.com/Coder-ux-coder/magpie/archive/refs/heads/main.zip
python3 -m magpie setup     # asks the same questions and writes .env
python3 -m magpie serve     # on Windows use py instead of python3
```

</details>

### No API key? Use Magpie inside Claude

[`artifact/magpie.html`](artifact/magpie.html) is a version of Magpie that runs as a Claude
artifact. Claude files your notes with your own Claude account (Haiku 5.5 by default), so
there's no API key and nothing to install. It keeps your notes private to you, with your
categories, reminders while it's open (plus Google Calendar and Outlook links), *My day*
and Ask. To make your own copy, ask Claude Code to “publish `artifact/magpie.html` as an
artifact with the db, user, sample and downloads capabilities”. Its notes export to a file
you can import into a self-hosted Magpie later (Settings → Data), categories included.

The self-hosted Magpie can't use a Claude subscription: apps outside Claude need an API
key. That's why it also works with other AIs, including free ones, or with none.

### Make it yours

Everything is in **Settings**, no restart needed:

- **AI:** the service, the model, and its key.
- **Reminders:** pop-ups on this device, email, Telegram, and a test for each.
- **Categories:** names, colours and order; what Magpie has learned from your changes.
- **Voice:** command and stop words, your accent (UK, US, Australian and more), the silence
  timeout, listening when Magpie opens.
- **Your instructions**, **about you**, how **proactive** to be, your **morning**, the
  **snooze** length, the **digest** and **weekly review** times.
- **Time:** follow this computer's time zone (on by default), or pick one.
- **Display:** theme, text size, high contrast, reduced motion, read-aloud.

### Next steps

1. **Get reminders on your phone.** Easiest: **Settings → Reminders → Email** with your
   phone's email address. Or connect Telegram: create a bot with
   [@BotFather](https://t.me/BotFather), set `MAGPIE_TELEGRAM_BOT_TOKEN`, restart, then
   open **Settings → Reminders → Connect Telegram**; you can send it notes too.
2. **Use Magpie from your phone.** Set a password (run the setup again), and for the
   microphone put Magpie behind HTTPS ([deployment guide](docs/deployment.md)).
3. **Hands-free on iPhone:** set up the [“Hey Siri, notes on” Shortcut](docs/integrations.md#iphone-and-ipad-hey-siri-notes-on).

Run `magpie doctor` (or `docker compose exec magpie magpie doctor`) to check your setup.

## Documentation

| | |
| --- | --- |
| [Getting started](docs/getting-started.md) | Step by step for Windows, Mac and Linux, and fixes for common problems |
| [Integrations](docs/integrations.md) | Email, pop-ups, Telegram, Siri, Android, bookmarklet, calendar, Claude Desktop (MCP), the HTTP API |
| [Deployment](docs/deployment.md) | HTTPS, servers, Raspberry Pi, backups, updates, troubleshooting |
| [Configuration](docs/configuration.md) | Every setting, with defaults |
| [Architecture](docs/architecture.md) | How the pieces fit, the data model, how the AI is used |
| [Product spec](docs/SPEC.md) | The refined brief Magpie was built from |
| [AGENTS.md](AGENTS.md) | For AI coding agents (and humans) working on the code |

## How Magpie uses AI

Every note is one request to the AI you chose, asking for a fixed JSON shape: the cleaned
text, title, kind, category, tags, any reminders (as local wall-clock times, which Magpie
converts with your time zone) and a one-line reply. With Claude this uses
[structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs),
so the answer is always valid; other services get a JSON schema, and Magpie checks and
repairs what comes back. Reviews, digests, web search and Ask are separate requests.

**What it costs** depends on the model and your habits. Haiku 5.5, the default, costs
the least of the three Claude models; Sonnet and Opus cost more per note but handle tricky dates and
long notes more carefully. Ollama on your own computer and *no AI* cost nothing. Check
actual spend with your provider; Settings → AI shows Magpie's token counts.

## Privacy

Magpie keeps your notes in a SQLite file on your computer. To do its job it sends:

- **note text and saved pages** to the AI you chose (nothing, with Ollama or no AI) for
  filing, reviews, digests and Ask;
- **audio** to your browser's speech service when you use the mic (Chrome and Edge use their
  makers' servers, Safari uses Apple's), or to your speech-to-text provider for Telegram
  voice notes and server-side transcription;
- **messages** to whichever reminder channels you set up (email, Telegram, ntfy, …);
- a check for new versions, to GitHub's container registry, about once an hour (turn it off
  with `MAGPIE_AUTO_UPDATE=false`).

Nothing else leaves your computer. See [SECURITY.md](SECURITY.md) to report a vulnerability.

## Development

```bash
git clone https://github.com/Coder-ux-coder/magpie.git && cd magpie
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev,mcp]"
MAGPIE_BRAIN=fake MAGPIE_LOGIN=false magpie serve      # http://localhost:8000

pytest                                   # Python tests (offline, no API key needed)
node --test "tests/js/*.test.mjs"        # browser-side tests
ruff check src tests scripts && ruff format --check src tests scripts
```

The web app is plain JavaScript modules with no build step: edit a file and reload.
Read [CONTRIBUTING.md](CONTRIBUTING.md) and [AGENTS.md](AGENTS.md) before opening a pull request.

## Roadmap

- WhatsApp reminders (needs the WhatsApp Business API)
- Photo notes: whiteboards, posters and receipts, read by the AI
- Two-way calendar sync
- Speech recognition fully on the device
- Shared lists for families and teams

Ideas and pull requests are welcome.

## License

[MIT](LICENSE). Magpie is an independent open-source project and is not affiliated with Anthropic.
