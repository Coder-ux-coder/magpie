// Capturing notes: the mic (browser speech or server transcription), hands-free
// wake phrase, dictation into the text box, and typing.

import { api, createNote } from "./api.js";
import { watchNote } from "./actions.js";
import { speak, speaking, stopSpeaking } from "./speech.js";
import { emit, local, on, saveLocal, speechLang, state } from "./state.js";
import { announce, h, toast } from "./ui.js";
import { Recorder, VoiceCapture, recordingSupported, speechSupported } from "./voice.js";

const $ = (id) => document.getElementById(id);
let voice = null;
let recorder = null;
let dictating = false;

function wakePhrase() {
  return state.prefs?.wake_phrase || "notes on";
}

function useServer() {
  return (!speechSupported || local.serverStt) && recordingSupported && state.status?.stt?.enabled;
}

function canCapture() {
  return (speechSupported && !local.serverStt) || useServer();
}

function setMic(stateName, label, status) {
  const mic = $("mic");
  mic.dataset.state = stateName;
  mic.setAttribute("aria-pressed", String(stateName === "recording"));
  $("mic-label").textContent = label;
  mic.title = label;
  if (status) $("voice-status").textContent = status;
}

function idleStatus() {
  if (!canCapture()) {
    return speechSupported || recordingSupported
      ? "Voice needs Chrome, Edge or Safari here, or server transcription. You can always type."
      : "This browser can't capture voice. Type your note below.";
  }
  if (voice?.handsFree) return `Listening for “${wakePhrase()}”…`;
  return `Tap the mic, or turn on hands-free and say “${wakePhrase()}”.`;
}

function showTranscript(finalText, interim = "") {
  const box = $("transcript");
  box.hidden = !finalText && !interim;
  box.querySelector(".final").textContent = finalText;
  box.querySelector(".interim").textContent = interim ? ` ${interim}` : "";
  box.lang = speechLang();
}

export function syncVoiceSettings() {
  const prefs = state.prefs || {};
  document.querySelectorAll(".wake-phrase").forEach((el) => { el.textContent = wakePhrase(); });
  voice?.configure({
    lang: prefs.speech_lang || "",
    wake: prefs.wake_phrase,
    stop: prefs.stop_phrase,
    silenceSeconds: prefs.silence_seconds ?? 8,
  });
  const mic = $("mic");
  mic.disabled = !canCapture();
  $("handsfree").disabled = !speechSupported || local.serverStt;
  if (!voice || voice.state === "off") setMic("idle", "Start voice note", idleStatus());
}

function announceFiled(note) {
  const message = note.state === "ready" ? note.reply || "Saved." : note.error || "Saved.";
  toast(message);
  if (local.readAloud && note.reply) {
    voice?.mute(15000);
    speak(note.reply, { lang: speechLang(), onend: () => voice?.mute(600) });
  }
}

export async function saveNote(text, source) {
  text = text.trim();
  if (!text) return true;
  // A slow server: say so, rather than leave the user wondering where the note went.
  let dismissSlow = null;
  const slow = setTimeout(() => { dismissSlow = toast("Still saving…", { timeout: 35000 }); }, 2500);
  try {
    const result = await createNote(text, source);
    if (result.queued) {
      toast(result.offline ? "You're offline. The note is saved on this device and will sync later." : "Couldn't reach Magpie. The note is saved on this device and will sync later.");
      return true;
    }
    watchNote(result.note.id, { onFiled: announceFiled });
    emit("note-created", result.note);
    announce("Saved. Claude is filing it.");
    return true;
  } catch (error) {
    toast(error.message, { error: true });
    return false;
  } finally {
    clearTimeout(slow);
    dismissSlow?.();
  }
}

function deliver(text) {
  if (!text) return;
  if (dictating || local.reviewVoice) {
    dictating = false;
    const area = $("compose-text");
    area.value = area.value ? `${area.value.trimEnd()} ${text}` : text;
    area.dispatchEvent(new Event("input"));
    area.focus();
    toast("Check the text, then press Save.");
    return;
  }
  saveNote(text, "web-voice").then((saved) => {
    if (saved) return;
    // Not saved and not queued: put the words in the text box so they aren't lost.
    const area = $("compose-text");
    area.value = area.value ? `${area.value.trimEnd()} ${text}` : text;
    area.dispatchEvent(new Event("input"));
  });
}

function setupVoice() {
  if (!speechSupported) return;
  voice = new VoiceCapture();
  voice.addEventListener("state", ({ detail }) => {
    if (detail.state === "recording") {
      stopSpeaking();
      setMic("recording", "Stop and save", `Listening… say “${state.prefs?.stop_phrase || "notes off"}” or tap to save.`);
      showTranscript("", "");
    } else if (detail.state === "standby") {
      setMic("standby", "Start voice note", `Listening for “${wakePhrase()}”…`);
      showTranscript("");
    } else {
      setMic("idle", "Start voice note", idleStatus());
      showTranscript("");
    }
  });
  voice.addEventListener("wake", () => announce("Recording a note."));
  voice.addEventListener("transcript", ({ detail }) => showTranscript(detail.final, detail.interim));
  voice.addEventListener("note", ({ detail }) => deliver(detail.text));
  voice.addEventListener("empty", () => {
    dictating = false;
    toast("I didn't catch anything.");
  });
  voice.addEventListener("cancelled", () => toast("Note discarded."));
  voice.addEventListener("error", ({ detail }) => {
    toast(detail.message, { error: true });
    $("handsfree").checked = voice.handsFree;
    saveLocal({ handsFree: voice.handsFree });
  });
}

