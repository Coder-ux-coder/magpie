// Things you can do to a note or reminder, shared by the lists, the note
// dialog and keyboard shortcuts.

import { api } from "./api.js";
import { isWorking } from "./cards.js";
import { emit } from "./state.js";
import { toast } from "./ui.js";

async function guarded(work) {
  try {
    return await work();
  } catch (error) {
    toast(error.message || "Something went wrong.", { error: true });
    return null;
  }
}

export function patchNote(note, changes, message, undoChanges) {
  return guarded(async () => {
    const updated = await api(`/api/notes/${note.id}`, { method: "PATCH", body: changes });
    emit("note-updated", updated);
    if (message) {
      toast(message, undoChanges ? { action: "Undo", onAction: () => patchNote(updated, undoChanges) } : {});
    }
    return updated;
  });
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
      toast("Claude is taking another look.");
      return updated;
    });
  },
};

export const reminderActions = {
  async run(reminder, action, body) {
    return guarded(async () => {
      const result = await api(`/api/reminders/${reminder.id}/${action}`, { method: "POST", body });
      const messages = {
        done: "Done.",
        snooze: `Snoozed until ${result.when}.`,
        tomorrow: `Moved to ${result.when}.`,
        cancel: "Reminder cancelled.",
      };
      toast(messages[action] || "Updated.");
      emit("reminders-changed", reminder.note_id);
      return result;
    });
  },
  done: (reminder) => reminderActions.run(reminder, "done"),
  snooze: (reminder, minutes = 60) => reminderActions.run(reminder, "snooze", { minutes }),
  tomorrow: (reminder) => reminderActions.run(reminder, "tomorrow"),
  cancel: (reminder) => reminderActions.run(reminder, "cancel"),
};

// Poll a note until Claude has filed it and reviewed its links.
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
