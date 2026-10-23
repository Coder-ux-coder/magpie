"""The operating system: starting with the computer, one copy at a time, the active window.

Windows is the main platform; macOS and Linux get the same features where they have
an equivalent, and quietly skip them where they don't.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import plistlib
import shlex
import sys
from pathlib import Path
from typing import IO, Any

log = logging.getLogger(__name__)

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_ID = "Magpie"
LAUNCH_AGENT = "com.github.coder-ux-coder.magpie"


# ------------------------------------------------------------- start with the computer


def _autostart_file() -> Path | None:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCH_AGENT}.plist"
    if sys.platform.startswith("linux"):
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
        return base / "autostart" / "magpie.desktop"
    return None


def autostart_enabled() -> bool:
    if sys.platform == "win32":
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                winreg.QueryValueEx(key, APP_ID)
            return True
        except OSError:
            return False
    path = _autostart_file()
    return bool(path and path.exists())


def set_autostart(enabled: bool, command: list[str]) -> bool:
    """Start Magpie (in the background) when the user signs in. Returns True if it worked."""
    try:
        if sys.platform == "win32":
            import subprocess
            import winreg

            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                if enabled:
                    line = subprocess.list2cmdline(command)
                    winreg.SetValueEx(key, APP_ID, 0, winreg.REG_SZ, line)
                else:
                    with contextlib.suppress(FileNotFoundError):
                        winreg.DeleteValue(key, APP_ID)
            return True
        path = _autostart_file()
        if path is None:
            return False
        if not enabled:
            path.unlink(missing_ok=True)
            return True
        path.parent.mkdir(parents=True, exist_ok=True)
        if sys.platform == "darwin":
            plist = {"Label": LAUNCH_AGENT, "ProgramArguments": command, "RunAtLoad": True}
            path.write_bytes(plistlib.dumps(plist))
        else:
            path.write_text(
                "[Desktop Entry]\nType=Application\nName=Magpie\n"
                f"Exec={shlex.join(command)}\nX-GNOME-Autostart-enabled=true\n",
                encoding="utf-8",
            )
        return True
    except OSError:
        log.warning("Couldn't change starting with the computer", exc_info=True)
        return False


# ---------------------------------------------------------------------- one copy only


class InstanceLock:
    """Held for as long as Magpie runs, so a second copy can hand over and quit."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle: IO[str] | None = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+", encoding="utf-8")  # noqa: SIM115 (held open on purpose)
        try:
            if sys.platform == "win32":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self._handle = handle
        return True

    def release(self) -> None:
        handle, self._handle = self._handle, None
        if handle is not None:
            with contextlib.suppress(OSError):
                if sys.platform == "win32":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            handle.close()


def write_running(path: Path, **info: Any) -> None:
    with contextlib.suppress(OSError):
        path.write_text(json.dumps({"pid": os.getpid(), **info}), encoding="utf-8")


def read_running(path: Path) -> dict[str, Any]:
    try:
        info = json.loads(path.read_text(encoding="utf-8"))
        return info if isinstance(info, dict) else {}
    except (OSError, ValueError):
        return {}


# ------------------------------------------------------------- where the note was said


def active_window() -> str:
    """The window in front when the command word was heard, like “Budget.xlsx - Excel”.

    Windows only; elsewhere (and when it can't tell) an empty string.
    """
    if sys.platform != "win32":
        return ""
    try:
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return ""
        length = user32.GetWindowTextLengthW(hwnd)
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        return " ".join(buffer.value.split())[:60]
    except Exception:
        return ""


def beep() -> None:
    """A short, quiet sound for a reminder."""
    if sys.platform == "win32":
        with contextlib.suppress(Exception):
            import winsound

            winsound.MessageBeep(winsound.MB_ICONASTERISK)


def message_box(title: str, text: str) -> None:
    """A last-resort message when Magpie can't start its windows."""
    if sys.platform == "win32":
        with contextlib.suppress(Exception):
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, text, title, 0x40)
            return
    print(f"{title}: {text}", file=sys.stderr)
