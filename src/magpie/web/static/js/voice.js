// Voice capture.
//
// VoiceCapture wraps the browser's Web Speech API:
//   off       -> not listening
//   standby   -> hands-free: listening only for the wake phrase ("notes on")
//   recording -> capturing a note until the stop phrase, silence, Save or Notes off
// Each recognition session is read as a whole (see noteFromResults), so a wake or
// stop phrase still in the interim results acts at once, and later corrections
// from the recogniser replace earlier guesses instead of being added twice.
// Recorder is the fallback for browsers without speech recognition: it records
// audio and the server transcribes it (MAGPIE_STT_API_KEY).

import {
  DEFAULT_STOP,
  DEFAULT_WAKE,
  STOP_ALIASES,
  WAKE_ALIASES,
  englishLang,
  findWake,
  noteFromResults,
  phraseList,
  tidy,
} from "./phrases.js";

const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;

export const speechSupported = Boolean(Recognition);
export const recordingSupported = Boolean(navigator.mediaDevices?.getUserMedia && window.MediaRecorder);

// A stop or cancel phrase heard only in interim results waits this long, in case
// the recogniser is still changing its mind ("notes off…" → "notes office").
const SETTLE_MS = 700;

const ERRORS = {
  "not-allowed": "The microphone is blocked. Allow it for this site (the icon next to the address bar), then press the mic.",
  "service-not-allowed": "This browser doesn't allow speech recognition here. Use Chrome or Edge, or type your note.",
  "audio-capture": "No microphone was found. Plug one in or check your sound settings, then press the mic.",
  network: "Speech recognition needs an internet connection in this browser. Magpie keeps trying.",
  "language-not-supported": "Your browser can't recognise that accent. Pick another in Settings → Voice.",
};

const join = (a, b) => tidy(`${a || ""} ${b || ""}`);

export class VoiceCapture extends EventTarget {
  constructor(options = {}) {
    super();
    this.options = { lang: "", wake: DEFAULT_WAKE, stop: DEFAULT_STOP, silenceSeconds: 8, ...options };
    this.state = "off";
    this.handsFree = false;
    this.running = false;
    this.rec = null;
    this.results = []; // the current recognition session, as [{text, final}]
    this.consumed = -1; // results up to here belong to finished notes (or Magpie's own voice)
    this.from = 0; // where the note being recorded starts
    this.fromWake = false; // ...and whether that result holds the wake phrase
    this.carried = ""; // the note's words from earlier recognition sessions
    this.silenceTimer = null;
    this.settleTimer = null;
    this.muteUntil = 0;
    this.failures = 0;
    this.lastStart = 0;
    this._phrases();
  }

  configure(options) {
    Object.assign(this.options, options);
    this._phrases();
    if (this.rec) this.rec.lang = this._lang();
  }

  _phrases() {
    this.wake = phraseList(this.options.wake, DEFAULT_WAKE, WAKE_ALIASES);
    this.stop = phraseList(this.options.stop, DEFAULT_STOP, STOP_ALIASES);
  }

  _lang() {
    return englishLang(this.options.lang, navigator.language);
  }

  setHandsFree(on) {
    this.handsFree = on;
    if (on && this.state === "off") {
      this.consumed = this.results.length - 1; // nothing heard before now can wake it
      this._setState("standby");
      this._ensureRunning();
    } else if (!on && this.state === "standby") {
      this._setState("off");
      this._halt();
    }
  }

  // Push-to-talk: start a note right away.
  start() {
    if (this.state === "recording") return;
    this.carried = "";
    this.from = this.results.length;
    this.fromWake = false;
    this._setState("recording");
    this._ensureRunning();
    this._armSilence();
    this._emit("transcript", { final: "", interim: "" });
  }

  /** The note so far: {final, interim, text}. */
  current() {
    if (this.state !== "recording") return { final: "", interim: "", text: "" };
    const live = noteFromResults(this.results, {
      from: this.from,
      fromWake: this.fromWake,
      wake: this.wake,
      stop: this.stop,
    });
    return { ...live, final: join(this.carried, live.final), text: join(this.carried, live.text) };
  }

