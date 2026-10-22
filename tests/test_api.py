import json
from datetime import timedelta

from fastapi.testclient import TestClient

from magpie.app import create_app
from magpie.models import ReminderStatus
from magpie.timeutil import now_utc, to_local

from .conftest import TZ, make_triage, reminder


def test_requires_login(core, settings):
    app = create_app(settings, magpie=core, background=False)
    with TestClient(app) as anonymous:
        assert anonymous.get("/api/notes").status_code == 401
        assert anonymous.get("/api/session").json()["authenticated"] is False
        assert anonymous.post("/api/login", json={"password": "nope"}).status_code == 401
        assert anonymous.get("/healthz").json() == {"status": "ok"}
        page = anonymous.get("/")
        assert page.status_code == 200 and "Magpie" in page.text


def test_login_is_rate_limited(core, settings):
    from magpie import api

    api.login_limiter.reset("testclient")
    app = create_app(settings, magpie=core, background=False)
    with TestClient(app) as anonymous:
        codes = [
            anonymous.post("/api/login", json={"password": "x"}).status_code for _ in range(12)
        ]
        assert codes[:10] == [401] * 10
        assert codes[-1] == 429
    api.login_limiter.reset("testclient")


def test_cookie_sessions_need_the_csrf_header(client):
    response = client.post("/api/notes", json={"text": "hi"}, headers={"X-Magpie": ""})
    assert response.status_code == 403
    assert client.post("/api/notes", json={"text": "hi"}).status_code == 201


def test_bearer_token_works_without_csrf_header(core, settings):
    app = create_app(settings, magpie=core, background=False)
    with TestClient(app) as api_client:
        headers = {"Authorization": f"Bearer {core.secrets.api_token}"}
        response = api_client.post(
            "/api/notes?wait=true", json={"text": "from siri", "source": "siri"}, headers=headers
        )
        assert response.status_code == 201
        assert response.json()["note"]["source"] == "siri"
        assert (
            api_client.get("/api/notes", headers={"Authorization": "Bearer wrong"}).status_code
            == 401
        )


def test_create_and_wait_returns_the_reply(client, brain):
    brain.next_triage = make_triage(reply="Saved. I'll remind you at 5:00 PM.")
    response = client.post(
        "/api/notes?wait=true", json={"text": "call james at 5", "source": "WEIRD SOURCE!"}
    )
    body = response.json()
    assert response.status_code == 201
    assert body["reply"] == "Saved. I'll remind you at 5:00 PM."
    assert body["note"]["state"] == "ready"
    assert body["note"]["source"] == "api"  # invalid source names fall back


def test_offline_capture_time_is_kept(client):
    captured = (now_utc() - timedelta(hours=2)).isoformat()
    body = client.post("/api/notes", json={"text": "queued", "captured_at": captured}).json()
    assert body["note"]["created_at"][:13] == captured[:13]
    future = (now_utc() + timedelta(days=1)).isoformat()
    body = client.post("/api/notes", json={"text": "future", "captured_at": future}).json()
    assert body["note"]["created_at"][:10] == now_utc().date().isoformat()


def test_list_patch_delete(client):
    note = client.post("/api/notes?wait=true", json={"text": "idea: rooftop garden"}).json()["note"]
    listing = client.get("/api/notes").json()
    assert [n["id"] for n in listing["notes"]] == [note["id"]]
    assert listing["counts"]["inbox"] == 1

    patched = client.patch(
        f"/api/notes/{note['id']}", json={"tags": ["#Garden", "garden", " Home "], "pinned": True}
    ).json()
    assert patched["tags"] == ["garden", "home"]
    assert patched["pinned"] is True

    done = client.patch(f"/api/notes/{note['id']}", json={"status": "done"}).json()
    assert done["status"] == "done"
    assert client.get("/api/notes").json()["notes"] == []
    assert len(client.get("/api/notes?view=done").json()["notes"]) == 1
    assert client.get("/api/notes?q=rooftop").json()["notes"][0]["id"] == note["id"]

    assert client.delete(f"/api/notes/{note['id']}").status_code == 204
    assert client.get(f"/api/notes/{note['id']}").status_code == 404


