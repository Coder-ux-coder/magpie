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
    await tg.bot.handle_update(message("call ahmed at 5"))
    await tg.pipeline.drain()
    notes = tg.store.list_notes()
    assert len(notes) == 1
    assert notes[0].source == "telegram"
    assert notes[0].meta["telegram"] == {"chat_id": CHAT, "message_id": 10}
    reply = router.calls("/sendMessage")[-1]
    assert "Call Ahmed about the budget" in reply["text"]
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
    reminder = tg.store.add_reminder(note.id, now_utc(), "Call Ahmed")
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
    pair(tg)
    tg.store.add_reminder(None, now_utc() + timedelta(minutes=30), "Stand-up call")
    note = tg.store.create_note("budget review with Ahmed")
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
