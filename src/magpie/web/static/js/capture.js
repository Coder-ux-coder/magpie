// The voice bar, docked at the bottom of Magpie: say “notes on” (or press the mic,
// or type), watch the words appear, then save with “notes off”, Save or
// Ctrl+Enter. Cancel throws the note away, with Undo. The bar can pop out into a
// small always-on-top window (Document Picture-in-Picture in Chrome and Edge).
// With the Magpie app, the app does the listening and this bar drives it.

import { api, createNote } from "./api.js";
import { watchNote } from "./actions.js";
import { appendText, compact } from "./phrases.js";
import { openSettings } from "./settings.js";
import { speak, speaking, stopSpeaking } from "./speech.js";
import { desktopApp, emit, local, on, saveLocal, speechLang, state } from "./state.js";
import { h, toast } from "./ui.js";
import { Recorder, VoiceCapture, recordingSupported, speechSupported } from "./voice.js";

let el = null; // the bar's elements, found once: they keep working when the bar changes windows
let voice = null;
let recorder = null;
let transcribing = false;
let base = ""; // what was in the text box when recording started
let spoken = false; // the text in the box includes dictation
let notice = null; // a message shown instead of the usual status for a while: {text, until}
let noticeTimer = null;
let offerAllow = false; // ask for the microphone so "notes on" can work
let permission = "unknown";
let userStopped = false; // the user turned listening off: don't switch it back on by itself
let pip = null; // the pop-out window
let placeholder = null;
let undoText = "";
let undoTimer = null;
let updateHeight = () => {};
let noteContext = () => ({}); // the view a note is written in (set by app.js)
let startedIn = null; // that view, for a recording, when it started

const PLACEHOLDER = "Type a note, or press the mic…";
const SHORT_PLACEHOLDER = "Type a note…"; // a phone or the pop-out: the mic is right beside it
const phone = window.matchMedia?.("(max-width: 40rem)");
// The bar's own window decides (it may be the pop-out).
const narrowBar = () => Boolean(el.root.ownerDocument.defaultView?.matchMedia?.("(max-width: 40rem)").matches);
const wakePhrase = () => state.prefs?.wake_phrase || "notes on";
const stopPhrase = () => state.prefs?.stop_phrase || "notes off";
const pipSupported = "documentPictureInPicture" in window;

// ------------------------------------------------------------ the Magpie app

// The Magpie app (Windows) runs this server and listens for the command word itself,
// all the time, with its own bar over other windows. This page then leaves the
// microphone to it: the mic button and the listening switch drive the app instead.
let appTimer = null;

function app() {
  return state.status?.desktop || {};
}

function appStatus() {
  const { state: now, message } = app();
  const wake = app().wake || wakePhrase();
  const keys = app().shortcut?.label;
  if (now === "standby") return `The Magpie app is listening: say “${wake}” anywhere on this computer${keys ? `, press ${keys}` : ""}, press the mic, or type.`;
  if (now === "recording") return "Taking a note in the Magpie bar at the bottom of the screen…";
  if (now === "paused") return `The Magpie app isn't listening for the command word. Press the mic${keys ? ` or ${keys}` : ""}, or turn listening on in the voice options.`;
  if (now === "starting") return message || "The Magpie app is getting ready to listen…";
  return message || "The Magpie app can't hear a microphone. Press the mic to try again, or type.";
}

async function appCommand(action) {
  try {
    const { ok, ...now } = await api("/api/desktop/command", { method: "POST", body: { action } });
    if (state.status) state.status.desktop = now;
  } catch (error) {
    setNotice(error.message, 8000);
    return false;
  }
  render();
  followApp(600); // the app takes a moment to change state
  return true;
}

// Keep the status line in step with the app while this page is open.
function followApp(delay = 3000) {
  clearTimeout(appTimer);
  if (!desktopApp()) return;
  appTimer = setTimeout(async () => {
    if (!document.hidden) {
      try {
        state.status.desktop = await api("/api/desktop");
        emit("desktop", state.status.desktop);
        render();
      } catch {
        // restarting for an update, most likely: keep trying
      }
    }
    followApp();
  }, delay);
}

// ---------------------------------------------------------------- install

