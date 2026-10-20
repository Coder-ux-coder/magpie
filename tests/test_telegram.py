import json
from datetime import timedelta

import httpx2
import pytest

from magpie.config import Settings
from magpie.core import Magpie
from magpie.models import ProcState, ReminderStatus
from magpie.schemas import LinkReview
from magpie.telegram import format_review
from magpie.timeutil import now_utc

from .conftest import TZ, make_triage

CHAT = 555


@pytest.fixture
def tg(tmp_path, router, brain):
    router.add("POST", "https://api.telegram.org", {"ok": True, "result": {"message_id": 1}})
    router.add(
        "POST",
        "https://api.telegram.org/bot123:abc/getFile",
        {"ok": True, "result": {"file_path": "voice/a.oga", "file_size": 10}},
    )
    router.add(
        "GET",
        "https://api.telegram.org/file/bot123:abc/voice/a.oga",
        lambda r: httpx2.Response(200, content=b"OggS..."),
    )
    router.add(
        "POST",
        "https://stt.example.com/v1/audio/transcriptions",
        {"text": "remind me to call mom at six"},
    )
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        password="pw",
        timezone=TZ,
        brain="fake",
        telegram_bot_token="123:abc",
        stt_api_key="sk-test",
        stt_base_url="https://stt.example.com/v1",
        base_url="https://notes.example.com",
    )
    core = Magpie(
        settings, brain=brain, http=httpx2.AsyncClient(transport=httpx2.MockTransport(router))
    )
    core.bot.username = "magpie_test_bot"
    return core


def message(text=None, chat=CHAT, **extra):
    data = {"message_id": 10, "chat": {"id": chat, "type": "private"}, **extra}
    if text is not None:
        data["text"] = text
    return {"update_id": 1, "message": data}


def pair(core):
    prefs = core.prefs()
    prefs.telegram_chat_id = CHAT
    core.store.save_prefs(prefs)


def sent_texts(router):
    return [call.get("text", "") for call in router.calls("/sendMessage")]


async def test_pairing_with_a_code(tg, router):
    code = tg.bot.new_pairing_code()
    assert code["link"] == f"https://t.me/magpie_test_bot?start={code['code']}"
    await tg.bot.handle_update(message("/start 000000"))
    assert tg.telegram_chat_id() is None  # wrong code
    await tg.bot.handle_update(message(f"/start {code['code']}"))
    assert tg.telegram_chat_id() == CHAT
    assert any("Connected" in text for text in sent_texts(router))
    # Codes are single-use.
    await tg.bot.handle_update(message(f"/start {code['code']}", chat=777))
    assert tg.telegram_chat_id() == CHAT


async def test_strangers_are_turned_away_quietly(tg, router):
    pair(tg)
    await tg.bot.handle_update(message("hi", chat=999))
    await tg.bot.handle_update(message("hello?", chat=999))
    assert tg.store.list_notes() == []
    assert sum("private Magpie bot" in text for text in sent_texts(router)) == 1


