# AGENTS.md

Guidance for AI coding agents (Claude Code, Codex, Cursor, …) and humans working on
Magpie. Read this before changing code. The product brief is [docs/SPEC.md](docs/SPEC.md);
the design is in [docs/architecture.md](docs/architecture.md).

## What Magpie is

A self-hosted, single-user, voice-first notes app. Notes arrive from the browser (wake
phrase “notes on”, mic, typing, share target), Telegram, Siri Shortcuts, the HTTP API, the
CLI or MCP. Claude files each note (clean text, title, kind, tags), decides whether and when
to remind the user, and reviews linked videos and articles. A scheduler delivers reminders
and digests through Telegram, web push, ntfy, email or webhooks.

One Python process (FastAPI + background asyncio tasks), one SQLite file, and a
no-build-step vanilla-JS PWA.

## Commands

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev,mcp]"

# Run locally without an API key (offline stand-in for Claude) and with sample data
MAGPIE_DATA_DIR=dev-data python scripts/seed_demo.py
MAGPIE_DATA_DIR=dev-data MAGPIE_BRAIN=fake MAGPIE_PASSWORD=dev magpie serve   # http://localhost:8000

# Checks: run all of these before you finish
pytest -q
ruff check src tests scripts
ruff format --check src tests scripts     # `ruff format src tests scripts` to fix
node --test "tests/js/*.test.mjs"
for f in src/magpie/web/static/js/*.js src/magpie/web/sw.js; do node --check "$f"; done
docker build -t magpie .                   # when you touch the Dockerfile or dependencies
```

The tests are offline and need no keys. CI (`.github/workflows/ci.yml`) runs the same checks
on Python 3.11 to 3.13, plus a Docker build and smoke test.

## Repository map

```
src/magpie/
  config.py        Settings (env vars, MAGPIE_ prefix) and Prefs (dashboard, stored in DB)
  db.py            SQLite connection, numbered MIGRATIONS, FTS5 index
  store.py         ALL SQL lives here; returns models from models.py
  models.py        Note, Link, Reminder, Digest + enums
  schemas.py       Structured outputs Claude returns (Triage, LinkReview, FoundLink, Answer)
  brain/
    base.py        Brain protocol, Context, DigestData, BrainError types
    claude.py      ClaudeBrain: every Claude API call
    prompts.py     System prompts and the formatting of app data for Claude
    fake.py        FakeBrain: rule-based stand-in for tests and demos
  pipeline.py      Triage → reminders → links → reviews; retries; events for listeners
  reminders.py     Plans → reminder rows (time zones, RRULE, quiet mode); done/snooze/…
  scheduler.py     Fires due reminders, retries delivery, sends digests on time
  digest.py        Daily digest and weekly review (with a plain-text fallback)
  notify/          Notifier + channels (telegram, webpush, ntfy, email, webhook)
  telegram.py      The bot: long polling, pairing, commands, inline buttons
  telegram_api.py  Minimal Bot API client
  content/         URL extraction, SSRF guard (links.py), fetching pages/transcripts (fetch.py)
  stt.py           OpenAI-compatible speech-to-text client
  api.py           REST API + public routes (/a/ action links, /calendar/*.ics, /healthz)
  app.py           FastAPI factory, middleware (sessions, security headers), static files
  auth.py          Generated secrets, signed action links, login rate limiter
  core.py          Magpie container: builds and wires services, starts background tasks
  export.py ics.py JSON/Markdown export + import; iCalendar feed
  cli.py           `magpie serve|add|digest|doctor|password|token|backup|setup|mcp`
  mcp_server.py    MCP server (optional extra) that talks to the HTTP API
  web/             index.html, sw.js, manifest, static/{css,js,icons}
tests/             pytest suite (conftest.py has the fixtures) + tests/js (node:test)
scripts/           seed_demo.py
docs/              SPEC, architecture, configuration, deployment, integrations
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
- **Settings vs Prefs:** secrets and infrastructure are `Settings` (environment, restart to
  change). Anything the user adjusts in the dashboard is a `Prefs` field; add it in
  `config.py`, give it a safe default, and expose it in `web/static/js/settings.js`.
- Background work goes through `Pipeline.spawn()` so it is tracked, can be drained in tests,
  and is cancelled on shutdown. Long-running loops are started with `Magpie._supervise()`,
  which restarts them after an unexpected error: a loop must never die silently.
- Robustness is part of every change: anything from Claude, Telegram, the network, the
  database or a user can be malformed. Catch it where it enters, turn it into a clear
  state or message (a failed note, a skipped row, a 422), and add a test with the bad input.
  Repeat rules go through `validate_rrule()`, which refuses rules dateutil can't evaluate
  quickly.
- Errors shown to users are plain sentences (“Claude couldn't process this note: …”).

### Web app (`src/magpie/web`)

- Plain ES modules, **no build step, no frameworks, no CDNs** (the CSP blocks them anyway).
- Build DOM with `h()` from `ui.js`. **Never use `innerHTML`** with data. Note text, reviews
  and anything from Claude are untrusted. Use `renderMarkdown()` for Claude's Markdown.
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

### Copy and docs

- Plain, friendly sentences in sentence case. Say what happened and what to do next.
- Magpie is English-only. Use English names and places in copy, examples, sample data and
  tests (James, Mum, London), and keep Claude's output and speech recognition in English.
- When behaviour changes, update the README or the relevant `docs/` page, `docs/SPEC.md`
  if it changes the product, and `CHANGELOG.md`.

## Working with the Claude API

All Claude calls live in `brain/claude.py`. Rules that keep it working:

- Default model `claude-opus-5-5` (configurable via `MAGPIE_MODEL`). Use exact model ids;
  never add date suffixes.
- Structured data comes from **structured outputs** (`output_config.format` with a JSON
  schema made by `anthropic.transform_schema` from a Pydantic model in `schemas.py`).
  Field descriptions in `schemas.py` are written for Claude; keep them precise.
- **Don't** force tools with `tool_choice` `any`/`tool`, prefill the assistant turn, send
  `thinking: {type: "disabled"}` or a `budget_tokens` thinking budget: current models
  reject all of these with a 400. Control depth with `output_config.effort` (set
  explicitly; defaults are per task in `DEFAULT_EFFORT`).
- Always check `stop_reason` before reading content (`refusal`, `max_tokens`,
  `pause_turn`). Server tools (`web_search_20260209`, `web_fetch_20260209`) can pause; `_run`
  resumes them.
- System prompts are static so they can be prompt-cached; put the time, user preferences
  and note text in the user message. Don't put timestamps in system prompts.
- Treat fetched pages and forwarded text as untrusted data in prompts.
- When you change what Claude returns: update the schema, the prompt in `prompts.py`,
  `FakeBrain` (so demos and tests keep working), the consumers, and add or adjust a contract
  test in `tests/test_claude_brain.py` that asserts the request shape.

## Testing

- `tests/conftest.py` provides `settings` (temp data dir, fake brain, America/New_York
  time zone, so local time never equals UTC), `brain` (a `ScriptedBrain`: set `brain.next_triage = make_triage(...)` to script
  Claude), `router` (programmable mock internet for every outbound HTTP call), `core`
  (a full `Magpie` container) and `client` (a logged-in `TestClient` that sends the CSRF
  header).
- Async tests are plain `async def` (pytest-asyncio auto mode). Call
  `await core.pipeline.drain()` to wait for background work.
- Test behaviour through public functions or the API, with realistic inputs: time zones,
  DST, past times, repeated rules, unreachable sites, private addresses, refusals.
- Never call the real Claude API, Telegram or the web in tests.
- For UI changes, run the app with `MAGPIE_BRAIN=fake` and the seed script, check phone
  (390 px) and desktop widths in light and dark mode, and use the keyboard only once.

## Recipes

**Add a notification channel.** Write a class in `notify/channels.py` with `name`,
`label`, a `configured` property and `async send(notification)` (raise on failure). Add its
settings to `config.py`, `.env.example` and `docs/configuration.md`, register it in
`core.py`, add help text in `CHANNEL_HELP` (`settings.js`), and test the payload with the
`router` fixture (`tests/test_notify.py`).

**Make Claude extract something new.** Add the field (with a description) to the schema in
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
- Don't loosen the Content-Security-Policy or add third-party scripts.
- Escape everything sent to Telegram with `telegram_api.escape` (HTML parse mode).

## Definition of done

- [ ] Tests added or updated, and every check under [Commands](#commands) passes
- [ ] Works without an API key (`MAGPIE_BRAIN=fake`) and degrades gracefully when Claude errors
- [ ] Keyboard and screen-reader friendly; checked at phone and desktop widths
- [ ] Docs, `.env.example`, `docs/SPEC.md` (if product behaviour changed) and `CHANGELOG.md` updated