let installPrompt = null;
window.addEventListener("beforeinstallprompt", (event) => {
  event.preventDefault(); // offered from the bar instead of the browser's own bubble
  installPrompt = event;
  render();
});
window.addEventListener("appinstalled", () => {
  installPrompt = null;
  render();
  toast("Magpie is installed. Open it from your apps like any other program.");
});

function standalone() {
  return window.matchMedia?.("(display-mode: standalone)").matches || navigator.standalone === true;
}

async function install() {
  const prompt = installPrompt;
  if (!prompt) return;
  installPrompt = null;
  render();
  try {
    await prompt.prompt();
    const choice = await prompt.userChoice;
    if (choice?.outcome !== "accepted") setNotice("You can install Magpie any time from the browser's menu.", 6000);
  } catch {
    // The browser declined to show it.
  }
}

// ---------------------------------------------------- one window listens

// Two Magpie windows listening at once would both save every note. The window
// that started listening last wins; the others pause until they get focus again.
const windowId = Math.random().toString(36).slice(2);
const channel = "BroadcastChannel" in window ? new BroadcastChannel("magpie-voice") : null;
let pausedByOther = false;

channel?.addEventListener("message", ({ data }) => {
  if (data?.type !== "listening" || data.from === windowId || voice?.state !== "standby") return;
  voice.setHandsFree(false);
  pausedByOther = true;
  render();
});

function claimMic() {
  pausedByOther = false;
  channel?.postMessage({ type: "listening", from: windowId });
}

// ----------------------------------------------------------------- status

function useServer() {
  return (!speechSupported || local.serverStt) && recordingSupported && Boolean(state.status?.stt?.enabled);
}

function canCapture() {
  return desktopApp() || (speechSupported && !local.serverStt) || useServer();
}

function recording() {
  return voice?.state === "recording" || Boolean(recorder?.active);
}

function statusText() {
  if (transcribing) return "Transcribing…";
  if (recorder?.active) return "Recording… press Notes off to save, or Cancel.";
  if (voice?.state === "recording") return `Recording… say “${stopPhrase()}” or press Notes off to save.`;
  if (notice && notice.until > Date.now()) return notice.text;
  if (desktopApp()) return appStatus();
  if (!canCapture()) {
    return speechSupported || recordingSupported
      ? "Type a note. For voice, use Chrome, Edge or Safari, or set up server transcription."
      : "Type a note. This browser can't capture voice.";
  }
  if (pausedByOther) return "Listening in another Magpie window. Click here to listen in this one.";
  if (voice?.state === "standby") return `Say “${wakePhrase()}”, press the mic, or type.`;
  if (permission === "denied") return "The microphone is blocked for this site. Allow it in the browser's site settings to talk to Magpie.";
  if (offerAllow) return `Allow the microphone so Magpie can hear “${wakePhrase()}”.`;
  return "Press the mic or type a note.";
}

function setNotice(text, ms = 6000) {
  notice = { text, until: Date.now() + ms };
  clearTimeout(noticeTimer);
  noticeTimer = setTimeout(render, ms + 50);
  render();
}

function render() {
  if (!el) return;
  const inApp = desktopApp();
  const rec = recording();
  const appRec = inApp && app().state === "recording";
  const listening = inApp ? app().state === "standby" : voice?.state === "standby";
  const mode = transcribing ? "busy" : rec || appRec ? "recording" : listening ? "standby" : "idle";
  el.root.dataset.state = mode;
  const status = statusText();
  if (el.status.textContent !== status) el.status.textContent = status;
  el.allow.hidden = inApp || !offerAllow || rec || listening || permission === "denied";

  el.mic.dataset.state = mode;
  el.mic.disabled = !canCapture() || transcribing;
  el.mic.setAttribute("aria-pressed", String(rec || appRec));
  const micLabel = rec || appRec ? "Stop and save" : "Start recording";
  el.micLabel.textContent = micLabel;
  el.mic.title = `${micLabel} (Alt+N)`;

  // While recording, the words appear here: no placeholder in their way.
  el.area.placeholder = rec || transcribing ? "" : narrowBar() ? SHORT_PLACEHOLDER : PLACEHOLDER;
  const hasText = Boolean(el.area.value.trim());
  el.off.hidden = !rec;
  el.save.hidden = rec; // Notes off saves, as Save would
  el.cancel.hidden = !(rec || hasText); // always there while recording
  el.save.disabled = transcribing;

  // The app is installed and has its own floating bar: no install or pop-out here.
  el.install.hidden = inApp || !installPrompt || standalone() || Boolean(pip);
  el.popout.hidden = inApp || !pipSupported || Boolean(pip);
  el.back.hidden = !pip;
  el.options.hidden = Boolean(pip);
  el.undo.hidden = !(pip && undoText);
  el.handsfree.checked = inApp ? app().listening !== false : Boolean(voice?.handsFree);
  el.handsfree.disabled = !inApp && (!speechSupported || local.serverStt);
  el.autoListen.closest("label").hidden = inApp;
  el.review.closest("label").hidden = inApp;
}

