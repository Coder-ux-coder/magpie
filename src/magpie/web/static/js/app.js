// Magpie dashboard: boot, routing, keyboard shortcuts.

import { api, createNote, flushOutbox, onUnauthorized, outbox } from "./api.js";
import { noteActions } from "./actions.js";
import { setupCapture, startVoiceFromShortcut, stopVoice, toggleHandsFree, toggleMic } from "./capture.js";
import { closeNote, openNote } from "./note-dialog.js";
import { openSettings, refreshStatus } from "./settings.js";
import { applyLocal, deviceTimeZone, emit, local, on, saveLocal, state } from "./state.js";
import { clear, debounce, h, icon, iconButton, toast } from "./ui.js";
import { detachList, renderAsk, renderDigests, renderNotes, renderScheduled, visibleNotes } from "./views.js";

const $ = (id) => document.getElementById(id);

const VIEWS = [
  { hash: "inbox", label: "Inbox", icon: "inbox", count: "inbox", key: "1" },
  { hash: "scheduled", label: "Scheduled", icon: "clock", count: "scheduled", key: "2" },
  { hash: "reviews", label: "To watch & read", icon: "play", count: "reviews", key: "3" },
  { hash: "kind/task", label: "Tasks", icon: "task", count: "task", key: "4" },
  { hash: "kind/idea", label: "Ideas", icon: "idea", count: "idea", key: "5" },
  { hash: "kind/journal", label: "Journal", icon: "journal", count: "journal" },
  { hash: "kind/meeting", label: "Meetings", icon: "meeting", count: "meeting" },
  { hash: "kind/reference", label: "Reference", icon: "reference", count: "reference" },
  { hash: "kind/list", label: "Lists", icon: "list", count: "list" },
  { hash: "digests", label: "Digests", icon: "sun", key: "6" },
  { hash: "ask", label: "Ask", icon: "ask", key: "7" },
  { hash: "done", label: "Done", icon: "check" },
  { hash: "archived", label: "Archive", icon: "archive" },
];
const TITLES = {
  inbox: "Inbox",
  scheduled: "Scheduled reminders",
  reviews: "Videos and articles to watch and read",
  digests: "Digests and weekly reviews",
  ask: "Ask your notes",
  done: "Done",
  archived: "Archive",
};

let counts = {};
let currentView = "inbox";

// ------------------------------------------------------------------- boot

async function boot() {
  applyLocal();
  registerServiceWorker();
  onUnauthorized(showLogin);
  let session;
  try {
    session = await api("/api/session");
  } catch {
    document.body.append(h("p", { class: "empty", style: "margin:2rem" }, "Can't reach your Magpie server. Check your connection and reload."));
    return;
  }
  if (location.pathname === "/share") return handleShare(session.authenticated);
  if (!session.authenticated) return showLogin();
  return startApp();
}

function registerServiceWorker() {
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  }
}

// ------------------------------------------------------------------ login

function showLogin() {
  $("app").hidden = true;
  $("share").hidden = true;
  $("login").hidden = false;
  const form = $("login-form");
  const input = $("login-password");
  input.focus();
  form.onsubmit = async (event) => {
    event.preventDefault();
    $("login-error").textContent = "";
    try {
      await api("/api/login", { method: "POST", body: { password: input.value } });
      input.value = "";
      $("login").hidden = true;
      const pending = sessionStorage.getItem("magpie.pendingShare");
      if (pending) {
        sessionStorage.removeItem("magpie.pendingShare");
        return finishShare(JSON.parse(pending));
      }
      startApp();
    } catch (error) {
      $("login-error").textContent = error.message;
      input.select();
    }
  };
}

// ------------------------------------------------------------------ share

function shareText(params) {
  const text = (params.get("text") || "").trim();
  const url = (params.get("url") || "").trim();
  const title = (params.get("title") || "").trim();
  const parts = [];
  if (text) parts.push(text);
  if (url && !text.includes(url)) parts.push(url);
  if (!parts.length && title) parts.push(title);
  if (url && title && !text) parts.unshift(title);
  return { text: parts.join("\n"), source: params.get("source") === "bookmarklet" ? "bookmarklet" : "share" };
}

