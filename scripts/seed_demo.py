"""Fill a fresh Magpie data directory with sample notes, for UI work and screenshots.

    MAGPIE_DATA_DIR=demo-data python scripts/seed_demo.py
    MAGPIE_DATA_DIR=demo-data MAGPIE_BRAIN=fake MAGPIE_LOGIN=false magpie serve

The notes are written straight into the database, as if the AI had already
filed them. Nothing is sent to any API.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from magpie.config import Prefs, Settings
from magpie.db import Database
from magpie.models import ProcState
from magpie.schemas import LinkReview
from magpie.store import Store
from magpie.timeutil import local_now, localize, now_utc


def main() -> None:
    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    store = Store(Database(settings.db_path), settings)
    tz = settings.timezone if settings.timezone != "UTC" else "Europe/London"
    # Set up already, so the welcome screen doesn't cover the sample notes.
    store.save_prefs(Prefs(timezone=tz, onboarded=True))
    today = local_now(tz).date()
    work = store.add_category("Work", "blue").id
    home = store.add_category("Home", "green").id
    family = store.add_category("Family", "pink").id

    def at(days: int, hour: int, minute: int = 0):
        day = today + timedelta(days=days)
        return localize(datetime(day.year, day.month, day.day, hour, minute), tz)

    def note(raw, minutes_ago, **fields):
        written_in = fields.pop("written_in", "Inbox")
        created = store.create_note(
            raw, fields.pop("source", "web-voice"), {"captured_in": written_in}
        )
        store.set_created_at(created.id, now_utc() - timedelta(minutes=minutes_ago))
        store.update_note(created.id, state=ProcState.READY, **fields)
        return created.id

    groceries = note(
        "groceries milk eggs bread lemons and uh tea bags",
        300,
        source="telegram-voice",
        title="Groceries",
        text="- [x] Milk\n- Eggs\n- Bread\n- Lemons\n- Tea bags",
        kind="list",
        tags=["home"],
        category_id=home,
        ping="none",
        reply="Saved to lists.",
    )
    store.update_note(groceries, pinned=True)

    note(
        "had a really good chat with dad today he's feeling much better after the surgery",
        900,
        title="Good chat with Dad",
        text="Had a really good chat with Dad today. He's feeling much better after the surgery.",
        kind="journal",
        tags=["family"],
        category_id=family,
        ping="none",
        ping_reason="A journal entry, nothing to follow up.",
        reply="Saved to your journal.",
    )

    mum = note(
        "call mum on saturday morning",
        200,
        title="Call Mum",
        text="Call Mum on Saturday morning.",
        kind="task",
        tags=["family"],
        category_id=family,
        ping="explicit",
        ping_reason="You said Saturday morning.",
        reply="Saved. I'll remind you on Saturday at 11:00 AM to call Mum.",
    )
    saturday = (5 - today.weekday()) % 7 or 7
    store.add_reminder(mum, at(saturday, 11), "Call Mum", origin="explicit")

    weekly = note(
        "every monday at 9 review the weekly plan",
        2000,
        title="Review the weekly plan",
        text="Every Monday at 9, review the weekly plan.",
        kind="task",
        tags=["work", "planning"],
        category_id=work,
        written_in="Work",
        ping="explicit",
        ping_reason="A weekly reminder, Mondays at 9:00 AM.",
        reply="Saved. I'll remind you every Monday at 9:00 AM.",
    )
    monday = (0 - today.weekday()) % 7 or 7
    store.add_reminder(
        weekly,
        at(monday, 9),
        "Review the weekly plan",
        origin="explicit",
        rrule="FREQ=WEEKLY;BYDAY=MO",
    )

    note(
        "idea what if reminders could reply with a short voice message instead of text",
        95,
        title="Voice replies for reminders",
        text="Idea: what if reminders could reply with a short voice message instead of text?",
        kind="idea",
        tags=["ideas", "magpie"],
        ping="digest",
        in_digest=True,
        ping_reason="No deadline, so it'll be in tomorrow's digest.",
        reply="Saved to ideas.",
    )

    essay = note(
        "read this later https://paulgraham.com/ds.html",
        60,
        source="share",
        title="Do Things That Don't Scale",
        text="Read this later: https://paulgraham.com/ds.html",
        kind="link",
        tags=["startups", "reading"],
        ping="digest",
        in_digest=True,
        ping_reason="Something to read, so it'll be in your digest.",
        reply="Saved. I'll review the essay for you.",
        search_extra="Paul Graham essay startups recruit users manually",
    )
    link = store.add_link(essay, "https://paulgraham.com/ds.html", "article")
    store.update_link(
        link.id,
        state=ProcState.READY,
        title="Do Things That Don't Scale",
        author="Paul Graham",
        site="paulgraham.com",
        minutes=21,
        review=LinkReview(
            title="Do Things That Don't Scale",
            tldr="Startups rarely take off on their own: early on, founders should do laborious, "
            "manual things (recruiting users one by one, over-serving them) that would never "
            "work at scale.",
            key_points=[
                "Recruit your first users by hand; growth doesn't start by itself.",
                "Make early users not just satisfied but delighted, with unusual attention.",
                "Doing things manually for a narrow first market teaches you what to automate.",
                "Treat the fragile early phase as a different job from running a big company.",
            ],
            verdict="must",
            rating=5,
            best_for="Anyone starting something new and waiting for users to appear.",
            caveats="Written in 2013 for software startups; the examples are dated, the advice "
            "isn't.",
            why_saved="You saved it while planning how to find Magpie's first users. It's "
            "directly useful.",
            minutes=21,
        ),
    )

    bill = note(
        "electricity bill is due on the 10th I think",
        40,
        title="Pay the electricity bill",
        text="The electricity bill is due on the 10th.",
        kind="task",
        tags=["money", "home"],
        category_id=home,
        ping="suggested",
        ping_reason="It's due on the 10th, so I'll nudge you the evening before.",
        reply="Saved. I'll remind you tomorrow at 6:00 PM, before it's due.",
    )
    store.add_reminder(
        bill,
        at(1, 18),
        "Pay the electricity bill (due soon)",
        origin="suggested",
        reason="Due on the 10th, so the evening before.",
    )

    budget = note(
        "uh remind me to call James tomorrow at 10 about the uh Q3 budget numbers",
        12,
        title="Call James about the Q3 budget",
        text="Remind me to call James tomorrow at 10 about the Q3 budget numbers.",
        kind="task",
        tags=["work", "budget"],
        category_id=work,
        ping="explicit",
        ping_reason="You asked for tomorrow at 10.",
        reply="Saved. I'll remind you tomorrow at 10:00 AM to call James about the Q3 budget.",
    )
    store.add_reminder(budget, at(1, 10), "Call James about the Q3 budget", origin="explicit")

    cleaning = note(
        "pick up the dry cleaning later",
        25,
        title="Pick up the dry cleaning",
        text="Pick up the dry cleaning later.",
        kind="task",
        tags=["errands"],
        category_id=home,
        ping="suggested",
        ping_reason="You said later, so I'll remind you this afternoon.",
        reply="Saved. I'll remind you later today.",
    )
    # Due today, in a couple of hours (on the half hour), for the Inbox's "Due today".
    soon = local_now(tz).replace(second=0, microsecond=0) + timedelta(hours=2)
    soon += timedelta(minutes=-soon.minute % 30)
    if soon.date() != today:
        soon = soon.replace(year=today.year, month=today.month, day=today.day, hour=23, minute=30)
    store.add_reminder(
        cleaning, at(0, soon.hour, soon.minute), "Pick up the dry cleaning", origin="suggested"
    )

    store.add_digest(
        "daily",
        "Good morning! Here's your day.\n\n"
        "• Tomorrow 10:00 AM  Call James about the Q3 budget\n"
        "• Tomorrow 6:00 PM  Pay the electricity bill (due on the 10th)\n\n"
        "To revisit\n"
        "• Voice replies for reminders (#5)\n\n"
        "New reviews\n"
        "• Do Things That Don't Scale: must read, 5/5. Recruit users by hand.\n\n"
        "Get the Q3 numbers ready before tomorrow's call with James.",
        sent=True,
    )
    print(f"Seeded sample notes into {settings.db_path}")


if __name__ == "__main__":
    main()
