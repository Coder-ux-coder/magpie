// Things you can do to a note or reminder, shared by the lists, the note
// dialog and keyboard shortcuts.

import { api } from "./api.js";
import { isWorking } from "./cards.js";
import { aiName, emit, tz } from "./state.js";
import { toast, when } from "./ui.js";

async function guarded(work) {
  try {
    return await work();
  } catch (error) {
    toast(error.message || "Something went wrong.", { error: true });
    return null;
  }
}

// Notes with a change on its way: a second click (on stale data) waits for the first.
const busy = new Set();

export function patchNote(note, changes, message, undoChanges) {
  if (busy.has(note.id)) return Promise.resolve(null);
  busy.add(note.id);
  return guarded(async () => {
    const updated = await api(`/api/notes/${note.id}`, { method: "PATCH", body: changes });
    emit("note-updated", updated);
    if ("category_id" in changes) emit("categories-changed"); // the sidebar's counts
    if (message) {
      toast(message, undoChanges ? { action: "Undo", onAction: () => patchNote(updated, undoChanges) } : {});
    }
    return updated;
  }).finally(() => busy.delete(note.id));
}

export const noteActions = {
  toggleDone(note) {
    const done = note.status === "done";
    return patchNote(
      note,
      { status: done ? "open" : "done" },
      done ? "Moved back to your inbox." : "Marked as done.",
      { status: note.status },
    );
  },
  togglePin(note) {
    return patchNote(note, { pinned: !note.pinned }, note.pinned ? "Unpinned." : "Pinned to the top.");
  },
  toggleArchive(note) {
    const archived = note.status === "archived";
    return patchNote(
      note,
      { status: archived ? "open" : "archived" },
      archived ? "Moved back to your inbox." : "Archived.",
      { status: note.status },
    );
  },
  // Dropped on a category in the sidebar (or chosen in the note window); Undo puts it back.
  moveTo(note, category) {
    const id = category?.id || 0;
    if ((note.category_id || 0) === id) {
      toast(category ? `It's already in ${category.name}.` : "It isn't in a category.");
      return Promise.resolve(null);
    }
    return patchNote(
      note,
      { category_id: id },
      category ? `Moved to ${category.name}.` : "Taken out of its category.",
      { category_id: note.category_id || 0 },
    );
  },
  remove(note) {
    emit("note-hidden", note.id);
    let undone = false;
    toast("Note deleted.", {
      action: "Undo",
      timeout: 6000,
      onAction: () => {
        undone = true;
        emit("note-updated", note);
      },
    });
    setTimeout(() => {
      if (undone) return;
      guarded(async () => {
        await api(`/api/notes/${note.id}`, { method: "DELETE" });
        emit("note-deleted", note.id);
      });
    }, 6300);
  },
  reprocess(note) {
    return guarded(async () => {
      const updated = await api(`/api/notes/${note.id}/reprocess`, { method: "POST" });
      emit("note-updated", updated);
      watchNote(note.id);
      toast(`${aiName()} is taking another look.`);
      return updated;
    });
  },
};

const busyReminders = new Set();

export const reminderActions = {
  async run(reminder, action, body) {
    if (busyReminders.has(reminder.id)) return null;
    busyReminders.add(reminder.id);
    return guarded(async () => {
      const result = await api(`/api/reminders/${reminder.id}/${action}`, { method: "POST", body });
      // In this device's own way of writing times, like the list beside it.
      const due = result.due_at ? when(result.due_at, tz()) : result.when;
      const messages = {
        done: result.rrule && result.status === "scheduled" ? `Done. The next one is ${due}.` : "Done.",
        snooze: `Snoozed until ${due}.`,
        tomorrow: `Moved to ${due}.`,
        cancel: "Reminder cancelled.",
      };
      toast(messages[action] || "Updated.");
      emit("reminders-changed", reminder.note_id);
      return result;
    }).finally(() => busyReminders.delete(reminder.id));
  },
  // The due time shown says which occurrence of a repeating reminder is meant: the one to
  // come (the server otherwise means the one that has just gone off).
  done: (reminder) => reminderActions.run(reminder, "done", { due: reminder.due_at }),
  // Without minutes the server uses the snooze length from Settings.
  snooze: (reminder, minutes) => reminderActions.run(reminder, "snooze", minutes ? { minutes, due: reminder.due_at } : { due: reminder.due_at }),
  tomorrow: (reminder) => reminderActions.run(reminder, "tomorrow", { due: reminder.due_at }),
  cancel: (reminder) => reminderActions.run(reminder, "cancel"),
};

// Poll a note until the AI has filed it and reviewed its links.
const watching = new Map();

export function watchNote(id, { onFiled } = {}) {
  if (watching.has(id)) return;
  let tries = 0;
  let filed = false;
  const tick = async () => {
    tries += 1;
    let delay = tries < 15 ? 1500 : 5000;
    try {
      const note = await api(`/api/notes/${id}`);
      emit("note-updated", note);
      if (!filed && note.state !== "pending" && note.state !== "processing") {
        filed = true;
        onFiled?.(note);
      }
      if (!isWorking(note) || tries > 120) {
        watching.delete(id);
        return;
      }
    } catch (error) {
      if (error.status === 404 || tries > 120) {
        watching.delete(id);
        return;
      }
      delay = 5000;
    }
    watching.set(id, setTimeout(tick, delay));
  };
  watching.set(id, setTimeout(tick, 1000));
}