  // Stop recording and hand back the text (Save or Notes off was pressed).
  take() {
    if (this.state !== "recording") return "";
    const { text } = this.current();
    this._endNote();
    return text;
  }

  // Stop recording and announce the note (stop phrase, silence, the mic button).
  finish() {
    if (this.state !== "recording") return;
    const { text } = this.current();
    this._endNote();
    this._emit(text ? "note" : "empty", { text });
  }

  // Throw the note away. Returns its text so it can be undone.
  cancelNote({ announce = true } = {}) {
    if (this.state !== "recording") return "";
    const { text } = this.current();
    this._endNote();
    if (announce) this._emit("cancelled", { text });
    return text;
  }

  // The user edited the text: keep listening, and add only what is said from now on.
  rebase() {
    if (this.state !== "recording") return;
    const pending = this.results.findIndex((result, index) => index >= this.from && !result.final);
    const from = pending === -1 ? this.results.length : pending;
    this.fromWake = this.fromWake && from === this.from;
    this.from = from;
    this.carried = "";
    this._clearSettle();
  }

  // Make sure recognition is running again (after the tab was in the background).
  resume() {
    if (this.state !== "off") this._ensureRunning();
  }

  // Ignore what the mic hears for a while (e.g. while Magpie reads a reply aloud).
  mute(ms) {
    this.muteUntil = Date.now() + ms;
  }

  shutdown() {
    this.handsFree = false;
    this._clearSilence();
    this._clearSettle();
    this._setState("off");
    this._halt();
  }

  _endNote() {
    this.consumed = this.results.length - 1;
    this.carried = "";
    this._clearSilence();
    this._clearSettle();
    if (this.handsFree) {
      this._setState("standby");
    } else {
      this._setState("off");
      this._halt();
    }
  }

  _ensureRunning() {
    if (this.running) return;
    const rec = this.rec || this._create();
    try {
      rec.start();
      this.running = true;
      this.lastStart = Date.now();
    } catch {
      // Already started (InvalidStateError): nothing to do.
    }
  }

  _halt() {
    if (this.rec && this.running) {
      try {
        this.rec.stop();
      } catch {
        // ignore
      }
    }
  }

  _create() {
    const rec = new Recognition();
    rec.continuous = true;
    rec.interimResults = true;
    rec.maxAlternatives = 1;
    rec.lang = this._lang();
    rec.onresult = (event) => this._onResult(event);
    rec.onerror = (event) => this._onError(event);
    rec.onend = () => this._onEnd();
    this.rec = rec;
    return rec;
  }

  _onEnd() {
    this.running = false;
    if (this.state === "recording") {
      // A stop phrase still waiting to settle counts: the session is over.
      const settling = Boolean(this.settleTimer);
      this._clearSettle();
      // Otherwise keep what was said; the next session's results start from scratch.
      if (!(settling && this._settle())) this.carried = this.current().text;
    }
    this._afterEnd();
  }

  _afterEnd() {
    this.results = [];
    this.consumed = -1;
    this.from = 0;
    this.fromWake = false;
    if (this.state === "off") return;
    // Browsers end recognition every so often; restart, backing off if it keeps
    // ending immediately.
    const quick = Date.now() - this.lastStart < 1500;
    this.failures = quick ? this.failures + 1 : 0;
    if (this.failures > 5) {
      this._fail("Speech recognition keeps stopping. Press the mic to try again.");
      return;
    }
    setTimeout(() => this.state !== "off" && this._ensureRunning(), quick ? 600 * this.failures : 150);
  }

