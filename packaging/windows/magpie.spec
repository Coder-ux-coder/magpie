# PyInstaller recipe for the Magpie app on Windows (the "Windows app" workflow runs it):
#
#   pip install -e ".[desktop]" pyinstaller
#   set MAGPIE_VOSK_MODEL=C:\path\to\vosk-model-small-en-us-0.15
#   pyinstaller --noconfirm packaging\windows\magpie.spec
#
# The result is dist\Magpie\Magpie.exe with everything it needs, the speech model
# included; Magpie.iss turns that folder into MagpieSetup.exe.

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules
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
from magpie.desktop.paths import MODEL_NAME

HERE = Path(SPECPATH).resolve()  # noqa: F821 (set by PyInstaller)
ROOT = HERE.parent.parent
BUILD = ROOT / "build"

model = Path(os.environ.get("MAGPIE_VOSK_MODEL", ""))
if not (model / "am" / "final.mdl").is_file():
    raise SystemExit(f"Set MAGPIE_VOSK_MODEL to the {MODEL_NAME} folder (got {model!s}).")

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

datas = [
    *collect_data_files("magpie"),  # the web app and the app's own pages
    *collect_data_files("trafilatura"),
    *collect_data_files("justext"),
    *collect_data_files("tzdata"),
    *collect_data_files("certifi"),
    (str(model), f"models/{MODEL_NAME}"),
]
binaries = collect_dynamic_libs("vosk")  # libvosk.dll and the runtime it needs
hiddenimports = [
    *collect_submodules("magpie", filter=lambda name: name != "magpie.mcp_server"),
    *collect_submodules("uvicorn"),
    "pystray._win32",
    "webview.platforms.winforms",
    "webview.platforms.edgechromium",
]

a = Analysis(  # noqa: F821
    [str(HERE / "magpie-app.py")],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "mcp", "pytest", "numpy", "IPython"],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Magpie",
    icon=str(ROOT / "magpie.ico"),
    version=str(version_file),
    console=False,
    upx=False,
)
COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="Magpie")  # noqa: F821