def test_editing_text_then_reprocessing_uses_the_edit(client, brain):
    note = client.post("/api/notes?wait=true", json={"text": "cal mom tmrw"}).json()["note"]
    client.patch(f"/api/notes/{note['id']}", json={"text": "Call mom tomorrow evening"})
    result = client.post(f"/api/notes/{note['id']}/reprocess?wait=true").json()
    assert result["raw_text"] == "Call mom tomorrow evening"
    assert result["meta"]["original_raw"] == "cal mom tmrw"


def test_reminder_actions(client, core, brain):
    day = to_local(now_utc(), TZ).date() + timedelta(days=1)
    brain.next_triage = make_triage(reminders=[reminder(f"{day}T17:00")], ping="explicit")
    note = client.post("/api/notes?wait=true", json={"text": "x"}).json()["note"]
    rid = note["reminders"][0]["id"]

    upcoming = client.get("/api/reminders").json()["reminders"]
    assert upcoming[0]["note_title"] == "Call James about the budget"
    assert upcoming[0]["when"] == "tomorrow 5:00 PM"

    # Postponing a reminder that is still ahead never moves it earlier.
    snoozed = client.post(f"/api/reminders/{rid}/snooze", json={"minutes": 30}).json()
    assert snoozed["status"] == "scheduled"
    assert snoozed["when"] == "tomorrow 5:30 PM"
    later = client.post(f"/api/reminders/{rid}/tomorrow").json()
    assert to_local(_dt(later["due_at"]), TZ).date() == day + timedelta(days=1)
    assert later["when"].endswith("9:00 AM")
    overdue = core.store.add_reminder(None, now_utc() - timedelta(hours=2), "Overdue")
    moved = client.post(f"/api/reminders/{overdue.id}/tomorrow").json()
    assert moved["when"] == "tomorrow 9:00 AM"
    done = client.post(f"/api/reminders/{rid}/done").json()
    assert done["status"] == "done"
    # Finishing the last reminder of a task finishes the task.
    assert client.get(f"/api/notes/{note['id']}").json()["status"] == "done"
    assert client.post("/api/reminders/9999/done").status_code == 404


def test_manual_reminders(client):
    note = client.post("/api/notes?wait=true", json={"text": "water plants"}).json()["note"]
    day = to_local(now_utc(), TZ).date() + timedelta(days=2)
    updated = client.post(
        f"/api/notes/{note['id']}/reminders",
        json={"when": f"{day}T08:30", "message": "Water the plants", "repeat": "FREQ=WEEKLY"},
    ).json()
    manual = [r for r in updated["reminders"] if r["origin"] == "manual"][0]
    assert manual["rrule"] == "FREQ=WEEKLY"
    assert to_local(_dt(manual["due_at"]), TZ).strftime("%H:%M") == "08:30"
    bad = client.post(f"/api/notes/{note['id']}/reminders", json={"when": "soon"})
    assert bad.status_code == 422
    rid = manual["id"]
    moved = client.patch(f"/api/reminders/{rid}", json={"when": f"{day}T09:15"}).json()
    assert to_local(_dt(moved["due_at"]), TZ).strftime("%H:%M") == "09:15"


def _dt(value):
    from magpie.timeutil import parse_iso

    return parse_iso(value)


def test_prefs(client):
    prefs = client.get("/api/prefs").json()
    assert prefs["timezone"] == TZ
    bad = client.put("/api/prefs", json={"timezone": "Mars/Olympus"})
    assert bad.status_code == 422 and "time zone" in bad.json()["detail"]
    saved = client.put(
        "/api/prefs", json={"proactivity": "proactive", "telegram_chat_id": 666, "nonsense": 1}
    ).json()
    assert saved["proactivity"] == "proactive"
    assert saved["telegram_chat_id"] is None  # cannot be set from the API
    assert client.put("/api/prefs", json={"speech_lang": "en-AU"}).json()["speech_lang"] == "en-AU"
    other = client.put("/api/prefs", json={"speech_lang": "fr-FR"})
    assert other.status_code == 422 and "English" in other.json()["detail"]


