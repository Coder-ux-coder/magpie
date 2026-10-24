# PyInstaller recipe for the Magpie app on a Mac (the "App" workflow runs it):
#
#   pip install -e ".[desktop]" pyinstaller
#   MAGPIE_VOSK_MODEL=/path/to/vosk-model-small-en-us-0.15 \
#     pyinstaller --noconfirm packaging/macos/magpie.spec
#
# The result is dist/Magpie.app with everything it needs (../bundle.py says what); the
# workflow zips it as Magpie-mac.zip for the release, and install.sh puts it in
# Applications. It's for Apple silicon (the build machine's kind of Mac) and macOS 14 or
# newer, which Whisper's libraries need.

import sys
from pathlib import Path

from magpie import __version__

HERE = Path(SPECPATH).resolve()  # noqa: F821 (set by PyInstaller)
sys.path.insert(0, str(HERE.parent))
from bundle import ENTRY, EXCLUDES, ROOT, contents  # noqa: E402

from magpie.desktop.system import LAUNCH_AGENT  # noqa: E402

datas, binaries, hiddenimports = contents()
hiddenimports += ["pystray._darwin", "webview.platforms.cocoa"]

a = Analysis(  # noqa: F821
    [str(ENTRY)],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=EXCLUDES,
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Magpie",
    console=False,
    upx=False,
    argv_emulation=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="Magpie")  # noqa: F821
BUNDLE(  # noqa: F821
    coll,
    name="Magpie.app",
    icon=str(ROOT / "src" / "magpie" / "web" / "static" / "icons" / "icon-512.png"),
    bundle_identifier=LAUNCH_AGENT,
    version=__version__,
    info_plist={
        "CFBundleName": "Magpie",
        "CFBundleDisplayName": "Magpie",
        "CFBundleShortVersionString": __version__,
        "CFBundleVersion": __version__,
        "LSMinimumSystemVersion": "14.0",
        # A menu bar app: no Dock icon while only the menu bar icon and the bar are up.
        "LSUIElement": True,
        "NSHighResolutionCapable": True,
        "NSMicrophoneUsageDescription": (
            "Magpie listens for “notes on” and writes down your voice notes, on this Mac."
        ),
        # Its windows are pages from Magpie's own server on this Mac (127.0.0.1).
        "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
    },
)
