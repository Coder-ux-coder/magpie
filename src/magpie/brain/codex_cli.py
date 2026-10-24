"""A brain that runs Codex, OpenAI's command-line agent, as it's signed in on this computer.

For people who use ChatGPT rather than an API key: Magpie keeps no key and never sees the
sign-in. Each request runs ``codex exec`` once, in an empty folder, read-only and with its
shell tools turned off, so Codex answers only from the text it's given; a JSON schema
gives the answer its shape. It's slower than an API (several seconds a request) and only
works where Codex is installed and signed in: in practice the Magpie app on the person's
own computer. Codex's own plan and its limits apply.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from magpie.brain import prompts
from magpie.brain.base import BrainError, BrainUnavailable, Context, DigestData
from magpie.brain.claude import TIMEOUTS
from magpie.brain.openai_compat import THINK, _json_instruction, _parse, _schema
from magpie.brain.providers import AIConfig
from magpie.content import LinkContent
from magpie.models import Note
from magpie.schemas import Answer, FoundLink, LinkReview, Triage

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

INSTALL_URL = "https://github.com/openai/codex"
# Model names go on Codex's command line, so only plain ones: Codex installed with npm is a
# batch file on Windows, where other characters could change the command.
MODEL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}$")
OPTIONS = (
    "--skip-git-repo-check",
    "--ephemeral",  # nothing kept in Codex's history
    "--color",
    "never",
    "--sandbox",
    "read-only",
    # Answer from the text given: no shell commands, so no files read or changed.
    "-c",
    "features.shell_tool=false",
    "-c",
    "features.unified_exec=false",
    "-c",
    "model_reasoning_effort=low",  # filing a note doesn't need long thought
    "--output-last-message",
    "answer.txt",
)
BRIEF = (
    "You're the AI inside Magpie, a personal notes app, not a coding agent. Don't run "
    "commands, open files or change anything: answer only from what's below."
)
FIND_AGAIN = 60.0  # seconds before looking for Codex again (it may have been installed)
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
SIGNED_OUT = ("not logged in", "log in", "login", "unauthorized", "401", "authentication")
LIMITED = ("usage limit", "rate limit", "429", "quota", "too many requests")
OFFLINE = ("network", "connection", "timed out", "dns", "stream disconnected", "unreachable")


def _fresh_path() -> str:
    """PATH as Windows has it now, so Codex installed after Magpie started is found too."""
    path = os.environ.get("PATH", "")
    if sys.platform != "win32":
        return path
    try:
        import winreg

        parts = [path]
        for root, key in (
            (winreg.HKEY_CURRENT_USER, r"Environment"),
            (
                winreg.HKEY_LOCAL_MACHINE,
                r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
            ),
        ):
            try:
                with winreg.OpenKey(root, key) as handle:
                    value, _kind = winreg.QueryValueEx(handle, "Path")
                parts.append(os.path.expandvars(str(value)))
            except OSError:
                continue
        npm = os.environ.get("APPDATA")
        if npm:
            parts.append(str(Path(npm) / "npm"))
        return os.pathsep.join(part for part in parts if part)
    except Exception:
        return path


def find_codex(configured: str = "") -> str | None:
    """The Codex program: MAGPIE_CODEX_PATH when set, otherwise ``codex`` on the PATH."""
    if configured:
        path = Path(configured).expanduser()
        return str(path) if path.is_file() else None
    names = ("codex.exe", "codex.cmd", "codex") if sys.platform == "win32" else ("codex",)
    search = _fresh_path()
    for name in names:
        found = shutil.which(name, path=search)
        if found:
            return found
    return None


def _detail(err: str) -> str:
    lines = [ANSI.sub("", line).strip() for line in err.splitlines()]
    lines = [line for line in lines if line and not line.startswith("Reading prompt")]
    return " ".join(lines[-2:])[:300]


class CodexBrain:
    name = "codex"

    def __init__(self, config: AIConfig, *, program: str = "", runner: Any = None) -> None:
        self.config = config
        self.label = config.provider.name
        self.model = config.model
        self._program = program  # MAGPIE_CODEX_PATH
        self._run = runner or _run_process
        self._found: tuple[float, str | None] = (-FIND_AGAIN, None)
        self._limit = asyncio.Semaphore(2)  # a couple of notes at a time, not dozens
        self._schemas = True  # Codex passes the answer's schema on; fall back if refused
        self._usage: Counter[str] = Counter()

    # ------------------------------------------------------------------ status

    def _codex(self) -> str | None:
        checked, found = self._found
        if time.monotonic() - checked > FIND_AGAIN:
            found = find_codex(self._program)
            self._found = (time.monotonic(), found)
        return found

    @property
    def available(self) -> bool:
        return self._codex() is not None and self._model_ok()

    def _model_ok(self) -> bool:
        return not self.model or bool(MODEL_NAME.fullmatch(self.model))

    def usage(self) -> dict[str, int]:
        return dict(self._usage)

    def _program_or_raise(self) -> str:
        program = self._codex()
        if program is None:
            if self._program:
                raise BrainUnavailable(f"Codex isn't at {self._program} (MAGPIE_CODEX_PATH).")
            raise BrainUnavailable(
                "Codex isn't installed on this computer. Install it from "
                f"{INSTALL_URL}, sign in with “codex login”, then choose Codex again."
            )
        if not self._model_ok():
            raise BrainError(f"{self.model!r} isn't a model name Codex can use.")
        return program

    async def check(self) -> None:
        """Codex is installed, signed in, and answers."""
        program = self._program_or_raise()
        code, _out, err = await asyncio.to_thread(
            self._run, [program, "login", "status"], None, b"", 60
        )
        if code != 0:
            raise BrainUnavailable(
                "Codex isn't signed in on this computer. Open a terminal, run “codex login” "
                f"and sign in, then choose Codex again. ({_detail(err) or code})"
            )
        await self._exec("triage", "Reply with the word OK.", None)

    async def list_models(self) -> list[str]:
        return []  # Codex uses its own default unless a model is named

    # -------------------------------------------------------------- the brain

    async def triage(self, text: str, ctx: Context) -> Triage:
        return await self._structured(
            "triage", Triage, prompts.TRIAGE_SYSTEM, prompts.triage_message(text, ctx)
        )

    async def review(self, content: LinkContent, note_text: str) -> LinkReview:
        if content.pdf and not content.text.strip():
            raise BrainError(f"{self.label} can't read PDF files in Magpie; Claude can.")
        return await self._structured(
            "review", LinkReview, prompts.REVIEW_SYSTEM, prompts.review_message(content, note_text)
        )

    async def review_url(self, url: str, note_text: str) -> LinkReview:
        raise BrainError(f"Magpie couldn't download this page, and {self.label} won't open it.")

    async def find_link(self, query: str) -> FoundLink:
        log.info("%s doesn't search the web for Magpie; skipping %r", self.label, query)
        return FoundLink(url=None, title="", confidence="low")

    async def digest(self, data: DigestData) -> str:
        task = "weekly" if data.kind == "weekly" else "digest"
        system = prompts.WEEKLY_SYSTEM if data.kind == "weekly" else prompts.DIGEST_SYSTEM
        prompt = _prompt(system, prompts.digest_message(data))
        text = THINK.sub("", await self._exec(task, prompt, None)).strip()
        if not text:
            raise BrainError(f"{self.label} returned an empty answer.")
        return text

    async def ask(self, question: str, notes: list[Note], ctx: Context) -> Answer:
        return await self._structured(
            "ask", Answer, prompts.ASK_SYSTEM, prompts.ask_message(question, notes, ctx)
        )

    # --------------------------------------------------------------- plumbing

    async def _structured(self, task: str, schema: type[T], system: str, content: str) -> T:
        shape = _schema(schema)
        text: str | None = None
        if self._schemas:
            try:
                text = await self._exec(task, _prompt(system, content), shape)
            except _SchemaRefused as exc:
                log.warning("Codex refused the answer's schema (%s); asking in the prompt", exc)
                self._schemas = False
        if text is None:
            asked = _prompt(system + _json_instruction(schema), content)
            text = await self._exec(task, asked, None)
        try:
            return schema.model_validate(_parse(text))
        except (ValueError, ValidationError) as exc:
            problem = exc
        # One repair round: Codex sees its answer and what was wrong with it.
        repair = _prompt(
            system + _json_instruction(schema),
            f"{content}\n\nYour previous reply was not a valid JSON object for the schema "
            f"({str(problem)[:500]}):\n{text[:20_000]}\n\nReply with only the corrected JSON.",
        )
        text = await self._exec(task, repair, shape if self._schemas else None)
        try:
            return schema.model_validate(_parse(text))
        except (ValueError, ValidationError) as exc:
            raise BrainError(f"{self.label}'s answer was not in the expected format.") from exc

    async def _exec(self, task: str, prompt: str, schema: dict[str, Any] | None) -> str:
        program = self._program_or_raise()
        args = [program, "exec", *OPTIONS]
        if schema is not None:
            args += ["--output-schema", "schema.json"]
        if self.model:
            args += ["--model", self.model]
        args.append("-")  # the prompt comes on stdin, never on the command line
        seconds = TIMEOUTS.get(task, 180)
        async with self._limit:
            with tempfile.TemporaryDirectory(prefix="magpie-codex-") as work:
                folder = Path(work)
                if schema is not None:
                    (folder / "schema.json").write_text(json.dumps(schema), encoding="utf-8")
                self._usage["requests"] += 1
                code, _out, err = await asyncio.to_thread(
                    self._run, args, folder, prompt.encode("utf-8"), seconds
                )
                answer = folder / "answer.txt"
                text = answer.read_text("utf-8", errors="replace") if answer.exists() else ""
        if code == -1:
            raise BrainError(f"{self.label} took too long to answer.", retryable=True)
        if code != 0:
            raise self._problem(err, schema is not None)
        if not text.strip():
            raise BrainError(f"{self.label} sent an empty answer. ({_detail(err)})")
        return text

    def _problem(self, err: str, with_schema: bool) -> BrainError:
        detail = _detail(err)
        said = detail.lower()
        if any(word in said for word in SIGNED_OUT):
            return BrainUnavailable(
                "Codex isn't signed in on this computer. Open a terminal, run “codex login” "
                "and sign in. Your notes are saved and filed once it is."
            )
        if any(word in said for word in LIMITED):
            return BrainError(
                f"Codex has reached its usage limit for now; Magpie tries again later. ({detail})",
                retryable=True,
            )
        if with_schema and ("schema" in said or "text.format" in said):
            return _SchemaRefused(detail)
        if self.model and "model" in said:
            return BrainError(
                f"Codex can't use the model {self.model!r} ({detail}). Pick another in "
                "Settings → AI, or leave it empty for Codex's own."
            )
        if any(word in said for word in OFFLINE):
            return BrainError(f"Codex couldn't reach OpenAI. ({detail})", retryable=True)
        return BrainError(f"Codex couldn't answer: {detail or 'it stopped without saying why'}")


class _SchemaRefused(BrainError):
    """Codex (or its model) didn't take the answer's JSON schema."""


def _prompt(system: str, content: str) -> str:
    return f"{BRIEF}\n\n<instructions>\n{system}\n</instructions>\n\n{content}"


def _run_process(
    args: list[str], folder: Path | None, stdin: bytes, seconds: float
) -> tuple[int, str, str]:
    """Run Codex and wait: (exit code, output, errors); -1 when it took too long."""
    options: dict[str, Any] = {}
    if sys.platform == "win32":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW  # no console flashing up
    try:
        done = subprocess.run(
            args,
            cwd=folder,
            input=stdin,
            capture_output=True,
            timeout=seconds,
            check=False,
            **options,
        )
    except subprocess.TimeoutExpired:
        return -1, "", ""
    except OSError as exc:
        return 127, "", f"Codex couldn't be started: {exc}"
    return (
        done.returncode,
        done.stdout.decode("utf-8", errors="replace"),
        done.stderr.decode("utf-8", errors="replace"),
    )
