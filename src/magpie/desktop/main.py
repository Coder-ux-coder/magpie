"""Start the Magpie app: Magpie.exe, ``magpie desktop`` or ``python -m magpie.desktop``."""

from __future__ import annotations

import argparse
import contextlib
import logging
import os
import sys
from pathlib import Path
from typing import NoReturn

from magpie import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="magpie desktop",
        description="Magpie as an app: in the tray, always listening for “notes on”.",
    )
    parser.add_argument(
        "--background",
        action="store_true",
        help="start in the tray without opening the notes window (as when the computer starts)",
    )
    parser.add_argument("--home", type=Path, help="keep the app's notes and settings here")
    parser.add_argument("--verbose", action="store_true", help="log more, and to the terminal")
    parser.add_argument(
        "--self-test", action="store_true", help="check that everything works, then quit"
    )
    parser.add_argument("--wav", type=Path, help="self-test: a recording to take a note from")
    parser.add_argument("--expect", help="self-test: a word the note from --wav must contain")
    parser.add_argument("--windows", action="store_true", help="self-test: open a window too")
    parser.add_argument("--report", type=Path, help="self-test: write the results here (JSON)")
    parser.add_argument("--version", action="version", version=f"Magpie {__version__}")
    return parser


def _no_console() -> None:
    """A windowed app has no terminal: stray output goes nowhere instead of failing."""
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))  # noqa: SIM115


def main(argv: list[str] | None = None) -> NoReturn:
    _no_console()
    args = build_parser().parse_args(argv)
    if args.self_test:
        from magpie.desktop.selftest import self_test

        code = self_test(wav=args.wav, expect=args.expect, windows=args.windows, report=args.report)
    else:
        from magpie.desktop.app import DesktopApp, setup_logging
        from magpie.desktop.paths import app_paths

        paths = app_paths(args.home)
        setup_logging(paths, verbose=args.verbose)
        code = DesktopApp(paths, background=args.background).run()
    # Everything is saved and stopped by now; a tray or window thread that hangs on the
    # way out must not keep a half-closed Magpie (and its lock) around.
    logging.shutdown()
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            stream.flush()
    os._exit(code)


def run() -> NoReturn:
    main()
