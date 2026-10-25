"""A keyboard shortcut that takes a voice note in any program (Windows).

Windows tells Magpie when the shortcut is pressed (RegisterHotKey) on a thread of its own,
which runs that thread's message loop: the keys reach only Magpie, and only while it
runs. A tap starts a note and the next tap saves it; held down, the shortcut records
while the keys are held and saves the note when they're let go.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

log = logging.getLogger(__name__)

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_NOREPEAT = 0x0001, 0x0002, 0x0004, 0x4000
VK_SPACE = 0x20
WM_HOTKEY = 0x0312
HOTKEY_ID = 0x4D47
HOLD_SECONDS = 0.45  # held longer than this, the shortcut records while it's held
LONGEST_HOLD = 600.0  # a key that seems stuck down stops being watched
POLL_SECONDS = 0.03


@dataclass(frozen=True)
class Shortcut:
    name: str
    label: str
    modifiers: int
    key: int  # the virtual key


SHORTCUTS: dict[str, Shortcut] = {
    shortcut.name: shortcut
    for shortcut in (
        Shortcut("ctrl+alt+space", "Ctrl+Alt+Space", MOD_CONTROL | MOD_ALT, VK_SPACE),
        Shortcut("ctrl+shift+space", "Ctrl+Shift+Space", MOD_CONTROL | MOD_SHIFT, VK_SPACE),
        Shortcut("ctrl+alt+n", "Ctrl+Alt+N", MOD_CONTROL | MOD_ALT, ord("N")),
    )
}
DEFAULT_SHORTCUT = "ctrl+alt+space"
OFF = "off"


def supported() -> bool:
    return sys.platform == "win32"


class Keys(Protocol):
    """The calls a shortcut needs from the system (stand-ins in tests)."""

    def prepare(self) -> int: ...  # this thread's id, once it has a message queue

    def register(self, modifiers: int, key: int) -> bool: ...

    def unregister(self) -> None: ...

    def wait(self) -> bool: ...  # True: the shortcut was pressed; False: time to stop

    def held(self, key: int) -> bool: ...

    def stop(self, thread_id: int) -> None: ...  # from another thread: wait() returns False


class Hotkey:
    """Listens for one shortcut at a time; ``use()`` changes it, ``stop()`` ends it."""

    def __init__(
        self,
        on_press: Callable[[], None],
        on_release: Callable[[], None],
        *,
        on_hold: Callable[[], None] | None = None,
        keys: Callable[[], Keys] | None = None,
    ) -> None:
        self.on_press = on_press  # the shortcut went down
        self.on_hold = on_hold or (lambda: None)  # still down: it's being held
        self.on_release = on_release  # let go after being held: the note is said
        self._keys = keys
        self._lock = threading.RLock()  # Settings and the app's start can both change it
        self._listener: _Listener | None = None
        self.shortcut: Shortcut | None = None
        self.problem = ""

    @property
    def available(self) -> bool:
        return self._keys is not None or supported()

    def use(self, name: str) -> str:
        """Listen for this shortcut from now on (``OFF``: none). Returns a problem, or ""."""
        with self._lock:
            self.stop()
            self.shortcut, self.problem = None, ""
            shortcut = SHORTCUTS.get(name)
            if shortcut is None or not self.available:
                return ""
            listener = _Listener(shortcut)
            self._listener = listener
            listener.thread = threading.Thread(
                target=self._listen, args=(listener,), name="magpie-shortcut", daemon=True
            )
            listener.thread.start()
            if not listener.ready.wait(5):
                listener.problem = "The keyboard shortcut didn't start."
            self.problem = listener.problem
            if not self.problem:
                self.shortcut = shortcut
            return self.problem

    def stop(self) -> None:
        with self._lock:
            listener, self._listener = self._listener, None
            if listener is None:
                return
            listener.stopping.set()
            keys, thread = listener.keys, listener.thread
            if thread is None or not thread.is_alive():
                return
            if keys is not None:
                try:
                    keys.stop(listener.thread_id)
                except Exception:
                    log.debug("Stopping the keyboard shortcut failed", exc_info=True)
            thread.join(2)

    def _listen(self, listener: _Listener) -> None:
        shortcut = listener.shortcut
        try:
            keys = (self._keys or _windows_keys)()
            listener.thread_id = keys.prepare()
            listener.keys = keys
            if not keys.register(shortcut.modifiers | MOD_NOREPEAT, shortcut.key):
                listener.problem = (
                    f"{shortcut.label} is already used by another program. Choose another "
                    "shortcut in Settings → Voice."
                )
                return
        except Exception as exc:
            log.warning("The keyboard shortcut couldn't be set up", exc_info=True)
            listener.problem = f"The keyboard shortcut couldn't be set up: {exc}"
            return
        finally:
            listener.ready.set()
        log.info("Listening for %s", shortcut.label)
        try:
            while not listener.stopping.is_set() and keys.wait():
                if not listener.stopping.is_set():
                    self._pressed(keys, listener)
        except Exception:
            log.exception("The keyboard shortcut stopped working")
        finally:
            try:
                keys.unregister()
            except Exception:
                log.debug("Letting go of the keyboard shortcut failed", exc_info=True)

    def _pressed(self, keys: Keys, listener: _Listener) -> None:
        """Pressed: start or save a note; then, if the keys stay down, it's held."""
        key = listener.shortcut.key
        down = time.monotonic()
        _call(self.on_press)
        held = False
        while keys.held(key) and not listener.stopping.is_set():
            if not held and time.monotonic() - down >= HOLD_SECONDS:
                held = True
                _call(self.on_hold)
            if time.monotonic() - down > LONGEST_HOLD:
                break
            time.sleep(POLL_SECONDS)
        if held or time.monotonic() - down >= HOLD_SECONDS:
            _call(self.on_release)


