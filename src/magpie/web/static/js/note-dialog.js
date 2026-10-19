// The note dialog: everything about one note.

import { api } from "./api.js";
import { noteActions, reminderActions, watchNote } from "./actions.js";
import { KINDS, SOURCES, isWorking, kindBadge, noteTitle, reviewBlock, reviewSpeech } from "./cards.js";
import { speak, stopSpeaking } from "./speech.js";
import { emit, on, speechLang, tz } from "./state.js";
import { clear, copyText, fullDate, h, icon, iconButton, localInputValue, toast, when } from "./ui.js";

const dialog = () => document.getElementById("note-dialog");
let current = null;
let returnFocus = null;
let onClose = null;

export function openNote(id, { closed } = {}) {
  const el = dialog();
  onClose = closed;
  if (!el.open) {
    returnFocus = document.activeElement;
    clear(el);
    el.append(h("div", { class: "sheet-inner" }, h("div", { class: "sheet-body" }, h("p", { class: "muted" }, "Loading…"))));
    el.showModal();
  }
  load(id);
}

async function load(id) {
  try {
    const note = await api(`/api/notes/${id}`);
    render(note);
    if (isWorking(note)) watchNote(note.id);
  } catch (error) {
    clear(dialog());
    dialog().append(
      h("div", { class: "sheet-inner" },
        h("div", { class: "sheet-head" }, h("h2", { id: "note-dialog-title" }, "Note not found"), iconButton("close", "Close", close)),
        h("div", { class: "sheet-body" }, h("p", {}, error.message)),
      ),
    );
  }
}

export function closeNote() {
  close();
}

function close() {
  stopSpeaking();
  const el = dialog();
  if (el.open) el.close();
}

function setup() {
  const el = dialog();
  el.addEventListener("close", () => {
    current = null;
    onClose?.();
    returnFocus?.focus?.();
  });
  // Clicking the backdrop closes the dialog.
  el.addEventListener("click", (event) => {
    if (event.target === el) close();
  });
}

on("note-updated", (note) => {
  if (current && note.id === current.id && dialog().open) render(note, { keepScroll: true });
});
on("reminders-changed", (noteId) => {
  if (current && noteId === current.id) load(current.id);
});

function render(note, { keepScroll = false } = {}) {
  const el = dialog();
  const scroll = el.querySelector(".sheet-body")?.scrollTop || 0;
  const editing = el.querySelector("textarea.note-editor");
  if (keepScroll && editing && current?.id === note.id) {
    // Don't wipe out an edit in progress; just refresh the rest next time.
    current = note;
    return;
  }
  current = note;
  clear(el);
  const title = noteTitle(note);
  el.append(
    h("div", { class: "sheet-inner" },
      h("div", { class: "sheet-head" },
        h("div", { class: "title-block" },
          kindBadge(note.kind),
          h("h2", { id: "note-dialog-title", dir: "auto" }, title),
        ),
        iconButton("speaker", "Read this note aloud", () => speak(`${title}. ${note.text || note.raw_text}`, { lang: speechLang() })),
        iconButton("close", "Close", close),
      ),
      h("div", { class: "sheet-body" }, body(note)),
      h("div", { class: "sheet-foot" }, footer(note)),
    ),
  );
  if (keepScroll) el.querySelector(".sheet-body").scrollTop = scroll;
  else el.querySelector(".sheet-head .icon-btn:last-child").focus();
}