async def test_text_message_becomes_a_note_and_gets_a_reply(tg, router, brain):
    pair(tg)
    brain.next_triage = make_triage(reply="Saved. I'll remind you at 5:00 PM.")
    await tg.bot.handle_update(message("call james at 5"))
    await tg.pipeline.drain()
    notes = tg.store.list_notes()
    assert len(notes) == 1
    assert notes[0].source == "telegram"
    assert notes[0].meta["telegram"] == {"chat_id": CHAT, "message_id": 10}
    reply = router.calls("/sendMessage")[-1]
    assert "Call James about the budget" in reply["text"]
    assert "remind you at 5:00 PM" in reply["text"]
    assert reply["reply_parameters"]["message_id"] == 10
    assert (
        reply["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == f"n:undo:{notes[0].id}"
    )


async def test_forwarded_messages_keep_their_origin(tg):
    pair(tg)
    origin = {"type": "user", "sender_user": {"first_name": "Sara", "last_name": "K"}}
    await tg.bot.handle_update(message("meeting moved to 3", forward_origin=origin))
    await tg.pipeline.drain()
    assert tg.store.list_notes()[0].raw_text.startswith("Forwarded from Sara K:")


async def test_voice_messages_are_transcribed(tg, router):
    pair(tg)
    await tg.bot.handle_update(
        message(voice={"file_id": "abc", "mime_type": "audio/ogg", "duration": 3})
    )
    await tg.pipeline.drain()
    note = tg.store.list_notes()[0]
    assert note.source == "telegram-voice"
    assert note.raw_text == "remind me to call mom at six"
    upload = [r for r in router.requests if "audio/transcriptions" in str(r.url)][0]
    assert upload.headers["authorization"] == "Bearer sk-test"
    assert b"recording.ogg" in upload.content
    assert b'name="language"\r\n\r\nen\r\n' in upload.content  # Magpie expects English


async def test_voice_without_transcription_explains_itself(tg, router):
    pair(tg)
    tg.settings.stt_api_key = None
    await tg.bot.handle_update(message(voice={"file_id": "abc"}))
    assert tg.store.list_notes() == []
    assert any("MAGPIE_STT_API_KEY" in text for text in sent_texts(router))


async def test_reminder_buttons(tg, router):
    pair(tg)
    note = tg.store.create_note("task")
    tg.store.update_note(note.id, kind="task")
    reminder = tg.store.add_reminder(note.id, now_utc(), "Call James")
    query = {
        "id": "q1",
        "data": f"r:snz:{reminder.id}:30",
        "message": {"message_id": 3, "chat": {"id": CHAT}},
    }
    await tg.bot.handle_update({"update_id": 2, "callback_query": query})
    assert tg.store.get_reminder(reminder.id).due_at > now_utc() + timedelta(minutes=25)
    answer = router.calls("answerCallbackQuery")[-1]
    assert answer["text"].startswith("Snoozed until")

    query["data"] = f"r:done:{reminder.id}"
    await tg.bot.handle_update({"update_id": 3, "callback_query": query})
    assert tg.store.get_reminder(reminder.id).status == ReminderStatus.DONE
    assert tg.store.get_note(note.id).status == "done"

    query["data"] = f"n:undo:{note.id}"
    await tg.bot.handle_update({"update_id": 4, "callback_query": query})
    assert tg.store.get_note(note.id) is None

    query["data"] = "r:done:9999"
    await tg.bot.handle_update({"update_id": 5, "callback_query": query})
    assert router.calls("answerCallbackQuery")[-1]["text"] == "That item no longer exists."


async def test_callbacks_from_strangers_are_ignored(tg, router):
    pair(tg)
    reminder = tg.store.add_reminder(None, now_utc(), "x")
    query = {
        "id": "q",
        "data": f"r:done:{reminder.id}",
        "message": {"message_id": 1, "chat": {"id": 999}},
    }
    await tg.bot.handle_update({"update_id": 1, "callback_query": query})
    assert tg.store.get_reminder(reminder.id).status == ReminderStatus.SCHEDULED


async def test_commands(tg, router):
    from magpie.timeutil import at_local_time, to_local

    pair(tg)
    noon = at_local_time(to_local(now_utc(), TZ).date(), "12:00", TZ)  # today, whatever the time
    tg.store.add_reminder(None, noon, "Stand-up call")
    note = tg.store.create_note("budget review with James")
    tg.store.update_note(note.id, title="Budget review", state=ProcState.READY)
    for command in (
        "/today",
        "/upcoming",
        "/search budget",
        "/ask what about the budget?",
        "/help",
        "/digest",
        "/nope",
    ):
        await tg.bot.handle_update(message(command))
    texts = sent_texts(router)
    assert any("Stand-up call" in t and "<b>Today</b>" in t for t in texts)
    assert any("Budget review" in t and "#" in t for t in texts)
    assert any("Commands:" in t for t in texts)
    assert any("don't know that command" in t for t in texts)


def test_review_formatting():
    from magpie.models import Link

    link = Link(
        id=1,
        note_id=1,
        url="https://youtu.be/x",
        kind="youtube",
        title="Talk",
        state="ready",
        minutes=12,
        created_at=now_utc(),
        updated_at=now_utc(),
        review=LinkReview(
            title="A <great> talk",
            tldr="Short.",
            key_points=["One", "Two"],
            verdict="worth_it",
            rating=4,
            best_for="Builders",
            caveats="",
            why_saved="w",
            minutes=None,
        ),
    )
    text = format_review(link)
    assert "📺 <b>A &lt;great&gt; talk</b>" in text
    assert "worth it · 4/5 · 12 min" in text
    assert "• One" in text


async def test_strangers_get_a_reply_right_after_a_reboot(tg, router, monkeypatch):
    from magpie import telegram

    monkeypatch.setattr(telegram.time, "monotonic", lambda: 5.0)  # the machine just booted
    pair(tg)
    await tg.bot.handle_update(message("hi", chat=999))
    assert sum("private Magpie bot" in text for text in sent_texts(router)) == 1


def _polling_script(router, *answers):
    """Make getUpdates return each answer in turn, then stop the bot."""
    import asyncio

    answers = list(answers)

    def handler(request):
        if not answers:
            raise asyncio.CancelledError  # stands in for shutdown
        answer = answers.pop(0)
        if isinstance(answer, httpx2.Response):
            return answer
        return httpx2.Response(200, json=answer)

    router.add(
        "POST",
        "https://api.telegram.org/bot123:abc/getMe",
        {"ok": True, "result": {"username": "magpie_test_bot"}},
    )
    router.add("POST", "https://api.telegram.org/bot123:abc/getUpdates", handler)


async def test_polling_survives_errors_and_odd_updates(tg, router, monkeypatch):
    import asyncio

    from magpie import telegram

    real_sleep = asyncio.sleep

    async def no_wait(_seconds):
        await real_sleep(0)

    monkeypatch.setattr(telegram.asyncio, "sleep", no_wait)
    pair(tg)
    good = message("buy milk")
    good["update_id"] = 7
    _polling_script(
        router,
        httpx2.Response(502, text="<html>Bad Gateway</html>"),
        {"ok": False, "error_code": 409, "description": "Conflict: terminated by other getUpdates"},
        {"ok": False, "error_code": 429, "parameters": {"retry_after": 3}},
        {"ok": True, "result": {"not": "a list"}},
        {"ok": True, "result": [{"no_update_id": True}, {"update_id": 6, "message": "odd"}, good]},
    )
    with pytest.raises(asyncio.CancelledError):
        await tg.bot.run()
    await tg.pipeline.drain()
    assert [n.raw_text for n in tg.store.list_notes()] == ["buy milk"]
    assert tg.store.get_kv("telegram_offset") == 8


async def test_a_revoked_token_stops_polling_quietly(tg, router):
    _polling_script(router, {"ok": False, "error_code": 401, "description": "Unauthorized"})
    await tg.bot.run()  # returns instead of retrying forever


async def test_markup_errors_fall_back_to_plain_text(tg, router):
    def send(request):
        body = json.loads(request.content)
        if body.get("parse_mode") == "HTML":
            return httpx2.Response(
                400,
                json={
                    "ok": False,
                    "error_code": 400,
                    "description": "Bad Request: can't parse entities: unclosed start tag",
                },
            )
        return httpx2.Response(200, json={"ok": True, "result": {"message_id": 2}})

    router.add("POST", "https://api.telegram.org/bot123:abc/sendMessage", send)
    await tg.telegram_api.send_message(CHAT, "<b>Rock &amp; roll")
    last = router.calls("sendMessage")[-1]
    assert last["text"] == "Rock & roll" and "parse_mode" not in last


async def test_refused_link_buttons_are_dropped(tg, router):
    def send(request):
        body = json.loads(request.content)
        rows = (body.get("reply_markup") or {}).get("inline_keyboard", [])
        if any("url" in button for row in rows for button in row):
            return httpx2.Response(
                400,
                json={
                    "ok": False,
                    "error_code": 400,
                    "description": "Bad Request: BUTTON_URL_INVALID",
                },
            )
        return httpx2.Response(200, json={"ok": True, "result": {"message_id": 2}})

    router.add("POST", "https://api.telegram.org/bot123:abc/sendMessage", send)
    buttons = [
        [{"text": "Done", "callback_data": "r:done:1"}, {"text": "Open", "url": "http://10.0.0.5"}]
    ]
    await tg.telegram_api.send_message(CHAT, "Reminder", buttons=buttons)
    last = router.calls("sendMessage")[-1]
    assert last["reply_markup"]["inline_keyboard"] == [
        [{"text": "Done", "callback_data": "r:done:1"}]
    ]


def test_long_messages_split_within_telegrams_limit():
    from magpie.telegram_api import _units, split_message

    text = "&amp; " * 1500 + "\n" + "😀" * 3000 + "\n" + "<b>title</b>"
    parts = split_message(text)
    assert all(_units(part) <= 4000 for part in parts)
    assert not any(part.rstrip().endswith(("&", "&am", "&amp")) for part in parts)
    assert "".join(parts).count("😀") == 3000


async def test_buttons_still_work_when_telegram_refuses_the_answer(tg, router):
    router.add(
        "POST",
        "https://api.telegram.org/bot123:abc/answerCallbackQuery",
        {"ok": False, "error_code": 400, "description": "Bad Request: query is too old"},
    )
    pair(tg)
    reminder = tg.store.add_reminder(None, now_utc(), "Call James")
    query = {
        "id": "q1",
        "data": f"r:snz:{reminder.id}:30",
        "message": {"message_id": 3, "chat": {"id": CHAT}},
    }
    await tg.bot.handle_update({"update_id": 2, "callback_query": query})
    assert tg.store.get_reminder(reminder.id).due_at > now_utc() + timedelta(minutes=25)
    assert router.calls("editMessageReplyMarkup")  # the buttons were still updated


async def test_voice_notes_over_the_bot_limit_are_explained(tg, router):
    pair(tg)
    await tg.bot.handle_update(message(voice={"file_id": "big", "file_size": 30 * 1024 * 1024}))
    assert any("too long" in text for text in sent_texts(router))
    assert not router.calls("getFile")


async def test_digest_command_always_answers(tg, router, monkeypatch):
    pair(tg)

    async def nothing(*args, **kwargs):
        return None

    monkeypatch.setattr(tg.digests, "run", nothing)
    await tg.bot.handle_update(message("/digest"))
    assert any("Nothing to report" in text for text in sent_texts(router))
