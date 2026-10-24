"""What you see of the desktop app: the notes window, the voice bar, reminder pop-ups and
the tray icon.

The windows are web pages served by the app's own Magpie server, shown by pywebview
(WebView2 on Windows, WebKit on a Mac). The bar and the pop-ups float above other windows
and appear without taking the keyboard from whatever you're doing. Their pages reach the
app through the server's API (/api/desktop/ui), and the app drives them with run_js: no
pywebview js_api, whose generated functions the pages' strict CSP (no eval) refuses.

On a Mac everything that touches AppKit (the menu bar icon, the windows) runs on the
main thread, and Magpie is in the Dock only while its notes window is open.
"""

from __future__ import annotations

import contextlib
import json
import logging
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

if TYPE_CHECKING:
    from magpie.desktop.app import DesktopApp

log = logging.getLogger(__name__)
W = TypeVar("W")

BAR_SIZE = (680, 172)
ALERTS_WIDTH = 380
BOTTOM_GAP = 104 if sys.platform == "darwin" else 64  # clear of the Dock or the taskbar
NOACTIVATE = 0x08000000  # WS_EX_NOACTIVATE
GWL_EXSTYLE = -20
SW_HIDE, SW_SHOWNOACTIVATE = 0, 4
HWND_TOPMOST = -1
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE, SWP_SHOWWINDOW = 0x1, 0x2, 0x10, 0x40
DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_ROUND = 33, 2


def _hwnd(title: str) -> int:
    if sys.platform != "win32":
        return 0
    import ctypes

    return int(ctypes.windll.user32.FindWindowW(None, title) or 0)


def _user32() -> Any:
    if sys.platform != "win32":
        raise RuntimeError("user32 is part of Windows")
    import ctypes

    return ctypes.windll.user32


def on_main(work: Callable[[], Any]) -> None:
    """AppKit may only be touched from the main thread: there it waits its turn on a Mac."""
    if sys.platform == "darwin":
        from PyObjCTools import AppHelper

        AppHelper.callAfter(work)
    else:
        work()


def _round_corners(hwnd: int) -> None:
    """Windows 11 rounds a borderless window's corners when asked; Windows 10 ignores it."""
    if sys.platform != "win32":
        return
    import ctypes

    preference = ctypes.c_int(DWMWCP_ROUND)
    with contextlib.suppress(Exception):  # only how it looks
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd,
            DWMWA_WINDOW_CORNER_PREFERENCE,
            ctypes.byref(preference),
            ctypes.sizeof(preference),
        )


def _nsapp() -> Any:
    if sys.platform != "darwin":
        raise RuntimeError("AppKit is part of macOS")
    import AppKit

    return AppKit.NSApplication.sharedApplication()


def _made(window: W | None) -> W:
    """pywebview gives no window only when an `initialized` handler cancels it (none does)."""
    if window is None:
        raise RuntimeError("pywebview didn't make the window")
    return window


ALL_SPACES = 1 << 0 | 1 << 8  # NSWindowCollectionBehavior: every desktop, and full screen


class Floating:
    """Shows, hides and places a floating window without taking the keyboard.

    pywebview's own show() activates the window, which would pull the keyboard away from
    whatever you're typing in, so these windows are only ever shown, hidden and moved
    with the Windows API, or on a Mac with the window's own NSWindow calls.
    """

    def __init__(self, window: Any, scale: float) -> None:
        self.window = window
        self.scale = scale or 1.0
        self._rounded = 0  # the window whose corners were rounded

    def _handle(self) -> int:
        return _hwnd(self.window.title)

    def _mac(self, work: Callable[[Any], Any]) -> bool:
        """Do ``work`` with the NSWindow (on the main thread). False when not on a Mac."""
        native = getattr(self.window, "native", None)
        if sys.platform != "darwin" or native is None:
            return False
        on_main(lambda: work(native))
        return True

    def show(self) -> None:
        def front(native: Any) -> None:
            native.setCollectionBehavior_(ALL_SPACES)
            native.orderFrontRegardless()  # in front, without making Magpie active

        if self._mac(front):
            return
        hwnd = self._handle()
        if not hwnd:
            self.window.show()
            return
        if self._rounded != hwnd:
            _round_corners(hwnd)
            self._rounded = hwnd
        user32 = _user32()
        user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
        flags = SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW
        user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, flags)

    def hide(self) -> None:
        if self._mac(lambda native: native.orderOut_(None)):
            return
        hwnd = self._handle()
        if hwnd:
            _user32().ShowWindow(hwnd, SW_HIDE)
        else:
            self.window.hide()

    def place(self, x: int, y: int, width: int, height: int) -> None:
        """Move and size it (in the screen's logical pixels) and show it."""
        hwnd = self._handle()
        if not hwnd:
            self.window.resize(width, height)
            self.window.move(x, y)
            self.show()
            return
        s = self.scale
        _user32().SetWindowPos(
            hwnd,
            HWND_TOPMOST,
            int(x * s),
            int(y * s),
            int(width * s),
            int(height * s),
            SWP_NOACTIVATE | SWP_SHOWWINDOW,
        )

    def keyboard(self, on: bool) -> None:
        """Whether a click may give the window the keyboard (to type a note)."""
        self.window.focus = on  # pywebview re-applies the style when the window activates

        def mac(native: Any) -> None:
            native.focus = on  # what pywebview's window asks before it takes the keyboard
            if on:
                _nsapp().activateIgnoringOtherApps_(True)
                native.makeKeyAndOrderFront_(None)

        if self._mac(mac):
            return
        hwnd = self._handle()
        if not hwnd:
            return
        user32 = _user32()
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        style = style & ~NOACTIVATE if on else style | NOACTIVATE
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
        if on:
            user32.SetForegroundWindow(hwnd)


