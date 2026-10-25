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

Build **Magpie**, an open-source, voice-first notes app that lives on the user's
own computer, with an AI as its brain: Claude by default, any other provider the
user prefers, or none. On Windows it is an ordinary app: installed with one
download, started with the computer, always listening, and never asking for a
password.

**Who it is for.** One person per install. They capture thoughts on the move,
mostly by voice and often messily, and want those thoughts organised, followed
up and summarised without filing anything by hand.

### Core loop

1. **Capture.** On Windows and Mac, the Magpie app listens for "notes on" all the time,
   offline and in any program, without the user turning anything on: saying it
   (at the start of what they say) brings up a small floating bar at the bottom
   of the screen with the words as they are spoken, how long the note has run and a
   meter of the microphone's level, without taking the keyboard from what the user
   is doing. "notes off", Save or a few seconds of silence
   saves the note; "cancel note" or Cancel discards it, with Undo; clicking the
   bar lets the user type into it. Something that only sounds like "notes on"
   must not start a note: a second, fuller recogniser hears the same moment again
   and must agree. While the user speaks, the bar shows the words an accurate
   offline recogniser (Whisper) writes down, a second or so behind; when the note
   ends, Whisper writes it down again from its sound, coping with accents and adding
   punctuation, with its most accurate model when the computer is quick enough, and
   that is saved. The faster model and then the quick listener's words are the
   fallbacks, so a note is never lost. The user can instead choose a speech service of
   their own with their key (OpenAI, Groq, Deepgram, ElevenLabs, AssemblyAI, Gladia or
   any OpenAI-compatible server), which then writes each note down (and Telegram voice
   messages), or their own dictation app (Glaido, Wispr Flow, voice typing): "notes on"
   then opens the bar with the keyboard for that app to type into, and the note is saved
   when its words end with the stop phrase, after a pause, or with Enter. Names and words
   the user lists are listened out for by Whisper and the services that take them. The
   choice is on the welcome screen too. On Windows a keyboard shortcut (Ctrl+Alt+Space,
   or another the user picks in Settings → Voice) takes a voice note in any program: a tap
   starts it and another saves it, or the user holds it down while talking and lets go to
   save. In the notes window a voice bar sits at the
   bottom of every view and works the same way. The same note can come from the mic
   button, typed text, a Telegram text or voice message, Claude through MCP, or the
   HTTP API.
2. **Understand.** The AI cleans the transcript (filler words, recognition
   mistakes, false starts) without changing its meaning, and writes a list said in one
   go (“shopping list milk, eggs and bread”) with a line for each thing. It gives the note a
   title (which the user can edit), a kind (task, idea, link, journal, meeting,
   reference, list or note), one of the user's own colour-coded categories, and
   tags, and reads any dates and times in it. A note written while a category is
   open stays in that category, and every note remembers the view it was written
   in. When the user moves a note or changes its kind, Magpie keeps the
   correction as a lesson for similar notes.
