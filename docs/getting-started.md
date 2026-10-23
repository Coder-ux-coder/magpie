# Getting started

This guide is for anyone who has never installed a program like Magpie before. On Windows it
takes a couple of minutes; on a Mac or Linux about 15, most of it waiting for downloads.

## What Magpie is

Magpie is a program that runs on **your own computer**, not on a website. Your notes stay
on that computer, and there's no account or password. On **Windows** it's an app you install
like any other: it starts with your computer, waits by the clock, and listens for “notes on”
whatever you're doing. On a **Mac or Linux** it runs in Docker and listens while its window
is open. On your phone you use it through email, Telegram or Siri.

Optionally Magpie uses an **AI**, which makes it smart: it files your notes, decides when to
remind you and reviews links. It works with Claude (Haiku 5.5 is the default), ChatGPT,
Google Gemini, OpenRouter or a free AI on your own computer (Ollama), and without any AI
too. You choose on Magpie's welcome screen the first time it opens.

## Windows

1. **Download [MagpieSetup.exe](https://github.com/Coder-ux-coder/magpie/releases/latest/download/MagpieSetup.exe).**
2. **Open it** from your Downloads folder. If Windows says *Windows protected your PC*,
   click **More info**, then **Run anyway**. (Windows says this about programs that aren't
   signed by a company.)
3. That's all you do: Magpie installs itself (there's nothing to answer and no
   administrator password), puts a Magpie icon on your Desktop and in the Start menu, and
   opens with the **welcome screen**. It walks you through the rest: which AI files your
   notes (paste its key there; it's checked first), reminders (press *Show a test pop-up*),
   your command word, and your own categories. Each step can be skipped and changed later
   in Settings.
4. **Say “notes on”**, then your note, then “notes off”. Try it in another program, too.

<p align="center"><img src="images/welcome-ai.png" width="60%" alt="The welcome screen's second step, Choose your AI: Claude is selected, with a box for the API key and the model claude-haiku-5-5"></p>

### Every day

- **Magpie waits by the clock**, in the notification area (click the **^** arrow if you
  don't see it, and drag the icon onto the taskbar to keep it in view). The icon turns grey
  when Magpie isn't listening, and gets a red dot while it takes a note.
- **Say “notes on”** at the start of a sentence, in any program, and a small bar appears at
  the bottom of the screen with your words as you speak. It doesn't take the keyboard from
  what you're doing. Say **“notes off”** to save the note (or stop talking for a few
  seconds, or press **Save**), or **“cancel note”** to throw it away; **Undo** brings it
  back. “Notes on” in the middle of a sentence (“I took notes on the meeting”) doesn't count.
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

### Used Magpie in Docker before?

Install the app as above. The first time it starts, it brings over your notes and your
settings (the AI key, Telegram, email; but no password, because the app has none), and stops
the Docker version so reminders don't arrive twice. If Docker Desktop isn't running, Magpie
starts it for this and says so by the clock; it takes a minute or two. The old Magpie's data
stays in Docker, untouched; once you're happy, you can delete it in Docker Desktop
(*Containers* and *Volumes*, both called *magpie*).

## Mac

1. **Install Docker Desktop** from
   [docker.com/products/docker-desktop](https://www.docker.com/products/docker-desktop/)
   (pick *Apple Silicon* or *Intel* to match your Mac: Apple menu → About This Mac). Open the
   downloaded file and drag Docker to Applications.
2. **Start Docker** from Applications and wait until the whale icon in the menu bar says
   *Docker Desktop is running*.
3. **Download Magpie:**
   [magpie-main.zip](https://github.com/Coder-ux-coder/magpie/archive/refs/heads/main.zip).
   Double-click it in Downloads to unzip it.
4. Open **Terminal** (press Cmd+Space, type *Terminal*, press Enter) and paste these two
   lines, pressing Enter after each:

   ```bash
   cd ~/Downloads/magpie-main
   sh setup.sh
   ```

5. The first time, it spends a few minutes downloading Magpie; there's nothing to answer.
   Magpie is added to your **Applications** folder, Launchpad and your login items, and
   opens in its own window with the welcome screen (step 3 for Windows). From now on, open
   Magpie from Applications or Launchpad.

## Linux

Install [Docker Engine](https://docs.docker.com/engine/install/) and the Compose plugin,
then:

```bash
git clone https://github.com/Coder-ux-coder/magpie.git
cd magpie
sh setup.sh
```

On a desktop, the setup adds Magpie to your applications menu and opens it when you log in.

## Every day with Docker (Mac, Linux and browsers)

- **The voice bar** sits at the bottom of Magpie. Say **“notes on”**, talk, and watch your
  words appear; fix anything by typing; then say **“notes off”** or press **Save**. Press
  **Cancel** (or Esc) to throw a note away, and **Undo** if you didn't mean to. You can
  also just type and press Enter (Shift+Enter starts a new line). Alt+N starts or stops the
  mic from anywhere in Magpie.
- Magpie **starts listening** for “notes on” when it opens, once you've allowed the
  microphone (the bar asks once). The options button on the bar changes the command and stop
  words, turns listening off, or lets you check each voice note before it's saved. While it
  listens, Chrome and Edge send the audio to their speech service.
- **Pop out** (the arrow on the bar, in Chrome and Edge) keeps the bar in a small window on
  top of your other programs.
- **Categories** are tabs next to the inbox. Write a note while a category's tab is open and
  it stays there; drag a note onto a tab to move it.
- **Open Magpie from its icon** (Applications or Launchpad on a Mac, the applications menu
  on Linux). It opens in its own window, and starts Docker and Magpie first if they aren't
  running. (Any browser works too, at <http://localhost:8000>.)
- **Magpie keeps running** in the background and starts again by itself whenever Docker
  starts, so reminders arrive even when its window is closed.
- **Updates are automatic.** Magpie checks for a new version every hour and restarts on it
  within a few seconds. Your notes and settings are kept, and if a new version doesn't start
  properly, Magpie goes back to the one that worked. To turn this off or pick a version, see
  [Updates](deployment.md#updates).
- **To stop it**, open Docker Desktop → *Containers* and press stop next to *magpie*, or run
  `docker compose stop` in the Magpie folder. Opening Magpie from its icon starts it again.
- **The time zone** follows your computer; Settings → Time changes it. Everything else, the
  AI included, is in Settings too.
- **To remove the icons**:
  - Mac: drag *Magpie* from Applications to the Bin, and remove it in System Settings →
    General → Login Items.
  - Linux: delete `~/.local/share/applications/magpie.desktop` and
    `~/.config/autostart/magpie.desktop`.

  Running the setup again puts them back.

## Installed Magpie in Docker before automatic updates?

Copies of Magpie set up in Docker before October 2026 don't update themselves yet. On
Windows, simply install the app (above). On a Mac or Linux, update once by hand, and Magpie
takes care of it from then on:

1. Download [magpie-main.zip](https://github.com/Coder-ux-coder/magpie/archive/refs/heads/main.zip)
   again, and unzip it over the old folder, replacing the files. Your settings are kept in
   the `.env` file, which isn't in the ZIP, so it stays as it is.
2. Run `sh setup.sh` again. Your notes, your AI key and your settings are kept. An older
   Magpie that asked for a password stops asking: from now on it opens straight away, on
   this computer only.

If you unzipped it somewhere new instead, copy the `.env` file from the old folder into the
new one before running the setup.

## On your phone

- The easiest way to get reminders on your phone is **email**: in Magpie open
  **Settings → Reminders → Email**, enter your address and an app password (the form links
  to where Gmail, Outlook, Yahoo and iCloud make one), and Magpie sends a test.
- **Telegram** also lets you send notes by text or voice, and gets reminders with Done and
  Snooze buttons. It works with the app as it is: Magpie fetches your messages from
  Telegram, so nothing has to reach your computer. See [Telegram](integrations.md#telegram).
- Magpie itself only opens on the computer it runs on. To open it from your phone too, run
  it on a server instead ([deployment](deployment.md)); a server that other devices can
  reach needs a secure address and a password.

## If something doesn't work

| What you see | What to do |
| --- | --- |
| *Windows protected your PC* when opening MagpieSetup.exe | Click **More info**, then **Run anyway**. |
| No Magpie icon by the clock | Click the **^** arrow next to the clock; drag the icon onto the taskbar to keep it there. Not there either? Open Magpie from the Start menu. |
| Magpie doesn't hear “notes on” | Point at the Magpie icon: its tooltip says what's wrong. *Hears only silence*: in Windows Settings → Privacy & security → Microphone, turn on *Let desktop apps access your microphone*, and check that the microphone isn't muted. Say “notes on” at the start of a sentence, or right-click the icon → *Start a voice note*. |
| The words in the bar are a little wrong | The speech model is small and works offline. Click the bar and fix them before saving; the AI tidies up notes too. Speaking a little slower helps. |
| *Magpie couldn't open its windows* | Install Microsoft Edge WebView2 from <https://developer.microsoft.com/microsoft-edge/webview2/>, then open Magpie again. |
| Reminders arrive twice | An old Magpie is still running in Docker. Stop it in Docker Desktop (*Containers* → *magpie* → stop), or turn off pop-ups in a browser that has them. |
| *Magpie is starting* when you open it | It's still getting ready, often bringing over notes from an old Magpie: give it a minute. |
| You pasted `./setup.sh` into Windows PowerShell and nothing happened | That line is for Mac and Linux. On Windows, install the app instead. |
| *docker is not recognized* or *command not found: docker* | Docker Desktop isn't installed, or the window was opened before installing it. Install it, then open a new window. |
| *Docker Desktop is installed but not running*, or *Cannot connect to the Docker daemon* | Start Docker Desktop and wait for *Engine running*, then try again. |
| *Bind for 127.0.0.1:8000 failed: port is already allocated* | Another program uses port 8000. Open the `.env` file in the Magpie folder with TextEdit, add the line `MAGPIE_HOST_PORT=8080`, save, and run the setup again. The Magpie icon then opens the new address by itself. |
| Mac: *permission denied: ./setup.sh* | Use `sh setup.sh` as shown above. |
| Magpie's window says it can't reach the site | Give Magpie a few more seconds to start and reload (Ctrl+R, or Cmd+R on a Mac). Still nothing? In the Magpie folder run `docker compose logs magpie` to see what it says. |
| Magpie in Docker didn't update | In the Magpie folder run `docker compose logs updates`: it says what happened. If you set Magpie up before automatic updates existed, see [above](#installed-magpie-in-docker-before-automatic-updates). |
| Notes are saved but not filed | Choose your AI, or add its key, in Settings → AI. |
| Email reminders don't arrive | Settings → Reminders → Email → *Send a test*. The message says what went wrong; usually the app password. Check your spam folder once. |
| No pop-ups appear (Docker) | Settings → Reminders → *Turn on pop-ups here*, and allow notifications when the browser asks. On Windows and Mac, also check that the browser may show notifications (Focus or Do Not Disturb can hide them). |

Still stuck? On Windows, Magpie's log (`%LOCALAPPDATA%\Magpie\logs\magpie.log`) says what
went wrong. With Docker, run `docker compose exec magpie magpie doctor` in the Magpie
folder: it checks the setup and says what's missing.

## Without Docker

If you already use Python 3.11 or newer, you can run Magpie directly.

**The app** (it works on a Mac and Linux too, but is newest there; on Linux it needs GTK
or Qt for its windows):

```bash
python3 -m pip install "magpie-notes[desktop] @ https://github.com/Coder-ux-coder/magpie/archive/refs/heads/main.zip"
python3 -m magpie desktop
```

The first start downloads the speech model (about 40 MB). The tray menu can make it start
with your computer.

**As a server** in a folder of its own (always start it from that folder, because your notes
are stored there):

```bash
# Windows (PowerShell)
mkdir magpie; cd magpie
py -m pip install https://github.com/Coder-ux-coder/magpie/archive/refs/heads/main.zip
py -m magpie setup
py -m magpie serve

# Mac or Linux
mkdir magpie && cd magpie
python3 -m pip install https://github.com/Coder-ux-coder/magpie/archive/refs/heads/main.zip
python3 -m magpie setup
python3 -m magpie serve
```

Leave that window open while you use Magpie, and open <http://localhost:8000>. Installed
this way, Magpie doesn't update itself: to update, run the same `pip install` line with
`--upgrade` added.