class DesktopUI:
    def __init__(self, app: DesktopApp, base_url: str, *, show_main: bool) -> None:
        import webview

        self._webview = webview
        self._app = app
        self.base_url = base_url
        screen = webview.screens[0] if webview.screens else None
        self._screen = (
            (screen.x, screen.y, screen.width, screen.height) if screen else (0, 0, 1366, 768)
        )
        scale = float(getattr(screen, "scale", 1.0) or 1.0)
        sx, sy, sw, sh = self._screen
        bar_w, bar_h = BAR_SIZE
        webview.settings["ALLOW_DOWNLOADS"] = True  # exports from the notes window
        self.main = _made(
            webview.create_window(
                "Magpie",
                f"{base_url}/",
                width=min(1120, sw - 80),
                height=min(800, sh - 120),
                min_size=(420, 560),
                hidden=not show_main,
                background_color="#f4f5f9",
                text_select=True,
            )
        )
        self.main.events.closing += self._closing
        self.bar = _made(
            webview.create_window(
                "Magpie voice bar",
                f"{base_url}/static/desktop/bar.html",
                width=bar_w,
                height=bar_h,
                x=sx + (sw - bar_w) // 2,
                y=sy + sh - bar_h - BOTTOM_GAP,
                frameless=True,
                easy_drag=False,  # moved by its top line, so the text can be selected
                on_top=True,
                hidden=True,
                focus=False,
                resizable=False,
                shadow=True,
                text_select=True,
            )
        )
        self.alerts = _made(
            webview.create_window(
                "Magpie reminders",
                f"{base_url}/static/desktop/reminders.html",
                width=ALERTS_WIDTH,
                height=40,
                x=sx + sw - ALERTS_WIDTH - 16,
                y=sy + sh - 40 - BOTTOM_GAP,
                frameless=True,
                easy_drag=False,
                on_top=True,
                hidden=True,
                focus=False,
                resizable=False,
                shadow=True,
                text_select=True,
            )
        )
        self.quitting = False
        self.main_visible = show_main
        self.bar_visible = False
        self._alerts_height = 0
        self._bar = Floating(self.bar, scale)
        self._alerts = Floating(self.alerts, scale)

    # ------------------------------------------------------------------ running

    def run(self, on_start: Callable[[], None], storage: Path) -> None:
        """Blocks (on the main thread) until every window is closed."""
        storage.mkdir(parents=True, exist_ok=True)

        def started() -> None:
            self._dock(self.main_visible)
            on_start()

        self._webview.start(started, private_mode=False, storage_path=str(storage))

    def quit(self) -> None:
        self.quitting = True
        for window in (self.alerts, self.bar, self.main):
            try:
                window.destroy()
            except Exception:
                log.debug("Closing a window failed", exc_info=True)

    def _closing(self) -> bool:
        # Closing the notes window keeps Magpie running (and listening) in the tray.
        if self.quitting:
            return True
        self.main_visible = False
        threading.Thread(target=self.main.hide, daemon=True).start()
        self._dock(False)
        return False

    def _dock(self, visible: bool) -> None:
        """On a Mac: in the Dock and Cmd-Tab while the notes window is open, else only in
        the menu bar."""
        if sys.platform != "darwin":
            return

        def apply() -> None:
            app = _nsapp()
            app.setActivationPolicy_(0 if visible else 1)  # regular, or accessory
            if visible:
                app.activateIgnoringOtherApps_(True)

        on_main(apply)

    # ------------------------------------------------------------- notes window

    def show_main(self, path: str = "/") -> None:
        if path and path != "/":
            self.main.load_url(f"{self.base_url}{path}")
        self.main_visible = True
        self._dock(True)
        self.main.show()
        self.main.restore()

    # ---------------------------------------------------------------- voice bar

    def bar_call(self, name: str, payload: dict[str, Any] | None = None) -> None:
        script = f"window.magpieBar && window.magpieBar.{name}({json.dumps(payload or {})})"
        try:
            self.bar.run_js(script)
        except Exception:
            log.debug("The voice bar didn't take %s", name, exc_info=True)

    def show_bar(self, *, keyboard: bool = False) -> None:
        """Show the bar over everything; only typing a note gives it the keyboard."""
        self.bar_visible = True
        self._bar.show()
        if keyboard:
            self._bar.keyboard(True)

    def hide_bar(self) -> None:
        self.bar_visible = False
        self._bar.hide()
        self._bar.keyboard(False)

    def take_keyboard(self) -> None:
        """Let the bar take the keyboard: after a click on it, or to type a note."""
        self._bar.keyboard(True)

    # ------------------------------------------------------------------ pop-ups

    def alerts_call(self, name: str, payload: Any) -> None:
        script = f"window.magpieAlerts && window.magpieAlerts.{name}({json.dumps(payload)})"
        try:
            self.alerts.run_js(script)
        except Exception:
            log.debug("The pop-ups didn't take %s", name, exc_info=True)

    def fit_alerts(self, height: Any) -> None:
        try:
            height = max(0, min(int(float(height)), self._screen[3] - 160))
        except (TypeError, ValueError):
            return
        if height == self._alerts_height:
            return
        self._alerts_height = height
        if height <= 2:
            self._alerts.hide()
            return
        sx, sy, sw, sh = self._screen
        self._alerts.place(
            sx + sw - ALERTS_WIDTH - 16,
            sy + sh - height - 16 - BOTTOM_GAP,
            ALERTS_WIDTH,
            height + 16,
        )


