# The Magpie guide

Everything about using the Magpie app on Windows and on a Mac, for people who have never
installed a program like it. [The README](../README.md#install) has the two-minute install;
this page has the rest.

- [Every day](#every-day)
- [How Magpie hears you](#how-magpie-hears-you)
- [Choosing the AI](#choosing-the-ai)
- [Updates](#updates)
- [Reminders on your phone](#reminders-on-your-phone)
- [Where things are, and removing Magpie](#where-things-are-and-removing-magpie)
- [Moving from Magpie in Docker](#moving-from-magpie-in-docker)
- [If something doesn't work](#if-something-doesnt-work)

## Every day

- **Magpie waits in the background**: by the clock on Windows (click the **^** arrow if you
  don't see it, and drag the icon onto the taskbar to keep it in view), in the menu bar at
  the top of the screen on a Mac. The icon turns grey when Magpie isn't listening, and gets
  a red dot while it takes a note.
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
- **Magpie's menu** (right-click the icon on Windows, click it on a Mac): *Open Magpie* (your
  notes and settings), *Start a voice note*, *Write a note*, *Listen for “notes on”* (untick
  it to stop listening), *Start Magpie with Windows* (or *with your Mac*), *Check for
  updates* and *Quit Magpie*.
- **Reminders pop up** in the bottom-right corner of the screen with *Done*, *Snooze* and
  *Open*, whether Magpie's window is open or not.
- **Closing the notes window** keeps Magpie running and listening. *Quit Magpie* stops it
  until you open it again (from the Start menu or the Desktop on Windows, from Applications
  on a Mac).

## How Magpie hears you

Everything happens on your computer: **no audio ever leaves it**.

1. A small, quick recogniser listens all the time for “notes on”. It's light enough to run
   all day, but it mishears a lot, so before a note starts, a fuller recogniser listens to
   the same moment again: only when both heard “notes on” does the bar appear.
2. While you speak, **Whisper**, a far more accurate recogniser, writes down what you've
   said so far, again every second or so, and the bar shows its words. The last few words
   come first from the quick recogniser, in lighter letters, until Whisper catches up.
3. When you finish, Whisper's most accurate model writes the whole note down once more,
   with punctuation and capitals, and copes with accents far better. That's the text that's
   saved.

Magpie downloads Whisper's models once, in the background, the first time it starts, into
its folder; updates keep them. The smaller one (480 MB) comes first and does both jobs
until the most accurate one (1.6 GB) is there; before that, the bar and your notes have the
quick recogniser's words. You can watch the download, and choose, in **Settings → Voice →
Writing down what you say**:

| Choice | What it's like |
| --- | --- |
| **Most accurate** (the default) | Whisper's *large turbo* model: the best with accents and noisy rooms. It takes a few seconds a note, longer on an older computer. |
| **Faster** | Whisper's *small* English model, for older computers: quicker, with a few more mistakes. |
| **Off** | Just the quick recogniser's words. Instant, but often wrong. |

If the chosen model ever fails, Magpie writes the note down with the faster one, and if
that fails too, it saves the quick recogniser's words, so a note is never lost. Speak
naturally; you can pause mid-sentence. The AI tidies up whatever is left (“at 4, no, 5 pm”
becomes 5 pm).

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

1. Install Codex. On Windows, in PowerShell:

   ```powershell
   powershell -ExecutionPolicy ByPass -c "irm https://chatgpt.com/codex/install.ps1 | iex"
   ```

   On a Mac, in Terminal:

   ```sh
   curl -fsSL https://chatgpt.com/codex/install.sh | sh
   ```

   (Or see [github.com/openai/codex](https://github.com/openai/codex) for other ways.)
2. Sign in: open a new PowerShell or Terminal window, run `codex login`, and choose **Sign
   in with ChatGPT**.
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

## Updates

Magpie updates itself. Every few hours it checks for a new version, downloads it, checks it
against the release, and installs it when you're not in the middle of a note; it's back a
few seconds later, with your notes and settings.

To update right away, open **Settings → Updates**: *Check for updates* looks now, and
*Update now* installs a new version straight away. *Check for updates* is in Magpie's menu
too, and tells you what it found.

On a Mac, Magpie can only update itself from Applications (where the installer puts it). A
copy run from Python doesn't update itself: run its install line again.

## Reminders on your phone

- **Email** is the easiest: in Magpie open **Settings → Reminders → Email**, enter your
  address and an app password (the form links to where Gmail, Outlook, Yahoo and iCloud make
  one), and Magpie sends a test.
- **Telegram** gets reminders with Done and Snooze buttons, and takes notes too, by text or
  voice. Magpie fetches your messages from Telegram, so nothing has to reach your computer.
  See [Telegram](integrations.md#telegram).
- Magpie itself only opens on the computer it runs on.

More ways in and out (Claude Desktop through MCP, webhooks, the HTTP API) are in
[integrations](integrations.md).

## Where things are, and removing Magpie

| | Windows | Mac |
| --- | --- | --- |
| Your notes, settings and the log (`logs`) | `%LOCALAPPDATA%\Magpie` (paste it into File Explorer's address bar) | `~/Library/Application Support/Magpie` (in Finder: Go → Go to Folder) |
| The program | `%LOCALAPPDATA%\Programs\Magpie` | `/Applications/Magpie.app` |
| Removing it | Windows Settings → Apps → Installed apps → Magpie | Untick *Start Magpie with your Mac* in its menu, quit it, and drag it from Applications to the Bin |

Removing Magpie keeps your notes in its folder until you delete that folder. Settings →
Data exports them any time.

## Moving from Magpie in Docker

Install the app. The first time it starts, it brings over your notes and settings (the AI
key, Telegram, email; but no password, because the app has none), and stops the Docker
version so reminders don't arrive twice. If Docker Desktop isn't running, Magpie starts it
for this and says so; it takes a minute or two. The old Magpie's data stays in Docker,
untouched; once you're happy, delete it in Docker Desktop (*Containers* and *Volumes*, both
called *magpie*).

## If something doesn't work

| What you see | What to do |
| --- | --- |
| *Windows protected your PC* when opening MagpieSetup.exe | Click **More info**, then **Run anyway**. Magpie isn't signed by a company. |
| A Mac says Magpie *can't be opened* or *is damaged* | It was downloaded with a web browser. Install it with the Terminal line in the README instead, which doesn't have this problem. |
| No Magpie icon by the clock (Windows) | Click the **^** arrow next to the clock; drag the icon onto the taskbar to keep it there. Not there either? Open Magpie from the Start menu. |
| No Magpie icon in the menu bar (Mac) | A full menu bar hides icons behind the camera notch: quit a menu bar app or two. Not there either? Open Magpie from Applications. |
| Magpie doesn't hear “notes on” | Point at Magpie's icon (or open its menu): it says what's wrong. *Hears only silence*: on Windows, Settings → Privacy & security → Microphone → *Let desktop apps access your microphone*; on a Mac, System Settings → Privacy & Security → Microphone → Magpie. Check the microphone isn't muted. Say “notes on” at the start of a sentence, or use *Start a voice note* in its menu. |
| A note starts when you didn't say “notes on” | Magpie checks with a second recogniser before it starts a note, so this should be rare. If it still happens, choose a wake phrase that sounds like nothing else you say, such as “hello magpie” (Settings → Voice). |
| Saved notes have mistakes in them | Check Settings → Voice: *Ready* means Whisper writes notes down. While it's *Downloading*, notes keep the quick words or the faster model's. Choose *Most accurate* (the default), and speak towards the microphone. |
| The download of the speech model fails | Settings → Voice says why. Magpie tries again every 15 minutes. It comes from Hugging Face (huggingface.co), which some networks block. |
| *Writing it down…* takes long | Whisper is working on a slow computer. Choose *Faster* rather than *Most accurate*. |
| *Codex isn't installed* or *isn't signed in* | Install Codex and run `codex login` in a new window (see [Codex](#codex-your-chatgpt-sign-in-no-key)), then choose Codex again. |
| *Codex has reached its usage limit* | Your ChatGPT plan's Codex limit: notes wait and are filed when it resets. |
| An update doesn't arrive | Settings → Updates → *Check for updates* says what's wrong. On a Mac, Magpie has to be in Applications. |
| *Magpie couldn't open its windows* (Windows) | Install Microsoft Edge WebView2 from <https://developer.microsoft.com/microsoft-edge/webview2/>, then open Magpie again. |
| Notes are saved but not filed | Choose your AI, or add its key, in Settings → AI. |
| Reminders arrive twice | An old Magpie is still running in Docker. Stop it in Docker Desktop (*Containers* → *magpie* → stop). |
| *Magpie is starting* when you open it | It's still getting ready, often bringing over notes from an old Magpie: give it a minute. |
| Email reminders don't arrive | Settings → Reminders → Email → *Send a test*. The message says what went wrong; usually the app password. Check your spam folder once. |

Still stuck? Magpie's log, `magpie.log` in the `logs` folder of Magpie's folder (above), says
what went wrong. To report a problem,
[open an issue](https://github.com/Coder-ux-coder/magpie/issues) with its last lines.