// -------------------------------------------------------------- text box

function setArea(value) {
  if (el.area.value !== value) {
    el.area.value = value;
    autosize();
  }
}

function autosize() {
  const area = el.area;
  area.style.height = "auto";
  area.style.height = `${area.scrollHeight + 2}px`; // CSS caps it at about five lines
}

function showInterim(text) {
  el.interim.textContent = text ? `${text}…` : "";
  el.interim.hidden = !text;
}

// ----------------------------------------------------------------- saving

async function submit(text, source) {
  text = String(text || "").trim();
  if (!text) {
    el.area.focus();
    return false;
  }
  // Clear the box first, so a second press of Save or a late "notes off" can't save it twice.
  setArea("");
  showInterim("");
  spoken = false;
  // A voice note keeps the view it was started in; a typed one, the view it's saved from.
  const context = source === "web-voice" && startedIn ? startedIn : noteContext();
  startedIn = null;
  setNotice("Saving…", 30000);
  let result;
  try {
    result = await createNote(text, source, context);
  } catch (error) {
    // Not saved, and not kept on this device either: put the words back.
    const now = el.area.value;
    setArea(now.trim() ? `${text}\n${now}` : text);
    setNotice(`Not saved: ${error.message}`, 12000);
    toast(error.message, { error: true });
    return false;
  }
  if (result.queued) {
    setNotice(result.offline
      ? "Saved on this device. It syncs when you're back online."
      : "Saved on this device. It syncs as soon as Magpie can be reached.", 10000);
    return true;
  }
  setNotice("Saved ✓", 8000);
  emit("note-created", result.note);
  watchNote(result.note.id, { onFiled });
  return true;
}

function onFiled(note) {
  if (note.state === "ready") setNotice(`Saved ✓ ${note.reply || ""}`.trim(), 10000);
  else if (note.error) setNotice(`Saved. ${note.error}`, 12000);
  if (local.readAloud && note.reply) {
    voice?.mute(15000);
    speak(note.reply, { lang: speechLang(), onend: () => voice?.mute(600) });
  }
}

// Save, Notes off, Enter and Ctrl+Enter: save exactly what the box shows, including
// words the recogniser is still settling.
function saveNow() {
  if (transcribing) return;
  if (recorder?.active) {
    finishServerRecording({ save: true });
    return;
  }
  let text = el.area.value;
  let source = spoken ? "web-voice" : "web-text";
  if (voice?.state === "recording") {
    text = appendText(base, voice.take());
    source = "web-voice";
    base = "";
  }
  submit(text, source);
}

// Cancel and Esc: throw the note away, recording or not, with Undo.
function discard() {
  let text = el.area.value;
  if (recorder?.active) {
    recorder.abort();
    recorder = null;
  } else if (voice?.state === "recording") {
    text = appendText(base, voice.cancelNote({ announce: false }));
  }
  base = "";
  discardText(text);
}

function discardText(text) {
  startedIn = null;
  setArea("");
  showInterim("");
  spoken = false;
  if (!text.trim()) {
    setNotice("Stopped. Nothing was saved.", 4000);
    return;
  }
  undoText = text;
  clearTimeout(undoTimer);
  undoTimer = setTimeout(() => {
    undoText = "";
    render();
  }, 6000);
  setNotice("Note discarded.", 6000);
  toast("Note discarded.", { action: "Undo", timeout: 6000, onAction: undo });
}