# --------------------------------------------------------------------- the tray


def _icon_image(state: str) -> Any:
    from PIL import Image, ImageDraw, ImageOps

    path = Path(__file__).resolve().parent.parent / "web" / "static" / "icons" / "icon-192.png"
    image = Image.open(path).convert("RGBA").resize((64, 64))
    if state in ("paused", "no-mic", "muted", "error", "starting"):
        grey = ImageOps.grayscale(image).convert("RGBA")
        grey.putalpha(image.getchannel("A"))
        image = grey
    if state == "recording":
        draw = ImageDraw.Draw(image)
        red, white = (226, 44, 56, 255), (255, 255, 255, 255)
        draw.ellipse((38, 38, 62, 62), fill=red, outline=white, width=3)
    return image


PLAIN = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'", "…": "...", "→": "->"})


class Tray:
    """The icon in the notification area, with Magpie's menu.

    Magpie works without it (a desktop with no tray, an icon that fails): it's never
    allowed to stop the app.
    """

    def __init__(self, app: DesktopApp) -> None:
        self._app = app
        self._icon: Any = None
        self._state = "starting"
        self._latin1 = False  # the X11 fallback tray only takes Latin-1 text

    def text(self, value: str) -> str:
        if not self._latin1:
            return value
        return value.translate(PLAIN).encode("latin-1", "replace").decode("latin-1")

    def start(self) -> bool:
        try:
            import pystray

            self._latin1 = pystray.Icon.__module__.endswith("_xorg")
            app = self._app
            item = pystray.MenuItem
            system = {"win32": "Windows", "darwin": "your Mac"}.get(sys.platform, "the computer")
            menu = pystray.Menu(
                item("Open Magpie", lambda: app.open_notes(), default=True),
                item("Start a voice note", lambda: app.start_note()),
                item("Write a note", lambda: app.write_note()),
                pystray.Menu.SEPARATOR,
                item(
                    lambda _: self.text(f"Listen for “{app.wake_phrase}”"),
                    lambda: app.set_listening(not app.listening),
                    checked=lambda _: app.listening,
                ),
                item(
                    f"Start Magpie with {system}",
                    lambda: app.set_autostart(not app.autostart),
                    checked=lambda _: app.autostart,
                ),
                pystray.Menu.SEPARATOR,
                item("Check for updates", lambda: app.check_for_updates()),
                item("Quit Magpie", lambda: app.quit()),
            )
            icon = pystray.Icon(
                "Magpie", _icon_image("starting"), self.text("Magpie is starting…"), menu
            )
            icon.run_detached()
        except Exception:  # no tray here (no display, an unsupported desktop, …)
            log.warning("No tray icon on this system", exc_info=True)
            return False
        self._icon = icon
        return True

    def show(self, state: str, tooltip: str) -> None:
        icon = self._icon
        if icon is None:
            return

        def update() -> None:
            try:
                if state != self._state:
                    self._state = state
                    icon.icon = _icon_image(state)
                icon.title = self.text(tooltip)[:127]  # Windows cuts tooltips at 128 characters
                icon.update_menu()
            except Exception:
                log.debug("Updating the tray icon failed", exc_info=True)

        on_main(update)

    def notify(self, message: str) -> None:
        """A notification from the tray icon (Windows shows it by the clock)."""
        icon = self._icon
        if icon is None or not getattr(icon, "HAS_NOTIFICATION", False):
            return
        try:
            icon.notify(self.text(message), "Magpie")
        except Exception:
            log.debug("The tray notification failed", exc_info=True)

    def stop(self) -> None:
        # On a Mac stopping the icon stops the whole app's event loop, which is ending anyway.
        if self._icon is not None and sys.platform != "darwin":
            try:
                self._icon.stop()
            except Exception:
                log.debug("Stopping the tray icon failed", exc_info=True)
