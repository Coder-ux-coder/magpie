# Settings

Almost everything is set in Magpie itself: the welcome screen walks you through the AI,
reminders, your command word and categories, and **Settings** has them all, saved as you
change them.

A few things are only for people who need them (a Telegram bot, a mail server of your own,
another AI address). They go in a small text file, **`magpie.env`**, in Magpie's folder:
`%LOCALAPPDATA%\Magpie\magpie.env` on Windows, `~/Library/Application Support/Magpie/magpie.env`
on a Mac. One setting per line, such as `MAGPIE_TELEGRAM_BOT_TOKEN=123456:ABC...`; quit
Magpie and open it again to use them. (Settings brought over from Magpie in Docker are
written there.) The app always answers only on its own computer, with no password, whatever
the file says. A value that can't be read is named in Magpie's log.

## In Settings

| Setting | Default | Meaning |
| --- | --- | --- |
| Welcome screen | shown once | Choose an AI, reminders, your command word and categories. Opens again from Settings → Privacy & account. |
| AI | Claude Haiku 5.5 | Which AI files your notes, its model, and its key or address. *No AI* files notes with simple rules. |
| Email | off | Your address and an app password; Magpie checks the login before saving and sends a test. Every reminder arrives as an email such as “⏰ Call James · due today 10:00 AM”. |
| Pop-ups | on | Every reminder in the corner of the screen, with Done, Snooze and Open. |
| Categories | none | Your own, each with a colour, in the order you choose (drag, or the arrows). The AI files notes into them; a note written in a category's tab stays there; drag a note onto a tab to move it. |
| Learn from my changes | on | When you move a note to another category or change its kind, Magpie remembers and files similar notes your way (the last 40 changes; you can forget any of them). |
| Proactivity | Balanced | *Quiet*: only reminders you ask for. *Balanced*: also before clear deadlines and appointments. *Proactive*: suggests reminders for anything actionable. |
| About you | | Free text the AI reads with every note: working hours, names, family. Up to 2,000 characters. |
| Your instructions | | Standing instructions the AI follows when filing notes, writing digests and answering questions: “tag shop notes #shop”, “remind me 30 minutes before meetings”, “use British spelling”. Up to 2,000 characters. |
| Web search | on | Find videos and articles mentioned without a link (Claude). |
| Time zone | this computer's | Used for every reminder and digest; the app follows the computer's clock. |
| Your morning | 09:00 | What “tomorrow morning” and the Tomorrow button mean. |
| Snooze for | 1 hour | Length of every Snooze button: pop-ups, Telegram and email (5 minutes to 1 day). Snoozing a reminder that isn't due yet moves it later, never earlier. |
| Listen for the command word | on | Say the command word to start a note (also in Magpie's menu). |
| Writing down what you say | Automatic | How each voice note is written down once it ends: *Automatic* (the most accurate model on a computer with 8 or more processor threads, the faster one otherwise), *Most accurate* (Whisper large turbo, a 1.6 GB download; best with accents), *Faster* (Whisper small, 480 MB) or *Off* (the quick recogniser's words). Whisper's small model also writes the words in the bar as you speak. It all runs on the computer; no audio leaves it. |
| Wake / stop phrase | “notes on” / “notes off” | Any phrase you like. |
| Silence timeout | 8 s | Save a voice note after this much silence (0 = only when you say the stop phrase). |
| Channels | all on | Switch each way of reminding you on or off. |
| Daily digest | 08:00 | Empty turns it off. |
| Weekly review | Sunday 18:00 | Day *Off* turns it off. |
| Updates | automatic | *Check for updates* and *Update now* (Settings → Updates). |
| Theme, text size, contrast, motion, read-aloud | this computer's | Display options. |

Magpie's menu also turns listening and starting with the computer on and off; Magpie keeps
those in `desktop.json` in its folder.

## In magpie.env

### AI

Set these only to fix the AI from the file; Settings → AI can't change it then.

| Setting | Default | Meaning |
| --- | --- | --- |
| `MAGPIE_BRAIN` | chosen in Magpie | `claude`, `openai`, `codex` (Codex installed and signed in on this computer; no key), `gemini`, `openrouter`, `ollama`, `compatible` (any OpenAI-compatible service) or `basic` (no AI). |
| `ANTHROPIC_API_KEY` | | Claude API key. A key set here wins over one pasted in Magpie. |
| `MAGPIE_AI_API_KEY` | | API key for the service `MAGPIE_BRAIN` names, when it isn't Claude. |
| `MAGPIE_AI_BASE_URL` | the service's | Its OpenAI-compatible address, ending in `/v1`. Needed for `compatible`; Ollama is `http://localhost:11434/v1`. |
| `MAGPIE_MODEL` | `claude-haiku-5-5` for Claude | The model. `claude-sonnet-5-5` and `claude-opus-5-5` are more capable. Other services start with their own default (for example `gpt-5-mini`; Codex uses its own). |
| `MAGPIE_CODEX_PATH` | `codex` on the PATH | Where the Codex program is, when Magpie can't find it. |
| `MAGPIE_EFFORT` | per task | Claude only: one effort level (`low` to `max`) for all calls. |
| `MAGPIE_FALLBACKS` | `true` | Claude only: if Claude declines a request, retry it on Anthropic's recommended fallback model. |

### Telegram, voice messages and other channels

| Setting | Default | Meaning |
| --- | --- | --- |
| `MAGPIE_TELEGRAM_BOT_TOKEN` | | Bot token from @BotFather ([Telegram](integrations.md#telegram)). |
| `MAGPIE_TELEGRAM_CHAT_ID` | | Optional: your chat id, instead of pairing from Magpie. |
| `MAGPIE_STT_API_KEY` | | Speech-to-text for Telegram voice messages (any OpenAI-compatible `/audio/transcriptions` service). |
| `MAGPIE_STT_BASE_URL` | `https://api.openai.com/v1` | That service's address (OpenAI, Groq, a Whisper server of your own). |
| `MAGPIE_STT_MODEL` | `whisper-1` | Its model. |
| `MAGPIE_NTFY_TOPIC` | | An [ntfy](https://ntfy.sh) topic (a long, secret name). |
| `MAGPIE_NTFY_SERVER` / `MAGPIE_NTFY_TOKEN` | `https://ntfy.sh` | Your own ntfy server, and its access token. |
| `MAGPIE_WEBHOOK_URL` | | Slack, Discord or any address that takes JSON. |
| `MAGPIE_WEBHOOK_FORMAT` | `json` | `json`, `slack` or `discord`. |
| `MAGPIE_SMTP_HOST`, `_PORT`, `_SECURITY`, `_USERNAME`, `_PASSWORD`, `_FROM`, `MAGPIE_EMAIL_TO` | | A mail server Settings → Email doesn't know (`MAGPIE_SMTP_SECURITY`: `starttls`, `ssl` or `none`). These win over Settings. |

### The app itself

| Setting | Meaning |
| --- | --- |
| `MAGPIE_DESKTOP_HOME` | Keep Magpie's folder somewhere else (set it where Magpie starts, not in `magpie.env`). |
| `MAGPIE_VOSK_MODEL` | Another [Vosk](https://alphacephei.com/vosk/models) model folder for listening. |
| `MAGPIE_WHISPER_MODEL` | A Whisper model folder (faster-whisper format) to write notes down with; a `whisper-small.en` folder next to it writes the words shown as you speak. |
| `MAGPIE_ALLOW_PRIVATE_URLS` | `true` lets Magpie fetch links on your own network (only if you trust everything you save). |

Generated secrets (the API token for integrations, the key that signs Magpie's own links)
are kept in Magpie's database; Settings → Integrations shows and renews the token.