let transcribing = false;

async function uploadRecording(blob) {
  transcribing = true;
  setMic("busy", "Transcribing…", "Transcribing…");
  const form = new FormData();
  form.append("file", blob, "note.webm");
  try {
    if (dictating || local.reviewVoice) {
      const result = await api("/api/transcribe", { method: "POST", form });
      deliver(result.text);
    } else {
      form.append("source", "web-audio");
      const result = await api("/api/notes/audio", { method: "POST", form });
      watchNote(result.note.id, { onFiled: announceFiled });
      emit("note-created", result.note);
      toast("Saved. Claude is filing it…");
    }
  } catch (error) {
    // Keep the recording, so a failed upload loses nothing.
    toast(error.message, { error: true, timeout: 30000, action: "Try again", onAction: () => uploadRecording(blob) });
  } finally {
    transcribing = false;
    setMic("idle", "Start voice note", idleStatus());
  }
}

async function toggleServerRecording() {
  if (transcribing) return; // the mic stays off until the last recording is done
  if (recorder?.active) {
    const blob = await recorder.stop();
    recorder = null;
    await uploadRecording(blob);
    return;
  }
  try {
    recorder = new Recorder();
    await recorder.start();
    setMic("recording", "Stop and save", "Recording… tap again to save.");
  } catch (error) {
    recorder = null;
    toast(error.name === "NotAllowedError" ? "Microphone access is blocked for this site." : `Couldn't start recording: ${error.message}`, { error: true });
    setMic("idle", "Start voice note", idleStatus());
  }
}

export function toggleMic() {
  if (useServer()) return toggleServerRecording();
  if (!voice) {
    toast("Voice capture isn't available in this browser. Type your note instead.", { error: true });
    return;
  }
  if (speaking()) stopSpeaking();
  if (voice.state === "recording") voice.finish();
  else voice.start();
}

export function toggleHandsFree(force) {
  const box = $("handsfree");
  if (!voice || box.disabled) return;
  const on = typeof force === "boolean" ? force : !voice.handsFree;
  box.checked = on;
  voice.setHandsFree(on);
  saveLocal({ handsFree: on });
  toast(on ? `Hands-free on. Say “${wakePhrase()}” to start a note.` : "Hands-free off.");
}

export function stopVoice() {
  if (voice && voice.state === "recording") voice.cancelNote();
  if (recorder?.active) {
    recorder.abort();
    recorder = null;
    setMic("idle", "Start voice note", idleStatus());
  }
}

function setupCompose() {
  const form = $("compose");
  const area = $("compose-text");
  const resize = () => {
    area.style.height = "auto";
    area.style.height = `${Math.min(area.scrollHeight + 2, 224)}px`;
  };
  area.addEventListener("input", resize);
  area.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      form.requestSubmit();
    }
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const text = area.value.trim();
    if (!text) {
      area.focus();
      return;
    }
    area.value = "";
    resize();
    if (!(await saveNote(text, "web-text")) && !area.value.trim()) {
      area.value = text; // not saved: give it back so nothing is lost
      resize();
    }
  });
  $("compose-dictate").addEventListener("click", () => {
    if (!canCapture()) {
      toast("Voice capture isn't available in this browser.", { error: true });
      return;
    }
    dictating = true;
    if (useServer()) toggleServerRecording();
    else if (voice.state !== "recording") voice.start();
    else voice.finish();
  });
  // Pasting a lone link saves it straight away: the fastest way to get a review.
  area.addEventListener("paste", (event) => {
    const pasted = event.clipboardData?.getData("text")?.trim() || "";
    if (!area.value.trim() && /^https?:\/\/\S+$/.test(pasted)) {
      event.preventDefault();
      saveNote(pasted, "web-text");
    }
  });
}

export function setupCapture() {
  setupVoice();
  setupCompose();
  $("mic").addEventListener("click", toggleMic);
  $("handsfree").addEventListener("change", (event) => toggleHandsFree(event.target.checked));
  syncVoiceSettings();
  if (voice && local.handsFree && !local.serverStt) {
    $("handsfree").checked = true;
    voice.setHandsFree(true);
  }
  on("prefs", syncVoiceSettings);
  on("status", syncVoiceSettings);
  document.addEventListener("visibilitychange", () => {
    // Browsers stop recognition in background tabs; resume when we're back.
    if (!document.hidden && voice?.handsFree && voice.state === "standby") voice.setHandsFree(true);
  });
}

export function startVoiceFromShortcut() {
  if (!canCapture()) return;
  const prompt = h("div", { class: "banner info" }, h("p", {}, "Ready when you are."),
    h("button", { type: "button", class: "btn small primary", onclick: () => { prompt.remove(); toggleMic(); } }, "Start recording"));
  try {
    toggleMic();
  } catch {
    document.getElementById("banners").prepend(prompt);
  }
}
