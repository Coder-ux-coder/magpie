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
        "/api/notes?wait=true", json={"text": "call ahmed at 5", "source": "WEIRD SOURCE!"}
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
    assert upcoming[0]["note_title"] == "Call Ahmed about the budget"
    assert upcoming[0]["when"] == "tomorrow 5:00 PM"

    snoozed = client.post(f"/api/reminders/{rid}/snooze", json={"minutes": 30}).json()
    assert snoozed["status"] == "scheduled"
    assert client.post(f"/api/reminders/{rid}/tomorrow").json()["when"].startswith("tomorrow")
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
        reminders=[reminder(f"{day}T17:00", message="Call Ahmed, about money; now")],
        ping="explicit",
    )
    client.post("/api/notes?wait=true", json={"text": "x"})
    url = client.get("/api/calendar").json()["url"]
    assert url.startswith("https://notes.example.com/calendar/")
    path = url.replace("https://notes.example.com", "")
    feed = client.get(path)
    assert feed.headers["content-type"].startswith("text/calendar")
    assert "SUMMARY:Call Ahmed\\, about money\\; now" in feed.text
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