async function handleShare(authenticated) {
  const shared = shareText(new URLSearchParams(location.search));
  history.replaceState(null, "", "/");
  if (!shared.text) return authenticated ? startApp() : showLogin();
  if (!authenticated) {
    sessionStorage.setItem("magpie.pendingShare", JSON.stringify(shared));
    return showLogin();
  }
  return finishShare(shared);
}

// Shared content is only saved after you confirm, so another website can't
// slip notes into your Magpie by linking to /share.
function finishShare(shared) {
  $("share").hidden = false;
  const input = $("share-input");
  const status = $("share-text");
  input.value = shared.text;
  input.focus();
  input.setSelectionRange(0, 0);
  $("share-cancel").addEventListener("click", (event) => {
    if (window.opener) {
      event.preventDefault();
      window.close();
    }
  });
  $("share-form").onsubmit = async (event) => {
    event.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    $("share-save").disabled = true;
    status.textContent = "Saving…";
    try {
      const result = await createNote(text, shared.source);
      $("share-title").textContent = result.queued ? "Saved on this device" : "Saved";
      status.textContent = result.queued
        ? "You're offline. It will sync when you're back online."
        : "Claude is filing it and will review any link.";
      input.hidden = true;
      $("share-save").hidden = true;
      $("share-cancel").textContent = "Open Magpie";
      if (window.opener) setTimeout(() => window.close(), 1500);
    } catch (error) {
      status.textContent = error.message;
      $("share-save").disabled = false;
    }
  };
}

// -------------------------------------------------------------------- app

async function startApp() {
  $("login").hidden = true;
  $("share").hidden = true;
  $("app").hidden = false;
  try {
    await refreshStatus();
  } catch (error) {
    toast(error.message, { error: true });
    return;
  }
  $("version").textContent = state.status.version;
  setupCapture();
  setupSearch();
  setupKeyboard();
  $("btn-settings").addEventListener("click", () => openSettings());
  $("btn-shortcuts").addEventListener("click", showShortcuts);
  $("footer-shortcuts").addEventListener("click", showShortcuts);
  on("counts", (next) => {
    counts = next || {};
    renderTabs();
  });
  on("status", () => renderBanners());
  on("prefs", () => renderBanners());
  on("refresh", () => renderView());
  on("shortcuts", showShortcuts);
  on("settings-closed", () => renderView());
  window.addEventListener("hashchange", route);
  window.addEventListener("online", syncOutbox);
  document.addEventListener("visibilitychange", () => !document.hidden && quietRefresh());
  setInterval(quietRefresh, 30000);
  counts = state.status.counts;
  renderBanners();
  route();
  syncOutbox();
  const params = new URLSearchParams(location.search);
  if (params.get("voice") === "1") {
    history.replaceState(null, "", `/${location.hash}`);
    startVoiceFromShortcut();
  }
}

async function syncOutbox() {
  if (!outbox().length) return;
  const sent = await flushOutbox();
  if (sent) {
    toast(`Synced ${sent} note${sent > 1 ? "s" : ""} saved while offline.`);
    renderView();
  }
  renderBanners();
}

// ---------------------------------------------------------------- routing