  _onResult(event) {
    this.results = Array.from(event.results, (result) => ({
      text: result[0]?.transcript || "",
      final: Boolean(result.isFinal),
    }));
    if (Date.now() < this.muteUntil) {
      this.consumed = this.results.length - 1; // Magpie's own voice, read aloud
      return;
    }
    if (this.state === "standby") {
      const index = findWake(this.results, this.consumed + 1, this.wake);
      if (index === -1) {
        // Settled chatter can't start a note any more; keep the last result in
        // case "notes" and "on" arrive separately.
        const settled = this.results.findLastIndex((result) => result.final);
        this.consumed = Math.max(this.consumed, settled - 1);
        return;
      }
      this.carried = "";
      this.from = index;
      this.fromWake = true;
      this._setState("recording");
      this._emit("wake");
    }
    if (this.state === "recording") this._review();
  }

  _review() {
    const note = this.current();
    if (note.cancelled) {
      if (note.cancelSettled) this.cancelNote();
      else this._settleLater();
      return;
    }
    this._emit("transcript", { final: note.final, interim: note.interim });
    if (note.stopped) {
      if (note.settled) this.finish();
      else this._settleLater();
      return;
    }
    this._clearSettle();
    this._armSilence();
  }

  _settleLater() {
    if (this.settleTimer) return;
    this.settleTimer = setTimeout(() => {
      this.settleTimer = null;
      this._settle();
    }, SETTLE_MS);
  }

  // Act on a stop or cancel phrase that is still in the interim results.
  _settle() {
    if (this.state !== "recording") return false;
    const note = this.current();
    if (note.cancelled) this.cancelNote();
    else if (note.stopped) this.finish();
    else return false;
    return true;
  }

  _clearSettle() {
    clearTimeout(this.settleTimer);
    this.settleTimer = null;
  }

  _armSilence() {
    this._clearSilence();
    const seconds = Number(this.options.silenceSeconds) || 0;
    if (!seconds) return;
    this.silenceTimer = setTimeout(() => {
      if (this.state !== "recording") return;
      if (this.current().text || !this.handsFree) this.finish();
      else this._endNote(); // woken by mistake: back to listening, quietly
    }, seconds * 1000);
  }

  _clearSilence() {
    clearTimeout(this.silenceTimer);
    this.silenceTimer = null;
  }

  _onError(event) {
    if (event.error === "no-speech" || event.error === "aborted") return;
    const message = ERRORS[event.error] || `Speech recognition stopped (${event.error}). Press the mic to try again.`;
    if (event.error === "network") {
      // Recognition ends and restarts by itself (see _afterEnd).
      this._emit("error", { error: event.error, message, transient: true });
      return;
    }
    this._fail(message, event.error);
  }

  // Stop for good, handing back anything said so far so it isn't lost.
  _fail(message, error = "stopped") {
    const text = this.state === "recording" ? this.current().text : "";
    this.shutdown();
    this._emit("error", { error, message, text });
  }

  _setState(state) {
    if (this.state === state) return;
    this.state = state;
    this._emit("state", { state });
  }

  _emit(type, detail = {}) {
    this.dispatchEvent(new CustomEvent(type, { detail }));
  }
}

export class Recorder {
  async start() {
    this.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const types = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"];
    const mimeType = types.find((type) => MediaRecorder.isTypeSupported?.(type)) || "";
    this.recorder = new MediaRecorder(this.stream, mimeType ? { mimeType } : undefined);
    this.chunks = [];
    this.recorder.ondataavailable = (event) => event.data.size && this.chunks.push(event.data);
    this.recorder.start(1000);
  }

  get active() {
    return this.recorder?.state === "recording";
  }

  stop() {
    return new Promise((resolve) => {
      if (!this.recorder) return resolve(null);
      this.recorder.onstop = () => {
        this.stream.getTracks().forEach((track) => track.stop());
        resolve(new Blob(this.chunks, { type: this.recorder.mimeType || "audio/webm" }));
      };
      this.recorder.stop();
    });
  }

  abort() {
    try {
      this.recorder?.stop();
    } catch {
      // ignore
    }
    this.stream?.getTracks().forEach((track) => track.stop());
  }
}
