from datetime import UTC, datetime, timedelta

from magpie.brain import BrainError, BrainUnavailable
from magpie.content import FetchError, LinkContent
from magpie.models import ProcState, ReminderStatus
from magpie.schemas import FoundLink
from magpie.timeutil import local_to_utc, now_utc, to_local

from .conftest import TZ, make_triage, reminder


def tomorrow_local(hour: int) -> str:
    day = to_local(now_utc(), TZ).date() + timedelta(days=1)
    return f"{day.isoformat()}T{hour:02d}:00"


async def test_triage_files_the_note_and_schedules_reminders(core, brain):
    when = tomorrow_local(17)
    brain.next_triage = make_triage(
        reminders=[reminder(when)],
        ping="explicit",
        ping_reason="You asked for 5 PM.",
        reply="Saved. I'll remind you at 5:00 PM.",
    )
    note = core.store.create_note("uh remind me to call ahmed tomorrow at 5", "web-voice")
    result = await core.pipeline.process(note.id)

    assert result.state == ProcState.READY
    assert result.title == "Call Ahmed about the budget"
    assert result.kind == "task"
    assert result.tags == ["work"]
    assert result.reply.startswith("Saved.")
    assert len(result.reminders) == 1
    assert result.reminders[0].due_at == local_to_utc(when, TZ)
    assert result.reminders[0].origin == "explicit"
    ctx = brain.contexts[0]
    assert ctx.timezone == TZ and ctx.source == "web-voice"


async def test_quiet_mode_drops_suggested_reminders(core, brain):
    prefs = core.prefs()
    prefs.proactivity = "quiet"
    core.store.save_prefs(prefs)
    brain.next_triage = make_triage(
        reminders=[reminder(tomorrow_local(18), origin="suggested")], ping="suggested"
    )
    note = core.store.create_note("electricity bill due on the 10th")
    result = await core.pipeline.process(note.id)
    assert result.reminders == []
    assert result.ping == "digest"
    assert result.in_digest


