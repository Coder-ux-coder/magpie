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
    const size = tokens(phrase).length || 1;
    for (let i = 0; i < toks.length; i++) {
      for (let len = Math.max(1, size - 1); len <= size + 1 && i + len <= toks.length; len++) {
        const joined = toks.slice(i, i + len).map((t) => compact(t.word)).join("");
        // Fuzzy matches may swap or drop one letter but not add one, so
        // "nodes on" wakes Magpie while "notes one" does not.
        const close =
          joined === target ||
          (fuzzy &&
            target.length >= 6 &&
            joined.length >= target.length - 1 &&
            joined.length <= target.length &&
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

// The first English language tag given (the chosen accent, then the browser's), else UK English.
export function englishLang(...tags) {
  for (const tag of tags) {
    const clean = String(tag ?? "").trim();
    if (/^en(-[a-z0-9]{1,8})*$/i.test(clean)) return clean;
  }
  return "en-GB";
}
