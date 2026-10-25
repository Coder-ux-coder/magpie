// The note dialog: everything about one note.

import { api } from "./api.js";
import { noteActions, reminderActions, watchNote } from "./actions.js";
import { KINDS, SOURCES, isWorking, kindBadge, noteTitle, reviewBlock, reviewSpeech } from "./cards.js";
import { speak, stopSpeaking } from "./speech.js";
import { aiName, categoryOf, emit, on, snoozeLabel, speechLang, state, tz } from "./state.js";
import { isChecklist, listItems, toggleItem } from "./text.js";
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
    const id = current?.id;
    current = null;
    onClose?.();
    // The card that opened the dialog may have been redrawn meanwhile: find it again.
    const target = returnFocus?.isConnected
      ? returnFocus
      : document.querySelector(`.note-card[data-id="${id}"] .note-title a`) || document.getElementById("main");
    target?.focus?.({ preventScroll: true });
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

function render(note, { keepScroll = false, force = false } = {}) {
  const el = dialog();
  const scroll = el.querySelector(".sheet-body")?.scrollTop || 0;
  // A box just ticked (or any control with an id) keeps the keyboard when it's redrawn.
  const focused = el.contains(document.activeElement) ? document.activeElement.id : "";
  const editing = el.querySelector("textarea.note-editor");
  if (!force && keepScroll && editing && current?.id === note.id) {
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
        titleBlock(note, title),
        iconButton("copy", "Copy the note", () => copyText(note.text || note.raw_text, "Note copied."), { title: "Copy the note" }),
        iconButton("speaker", "Read this note aloud", () => speak(`${title}. ${note.text || note.raw_text}`, { lang: speechLang() }), { title: "Read it aloud" }),
        iconButton("close", "Close", close),
      ),
      h("div", { class: "sheet-body" }, body(note)),
      h("div", { class: "sheet-foot" }, footer(note)),
    ),
  );
  if (keepScroll) {
    el.querySelector(".sheet-body").scrollTop = scroll;
    if (focused) el.querySelector(`#${CSS.escape(focused)}`)?.focus({ preventScroll: true });
  } else {
    el.querySelector(".sheet-head > .icon-btn:last-child").focus();
  }
}

function titleBlock(note, title) {
  const block = h("div", { class: "title-block" });
  const show = () => {
    clear(block).append(
      kindBadge(note.kind),
      h("h2", { id: "note-dialog-title", dir: "auto" }, title),
      iconButton("edit", "Change the title", edit, { class: "icon-btn title-edit" }),
    );
  };
  const edit = () => {
    const input = h("input", { type: "text", id: "note-title-input", value: noteTitle(note), maxlength: 200, dir: "auto", "aria-label": "Title" });
    let saving = false;
    const save = async () => {
      const value = input.value.trim();
      if (saving) return;
      if (!value || value === noteTitle(note)) return show();
      saving = true;
      try {
        const updated = await api(`/api/notes/${note.id}`, { method: "PATCH", body: { title: value } });
        emit("note-updated", updated);
        toast("Title saved.");
      } catch (error) {
        toast(error.message, { error: true });
        show();
      }
    };
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") { event.preventDefault(); save(); }
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); show(); block.querySelector(".title-edit")?.focus(); }
    });
    input.addEventListener("blur", save);
    clear(block).append(kindBadge(note.kind), h("h2", { id: "note-dialog-title", class: "sr-only" }, title), input);
    input.focus();
    input.select();
  };
  show();
  return block;
}

function body(note) {
  const parts = [];
  parts.push(
    h("p", { class: "meta-line" },
      h("span", {}, fullDate(note.created_at, tz())),
      h("span", {}, SOURCES[note.source] || note.source),
      note.meta?.captured_in ? h("span", {}, `Written in ${note.meta.captured_in}`) : null,
      categoryOf(note.category_id) ? h("span", { class: `chip category swatch-${categoryOf(note.category_id).color}` }, categoryOf(note.category_id).name) : null,
      note.status !== "open" ? h("span", {}, note.status === "done" ? "Done" : "Archived") : null,
    ),
  );

  if (note.state === "pending" || note.state === "processing") {
    parts.push(
      h("div", { class: "claude-box" },
        note.error ? icon("clock") : h("span", { class: "spinner", "aria-hidden": "true" }),
        h("p", {}, note.error || `${aiName()} is reading this note…`),
      ),
    );
  } else if (note.state === "failed") {
    parts.push(
      h("div", { class: "banner" },
        icon("alert"),
        h("p", {}, `${aiName()} couldn't process this note: ${note.error || "unknown error"}`),
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
        h("h3", {}, "Background"),
        h("p", { dir: "auto" }, note.briefing),
        h("p", { class: "muted small" }, `From ${aiName()}'s general knowledge, which may be out of date.`),
      ),
    );
  }
  parts.push(detailsSection(note));
  return parts;
}

