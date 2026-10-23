"""The AI services Magpie can use, and how the active one is chosen.

Claude is the default. Any service with an OpenAI-compatible chat API works
too: OpenAI, Google Gemini, OpenRouter, Ollama on your own computer, and
others such as Groq, Mistral, DeepSeek or LM Studio ("compatible"). Codex,
OpenAI's command-line agent, works without a key where it's installed and
signed in (brain/codex_cli.py).

The provider comes from MAGPIE_BRAIN when the server sets it, otherwise from
Settings → AI in the dashboard, which is stored in the database as::

    {"provider": "openai",
     "settings": {"openai": {"model": "...", "base_url": "", "key": "...", "key_for": "..."}}}

A saved key remembers the address it was entered for (``key_for``) and is
only ever sent there, so changing the address can't leak it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from magpie.config import Settings


@dataclass(frozen=True)
class Provider:
    id: str
    label: str  # in the provider list: "OpenAI (ChatGPT)"
    name: str  # in sentences: "Gemini is filing it…"
    base_url: str
    model: str
    needs_key: bool = True
    key_url: str = ""
    key_hint: str = ""
    fixed_url: bool = True  # the address can't be changed in the dashboard
    help: str = ""


PROVIDERS: dict[str, Provider] = {
    provider.id: provider
    for provider in (
        Provider(
            "claude",
            "Claude (Anthropic)",
            "Claude",
            "",
            "claude-haiku-5-5",
            key_url="https://console.anthropic.com/settings/keys",
            key_hint="sk-ant-…",
            help="The default. Files notes, reviews links and can search the web.",
        ),
        Provider(
            "openai",
            "OpenAI (ChatGPT)",
            "OpenAI",
            "https://api.openai.com/v1",
            "gpt-5-mini",
            key_url="https://platform.openai.com/api-keys",
            key_hint="sk-…",
            help="Uses an OpenAI API key (a ChatGPT subscription doesn't include one).",
        ),
        Provider(
            "codex",
            "Codex (your ChatGPT sign-in, no key)",
            "Codex",
            "",
            "",
            needs_key=False,
            key_url="https://github.com/openai/codex",
            help="No API key: Magpie runs Codex, OpenAI's command-line tool, as it's signed in "
            "on this computer. Install Codex, run “codex login” and sign in with ChatGPT, then "
            "choose it here. Slower than the others, and your Codex plan's limits apply.",
        ),
        Provider(
            "gemini",
            "Google Gemini",
            "Gemini",
            "https://generativelanguage.googleapis.com/v1beta/openai",
            "gemini-flash-latest",
            key_url="https://aistudio.google.com/apikey",
            key_hint="AIza…",
            help="Google AI Studio gives free keys with a daily limit.",
        ),
        Provider(
            "openrouter",
            "OpenRouter (many models)",
            "OpenRouter",
            "https://openrouter.ai/api/v1",
            "openrouter/auto",
            key_url="https://openrouter.ai/keys",
            key_hint="sk-or-…",
            help="One key for hundreds of models from many companies, some of them free.",
        ),
        Provider(
            "ollama",
            "Ollama (free, on your computer)",
            "Ollama",
            "http://localhost:11434/v1",
            "llama3.2",
            needs_key=False,
            key_url="https://ollama.com/download",
            fixed_url=False,
            help="Free and private: the model runs on your own computer. Install Ollama, run "
            "“ollama pull llama3.2” and keep Ollama open.",
        ),
        Provider(
            "basic",
            "No AI (simple rules)",
            "Magpie",
            "",
            "rules",
            needs_key=False,
            help="Works without any AI: picks out times (“at 5 pm”, “tomorrow”, “every "
            "Monday”), lists and links. Notes aren't cleaned up and links aren't reviewed.",
        ),
        Provider(
            "compatible",
            "Other (OpenAI-compatible)",
            "AI",
            "",
            "",
            needs_key=False,
            fixed_url=False,
            help="Any service with an OpenAI-compatible API, such as Groq, Mistral, DeepSeek or "
            "LM Studio. Enter its address, a model name and its key if it needs one.",
        ),
    )
}
OFFLINE = Provider("fake", "Offline stand-in", "Magpie", "", "offline", needs_key=False)


def in_docker() -> bool:
    return os.path.exists("/.dockerenv")


def default_url(provider: Provider) -> str:
    """The address to use when none was entered; Ollama runs on the host, outside Docker."""
    if provider.id == "ollama" and in_docker():
        return "http://host.docker.internal:11434/v1"
    return provider.base_url


@dataclass(frozen=True)
class AIConfig:
    provider: Provider
    model: str
    base_url: str
    api_key: str | None
    key_source: str | None  # "env", "dashboard" or None
    locked: bool  # MAGPIE_BRAIN on the server chose the provider

    @property
    def ready(self) -> bool:
        """Enough is configured to try (the service may still refuse)."""
        if self.provider.id in ("fake", "basic", "codex"):
            return True  # nothing to set up here (Codex signs in by itself)
        if self.provider.id != "claude" and not (self.base_url and self.model):
            return False
        return bool(self.api_key) or not self.provider.needs_key


def clean_saved(saved: Any) -> dict[str, Any]:
    """The stored dashboard choice, with anything malformed dropped."""
    out: dict[str, Any] = {"settings": {}}
    if not isinstance(saved, dict):
        return out
    if saved.get("provider") in PROVIDERS:
        out["provider"] = saved["provider"]
    per: dict[str, dict[str, str]] = {}
    raw = saved.get("settings")
    if isinstance(raw, dict):
        for pid, values in raw.items():
            if pid in PROVIDERS and isinstance(values, dict):
                per[pid] = {
                    k: v
                    for k, v in values.items()
                    if k in ("model", "base_url", "key", "key_for") and isinstance(v, str)
                }
    out["settings"] = per
    return out


def resolve(settings: Settings, saved: Any, claude_key: str | None = None) -> AIConfig:
    """The provider, model, address and key Magpie should use right now."""
    if settings.brain == "fake":
        return AIConfig(OFFLINE, "offline", "", None, None, True)
    stored = clean_saved(saved)
    locked = "brain" in settings.model_fields_set
    pid = settings.brain if locked else stored.get("provider", settings.brain)
    provider = PROVIDERS.get(pid, PROVIDERS["claude"])
    chosen_here = not locked and stored.get("provider") == provider.id
    mine = stored["settings"].get(provider.id, {})

    if provider.id == "basic":
        return AIConfig(provider, provider.model, "", None, None, locked)
    if provider.id == "codex":  # Codex's own default model unless one is named
        explicit = locked and "model" in settings.model_fields_set
        model = (mine.get("model") if chosen_here else None) or (settings.model if explicit else "")
        return AIConfig(provider, model, "", None, None, locked)
    if provider.id == "claude":
        model = (mine.get("model") if chosen_here else None) or settings.model
        if settings.anthropic_api_key or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
            key, source = settings.anthropic_api_key, "env"
        else:
            key, source = claude_key, ("dashboard" if claude_key else None)
        return AIConfig(provider, model, "", key, source, locked)

    if chosen_here:
        model = mine.get("model") or provider.model
        base_url = (mine.get("base_url") if not provider.fixed_url else "") or default_url(provider)
    else:
        explicit = "model" in settings.model_fields_set
        model = (settings.model if explicit else "") or mine.get("model") or provider.model
        base_url = settings.ai_base_url or default_url(provider)
    base_url = base_url.rstrip("/")
    if locked and settings.ai_api_key:
        key, source = settings.ai_api_key, "env"
    elif mine.get("key") and mine.get("key_for") == base_url:
        key, source = mine["key"], "dashboard"
    else:
        key, source = None, None
    return AIConfig(provider, model, base_url, key, source, locked)