async def test_past_times_are_moved_or_dropped(core, brain):
    now = to_local(now_utc(), TZ)
    an_hour_ago = (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M")
    two_days_ago = (now - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M")
    brain.next_triage = make_triage(
        reminders=[reminder(an_hour_ago), reminder(two_days_ago)], ping="explicit"
    )
    note = core.store.create_note("x")
    result = await core.pipeline.process(note.id)
    assert len(result.reminders) == 1
    assert now_utc() <= result.reminders[0].due_at <= now_utc() + timedelta(minutes=2)


async def test_repeating_reminders_and_bad_rules(core, brain):
    brain.next_triage = make_triage(
        reminders=[
            reminder(tomorrow_local(8), repeat="FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"),
            reminder(tomorrow_local(9), repeat="FREQ=MINUTELY"),
            reminder("not a time"),
        ],
        ping="explicit",
    )
    note = core.store.create_note("every weekday at 8 take vitamins")
    result = await core.pipeline.process(note.id)
    rules = sorted((r.rrule or "") for r in result.reminders)
    assert rules == [
        "",
        "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR",
    ]  # bad rule kept as one-off, bad time dropped


async def test_reprocess_replaces_claude_reminders_but_keeps_manual(core, brain):
    note = core.store.create_note("x")
    core.store.add_reminder(note.id, now_utc() + timedelta(days=1), "mine", origin="manual")
    brain.next_triage = make_triage(reminders=[reminder(tomorrow_local(10))], ping="explicit")
    await core.pipeline.process(note.id)
    brain.next_triage = make_triage(reminders=[reminder(tomorrow_local(11))], ping="explicit")
    result = await core.pipeline.process(note.id)
    times = sorted((r.origin, to_local(r.due_at, TZ).hour) for r in result.reminders)
    assert ("explicit", 10) not in times
    assert ("explicit", 11) in times
    assert any(origin == "manual" for origin, _ in times)


async def test_links_are_fetched_and_reviewed(core, brain, monkeypatch):
    fetched = []

    async def fake_fetch(url):
        fetched.append(url)
        return LinkContent(
            url=url, kind="article", title="Great essay", text="Body. " * 100, minutes=7
        )

    monkeypatch.setattr(core.fetcher, "fetch", fake_fetch)
    brain.next_triage = make_triage(kind="note", urls=["https://example.com/essay"], ping="digest")
    note = core.store.create_note("read this later https://example.com/essay")
    await core.pipeline.process(note.id)
    await core.pipeline.drain()

    result = core.store.get_note(note.id)
    assert result.kind == "link"  # a plain note with a link becomes a link note
    assert fetched == ["https://example.com/essay"]
    assert len(result.links) == 1
    link = result.links[0]
    assert link.state == ProcState.READY
    assert link.review is not None
    assert link.title == "Great essay"
    assert link.minutes == 7
    # The review is searchable from the note.
    assert [n.id for n in core.store.list_notes(q="Great")] == [note.id]


async def test_unfetchable_link_is_reviewed_by_claude_directly(core, brain, monkeypatch):
    async def failing_fetch(url):
        raise FetchError("The page returned HTTP 403")

    monkeypatch.setattr(core.fetcher, "fetch", failing_fetch)
    brain.next_triage = make_triage(kind="link", urls=["https://example.com/paywalled"])
    note = core.store.create_note("https://example.com/paywalled")
    await core.pipeline.process(note.id)
    await core.pipeline.drain()
    assert "review_url" in brain.calls
    assert core.store.get_note(note.id).links[0].state == ProcState.READY


async def test_private_links_are_not_reviewed(core, brain, monkeypatch):
    async def private(url):
        raise FetchError("Refusing to fetch 10.0.0.1: it resolves to a private address")

    monkeypatch.setattr(core.fetcher, "fetch", private)
    brain.next_triage = make_triage(kind="link", urls=["http://10.0.0.1/admin"])
    note = core.store.create_note("http://10.0.0.1/admin")
    await core.pipeline.process(note.id)
    await core.pipeline.drain()
    link = core.store.get_note(note.id).links[0]
    assert link.state == ProcState.FAILED
    assert "review_url" not in brain.calls


async def test_lookup_finds_a_link_with_web_search(core, brain, monkeypatch):
    async def find_link(query):
        brain.calls.append(f"find:{query}")
        return FoundLink(
            url="https://www.youtube.com/watch?v=dQw4w9WgXcQ", title="Found", confidence="high"
        )

    async def fake_fetch(url):
        return LinkContent(url=url, kind="youtube", title="Found video", text="transcript " * 50)

    monkeypatch.setattr(brain, "find_link", find_link)
    monkeypatch.setattr(core.fetcher, "fetch", fake_fetch)
    brain.next_triage = make_triage(
        kind="link", lookup="Veritasium Monty Hall video", ping="digest"
    )
    note = core.store.create_note("watch that veritasium video about monty hall")
    await core.pipeline.process(note.id)
    await core.pipeline.drain()
    result = core.store.get_note(note.id)
    assert "find:Veritasium Monty Hall video" in brain.calls
    assert result.links[0].found_by_search
    assert result.links[0].kind == "youtube"
    assert result.links[0].state == ProcState.READY


async def test_lookup_respects_the_web_search_setting(core, brain):
    prefs = core.prefs()
    prefs.web_search = False
    core.store.save_prefs(prefs)
    brain.next_triage = make_triage(kind="link", lookup="some essay")
    note = core.store.create_note("read that essay")
    await core.pipeline.process(note.id)
    await core.pipeline.drain()
    assert not any(call.startswith("find") for call in brain.calls)


async def test_errors(core, brain, monkeypatch):
    spawned = []
    monkeypatch.setattr(
        core.pipeline, "_retry", lambda note_id, delay: spawned.append(delay) or _noop()
    )

    brain.raise_on_triage = BrainError("overloaded", retryable=True)
    note = core.store.create_note("x")
    result = await core.pipeline.process(note.id)
    assert result.state == ProcState.PENDING
    assert "Retrying" in result.error
    assert result.meta["attempts"] == 1

    brain.raise_on_triage = BrainError("bad request")
    note = core.store.create_note("y")
    result = await core.pipeline.process(note.id)
    assert result.state == ProcState.FAILED

    brain.raise_on_triage = BrainUnavailable("no key")
    note = core.store.create_note("z")
    result = await core.pipeline.process(note.id)
    assert result.state == ProcState.PENDING
    await core.pipeline.drain()


async def _noop():
    return None


async def test_unavailable_brain_keeps_notes_pending(core, brain, monkeypatch):
    monkeypatch.setattr(type(brain), "available", property(lambda self: False))
    note = core.store.create_note("saved while offline")
    result = await core.pipeline.process(note.id)
    assert result.state == ProcState.PENDING
    assert "ANTHROPIC_API_KEY" in result.error
    assert brain.calls == []


async def test_resume_requeues_unfinished_work(core, brain):
    stuck = core.store.create_note("stuck")
    core.store.update_note(stuck.id, state=ProcState.PROCESSING)
    core.pipeline.resume()
    await core.pipeline.drain()
    assert core.store.get_note(stuck.id).state == ProcState.READY


async def test_capture_time_is_the_clock_for_relative_times(core, brain):
    note = core.store.create_note("in 20 minutes call mom")
    captured = now_utc() - timedelta(hours=1)
    core.store.set_created_at(note.id, captured)
    await core.pipeline.process(note.id)
    assert brain.contexts[-1].now == captured
    old = core.store.create_note("old")
    core.store.set_created_at(old.id, now_utc() - timedelta(days=3))
    await core.pipeline.process(old.id)
    assert brain.contexts[-1].now > now_utc() - timedelta(minutes=1)


async def test_listeners_get_events(core, brain):
    events = []

    async def listener(event, note, link):
        events.append((event, note.id))

    core.pipeline.listeners.append(listener)
    note = core.store.create_note("hello")
    await core.pipeline.process(note.id)
    assert events == [("triaged", note.id)]


def test_reminder_status_enum_is_text():
    assert ReminderStatus.SCHEDULED == "scheduled"
    assert datetime(2026, 1, 1, tzinfo=UTC).tzinfo is UTC