def test_status(client):
    status = client.get("/api/status").json()
    assert status["brain"]["available"] is True
    assert {c["name"] for c in status["channels"]} == {
        "telegram",
        "webpush",
        "ntfy",
        "email",
        "webhook",
    }
    assert status["push"]["public_key"]
    assert status["counts"]["inbox"] == 0


def test_export_and_import_roundtrip(client, core):
    client.post(
        "/api/notes?wait=true", json={"text": "remember the wifi password is on the router"}
    )
    exported = client.get("/api/export?format=json")
    assert exported.headers["content-disposition"].startswith("attachment")
    payload = exported.json()
    assert payload["app"] == "magpie" and len(payload["notes"]) == 1
    markdown = client.get("/api/export?format=markdown").text
    assert "# Magpie notes" in markdown

    files = {"file": ("export.json", json.dumps(payload), "application/json")}
    again = client.post("/api/import", files=files).json()
    assert again == {"imported": 0, "skipped": 1}  # duplicates are skipped
    core.store.delete_note(payload["notes"][0]["id"])
    restored = client.post("/api/import", files=files).json()
    assert restored == {"imported": 1, "skipped": 0}
    bad = client.post("/api/import", files={"file": ("x.json", "{}", "application/json")})
    assert bad.status_code == 422


def test_calendar_feed(client, core, brain):
    day = to_local(now_utc(), TZ).date() + timedelta(days=1)
    brain.next_triage = make_triage(
        reminders=[reminder(f"{day}T17:00", message="Call James, about money; now")],
        ping="explicit",
    )
    client.post("/api/notes?wait=true", json={"text": "x"})
    url = client.get("/api/calendar").json()["url"]
    assert url.startswith("https://notes.example.com/calendar/")
    path = url.replace("https://notes.example.com", "")
    feed = client.get(path)
    assert feed.headers["content-type"].startswith("text/calendar")
    assert "SUMMARY:Call James\\, about money\\; now" in feed.text
    assert "\r\n" in feed.text
    assert client.get("/calendar/wrong.ics").status_code == 404
    rotated = client.post("/api/calendar/rotate").json()["url"]
    assert client.get(path).status_code == 404
    assert client.get(rotated.replace("https://notes.example.com", "")).status_code == 200


def test_signed_action_links(client, core):
    reminder_row = core.store.add_reminder(None, now_utc() + timedelta(hours=1), "Stretch")
    links = core.reminder_actions(reminder_row.id)
    done_path = links["done"].replace("https://notes.example.com", "")
    # GET only shows a confirmation, so link scanners can't act.
    page = client.get(done_path)
    assert page.status_code == 200 and "Mark as done" in page.text
    assert core.store.get_reminder(reminder_row.id).status == ReminderStatus.SCHEDULED
    performed = client.post(done_path, headers={"Accept": "application/json"})
    assert performed.json()["status"] == "done"
    snooze_path = links["snooze"].replace("https://notes.example.com", "")
    assert "Snoozed" in client.post(snooze_path).text
    assert client.post("/a/forged-token").status_code == 404


def test_security_headers(client):
    response = client.get("/")
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert response.headers["x-frame-options"] == "DENY"
    assert client.get("/api/notes").headers["cache-control"] == "no-store"
    assert client.get("/sw.js").headers["service-worker-allowed"] == "/"
    assert client.get("/manifest.webmanifest").json()["share_target"]["action"] == "/share"
    assert client.get("/share?url=https://example.com").status_code == 200


def test_token_rotation(client, core):
    old = client.get("/api/token").json()["token"]
    new = client.post("/api/token/rotate").json()["token"]
    assert new != old and core.secrets.check_token(new)


def test_digest_and_ask(client, brain):
    client.post("/api/notes?wait=true", json={"text": "idea: solar panels on the roof"})
    digest = client.post("/api/digests", json={"kind": "daily", "send": False}).json()
    assert digest["kind"] == "daily"
    assert client.get("/api/digests").json()["digests"][0]["id"] == digest["id"]
    answer = client.post("/api/ask", json={"question": "what about solar?"}).json()
    assert answer["notes"] and answer["notes"][0]["title"]


def test_telegram_pairing_needs_a_bot(client):
    assert client.post("/api/telegram/pair").status_code == 400