function body(note) {
  const parts = [];
  parts.push(
    h("p", { class: "meta-line" },
      h("span", {}, fullDate(note.created_at, tz())),
      h("span", {}, SOURCES[note.source] || note.source),
      note.status !== "open" ? h("span", {}, note.status === "done" ? "Done" : "Archived") : null,
    ),
  );

  if (note.state === "pending" || note.state === "processing") {
    parts.push(
      h("div", { class: "claude-box" },
        note.error ? icon("clock") : h("span", { class: "spinner", "aria-hidden": "true" }),
        h("p", {}, note.error || "Claude is reading this note…"),
      ),
    );
  } else if (note.state === "failed") {
    parts.push(
      h("div", { class: "banner" },
        icon("alert"),
        h("p", {}, `Claude couldn't process this note: ${note.error || "unknown error"}`),
        h("button", { type: "button", class: "btn small", onclick: () => noteActions.reprocess(note) }, icon("refresh"), "Try again"),
      ),
    );
  } else if (note.reply || note.ping_reason) {
    parts.push(
      h("div", { class: "claude-box" },
        icon("sparkles"),
        h("div", {},
          note.reply ? h("p", { dir: "auto" }, note.reply) : null,
          note.ping_reason && note.ping_reason !== note.reply ? h("p", { class: "muted small", dir: "auto" }, note.ping_reason) : null,
        ),
      ),
    );
  }

  parts.push(textSection(note));
  if (note.summary) {
    parts.push(h("section", { class: "section" }, h("h3", {}, "Summary"), h("p", { dir: "auto" }, note.summary)));
  }
  if (note.links?.length) {
    parts.push(
      h("section", { class: "section" },
        h("h3", {}, note.links.length > 1 ? "Links" : "Link"),
        note.links.map((link) =>
          reviewBlock(link, {
            onRetry: () => retryLink(link),
            onSpeak: (review) => speak(reviewSpeech(review), { lang: speechLang() }),
          }),
        ),
      ),
    );
  }
  const original = note.meta?.original_raw || note.raw_text;
  if (note.text && original && original.trim() !== note.text.trim()) {
    parts.push(
      h("details", {},
        h("summary", {}, "What you originally said"),
        h("p", { class: "note-text muted", dir: "auto" }, original),
      ),
    );
  }
  parts.push(remindersSection(note));
  if (note.briefing) {
    parts.push(
      h("section", { class: "section" },
        h("h3", {}, "Context from Claude"),
        h("p", { dir: "auto" }, note.briefing),
        h("p", { class: "muted small" }, "From Claude's general knowledge, which may be out of date."),
      ),
    );
  }
  parts.push(detailsSection(note));
  return parts;
}

function textSection(note) {
  const section = h("section", { class: "section", "aria-labelledby": "note-text-heading" });
  const show = () => {
    clear(section);
    section.append(
      h("div", { class: "row spread" },
        h("h3", { id: "note-text-heading" }, "Note"),
        h("button", { type: "button", class: "btn small", onclick: edit }, icon("edit"), "Edit"),
      ),
      h("p", { class: "note-text", dir: "auto" }, note.text || note.raw_text),
    );
  };
  const edit = () => {
    clear(section);
    const area = h("textarea", { class: "note-editor", rows: 6, dir: "auto", "aria-labelledby": "note-text-heading" });
    area.value = note.text || note.raw_text;
    const save = async () => {
      const text = area.value.trim();
      if (!text) return;
      try {
        const updated = await api(`/api/notes/${note.id}`, { method: "PATCH", body: { text } });
        emit("note-updated", updated);
        render(updated, { keepScroll: true });
        toast("Saved. Use “Ask Claude again” to re-file it with your changes.");
      } catch (error) {
        toast(error.message, { error: true });
      }
    };
    section.append(
      h("h3", { id: "note-text-heading" }, "Edit note"),
      area,
      h("div", { class: "row" },
        h("button", { type: "button", class: "btn primary small", onclick: save }, "Save"),
        h("button", { type: "button", class: "btn small", onclick: show }, "Cancel"),
      ),
    );
    area.focus();
  };
  show();
  return section;
}

const REPEATS = [
  ["", "Doesn't repeat"],
  ["FREQ=DAILY", "Every day"],
  ["FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR", "Every weekday"],
  ["FREQ=WEEKLY", "Every week"],
  ["FREQ=MONTHLY", "Every month"],
];

