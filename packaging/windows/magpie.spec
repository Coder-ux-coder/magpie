# PyInstaller recipe for the Magpie app on Windows (the "App" workflow runs it):
#
#   pip install -e ".[desktop]" pyinstaller
#   set MAGPIE_VOSK_MODEL=C:\path\to\vosk-model-small-en-us-0.15
#   pyinstaller --noconfirm packaging\windows\magpie.spec
#
# The result is dist\Magpie\Magpie.exe with everything it needs (../bundle.py says what);
# Magpie.iss turns that folder into MagpieSetup.exe.

import sys
from pathlib import Path

from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)

from magpie import __version__

HERE = Path(SPECPATH).resolve()  # noqa: F821 (set by PyInstaller)
sys.path.insert(0, str(HERE.parent))
from bundle import ENTRY, EXCLUDES, ROOT, contents  # noqa: E402

BUILD = ROOT / "build"

numbers = (*(int(part) for part in __version__.split(".")[:3]), 0)
version_file = BUILD / "version-info.txt"
version_file.parent.mkdir(parents=True, exist_ok=True)
version_file.write_text(
    str(
        VSVersionInfo(
            ffi=FixedFileInfo(filevers=numbers, prodvers=numbers),
            kids=[
                StringFileInfo(
                    [
                        StringTable(
                            "040904B0",
                            [
                                StringStruct("CompanyName", "Magpie contributors"),
                                StringStruct("FileDescription", "Magpie"),
                                StringStruct("FileVersion", __version__),
                                StringStruct("InternalName", "Magpie"),
                                StringStruct("LegalCopyright", "MIT License"),
                                StringStruct("OriginalFilename", "Magpie.exe"),
                                StringStruct("ProductName", "Magpie"),
                                StringStruct("ProductVersion", __version__),
                            ],
                        )
                    ]
                ),
                VarFileInfo([VarStruct("Translation", [1033, 1200])]),
            ],
        )
    ),
    encoding="utf-8",
)

datas, binaries, hiddenimports = contents()
hiddenimports += [
    "pystray._win32",
    "webview.platforms.winforms",
    "webview.platforms.edgechromium",
]

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
    icon=str(HERE / "magpie.ico"),
    version=str(version_file),
    console=False,
    upx=False,
)
COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="Magpie")  # noqa: F821
