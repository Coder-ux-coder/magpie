"""Shows test results and what the app logged as annotations on the workflow run, so they
can be read on the run's summary page without downloading anything (Windows and Mac):

    python packaging/report.py --report build/selftest.json --title "Built app"
    python packaging/report.py --junit build/tests.xml
    python packaging/report.py --log "$HOME/Library/Application Support/Magpie/logs/magpie.log"
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import xml.etree.ElementTree as ElementTree
from pathlib import Path

LOGGED = re.compile(r"starting \(|is ready on|Time zone set|Brought over|Whisper ready")
TROUBLE = re.compile(r" (WARNING|ERROR|CRITICAL) ")
# What a build machine always logs, as it has no AI key and no microphone: not trouble.
EXPECTED = re.compile(r"isn't connected yet|No microphone|PortAudio")


def annotate(kind: str, title: str, message: str) -> None:
    # Workflow commands: one line, with % escaped; titles without commas or colons.
    message = message.replace("%", "%25").replace("\r", " ").replace("\n", " ")
    print(f"::{kind} title={re.sub(r'[,:]', ' ', title)}::{message[:1500]}", flush=True)


def report(path: Path, title: str) -> None:
    results = json.loads(path.read_text(encoding="utf-8"))
    for check in results.get("checks", []):
        # A check that isn't required (the microphone, on a machine without one) is a note.
        kind = "error" if check.get("required") and not check.get("ok") else "notice"
        annotate(kind, f"{title} - {check.get('name')}", str(check.get("detail", "")))


def junit(path: Path) -> None:
    for case in ElementTree.parse(path).iter("testcase"):
        for problem in (*case.findall("failure"), *case.findall("error")):
            text = f"{problem.get('message', '')} {problem.text or ''}".strip()
            annotate("error", f"Test {case.get('name')}", text)


def log(path: Path) -> None:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    for line in [line for line in lines if LOGGED.search(line)][:5]:
        annotate("notice", "The app's log", line)
    for line in [line for line in lines if TROUBLE.search(line)][:6]:
        annotate("notice" if EXPECTED.search(line) else "warning", "The app's log", line)


def main() -> None:
    # The workflow reads UTF-8; a Windows console would otherwise refuse “ or →.
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, help="a self-test's results (JSON)")
    parser.add_argument("--title", default="Self-test")
    parser.add_argument("--junit", type=Path, help="pytest's --junitxml results")
    parser.add_argument("--log", type=Path, help="the app's log")
    args = parser.parse_args()
    if args.report and args.report.is_file():
        report(args.report, args.title)
    if args.junit and args.junit.is_file():
        junit(args.junit)
    if args.log and args.log.is_file():
        log(args.log)


if __name__ == "__main__":
    main()
