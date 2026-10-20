"""MCP server: use your Magpie notes from Claude Desktop, Claude Code or any MCP client.

It runs on your computer over stdio and talks to your Magpie server through its
HTTP API, so it works with a Magpie running anywhere. Install the extra with
``pip install 'magpie-notes[mcp]'`` and see docs/integrations.md.
"""

from __future__ import annotations

import os
from typing import Any

import httpx2
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

INSTRUCTIONS = (
    "Magpie is the user's voice-first notes app. Use these tools to save notes (Magpie's own "
    "Claude files them and schedules any reminder a note asks for), search and read notes, and "
    "check or update reminders."
)


def _note_line(note: dict[str, Any]) -> str:
    title = note.get("title") or (note.get("text") or note.get("raw_text") or "")[:60]
    tags = " ".join(f"#{tag}" for tag in note.get("tags", []))
    return f"#{note['id']} [{note.get('kind', 'note')}] {title} ({note['created_at'][:10]}) {tags}"


def _note_detail(note: dict[str, Any]) -> str:
    lines = [_note_line(note), "", note.get("text") or note.get("raw_text") or ""]
    if note.get("summary"):
        lines += ["", f"Summary: {note['summary']}"]
    for reminder in note.get("reminders", []):
        lines.append(
            f"Reminder #{reminder['id']} ({reminder['status']}): {reminder['due_at']} "
            f"{reminder['message']}"
        )
    for link in note.get("links", []):
        review = link.get("review") or {}
        line = f"Link: {link['url']}"
        if review:
            line += f" | {review.get('tldr', '')} | verdict {review.get('verdict')}"
        lines.append(line)
    return "\n".join(lines)


def build(url: str, token: str, client: httpx2.AsyncClient | None = None) -> MCPServer:
    client = client or httpx2.AsyncClient(
        base_url=url.rstrip("/"), headers={"Authorization": f"Bearer {token}"}, timeout=90
    )
    server = MCPServer("magpie", instructions=INSTRUCTIONS)

    async def call(method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = await client.request(method, path, **kwargs)
        except httpx2.HTTPError as exc:
            raise ToolError(f"Could not reach Magpie at {url} ({type(exc).__name__}).") from exc
        try:
            data = response.json() if response.content else None
        except ValueError:
            data = None
        if response.status_code >= 400:
            detail = data.get("detail") if isinstance(data, dict) else None
            # ToolError: its message reaches Claude (other errors are hidden as crashes).
            raise ToolError(f"Magpie returned {response.status_code}: {detail or response.text}")
        if data is None and response.content:
            raise ToolError("Magpie sent an answer that is not JSON. Is the URL right?")
        return data

    @server.tool()
    async def add_note(text: str) -> str:
        """Save a note in Magpie. Reminders in the text ("remind me to call James at 5 pm") are
        scheduled automatically and links are reviewed."""
        data = await call(
            "POST", "/api/notes", params={"wait": "true"}, json={"text": text, "source": "mcp"}
        )
        return f"{data['reply']} (note #{data['note']['id']})"

    @server.tool()
    async def search_notes(query: str, limit: int = 10) -> str:
        """Full-text search across all of the user's notes, newest relevant first."""
        data = await call(
            "GET",
            "/api/notes",
            params={"q": query[:300], "view": "all", "limit": max(1, min(limit, 50))},
        )
        notes = data["notes"]
        return "\n".join(_note_line(n) for n in notes) or "No notes match."

    @server.tool()
    async def recent_notes(limit: int = 20, view: str = "inbox") -> str:
        """List recent notes. view: inbox, all, done, archived, scheduled or reviews."""
        data = await call(
            "GET", "/api/notes", params={"view": view, "limit": max(1, min(limit, 100))}
        )
        return "\n".join(_note_line(n) for n in data["notes"]) or "No notes."

    @server.tool()
    async def get_note(note_id: int) -> str:
        """Read one note in full, with its reminders and link reviews."""
        return _note_detail(await call("GET", f"/api/notes/{note_id}"))

    @server.tool()
    async def upcoming_reminders(limit: int = 20) -> str:
        """Scheduled reminders, soonest first, in the user's local time."""
        data = await call("GET", "/api/reminders", params={"limit": max(1, min(limit, 100))})
        lines = [
            f"#{r['id']} {r['when']}: {r['message']}" + (" (repeats)" if r.get("rrule") else "")
            for r in data["reminders"]
        ]
        return "\n".join(lines) or "Nothing scheduled."

    @server.tool()
    async def complete_reminder(reminder_id: int) -> str:
        """Mark a reminder as done."""
        await call("POST", f"/api/reminders/{reminder_id}/done")
        return "Marked as done."

    @server.tool()
    async def snooze_reminder(reminder_id: int, minutes: int = 60) -> str:
        """Push a reminder back by some minutes."""
        minutes = max(1, min(minutes, 60 * 24 * 30))
        data = await call("POST", f"/api/reminders/{reminder_id}/snooze", json={"minutes": minutes})
        return f"Snoozed until {data['when']}."

    return server


def run(url: str | None = None, token: str | None = None) -> None:
    url = url or os.environ.get("MAGPIE_URL", "http://localhost:8000")
    token = token or os.environ.get("MAGPIE_API_TOKEN")
    if not token:
        raise SystemExit(
            "Pass --token or set MAGPIE_API_TOKEN (dashboard → Settings → Integrations)."
        )
    build(url, token).run("stdio")