def test_audio_needs_server_transcription(client):
    files = {"file": ("note.webm", b"\x1a\x45\xdf\xa3", "audio/webm")}
    assert client.post("/api/notes/audio", files=files).status_code == 503


def test_impossible_ids_are_not_found(client):
    huge = 10**30
    assert client.get(f"/api/notes/{huge}").status_code == 404
    assert client.patch(f"/api/notes/{huge}", json={"title": "x"}).status_code == 404
    assert client.delete(f"/api/notes/{huge}").status_code == 404
    assert client.post(f"/api/reminders/{huge}/done").status_code == 404
    assert (
        client.patch(f"/api/reminders/{huge}", json={"when": "2026-12-01T09:00"}).status_code == 404
    )
    assert client.post(f"/api/links/{huge}/retry").status_code == 404
    assert client.get("/api/notes", params={"offset": 10**20}).status_code == 422


def test_blank_and_out_of_range_input_is_refused(client):
    assert client.post("/api/notes", json={"text": "   \n "}).status_code == 422
    note = client.post("/api/notes", json={"text": "plan the trip"}).json()["note"]
    response = client.post(
        f"/api/notes/{note['id']}/reminders", json={"when": "9999-12-31T23:59", "message": "x"}
    )
    assert response.status_code == 422
    response = client.post(
        f"/api/notes/{note['id']}/reminders",
        json={"when": "2030-01-01T09:00", "repeat": "FREQ=WEEKLY;INTERVAL=0"},
    )
    assert response.status_code == 422
    assert client.put("/api/prefs", json={"about_me": "x" * 5000}).status_code == 422


def test_calendar_and_action_links_with_odd_tokens(client):
    assert client.get("/calendar/%C3%A9t%C3%A9.ics").status_code == 404
    assert client.get("/a/not-a-token").status_code == 404
    app = client.app.state.magpie
    reminder = app.store.add_reminder(None, now_utc() + timedelta(hours=1), "Stretch")
    token = app.signer.sign(reminder.id, "done")
    app.store.delete_reminder(reminder.id)
    page = client.get(f"/a/{token}")
    assert page.status_code == 404 and "no longer exists" in page.text


def test_import_skips_bad_notes_without_leaving_half_of_one(client):
    good = {
        "id": 1,
        "created_at": "2026-01-02T10:00:00",  # no zone: taken as UTC
        "updated_at": "2026-01-02T10:00:00",
        "source": "web",
        "raw_text": "bring the passport",
        "links": [
            {
                "id": 1,
                "note_id": 1,
                "url": "javascript:alert(1)",
                "kind": "article",
                "state": "ready",
                "created_at": "2026-01-02T10:00:00Z",
                "updated_at": "2026-01-02T10:00:00Z",
            }
        ],
        "reminders": [
            {
                "id": 1,
                "note_id": 1,
                "due_at": "2099-01-01T09:00:00",
                "message": "Passport",
                "origin": "explicit",
                "status": "scheduled",
                "rrule": "FREQ=WEEKLY;INTERVAL=0",
                "created_at": "2026-01-02T10:00:00Z",
                "updated_at": "2026-01-02T10:00:00Z",
            }
        ],
    }
    payload = {"app": "magpie", "notes": [good, {"id": "x"}, "nonsense", 42]}
    response = client.post(
        "/api/import", files={"file": ("export.json", json.dumps(payload), "application/json")}
    )
    assert response.status_code == 200
    assert response.json() == {"imported": 1, "skipped": 3}
    notes = client.get("/api/notes", params={"view": "all"}).json()["notes"]
    assert [n["raw_text"] for n in notes] == ["bring the passport"]
    assert notes[0]["links"] == []  # the javascript: link was dropped
    assert [r["rrule"] for r in notes[0]["reminders"]] == [None]
    for bad in ({"app": "magpie", "notes": 5}, [1, 2], "[" * 100_000):
        body = bad if isinstance(bad, str) else json.dumps(bad)
        response = client.post("/api/import", files={"file": ("x.json", body, "application/json")})
        assert response.status_code == 422


