<p align="center">
  <img src="src/magpie/web/static/icons/icon-192.png" width="96" height="96" alt="">
</p>

<h1 align="center">Magpie</h1>

<p align="center">
  <strong>Say “notes on”, talk, and Magpie does the rest.</strong><br>
  A voice notes app for Windows. It listens for “notes on” in any program, writes down what<br>
  you say, and the AI you choose files the note and reminds you when it matters.
</p>

<p align="center">
  <a href="https://github.com/Coder-ux-coder/magpie/releases/latest/download/MagpieSetup.exe"><strong>Download Magpie for Windows</strong></a>
</p>

<p align="center">
  <a href="https://github.com/Coder-ux-coder/magpie/actions/workflows/windows-app.yml"><img src="https://github.com/Coder-ux-coder/magpie/actions/workflows/windows-app.yml/badge.svg" alt="Windows app"></a>
  <a href="https://github.com/Coder-ux-coder/magpie/actions/workflows/ci.yml"><img src="https://github.com/Coder-ux-coder/magpie/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="MIT license"></a>
</p>

<p align="center">
  <img src="docs/images/voice-bar.png" width="560" alt="The Magpie voice bar floating at the bottom of the screen while recording: the words “Remind me to call James at five about the budget” appear as they are spoken, with Cancel and Save buttons">
</p>

## Install

1. **Download [MagpieSetup.exe](https://github.com/Coder-ux-coder/magpie/releases/latest/download/MagpieSetup.exe)**
   and double-click it. There's nothing to answer and no administrator password. (If
   Windows says *Windows protected your PC*, click **More info**, then **Run anyway**: Magpie
   isn't signed by a company.)
2. **Choose your AI** on the welcome screen: Claude, Codex (your ChatGPT sign-in, no key),
   ChatGPT, Gemini, OpenRouter, a free AI on your own computer (Ollama), or none.
3. **Say “notes on”**, your note, then **“notes off”**. That's it.

Magpie starts with Windows, waits by the clock and updates itself. There's no account, no
password and nothing else to install; your notes stay on your computer. On its first start
it downloads Whisper, its accurate speech model (480 MB), in the background.

Prefer a terminal? Paste this into PowerShell, which does the same:

```powershell
irm https://raw.githubusercontent.com/Coder-ux-coder/magpie/main/install.ps1 | iex
```

## No API key?

- **ChatGPT users: choose Codex.** Install [Codex](https://github.com/openai/codex), OpenAI's
  command-line tool, sign it in with `codex login`, and choose **Codex** in Magpie's welcome
  screen or Settings → AI. Magpie runs it on your computer for each note; there's no key to
  paste and Magpie never sees your sign-in. Your Codex plan's limits apply.
- **Claude users: use Magpie inside Claude** (below).

### Use Claude without an API key

Apps outside Claude, this one included, can't use a Claude subscription. So
[`artifact/magpie.html`](artifact/magpie.html) is a version of Magpie that runs **inside
Claude** as an artifact: Claude files your notes with your own Claude account, so there's no
API key and nothing to install. It has your categories, reminders while it's open (plus
Google Calendar and Outlook links), *My day* and *Ask*. To make your own copy, ask Claude
Code to *“publish `artifact/magpie.html` as an artifact with the db, user, sample, downloads
and permissions capabilities”*. Its notes export to a file the app imports (Settings → Data).

## Set Magpie up for someone (for AI agents)

Magpie runs on the person's own Windows computer, so run these in PowerShell there. No
administrator rights are needed, and nothing asks questions.

1. **Install or update Magpie, and start it:**

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/Coder-ux-coder/magpie/main/install.ps1 | iex"
   ```

   It downloads the latest release, checks it against the release's SHA-256, installs it
   for the current user, and waits until Magpie answers. Its last line is Magpie's address,
   such as `http://127.0.0.1:8000` (also in `%LOCALAPPDATA%\Magpie\running.json`). The
   welcome screen then opens for the person.

2. **Optionally choose the AI for them** (otherwise the welcome screen asks). The key is
   checked before it's saved, and never shown again:

   ```powershell
   $magpie = "http://127.0.0.1:8000"   # the address install.ps1 printed
   Invoke-RestMethod "$magpie/api/ai" -Method Put -Headers @{ "X-Magpie" = "1" } `
     -ContentType "application/json" -Body '{"provider": "claude", "key": "sk-ant-..."}'
   ```

   Providers: `claude`, `openai`, `gemini`, `openrouter`, `ollama` (free, local; no key),
   `codex` (no key), `compatible` (add `"base_url"`), or `basic` (no AI). Add `"model"` to
   pick a model. For `codex`, install Codex first
   (`irm https://chatgpt.com/codex/install.ps1 | iex`) and have the person run
   `codex login` in a new window, which signs in through their browser; then send
   `{"provider": "codex"}`.

3. **Check that it listens:** `Invoke-RestMethod "$magpie/api/desktop"` answers
   `{"active": true, "state": "standby", ...}`. `"no-mic"` means Windows gives it no
   microphone: Settings → Privacy & security → Microphone → *Let desktop apps access your
   microphone*. Its `speech` part shows Whisper's download (`"state": "downloading"`,
   `"progress"`), then `"ready"`; notes are written down accurately from then on.

