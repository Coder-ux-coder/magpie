# The Magpie guide

Everything about using the Magpie app, for people who have never installed a program like it.
[The README](../README.md) has the two-minute install; this page has the rest.

- [Every day](#every-day)
- [How Magpie hears you](#how-magpie-hears-you)
- [Choosing the AI](#choosing-the-ai)
- [Reminders on your phone](#reminders-on-your-phone)
- [Mac and Linux](#mac-and-linux)
- [Moving from Magpie in Docker](#moving-from-magpie-in-docker)
- [If something doesn't work](#if-something-doesnt-work)

## Every day

- **Magpie waits by the clock**, in the notification area (click the **^** arrow if you
  don't see it, and drag the icon onto the taskbar to keep it in view). The icon turns grey
  when Magpie isn't listening, and gets a red dot while it takes a note.
- **Say “notes on”** at the start of a sentence, in any program, and a small bar appears at
  the bottom of the screen with your words as you speak. It doesn't take the keyboard from
  what you're doing. Say **“notes off”** to save the note (or stop talking for a few
  seconds, or press **Save**), or **“cancel note”** to throw it away; **Undo** brings it
  back. “Notes on” in the middle of a sentence (“I took notes on the meeting”) doesn't count.
- **When you finish**, the bar says *Writing it down…* for a moment while Magpie writes the
  note down again, accurately (see [below](#how-magpie-hears-you)), then *Saved* with what
  the AI made of it.
- **Click the bar to type** into it: fix a word, or write a whole note. Enter saves,
  Shift+Enter starts a new line, Esc cancels.
- **Right-click the Magpie icon** for its menu: *Open Magpie* (your notes and settings),
  *Start a voice note*, *Write a note*, *Listen for “notes on”* (untick it to stop
  listening), *Start Magpie with Windows*, and *Quit Magpie*.
- **Reminders pop up** in the bottom-right corner of the screen with *Done*, *Snooze* and
  *Open*, whether Magpie's window is open or not.
- **Closing the notes window** keeps Magpie running and listening. *Quit Magpie* stops it
  until you open it again from the Start menu or the Desktop.
- **Updates are automatic.** Magpie checks for a new version every few hours, downloads it,
  checks it, and installs it when you're not in the middle of a note; it's back by the clock
  a few seconds later. Your notes and settings are kept.
- **Your notes** are in `%LOCALAPPDATA%\Magpie` (paste that into File Explorer's address
  bar), and Magpie's log is in its `logs` folder. Settings → Data exports them any time.
- **To uninstall**, use Windows Settings → Apps → Installed apps → Magpie. Your notes stay
  in `%LOCALAPPDATA%\Magpie` until you delete that folder.

## How Magpie hears you

Two speech recognisers work together, both on your computer: **no audio ever leaves it**.

1. A small, quick one listens all the time for “notes on”, and shows your words in the bar
   as you speak. It's light enough to run all day, but it mishears quite a lot.
2. When you finish a note, **Whisper**, a much more accurate one, writes the whole note down
   again from its sound, with punctuation and capitals, and copes with accents far better.
   That's the text that's saved.

Magpie downloads Whisper once, in the background, the first time it starts (about 480 MB),
into `%LOCALAPPDATA%\Magpie\models`; updates keep it. Until it's there, notes keep the quick
recogniser's words. You can watch the download, and choose, in **Settings → Voice →
Writing down what you say**:

| Choice | What it's like |
| --- | --- |
| **Accurate** (the default) | Whisper's *small* English model. A few seconds a note on most computers. |
| **Most accurate** | Whisper's *large turbo* model: best with strong accents and noisy rooms. A 1.6 GB download, and slower; use it on a recent computer. |
| **Quick** | Just the words shown as you speak. Instant, but with more mistakes. |

If Whisper ever takes too long or fails, Magpie saves the quick recogniser's words instead,
so a note is never lost. Speak naturally; you can pause mid-sentence. The AI tidies up
whatever is left (“at 4, no, 5 pm” becomes 5 pm).

## Choosing the AI

The AI files each note (title, kind, category, tags), decides whether and when to remind
you, answers *Ask*, and reviews the videos and articles you save. Choose it on the welcome
screen or in **Settings → AI**; you can change it any time, and your notes wait until it's
connected.

| AI | What you need |
| --- | --- |
| **Claude** (the default: Haiku 5.5) | An API key from [console.anthropic.com](https://console.anthropic.com/settings/keys). Paste it in; it's checked first. Claude can also search the web for links you mention. |
| **Codex** (no key) | Codex, OpenAI's command-line tool, on this computer, signed in with your ChatGPT account. See below. |
| **OpenAI**, **Gemini**, **OpenRouter** | An API key from that service (Gemini has free keys with a daily limit). |
| **Ollama** (free, private) | [Ollama](https://ollama.com/download) on this computer, and a model: `ollama pull llama3.2`. |
| **No AI** | Nothing. Simple rules pick out times (“at 5 pm”, “every Monday”), lists and links. |

### Codex: your ChatGPT sign-in, no key

If you use ChatGPT (Plus, Pro, Business and so on), Magpie can use it through **Codex**, so
you don't need an API key:

1. Install Codex. In PowerShell:

   ```powershell
   powershell -ExecutionPolicy ByPass -c "irm https://chatgpt.com/codex/install.ps1 | iex"
   ```

   (Or `npm install -g @openai/codex`; see [github.com/openai/codex](https://github.com/openai/codex).)
2. Sign in: open a new PowerShell window, run `codex login`, and choose **Sign in with
   ChatGPT**.
3. In Magpie, choose **Codex** in Settings → AI and press **Use Codex**. Magpie checks that
   Codex is signed in and answers.

Magpie runs Codex for each note, in an empty folder, read-only and without its tools, so it
answers only from the note; Magpie never sees your sign-in. It's slower than the others (a
few seconds a note), it can't review PDFs or search the web for Magpie, and your Codex
plan's limits apply. Leave *Model* empty to use Codex's own default, or name one.

### Claude without an API key: Magpie in Claude

Apps outside Claude, this one included, can't use a Claude subscription: they need an API
key. So there's also a version of Magpie that runs **inside Claude**, as an artifact, where
Claude files your notes with **your own Claude account**: no key, nothing to install. It
has your categories, reminders while it's open (with Google Calendar and Outlook links),
*My day* and *Ask*, and its notes export to a file the app imports (Settings → Data).

It's [`artifact/magpie.html`](../artifact/magpie.html). To make your own copy, ask Claude
Code: *“Publish `artifact/magpie.html` as an artifact with the db, user, sample, downloads
and permissions capabilities.”* Then open the link it gives you in Claude.

## Reminders on your phone

- **Email** is the easiest: in Magpie open **Settings → Reminders → Email**, enter your
  address and an app password (the form links to where Gmail, Outlook, Yahoo and iCloud make
  one), and Magpie sends a test.
- **Telegram** gets reminders with Done and Snooze buttons, and takes notes too, by text or
  voice. Magpie fetches your messages from Telegram, so nothing has to reach your computer.
  See [Telegram](integrations.md#telegram).
- Magpie itself only opens on the computer it runs on.

More ways in and out (Siri Shortcuts, the calendar feed, Claude Desktop through MCP, the
HTTP API) are in [integrations](integrations.md).

## Mac and Linux

The app is made for Windows. On a Mac or Linux it runs from Python 3.11 or newer (Linux
needs GTK or Qt for its windows):

```bash
python3 -m pip install "magpie-notes[desktop] @ https://github.com/Coder-ux-coder/magpie/archive/refs/heads/main.zip"
python3 -m magpie desktop
```

It works the same way, except that it doesn't update itself: run the first line again for
a new version.

## Moving from Magpie in Docker

Install the app. The first time it starts, it brings over your notes and settings (the AI
key, Telegram, email; but no password, because the app has none), and stops the Docker
version so reminders don't arrive twice. If Docker Desktop isn't running, Magpie starts it
for this and says so by the clock; it takes a minute or two. The old Magpie's data stays in
Docker, untouched; once you're happy, delete it in Docker Desktop (*Containers* and
*Volumes*, both called *magpie*).

## If something doesn't work

| What you see | What to do |
| --- | --- |
| *Windows protected your PC* when opening MagpieSetup.exe | Click **More info**, then **Run anyway**. Magpie isn't signed by a company. |
| No Magpie icon by the clock | Click the **^** arrow next to the clock; drag the icon onto the taskbar to keep it there. Not there either? Open Magpie from the Start menu. |
| Magpie doesn't hear “notes on” | Point at the Magpie icon: its tooltip says what's wrong. *Hears only silence*: in Windows Settings → Privacy & security → Microphone, turn on *Let desktop apps access your microphone*, and check that the microphone isn't muted. Say “notes on” at the start of a sentence, or right-click the icon → *Start a voice note*. |
| Saved notes have mistakes in them | Check Settings → Voice: *Ready* means Whisper writes notes down. *Downloading* means it isn't there yet (notes keep the quick words meanwhile). With a strong accent, try *Most accurate*. |
| The download of the speech model fails | Settings → Voice says why. Magpie tries again every 15 minutes. It comes from Hugging Face (huggingface.co), which some networks block. |
| *Writing it down…* takes long | Whisper is working on a slow computer. Choose *Accurate* rather than *Most accurate*, or *Quick*. |
| *Codex isn't installed* or *isn't signed in* | Install Codex and run `codex login` in a new PowerShell window (see [Codex](#codex-your-chatgpt-sign-in-no-key)), then choose Codex again. |
| *Codex has reached its usage limit* | Your ChatGPT plan's Codex limit: notes wait and are filed when it resets. |
| *Magpie couldn't open its windows* | Install Microsoft Edge WebView2 from <https://developer.microsoft.com/microsoft-edge/webview2/>, then open Magpie again. |
| Notes are saved but not filed | Choose your AI, or add its key, in Settings → AI. |
| Reminders arrive twice | An old Magpie is still running in Docker. Stop it in Docker Desktop (*Containers* → *magpie* → stop). |
| *Magpie is starting* when you open it | It's still getting ready, often bringing over notes from an old Magpie: give it a minute. |
| Email reminders don't arrive | Settings → Reminders → Email → *Send a test*. The message says what went wrong; usually the app password. Check your spam folder once. |

Still stuck? Magpie's log, `%LOCALAPPDATA%\Magpie\logs\magpie.log`, says what went wrong.
To report a problem, [open an issue](https://github.com/Coder-ux-coder/magpie/issues) with
the last lines of the log.
