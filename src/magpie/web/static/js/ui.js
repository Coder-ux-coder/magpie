// DOM helpers. Everything is built with createElement/textContent, never
// innerHTML, so note text can't inject markup.

const SVG_NS = "http://www.w3.org/2000/svg";

export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") el.className = value;
    else if (key === "dataset") Object.assign(el.dataset, value);
    else if (key.startsWith("on") && typeof value === "function") {
      el.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (key === "text") el.textContent = value;
    else if (value === true) el.setAttribute(key, "");
    else el.setAttribute(key, String(value));
  }
  append(el, children);
  return el;
}

export function append(el, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

export function icon(name, cls = "icon") {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("class", cls);
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS(SVG_NS, "use");
  use.setAttribute("href", `/static/icons/sprite.svg#${name}`);
  svg.append(use);
  return svg;
}

export function iconButton(name, label, onClick, attrs = {}) {
  return h(
    "button",
    { type: "button", class: "icon-btn", "aria-label": label, title: label, onclick: onClick, ...attrs },
    icon(name),
  );
}

export function clear(el) {
  while (el.firstChild) el.firstChild.remove();
  return el;
}

export function $(selector, root = document) {
  return root.querySelector(selector);
}

// ------------------------------------------------------------------ toasts

const toasts = () => document.getElementById("toasts");

export function toast(message, { action, onAction, error = false, timeout = 5000 } = {}) {
  const box = toasts();
  const el = h("div", { class: `toast${error ? " error" : ""}` }, h("p", {}, message));
  let timer;
  const dismiss = () => {
    clearTimeout(timer);
    el.remove();
  };
  if (action) {
    el.append(
      h("button", { type: "button", onclick: () => { dismiss(); onAction?.(); } }, action),
    );
  }
  el.append(h("button", { type: "button", "aria-label": "Dismiss", onclick: dismiss }, "✕"));
  // Keep at most three on screen; the oldest make way.
  while (box.children.length >= 3) box.firstElementChild.remove();
  box.append(el);
  timer = setTimeout(dismiss, timeout);
  if (error) announce(message);
  return dismiss;
}

export function announce(message) {
  const region = document.getElementById("alerts");
  region.textContent = "";
  setTimeout(() => { region.textContent = message; }, 50);
}

// ------------------------------------------------------------------- time

function partsIn(date, timeZone) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone, year: "numeric", month: "2-digit", day: "2-digit",
  }).formatToParts(date);
  const get = (type) => Number(parts.find((p) => p.type === type).value);
  return Date.UTC(get("year"), get("month") - 1, get("day"));
}

export function dayOffset(date, timeZone) {
  try {
    return Math.round((partsIn(date, timeZone) - partsIn(new Date(), timeZone)) / 86400000);
  } catch {
    return Math.round((date - new Date()) / 86400000);
  }
}

export function clock(date, timeZone) {
  return new Intl.DateTimeFormat(undefined, { timeZone, hour: "numeric", minute: "2-digit" }).format(date);
}

export function dayLabel(date, timeZone) {
  const offset = dayOffset(date, timeZone);
  if (offset === 0) return "Today";
  if (offset === 1) return "Tomorrow";
  if (offset === -1) return "Yesterday";
  const opts = { timeZone, weekday: "long", day: "numeric", month: "short" };
  if (Math.abs(offset) > 300) opts.year = "numeric";
  return new Intl.DateTimeFormat(undefined, opts).format(date);
}

export function when(date, timeZone) {
  const d = date instanceof Date ? date : new Date(date);
  const offset = dayOffset(d, timeZone);
  if (offset >= -1 && offset <= 1) return `${dayLabel(d, timeZone).toLowerCase()} ${clock(d, timeZone)}`;
  return `${dayLabel(d, timeZone)}, ${clock(d, timeZone)}`;
}

const relativeFormat = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });

export function ago(date) {
  const d = date instanceof Date ? date : new Date(date);
  const seconds = Math.round((d - new Date()) / 1000);
  const abs = Math.abs(seconds);
  if (abs < 45) return "just now";
  if (abs < 3600) return relativeFormat.format(Math.round(seconds / 60), "minute");
  if (abs < 86400) return relativeFormat.format(Math.round(seconds / 3600), "hour");
  if (abs < 86400 * 7) return relativeFormat.format(Math.round(seconds / 86400), "day");
  return new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short", year: abs > 86400 * 300 ? "numeric" : undefined }).format(d);
}

export function fullDate(date, timeZone) {
  return new Intl.DateTimeFormat(undefined, {
    timeZone, dateStyle: "full", timeStyle: "short",
  }).format(date instanceof Date ? date : new Date(date));
}

// "2026-10-19T17:00" in the user's zone, for <input type="datetime-local">
export function localInputValue(date, timeZone) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  }).formatToParts(date);
  const get = (type) => parts.find((p) => p.type === type).value;
  return `${get("year")}-${get("month")}-${get("day")}T${get("hour")}:${get("minute")}`;
}

export function debounce(fn, ms = 400) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

// ----------------------------------------------------------------- dialogs

export function confirmDialog({ title, message, confirm = "OK", danger = false }) {
  const dialog = document.getElementById("confirm-dialog");
  return new Promise((resolve) => {
    clear(dialog);
    const done = (value) => {
      dialog.close();
      resolve(value);
    };
    dialog.append(
      h("div", { class: "sheet-inner" },
        h("div", { class: "sheet-head" }, h("h2", { id: "confirm-title" }, title)),
        h("div", { class: "sheet-body" }, h("p", {}, message)),
        h("div", { class: "sheet-foot" },
          h("button", { type: "button", class: "btn", onclick: () => done(false) }, "Cancel"),
          h("button", { type: "button", class: `btn ${danger ? "danger" : "primary"}`, onclick: () => done(true) }, confirm),
        ),
      ),
    );
    dialog.addEventListener("cancel", () => resolve(false), { once: true });
    dialog.showModal();
    dialog.querySelector(".btn:last-child").focus();
  });
}

export async function copyText(text, label = "Copied") {
  try {
    await navigator.clipboard.writeText(text);
    toast(label);
  } catch {
    toast("Couldn't copy. Select the text and copy it manually.", { error: true });
  }
}