function textSection(note) {
  const section = h("section", { class: "section", "aria-labelledby": "note-text-heading" });
  // The newest version of the note: it may have changed while the editor was open.
  const latest = () => (current?.id === note.id ? current : note);
  // Ticking items off saves each change in turn, starting from the newest text.
  let saving = Promise.resolve();
  const tick = (line, done) => {
    saving = saving.then(async () => {
      const now = latest().text || latest().raw_text;
      const text = toggleItem(now, line, done);
      if (text === now) return;
      try {
        const updated = await api(`/api/notes/${note.id}`, { method: "PATCH", body: { text } });
        emit("note-updated", updated);
      } catch (error) {
        toast(error.message, { error: true });
        show(); // what's really saved
      }
    });
  };
  const show = () => {
    clear(section);
    const text = latest().text || latest().raw_text;
    const list = isChecklist(text, latest().kind);
    const items = list ? listItems(text) : [];
    const done = items.filter((item) => item.done).length;
    section.append(
      h("div", { class: "row spread" },
        h("h3", { id: "note-text-heading" }, "Note", list ? h("span", { class: "list-count" }, ` · ${done} of ${items.length} done`) : null),
        h("button", { type: "button", class: "btn small", onclick: edit }, icon("edit"), "Edit"),
      ),
      list ? checklist(note.id, text, tick) : h("p", { class: "note-text", dir: "auto" }, text),
    );
  };
  const edit = () => {
    clear(section);
    const area = h("textarea", { class: "note-editor", rows: 6, dir: "auto", maxlength: 50000, "aria-labelledby": "note-text-heading" });
    area.value = latest().text || latest().raw_text;
    let saving = false;
    const save = async () => {
      const text = area.value.trim();
      if (!text || saving) return;
      saving = true;
      try {
        const updated = await api(`/api/notes/${note.id}`, { method: "PATCH", body: { text } });
        emit("note-updated", updated);
        render(updated, { keepScroll: true, force: true });
        toast(`Saved. Use “Ask ${aiName()} again” to re-file it with your changes.`);
      } catch (error) {
        toast(error.message, { error: true });
      } finally {
        saving = false;
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

// A list to tick off: its items as boxes, any other lines (a heading, a remark) as text.
function checklist(id, text, tick) {
  const items = new Map(listItems(text).map((item) => [item.line, item]));
  const box = h("div", { class: "checklist" });
  let list = null;
  text.split("\n").forEach((line, index) => {
    const item = items.get(index);
    if (!item) {
      list = null;
      if (line.trim()) box.append(h("p", { class: "note-text", dir: "auto" }, line));
      return;
    }
    if (!list) box.append((list = h("ul", { role: "list" })));
    const input = h("input", { type: "checkbox", id: `item-${id}-${index}`, checked: item.done });
    input.addEventListener("change", () => tick(index, input.checked));
    list.append(h("li", { class: item.done ? "done" : null }, input, h("label", { for: input.id, dir: "auto" }, item.text)));
  });
  return box;
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
              h("button", { type: "button", class: "btn small", onclick: () => reminderActions.snooze(reminder), "aria-label": `Snooze for ${snoozeLabel()}` }, `+${snoozeLabel()}`),
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
    const button = form.querySelector("button[type=submit]");
    if (button.disabled) return; // a double click must not add it twice
    button.disabled = true;
    try {
      const updated = await api(`/api/notes/${note.id}/reminders`, {
        method: "POST",
        body: { when: whenInput.value, message: message.value.trim(), repeat: repeat.value || null },
      });
      emit("note-updated", updated);
      render(updated, { keepScroll: true, force: true });
      toast("Reminder added.");
    } catch (error) {
      toast(error.message, { error: true });
      button.disabled = false;
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
    try {
      const updated = await api(`/api/notes/${note.id}`, { method: "PATCH", body: { kind: kind.value } });
      emit("note-updated", updated);
      toast("Kind updated.");
    } catch (error) {
      kind.value = note.kind; // show what is actually saved
      toast(error.message, { error: true });
    }
  });
  const tags = h("input", { type: "text", id: "note-tags", placeholder: "work, family", dir: "auto" });
  tags.value = (note.tags || []).join(", ");
  tags.addEventListener("change", async () => {
    const list = tags.value.split(",").map((t) => t.trim()).filter(Boolean);
    try {
      const updated = await api(`/api/notes/${note.id}`, { method: "PATCH", body: { tags: list } });
      emit("note-updated", updated);
      toast("Tags saved.");
    } catch (error) {
      tags.value = (note.tags || []).join(", ");
      toast(error.message, { error: true });
    }
  });
  const categories = state.categories || [];
  const category = h("select", { id: "note-category" },
    h("option", { value: "0", selected: !note.category_id }, "No category"),
    categories.map((c) => h("option", { value: String(c.id), selected: c.id === note.category_id }, c.name)),
  );
  category.addEventListener("change", async () => {
    const id = Number(category.value);
    try {
      const updated = await api(`/api/notes/${note.id}`, { method: "PATCH", body: { category_id: id } });
      emit("note-updated", updated);
      emit("categories-changed");
      toast(id ? `Moved to ${categoryOf(id)?.name || "the category"}.` : "Taken out of its category.");
    } catch (error) {
      category.value = String(note.category_id || 0);
      toast(error.message, { error: true });
    }
  });
  return h("section", { class: "section" },
    h("h3", {}, "Details"),
    h("div", { class: "field" }, h("label", { for: "note-category" }, "Category"), category,
      categories.length ? null : h("span", { class: "hint" }, "Make your own categories in Settings → Categories.")),
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
    h("button", { type: "button", class: "btn", onclick: () => noteActions.reprocess(note) }, icon("sparkles"), `Ask ${aiName()} again`),
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
