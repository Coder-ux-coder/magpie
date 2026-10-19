from datetime import timedelta

import pytest

from magpie.config import Prefs, Settings
from magpie.db import Database
from magpie.models import ProcState, ReminderStatus
from magpie.schemas import LinkReview
from magpie.store import Store
from magpie.timeutil import now_utc


@pytest.fixture
def store() -> Store:
    return Store(Database(":memory:"), Settings(_env_file=None, timezone="Asia/Karachi"))


def test_create_update_and_search(store: Store):
    note = store.create_note("uh call ahmed about the budget", "web-voice", {"k": 1})
    assert note.state == ProcState.PENDING
    assert note.meta == {"k": 1}
    store.update_note(
        note.id, title="Call Ahmed", text="Call Ahmed about the budget.", tags=["work"]
    )
    other = store.create_note("buy milk and eggs", "web-text")

    assert [n.id for n in store.list_notes(q="budg")] == [note.id]
    assert [n.id for n in store.list_notes(q="WORK")] == [note.id]  # tags are searchable
    assert [n.id for n in store.list_notes(q="milk eggs")] == [other.id]
    assert store.list_notes(q="zebra") == []
    assert store.list_notes(q="!!!") == []


def test_search_covers_link_reviews(store: Store):
    note = store.create_note("https://example.com", "web-text")
    store.update_note(note.id, search_extra="An essay on startups that don't scale")
    assert [n.id for n in store.list_notes(q="startups")] == [note.id]


def test_views(store: Store):
    open_note = store.create_note("open one")
    done = store.create_note("done one")
    archived = store.create_note("archived one")
    store.update_note(done.id, status="done")
    store.update_note(archived.id, status="archived")
    scheduled = store.create_note("with reminder")
    store.add_reminder(scheduled.id, now_utc() + timedelta(hours=1), "Ping")
    linked = store.create_note("with link")
    store.add_link(linked.id, "https://example.com", "article")

    ids = lambda view: {n.id for n in store.list_notes(view=view)}  # noqa: E731
    assert ids("inbox") == {open_note.id, scheduled.id, linked.id}
    assert ids("done") == {done.id}
    assert ids("archived") == {archived.id}
    assert ids("scheduled") == {scheduled.id}
    assert ids("reviews") == {linked.id}
    assert archived.id not in ids("all")
    assert ids("any") == {open_note.id, done.id, archived.id, scheduled.id, linked.id}
    counts = store.count_notes()
    assert counts["inbox"] == 3
    assert counts["scheduled"] == 1
    assert counts["reviews"] == 1


def test_pinned_notes_come_first(store: Store):
    first = store.create_note("first")
    second = store.create_note("second")
    store.update_note(first.id, pinned=True)
    assert [n.id for n in store.list_notes()] == [first.id, second.id]


def test_due_reminders_and_children(store: Store):
    note = store.create_note("x")
    past = store.add_reminder(note.id, now_utc() - timedelta(minutes=1), "Past")
    store.add_reminder(note.id, now_utc() + timedelta(hours=1), "Future")
    assert [r.id for r in store.due_reminders(now_utc())] == [past.id]
    store.update_reminder(past.id, status=ReminderStatus.SENT)
    assert store.due_reminders(now_utc()) == []
    loaded = store.get_note(note.id)
    assert [r.message for r in loaded.reminders] == ["Past", "Future"]


def test_cancel_open_reminders_keeps_manual_and_sent(store: Store):
    note = store.create_note("x")
    later = now_utc() + timedelta(hours=1)
    store.add_reminder(note.id, later, "explicit", origin="explicit")
    store.add_reminder(note.id, later, "suggested", origin="suggested")
    store.add_reminder(note.id, later, "manual", origin="manual")
    sent = store.add_reminder(note.id, later, "sent", origin="explicit")
    store.update_reminder(sent.id, status=ReminderStatus.SENT)
    assert store.cancel_open_reminders(note.id, ("explicit", "suggested")) == 2
    assert sorted(r.message for r in store.get_note(note.id).reminders) == ["manual", "sent"]


def test_link_review_roundtrip(store: Store):
    note = store.create_note("x")
    link = store.add_link(note.id, "https://example.com", "article")
    review = LinkReview(
        title="T",
        tldr="Short",
        key_points=["a"],
        verdict="worth_it",
        rating=9,
        best_for="b",
        caveats="",
        why_saved="w",
        minutes=4,
    )
    assert review.rating == 5  # clamped
    updated = store.update_link(link.id, review=review, state=ProcState.READY)
    assert updated.review.tldr == "Short"
    assert store.reviews_since(now_utc() - timedelta(minutes=1))[0].id == link.id


def test_prefs_defaults_and_bad_values(store: Store):
    prefs = store.get_prefs()
    assert prefs.timezone == "Asia/Karachi"
    store.set_kv("prefs", {"timezone": "Not/AZone", "proactivity": "proactive"})
    prefs = store.get_prefs()
    assert prefs.timezone == "Asia/Karachi"  # bad value ignored, not fatal
    assert prefs.proactivity == "proactive"
    store.save_prefs(Prefs(timezone="Europe/London"))
    assert store.get_prefs().timezone == "Europe/London"


def test_unknown_fields_are_rejected(store: Store):
    note = store.create_note("x")
    with pytest.raises(ValueError):
        store.update_note(note.id, evil="1; DROP TABLE notes")


def test_digest_flags(store: Store):
    note = store.create_note("x")
    store.update_note(note.id, in_digest=True)
    assert [n.id for n in store.digest_notes()] == [note.id]
    store.clear_digest_flags([note.id])
    assert store.digest_notes() == []
