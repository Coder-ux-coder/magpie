// Voice capture.
//
// VoiceCapture wraps the browser's Web Speech API:
//   off       -> not listening
//   standby   -> hands-free: listening only for the wake phrase ("notes on")
//   recording -> capturing a note until the stop phrase, silence, or a tap
// Recorder is the fallback for browsers without speech recognition: it records
// audio and the server transcribes it (MAGPIE_STT_API_KEY).

import {
  CANCEL_PHRASES,
  DEFAULT_STOP,
  DEFAULT_WAKE,
  STOP_ALIASES,
  WAKE_ALIASES,
  afterWake,
  beforeStop,
  englishLang,
  isCancel,
  phraseList,
  tidy,
} from "./phrases.js";

const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;

export const speechSupported = Boolean(Recognition);
export const recordingSupported = Boolean(navigator.mediaDevices?.getUserMedia && window.MediaRecorder);

const ERRORS = {
  "not-allowed": "Microphone access is blocked. Allow the microphone for this site in your browser settings.",
  "service-not-allowed": "Speech recognition isn't allowed in this browser. Try Chrome, Edge or Safari, or type instead.",
  "audio-capture": "No microphone was found.",
  network: "Speech recognition needs an internet connection in this browser.",
  "language-not-supported": "Your browser can't recognise that accent. Pick another in Settings → Voice.",
};

export class VoiceCapture extends EventTarget {
  constructor(options = {}) {
    super();
    this.options = { lang: "", wake: DEFAULT_WAKE, stop: DEFAULT_STOP, silenceSeconds: 8, ...options };
    this.state = "off";
    this.handsFree = false;
    this.buffer = "";
    this.standbyTail = "";
    this.running = false;
    this.rec = null;
    this.silenceTimer = null;
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
      this._setState("standby");
      this._ensureRunning();
    } else if (!on && this.state === "standby") {
      this._setState("off");
      this._halt();
    }
  }

  // Push-to-talk: start a note right away.
  start() {
    this.buffer = "";
    this._setState("recording");
    this._ensureRunning();
    this._armSilence();
  }

  finish() {
    const text = tidy(this.buffer);
    this.buffer = "";
    this._clearSilence();
    this._afterNote();
    this._emit(text ? "note" : "empty", { text });
  }

  cancelNote() {
    this.buffer = "";
    this._clearSilence();
    this._afterNote();
    this._emit("cancelled");
  }

  // Ignore what the mic hears for a while (e.g. while Magpie reads a reply aloud).
  mute(ms) {
    this.muteUntil = Date.now() + ms;
  }

  shutdown() {
    this.handsFree = false;
    this._clearSilence();
    this._setState("off");
    this._halt();
  }

  _afterNote() {
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
    rec.onend = () => {
      this.running = false;
      if (this.state === "off") return;
      // Browsers end recognition every so often; restart, backing off if it
      // keeps ending immediately.
      const quick = Date.now() - this.lastStart < 1500;
      this.failures = quick ? this.failures + 1 : 0;
      if (this.failures > 5) {
        this._emit("error", { message: "Speech recognition keeps stopping. Tap the mic to try again." });
        this.shutdown();
        return;
      }
      setTimeout(() => this.state !== "off" && this._ensureRunning(), quick ? 600 * this.failures : 150);
    };
    this.rec = rec;
    return rec;
  }

  _onResult(event) {
    if (Date.now() < this.muteUntil) return;
    let interim = "";
    for (let i = event.resultIndex; i < event.results.length; i++) {
      const result = event.results[i];
      const text = result[0]?.transcript || "";
      if (result.isFinal) this._onFinal(text);
      else interim += text;
    }
    if (this.state === "recording") {
      this._emit("transcript", { final: this.buffer, interim });
      if (interim) this._armSilence();
    }
  }

  _onFinal(text) {
    if (this.state === "standby") {
      // Look across the previous phrase too, in case "notes" and "on" were
      // recognised as separate results.
      const combined = `${this.standbyTail} ${text}`;
      const wake = afterWake(combined, this.wake);
      this.standbyTail = text;
      if (!wake.found) return;
      this.standbyTail = "";
      this.buffer = "";
      this._setState("recording");
      this._emit("wake");
      text = wake.rest;
      if (!text) {
        this._armSilence();
        return;
      }
    }
    if (this.state !== "recording") return;
    if (isCancel(text, CANCEL_PHRASES)) {
      this.cancelNote();
      return;
    }
    const combined = tidy(`${this.buffer} ${text}`);
    const stop = beforeStop(combined, this.stop);
    this.buffer = stop.text;
    this._emit("transcript", { final: this.buffer, interim: "" });
    if (stop.found) {
      this.finish();
      return;
    }
    this._armSilence();
  }

  _armSilence() {
    this._clearSilence();
    const seconds = Number(this.options.silenceSeconds) || 0;
    if (!seconds) return;
    this.silenceTimer = setTimeout(() => {
      if (this.state !== "recording") return;
      if (this.buffer.trim()) this.finish();
      else if (this.handsFree) this._setState("standby");
      else this.finish();
    }, seconds * 1000);
  }

  _clearSilence() {
    clearTimeout(this.silenceTimer);
    this.silenceTimer = null;
  }

  _onError(event) {
    if (event.error === "no-speech" || event.error === "aborted") return;
    const message = ERRORS[event.error] || `Speech recognition error: ${event.error}`;
    if (event.error !== "network") this.shutdown();
    this._emit("error", { error: event.error, message });
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
