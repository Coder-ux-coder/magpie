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
  beforeStop,
  englishLang,
  isCancel,
  levenshtein,
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
