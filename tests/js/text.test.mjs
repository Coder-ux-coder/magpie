// Run with: node --test "tests/js/*.test.mjs"
import assert from "node:assert/strict";
import { test } from "node:test";

import { dayGroup, isChecklist, listItems, listSummary, markWords, savedLine, searchWords, toggleItem } from "../../src/magpie/web/static/js/text.js";

const GROCERIES = "Groceries for the weekend:\n- Milk\n- [x] Eggs\n* Bread\n• Lemons\n2. Tea bags";

test("list items, ticked off or not, in any bullet style", () => {
  assert.deepEqual(listItems(GROCERIES), [
    { line: 1, text: "Milk", done: false },
    { line: 2, text: "Eggs", done: true },
    { line: 3, text: "Bread", done: false },
    { line: 4, text: "Lemons", done: false },
    { line: 5, text: "Tea bags", done: false },
  ]);
  assert.deepEqual(listItems("- [X] Done in capitals"), [{ line: 0, text: "Done in capitals", done: true }]);
  assert.deepEqual(listItems("-  \n- [ ]\nno bullet here\n-dash without a space"), []);
  assert.deepEqual(listItems(null), []);
});

test("a note is a checklist when it's mostly a list, or filed as one", () => {
  assert.equal(isChecklist(GROCERIES), true);
  assert.equal(isChecklist("- Milk"), false); // one item isn't a list
  const prose = "Met James about the budget.\nWe agreed on three things.\nHe sends numbers Friday.\n- see the deck\n- and the plan";
  assert.equal(isChecklist(prose), false);
  assert.equal(isChecklist(prose, "list"), true);
});

test("ticking an item off changes only its line, and back again", () => {
  const ticked = toggleItem(GROCERIES, 1, true);
  assert.equal(ticked.split("\n")[1], "- [x] Milk");
  assert.equal(toggleItem(ticked, 1, false).split("\n")[1], "- Milk");
  assert.equal(toggleItem(GROCERIES, 5, true).split("\n")[5], "2. [x] Tea bags");
  assert.equal(toggleItem("  * nested", 0, true), "  * [x] nested");
  assert.equal(toggleItem(GROCERIES, 0, true), GROCERIES); // not an item: unchanged
  assert.equal(toggleItem(GROCERIES, 99, true), GROCERIES);
});

test("a card's line says what's left", () => {
  assert.equal(listSummary(GROCERIES), "1 of 5 done · Milk · Bread · Lemons · Tea bags");
  assert.equal(listSummary("- Milk\n- Eggs"), "Milk · Eggs");
  assert.equal(listSummary("- [x] Milk\n- [x] Eggs"), "All 2 done");
  assert.equal(listSummary("no list"), "");
});

test("search words are marked wherever they appear, whatever their case", () => {
  assert.deepEqual(searchWords("  the Budget  budget a "), ["budget", "the"]);
  assert.deepEqual(searchWords("C++ (x)"), []);
  assert.deepEqual(markWords("Call James about the Q3 budget", ["budget", "james"]), [
    { text: "Call ", hit: false },
    { text: "James", hit: true },
    { text: " about the Q3 ", hit: false },
    { text: "budget", hit: true },
  ]);
  // Characters that mean something in a pattern are plain text here.
  assert.deepEqual(markWords("costs $5 (approx.)", searchWords("$5 (approx.)")), [
    { text: "costs $5 (", hit: false },
    { text: "approx", hit: true },
    { text: ".)", hit: false },
  ]);
  assert.deepEqual(markWords("a+b or a.b", ["a.b"]), [{ text: "a+b or ", hit: false }, { text: "a.b", hit: true }]);
  assert.deepEqual(markWords("nothing to find", []), [{ text: "nothing to find", hit: false }]);
  assert.deepEqual(markWords("", ["x"]), [{ text: "", hit: false }]);
});

test("days ago, in groups", () => {
  assert.equal(dayGroup(0), "today");
  assert.equal(dayGroup(-1), "today"); // a clock a little ahead
  assert.equal(dayGroup(1), "yesterday");
  assert.equal(dayGroup(6), "week");
  assert.equal(dayGroup(7), "earlier");
});

test("the line once a note is filed doesn't say Saved twice", () => {
  assert.equal(savedLine("Saved. I'll remind you Mon 4:00 PM."), "✓ Saved. I'll remind you Mon 4:00 PM.");
  assert.equal(savedLine("saved to ideas."), "✓ saved to ideas.");
  assert.equal(savedLine("Filed under Work."), "Saved ✓ Filed under Work.");
  assert.equal(savedLine(""), "Saved ✓");
  assert.equal(savedLine(null), "Saved ✓");
  assert.equal(savedLine("Savedish words"), "Saved ✓ Savedish words");
});
