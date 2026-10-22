// Run with: node --test "tests/js/*.test.mjs"
// Drives VoiceCapture with a fake speech recogniser that reports whole sessions,
// the way Chrome does: every event carries the full list of results so far.
import assert from "node:assert/strict";
import { mock, test } from "node:test";

class FakeRecognition {
  static last = null;

  constructor() {
    FakeRecognition.last = this;
    this.running = false;
    this.starts = 0;
  }

  start() {
    if (this.running) throw new Error("InvalidStateError");
    this.running = true;
    this.starts += 1;
  }

  stop() {
    if (!this.running) return;
    this.running = false;
    this.onend?.();
  }

  // Test helper: the session's results so far, as [text, final] pairs.
  hear(...results) {
    const list = results.map(([text, final]) => Object.assign([{ transcript: text }], { isFinal: final }));
    this.onresult?.({ resultIndex: 0, results: list });
  }

  // Test helper: the browser ends the session by itself.
  end() {
    this.running = false;
    this.onend?.();
  }
}

globalThis.window = { SpeechRecognition: FakeRecognition };
Object.defineProperty(globalThis, "navigator", { value: { language: "en-GB" }, configurable: true });
const { VoiceCapture } = await import("../../src/magpie/web/static/js/voice.js");

function capture(options = {}) {
  const voice = new VoiceCapture({ silenceSeconds: 0, ...options });
  const events = [];
  for (const type of ["note", "empty", "cancelled", "wake", "error"]) {
    voice.addEventListener(type, ({ detail }) => events.push([type, detail.text ?? ""]));
  }
  return { voice, events, rec: () => FakeRecognition.last };
}

test("“notes on … notes off” in interim results saves once, after a moment", (t) => {
  t.mock.timers.enable({ apis: ["setTimeout", "Date"] });
  const { voice, events, rec } = capture();
  voice.setHandsFree(true);
  rec().hear(["notes on buy milk", false]);
  assert.equal(voice.state, "recording");
  rec().hear(["notes on buy milk notes off", false]);
  assert.deepEqual(events.filter(([type]) => type === "note"), []); // still settling
  t.mock.timers.tick(700);
  assert.deepEqual(events.filter(([type]) => type === "note"), [["note", "buy milk"]]);
  assert.equal(voice.state, "standby");
  // The recogniser later settles the same words: no second note.
  rec().hear(["Notes on, buy milk. Notes off.", true]);
  t.mock.timers.tick(5000);
  assert.equal(events.filter(([type]) => type === "note").length, 1);
});

test("a settled “Notes off.” saves at once, whatever the punctuation", () => {
  const { voice, events, rec } = capture();
  voice.setHandsFree(true);
  rec().hear(["Notes on, call James at five. NOTES OFF!", true]);
  assert.deepEqual(events.at(-1), ["note", "call James at five."]);
  assert.equal(voice.state, "standby");
});

test("a stop word the recogniser takes back does not end the note", (t) => {
  t.mock.timers.enable({ apis: ["setTimeout", "Date"] });
  const { voice, events, rec } = capture();
  voice.start();
  rec().hear(["book the notes off", false]);
  rec().hear(["book the notes office", false]);
  t.mock.timers.tick(1000);
  assert.equal(voice.state, "recording");
  assert.deepEqual(events, []);
  assert.equal(voice.take(), "book the notes office");
});

test("Save takes everything showing, including unsettled words, exactly once", () => {
  const { voice, events, rec } = capture();
  voice.start();
  rec().hear(["call Mum", true], [" on Saturday", false]);
  assert.equal(voice.take(), "call Mum on Saturday");
  assert.equal(voice.take(), ""); // a second press finds nothing left
  assert.deepEqual(events, []);
  assert.equal(voice.state, "off");
});

test("cancel always works while recording and hands the text back", () => {
  const { voice, events, rec } = capture();
  voice.start();
  rec().hear(["remind me to call James", false]);
  assert.equal(voice.cancelNote({ announce: false }), "remind me to call James");
  assert.equal(voice.state, "off");
  assert.deepEqual(events, []);
  // Saying "cancel note" works too.
  voice.setHandsFree(true);
  rec().hear(["notes on buy bread cancel note", true]);
  assert.deepEqual(events.at(-1), ["cancelled", "buy bread cancel note"]);
  assert.equal(voice.state, "standby");
});

test("words survive the browser restarting recognition mid-note", (t) => {
  t.mock.timers.enable({ apis: ["setTimeout", "Date"] });
  const { voice, rec } = capture();
  voice.start();
  rec().hear(["first part", true]);
  t.mock.timers.tick(2000);
  rec().end(); // the browser ends the session; Magpie starts a new one
  t.mock.timers.tick(200);
  assert.equal(rec().running, true);
  rec().hear(["second part", false]);
  assert.equal(voice.current().text, "first part second part");
});

test("typing while recording keeps the edit and adds only new words", () => {
  const { voice, rec } = capture();
  voice.start();
  rec().hear(["call James", true], [" about", false]);
  voice.rebase(); // the user typed into the box
  rec().hear(["call James", true], [" about the budget", true]);
  assert.equal(voice.current().text, "about the budget");
});

test("a pause after a false wake goes back to listening without saving", (t) => {
  t.mock.timers.enable({ apis: ["setTimeout", "Date"] });
  const { voice, events, rec } = capture({ silenceSeconds: 3 });
  voice.setHandsFree(true);
  rec().hear(["notes on", true]);
  assert.equal(voice.state, "recording");
  t.mock.timers.tick(3000);
  assert.equal(voice.state, "standby");
  assert.deepEqual(events.filter(([type]) => type !== "wake"), []);
});

test("Magpie's own voice, read aloud, can't wake it", (t) => {
  t.mock.timers.enable({ apis: ["setTimeout", "Date"] });
  const { voice, rec } = capture();
  voice.setHandsFree(true);
  voice.mute(5000);
  rec().hear(["saved notes on your list", true]);
  assert.equal(voice.state, "standby");
  t.mock.timers.tick(6000);
  rec().hear(["saved notes on your list", true], ["notes on call Mum", false]);
  assert.equal(voice.state, "recording");
  assert.equal(voice.current().text, "call Mum");
});

test("a blocked microphone stops listening and keeps what was said", () => {
  const { voice, events, rec } = capture();
  voice.start();
  rec().hear(["buy milk", true]);
  rec().onerror({ error: "not-allowed" });
  assert.equal(voice.state, "off");
  assert.deepEqual(events.at(-1), ["error", "buy milk"]);
});

mock.reset();
