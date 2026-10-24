# Changelog

All notable changes to Magpie are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.7.0] - 2026-10-24

### Added

- **Accurate words in the bar as you speak.** Whisper now writes down what you've said so
  far while you're still talking, again every second or so, so the bar shows its words
  rather than the quick listener's guesses. The last few words come from the listener, in
  lighter letters, until Whisper catches up.

### Changed

- **The most accurate speech model, on computers quick enough for it.** The new default,
  *Automatic*, writes notes down with Whisper's large turbo model, which is much better
  with accents, on a computer with 8 or more processor threads (most from the last few
  years, every Mac with Apple silicon), and with the small model on others. Magpie
  downloads the small model first (480 MB); it writes the words shown as you speak, and
  writes notes down too until the large one (1.6 GB) is there. Installs that had the small
  model move to *Automatic* once; Settings → Voice has every choice.
- When the chosen model can't write a note down (not enough memory, say), the faster one
  does, before Magpie falls back to the listener's words.
- Quiet microphones are brought up before Whisper hears them, and sound from microphones
  that only work at 44.1 or 48 kHz is filtered properly before it's turned into 16 kHz.

### Fixed

- **Notes no longer start when you didn't say “notes on”.** The always-on recogniser only
  knows the command word, so it heard it in all sorts of things (“no, it's on”). Magpie now
  waits until it's sure, then has a full recogniser listen to the same moment again, and
  only starts a note when that one heard “notes on” too.

## [0.6.0] - 2026-10-24

### Added

- **Magpie for Mac** (Apple silicon, macOS 14 or newer): paste one line into Terminal
  (`curl -fsSL https://raw.githubusercontent.com/Coder-ux-coder/magpie/main/install.sh | sh`)
  and Magpie is in Applications and running. It waits in the menu bar, starts when you sign
  in, listens for “notes on” in any app, shows the floating bar without taking the
  keyboard, writes notes down with Whisper, and pops up reminders. It's in the Dock only
  while its notes window is open. The release has `Magpie-mac.zip` next to
  `MagpieSetup.exe`.
- **The Mac app updates itself** like the Windows one: it downloads the new version,
  checks its size and SHA-256, and once you're not taking a note swaps in the new
  Magpie.app (putting the old one back if that fails) and opens it again.
- **An update button**: Settings → Updates shows the version and what's happening, with
  *Check for updates* and *Update now*. Magpie's menu has *Check for updates* too, and says
  what it found.

### Changed

- **The repository is just the app.** The Docker setup (Dockerfile, Compose files, Caddy,
  the image workflow, the setup scripts) and its docs are gone, with the community
  templates. Magpie in Docker no longer gets new versions: install the app, which brings
  its notes and settings over. Advanced settings for the app go in `magpie.env` in its
  folder ([settings](docs/configuration.md)).
- The *Windows app* workflow is now *App*: it builds, tests and installs the app on a real
  Windows and a real Mac, and publishes one release with both. Test results show up on
  the run's summary page (`packaging/report.py`).
- On a Mac, the microphone help points to System Settings, the app in front is kept with a
  note, and reminders make a sound.

## [0.5.0] - 2026-10-23

### Added

- **Far more accurate voice notes.** When a note ends, the app writes it down again with
  **Whisper** (faster-whisper), offline on your computer: punctuation, capitals, and much
  better with accents. The bar says *Writing it down…* meanwhile. The quick listener still
  spots “notes on” and shows your words as you speak; if Whisper fails or takes too long,
  its words are saved instead, so no note is lost. Whisper's model (480 MB) downloads once,
  in the background, on the app's first start, and retries if the download fails.
- Settings → Voice → **Writing down what you say**: *Accurate* (Whisper small, the
  default), *Most accurate* (Whisper large turbo, best with strong accents; 1.6 GB), or
  *Quick* (the listener's own words), with the download's progress.
- **Codex as the AI, with no API key**: choose *Codex* in Settings → AI and Magpie uses
  Codex, OpenAI's command-line tool, as it's signed in on this computer (with ChatGPT).
  Each request runs once, read-only and without Codex's tools, in an empty folder; the
  note goes in on stdin. `MAGPIE_BRAIN=codex` and `MAGPIE_CODEX_PATH` set it on a server.
- `install.ps1`: one PowerShell line installs (or updates) the app, checks the download's
  SHA-256, and waits until Magpie answers. The README has a section for AI agents setting
  Magpie up for someone: install, choose the AI through the API, check it listens.
- [The guide](docs/guide.md): everyday use, how Magpie hears you, choosing the AI, phones,
  and fixes for common problems.

### Changed

- Claude without an API key points to **Magpie in Claude** (`artifact/magpie.html`),
  which runs on your own Claude account, from Settings → AI and the README.
- The app's self-test checks that Whisper writes down the test recording; the Windows
  build downloads Whisper's model for it and checks the installed app's speech state.
- Settings → Voice no longer shows the browser's accent setting in the app, which hears
  every accent the same way.

## [0.4.1] - 2026-10-23

### Changed

- A password made up by a version before 0.4 now also goes away by itself on an install
  that only opens on its own computer. It only stays where other devices can reach
  Magpie (`MAGPIE_BIND` other than `127.0.0.1`, `MAGPIE_DOMAIN`, or a public
  `MAGPIE_BASE_URL`), until the setup runs again.
- The Windows build reports each self-test check, and anything the installed app logged
  as a warning, on the workflow run's summary.

## [0.4.0] - 2026-10-23

### Added

- **Magpie is an app for Windows.** Download `MagpieSetup.exe` from the latest release and
  double-click it: nothing to answer, no administrator rights, no Docker, no password. It
  starts with Windows, waits by the clock, and listens for **“notes on”** all the time, in
  any program. A small bar floats up at the bottom of the screen with your words as you
  speak, without taking the keyboard from what you're doing; “notes off”, a pause or Save
  saves the note, “cancel note” discards it (with Undo), and a click on the bar lets you
  type. Listening happens offline on your computer (Vosk); no audio leaves it.
- Reminders **pop up in the corner of the screen** from the app, with Done, Snooze and
  Open (a new `desktop` notification channel, with a test button).
- The tray icon's menu opens your notes, starts a voice note or a typed one, turns
  listening off and on, and turns starting with Windows off and on.
- **The app updates itself** from GitHub releases: it checks every few hours, checks the
  download's size and SHA-256 against the release, and installs it when no note is being
  taken.
- **Moving from Docker:** the app's first start brings over the notes and settings of a
  Magpie running in Docker (but no password), then stops it and its sign-in shortcut so
  reminders don't arrive twice. Docker's data is left untouched.
- The dashboard knows when the app runs it: the mic button and the listening switch drive
  the app, its status line says what the app is doing, and Settings and the welcome screen
  show the app's pop-ups. Docker installs on Windows suggest the app.
- `magpie desktop` runs the app from a Python install (the new `desktop` extra), on a Mac
  or Linux too; `--self-test` checks an installation without a screen or microphone.
- The **Windows app** workflow builds the installer on Windows, runs the built app's
  self-test (a real window, and a note taken from a recording of Windows' own voice),
  installs it, checks the installed app and its uninstaller, and publishes a release for
  each new version.

### Changed

- **No passwords.** Magpie has no password by default and only answers on its own
  computer. The setup asks nothing (`setup.sh`, `setup-docker.cmd`, `magpie setup`), and
  running it again retires a password an older version made up. A server that other
  devices reach can still set `MAGPIE_PASSWORD` (`magpie setup --password`). Installs
  that already used a made-up password keep it until the setup runs again, so updating
  never unlocks a server whose port is open to the network.
- `setup.cmd` now installs the Windows app; the Docker setup for Windows is
  `setup-docker.cmd`.

## [0.3.0] - 2026-10-22

### Added

- **A voice bar at the bottom of every view**, like a dictation tool: say “notes on” (or
  press the mic, or type), watch the words appear and edit them, then say “notes off” or
  press Save. Cancel, Esc or “cancel note” discards the note with Undo. Magpie listens for
  the command word as soon as it opens (once the microphone is allowed), only one window
  listens at a time, and the bar can pop out into a small always-on-top window (Chrome and
  Edge). An Install button makes Magpie an app with its own window.
- **Any AI, or none.** Settings → AI chooses Claude (Haiku 5.5 by default; Sonnet 5.5 and
  Opus 5.5 too), ChatGPT, Gemini, OpenRouter, Ollama on your own computer, any
  OpenAI-compatible service, or no AI (simple rules). Keys are checked before they're
  saved, never shown again, and only sent to the address they were entered for. On the
  server: `MAGPIE_BRAIN`, `MAGPIE_AI_API_KEY`, `MAGPIE_AI_BASE_URL`.
- **A welcome screen** for new users: choose the AI, turn on pop-ups and email, set the
  command word and make categories. It can be skipped and opened again from Settings.
- **Your own categories**, each with a colour, in your order (drag or arrows). They're tabs
  next to the inbox with counts; the AI files notes into them; a note written in a
  category's tab stays there; drag a note onto a tab (or onto Done or Archive) to move it,
  with Undo. Colour-coded cards, and categories travel with exports.
- **Magpie learns from your changes:** moving a note to another category or changing its
  kind becomes a lesson sent with new notes (the last 40; see and forget them in Settings).
- Every note remembers the tab it was written in (shown in the note, and given to the AI as
  a hint), and the API accepts `category_id` and `captured_in`.
- Titles can be edited in the note window.
- **Email reminders set up in Magpie**: an address and an app password (Gmail, Outlook,
  Yahoo and iCloud are filled in), checked before saving, with plain-English errors. The
  subject says what's due and when: “⏰ Call James · due today 5:00 PM”.
- **Pop-ups**: Settings → Reminders → *Turn on pop-ups here* shows each reminder as a
  notification on this computer or phone, with Done and Snooze.
- **No password needed** on your own computer: `MAGPIE_LOGIN=false` (the setup's default)
  opens Magpie straight away and only answers on localhost.
- **Automatic updates** for Docker installs: an `updates` service checks for a new image
  every hour, restarts Magpie on it, and rolls back if it doesn't start
  (`MAGPIE_AUTO_UPDATE`, `MAGPIE_VERSION`). Open windows load the new version when nobody is
  using them, or show a Reload banner.
- A Magpie icon (Desktop and Start menu, Applications, or the applications menu) that opens
  Magpie in its own window, starting Docker first if needed; optional start at sign-in.
- **Magpie in Claude** (`artifact/magpie.html`): Magpie as a Claude artifact, where your
  own Claude account files the notes, with no API key. Its exports import into a
  self-hosted Magpie with their categories, and the other way round.
- Following the computer's time zone: a computer switches Magpie's zone to its own when it
  changes (Settings → Time).
- JSON webhooks include `due` for reminders.
- `setup.cmd`: double-click setup for Windows (`./setup.sh` doesn't run in PowerShell).
- A step-by-step [getting started](docs/getting-started.md) guide for new users, with fixes
  for common problems.
- The setup scripts wait for Magpie to start and open it in the browser.

### Changed

- The default Claude model is `claude-haiku-5-5`: fast and low-cost for short notes.
- The setup asks only for a password (Enter for none) and your time zone; the AI is chosen
  on the welcome screen. Rerunning it keeps an existing password (and opens the port to
  your network for it); type `none` to stop using one.
- Settings is reorganised: AI, Reminders (pop-ups, email, Telegram, …), Categories, Voice,
  Time, Display, Integrations, Data, Privacy & account.
- Messages name the AI in use instead of always saying Claude.
- Tabs and other touch targets are at least 44 px tall.
- The Docker port is published on `127.0.0.1` unless `MAGPIE_BIND` says otherwise.
- When the setup can't tell your time zone (Windows, containers), it leaves it to your
  browser instead of assuming UTC.
- Docker Compose uses the fixed project name `magpie`, so your notes stay in the same volume
  whatever the folder is called (for example after unzipping a newer version).
- Magpie is now English-only. Claude writes titles, replies, reviews and digests in English;
  the browser always listens and reads aloud in English, even when it is set to another
  language; Settings → Voice offers English accents (UK, US, Australia, Canada, Ireland,
  New Zealand, South Africa); server transcription defaults to `MAGPIE_STT_LANGUAGE=en`.
  A saved voice language that isn't English falls back to *Automatic*.
- Examples, sample data and screenshots use English names and places.

### Fixed

- Notes could be lost or saved twice when Save, Enter and “notes off” came together; the bar
  now saves exactly what it shows, once, and puts the text back if saving fails.
- “Notes off” said while the browser was still unsure of the words didn't stop recording,
  and words settled after a save made a second note.
- “Notes of” and “notes in” no longer start a recording by mistake.
- Notes were hard to delete and a recording hard to cancel: every note card now has a
  Delete button, Cancel is always shown while recording, and both have Undo.

## [0.2.0] - 2026-10-19

### Added

- One-step setup: `./setup.sh` (Docker) and `magpie setup` ask for the API key, password
  and time zone and write `.env`; the key can also be pasted in Settings → Claude, where
  Magpie checks it with Anthropic and starts filing waiting notes.
- New installs adopt the browser's time zone the first time the dashboard opens.
- “Your rules for Claude”: standing instructions used when filing notes, writing digests
  and answering questions.
- A snooze length setting used by every Snooze button.
- Notes sent from the dashboard carry a client id, so a retry after a lost answer is never
  saved twice; `POST /api/notes` accepts `client_id`.

### Changed

- Snooze and Tomorrow postpone a reminder that isn't due yet from its due time, so they
  never move it earlier.
- Validation errors from the API are one readable sentence (`detail` is a string).
- Telegram `/today` lists all of today's scheduled reminders, including one due a moment
  ago that hasn't been sent yet.

### Fixed

- Reliability: background loops (scheduler, Telegram) restart after any unexpected error;
  one failing reminder no longer blocks the others or the digests; a note or link can no
  longer be left stuck “processing”; channels that hang time out.
- Repeat rules: `INTERVAL=0`, more-than-hourly and impossible rules (“February 30th”) are
  refused instead of freezing the server; `COUNT` now ends a series.
- Telegram: strangers got no reply for 10 minutes after a reboot; polling survives network
  errors, 409/429/5xx and odd updates; messages Telegram can't parse are sent as plain text;
  long messages split safely; old buttons still work.
- Text with half a surrogate pair, impossible ids, huge offsets, malformed URLs, unknown
  charsets, damaged database rows or settings, and bad imports no longer cause errors;
  imports are all-or-nothing per note and skip `javascript:` links.
- Dashboard: notes are no longer lost on a server error, an expired session or blocked
  storage (they wait in the outbox, or go back into the text box); logging in again no
  longer doubles every action; stale responses no longer overwrite the view; the 30-second
  refresh keeps your scroll position and focus; Ask answers appear after navigating away;
  double clicks no longer duplicate reminders or pages; editing saves properly; the app
  opens offline for capture; updates are never mixed with cached old scripts.
- MCP tool errors now reach Claude instead of a generic failure.
- The offline digest describes times relative to the digest's own day.
- Tests no longer depend on the date or time of day they run at.
- Clear start-up messages for invalid settings, an unwritable data folder or a damaged
  database; `magpie backup` never overwrites a file.

## [0.1.0] - 2026-10-19

First release.

### Added

- Voice capture in the browser: hands-free “notes on” / “notes off” wake and stop phrases
  (any language), push-to-talk, dictation into the text box, and a server speech-to-text
  fallback for browsers without speech recognition.
- Claude triage of every note: cleaned text, title, kind, tags, and reminder decisions
  (explicit, suggested, digest or none) with a quiet / balanced / proactive setting.
- Reminders with time zone and DST handling, repeating reminders, snooze, retries and late
  delivery after downtime.
- Reviews of YouTube videos, articles and PDFs; web search for things mentioned without a link.
- Daily digest, weekly review, and Ask (questions answered from your notes).
- Notifications through Telegram, browser push, ntfy, email and Slack/Discord webhooks,
  with one-tap Done and Snooze.
- Telegram bot for text and voice notes, with secure pairing and commands.
- Installable PWA with share target, offline outbox, keyboard shortcuts, read-aloud,
  text size, high contrast, dark mode and right-to-left text support.
- Integrations: Siri Shortcut guide, bookmarklet, iCalendar feed, HTTP API, MCP server
  for Claude Desktop and Claude Code, JSON/Markdown export and import, and a CLI
  (`serve`, `add`, `digest`, `doctor`, `password`, `token`, `backup`, `mcp`).
- Docker image with an optional Caddy HTTPS profile, CI, and an offline test suite.
