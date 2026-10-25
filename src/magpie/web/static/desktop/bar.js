// The Magpie app's voice bar: shows your words as you speak, and saves the note.
//
// The app (Python) drives it with magpieBar.<state>(details); the buttons and keys reach
// the app through Magpie's own API on this computer (/api/desktop/ui).

import { CANCEL_PHRASES, DEFAULT_STOP, STOP_ALIASES, dictationEnd, phraseList, tidy } from "../js/phrases.js";

const $ = (id) => document.getElementById(id);
const bar = $("bar");
const text = $("text");
let state = "idle";
let edited = false; // typed over what was dictated: send the new text to the app
let editTimer = 0;
// While a dictation app of the user's own types the note in: {stops, seconds, timer}.
let dictation = null;
const SETTLE_MS = 1500; // the stop phrase must stay at the end this long: more may come
const NOTHING_MS = 60000; // nothing typed in a minute: the bar closes
let clock = 0; // the time shown while recording
let startedAt = 0;

function call(action, details = {}) {
  return fetch("/api/desktop/ui", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Magpie": "1" },
    body: JSON.stringify({ window: "bar", action, ...details }),
  }).then((response) => response.json()).catch(() => null);
}

function tick() {
  const seconds = Math.max(0, Math.floor((Date.now() - startedAt) / 1000));
  $("timer").textContent = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

function startClock() {
  startedAt = Date.now();
  tick();
  clearInterval(clock);
  clock = setInterval(tick, 500);
}

function set(next, status, { hint = "", reply = "", keys = "" } = {}) {
  if (next !== "dictate") stopDictation();
  if (next !== "recording") clearInterval(clock);
  bar.style.setProperty("--level", "0");
  state = next;
  bar.dataset.state = next;
  bar.dataset.tone = "";
  $("status").textContent = "";
  $("status").append(status, hint ? Object.assign(document.createElement("span"), { className: "hint", textContent: ` · ${hint}` }) : "");
  $("reply").textContent = reply;
  $("reply").hidden = !reply;
  $("keys").textContent = keys;
  const recording = next === "recording";
  const typing = next === "typing" || next === "dictate";
  $("save").hidden = !(recording || typing);
  $("cancel").hidden = !(recording || typing);
  $("close").hidden = recording || typing || next === "saving";
  $("undo").hidden = next !== "discarded";
  $("open").hidden = next !== "saved";
  text.readOnly = next === "saving" || next === "saved";
  text.hidden = next === "saved" || next === "empty";
  $("partial").hidden = !recording || !$("partial").textContent;
  fit();
}

// The text box is as tall as its words (the half-heard ones follow straight after), and
// while you speak the newest words stay in view.
function fit({ end = false } = {}) {
  text.style.height = "auto";
  text.style.height = `${text.scrollHeight}px`;
  if (end) text.scrollTop = text.scrollHeight;
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
    startClock();
  },
  // A new hint while recording, such as "let go of Ctrl+Alt+Space to save".
  hint({ hint = "" } = {}) {
    if (state !== "recording" || !hint) return;
    const span = $("status").querySelector(".hint");
    if (span) span.textContent = ` · ${hint}`;
  },
  // The keyboard shortcut, pressed while the bar has the keyboard: save what's in it, or,
  // with nothing typed yet, take a voice note instead.
  finish() {
    if (state === "dictate") save();
    else if (state === "typing") {
      if (text.value.trim()) save();
      else call("start");
    }
  },
  // How loud the microphone is (0 to 1), a few times a second while recording.
  level({ level = 0 } = {}) {
    if (state !== "recording") return;
    bar.style.setProperty("--level", String(Math.max(0, Math.min(1, Number(level) || 0))));
  },
  words({ base = "", final = "", partial = "" } = {}) {
    if (state !== "recording") return;
    if (!edited || document.activeElement !== text) text.value = words(base, final);
    $("partial").textContent = partial ? `${partial}…` : "";
    $("partial").hidden = !partial;
    fit({ end: !edited });
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
  // The user's own dictation app types the note in: it's saved when the words end with
  // the stop phrase, after a pause, or with Enter.
  dictate({ stop = DEFAULT_STOP, seconds = 8 } = {}) {
    edited = false;
    text.value = "";
    set("dictate", "Dictate your note", { hint: `with your dictation app, then say “${stop}”`, keys: "Enter saves · Esc cancels" });
    dictation = { stops: phraseList(stop, DEFAULT_STOP, STOP_ALIASES), seconds: Number(seconds) || 0, timer: 0 };
    dictation.timer = setTimeout(() => finishDictation(""), NOTHING_MS);
    text.focus();
  },
};

function stopDictation() {
  if (dictation) clearTimeout(dictation.timer);
  dictation = null;
}

function dictated() {
  clearTimeout(dictation.timer);
  const end = dictationEnd(text.value, dictation.stops, CANCEL_PHRASES);
  if (end.state === "cancel") dictation.timer = setTimeout(() => discardDictation(end.text), SETTLE_MS);
  else if (end.state === "stop") dictation.timer = setTimeout(() => finishDictation(end.text), SETTLE_MS);
  else if (end.text && dictation.seconds) dictation.timer = setTimeout(() => finishDictation(end.text), dictation.seconds * 1000);
  else if (!end.text) dictation.timer = setTimeout(() => finishDictation(""), NOTHING_MS);
}

function finishDictation(note) {
  if (state !== "dictate") return;
  stopDictation();
  if (note) call("save_dictated", { text: note });
  else call("close");
}

function discardDictation(note) {
  if (state !== "dictate") return;
  stopDictation();
  call("discard", { text: note });
}

function save() {
  if (state === "recording") call("save");
  else if (state === "dictate") {
    const end = dictationEnd(text.value, dictation.stops, CANCEL_PHRASES);
    if (end.state === "cancel") discardDictation(end.text);
    else finishDictation(end.text);
  } else if (state === "typing") {
    const note = text.value.trim();
    if (note) call("save_text", { text: note });
    else call("close");
  }
}

function cancel() {
  if (state === "recording") call("cancel");
  else if (state === "dictate") discardDictation(tidy(text.value));
  else call("close");
}

$("save").addEventListener("click", save);
$("cancel").addEventListener("click", cancel);
$("close").addEventListener("click", () => call("close"));
$("undo").addEventListener("click", () => call("undo"));
$("open").addEventListener("click", () => call("open_notes"));

text.addEventListener("input", () => {
  fit();
  if (state === "dictate") return dictated();
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