class _Listener:
    """One shortcut's thread, and what it found out."""

    def __init__(self, shortcut: Shortcut) -> None:
        self.shortcut = shortcut
        self.ready = threading.Event()  # registered, or not
        self.stopping = threading.Event()
        self.thread: threading.Thread | None = None
        self.keys: Keys | None = None
        self.thread_id = 0
        self.problem = ""


def _call(callback: Callable[[], None]) -> None:
    try:
        callback()
    except Exception:
        log.exception("The keyboard shortcut's action failed")


def _windows_keys() -> Keys:
    if sys.platform != "win32":
        raise RuntimeError("keyboard shortcuts are made with Windows' own calls")
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    wm_quit, wm_user, pm_noremove = 0x0012, 0x0400, 0x0000
    get_key_state = user32.GetAsyncKeyState
    get_key_state.argtypes, get_key_state.restype = [ctypes.c_int], ctypes.c_short

    class WindowsKeys:
        def prepare(self) -> int:
            message = wintypes.MSG()
            # Asking for a message gives this thread its queue, so stop() can reach it.
            user32.PeekMessageW(ctypes.byref(message), None, wm_user, wm_user, pm_noremove)
            return int(kernel32.GetCurrentThreadId())

        def register(self, modifiers: int, key: int) -> bool:
            return bool(user32.RegisterHotKey(None, HOTKEY_ID, modifiers, key))

        def unregister(self) -> None:
            user32.UnregisterHotKey(None, HOTKEY_ID)

        def wait(self) -> bool:
            message = wintypes.MSG()
            while True:
                got = user32.GetMessageW(ctypes.byref(message), None, 0, 0)
                if got in (0, -1):  # told to stop (WM_QUIT), or the queue failed
                    return False
                if message.message == WM_HOTKEY and message.wParam == HOTKEY_ID:
                    return True

        def held(self, key: int) -> bool:
            return bool(get_key_state(key) & 0x8000)  # the high bit: down right now

        def stop(self, thread_id: int) -> None:
            user32.PostThreadMessageW(thread_id, wm_quit, 0, 0)

    return WindowsKeys()
