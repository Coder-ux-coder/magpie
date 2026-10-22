"""The user's own categories, the tab a note was written in, and learning from corrections."""

from __future__ import annotations

import sqlite3

from magpie.brain.prompts import triage_message
from magpie.db import MIGRATIONS, Database

from .conftest import make_triage


def test_categories_can_be_made_coloured_ordered_and_removed(client):
    work = client.post("/api/categories", json={"name": "  Work  ", "color": "teal"}).json()
    home = client.post("/api/categories", json={"name": "Home"}).json()
    assert work["name"] == "Work" and work["color"] == "teal" and home["color"] == "blue"
    assert client.post("/api/categories", json={"name": "work"}).status_code == 409
    assert client.post("/api/categories", json={"name": "X", "color": "neon"}).status_code == 422
    assert client.post("/api/categories", json={"name": "   "}).status_code == 422

    renamed = client.patch(
        f"/api/categories/{home['id']}", json={"name": "Family", "color": "pink"}
    )
    assert renamed.json()["name"] == "Family" and renamed.json()["color"] == "pink"
    clash = client.patch(f"/api/categories/{home['id']}", json={"name": "WORK"})
    assert clash.status_code == 409

    ordered = client.put("/api/categories/order", json={"ids": [home["id"], work["id"], 999]})
    assert [c["name"] for c in ordered.json()["categories"]] == ["Family", "Work"]

    note = client.post(
        "/api/notes?wait=true", json={"text": "fix the tap", "category_id": home["id"]}
    )
    note_id = note.json()["note"]["id"]
    assert client.delete(f"/api/categories/{home['id']}").status_code == 204
    assert client.get(f"/api/notes/{note_id}").json()["category_id"] is None  # the note stays
    assert client.delete(f"/api/categories/{home['id']}").status_code == 404


async def test_the_ai_files_notes_into_the_users_categories(core, brain):
    work = core.store.add_category("Work")
    brain.next_triage = make_triage(category="work")  # any case
    note = core.store.create_note("call james about the budget")
    filed = await core.pipeline.process(note.id)
    assert filed.category_id == work.id and filed.meta["category_by"] == "ai"
    assert brain.contexts[-1].categories == ("Work",)

    brain.next_triage = make_triage(category="Space station")  # not one of theirs
    other = await core.pipeline.process(core.store.create_note("orbit things").id)
    assert other.category_id is None


def test_notes_stay_in_the_tab_they_were_written_in(client, brain):
    work = client.post("/api/categories", json={"name": "Work"}).json()
    client.post("/api/categories", json={"name": "Home"})
    brain.next_triage = make_triage(category="Home")
    saved = client.post(
        "/api/notes?wait=true",
        json={"text": "book the plumber", "category_id": work["id"], "captured_in": "Work"},
    ).json()["note"]
    assert saved["category_id"] == work["id"]
    assert saved["meta"]["captured_in"] == "Work" and saved["meta"]["category_by"] == "you"
    listed = client.get("/api/notes", params={"category": work["id"]}).json()
    assert [n["id"] for n in listed["notes"]] == [saved["id"]]
    assert next(c for c in listed["categories"] if c["id"] == work["id"])["count"] == 1
    # The AI hears which tab it was, as a hint.
    assert brain.contexts[-1].written_in == "Work"
    assert "Written in: Work\n" in triage_message("book the plumber", brain.contexts[-1])

    # Any tab counts, and a category removed before an offline note syncs is no error.
    brain.next_triage = make_triage(kind="task")
    later = client.post(
        "/api/notes?wait=true",
        json={"text": "renew the car tax", "captured_in": "  Tasks\n", "category_id": 999},
    )
    assert later.status_code == 201
    assert later.json()["note"]["meta"]["captured_in"] == "Tasks"
    assert later.json()["note"]["category_id"] is None
    assert brain.contexts[-1].written_in == "Tasks"


def test_status_lists_categories_and_the_build(client, core):
    client.post("/api/categories", json={"name": "Work", "color": "teal"})
    status = client.get("/api/status").json()
    assert [(c["name"], c["color"], c["count"]) for c in status["categories"]] == [
        ("Work", "teal", 0)
    ]
    assert status["build"] == ""
    core.settings.build = "0123456789abcdef"
    assert client.get("/api/status").json()["build"] == "0123456789ab"


