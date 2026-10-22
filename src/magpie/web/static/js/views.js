// The main views: note lists, the reminder timeline, digests and Ask.

import { api } from "./api.js";
import { noteActions, reminderActions } from "./actions.js";
import { KINDS, noteCard } from "./cards.js";
import { renderMarkdown } from "./markdown.js";
import { speak } from "./speech.js";
import { aiName, categoryOf, emit, local, on, snoozeLabel, speechLang, state, tz } from "./state.js";
import { clear, clock, dayLabel, h, icon, toast, when } from "./ui.js";

const PAGE = 40;
let list = { notes: [], hasMore: false, key: "", container: null };

// ------------------------------------------------------------------ lists

function belongs(note, route, q) {
  if (q) return true;
  if (route.kind && note.kind !== route.kind) return false;
  if (route.category && note.category_id !== route.category) return false;
  switch (route.view) {
    case "inbox":
    case "kind":
    case "category":
      return note.status === "open";
    case "done":
      return note.status === "done";
    case "archived":
      return note.status === "archived";
    case "scheduled":
      return (note.reminders || []).some((r) => r.status === "scheduled");
    case "reviews":
      return (note.links || []).length > 0 && note.status !== "archived";
    default:
      return note.status !== "archived";
  }
}

function emptyState(route, q) {
  if (q) return h("div", { class: "empty" }, h("h3", {}, "No matches"), h("p", {}, `No notes mention “${q}”.`));
  const wake = state.prefs?.wake_phrase || "notes on";
  if (route.view === "inbox") {
    return h("div", { class: "empty" },
      h("h3", {}, "Your inbox is clear"),
      h("p", {}, "Capture anything. Magpie files it and decides when to remind you."),
      h("ul", {},
        h("li", {}, `Tap the mic, or turn on hands-free and say “${wake}”.`),
        h("li", {}, "Type: “remind me to drink water in 10 minutes”."),
        h("li", {}, "Paste a YouTube or article link for a short review."),
        h("li", {}, "Connect Telegram in Settings to capture voice notes on the go."),
      ),
    );
  }
  const messages = {
    scheduled: "Nothing scheduled. Say when, like “remind me to call James at 5 pm”.",
    reviews: `Share a YouTube video or an article and ${aiName()} will review it for you.`,
    done: "Finished notes show up here.",
    archived: "Archived notes show up here.",
  };
  if (route.view === "category") {
    const name = categoryOf(route.category)?.name || "this category";
    return h("div", { class: "empty" },
      h("h3", {}, `Nothing in ${name} yet`),
      h("p", {}, `Notes you write while this tab is open go into ${name}. You can also drag a note onto its tab, or pick ${name} in a note's details.`));
  }
  const kind = route.kind ? KINDS[route.kind]?.label.toLowerCase() : null;
  return h("div", { class: "empty" }, h("p", {}, messages[route.view] || `No ${kind || ""} notes yet.`));
}

export async function renderNotes(container, route, q, isCurrent = () => true) {
  const key = JSON.stringify([route, q]);
  // A refresh of the same list keeps the pages already loaded.
  const loaded = key === list.key && list.container ? list.notes.length : 0;
  const params = new URLSearchParams({ limit: String(Math.min(200, Math.max(PAGE, loaded))) });
  if (q) {
    params.set("q", q);
    params.set("view", "all");
  } else {
    params.set("view", route.view === "kind" || route.view === "category" ? "inbox" : route.view);
    if (route.kind) params.set("kind", route.kind);
    if (route.category) params.set("category", String(route.category));
  }
  const data = await api(`/api/notes?${params}`);
  if (!isCurrent()) return;
  if (Array.isArray(data.categories)) {
    state.categories = data.categories;
    emit("categories", data.categories);
  }
  emit("counts", data.counts);
  params.set("limit", String(PAGE));
  list = { notes: data.notes, hasMore: data.notes.length >= PAGE, key, container, route, q, params };
  draw();
}

function draw() {
  const { container, notes, route, q } = list;
  if (!container) return;
  const focusedId = document.activeElement?.closest?.(".note-card")?.dataset.id;
  clear(container);
  if (!notes.length) {
    container.append(emptyState(route, q));
    return;
  }
  container.append(h("ul", { class: "list", role: "list" }, notes.map((note) => noteCard(note, noteActions))));
  if (list.hasMore) {
    container.append(h("button", { type: "button", class: "btn load-more", onclick: loadMore }, "Show more"));
  }
  if (focusedId) container.querySelector(`.note-card[data-id="${focusedId}"] .note-title a`)?.focus({ preventScroll: true });
}

let loadingMore = false;
async function loadMore() {
  if (loadingMore) return;
  loadingMore = true;
  const { key } = list;
  const params = new URLSearchParams(list.params);
  params.set("offset", String(list.notes.length));
  try {
    const data = await api(`/api/notes?${params}`);
    if (key !== list.key) return;
    list.notes.push(...data.notes);
    list.hasMore = data.notes.length === PAGE;
    draw();
  } catch (error) {
    toast(error.message, { error: true });
  } finally {
    loadingMore = false;
  }
}