function remindersSection(note) {
  const reminders = (note.reminders || []).filter((r) => r.status !== "cancelled");
  const section = h("section", { class: "section", "aria-labelledby": "reminders-heading" }, h("h3", { id: "reminders-heading" }, "Reminders"));
  if (!reminders.length) {
    section.append(h("p", { class: "muted" }, note.ping === "digest" ? "No reminder. It will be in your next daily digest." : "No reminders."));
  }
  for (const reminder of reminders) {
    const live = reminder.status === "scheduled" || reminder.status === "sent";
    section.append(
      h("div", { class: "card reminder-row" },
        h("div", { class: "what" },
          h("p", {}, h("strong", {}, when(reminder.due_at, tz())), reminder.rrule ? " · repeats" : "", ` · ${reminder.status}`),
          h("p", { dir: "auto" }, reminder.message),
          reminder.reason ? h("p", { class: "muted small", dir: "auto" }, reminder.reason) : null,
          reminder.last_error ? h("p", { class: "small error" }, reminder.last_error) : null,
        ),
        live
          ? h("div", { class: "actions" },
              h("button", { type: "button", class: "btn small", onclick: () => reminderActions.done(reminder) }, icon("check"), "Done"),
              h("button", { type: "button", class: "btn small", onclick: () => reminderActions.snooze(reminder, 60) }, "+1 h"),
              h("button", { type: "button", class: "btn small", onclick: () => reminderActions.tomorrow(reminder) }, "Tomorrow"),
              reminder.status === "scheduled"
                ? h("button", { type: "button", class: "btn small danger", onclick: () => reminderActions.cancel(reminder) }, "Cancel")
                : null,
            )
          : null,
      ),
    );
  }
  // Add a reminder by hand.
  const soon = new Date(Date.now() + 60 * 60 * 1000);
  soon.setMinutes(0, 0, 0);
  const whenInput = h("input", { type: "datetime-local", id: "new-reminder-when", required: true });
  whenInput.value = localInputValue(soon, tz());
  const message = h("input", { type: "text", id: "new-reminder-message", placeholder: noteTitle(note), dir: "auto" });
  const repeat = h("select", { id: "new-reminder-repeat" }, REPEATS.map(([value, label]) => h("option", { value }, label)));
  const form = h("form", { class: "card" },
    h("div", { class: "field" }, h("label", { for: "new-reminder-when" }, "Remind me at"), whenInput, h("span", { class: "hint" }, `Time zone: ${tz()}`)),
    h("div", { class: "field" }, h("label", { for: "new-reminder-message" }, "Message (optional)"), message),
    h("div", { class: "field" }, h("label", { for: "new-reminder-repeat" }, "Repeat"), repeat),
    h("button", { type: "submit", class: "btn small primary" }, icon("plus"), "Add reminder"),
  );
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const updated = await api(`/api/notes/${note.id}/reminders`, {
        method: "POST",
        body: { when: whenInput.value, message: message.value.trim(), repeat: repeat.value || null },
      });
      emit("note-updated", updated);
      render(updated, { keepScroll: true });
      toast("Reminder added.");
    } catch (error) {
      toast(error.message, { error: true });
    }
  });
  section.append(h("details", {}, h("summary", {}, "Add a reminder"), form));
  return section;
}

function detailsSection(note) {
  const kind = h("select", { id: "note-kind" },
    Object.entries(KINDS).map(([value, info]) => h("option", { value, selected: value === note.kind }, info.label)),
  );
  kind.addEventListener("change", async () => {
    const updated = await api(`/api/notes/${note.id}`, { method: "PATCH", body: { kind: kind.value } });
    emit("note-updated", updated);
    toast("Kind updated.");
  });
  const tags = h("input", { type: "text", id: "note-tags", placeholder: "work, family", dir: "auto" });
  tags.value = (note.tags || []).join(", ");
  tags.addEventListener("change", async () => {
    const list = tags.value.split(",").map((t) => t.trim()).filter(Boolean);
    const updated = await api(`/api/notes/${note.id}`, { method: "PATCH", body: { tags: list } });
    emit("note-updated", updated);
    toast("Tags saved.");
  });
  return h("section", { class: "section" },
    h("h3", {}, "Details"),
    h("div", { class: "field" }, h("label", { for: "note-kind" }, "Kind"), kind),
    h("div", { class: "field" }, h("label", { for: "note-tags" }, "Tags"), tags, h("span", { class: "hint" }, "Separate tags with commas.")),
  );
}

async function retryLink(link) {
  try {
    const note = await api(`/api/links/${link.id}/retry`, { method: "POST" });
    emit("note-updated", note);
    watchNote(note.id);
  } catch (error) {
    toast(error.message, { error: true });
  }
}

function footer(note) {
  const done = note.status === "done";
  const archived = note.status === "archived";
  return [
    h("button", { type: "button", class: "btn primary", onclick: () => noteActions.toggleDone(note) }, icon(done ? "undo" : "check"), done ? "Not done" : "Done"),
    h("button", { type: "button", class: "btn", onclick: () => noteActions.togglePin(note), "aria-pressed": String(note.pinned) }, icon("pin"), note.pinned ? "Unpin" : "Pin"),
    h("button", { type: "button", class: "btn", onclick: () => noteActions.toggleArchive(note) }, icon("archive"), archived ? "Unarchive" : "Archive"),
    h("button", { type: "button", class: "btn", onclick: () => noteActions.reprocess(note) }, icon("sparkles"), "Ask Claude again"),
    h("button", { type: "button", class: "btn", onclick: () => copyText(note.text || note.raw_text, "Note copied.") }, icon("copy"), "Copy"),
    h("button", {
      type: "button",
      class: "btn danger",
      onclick: () => {
        close();
        noteActions.remove(note);
      },
    }, icon("trash"), "Delete"),
  ];
}

setup();