def test_unexpected_errors_come_back_as_json(core, settings, monkeypatch):
    app = create_app(settings, magpie=core, background=False)
    with TestClient(app, base_url="https://testserver", raise_server_exceptions=False) as plain:
        plain.post("/api/login", json={"password": "correct horse"})

        def broken():
            raise RuntimeError("database on fire")

        monkeypatch.setattr(core.store, "count_notes", broken)
        response = plain.get("/api/notes")
        assert response.status_code == 500
        assert "went wrong" in response.json()["detail"]
        assert "fire" not in response.text


def test_oversized_uploads_are_refused():
    from fastapi import FastAPI, Request
    from fastapi.responses import PlainTextResponse

    from magpie.app import BodyLimit

    app = FastAPI()  # the same middleware order as Magpie's app, with a tiny limit

    @app.post("/")
    async def echo(request: Request):
        return PlainTextResponse(str(len(await request.body())))

    app.add_middleware(BodyLimit, limit=10)
    with TestClient(app) as small:
        assert small.post("/", content=b"12345").text == "5"
        assert small.post("/", content=b"x" * 11).status_code == 413

        def chunks():
            yield b"x" * 8
            yield b"x" * 8

        assert small.post("/", content=chunks()).status_code == 413  # no Content-Length


def test_bad_settings_do_not_stop_the_app():
    from magpie.config import Settings

    settings = Settings(_env_file=None, timezone="Mars/Olympus", log_level="loud")
    assert settings.timezone == "UTC"
    assert settings.log_level == "info"


def test_damaged_prefs_fall_back_to_defaults(core):
    core.store.set_kv("prefs", "garbage")
    assert core.prefs().timezone == TZ
    core.store.set_kv("prefs", {"timezone": "Mars/Olympus", "digest_time": "25:00", "about_me": 7})
    prefs = core.prefs()
    assert prefs.timezone == TZ and prefs.digest_time == "08:00"


def test_half_a_character_never_breaks_a_response(client):
    raw = '{"text": "call mom \\ud800 at 5"}'
    created = client.post("/api/notes", content=raw, headers={"Content-Type": "application/json"})
    assert created.status_code == 422
    assert created.json()["detail"].startswith("text: ")
    bad = client.post(
        "/api/ask",
        content='{"question": [1, "\\ud800"]}',
        headers={"Content-Type": "application/json"},
    )
    assert bad.status_code == 422 and "question" in bad.json()["detail"]
    prefs = client.put(
        "/api/prefs",
        content='{"about_me": "\\udfff"}',
        headers={"Content-Type": "application/json"},
    )
    assert prefs.status_code == 422
    assert client.get("/api/notes").status_code == 200  # the connection is still fine
    # Paths without validation (Telegram, imports) are cleaned before they are stored.
    app = client.app.state.magpie
    note = app.store.create_note("half \ud83d a smile", "telegram")
    assert app.store.get_note(note.id).raw_text == "half � a smile"


def test_snooze_uses_the_length_from_settings(client):
    assert client.put("/api/prefs", json={"snooze_minutes": 15}).status_code == 200
    app = client.app.state.magpie
    reminder = app.store.add_reminder(None, now_utc(), "Stretch")
    result = client.post(f"/api/reminders/{reminder.id}/snooze").json()
    due = app.store.get_reminder(reminder.id).due_at
    assert timedelta(minutes=14) < due - now_utc() <= timedelta(minutes=15)
    assert result["status"] == "scheduled"
    token = app.signer.sign(reminder.id, "snooze")
    assert "Snooze for 15 min" in client.get(f"/a/{token}").text
    assert client.put("/api/prefs", json={"snooze_minutes": 1}).status_code == 422


def test_standing_instructions_reach_claude(client, brain):
    rules = "Always tag work notes with #office."
    assert client.put("/api/prefs", json={"instructions": rules}).status_code == 200
    client.post("/api/notes?wait=true", json={"text": "budget meeting notes"})
    assert brain.contexts[-1].instructions == rules
    from magpie.brain.prompts import triage_message

    assert rules in triage_message("x", brain.contexts[-1])