function replaceCard(note) {
  if (!list.container) return;
  const index = list.notes.findIndex((n) => n.id === note.id);
  const keep = belongs(note, list.route, list.q);
  if (index === -1) {
    if (keep) {
      list.notes.unshift(note);
      draw();
    }
    return;
  }
  if (!keep) {
    const hadFocus = list.container.querySelector(`.note-card[data-id="${note.id}"]`)?.contains(document.activeElement);
    list.notes.splice(index, 1);
    draw();
    if (hadFocus) {
      // Keep the keyboard user's place: focus the card that moved up, or the last one.
      const next = list.notes[Math.min(index, list.notes.length - 1)];
      if (next) list.container.querySelector(`.note-card[data-id="${next.id}"] .note-title a`)?.focus();
    }
    return;
  }
  list.notes[index] = note;
  const existing = list.container.querySelector(`.note-card[data-id="${note.id}"]`)?.closest("li");
  const hadFocus = existing?.contains(document.activeElement);
  const fresh = noteCard(note, noteActions);
  if (existing) existing.replaceWith(fresh);
  else draw();
  if (hadFocus) fresh.querySelector(".note-title a")?.focus();
}

function removeCard(id) {
  const index = list.notes.findIndex((n) => n.id === id);
  if (index === -1) return;
  list.notes.splice(index, 1);
  draw();
}

on("note-updated", replaceCard);
on("note-created", replaceCard);
on("note-hidden", removeCard);
on("note-deleted", removeCard);

export function visibleNotes() {
  return list.notes;
}

export function detachList() {
  list.container = null;
}

// -------------------------------------------------------------- scheduled

function reminderCard(reminder) {
  const due = new Date(reminder.due_at);
  const sent = reminder.status === "sent";
  const sameTitle = Boolean(reminder.note_id) && reminder.note_title === reminder.message;
  return h("li", {},
    h("div", { class: "card reminder-row" },
      h("span", { class: "time" }, sent ? when(due, tz()) : clock(due, tz())),
      h("div", { class: "what" },
        h("p", { dir: "auto" },
          h("strong", {}, sameTitle ? h("a", { href: `#/note/${reminder.note_id}` }, reminder.message) : reminder.message),
          reminder.rrule ? h("span", { class: "muted small" }, " · repeats") : null,
        ),
        reminder.note_id && !sameTitle
          ? h("p", { class: "small" }, h("a", { href: `#/note/${reminder.note_id}` }, reminder.note_title || `Note #${reminder.note_id}`))
          : null,
        reminder.reason ? h("p", { class: "muted small", dir: "auto" }, reminder.reason) : null,
        reminder.last_error ? h("p", { class: "small error" }, reminder.last_error) : null,
      ),
      h("div", { class: "actions" },
        h("button", { type: "button", class: "btn small", onclick: () => reminderActions.done(reminder) }, icon("check"), "Done"),
        h("button", { type: "button", class: "btn small", onclick: () => reminderActions.snooze(reminder), "aria-label": `Snooze for ${snoozeLabel()}` }, `+${snoozeLabel()}`),
        h("button", { type: "button", class: "btn small", onclick: () => reminderActions.tomorrow(reminder) }, "Tomorrow"),
        sent ? null : h("button", { type: "button", class: "btn small danger", onclick: () => reminderActions.cancel(reminder), "aria-label": `Cancel reminder: ${reminder.message}` }, "Cancel"),
      ),
    ),
  );
}

export async function renderScheduled(container, isCurrent = () => true) {
  const [upcoming, sent] = await Promise.all([
    api("/api/reminders?status=scheduled&limit=300"),
    api("/api/reminders?status=sent&limit=30"),
  ]);
  if (!isCurrent()) return;
  clear(container);
  const recent = sent.reminders.filter((r) => Date.now() - new Date(r.sent_at || r.due_at) < 7 * 86400000);
  if (recent.length) {
    container.append(
      h("section", { class: "day-group", "aria-labelledby": "needs-response" },
        h("h3", { id: "needs-response" }, "Sent, waiting for you"),
        h("ul", { class: "list", role: "list" }, recent.map(reminderCard)),
      ),
    );
  }
  if (!upcoming.reminders.length) {
    if (!recent.length) container.append(emptyState({ view: "scheduled" }, ""));
    return;
  }
  const groups = new Map();
  for (const reminder of upcoming.reminders) {
    const label = dayLabel(new Date(reminder.due_at), tz());
    if (!groups.has(label)) groups.set(label, []);
    groups.get(label).push(reminder);
  }
  let index = 0;
  for (const [label, reminders] of groups) {
    const id = `day-${index++}`;
    container.append(
      h("section", { class: "day-group", "aria-labelledby": id },
        h("h3", { id }, label),
        h("ul", { class: "list", role: "list" }, reminders.map(reminderCard)),
      ),
    );
  }
}

// ---------------------------------------------------------------- digests