function undo() {
  const text = undoText;
  if (!text) return;
  undoText = "";
  clearTimeout(undoTimer);
  const now = el.area.value;
  setArea(now.trim() ? `${text}\n${now}` : text);
  if (voice?.state === "recording") {
    base = el.area.value;
    voice.rebase();
  }
  spoken = true;
  setNotice("Note restored.", 4000);
  el.area.focus();
}

// ------------------------------------------------------------------ voice

function setupVoice() {
  if (!speechSupported) return;
  voice = new VoiceCapture();
  voice.addEventListener("state", ({ detail }) => {
    if (detail.state === "recording") {
      stopSpeaking();
      base = el.area.value;
      startedIn = noteContext();
    }
    if (detail.state !== "off") claimMic();
    if (detail.state !== "recording") showInterim("");
    render();
  });
  voice.addEventListener("transcript", ({ detail }) => {
    setArea(appendText(base, detail.final));
    if (detail.final) spoken = true;
    showInterim(detail.interim);
    render();
  });
  voice.addEventListener("note", ({ detail }) => {
    const text = appendText(base, detail.text);
    base = "";
    showInterim("");
    if (local.reviewVoice) {
      setArea(text);
      spoken = true;
      setNotice("Check the text, then press Save.", 15000);
      el.area.focus();
      return;
    }
    submit(text, "web-voice");
  });
  voice.addEventListener("empty", () => {
    startedIn = null;
    setArea(base);
    base = "";
    showInterim("");
    setNotice("I didn't catch anything.", 4000);
  });
  // "cancel note", said out loud.
  voice.addEventListener("cancelled", ({ detail }) => {
    const text = appendText(base, detail.text);
    base = "";
    discardText(text);
  });
  voice.addEventListener("error", ({ detail }) => {
    if (detail.text) setArea(appendText(base, detail.text)); // keep what was said
    if (!detail.transient) {
      base = "";
      if (!detail.text) startedIn = null;
    }
    showInterim("");
    if (detail.error === "not-allowed") permission = "denied";
    setNotice(detail.message, detail.transient ? 6000 : 15000);
    if (!detail.transient) toast(detail.message, { error: true, timeout: 10000 });
  });
}

export function toggleMic() {
  if (!el) return;
  if (desktopApp()) {
    appCommand(app().state === "recording" ? "finish" : "start");
    return;
  }
  if (useServer()) {
    toggleServerRecording();
    return;
  }
  if (!voice) {
    setNotice("Voice capture isn't available in this browser. Type your note instead.", 6000);
    return;
  }
  if (speaking()) stopSpeaking();
  if (voice.state === "recording") voice.finish();
  else voice.start();
}

export function toggleHandsFree(force) {
  if (desktopApp()) {
    const on = typeof force === "boolean" ? force : app().listening === false;
    appCommand(on ? "listen" : "pause").then((done) => done && setNotice(on
      ? `The Magpie app is listening for “${wakePhrase()}”.`
      : "The Magpie app stopped listening for the command word.", 5000));
    return;
  }
  if (!voice || local.serverStt) return;
  const on = typeof force === "boolean" ? force : !voice.handsFree;
  offerAllow = false;
  pausedByOther = false;
  userStopped = !on;
  voice.setHandsFree(on);
  saveLocal({ handsFree: on });
  setNotice(on ? `Listening for “${wakePhrase()}”.` : "Stopped listening for the command word.", 4000);
}

function startListening() {
  offerAllow = false;
  pausedByOther = false;
  if (voice && !voice.handsFree) voice.setHandsFree(true);
  render();
}

// Esc anywhere in Magpie (outside the bar): close the options, or drop a recording.
export function stopVoice() {
  if (!el) return;
  if (!el.popover.hidden) closePopover(true);
  else if (recording()) discard();
}

export function startVoiceFromShortcut() {
  if (canCapture()) toggleMic();
}

/** Tell the bar where notes are being written: () => ({category_id, captured_in}). */
export function setNoteContext(fn) {
  noteContext = () => {
    try {
      return fn() || {};
    } catch {
      return {};
    }
  };
}

/** The bar's text box, wherever the bar is (here or in the pop-out window). */
export function composeArea() {
  return el?.area || null;
}

/** Is someone in the middle of a note? Then Magpie mustn't reload by itself. */
export function captureBusy() {
  return Boolean(el && (recording() || transcribing || pip || el.area.value.trim()));
}