3. **Decide.** The AI decides whether and when to ping:
   - **explicit**: the user named a time ("at 5 pm", "tomorrow morning", "in 20
     minutes", "every Monday at 9"). Schedule exactly that, in the user's time zone.
   - **suggested**: no time was named, but the note is actionable and
     time-sensitive (a bill, an appointment, a call to return). Pick a sensible
     time and say why.
   - **digest**: worth seeing again but not urgent. Mention it in the next
     daily digest.
   - **none**: nothing to follow up (journal entries, reference facts). File it.

   A user setting (quiet, balanced or proactive) controls how readily the AI
   suggests pings it was not asked for. Times follow the computer's clock.
4. **Notify.** Reminders pop up in the corner of the user's screen (from the
   app, or web push in a browser) and arrive by email ("⏰ Call James · due today
   5:00 PM"), set up from the dashboard; Telegram, ntfy and webhooks also work.
   Done and Snooze actions come with every channel that supports them.
5. **Review.** When a note contains a YouTube video or an article, Magpie
   fetches the transcript or text, and the AI writes a short review: TL;DR, key
   points, a verdict on whether it is worth the time, and who it is for. When the
   user only names something by voice ("that Paul Graham essay about doing things
   that don't scale"), Claude finds it with web search first.
6. **Dashboard.** Browse (the views and the user's categories in a sidebar, a
   drawer on narrow screens), search (with the words found marked), filter, edit,
   delete (with Undo) and act on notes; drag them onto categories. The Inbox starts
   with what's due today, then the notes by day (today, yesterday, the last 7 days,
   earlier), pinned ones first. A list note is a checklist to tick off, and its card
   says what's left. See upcoming reminders, reviews, and daily and weekly digests,
   and ask questions across all of your notes.
7. **Make it yours.** Installing asks nothing: on Windows one download, elsewhere
   one command, and the time zone follows the computer. A welcome screen then sets
   up the AI (provider, model, key), pop-ups and email, the command word and
   categories.
   Users can give the AI standing instructions ("tag shop notes #shop", "remind me
   30 minutes before meetings"), choose the snooze length, and adjust everything
   else in Settings.
8. **Stay current.** The app updates itself and keeps every note and setting: it
   installs a new release when no note is being taken, after checking it against the
   release's checksum, on Windows and on a Mac; an update button (Settings → Updates,
   and the app's menu) checks or installs right away. Moving from Magpie in Docker to
   the app brings the notes and settings along by itself.
9. **Without an API key.** A version of Magpie runs as a Claude artifact, where
   the user's own Claude account files the notes; its notes can be exported into a
   self-hosted Magpie. In the app, ChatGPT users can choose Codex, which runs on
   their own Codex sign-in, so no key is pasted anywhere.

### Constraints

- **Easy to run.** One installer (Windows) or one Terminal line (Mac); one process and
  one SQLite file (Python 3.11+ underneath). No external service is required: the AI can be Claude, any OpenAI-compatible
  service (OpenAI, Gemini, OpenRouter, Groq, a local Ollama) or simple rules, and
  Telegram, speech-to-text and email are optional.
- **No password, secure by default.** The dashboard only answers on the user's
  own computer (the app listens on 127.0.0.1, and every install refuses other
  host names, against DNS rebinding). Only a server that other devices reach is
  given a password. Integrations use a revocable API token, CSRF is
  blocked, fetching a link cannot reach private network addresses, keys are never
  shown again once saved, and all data stays on the user's own computer.
- **Robust.** Notes are never lost: without an AI, or when it is unavailable,
  notes are saved and processed later. Reminders survive restarts,
  respect time zones and DST, and retry when delivery fails.
- **Phone-first UI.** It is responsive and accessible, installs as a PWA, works
  as a share target, and supports dark mode.
- **English only.** The app, the AI's titles, replies, reviews and digests, and
  speech recognition are all in English; the user picks their accent (UK, US,
  Australian and others).
- **Documented for humans and agents.** A README for people and an AGENTS.md for
  coding agents, plus tests and CI.

### Out of scope for v1

Multi-user accounts, native mobile apps, WhatsApp (its business API needs Meta
verification and paid templates), calendar sync and image notes.

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
6. Installing MagpieSetup.exe (or running install.sh on a Mac), with nothing to answer
   and no password, gives a running app by the clock (or in the menu bar) that starts
   with the computer, and a welcome screen to choose the AI. Saying "notes on, call James, notes off" in another program shows
   the floating bar with the words and saves the note, without taking the keyboard
   from that program.
7. With no AI set up, notes are still saved and are processed once one is chosen;
   with "no AI", simple rules file them.
8. A note written while Work is open lands in Work; dragging a note onto Home in the
   sidebar moves it there (with Undo), and similar notes go to Home afterwards.
9. A reminder pops up on the computer and arrives as an email whose subject says
   what is due and when.
10. A new published version reaches an installed app within hours, or at once from
    the update button, without losing notes; the app never installs a download that
    doesn't match its release, and a Mac puts the old app back if the swap fails.
11. Installing the app on a computer that ran Magpie in Docker brings over its
    notes and settings and stops the Docker copy, so no reminder arrives twice.

### Design decisions

| Question | Decision | Why |
| --- | --- | --- |
| Wake phrase (app) | Vosk offline: a grammar recogniser that only knows the command word listens all the time, and a full recogniser checks what it settles on against the same audio; from the exact end of the command word (the last 20 seconds of audio are kept) a full recogniser follows the note, and a second grammar listens for the stop and cancel words | Private and free, cheap enough to run all day; a grammar alone hears its word in anything, so the check stops false starts; and the word timings put the start and end of a note exactly where they were said. |
| Writing notes down (app) | Whisper (faster-whisper): `small.en` writes the note so far every second or so while it's said, for the bar; `large-v3-turbo` writes the whole note once it ends on a computer with 8 or more threads (`auto`, the default; `small.en` otherwise). They're downloaded once, the small one first, not shipped | Far more accurate than the always-on recogniser, above all with accents, and offline; running only while a note is taken keeps listening cheap, and keeping the models out of the installer keeps updates small. |
| Wake phrase (browser) | Browser Web Speech API in continuous mode, fuzzy-matching "notes on" and "notes off" | Free, real-time and needs no server audio. Falls back to recording plus server speech-to-text. |
| The app | The same server runs inside the app (127.0.0.1); pywebview windows (WebView2, or WebKit on a Mac), a pystray tray or menu bar icon; PyInstaller makes the app, Inno Setup a per-user Windows installer, and a zip plus install.sh the Mac's Magpie.app; updates come from GitHub releases | One codebase for both systems; no admin rights; the windows are the web app's own pages. |
| Mac distribution | An ad-hoc signed Magpie.app for Apple silicon and macOS 14+, installed with a Terminal line (curl doesn't quarantine it), updated by swapping the bundle | No paid Apple developer account; Gatekeeper only stops apps a browser downloaded. |
| Phone capture | Telegram text and voice notes, and email for reminders | The app only answers on its own computer; Telegram and email reach the phone without opening it to the network. |
| Speech-to-text by a service of the user's own | OpenAI-compatible `/audio/transcriptions` (OpenAI, Groq, self-hosted servers), Deepgram, ElevenLabs, AssemblyAI and Gladia, chosen in Settings → Voice with the key kept like the AI's; `MAGPIE_STT_*` when none is chosen | People already pay for, or trust, a service that hears their accent well; Claude does not take audio input. |
| Model | `claude-haiku-5-5` with structured outputs and per-task effort; Sonnet 5.5 and Opus 5.5 selectable | Fast and low-cost for short notes; Settings → AI or `MAGPIE_MODEL` switches it. |
| Other AIs | One OpenAI-compatible client: JSON schema, then JSON mode, then the schema in the prompt, plus one repair round | Covers OpenAI, Gemini, OpenRouter, Groq, Ollama and others with one integration, whatever each supports. |
| Claude accounts | A Claude artifact version of Magpie | A self-hosted app can't use a Claude subscription (Anthropic doesn't allow apps to offer claude.ai sign-in or its limits); inside Claude, the user's own account files the notes. |
| ChatGPT accounts | Codex, run as a program (`codex exec`) where it's installed and signed in: read-only, without its tools, the note on stdin, the answer's shape as a JSON schema | No key in Magpie and no sign-in handled by Magpie; Codex's own plan and limits apply. |
| No password | Listening on 127.0.0.1 only, plus a Host-header check | Simplest for one person on one computer; safe against DNS rebinding. Only a server reachable by other devices sets one. |
| Updates | latest.json from the GitHub release names each system's download, size and SHA-256; it's installed when idle, or at once from the update button | Installs keep up without the user doing anything; notes live outside the program. |
| Reminder times | Claude returns local wall-clock times; the server converts them with the user's IANA time zone | Avoids offset and DST mistakes in model output. |
| Storage | SQLite with FTS5 | Zero setup, fast full-text search, one file to back up. |
| Frontend | Vanilla JS PWA, no build step | Anyone can run, read and modify it. |
