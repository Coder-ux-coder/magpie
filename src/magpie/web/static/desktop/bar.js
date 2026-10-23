// The Magpie app's voice bar: shows your words as you speak, and saves the note.
//
// The app (Python) drives it with magpieBar.<state>(details); the buttons and keys reach
// the app through Magpie's own API on this computer (/api/desktop/ui).

const $ = (id) => document.getElementById(id);
const bar = $("bar");
const text = $("text");
let state = "idle";
let edited = false; // typed over what was dictated: send the new text to the app
let editTimer = 0;

function call(action, details = {}) {
  return fetch("/api/desktop/ui", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Magpie": "1" },
    body: JSON.stringify({ window: "bar", action, ...details }),
  }).then((response) => response.json()).catch(() => null);
}

function set(next, status, { hint = "", reply = "", keys = "" } = {}) {
  state = next;
  bar.dataset.state = next;
  bar.dataset.tone = "";
  $("status").textContent = "";
  $("status").append(status, hint ? Object.assign(document.createElement("span"), { className: "hint", textContent: ` · ${hint}` }) : "");
  $("reply").textContent = reply;
  $("reply").hidden = !reply;
  $("keys").textContent = keys;
  const recording = next === "recording";
  const typing = next === "typing";
  $("save").hidden = !(recording || typing);
  $("cancel").hidden = !(recording || typing);
  $("close").hidden = recording || typing || next === "saving";
  $("undo").hidden = next !== "discarded";
  $("open").hidden = next !== "saved";
  text.readOnly = next === "saving" || next === "saved";
  text.hidden = next === "saved" || next === "empty";
  $("partial").hidden = !recording || !$("partial").textContent;
}

function words(base, final) {
  const start = String(base || "").trimEnd();
  const add = String(final || "").trim();
  if (!add) return start;
  return start ? `${start} ${add}` : add;
}

window.magpieBar = {
  recording({ hint = "" } = {}) {
    edited = false;
    text.value = "";
    $("partial").textContent = "";
    set("recording", "Recording", { hint: hint || "say “notes off” to save, “cancel note” to discard", keys: "Enter saves · Esc cancels" });
  },
  words({ base = "", final = "", partial = "" } = {}) {
    if (state !== "recording") return;
    if (!edited || document.activeElement !== text) text.value = words(base, final);
    $("partial").textContent = partial ? `${partial}…` : "";
    $("partial").hidden = !partial;
  },
  saving({ text: note = "", status = "" } = {}) {
    text.value = note;
    $("partial").textContent = "";
    set("saving", status || "Saving…");
  },
  saved({ title = "", reply = "" } = {}) {
    set("saved", title ? `Saved: ${title}` : "Saved", { reply });
  },
  discarded({ text: note = "" } = {}) {
    text.value = note;
    set("discarded", "Note discarded", { hint: note ? "press Undo to get it back" : "" });
  },
  empty() {
    text.value = "";
    set("empty", "I didn't catch anything", { hint: "say “notes on” and try again" });
  },
  typing({ text: note = "", status = "" } = {}) {
    edited = false;
    text.value = note;
    set("typing", status || "Write a note", { keys: "Enter saves · Shift+Enter new line · Esc closes" });
    bar.dataset.tone = status ? "error" : "";
    text.focus();
    text.setSelectionRange(text.value.length, text.value.length);
  },
  message({ text: message = "", error = false } = {}) {
    set(error ? "error" : "info", message);
  },
};

function save() {
  if (state === "recording") call("save");
  else if (state === "typing") {
    const note = text.value.trim();
    if (note) call("save_text", { text: note });
    else call("close");
  }
}

function cancel() {
  if (state === "recording") call("cancel");
  else call("close");
}

$("save").addEventListener("click", save);
$("cancel").addEventListener("click", cancel);
$("close").addEventListener("click", () => call("close"));
$("undo").addEventListener("click", () => call("undo"));
$("open").addEventListener("click", () => call("open_notes"));

text.addEventListener("input", () => {
  if (state !== "recording") return;
  edited = true;
  clearTimeout(editTimer);
  editTimer = setTimeout(() => call("edited", { text: text.value }), 250);
});

// The bar appears without taking the keyboard from what you were doing; clicking it
// hands the keyboard over so you can type.
document.addEventListener("mousedown", () => call("focus"), true);

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    event.preventDefault();
    cancel();
  } else if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    save();
  }
});

call("ready"); // the app sends what's waiting for the bar