export function focusCompose() {
  if (!el) return;
  if (pip) pip.focus?.();
  el.area.focus();
}

export function syncVoiceSettings() {
  const prefs = state.prefs || {};
  voice?.configure({
    lang: prefs.speech_lang || "",
    wake: prefs.wake_phrase,
    stop: prefs.stop_phrase,
    silenceSeconds: prefs.silence_seconds ?? 8,
  });
  if ((local.serverStt || desktopApp()) && voice && voice.state !== "off") voice.shutdown();
  if (el) {
    el.area.lang = speechLang();
    // Don't overwrite a word being typed in the options when fresh settings arrive.
    if (!el.popover.hidden && !el.popover.contains(el.root.ownerDocument.activeElement)) fillPopover();
  }
  render();
}

// Is the microphone already allowed? Then "notes on" can work as soon as Magpie opens.
async function micPermission() {
  try {
    const status = await navigator.permissions.query({ name: "microphone" });
    status.addEventListener("change", () => {
      permission = status.state;
      if (permission === "granted" && offerAllow) startListening();
      render();
    });
    return status.state;
  } catch {
    return "unknown"; // no Permissions API for the microphone here
  }
}

async function autoStart() {
  if (!voice || local.serverStt || desktopApp()) return;
  permission = await micPermission();
  if (local.autoListen === false || userStopped) return render();
  if (permission === "granted") startListening();
  else if (permission !== "denied") offerAllow = true;
  render();
}

// ------------------------------------------- browsers without speech recognition

async function toggleServerRecording() {
  if (transcribing) return;
  if (recorder?.active) {
    finishServerRecording();
    return;
  }
  try {
    recorder = new Recorder();
    await recorder.start();
    base = el.area.value;
    startedIn = noteContext();
  } catch (error) {
    recorder = null;
    setNotice(error.name === "NotAllowedError"
      ? "The microphone is blocked for this site. Allow it in the browser's site settings."
      : `Couldn't start recording: ${error.message}`, 12000);
  }
  render();
}

async function finishServerRecording({ save = !local.reviewVoice } = {}) {
  const active = recorder;
  recorder = null;
  if (!active) return;
  const blob = await active.stop();
  await transcribe(blob, save);
}

async function transcribe(blob, save) {
  transcribing = true;
  render();
  const form = new FormData();
  form.append("file", blob, "note.webm");
  try {
    const result = await api("/api/transcribe", { method: "POST", form });
    const text = appendText(base, result.text);
    base = "";
    transcribing = false;
    if (save) {
      await submit(text, "web-voice");
    } else {
      setArea(text);
      spoken = true;
      setNotice("Check the text, then press Save.", 15000);
      el.area.focus();
    }
  } catch (error) {
    // Keep the recording, so a failed upload loses nothing.
    setNotice(`Couldn't transcribe the recording: ${error.message}`, 15000);
    toast(error.message, { error: true, timeout: 30000, action: "Try again", onAction: () => transcribe(blob, save) });
  } finally {
    transcribing = false;
    render();
  }
}

// ---------------------------------------------------------------- options

function fillPopover() {
  const prefs = state.prefs || {};
  el.wake.value = prefs.wake_phrase || "notes on";
  el.stopWord.value = prefs.stop_phrase || "notes off";
  el.silence.value = String(prefs.silence_seconds ?? 8);
  el.autoListen.checked = local.autoListen !== false;
  el.review.checked = Boolean(local.reviewVoice);
  el.handsfree.checked = desktopApp() ? app().listening !== false : Boolean(voice?.handsFree);
}

function openPopover() {
  fillPopover();
  el.popover.hidden = false;
  el.options.setAttribute("aria-expanded", "true");
  (el.handsfree.disabled ? el.wake : el.handsfree).focus();
  el.root.ownerDocument.addEventListener("pointerdown", outside, true);
}

function closePopover(returnFocus = false) {
  if (el.popover.hidden) return;
  el.popover.hidden = true;
  el.options.setAttribute("aria-expanded", "false");
  el.root.ownerDocument.removeEventListener("pointerdown", outside, true);
  if (returnFocus) el.options.focus();
}

function outside(event) {
  if (!el.popover.contains(event.target) && !el.options.contains(event.target)) closePopover();
}

