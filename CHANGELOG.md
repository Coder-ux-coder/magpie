# Changelog

All notable changes to Magpie are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

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
