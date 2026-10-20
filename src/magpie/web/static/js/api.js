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

// A request that hangs counts as offline, so a note is queued instead of lost.
const TIMEOUT = 30000;

export async function api(path, { method = "GET", body, form, signal, timeout } = {}) {
  const controller = new AbortController();
  const cancel = () => controller.abort();
  signal?.addEventListener("abort", cancel, { once: true });
  const timer = setTimeout(cancel, timeout || (form ? 120000 : TIMEOUT));
  const options = { method, headers: { "X-Magpie": "1" }, credentials: "same-origin", signal: controller.signal };
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
    if (signal?.aborted) throw error; // the caller cancelled it
    const slow = controller.signal.aborted;
    throw new ApiError(slow ? "Magpie is taking too long to answer." : "You're offline.", 0, true);
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", cancel);
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
    const first = Array.isArray(detail) ? detail[0]?.msg : detail;
    const message = typeof first === "string" && first ? first : `Something went wrong (${response.status}).`;
    throw new ApiError(message, response.status);
  }
  if (response.status === 204) return null;
  const type = response.headers.get("content-type") || "";
  if (type.includes("application/json")) return response.json();
  // Every /api/ answer is JSON; anything else (a proxy's error page, a captive portal) isn't Magpie.
  if (path.startsWith("/api/")) throw new ApiError("The server sent an unexpected answer. Check that Magpie is running.", response.status);
  return response.text();
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
    return true;
  } catch {
    return false; // storage full or blocked
  }
}

// Worth trying again later: offline, logged out, overloaded or a server error.
const retryable = (error) => error.offline || [401, 408, 429].includes(error.status) || error.status >= 500;

// Sent with each note, so a retry after a lost answer can't save it twice.
function clientId() {
  if (crypto.randomUUID) return crypto.randomUUID(); // secure contexts only
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

const itemKey = (item) => item.client_id || `${item.captured_at}|${item.text}`;

export async function createNote(text, source = "web-text") {
  const body = { text, source, client_id: clientId() };
  try {
    return await api("/api/notes", { method: "POST", body });
  } catch (error) {
    if (!retryable(error)) throw error;
    const items = outbox();
    items.push({ ...body, captured_at: new Date().toISOString() });
    if (!saveOutbox(items)) {
      throw new ApiError("Couldn't keep the note on this device either. Copy it somewhere safe and try again.", error.status, error.offline);
    }
    return { queued: true, offline: error.offline };
  }
}

// Several triggers (the "online" event, timers, other tabs) can ask for a flush at
// once; run one at a time. (Without Web Locks two tabs may both send a note; the
// client id makes the server keep only one.)
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

/** Send queued notes. Returns how many were sent and which the server refused for good. */
async function sendQueued() {
  let sent = 0;
  const rejected = [];
  const tried = new Set();
  for (;;) {
    // Re-read each time: a note may have been queued while we were sending.
    const item = outbox().find((x) => !tried.has(itemKey(x)));
    if (!item) break;
    tried.add(itemKey(item));
    try {
      await api("/api/notes", { method: "POST", body: item });
      sent += 1;
    } catch (error) {
      if (error.offline || error.status === 401) break; // nothing else gets through now
      if (retryable(error)) continue; // keep it for next time, but don't hold up the rest
      rejected.push({ text: item.text, reason: error.message });
    }
    const remaining = outbox();
    const index = remaining.findIndex((x) => itemKey(x) === itemKey(item));
    if (index !== -1) remaining.splice(index, 1);
    saveOutbox(remaining);
  }
  return { sent, rejected };
}