function parseHash() {
  const hash = location.hash.replace(/^#\/?/, "");
  const [first, second] = hash.split("/");
  if (first === "note" && second) return { view: currentView, note: Number(second) };
  if (first === "settings") return { view: currentView, settings: true };
  if (first === "kind" && second) return { view: `kind/${second}` };
  if (TITLES[first]) return { view: first };
  return { view: "inbox" };
}

function route() {
  const parsed = parseHash();
  if (parsed.note) {
    if (!$("app").hidden && !document.querySelector("#view .list, #view .empty")) renderView();
    openNote(parsed.note, {
      closed: () => {
        if (location.hash.startsWith("#/note/")) history.replaceState(null, "", `#/${currentView}`);
      },
    });
    return;
  }
  if (parsed.settings) {
    openSettings();
    return;
  }
  closeNote();
  if (parsed.view !== currentView || !document.querySelector("#view > *")) {
    currentView = parsed.view;
    state.q = "";
    $("search").value = "";
  }
  renderView();
}

function viewInfo(view) {
  return VIEWS.find((v) => v.hash === view) || VIEWS[0];
}

function renderTabs() {
  const list = $("view-tabs");
  clear(list);
  for (const view of VIEWS) {
    const active = !state.q && view.hash === currentView;
    const count = view.count ? counts[view.count] : null;
    if (view.hash.startsWith("kind/") && !count && !active) continue;
    list.append(
      h("li", {},
        h("a", { class: "tab", href: `#/${view.hash}`, "aria-current": active ? "page" : null },
          icon(view.icon),
          view.label,
          count ? h("span", { class: "count" }, h("span", { class: "sr-only" }, ", "), String(count)) : null,
        ),
      ),
    );
  }
  list.querySelector('[aria-current="page"]')?.scrollIntoView({ block: "nearest", inline: "nearest" });
}

let renderToken = 0;
async function renderView() {
  const token = ++renderToken;
  const container = $("view");
  const title = $("view-title");
  renderTabs();
  try {
    if (state.q) {
      title.textContent = `Search: “${state.q}”`;
      await renderNotes(container, { view: "all" }, state.q);
      return;
    }
    const view = currentView;
    if (view === "scheduled") {
      title.textContent = TITLES.scheduled;
      detachList();
      await renderScheduled(container);
      api("/api/notes?limit=1").then((d) => emit("counts", d.counts)).catch(() => {});
    } else if (view === "digests") {
      title.textContent = TITLES.digests;
      detachList();
      await renderDigests(container);
    } else if (view === "ask") {
      title.textContent = TITLES.ask;
      detachList();
      renderAsk(container);
    } else if (view.startsWith("kind/")) {
      const kind = view.split("/")[1];
      title.textContent = viewInfo(view).label;
      await renderNotes(container, { view: "kind", kind }, "");
    } else {
      title.textContent = TITLES[view] || "Inbox";
      await renderNotes(container, { view }, "");
    }
  } catch (error) {
    if (token !== renderToken) return;
    if (error.status !== 401) {
      clear(container).append(h("div", { class: "empty" }, h("p", {}, error.message), h("button", { type: "button", class: "btn", onclick: renderView }, "Try again")));
    }
  }
}

function quietRefresh() {
  if (document.hidden || $("app").hidden) return;
  if (document.querySelector("dialog[open]")) return;
  if (currentView === "ask" || document.activeElement?.closest?.("#view form")) return;
  renderView();
  syncOutbox();
}

// ----------------------------------------------------------------- search

function setupSearch() {
  const input = $("search");
  const run = debounce(() => {
    state.q = input.value.trim();
    renderView();
  }, 300);
  input.addEventListener("input", run);
  $("search-form").addEventListener("submit", (event) => {
    event.preventDefault();
    state.q = input.value.trim();
    renderView();
  });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      input.value = "";
      state.q = "";
      renderView();
      input.blur();
    }
  });
}

// ---------------------------------------------------------------- banners

function dismissible(id, kind, text, action) {
  if (local.dismissed?.[id]) return null;
  return h("div", { class: `banner ${kind}` },
    icon(kind === "info" ? "sparkles" : "alert"),
    h("p", {}, text),
    action,
    iconButton("close", "Dismiss", () => {
      saveLocal({ dismissed: { ...local.dismissed, [id]: true } });
      renderBanners();
    }),
  );
}

function renderBanners() {
  const box = $("banners");
  clear(box);
  // DOM append() would turn null into the text "null"; skip dismissed banners.
  const add = (banner) => banner && box.append(banner);
  const status = state.status;
  if (!status) return;
  if (!status.brain.available) {
    box.append(h("div", { class: "banner" }, icon("alert"),
      h("p", {}, "Claude isn't connected yet. Notes are saved and will be filed once ANTHROPIC_API_KEY is set on the server.")));
  }
  const device = deviceTimeZone();
  if (state.prefs && state.prefs.timezone !== device) {
    add(dismissible(`tz-${device}`, "info", `Your device is in ${device.replaceAll("_", " ")}, but reminders use ${state.prefs.timezone}.`,
      h("button", { type: "button", class: "btn small primary", onclick: async () => {
        state.prefs = await api("/api/prefs", { method: "PUT", body: { timezone: device } });
        emit("prefs", state.prefs);
        toast(`Reminders now use ${device}.`);
      } }, `Use ${device.split("/").pop().replaceAll("_", " ")}`)));
  }
  const configured = status.channels.filter((c) => c.configured && c.enabled);
  if (!configured.length) {
    add(dismissible("no-channels", "info", "Reminders only appear here until you connect Telegram, browser push or another channel.",
      h("button", { type: "button", class: "btn small", onclick: () => openSettings("notify") }, "Set up notifications")));
  }
  const queued = outbox().length;
  if (queued) {
    box.append(h("div", { class: "banner info" }, icon("clock"),
      h("p", {}, `${queued} note${queued > 1 ? "s are" : " is"} waiting to sync.`),
      h("button", { type: "button", class: "btn small", onclick: syncOutbox }, "Sync now")));
  }
}

