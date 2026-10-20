# Magpie: product spec (the refined prompt)

Magpie started as a voice note: a rambling, transcribed description of an
app. This document turns that note into a precise brief. It is the prompt the
project was built from, and the reference for what "done" means. If you change
behaviour, update this file too.

## The original idea, condensed

> I say "notes on", the mic turns on, and my voice is transcribed into a note.
> Claude takes it from there. If I say "remind me at 5 pm", Claude messages me
> at 5 pm. Claude also decides on its own when to ping me, or whether a note
> needs a ping at all. My notes live in a dashboard where I can review them, and
> Claude writes short reviews of the things I save, like YouTube videos and
> articles. Make it open source, ready for anyone to run, with an AGENTS.md.

## The refined prompt

Build **Magpie**, an open-source, self-hosted, voice-first notes app with
Claude as its brain.

**Who it is for.** One person per install. They capture thoughts on the move,
mostly by voice and often messily, and want those thoughts organised, followed
up and summarised without filing anything by hand.

### Core loop

1. **Capture.** Saying "notes on" starts a note. The browser transcribes speech
   live, and "notes off" (or a few seconds of silence) saves it. The same note
   can come from a push-to-talk button, typed text, a link shared from the phone's
   share sheet, a Telegram text or voice message, a Siri Shortcut, or the HTTP API.
2. **Understand.** Claude cleans the transcript (filler words, recognition
   mistakes, false starts) without changing its meaning. It gives the note a
   title, a kind (task, idea, link, journal, meeting, reference, list or note)
   and tags, and reads any dates and times in it.
3. **Decide.** Claude decides whether and when to ping:
   - **explicit**: the user named a time ("at 5 pm", "tomorrow morning", "in 20
     minutes", "every Monday at 9"). Schedule exactly that, in the user's time zone.
   - **suggested**: no time was named, but the note is actionable and
     time-sensitive (a bill, an appointment, a call to return). Pick a sensible
     time and say why.
   - **digest**: worth seeing again but not urgent. Mention it in the next
     daily digest.
   - **none**: nothing to follow up (journal entries, reference facts). File it.

   A user setting (quiet, balanced or proactive) controls how readily Claude
   suggests pings it was not asked for.
4. **Notify.** Reminders go out through Telegram (recommended), ntfy, browser
   push, email or a webhook, with Done and Snooze actions where the channel
   supports them.
5. **Review.** When a note contains a YouTube video or an article, Magpie
   fetches the transcript or text, and Claude writes a short review: TL;DR, key
   points, a verdict on whether it is worth the time, and who it is for. When the
   user only names something by voice ("that Paul Graham essay about doing things
   that don't scale"), Claude finds it with web search first.
6. **Dashboard.** Browse, search, filter, edit and act on notes. See upcoming
   reminders, reviews, and daily and weekly digests, and ask questions across all
   of your notes.

7. **Make it yours.** Setup takes one command and three answers (API key, password,
   time zone), and the key can also be pasted in the dashboard. Users can give Claude
   standing instructions ("tag shop notes #shop", "remind me 30 minutes before
   meetings"), choose the snooze length, and adjust everything else in Settings.

### Constraints

- **Easy to run.** One process, one SQLite file, Docker or Python 3.11+. The
  only required external service is the Anthropic API; Telegram, speech-to-text
  and push are optional.
- **Secure by default.** The dashboard is password protected, integrations use a
  revocable API token, CSRF is blocked, fetching a link cannot reach private
  network addresses, and all data stays on the user's own server.
- **Robust.** Notes are never lost: without an API key, or when Claude is
  unavailable, notes are saved and processed later. Reminders survive restarts,
  respect time zones and DST, and retry when delivery fails.
- **Phone-first UI.** It is responsive and accessible, installs as a PWA, works
  as a share target, and supports dark mode.
- **English only.** The app, Claude's titles, replies, reviews and digests, and
  speech recognition are all in English; the user picks their accent (UK, US,
  Australian and others).
- **Documented for humans and agents.** A README for people and an AGENTS.md for
  coding agents, plus tests and CI.

### Out of scope for v1

Multi-user accounts, native mobile apps, WhatsApp (its business API needs Meta
verification and paid templates), calendar sync, on-device language models and
image notes.

### Acceptance criteria

1. "notes on, remind me to call James at 5 pm, notes off" creates a note titled
   like "Call James", a reminder at 17:00 local time, and at 17:00 a message with
   Done and Snooze buttons.
2. Sharing a YouTube link produces a note with a short review within about a
   minute, when the video has a transcript.
3. "I should read that Paul Graham essay on doing things that don't scale" (web
   search on) gets linked to the essay, which is then reviewed.
4. "Bought the train tickets, the trip is on the 14th" is filed with no
   reminder, or with one suggested shortly before the trip in proactive mode.
5. The daily digest at 08:00 lists today's reminders, notes waiting for the
   digest and fresh reviews.
6. `docker compose up` with `ANTHROPIC_API_KEY` and `MAGPIE_PASSWORD` in `.env`
   gives a working app at http://localhost:8000.
7. With no API key, notes are still saved and are processed once a key is added.

### Design decisions

| Question | Decision | Why |
| --- | --- | --- |
| Wake phrase | Browser Web Speech API in continuous mode, fuzzy-matching "notes on" and "notes off" | Free, real-time and needs no server audio. Falls back to recording plus server speech-to-text. |
| Phone capture | Telegram voice notes, an iOS Shortcut ("Hey Siri, notes on") and the PWA | Background listening is not possible in a mobile browser, so these use the OS's own voice entry points. |
| Speech-to-text on the server | Any OpenAI-compatible `/audio/transcriptions` endpoint | One integration covers OpenAI, Groq and self-hosted Whisper servers. Claude does not take audio input. |
| Model | `claude-opus-5-5` with structured outputs and per-task effort | Most capable default; `MAGPIE_MODEL` switches it. |
| Reminder times | Claude returns local wall-clock times; the server converts them with the user's IANA time zone | Avoids offset and DST mistakes in model output. |
| Storage | SQLite with FTS5 | Zero setup, fast full-text search, one file to back up. |
| Frontend | Vanilla JS PWA, no build step | Anyone can run, read and modify it. |