async function savePrefs(changes, message) {
  try {
    state.prefs = await api("/api/prefs", { method: "PUT", body: changes });
    emit("prefs", state.prefs);
    setNotice(message, 5000);
  } catch (error) {
    toast(error.message, { error: true });
    fillPopover();
  }
}

function savePhrase(field, input, name) {
  const value = input.value.trim().replace(/\s+/g, " ");
  const other = field === "wake_phrase" ? stopPhrase() : wakePhrase();
  let problem = "";
  if (!value) problem = `The ${name} can't be empty.`;
  else if (compact(value) === compact(other)) problem = "The command word and the stop word must be different.";
  if (problem) {
    setNotice(problem, 6000);
    fillPopover();
    return;
  }
  if (value === (state.prefs?.[field] || "")) return;
  savePrefs({ [field]: value }, `The ${name} is now “${value}”.`);
}

function wirePopover() {
  el.options.addEventListener("click", () => (el.popover.hidden ? openPopover() : closePopover(true)));
  el.handsfree.addEventListener("change", () => toggleHandsFree(el.handsfree.checked));
  el.wake.addEventListener("change", () => savePhrase("wake_phrase", el.wake, "command word"));
  el.stopWord.addEventListener("change", () => savePhrase("stop_phrase", el.stopWord, "stop word"));
  el.silence.addEventListener("change", () => {
    const seconds = Math.max(0, Math.min(120, Math.round(Number(el.silence.value) || 0)));
    el.silence.value = String(seconds);
    if (seconds === (state.prefs?.silence_seconds ?? 8)) return;
    savePrefs({ silence_seconds: seconds }, seconds
      ? `Notes save after ${seconds} seconds of silence.`
      : "Notes save only when you say the stop word or press Save.");
  });
  el.autoListen.addEventListener("change", () => {
    saveLocal({ autoListen: el.autoListen.checked });
    setNotice(el.autoListen.checked ? "Magpie starts listening when it opens." : "Magpie waits for the mic button when it opens.", 4000);
  });
  el.review.addEventListener("change", () => saveLocal({ reviewVoice: el.review.checked }));
  el.more.addEventListener("click", () => {
    closePopover();
    openSettings("voice");
  });
  el.done.addEventListener("click", () => closePopover(true));
}

// ---------------------------------------------------------------- pop-out

async function popOut() {
  if (pip || !pipSupported) return;
  closePopover();
  let win;
  try {
    win = await window.documentPictureInPicture.requestWindow({ width: 560, height: 150 });
  } catch (error) {
    setNotice(`Couldn't open the floating bar: ${error.message}`, 8000);
    return;
  }
  pip = win;
  const doc = win.document;
  doc.title = "Magpie";
  for (const link of document.querySelectorAll('link[rel="stylesheet"]')) {
    const copy = doc.createElement("link");
    copy.rel = "stylesheet";
    copy.href = link.href;
    doc.head.append(copy);
  }
  for (const name of ["lang", "data-theme", "data-contrast", "data-motion", "style"]) {
    const value = document.documentElement.getAttribute(name);
    if (value !== null) doc.documentElement.setAttribute(name, value);
  }
  doc.body.className = "pip";
  placeholder = h("div", { class: "voicebar-placeholder", role: "region", "aria-label": "Voice bar" },
    h("p", {}, "The voice bar is in a floating window."),
    h("button", { type: "button", class: "btn small", onclick: popIn }, "Bring it back"));
  el.root.after(placeholder);
  doc.body.append(el.root); // the same bar, listeners and all
  win.addEventListener("pagehide", popIn, { once: true });
  render();
  updateHeight();
  el.area.focus();
}

function popIn() {
  if (!pip) return;
  const win = pip;
  pip = null;
  closePopover();
  placeholder?.replaceWith(el.root);
  placeholder = null;
  try {
    win.close();
  } catch {
    // already closing
  }
  render();
  updateHeight();
}

// ------------------------------------------------------------------ setup

