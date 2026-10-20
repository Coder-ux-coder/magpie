# Getting started

This guide is for anyone who has never run a program like Magpie before. It takes about
15 minutes, most of it waiting for downloads.

## What "running Magpie" means

Magpie is a small program that runs on **your own computer** (or a home server), not on a
website. Once it's running you use it in your web browser at <http://localhost:8000>, and on
your phone through Telegram or the browser.

A phone or tablet can't run Magpie itself; it needs a Windows, Mac or Linux computer that is
switched on. Your notes stay on that computer.

You need:

- **Docker Desktop**, a free program that runs Magpie in its own little box, so you don't
  have to install anything else.
- Optionally a **Claude API key**, which makes Magpie smart: it files your notes, decides when
  to remind you and reviews links. Get one at
  [console.anthropic.com](https://console.anthropic.com/settings/keys). You can also add it
  later, inside Magpie.

## Windows

1. **Install Docker Desktop** from
   [docker.com/products/docker-desktop](https://www.docker.com/products/docker-desktop/).
   Run the installer with the default options. If it asks to install or update WSL, say yes
   and restart the computer when it asks.
2. **Start Docker Desktop** from the Start menu and wait until it shows *Engine running*
   (the first start can take a minute). You can skip signing in.
3. **Download Magpie:**
   [magpie-main.zip](https://github.com/Coder-ux-coder/magpie/archive/refs/heads/main.zip).
   Open your Downloads folder, right-click the file and choose **Extract All**, then
   **Extract**.
4. Open the extracted **magpie-main** folder and **double-click `setup.cmd`**. (If Windows
   says it protected your PC, click **More info**, then **Run anyway**.)
5. A black window opens. The first time, it spends a few minutes building Magpie. Then it
   asks three things:
   - your **Claude API key**: paste it (it stays hidden as you type) and press Enter, or
     just press Enter to add it later;
   - a **password** for Magpie: type one, or press Enter and Magpie makes one and shows it
     to you. **Write it down**;
   - your **time zone**, such as `Europe/London`: press Enter to use your browser's.
6. Magpie opens in your browser. Log in with the password, then say or type your first note.

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

5. Answer the three questions (Claude key, password, time zone; see step 5 for Windows).
   Magpie opens in your browser. Log in and say or type your first note.

## Linux

Install [Docker Engine](https://docs.docker.com/engine/install/) and the Compose plugin,
then:

```bash
git clone https://github.com/Coder-ux-coder/magpie.git
cd magpie
sh setup.sh
```

## Every day

- **Magpie keeps running** in the background and starts again by itself whenever Docker
  starts. Bookmark <http://localhost:8000>.
- **To stop it**, open Docker Desktop → *Containers* and press stop next to *magpie*, or run
  `docker compose stop` in the Magpie folder. Start it again with the play button or
  `docker compose up -d`.
- **To change your answers** (key, password, time zone), run the setup again. Your notes
  are kept.
- **To update**, download the new ZIP, unzip it, copy your `.env` file from the old folder
  into the new one, and run the setup again. Your notes are kept.

## On your phone

- The easiest way is **Telegram**: send notes by text or voice and get reminders with Done
  and Snooze buttons. See [Telegram](integrations.md#telegram).
- On the same Wi-Fi you can open `http://<your computer's address>:8000` in the phone's
  browser to read and type notes. The microphone in a browser needs a secure (`https://`)
  address, which [deployment](deployment.md#https) explains, for example with Tailscale.

## If something doesn't work

| What you see | What to do |
| --- | --- |
| You pasted `./setup.sh` into Windows PowerShell and nothing happened | That line is for Mac and Linux. On Windows, double-click `setup.cmd` instead. |
| *docker is not recognized* or *command not found: docker* | Docker Desktop isn't installed, or the window was opened before installing it. Install it, then open a new window. |
| *Docker Desktop is installed but not running*, or *Cannot connect to the Docker daemon* | Start Docker Desktop and wait for *Engine running*, then try again. |
| Windows: Docker Desktop asks for WSL, or says virtualization is off | Follow its instructions to install WSL and restart. If it says virtualization is disabled, it must be switched on in the computer's BIOS settings (search the web for your computer model and "enable virtualization"). |
| *Bind for 0.0.0.0:8000 failed: port is already allocated* | Another program uses port 8000. Open `docker-compose.yml` in Notepad or TextEdit, change `"8000:8000"` to `"8080:8000"`, save, run the setup again and open <http://localhost:8080>. |
| *no configuration file provided* on Windows | The ZIP wasn't extracted. Right-click it → Extract All, and double-click `setup.cmd` in the extracted folder. |
| Mac: *permission denied: ./setup.sh* | Use `sh setup.sh` as shown above. |
| The browser says it can't reach the site | Give Magpie a few more seconds to start and reload. Still nothing? In the Magpie folder run `docker compose logs magpie` to see what it says. |
| You forgot the password | Run the setup again and choose a new one. |
| Notes say *Waiting for Claude* | Add your Claude API key in Settings → Claude. |

Still stuck? Run `docker compose exec magpie magpie doctor` in the Magpie folder: it checks the
setup and says what's missing.

## Without Docker

If you already use Python 3.11 or newer, you can run Magpie directly. Make a folder for it
first and always start Magpie from that folder, because your notes are stored there.

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

Leave that window open while you use Magpie, and open <http://localhost:8000>.