// --------------------------------------------------------------- keyboard

const SHORTCUTS = [
  ["m", "Start or stop a voice note"],
  ["Alt + N", "Start or stop a voice note, even while typing"],
  ["h", "Turn hands-free listening on or off"],
  ["n", "Type a new note"],
  ["/", "Search"],
  ["1 – 7", "Inbox, Scheduled, To watch & read, Tasks, Ideas, Digests, Ask"],
  ["j / k", "Next / previous note"],
  ["Enter", "Open the focused note"],
  ["x", "Mark the focused note done (or not done)"],
  ["p", "Pin or unpin the focused note"],
  ["e", "Archive the focused note"],
  ["Delete", "Delete the focused note (with undo)"],
  [",", "Settings"],
  ["?", "This list"],
  ["Esc", "Close a dialog, or discard the note being recorded"],
];

function showShortcuts() {
  const dialog = $("shortcuts-dialog");
  clear(dialog);
  dialog.append(
    h("div", { class: "sheet-inner" },
      h("div", { class: "sheet-head" }, h("h2", { id: "shortcuts-title" }, "Keyboard shortcuts"), iconButton("close", "Close", () => dialog.close())),
      h("div", { class: "sheet-body" },
        h("table", { class: "shortcuts-table" },
          h("tbody", {}, SHORTCUTS.map(([keys, what]) => h("tr", {}, h("th", { scope: "row" }, h("kbd", {}, keys)), h("td", {}, what)))),
        ),
      ),
    ),
  );
  dialog.showModal();
}

function focusedNote() {
  const card = document.activeElement?.closest?.(".note-card");
  if (!card) return null;
  return visibleNotes().find((note) => note.id === Number(card.dataset.id)) || null;
}

function moveFocus(step) {
  const links = [...document.querySelectorAll("#view .note-title a")];
  if (!links.length) return;
  const index = links.findIndex((link) => link.closest(".note-card")?.contains(document.activeElement));
  const next = links[Math.max(0, Math.min(links.length - 1, index === -1 ? 0 : index + step))];
  next.focus();
  next.closest(".note-card").scrollIntoView({ block: "nearest" });
}

function setupKeyboard() {
  document.addEventListener("keydown", (event) => {
    const typing = event.target.closest?.("input, textarea, select, [contenteditable]");
    if (event.altKey && event.code === "KeyN") {
      event.preventDefault();
      toggleMic();
      return;
    }
    if (event.key === "Escape" && !document.querySelector("dialog[open]")) {
      stopVoice();
      return;
    }
    if (typing || event.ctrlKey || event.metaKey || event.altKey) return;
    if (document.querySelector("dialog[open]")) return;
    const view = VIEWS.find((v) => v.key === event.key);
    const note = focusedNote();
    const actions = {
      m: () => toggleMic(),
      h: () => toggleHandsFree(),
      n: () => $("compose-text").focus(),
      "/": () => $("search").focus(),
      ",": () => openSettings(),
      "?": () => showShortcuts(),
      j: () => moveFocus(1),
      k: () => moveFocus(-1),
      x: () => note && noteActions.toggleDone(note),
      p: () => note && noteActions.togglePin(note),
      e: () => note && noteActions.toggleArchive(note),
      Delete: () => note && noteActions.remove(note),
    };
    if (view) {
      event.preventDefault();
      location.hash = `#/${view.hash}`;
    } else if (actions[event.key]) {
      event.preventDefault();
      actions[event.key]();
    }
  });
}

boot();
