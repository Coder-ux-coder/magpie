// Reading a note's text: list items to tick off, and the words a search found.
// DOM-free, so it can be tested with node --test.

// "- Milk", "* Eggs", "• Bread", "1. Lemons", and ticked off: "- [x] Tea bags".
const ITEM = /^(\s*)([-*•]|\d{1,3}[.)])\s+(?:\[([ xX])\]\s*)?(.*)$/;

/** The lines of a note that are list items: {line, text, done}. */
export function listItems(text) {
  const items = [];
  String(text || "").split("\n").forEach((line, index) => {
    const match = ITEM.exec(line);
    if (match && match[4].trim()) items.push({ line: index, text: match[4].trim(), done: (match[3] || " ").trim() !== "" });
  });
  return items;
}

/** A note that is mostly a list: shown as items to tick off. */
export function isChecklist(text, kind = "") {
  const lines = String(text || "").split("\n").filter((line) => line.trim());
  const items = listItems(text);
  if (items.length < 2) return false;
  return kind === "list" || items.length >= Math.ceil(lines.length * 0.6);
}

/** The note with one item ticked off, or not: "- Milk" ⇄ "- [x] Milk". */
export function toggleItem(text, line, done) {
  const lines = String(text || "").split("\n");
  const match = ITEM.exec(lines[line] ?? "");
  if (!match) return String(text || "");
  const [, indent, bullet, , rest] = match;
  lines[line] = `${indent}${bullet} ${done ? "[x] " : ""}${rest.trim()}`;
  return lines.join("\n");
}

/** A line for a card: what's left on the list, and how much is done. */
export function listSummary(text) {
  const items = listItems(text);
  const left = items.filter((item) => !item.done).map((item) => item.text);
  const done = items.length - left.length;
  if (!items.length) return "";
  if (!left.length) return `All ${items.length} done`;
  return `${done ? `${done} of ${items.length} done · ` : ""}${left.join(" · ")}`;
}

// ------------------------------------------------------------------ search

/** The words of a search worth marking in the results. */
export function searchWords(query) {
  const words = String(query || "").toLowerCase().match(/[\p{L}\p{N}][\p{L}\p{N}'’-]*/gu) || [];
  return [...new Set(words.filter((word) => word.length > 1))].sort((a, b) => b.length - a.length);
}

/** The text in pieces, each one a search word or not: [{text, hit}]. */
export function markWords(text, words) {
  const value = String(text || "");
  if (!words?.length || !value) return [{ text: value, hit: false }];
  const escaped = words.map((word) => word.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const pattern = new RegExp(`(${escaped.join("|")})`, "giu");
  const pieces = [];
  let last = 0;
  for (const match of value.matchAll(pattern)) {
    if (match.index > last) pieces.push({ text: value.slice(last, match.index), hit: false });
    pieces.push({ text: match[0], hit: true });
    last = match.index + match[0].length;
  }
  if (last < value.length) pieces.push({ text: value.slice(last), hit: false });
  return pieces.length ? pieces : [{ text: value, hit: false }];
}

// ------------------------------------------------------------------ saved

/** The line once a note is filed: “Saved ✓ Filed under Work.”, but not “Saved ✓ Saved. …”
 *  when the AI's reply already says so. */
export function savedLine(reply) {
  const text = String(reply || "").trim();
  if (!text) return "Saved ✓";
  return /^saved\b/i.test(text) ? `✓ ${text}` : `Saved ✓ ${text}`;
}

// ------------------------------------------------------------------- days

/** Which group a note's day falls in, counted in the user's time zone: today, yesterday,
 *  week (the six days before) or earlier. `days` is how many days ago it was. */
export function dayGroup(days) {
  if (days <= 0) return "today";
  if (days === 1) return "yesterday";
  if (days < 7) return "week";
  return "earlier";
}