function find() {
  const root = document.getElementById("voicebar");
  const q = (id) => root.querySelector(`#${id}`);
  return {
    root,
    form: q("compose"),
    area: q("compose-text"),
    interim: q("voice-interim"),
    status: q("voice-status"),
    allow: q("voice-allow"),
    undo: q("voice-undo"),
    mic: q("mic"),
    micLabel: q("mic-label"),
    save: q("voice-save"),
    off: q("voice-off"),
    cancel: q("voice-cancel"),
    install: q("voice-install"),
    popout: q("voice-popout"),
    back: q("voice-back"),
    options: q("voice-options"),
    popover: q("voice-popover"),
    handsfree: q("handsfree"),
    wake: q("voice-wake"),
    stopWord: q("voice-stopword"),
    silence: q("voice-silence"),
    autoListen: q("voice-autolisten"),
    review: q("voice-review"),
    more: q("voice-more"),
    done: q("voice-popover-done"),
  };
}

// Keep the notes (and the toasts) clear of the bar, whatever its height.
function trackHeight() {
  const update = () => {
    const height = pip ? placeholder?.offsetHeight || 0 : el.root.offsetHeight;
    document.documentElement.style.setProperty("--voicebar-h", `${height}px`);
  };
  if ("ResizeObserver" in window) new ResizeObserver(update).observe(el.root);
  window.addEventListener("resize", update);
  update();
  return update;
}

function wireBar() {
  el.form.addEventListener("submit", (event) => {
    event.preventDefault();
    saveNow();
  });
  el.mic.addEventListener("click", toggleMic);
  el.off.addEventListener("click", saveNow);
  el.cancel.addEventListener("click", discard);
  el.allow.addEventListener("click", startListening);
  el.undo.addEventListener("click", undo);
  el.install.addEventListener("click", install);
  el.popout.addEventListener("click", popOut);
  el.back.addEventListener("click", popIn);
  el.status.addEventListener("click", () => pausedByOther && startListening());
  el.area.addEventListener("input", () => {
    autosize();
    if (voice?.state === "recording") {
      // Typing while recording: keep the edit, and add only what is said next.
      base = el.area.value;
      voice.rebase();
    }
    if (!el.area.value.trim()) spoken = false;
    render();
  });
  // Pasting a lone link saves it straight away: the fastest way to get a review.
  el.area.addEventListener("paste", (event) => {
    const pasted = event.clipboardData?.getData("text")?.trim() || "";
    if (!recording() && !el.area.value.trim() && /^https?:\/\/\S+$/.test(pasted)) {
      event.preventDefault();
      submit(pasted, "web-text");
    }
  });
  el.root.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation(); // the page's own Esc handling would do it twice
      if (!el.popover.hidden) closePopover(true);
      else if (recording() || el.area.value.trim()) discard();
      return;
    }
    const inOptions = el.popover.contains(event.target);
    const enter = event.key === "Enter" && !event.isComposing;
    if (!inOptions && enter && (event.ctrlKey || event.metaKey || (event.target === el.area && !event.shiftKey))) {
      event.preventDefault();
      saveNow();
      return;
    }
    // In the pop-out window the page's shortcuts don't reach: handle Alt+N here.
    if (event.altKey && event.code === "KeyN" && el.root.ownerDocument !== document) {
      event.preventDefault();
      toggleMic();
    }
  });
}

export function setupCapture() {
  if (el) return;
  el = find();
  phone?.addEventListener?.("change", render);
  setupVoice();
  wireBar();
  wirePopover();
  updateHeight = trackHeight();
  syncVoiceSettings();
  on("prefs", syncVoiceSettings);
  on("status", syncVoiceSettings);
  on("status", () => followApp());
  followApp();
  document.addEventListener("visibilitychange", () => {
    // Browsers may stop recognition in a background tab; pick up again on return.
    if (document.hidden || !voice || desktopApp()) return;
    if (voice.handsFree) voice.resume();
    else if (!userStopped && !pausedByOther && local.autoListen !== false && permission === "granted") startListening();
  });
  window.addEventListener("focus", () => {
    if (desktopApp()) return;
    if (pausedByOther && !userStopped && local.autoListen !== false) startListening();
  });
  if (document.getElementById("welcome-dialog")?.open) {
    // The welcome screen covers the bar: start listening once it's done.
    let waiting = true;
    on("welcome-done", () => {
      if (!waiting) return;
      waiting = false;
      autoStart();
    });
  } else {
    autoStart();
  }
  render();
}
