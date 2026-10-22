// Building blocks shared by the lists and the note dialog.

import { ago, append, h, icon, iconButton, when } from "./ui.js";
import { aiName, categoryOf, tz } from "./state.js";

export const KINDS = {
  task: { label: "Task", icon: "task" },
  idea: { label: "Idea", icon: "idea" },
  link: { label: "Watch / read", icon: "play" },
  journal: { label: "Journal", icon: "journal" },
  meeting: { label: "Meeting", icon: "meeting" },
  reference: { label: "Reference", icon: "reference" },
  list: { label: "List", icon: "list" },
  note: { label: "Note", icon: "note" },
};

export const VERDICTS = { must: "Must see", worth_it: "Worth it", skim: "Skim it", skip: "Skip it" };

export const SOURCES = {
  "web-voice": "Voice",
  "web-text": "Typed",
  "web-audio": "Recording",
  share: "Shared",
  bookmarklet: "Bookmarklet",
  telegram: "Telegram",
  "telegram-voice": "Telegram voice",
  siri: "Siri",
  shortcut: "Shortcut",
  api: "API",
  cli: "Command line",
  mcp: "Claude (MCP)",
  import: "Imported",
};

export function kindBadge(kind) {
  const info = KINDS[kind] || KINDS.note;
  return h("span", { class: "kind", dataset: { kind } }, icon(info.icon), info.label);
}

export function noteTitle(note) {
  if (note.title) return note.title;
  const words = (note.text || note.raw_text || "").split(/\s+/).filter(Boolean);
  return words.slice(0, 8).join(" ") + (words.length > 8 ? "…" : "") || "Untitled note";
}

export function isWorking(note) {
  return (
    note.state === "processing" ||
    (note.state === "pending" && !note.error) ||
    note.links?.some((link) => link.state === "pending" || link.state === "processing")
  );
}

export function nextReminder(note) {
  return (note.reminders || [])
    .filter((r) => r.status === "scheduled")
    .sort((a, b) => new Date(a.due_at) - new Date(b.due_at))[0];
}

export function stars(rating) {
  const wrap = h("span", { class: "stars", role: "img", "aria-label": `${rating} out of 5` });
  for (let i = 1; i <= 5; i++) {
    const star = icon("star");
    if (i <= rating) star.classList.add("on");
    wrap.append(star);
  }
  return wrap;
}

export function noteChips(note) {
  const chips = [];
  const category = categoryOf(note.category_id);
  if (category) chips.push(h("span", { class: `chip category swatch-${category.color}` }, category.name));
  if (note.pinned) chips.push(h("span", { class: "chip pinned" }, icon("pin"), "Pinned"));
  if (note.state === "processing" || (note.state === "pending" && !note.error)) {
    chips.push(h("span", { class: "chip pending" }, h("span", { class: "spinner", "aria-hidden": "true" }), `${aiName()} is filing this…`));
  } else if (note.state === "pending" && note.error) {
    chips.push(h("span", { class: "chip pending", title: note.error }, icon("clock"), `Waiting for ${aiName()}`));
  } else if (note.state === "failed") {
    chips.push(h("span", { class: "chip failed", title: note.error || "" }, icon("alert"), "Not processed"));
  }
  const next = nextReminder(note);
  if (next) {
    chips.push(
      h("span", { class: "chip reminder" }, icon(next.rrule ? "refresh" : "clock"), when(next.due_at, tz())),
    );
  }
  const waiting = (note.reminders || []).filter((r) => r.status === "sent");
  if (waiting.length) chips.push(h("span", { class: "chip sent" }, icon("bell"), "Reminder sent"));
  for (const link of note.links || []) {
    if (link.review) {
      chips.push(
        h("span", { class: `chip verdict-${link.review.verdict}` },
          icon(link.kind === "youtube" ? "play" : "doc"),
          `${VERDICTS[link.review.verdict] || link.review.verdict} · ${link.review.rating}/5`),
      );
    } else if (link.state === "pending" || link.state === "processing") {
      chips.push(h("span", { class: "chip pending" }, h("span", { class: "spinner", "aria-hidden": "true" }), "Reviewing link…"));
    } else if (link.state === "failed") {
      chips.push(h("span", { class: "chip failed", title: link.error || "" }, icon("link"), "Couldn't review link"));
    }
  }
  for (const tag of note.tags || []) chips.push(h("span", { class: "chip" }, `#${tag}`));
  return chips;
}