export async function renderDigests(container, isCurrent = () => true) {
  const data = await api("/api/digests");
  if (!isCurrent()) return;
  clear(container);
  const send = h("input", { type: "checkbox", id: "digest-send" });
  const busy = (button, label) => {
    button.disabled = true;
    button.textContent = label;
  };
  const run = async (kind, button) => {
    busy(button, kind === "weekly" ? "Writing your review…" : "Writing your digest…");
    try {
      // The AI writes it on the spot, which can take a minute or two.
      await api("/api/digests", { method: "POST", body: { kind, send: send.checked }, timeout: 240000 });
      toast(send.checked ? "Done, and sent to your channels." : "Done.");
    } catch (error) {
      toast(error.message, { error: true });
    }
    // Only redraw if the digests view is still on screen.
    if (button.isConnected) await renderDigests(container).catch(() => {});
  };
  const daily = h("button", { type: "button", class: "btn primary", onclick: () => run("daily", daily) }, icon("sun"), "Daily digest now");
  const weekly = h("button", { type: "button", class: "btn", onclick: () => run("weekly", weekly) }, icon("sparkles"), "Weekly review now");
  container.append(
    h("div", { class: "card" },
      h("p", { class: "muted" }, "Magpie writes a daily digest (scheduled reminders, notes to revisit, new reviews) and a weekly review of themes and open loops. Set their times in Settings → Reminders."),
      h("div", { class: "row" }, daily, weekly),
      h("label", { class: "switch" }, send, h("span", { class: "switch-track", "aria-hidden": "true" }), "Also send to my notification channels"),
    ),
  );
  if (!data.digests.length) {
    container.append(h("div", { class: "empty" }, h("p", {}, "No digests yet.")));
    return;
  }
  const items = data.digests.map((digest) => {
    const date = new Date(digest.created_at);
    const title = `${digest.kind === "weekly" ? "Weekly review" : "Daily digest"} · ${dayLabel(date, tz())}, ${clock(date, tz())}`;
    return h("li", {},
      h("article", { class: "card" },
        h("div", { class: "row spread" },
          h("h3", {}, title),
          h("button", { type: "button", class: "btn small", onclick: () => speak(digest.content, { lang: speechLang() }) }, icon("speaker"), "Read aloud"),
        ),
        digest.kind === "weekly" ? renderMarkdown(digest.content) : h("p", { class: "digest-content", dir: "auto" }, digest.content),
      ),
    );
  });
  container.append(h("ul", { class: "list", role: "list" }, items));
}

// -------------------------------------------------------------------- ask

const conversation = [];
let redrawAnswers = () => {};
const SUGGESTIONS = [
  "What do I need to do this week?",
  "Which saved videos are worth watching?",
  "Summarise my ideas from this month.",
  "What did I decide about money recently?",
];

export function renderAsk(container) {
  clear(container);
  const input = h("input", { type: "text", id: "ask-input", placeholder: "Ask anything about your notes…", autocomplete: "off", dir: "auto" });
  const form = h("form", { class: "ask-form" },
    h("label", { for: "ask-input", class: "sr-only" }, "Question"),
    input,
    h("button", { type: "submit", class: "btn primary" }, icon("send"), "Ask"),
  );
  const answers = h("div", { class: "list", "aria-live": "polite" });
  const drawAnswers = () => {
    clear(answers);
    for (const item of [...conversation].reverse()) {
      const body = item.pending
        ? h("p", { class: "muted" }, h("span", { class: "spinner", "aria-hidden": "true" }), ` ${aiName()} is reading your notes…`)
        : item.error
          ? h("p", { class: "error" }, item.error)
          : renderMarkdown(item.answer);
      answers.append(
        h("article", { class: "card answer" },
          h("p", { class: "question", dir: "auto" }, item.question),
          body,
          item.notes?.length
            ? h("div", { class: "chips" }, item.notes.map((n) => h("a", { class: "chip", href: `#/note/${n.id}` }, `#${n.id} ${n.title}`)))
            : null,
        ),
      );
    }
  };
  const ask = async (question) => {
    const item = { question, pending: true };
    conversation.push(item);
    drawAnswers();
    try {
      const result = await api("/api/ask", { method: "POST", body: { question }, timeout: 240000 });
      Object.assign(item, { pending: false, answer: result.answer, notes: result.notes });
      if (local.readAloud) speak(result.answer, { lang: speechLang() });
    } catch (error) {
      Object.assign(item, { pending: false, error: error.message });
    }
    redrawAnswers();
  };
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const question = input.value.trim();
    if (!question) return;
    input.value = "";
    ask(question);
  });
  container.append(
    h("div", { class: "card" },
      h("p", { class: "muted" }, `${aiName()} answers from your notes and links to the ones it used.`),
      form,
      h("div", { class: "chips", style: "margin-top: .75rem" },
        SUGGESTIONS.map((s) => h("button", { type: "button", class: "chip", onclick: () => ask(s) }, s)),
      ),
    ),
    answers,
  );
  redrawAnswers = drawAnswers;
  drawAnswers();
  input.focus();
}
