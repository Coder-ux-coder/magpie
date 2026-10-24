# AGENTS.md

Guidance for AI coding agents (Claude Code, Codex, Cursor, …) and humans working on
Magpie. Read this before changing code. The product brief is [docs/SPEC.md](docs/SPEC.md);
the design is in [docs/architecture.md](docs/architecture.md).

## What Magpie is

A single-user, voice-first notes app with no password. It is **the Magpie app for Windows
and Mac** (Apple silicon): it starts with the computer, lives in the tray or the menu bar,
listens offline for the wake phrase “notes on” all the time and shows a small floating bar
with the words as they're said. Notes also arrive from typing, Telegram, the HTTP API, the
CLI or MCP. An AI files each note (clean text, title, kind,
the user's own category, tags), decides whether and when to remind the user, and reviews
linked videos and articles. The AI is Claude by default (Haiku 5.5), or any
OpenAI-compatible service, or Codex signed in on the computer (no key), or none (simple
rules); Claude users without a key use the Claude artifact. A scheduler delivers reminders and digests
as pop-ups (from the app, or web push), email, Telegram, ntfy or webhooks. The app updates
itself, and Settings → Updates has an update button.

One Python process (FastAPI + background asyncio tasks), one SQLite file, and a
no-build-step vanilla-JS PWA. The app is that same server running inside a desktop process
(`magpie.desktop`) with offline speech recognition (Vosk listens; Whisper writes each note
down again accurately), its own windows and a tray or menu bar icon.

## Commands

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev,mcp]"

# Run locally without an API key (offline stand-in for Claude) and with sample data
MAGPIE_DATA_DIR=dev-data python scripts/seed_demo.py
MAGPIE_DATA_DIR=dev-data MAGPIE_BRAIN=fake MAGPIE_LOGIN=false magpie serve   # http://localhost:8000

# The desktop app (needs a screen and a microphone; Windows and Mac)
pip install -e ".[desktop]"
magpie desktop                                  # or: python -m magpie.desktop
magpie desktop --self-test                      # no screen needed; --windows opens one