Good to know:

- Requests from the same computer need no password or token; `POST`, `PUT` and `DELETE`
  need the header `X-Magpie: 1`. The full API is described at `$magpie/api/docs`.
- Add a note: `Invoke-RestMethod "$magpie/api/notes" -Method Post -Headers @{ "X-Magpie" = "1" } -ContentType "application/json" -Body '{"text": "Remind me to call James at 5pm"}'`
- Notes and settings live in `%LOCALAPPDATA%\Magpie` (the log is in its `logs` folder);
  the program is in `%LOCALAPPDATA%\Programs\Magpie`.
- To remove it: Windows Settings → Apps → Magpie, or
  `& "$env:LOCALAPPDATA\Programs\Magpie\unins000.exe" /VERYSILENT`. Notes are kept.
- If the person used Magpie in Docker before, the app brings those notes and settings over
  on its first start and stops the Docker copy.

## Using it

- **Say “notes on”** at the start of a sentence, in any program. A small bar appears at the
  bottom of the screen with your words as you speak, without taking the keyboard from what
  you're doing. **“Notes off”** saves the note (so does a pause, or **Save**); **“cancel
  note”** throws it away, with **Undo**. Click the bar to type into it.
- **The AI files it:** it tidies your words, gives the note a title, a kind (task, idea,
  link, …) and one of your own categories, and decides whether and when to remind you:
  “remind me at 5”, “every Monday at 9”, or, for “the bill is due on the 10th”, the evening
  before.
- **Reminders pop up** in the corner of the screen with Done and Snooze. On your phone, get
  them by **email** or **Telegram** (Settings → Reminders); Telegram can take notes too.
- **Accurate words:** when a note ends, Whisper writes it down again from its sound, with
  punctuation, and copes with accents. Both speech models run on your computer: no audio
  leaves it. Settings → Voice has a *Most accurate* model for strong accents.
- **The tray icon** (by the clock) opens your notes, starts a note, and turns listening off
  and on.
- **Your notes:** categories with colours, search, a daily digest, **Ask** (“what did I
  decide about rent?”), and short reviews of the videos and articles you save. Magpie learns
  from your corrections.

<p align="center">
  <img src="docs/images/dashboard.png" width="80%" alt="Magpie's notes window: colour-coded category tabs, filed notes, and the voice bar at the bottom">
</p>

More in the **[guide](docs/guide.md)**: everyday use, reminders on your phone,
integrations, and fixes for common problems.

### Mac and Linux

The app is made for Windows. On a Mac or Linux it runs from Python 3.11 or newer (still new
there; Linux needs GTK or Qt for its windows):

```bash
python3 -m pip install "magpie-notes[desktop] @ https://github.com/Coder-ux-coder/magpie/archive/refs/heads/main.zip"
python3 -m magpie desktop
```

## Privacy

Your notes are a SQLite file on your computer. Magpie sends **note text** (and pages you
save) to the AI you chose, for filing, reviews and Ask (nothing with Ollama or no AI);
**reminders** to the channels you set up (email, Telegram, …); asks GitHub for new versions
every few hours; and downloads its speech model from Hugging Face once. **No audio leaves
your computer.** See [SECURITY.md](SECURITY.md) to report a vulnerability.

## Development

```bash
git clone https://github.com/Coder-ux-coder/magpie.git && cd magpie
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev,mcp,desktop]"
MAGPIE_BRAIN=fake magpie desktop          # the app, with an offline stand-in for the AI
pytest -q && ruff check src tests scripts
```

The app is one Python process: the Magpie server (FastAPI and SQLite), its web pages,
offline listening (Vosk) and writing down (Whisper), and a tray icon. [AGENTS.md](AGENTS.md) explains the code for
people and AI agents, [docs/architecture.md](docs/architecture.md) how it fits together, and
[docs/SPEC.md](docs/SPEC.md) what it's for. The *Windows app* workflow builds
`MagpieSetup.exe`, tests it on Windows and publishes a release for each new version.

## License

[MIT](LICENSE). Magpie is an independent open-source project and is not affiliated with Anthropic.