def test_corrections_are_learned_and_sent_with_new_notes(client, brain, core):
    shopping = client.post("/api/categories", json={"name": "Shopping"}).json()
    client.post("/api/categories", json={"name": "Home"})
    brain.next_triage = make_triage(category="Home", kind="note")
    note = client.post("/api/notes?wait=true", json={"text": "buy bread and milk"}).json()["note"]

    client.patch(f"/api/notes/{note['id']}", json={"category_id": shopping["id"], "kind": "list"})
    lessons = client.get("/api/learning").json()["lessons"]
    assert {(item["field"], item["from"], item["to"]) for item in lessons} == {
        ("category", "Home", "Shopping"),
        ("kind", "note", "list"),
    }

    brain.next_triage = None
    client.post("/api/notes?wait=true", json={"text": "get eggs"})
    sent = brain.contexts[-1].lessons
    assert any(line == '"buy bread and milk" → category Shopping (not Home)' for line in sent)
    assert "How this user files notes" in triage_message("get eggs", brain.contexts[-1])
    assert "User's categories: Shopping, Home" in triage_message("get eggs", brain.contexts[-1])

    assert len(client.delete("/api/learning", params={"index": 0}).json()["lessons"]) == 1
    assert client.delete("/api/learning").json()["lessons"] == []


def test_learning_can_be_turned_off(client, brain):
    shopping = client.post("/api/categories", json={"name": "Shopping"}).json()
    client.put("/api/prefs", json={"learn": False})
    note = client.post("/api/notes?wait=true", json={"text": "buy bread"}).json()["note"]
    client.patch(f"/api/notes/{note['id']}", json={"category_id": shopping["id"]})
    assert client.get("/api/learning").json() == {"enabled": False, "lessons": []}


def test_a_note_can_leave_its_category(client):
    work = client.post("/api/categories", json={"name": "Work"}).json()
    note = client.post("/api/notes", json={"text": "x", "category_id": work["id"]}).json()["note"]
    assert (
        client.patch(f"/api/notes/{note['id']}", json={"category_id": 0}).json()["category_id"]
        is None
    )
    gone = client.patch(f"/api/notes/{note['id']}", json={"category_id": 12345})
    assert gone.status_code == 422


def test_the_offline_stand_in_matches_category_names(core):
    import asyncio

    from magpie.brain.fake import FakeBrain

    ctx = core.context()
    ctx = type(ctx)(**{**ctx.__dict__, "categories": ("Work", "Home Office")})
    triage = asyncio.run(FakeBrain().triage("email the #home office landlord", ctx))
    assert triage.category == "Home Office"
    assert asyncio.run(FakeBrain().triage("networking event", ctx)).category == ""


def test_an_existing_database_gains_categories(tmp_path):
    path = tmp_path / "magpie.db"
    conn = sqlite3.connect(path)
    conn.executescript(f"BEGIN;\n{MIGRATIONS[0]}\nPRAGMA user_version=1;\nCOMMIT;")
    conn.execute(
        "INSERT INTO notes (created_at, updated_at, source, raw_text) VALUES (?, ?, ?, ?)",
        ("2026-10-01T09:00:00+00:00", "2026-10-01T09:00:00+00:00", "web", "an old note"),
    )
    conn.commit()
    conn.close()
    db = Database(path)
    row = db.one("SELECT raw_text, category_id FROM notes")
    assert row["raw_text"] == "an old note" and row["category_id"] is None
    assert db.one("PRAGMA user_version")[0] == len(MIGRATIONS)


def test_categories_travel_with_an_export(core, tmp_path):
    from magpie.db import Database
    from magpie.export import export_json, import_json
    from magpie.store import Store

    work = core.store.add_category("Work", "teal")
    note = core.store.create_note("budget meeting notes")
    core.store.update_note(note.id, category_id=work.id, title="Budget meeting")
    dump = export_json(core.store)

    fresh = Store(Database(":memory:"), core.settings)
    fresh.add_category("Home")
    assert import_json(fresh, dump) == {"imported": 1, "skipped": 0}
    moved = fresh.list_notes(view="any")[0]
    category = fresh.get_category(moved.category_id)
    assert category.name == "Work" and category.color == "teal"
    assert "category" not in moved.meta

    # An export from Magpie in Claude (the artifact) gives each note's category as
    # {"name", "colour"}, in its own palette; a bare name works too.
    def note(text, category):
        return {
            "id": 1,
            "created_at": "2026-10-20T09:00:00+00:00",
            "updated_at": "2026-10-20T09:00:00+00:00",
            "source": "magpie-in-claude",
            "raw_text": text,
            "meta": {"from": "magpie-in-claude", "category": category},
        }

    artifact = {
        "app": "magpie",
        "version": "magpie-in-claude",
        "notes": [
            note("call mum on saturday", {"name": " Family ", "colour": "violet"}),
            note("water the plants", {"name": "work", "colour": "pink"}),  # already here
            note("plan the garden", "Garden"),
            note("odd one", {"name": ["not", "a", "name"]}),
        ],
    }
    import json

    assert import_json(fresh, json.dumps(artifact))["imported"] == 4
    colors = {c.name: c.color for c in fresh.list_categories()}
    assert colors == {"Home": "blue", "Work": "teal", "Family": "purple", "Garden": "blue"}
    by_text = {n.raw_text: n for n in fresh.list_notes(view="any")}
    assert fresh.get_category(by_text["water the plants"].category_id).name == "Work"
    assert by_text["odd one"].category_id is None and "category" not in by_text["odd one"].meta
