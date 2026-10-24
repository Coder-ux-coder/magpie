// Spotting "notes on" / "notes off" in live speech-recognition text.
//
// Recognisers mishear short phrases ("nodes on", "note son", "Notes, on."),
// so matching ignores case, punctuation and word boundaries, and the wake
// phrase also tolerates one wrong letter. Stop phrases must match exactly:
// a false "stop" would cut a note short. Magpie listens for English only,
// whatever language the browser is set to (englishLang).
// Pure functions, no DOM: tested with `node --test tests/js`.

export const DEFAULT_WAKE = "notes on";
export const DEFAULT_STOP = "notes off";
export const WAKE_ALIASES = ["note on", "nodes on", "node on", "notes own", "knots on", "nots on"];
export const STOP_ALIASES = ["note off", "nodes off", "node off", "end note", "save note"];
export const CANCEL_PHRASES = ["cancel note", "discard note", "delete this note"];

const WORD = /[\p{L}\p{N}]+(?:['’][\p{L}]+)?/gu;

export function compact(text) {
  return String(text).toLowerCase().normalize("NFKC").replace(/[^\p{L}\p{N}]/gu, "");
}

export function tokens(text) {
  const out = [];
  for (const match of String(text).matchAll(WORD)) {
    out.push({ word: match[0], start: match.index, end: match.index + match[0].length });
  }
  return out;
}

export function levenshtein(a, b) {
  if (a === b) return 0;
  if (!a.length) return b.length;
  if (!b.length) return a.length;
  let previous = Array.from({ length: b.length + 1 }, (_, i) => i);
  for (let i = 1; i <= a.length; i++) {
    const current = [i];
    for (let j = 1; j <= b.length; j++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      current[j] = Math.min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost);
    }
    previous = current;
  }
  return previous[b.length];
}

export function phraseList(custom, defaults, aliases) {
  const list = [];
  const add = (phrase) => {
    const clean = String(phrase || "").trim().toLowerCase();
    if (clean && !list.includes(clean)) list.push(clean);
  };
  add(custom || defaults);
  if (!custom || compact(custom) === compact(defaults)) aliases.forEach(add);
  return list;
}

// Find the earliest occurrence of any phrase. Returns {start, end, phrase} or null.
export function findPhrase(text, phrases, { fuzzy = false } = {}) {
  const toks = tokens(text);
  let best = null;
  for (const phrase of phrases) {
    const target = compact(phrase);
    if (!target) continue;
    const words = tokens(phrase);
    const size = words.length || 1;
    const lastWord = compact(words.at(-1)?.word || "");
    for (let i = 0; i < toks.length; i++) {
      for (let len = Math.max(1, size - 1); len <= size + 1 && i + len <= toks.length; len++) {
        const joined = toks.slice(i, i + len).map((t) => compact(t.word)).join("");
        // Fuzzy matches may swap or drop one letter but not add one, and the last
        // word must be right: "nodes on" wakes Magpie, while "notes one" and the
        // everyday "notes of" or "notes in" do not.
        const close =
          joined === target ||
          (fuzzy &&
            target.length >= 6 &&
            joined.length >= target.length - 1 &&
            joined.length <= target.length &&
            (size < 2 || compact(toks[i + len - 1].word).endsWith(lastWord)) &&
            levenshtein(joined, target) <= 1);
        if (close && (!best || toks[i].start < best.start)) {
          best = { start: toks[i].start, end: toks[i + len - 1].end, phrase };
        }
      }
    }
  }
  return best;
}

// "ok notes on buy milk" -> {found: true, rest: "buy milk"}
export function afterWake(text, phrases) {
  const hit = findPhrase(text, phrases, { fuzzy: true });
  if (!hit) return { found: false, rest: "" };
  return { found: true, rest: tidy(text.slice(hit.end)) };
}

// "buy milk notes off" -> {found: true, text: "buy milk"}
export function beforeStop(text, phrases) {
  const hit = findPhrase(text, phrases);
  if (!hit) return { found: false, text: tidy(text) };
  return { found: true, text: tidy(text.slice(0, hit.start)) };
}

export function isCancel(text, phrases = CANCEL_PHRASES) {
  return findPhrase(text, phrases) !== null;
}

export function tidy(text) {
  return String(text)
    .replace(/^[\s,.;:!?-]+/u, "")
    .replace(/[\s,;:-]+$/u, "")
    .replace(/\s+/g, " ")
    .trim();
}

// ------------------------------------------------- a note in live results

// Speech recognition reports a session as a growing list of results, each one
// final or still changing (interim): [{text, final}]. Reading a note straight
// from that list lets an interim "notes on" start a note at once and an interim
// "notes off" end it, without waiting for the recogniser to settle.

// Where a wake phrase starts among results[from..], or -1. It may be split over
// two results ("notes" | "on buy milk").
export function findWake(results, from, phrases) {
  for (let i = Math.max(0, from); i < results.length; i++) {
    if (afterWake(results[i].text, phrases).found) return i;
    if (i > from && afterWake(`${results[i - 1].text} ${results[i].text}`, phrases).found) return i - 1;
  }
  return -1;
}

// The note in results[from..]: what is settled (final), what is still changing
// (interim), its text (without a stop phrase), and whether a stop or cancel
// phrase was said (settled: in final results, so act now). With fromWake the
// text starts after the wake phrase; otherwise a leading wake phrase is dropped
// too ("notes on, buy milk" after pressing the mic).
export function noteFromResults(results, { from = 0, fromWake = false, wake = [], stop = [], cancel = CANCEL_PHRASES } = {}) {
  const slice = results.slice(Math.max(0, from));
  const strip = (raw) => {
    const text = tidy(raw);
    const hit = findPhrase(text, wake, { fuzzy: true });
    if (fromWake) return hit ? tidy(text.slice(hit.end)) : null;
    return hit && hit.start === 0 ? tidy(text.slice(hit.end)) : text;
  };
  const allRaw = slice.map((r) => r.text).join(" ");
  const finalRaw = slice.filter((r) => r.final).map((r) => r.text).join(" ");
  // A wake phrase the recogniser later heard differently: keep every word.
  const all = strip(allRaw) ?? tidy(allRaw);
  let final = strip(finalRaw) ?? "";
  if (!all.startsWith(final)) final = "";
  const interim = tidy(all.slice(final.length));
  const stopped = beforeStop(all, stop);
  return {
    final: stopped.found ? stopped.text : final,
    interim: stopped.found ? "" : interim,
    text: stopped.found ? stopped.text : all,
    stopped: stopped.found,
    settled: beforeStop(final, stop).found,
    cancelled: isCancel(all, cancel),
    cancelSettled: isCancel(final, cancel),
  };
}

// ------------------------------------------------- a dictation app's words

// What a dictation app of the user's own (Glaido, Wispr Flow, voice typing…) has typed
// into the bar: {state: "stop" | "cancel" | "more", text}. The stop or cancel phrase only
// counts at the very end ("take notes off the shelf" goes on), and the text is what came
// before it: "Buy milk, notes off." -> {state: "stop", text: "Buy milk"}.
export function dictationEnd(text, stops, cancels = CANCEL_PHRASES) {
  for (const [state, phrases] of [["cancel", cancels], ["stop", stops]]) {
    const at = endsWith(text, phrases);
    if (at !== null) return { state, text: tidy(String(text).slice(0, at)) };
  }
  return { state: "more", text: tidy(text) };
}

// Where a phrase that ends the text starts (punctuation after it aside), or null.
function endsWith(text, phrases) {
  const toks = tokens(text);
  for (const phrase of phrases) {
    const words = tokens(phrase).map((t) => compact(t.word));
    if (!words.length || words.length > toks.length) continue;
    const tail = toks.slice(-words.length);
    if (tail.every((t, i) => compact(t.word) === words[i])) return tail[0].start;
  }
  return null;
}

// Add words to what is already in the text box, keeping its line breaks.
export function appendText(base, addition) {
  const before = String(base ?? "");
  const add = String(addition ?? "").trim();
  if (!add) return before;
  const start = before.replace(/\s+$/u, "");
  if (!start) return add;
  return /\n\s*$/u.test(before) ? `${start}\n${add}` : `${start} ${add}`;
}

// The first English language tag given (the chosen accent, then the browser's), else UK English.
export function englishLang(...tags) {
  for (const tag of tags) {
    const clean = String(tag ?? "").trim();
    if (/^en(-[a-z0-9]{1,8})*$/i.test(clean)) return clean;
  }
  return "en-GB";
}