export function noteCard(note, actions) {
  const title = noteTitle(note);
  const snippetText = note.summary || note.text || note.raw_text || "";
  const titleId = `note-${note.id}-title`;
  const done = note.status === "done";
  const archived = note.status === "archived";
  const category = categoryOf(note.category_id);
  const card = h(
    "article",
    {
      class: `card note-card${category ? ` in-category swatch-${category.color}` : ""}`,
      dataset: { id: note.id, kind: note.kind, status: note.status },
      "aria-labelledby": titleId,
      draggable: "true",
    },
    h("div", { class: "head" },
      kindBadge(note.kind),
      h("span", { class: "note-time" }, h("time", { datetime: note.created_at, title: new Date(note.created_at).toLocaleString() }, ago(note.created_at))),
    ),
    h("h3", { class: "note-title", id: titleId, dir: "auto" }, h("a", { href: `#/note/${note.id}` }, title)),
    snippetText && snippetText !== title ? h("p", { class: "snippet", dir: "auto" }, snippetText) : null,
    h("div", { class: "chips" }, noteChips(note)),
    h("div", { class: "card-actions" },
      iconButton(done ? "undo" : "check", done ? "Mark as not done" : "Mark as done", () => actions.toggleDone(note), { "aria-pressed": String(done) }),
      iconButton("pin", note.pinned ? "Unpin" : "Pin to top", () => actions.togglePin(note), { "aria-pressed": String(note.pinned) }),
      iconButton("archive", archived ? "Move back to inbox" : "Archive", () => actions.toggleArchive(note)),
      iconButton("trash", "Delete", () => actions.remove(note)),
    ),
  );
  // Drag a card onto a category tab to move it there (the note dialog has the same choice).
  card.addEventListener("dragstart", (event) => {
    event.dataTransfer.setData("application/x-magpie-note", String(note.id));
    event.dataTransfer.effectAllowed = "move";
    card.classList.add("dragging");
    document.body.classList.add("dragging-note");
  });
  card.addEventListener("dragend", () => {
    card.classList.remove("dragging");
    document.body.classList.remove("dragging-note");
  });
  return h("li", {}, card);
}

export function youtubeId(url) {
  try {
    const parsed = new URL(url);
    if (parsed.hostname.endsWith("youtu.be")) return parsed.pathname.slice(1, 12);
    if (parsed.searchParams.get("v")) return parsed.searchParams.get("v");
    const parts = parsed.pathname.split("/").filter(Boolean);
    if (["shorts", "embed", "live"].includes(parts[0])) return parts[1];
  } catch {
    // not a URL
  }
  return null;
}

export function reviewBlock(link, { onRetry, onSpeak } = {}) {
  const review = link.review;
  const video = link.kind === "youtube" ? youtubeId(link.url) : null;
  const block = h("div", { class: "review" });
  if (video) {
    block.append(h("img", { class: "thumb", src: `https://i.ytimg.com/vi/${video}/hqdefault.jpg`, alt: "", loading: "lazy" }));
  }
  const title = review?.title || link.title || link.url;
  block.append(
    h("h4", { dir: "auto" },
      h("a", { href: link.url, target: "_blank", rel: "noopener noreferrer" }, title, " ", icon("external")),
    ),
  );
  const facts = [link.site, link.author].filter(Boolean);
  const minutes = link.minutes || review?.minutes;
  if (minutes) facts.push(`${minutes} min`);
  if (link.found_by_search) facts.push("found by web search");
  if (facts.length) block.append(h("p", { class: "muted small" }, facts.join(" · ")));

  if (!review) {
    if (link.state === "failed") {
      append(block, [
        h("p", { class: "error" }, link.error || "Couldn't review this link."),
        onRetry ? h("button", { type: "button", class: "btn small", onclick: onRetry }, icon("refresh"), "Try again") : null,
      ]);
    } else {
      block.append(h("p", { class: "muted" }, h("span", { class: "spinner", "aria-hidden": "true" }), ` ${aiName()} is reviewing this…`));
    }
    return block;
  }
  block.append(
    h("div", { class: "row" },
      h("span", { class: `chip verdict-${review.verdict}` }, VERDICTS[review.verdict] || review.verdict),
      stars(review.rating),
      onSpeak ? h("button", { type: "button", class: "btn small", onclick: () => onSpeak(review) }, icon("speaker"), "Read aloud") : null,
    ),
    h("p", { dir: "auto" }, h("strong", {}, "TL;DR "), review.tldr),
  );
  if (review.key_points?.length) {
    block.append(h("ul", { dir: "auto" }, review.key_points.map((point) => h("li", {}, point))));
  }
  if (review.best_for) block.append(h("p", { dir: "auto" }, h("strong", {}, "Best for: "), review.best_for));
  if (review.why_saved) block.append(h("p", { dir: "auto" }, h("strong", {}, "Why you saved it: "), review.why_saved));
  if (review.caveats) block.append(h("p", { class: "muted", dir: "auto" }, h("strong", {}, "Caveats: "), review.caveats));
  return block;
}

export function reviewSpeech(review) {
  return [
    review.title,
    `${VERDICTS[review.verdict] || review.verdict}, ${review.rating} out of 5.`,
    review.tldr,
    ...(review.key_points || []),
    review.best_for ? `Best for: ${review.best_for}` : "",
  ].join(". ");
}
