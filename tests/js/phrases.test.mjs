// Run with: node --test "tests/js/*.test.mjs"
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  CANCEL_PHRASES,
  DEFAULT_STOP,
  DEFAULT_WAKE,
  STOP_ALIASES,
  WAKE_ALIASES,
  afterWake,
  appendText,
  beforeStop,
  englishLang,
  findWake,
  isCancel,
  levenshtein,
  noteFromResults,
  phraseList,
} from "../../src/magpie/web/static/js/phrases.js";

const wake = phraseList("", DEFAULT_WAKE, WAKE_ALIASES);
const stop = phraseList("", DEFAULT_STOP, STOP_ALIASES);

test("wake phrase starts a note and keeps what follows", () => {
  assert.deepEqual(afterWake("Notes on buy milk tomorrow", wake), { found: true, rest: "buy milk tomorrow" });
  assert.deepEqual(afterWake("okay, nodes on. Call mom at five", wake), { found: true, rest: "Call mom at five" });
  assert.deepEqual(afterWake("note son pick up the dry cleaning", wake), { found: true, rest: "pick up the dry cleaning" });
  assert.deepEqual(afterWake("Notes, on.", wake), { found: true, rest: "" });
});

test("ordinary speech does not wake Magpie", () => {
  assert.equal(afterWake("nothing to see here", wake).found, false);
  assert.equal(afterWake("notes one two three", wake).found, false);
  assert.equal(afterWake("I took notes of the meeting", wake).found, false);
  assert.equal(afterWake("my notes in the drawer", wake).found, false);
  assert.equal(afterWake("", wake).found, false);
});

test("stop phrases end the note exactly", () => {
  assert.deepEqual(beforeStop("buy milk notes off", stop), { found: true, text: "buy milk" });
  assert.deepEqual(beforeStop("call mom. End note.", stop), { found: true, text: "call mom." });
  assert.deepEqual(beforeStop("remind me notes off and more", stop), { found: true, text: "remind me" });
  // "notes of" is everyday English and must not stop a note.
  assert.deepEqual(beforeStop("I took notes of the meeting", stop), { found: false, text: "I took notes of the meeting" });
});

test("cancel phrases", () => {
  assert.equal(isCancel("never mind, cancel note", CANCEL_PHRASES), true);
  assert.equal(isCancel("cancel the subscription", CANCEL_PHRASES), false);
});

test("custom phrases, with punctuation and accents", () => {
  const custom = phraseList("hey magpie", DEFAULT_WAKE, WAKE_ALIASES);
  assert.deepEqual(custom, ["hey magpie"]);
  assert.deepEqual(afterWake("Hey, Magpie! Water the plants", custom), { found: true, rest: "Water the plants" });
  const accented = phraseList("Café notes", DEFAULT_WAKE, WAKE_ALIASES);
  assert.deepEqual(afterWake("café notes: book a table for Friday", accented), { found: true, rest: "book a table for Friday" });
});

test("Magpie listens for English whatever the browser is set to", () => {
  assert.equal(englishLang("en-AU", "en-US"), "en-AU");
  assert.equal(englishLang("", "en-US"), "en-US");
  assert.equal(englishLang(" EN-gb ", "en-US"), "EN-gb");
  assert.equal(englishLang("", "fr-FR"), "en-GB");
  assert.equal(englishLang("fr-FR", "de-DE"), "en-GB");
  assert.equal(englishLang(undefined, null), "en-GB");
  assert.equal(englishLang("english", "e"), "en-GB");
});

test("levenshtein", () => {
  assert.equal(levenshtein("noteson", "nodeson"), 1);
  assert.equal(levenshtein("", "abc"), 3);
  assert.equal(levenshtein("same", "same"), 0);
});

// ----------------------------------------------------- notes in live results

const r = (text, final = false) => ({ text, final });
const read = (results, options) => noteFromResults(results, { wake, stop, ...options });

test("the wake phrase is found in interim results, even split over two", () => {
  assert.equal(findWake([r("hello there", true), r("notes on buy milk")], 0, wake), 1);
  assert.equal(findWake([r("so notes", true), r("on call Mum")], 0, wake), 0);
  assert.equal(findWake([r("so notes", true), r("on call Mum")], 1, wake), -1); // already used
  assert.equal(findWake([r("I took notes of it", true)], 0, wake), -1);
});

test("a note reads its words after the wake phrase, settled and interim", () => {
  assert.deepEqual(read([r("Notes on, buy milk", true), r(" and eggs")], { fromWake: true }).final, "buy milk");
  assert.equal(read([r("Notes on, buy milk", true), r(" and eggs")], { fromWake: true }).interim, "and eggs");
  assert.equal(read([r("so notes", true), r("on call Mum")], { fromWake: true }).text, "call Mum");
  // Pressing the mic and then saying "notes on" anyway.
  assert.equal(read([r("notes on, call James", true)]).text, "call James");
  assert.equal(read([r("call James about notes on the budget", true)]).text, "call James about notes on the budget");
});

test("the stop word ends a note even before it settles, with any punctuation", () => {
  const interim = read([r("buy milk", true), r(" notes off")]);
  assert.deepEqual([interim.stopped, interim.settled, interim.text], [true, false, "buy milk"]);
  const settled = read([r("Notes on, buy milk and eggs. Notes off.", true)], { fromWake: true });
  assert.deepEqual([settled.stopped, settled.settled, settled.text], [true, true, "buy milk and eggs."]);
  assert.equal(read([r("buy milk. NOTES OFF!", true)]).text, "buy milk.");
  assert.equal(read([r("I took notes of the meeting", true)]).stopped, false);
});

test("cancel phrases are noticed in interim results too", () => {
  const said = read([r("call James", true), r(" cancel note")]);
  assert.deepEqual([said.cancelled, said.cancelSettled], [true, false]);
  assert.equal(read([r("cancel the subscription", true)]).cancelled, false);
});

test("dictation is added to typed text, keeping line breaks", () => {
  assert.equal(appendText("Shopping:\n", "milk"), "Shopping:\nmilk");
  assert.equal(appendText("Call James  ", "at five"), "Call James at five");
  assert.equal(appendText("", "  hello "), "hello");
  assert.equal(appendText("keep this", ""), "keep this");
});