def test_claude_key_can_be_added_from_the_dashboard(tmp_path, router, monkeypatch):
    from types import SimpleNamespace

    import anthropic
    import httpx2

    from magpie import core as core_module
    from magpie.brain.claude import ClaudeBrain
    from magpie.config import Settings
    from magpie.core import Magpie

    async def retrieve(model, **kwargs):
        if fake.key == "sk-ant-bad-key":
            request = httpx2.Request("GET", "https://api.anthropic.com/v1/models/x")
            raise anthropic.AuthenticationError(
                "invalid x-api-key", response=httpx2.Response(401, request=request), body=None
            )
        return SimpleNamespace(id=model)

    fake = SimpleNamespace(key=None, models=SimpleNamespace(retrieve=retrieve))

    def make_brain(settings, api_key=None, *, config=None, http=None):
        key = config.api_key if config is not None else api_key
        fake.key = key
        return ClaudeBrain(settings, client=fake if key else None, api_key=key)

    monkeypatch.setattr(core_module, "make_brain", make_brain)
    settings = Settings(_env_file=None, data_dir=tmp_path, password="pw", brain="claude")
    magpie = Magpie(settings, http=httpx2.AsyncClient(transport=httpx2.MockTransport(router)))
    app = create_app(settings, magpie=magpie, background=False)
    with TestClient(app) as http:
        http.headers["X-Magpie"] = "1"
        http.post("/api/login", json={"password": "pw"})
        status = http.get("/api/status").json()
        assert status["brain"]["available"] is False and status["brain"]["key_source"] is None
        note = http.post("/api/notes", json={"text": "remind me to stretch"}).json()["note"]
        assert note["state"] == "pending"  # waits for a key

        refused = http.put("/api/claude/key", json={"key": "sk-ant-bad-key"})
        assert refused.status_code == 422 and "rejected" in refused.json()["detail"]
        assert http.get("/api/status").json()["brain"]["available"] is False

        assert http.put("/api/claude/key", json={"key": "sk-ant good"}).status_code == 422
        saved = http.put("/api/claude/key", json={"key": "sk-ant-good-key"})
        assert saved.json() == {"available": True, "warning": None}
        assert http.get("/api/status").json()["brain"]["key_source"] == "dashboard"
        assert "sk-ant-good-key" not in http.get("/api/status").text  # never sent back

        assert http.delete("/api/claude/key").json() == {"available": False}
        assert http.get("/api/status").json()["brain"]["key_source"] is None


def test_a_key_from_the_environment_cannot_be_changed_from_the_dashboard(client, monkeypatch):
    app = client.app.state.magpie
    monkeypatch.setattr(app.settings, "brain", "claude")
    monkeypatch.setattr(app.settings, "anthropic_api_key", "sk-ant-from-env")
    response = client.put("/api/claude/key", json={"key": "sk-ant-other-key"})
    assert response.status_code == 409 and "ANTHROPIC_API_KEY" in response.json()["detail"]


def test_fresh_installs_report_an_unset_time_zone(tmp_path, router):
    import httpx2

    from magpie.config import Settings
    from magpie.core import Magpie

    settings = Settings(_env_file=None, data_dir=tmp_path, password="pw", brain="fake")
    magpie = Magpie(settings, http=httpx2.AsyncClient(transport=httpx2.MockTransport(router)))
    app = create_app(settings, magpie=magpie, background=False)
    with TestClient(app) as http:
        http.headers["X-Magpie"] = "1"
        http.post("/api/login", json={"password": "pw"})
        assert http.get("/api/status").json()["timezone_unset"] is True
        http.put("/api/prefs", json={"timezone": "Europe/London"})
        assert http.get("/api/status").json()["timezone_unset"] is False


def test_a_retried_note_is_not_saved_twice(client):
    body = {"text": "buy milk", "source": "web-text", "client_id": "3f1c2a9e-77b0-4c41"}
    first = client.post("/api/notes", json=body).json()["note"]
    again = client.post("/api/notes", json=body).json()["note"]
    assert again["id"] == first["id"]
    notes = client.get("/api/notes", params={"view": "all"}).json()["notes"]
    assert [n["raw_text"] for n in notes] == ["buy milk"]
    assert client.post("/api/notes", json={**body, "client_id": "bad id!"}).status_code == 422