# Checks: run all of these before you finish
pytest -q
ruff check src tests scripts
ruff format --check src tests scripts     # `ruff format src tests scripts` to fix
pyright                                   # types (pyproject.toml has the settings)
node --test "tests/js/*.test.mjs"
for f in src/magpie/web/static/js/*.js src/magpie/web/static/desktop/*.js src/magpie/web/sw.js; do node --check "$f"; done
```

The tests are offline and need no keys. CI (`.github/workflows/ci.yml`) runs the same checks
on Python 3.11 to 3.13, and pyright with every extra installed, failing on warnings too
(without the `desktop` extra, its packages are only warnings). The **App** workflow
(`app.yml`) builds `MagpieSetup.exe` on Windows and `Magpie-mac.zip` on a Mac, runs each
built app's self-test (a real window, and a note taken from a recording of the system's own
voice, written down by Vosk and by Whisper), installs each (`install.sh` on the Mac), checks
the installed app, and on main publishes one release with both for each new version in
`pyproject.toml`. Its logs can't always be read from an agent's sandbox: results come back
as annotations (`packaging/report.py`).

## Repository map

```
src/magpie/
  config.py        Settings (env vars, MAGPIE_ prefix) and Prefs (dashboard, stored in DB)
  db.py            SQLite connection, numbered MIGRATIONS, FTS5 index
  store.py         ALL SQL lives here; returns models from models.py
  models.py        Note, Link, Reminder, Digest, Category + enums
  schemas.py       Structured outputs the AI returns (Triage, LinkReview, FoundLink, Answer)
  brain/
    __init__.py    make_brain(): picks the brain for the configured provider
    base.py        Brain protocol, Context, DigestData, BrainError types
    providers.py   Provider presets (Claude, OpenAI, Gemini, OpenRouter, Ollama, …) and
                   resolve(): which provider, model, address and key are in force
    claude.py      ClaudeBrain: every Claude API call
    openai_compat.py  OpenAICompatibleBrain: any /chat/completions service, JSON fallbacks
    codex_cli.py   CodexBrain: runs `codex exec` (signed in on this computer, no key)
    prompts.py     System prompts and the formatting of app data for the AI
    fake.py        FakeBrain (tests and demos) and BasicBrain (“no AI”: simple rules)
  pipeline.py      Triage → reminders → links → reviews; retries; events for listeners
  reminders.py     Plans → reminder rows (time zones, RRULE, quiet mode); done/snooze/…
  scheduler.py     Fires due reminders, retries delivery, sends digests on time
  digest.py        Daily digest and weekly review (with a plain-text fallback)
  notify/          Notifier + channels (desktop app, telegram, webpush, ntfy, email, webhook)
  telegram.py      The bot: long polling, pairing, commands, inline buttons
  telegram_api.py  Minimal Bot API client
  content/         URL extraction, SSRF guard (links.py), fetching pages/transcripts (fetch.py)
  stt.py           Speech services of the user's own (OpenAI-compatible, Deepgram,
                   ElevenLabs, AssemblyAI, Gladia): presets, resolve(), Transcriber
  api.py           REST API + public routes (/a/ action links, /calendar/*.ics, /healthz)
  app.py           FastAPI factory, middleware (sessions, security headers), static files
  auth.py          Generated secrets, signed action links, login rate limiter
                   (MAGPIE_LOGIN=false: no password, localhost only; see api.py LOCAL_ONLY)
  core.py          Magpie container: builds and wires services, starts background tasks
  export.py ics.py JSON/Markdown export + import; iCalendar feed
  cli.py           `magpie serve|desktop|add|digest|doctor|password|token|backup|setup|mcp`
  mcp_server.py    MCP server (optional extra) that talks to the HTTP API
  desktop/         The Magpie app (optional extra `desktop`; Windows and Mac):
    app.py         DesktopApp: starts everything, the voice bar's states, saving, pop-ups;
                   Bridge: what the server sees of the app (core.desktop)
    speech.py      Listener (wake → record → stop, from word timings) and VoiceEngine
                   (the microphone thread); Vosk models behind small protocols
    phrases.py     Spotting “notes on”/“notes off”/“cancel note” in recogniser words
    transcribe.py  Whisper (faster-whisper): writes each note down again from its sound;
                   the models are downloaded in the background (Whisper.choose/tick)
    live.py        LiveWriter: the quick Whisper model writes the note so far for the bar
    server.py      EmbeddedServer (uvicorn in a thread, 127.0.0.1) and LocalClient
    ui.py          pywebview windows (notes, the floating bar, reminders) and the tray
    paths.py       Where the app keeps things (%LOCALAPPDATA%\Magpie, or Application
                   Support on a Mac), desktop.json,
                   finding or downloading the speech model
    system.py      Start with the computer, one copy at a time, the active window
    migrate.py     First start: bring notes and settings over from a Docker Magpie
    update.py      Self-update from the GitHub release's latest.json (sha-256 checked;
                   Windows runs the installer, a Mac swaps Magpie.app) and the update button
    selftest.py    `--self-test`: what the App workflow checks on the built app
  web/             index.html, sw.js, manifest, static/{css,js,icons,desktop}
    static/js/     app.js (boot, routing, tabs), capture.js + voice.js (the voice bar),
                   controls.js (AI, email, pop-ups, categories, learning), welcome.js
                   (first-run screen), settings.js, views.js, cards.js, note-dialog.js, …
    static/desktop/ The app's own pages: bar.html/js (floating voice bar), reminders.html/js
artifact/          magpie.html: Magpie as a Claude artifact (the user's own Claude account)
packaging/         bundle.py (what every build includes), magpie-app.py (entry point),
                   report.py (results as workflow annotations);
                   windows/: magpie.spec, Magpie.iss (installer), speak.ps1 (test audio);
                   macos/: magpie.spec (Magpie.app, Info.plist), speak.sh (test audio)
install.ps1        Windows: one line installs or updates the app (SHA-256 checked)
install.sh         Mac: the same, into Applications
tests/             pytest suite (conftest.py has the fixtures) + tests/js (node:test)
scripts/           seed_demo.py
docs/              guide (for people), configuration (settings), integrations, SPEC,
                   architecture
```

## Conventions

### Python

- Python 3.11+, `from __future__ import annotations`, type hints everywhere. Ruff with a
  100-character line limit; match the surrounding style and comment density (short
  docstrings that explain *why*, few inline comments).
- **All SQL goes in `store.py`.** Other modules call store methods. Column names in
  `update_*` calls are checked against allow-lists (`NOTE_FIELDS`, …); extend those when
  you add columns.
- **Schema changes:** append a new script to `MIGRATIONS` in `db.py`. Never edit an
  existing migration: installed databases have already run it.
- **Time:** store UTC; use `timeutil` (`now_utc`, `iso`, `local_to_utc`, `humanize`). Never
  create naive datetimes. Anything user-facing uses the user's `Prefs.timezone`.
- **HTTP:** use `httpx2` (the maintained httpx fork the Anthropic SDK is built on) through
  the shared client `core.http`, so tests can swap in a mock transport. Don't create clients
  inside functions.
- **Installed apps update themselves** (within hours, or from the update button), on top of
  an existing `magpie.env` and database. Every new setting needs a safe default, old
  settings must keep working, and migrations must cope with any older database.
- **Settings vs Prefs:** secrets and infrastructure are `Settings` (environment, restart to
  change). Anything the user adjusts in the dashboard is a `Prefs` field; add it in
  `config.py`, give it a safe default, and expose it in `web/static/js/settings.js`.
- Background work goes through `Pipeline.spawn()` so it is tracked, can be drained in tests,
  and is cancelled on shutdown. Long-running loops are started with `Magpie._supervise()`,
  which restarts them after an unexpected error: a loop must never die silently.
- Robustness is part of every change: anything from the AI, Telegram, the network, the
  database or a user can be malformed. Catch it where it enters, turn it into a clear
  state or message (a failed note, a skipped row, a 422), and add a test with the bad input.
  Repeat rules go through `validate_rrule()`, which refuses rules dateutil can't evaluate
  quickly.
- Errors shown to users are plain sentences that name the AI in use with `brain.label`
  (“Gemini couldn't process this note: …”).

### Web app (`src/magpie/web`)

- Plain ES modules, **no build step, no frameworks, no CDNs** (the CSP blocks them anyway).
- Build DOM with `h()` from `ui.js`. **Never use `innerHTML`** with data. Note text, reviews
  and anything from the AI are untrusted. Use `renderMarkdown()` for the AI's Markdown.
- DOM `append()` turns `null` into the text “null”: use `append(el, [..])` from `ui.js`, or
  `h()`, which skip `null`.
- No inline `<script>` or `on*=` attributes (CSP `script-src 'self'`).
- Accessibility is a requirement, not a nice-to-have: real `<button>`/`<a>` elements, a
  visible label or `aria-label` on every control, keyboard support (`app.js` has the
  shortcut map), focus moved into and back out of dialogs, `dir="auto"` on user text,
  touch targets of at least 44 px, colours that work in light, dark and high contrast, and
  `prefers-reduced-motion` respected.
- Pure logic (like `phrases.js`) stays DOM-free so it can be tested with `node --test`.
- Bump `CACHE` in `sw.js` when you add files to the app shell.
- When the Magpie app runs the server (`status.desktop.active`, `desktopApp()` in
  `state.js`), the app does the listening: the page never starts its own microphone, and
  the mic button and listening switch call `/api/desktop/command`.
- How the app writes down what's said (`speech_model`: a Whisper model, `service`,
  `dictation` or `off`) and the speech service picker live in `controls.js`, shared by
  Settings → Voice and the welcome screen (give each its own id `prefix`).

### The desktop app (`src/magpie/desktop`)

- Everything that touches the screen, the microphone or the operating system is imported
  lazily, so the logic runs (and is tested) anywhere; Windows-only calls (`ctypes.windll`,
  `winreg`, `msvcrt`) and Mac-only ones (AppKit, `osascript`) sit behind `sys.platform`
  checks with a quiet fallback elsewhere.
- On a Mac, AppKit may only be touched from the main thread: tray and window changes go
  through `ui.on_main`; pywebview and pystray share the main thread's event loop (the tray
  starts with `run_detached()` before `webview.start()`). Magpie is in the Dock only while
  its notes window is open.
- The app's pages (`static/desktop/`) reach the app through the server's
  `/api/desktop/ui`, and the app drives them with `window.run_js()`. Don't use pywebview's
  `js_api` or `evaluate_js`: both need `eval`, which the CSP refuses (and must keep refusing).
- The floating bar and the reminders must never take the keyboard from what the user is
  doing: show, hide and move them through `ui.Floating` (Windows API calls, or the
  NSWindow's `orderFrontRegardless` on a Mac), not pywebview's `show()`.
- Server code calls into the app with `asyncio.to_thread` (the app may call the server back
  through `LocalClient`, which would deadlock the event loop).
- Nothing may stop the app: the tray, the microphone, the speech model, Docker and updates
  can all fail; log it, show it (tray tooltip, bar message) and carry on.
- A voice note is never lost to Whisper: the chosen model, then the quick one, then the
  listener's words, whenever Whisper is missing, fails or runs past its time budget.
  Whisper's models aren't in the installer (they'd make every update GBs); `Whisper`
  downloads them once into the app's `models` folder, the quick one (`small.en`, also the
  bar's live words) first. Tests use stand-ins (`FakeTranscriber`, `LiveModel`, `load=`,
  `download=`): never download a model in tests. `MAGPIE_WHISPER_MODEL` points the
  self-test at a model (and the quick one next to it).
- Words from the bar's `LiveWriter` must never show once its note has ended or been typed
  over: `_live_words` checks it's still the current writer, under `_words_lock`.
- `--self-test` is what the App workflow checks; extend it when you add something the
  installed app needs (a module, a page, a data file in `packaging/bundle.py`).

### Copy and docs

- Plain, friendly sentences in sentence case. Say what happened and what to do next.
- Magpie is English-only. Use English names and places in copy, examples, sample data and
  tests (James, Mum, London), and keep the AI's output and speech recognition in English.
- When behaviour changes, update the README or the relevant `docs/` page, `docs/SPEC.md`
  if it changes the product, and `CHANGELOG.md`.

## Working with the Claude API

All Claude calls live in `brain/claude.py`; other providers go through
`brain/openai_compat.py`, and both must accept the same inputs and return the same schemas.
Rules that keep Claude working:

- Default model `claude-haiku-5-5` (Settings → AI, or `MAGPIE_MODEL`). Use exact model ids;
  never add date suffixes. Sonnet 5.5 and Opus 5.5 are the other choices offered.
- Structured data comes from **structured outputs** (`output_config.format` with a JSON
  schema made by `anthropic.transform_schema` from a Pydantic model in `schemas.py`).
  Field descriptions in `schemas.py` are written for Claude; keep them precise.
- **Don't** force tools with `tool_choice` `any`/`tool`, prefill the assistant turn, send
  `thinking: {type: "disabled"}` or a `budget_tokens` thinking budget: current models
  reject all of these with a 400. Control depth with `output_config.effort` (set
  explicitly; defaults are per task in `DEFAULT_EFFORT`).
- Always check `stop_reason` before reading content (`refusal`, `max_tokens`,
  `pause_turn`). Server tools can pause; `_run` resumes them. Models in
  `DYNAMIC_WEB_TOOL_MODELS` get `web_search_20260209`/`web_fetch_20260209`; others, Haiku
  included, get `web_search_20250305`/`web_fetch_20250910`. Only models in
  `FALLBACK_MODELS` get server-side fallbacks.
- System prompts are static so they can be prompt-cached; put the time, user preferences
  and note text in the user message. Don't put timestamps in system prompts.
- Treat fetched pages and forwarded text as untrusted data in prompts.
- When you change what the AI returns: update the schema, the prompt in `prompts.py`,
  `FakeBrain` (so demos and tests keep working), the consumers, and add or adjust a contract
  test in `tests/test_claude_brain.py` (and `tests/test_providers.py` for other services)
  that asserts the request shape.
- Other services vary: `OpenAICompatibleBrain` tries `json_schema` (strict only for OpenAI),
  then `json_object`, then the schema in the prompt, strips `<think>` blocks and code
  fences, and asks once for a repair. Keep that chain when you touch it.
- `CodexBrain` runs Codex as a program: the prompt goes on **stdin, never on the command
  line** (an npm install is a batch file on Windows), the arguments are fixed apart from a
  validated model name, it runs in an empty temporary folder, read-only, with its shell
  tools off, and the answer's schema goes in a file (`--output-schema`). Never use a Claude
  subscription the same way: Anthropic doesn't allow apps to offer claude.ai sign-in or
  its limits. Claude without a key is the artifact (`artifact/magpie.html`).

## Testing

- `tests/conftest.py` provides `settings` (temp data dir, fake brain, Australia/Brisbane
  time zone, so local time never equals UTC), `brain` (a `ScriptedBrain`: set `brain.next_triage = make_triage(...)` to script
  Claude), `router` (programmable mock internet for every outbound HTTP call), `core`
  (a full `Magpie` container) and `client` (a logged-in `TestClient` that sends the CSRF
  header).
- Async tests are plain `async def` (pytest-asyncio auto mode). Call
  `await core.pipeline.drain()` to wait for background work.
- Test behaviour through public functions or the API, with realistic inputs: time zones,
  DST, past times, repeated rules, unreachable sites, private addresses, refusals.
- Never call a real AI API, Telegram or the web in tests.
- For UI changes, run the app with `MAGPIE_BRAIN=fake` and the seed script, check phone
  (390 px) and desktop widths in light and dark mode, and use the keyboard only once.

## Recipes

**Add a notification channel.** Write a class in `notify/channels.py` with `name`,
`label`, a `configured` property and `async send(notification)` (raise on failure). Add its
settings to `config.py` and `docs/configuration.md`, register it in
`core.py`, add help text in `CHANNEL_HELP` (`settings.js`), and test the payload with the
`router` fixture (`tests/test_notify.py`).

**Make the AI extract something new.** Add the field (with a description) to the schema in
`schemas.py`, explain it in `TRIAGE_SYSTEM`, set it in `FakeBrain.triage`, store it (a
column needs a migration plus `NOTE_FIELDS`), show it in `note-dialog.js`, and test it.

**Add an API endpoint.** Add it to `api.py` with the `User` dependency (authenticated;
cookie sessions also need the CSRF header automatically). Use Pydantic bodies and return
models or plain dicts. Add a test in `tests/test_api.py`. Public endpoints (no login) need
a strong reason and a capability token, like the calendar feed or action links.

**Add a dashboard setting.** Server-wide: a `Prefs` field and a control in
`settings.js` (saved with `savePrefs`). Device-only: a key in `state.js` `DEFAULTS` and
`saveLocal`.

## Security rules

- Never log or return secrets (API keys, tokens, the password), except the generated
  password, which is printed on purpose at start-up so the owner can log in.
- Keep `ensure_public()` on every server-side fetch of a user-supplied URL, including
  redirects.
- State-changing endpoints must require auth; keep the `X-Magpie` CSRF check for cookie
  sessions; one-tap links stay signed, scoped and POST-only for changes.
- Magpie has no password by default: the dashboard answers only requests whose `Host` is a
  local name (localhost, 127.0.0.1, ::1), which blocks DNS rebinding; the CSRF header is
  still required, and the Bearer token works from anywhere. Keep both. A password only
  exists when `MAGPIE_PASSWORD` is set (a server other devices reach), or for an older
  install that other devices can reach, whose made-up password is kept
  (`auth.open_to_other_devices`). The app's server listens on 127.0.0.1 only.
- The app updates itself only from `latest.json` on this repository's GitHub releases,
  with the download's size and SHA-256 checked before it's used (`desktop/update.py`).
- AI keys, speech service keys and the email app password are secrets: never return them
  from the API (only whether one is saved), never log a service's answer to a refused key
  (it can quote the key), and keep a saved key tied to the address it was entered for.
- Don't loosen the Content-Security-Policy or add third-party scripts.
- Escape everything sent to Telegram with `telegram_api.escape` (HTML parse mode).

## Definition of done

- [ ] Tests added or updated, and every check under [Commands](#commands) passes
- [ ] Works without an API key (`MAGPIE_BRAIN=fake`, and “no AI”) and degrades gracefully when the AI errors
- [ ] Keyboard and screen-reader friendly; checked at phone and desktop widths
- [ ] Docs, `docs/SPEC.md` (if product behaviour changed) and `CHANGELOG.md` updated
