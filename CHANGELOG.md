# Changelog

All notable changes to Magpie are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

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
