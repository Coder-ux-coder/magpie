"""What goes into the app on every system, for the PyInstaller recipes in windows/ and macos/.

Each recipe adds its own system's parts (the tray and window backends, the icon, the
version details) to these.
"""

from __future__ import annotations

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

from magpie.desktop.paths import MODEL_NAME

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
ENTRY = HERE / "magpie-app.py"
# Hugging Face's library imports its parts lazily, where PyInstaller can't see them.
HUB_SKIP = ("huggingface_hub.inference", "huggingface_hub.cli", "huggingface_hub.commands")
EXCLUDES = ["tkinter", "mcp", "pytest", "IPython", "torch", "matplotlib"]


def speech_model() -> Path:
    """The listener's speech model (Vosk), which ships with the app."""
    model = Path(os.environ.get("MAGPIE_VOSK_MODEL", ""))
    if not (model / "am" / "final.mdl").is_file():
        raise SystemExit(f"Set MAGPIE_VOSK_MODEL to the {MODEL_NAME} folder (got {model!s}).")
    return model


def contents() -> tuple[list, list, list]:
    """(datas, binaries, hiddenimports) every build needs.

    Whisper's model (480 MB or more) isn't included: the app downloads it once.
    """
    datas = [
        *collect_data_files("magpie"),  # the web app and the app's own pages
        *collect_data_files("trafilatura"),
        *collect_data_files("justext"),
        *collect_data_files("tzdata"),
        *collect_data_files("certifi"),
        *collect_data_files("faster_whisper"),  # the voice activity model it skips silence with
        (str(speech_model()), f"models/{MODEL_NAME}"),
    ]
    binaries = [
        *collect_dynamic_libs("vosk"),  # libvosk and the runtime it needs
        *collect_dynamic_libs("ctranslate2"),  # Whisper's engine
        *collect_dynamic_libs("onnxruntime"),
    ]
    hiddenimports = [
        *collect_submodules("magpie", filter=lambda name: name != "magpie.mcp_server"),
        *collect_submodules("uvicorn"),
        *collect_submodules("faster_whisper"),
        *collect_submodules("av"),
        *collect_submodules("huggingface_hub", filter=lambda name: not name.startswith(HUB_SKIP)),
    ]
    return datas, binaries, hiddenimports
