// Talking to the Magpie server, plus an offline outbox so no note is lost.

let unauthorized = () => {};

export function onUnauthorized(handler) {
  unauthorized = handler;
}

export class ApiError extends Error {
  constructor(message, status = 0, offline = false) {
    super(message);
    this.status = status;
    this.offline = offline;
  }
}

export async function api(path, { method = "GET", body, form, signal } = {}) {
  const options = { method, headers: { "X-Magpie": "1" }, credentials: "same-origin", signal };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  } else if (form) {
    options.body = form;
  }
  let response;
  try {
    response = await fetch(path, options);
  } catch (error) {
    if (error.name === "AbortError") throw error;
    throw new ApiError("You're offline. Magpie will retry.", 0, true);
  }
  if (response.status === 401 && !path.startsWith("/api/login")) {
    unauthorized();
    throw new ApiError("Please log in again.", 401);
  }
  if (!response.ok) {
    let detail = "";
    try {
      detail = (await response.json()).detail;
    } catch {
      // not JSON
    }
    const message = typeof detail === "string" && detail ? detail : `Something went wrong (${response.status}).`;
    throw new ApiError(message, response.status);
  }
  if (response.status === 204) return null;
  const type = response.headers.get("content-type") || "";
  return type.includes("application/json") ? response.json() : response.text();
}

// ------------------------------------------------------------------ outbox

const OUTBOX = "magpie.outbox.v1";

export function outbox() {
  try {
    return JSON.parse(localStorage.getItem(OUTBOX) || "[]");
  } catch {
    return [];
  }
}

function saveOutbox(items) {
  try {
    localStorage.setItem(OUTBOX, JSON.stringify(items));
  } catch {
    // storage full or blocked: nothing more we can do
  }
}

export async function createNote(text, source = "web-text") {
  const body = { text, source };
  try {
    return await api("/api/notes", { method: "POST", body });
  } catch (error) {
    if (!error.offline) throw error;
    const items = outbox();
    items.push({ ...body, captured_at: new Date().toISOString() });
    saveOutbox(items);
    return { queued: true };
  }
}

// Several triggers (the "online" event, timers, other tabs) can ask for a flush at
// once; run one at a time so a queued note is never sent twice.
let flushing = null;

export function flushOutbox() {
  if (!flushing) {
    const run = () => sendQueued();
    flushing = (navigator.locks ? navigator.locks.request("magpie-outbox", run) : run()).finally(() => {
      flushing = null;
    });
  }
  return flushing;
}

async function sendQueued() {
  let sent = 0;
  for (;;) {
    // Re-read each time: a note may have been queued while we were sending.
    const item = outbox()[0];
    if (!item) break;
    try {
      await api("/api/notes", { method: "POST", body: item });
      sent += 1;
    } catch (error) {
      if (error.offline || error.status === 401 || error.status >= 500) break;
      // The server rejected it for good (e.g. validation); don't retry forever.
    }
    const remaining = outbox();
    const index = remaining.findIndex((x) => x.captured_at === item.captured_at && x.text === item.text);
    if (index !== -1) remaining.splice(index, 1);
    saveOutbox(remaining);
  }
  return sent;
}
