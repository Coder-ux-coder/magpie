// Magpie dashboard: boot, routing, keyboard shortcuts.

import { api, createNote, flushOutbox, onUnauthorized, outbox } from "./api.js";
import { noteActions } from "./actions.js";
import { captureBusy, composeArea, focusCompose, setNoteContext, setupCapture, startVoiceFromShortcut, stopVoice, toggleHandsFree, toggleMic } from "./capture.js";
import { closeNote, openNote } from "./note-dialog.js";
import { openSettings, refreshStatus } from "./settings.js";
import { aiName, applyLocal, categoryOf, deviceTimeZone, emit, local, on, saveLocal, state } from "./state.js";
import { clear, copyText, debounce, h, icon, iconButton, toast } from "./ui.js";
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
let started = false;
let pendingShare = null; // also kept here in case session storage is blocked
let loadedBuild = ""; // the server version this page came from
let updateReady = false;

// Session storage can throw (private windows, blocked site data).
function sessionGet(key) { try { return sessionStorage.getItem(key); } catch { return null; } }
function sessionSet(key, value) { try { sessionStorage.setItem(key, value); } catch { /* blocked */ } }
function sessionRemove(key) { try { sessionStorage.removeItem(key); } catch { /* blocked */ } }

// ------------------------------------------------------------------- boot

// Every form is handled in script. Until it is wired up (or if start-up failed), a
// press of Enter must not reload the page and lose what was typed.
document.addEventListener("submit", (event) => event.preventDefault());

// Any action that fails without its own message still says what went wrong.
window.addEventListener("unhandledrejection", (event) => {
  const error = event.reason;
  if (error?.name === "AbortError") return;
  toast(error?.message || "Something went wrong.", { error: true });
});

async function boot() {
  applyLocal();
  registerServiceWorker();
  onUnauthorized(showLogin);
  let session;
  try {
    session = await api("/api/session");
  } catch (error) {
    if (error.offline) return startOffline();
    document.body.append(h("p", { class: "empty", style: "margin:2rem" }, `Can't reach your Magpie server (${error.message}). Reload to try again.`));
    return;
  }
  if (location.pathname === "/share") return handleShare(session.authenticated);
  if (!session.authenticated) return showLogin();
  return startApp();
}

// Opened without a connection (an installed app, a share): typing still works.
// Notes wait on this device, and Magpie starts properly once the connection is back.
function startOffline() {
  $("login").hidden = true;
  $("app").hidden = false;
  window.addEventListener("online", () => location.reload(), { once: true });
  if (location.pathname === "/share") {
    const shared = shareText(new URLSearchParams(location.search));
    if (shared.text) {
      $("app").hidden = true;
      return finishShare(shared);
    }
  }
  setupCapture();
  $("view-title").textContent = "Offline";
  clear($("view")).append(h("div", { class: "empty" },
    h("h3", {}, "You're offline"),
    h("p", {}, "You can still type notes. They stay on this device and are sent as soon as you're back online.")));
}

function registerServiceWorker() {
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  }
}

// ------------------------------------------------------------------ login

function showLogin() {
  // A dialog left open (the session ended while reading a note) would cover the form.
  document.querySelectorAll("dialog[open]").forEach((dialog) => dialog.close());
  $("app").hidden = true;
  $("share").hidden = true;
  $("login").hidden = false;
  const form = $("login-form");
  const input = $("login-password");
  input.focus();
  form.onsubmit = async (event) => {
    event.preventDefault();
    const button = form.querySelector("button[type=submit]");
    if (button.disabled) return;
    button.disabled = true;
    $("login-error").textContent = "";
    try {
      await api("/api/login", { method: "POST", body: { password: input.value } });
      input.value = "";
      $("login").hidden = true;
      const pending = sessionGet("magpie.pendingShare");
      sessionRemove("magpie.pendingShare");
      const shared = pending ? JSON.parse(pending) : pendingShare;
      pendingShare = null;
      if (shared) return finishShare(shared);
      startApp();
    } catch (error) {
      $("login-error").textContent = error.message;
      input.select();
    } finally {
      button.disabled = false;
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
    pendingShare = shared;
    sessionSet("magpie.pendingShare", JSON.stringify(shared));
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
        ? "Magpie can't be reached right now. It will sync when you open Magpie again."
        : "Magpie is filing it and will review any link.";
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
    if (error.status === 401) return; // the login form is showing
    clear($("view")).append(h("div", { class: "empty" },
      h("p", {}, `Magpie couldn't load: ${error.message}`),
      h("button", { type: "button", class: "btn", onclick: startApp }, "Try again")));
    return;
  }
  if (started) {
    // Logged in again after the session ended: everything is already wired up.
    renderBanners();
    route();
    syncOutbox();
    return;
  }
  started = true;
  if (Array.isArray(state.status.categories)) state.categories = state.status.categories;
  $("version").textContent = state.status.version;
  loadedBuild = buildOf(state.status);
  await adoptDeviceTimeZone();
  setNoteContext(noteContext);
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
  on("status", (status) => {
    if (Array.isArray(status?.categories)) state.categories = status.categories;
    if (loadedBuild && buildOf(status) !== loadedBuild) updateReady = true;
    renderBanners();
    renderTabs();
  });
  on("prefs", () => renderBanners());
  on("categories", () => renderTabs());
  on("categories-changed", refreshCategories);
  on("refresh", () => renderView());
  on("reminders-changed", () => {
    if (currentView === "scheduled" && !state.q) renderView();
  });
  on("shortcuts", showShortcuts);
  on("settings-closed", () => {
    if (location.hash === "#/settings") history.replaceState(null, "", `#/${currentView}`);
    renderView();
  });
  window.addEventListener("hashchange", route);
  window.addEventListener("online", syncOutbox);
  document.addEventListener("visibilitychange", () => (document.hidden ? reloadIfUpdated() : quietRefresh()));
  setInterval(quietRefresh, 30000);
  setInterval(checkForUpdate, 5 * 60000);
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

// Reminders follow the computer's clock. On a fresh install any device sets the time
// zone once; after that a computer opening Magpie updates it when its zone changed
// (travel, a new machine). Phones and tablets only get the banner below, so a phone
// in another zone can't keep switching it back and forth.
async function adoptDeviceTimeZone() {
  const device = deviceTimeZone();
  if (device === state.prefs.timezone) return;
  const computer = window.matchMedia?.("(pointer: fine)").matches && !/Android|iPhone|iPad|Mobile/i.test(navigator.userAgent);
  // "UTC" and "Etc/…" usually mean a device that doesn't know where it is.
  const real = device.includes("/") && !device.startsWith("Etc/");
  if (!state.status.timezone_unset && !(local.followDeviceZone !== false && computer && real)) return;
  try {
    state.prefs = await api("/api/prefs", { method: "PUT", body: { timezone: device } });
    state.status.timezone_unset = false;
    emit("prefs", state.prefs);
    toast(`Time zone set to ${device.replaceAll("_", " ")} from this ${computer ? "computer" : "device"}. Change it in Settings → Time.`);
  } catch {
    // The banner below still offers the switch.
  }
}

// Magpie updates itself on the server. An open window notices and loads the new
// version: by itself while nobody is looking at it, otherwise from a banner.
function buildOf(status) {
  return `${status?.version || ""}+${status?.build || ""}`;
}

async function checkForUpdate() {
  if ($("app").hidden) return;
  try {
    await refreshStatus();
  } catch {
    return; // restarting for the update, or offline: try again later
  }
  reloadIfUpdated();
}

function reloadIfUpdated() {
  if (updateReady && document.hidden && !captureBusy() && !outbox().length) location.reload();
}

async function syncOutbox() {
  if (!outbox().length) return;
  const { sent, rejected } = await flushOutbox();
  if (sent) {
    toast(`Synced ${sent} note${sent > 1 ? "s" : ""} saved while offline.`);
    renderView();
  }
  for (const item of rejected) {
    // Never drop a note silently: give it back to the user to fix or copy.
    const area = composeArea();
    if (area && !area.value.trim()) {
      area.value = item.text;
      toast(`A note saved offline couldn't be filed (${item.reason}). It's back in the text box.`, { error: true, timeout: 15000 });
    } else {
      toast(`A note saved offline couldn't be filed (${item.reason}).`, { error: true, timeout: 15000, action: "Copy it", onAction: () => copyText(item.text, "Note copied") });
    }
  }
  renderBanners();
}

// ---------------------------------------------------------------- routing

function parseHash() {
  const hash = location.hash.replace(/^#\/?/, "");
  const [first, second] = hash.split("/");
  const id = Number(second);
  if (first === "note" && Number.isInteger(id) && id > 0) return { view: currentView, note: id };
  if (first === "settings") return { view: currentView, settings: true };
  if (first === "kind" && VIEWS.some((v) => v.hash === `kind/${second}`)) return { view: `kind/${second}` };
  if (first === "category" && Number.isInteger(id) && id > 0) return { view: `category/${id}` };
  if (TITLES[first]) return { view: first };
  return { view: "inbox" };
}

function route() {
  // In-page anchors such as the "Skip to notes" link are not views.
  if (location.hash && !location.hash.startsWith("#/")) return;
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

// ------------------------------------------------- categories and dragging

const NOTE_TYPE = "application/x-magpie-note";

// A tab you can drop a dragged note on: a category, Done or Archive.
function acceptNotes(target, drop) {
  target.classList.add("drop-target");
  target.addEventListener("dragover", (event) => {
    if (!event.dataTransfer?.types.includes(NOTE_TYPE)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    target.classList.add("drop-here");
  });
  target.addEventListener("dragleave", () => target.classList.remove("drop-here"));
  target.addEventListener("drop", (event) => {
    target.classList.remove("drop-here");
    const id = Number(event.dataTransfer?.getData(NOTE_TYPE));
    const note = visibleNotes().find((n) => n.id === id);
    if (!note) return;
    event.preventDefault();
    drop(note);
  });
}

function dropOn(view) {
  if (view.hash === "done") return (note) => note.status !== "done" && noteActions.toggleDone(note);
  if (view.hash === "archived") return (note) => note.status !== "archived" && noteActions.toggleArchive(note);
  return null;
}

let categoriesRequest = null;
function refreshCategories() {
  // Several changes in a row (a drag, then an undo) need only one fetch.
  categoriesRequest ||= api("/api/categories")
    .then((data) => {
      state.categories = data.categories;
      renderTabs();
      if (currentView.startsWith("category/") && !categoryOf(Number(currentView.split("/")[1]))) location.hash = "#/inbox";
    })
    .catch(() => {})
    .finally(() => { categoriesRequest = null; });
  return categoriesRequest;
}

// Where a new note was written, so it remembers the tab (and stays in its category).
function noteContext() {
  if (state.q) return { captured_in: "Search" };
  if (currentView.startsWith("category/")) {
    const category = categoryOf(Number(currentView.split("/")[1]));
    return category ? { category_id: category.id, captured_in: category.name } : {};
  }
  return { captured_in: viewInfo(currentView).label };
}

function tab(hash, content, { count, className = "", drop = null } = {}) {
  const active = !state.q && hash === currentView;
  const link = h("a", { class: `tab ${className}`.trim(), href: `#/${hash}`, "aria-current": active ? "page" : null },
    content,
    count ? h("span", { class: "count" }, h("span", { class: "sr-only" }, ", "), String(count)) : null);
  if (drop) acceptNotes(link, drop);
  return h("li", {}, link);
}

function renderTabs() {
  const list = $("view-tabs");
  clear(list);
  const categories = state.categories || [];
  for (const view of VIEWS) {
    const count = view.count ? counts[view.count] : null;
    if (view.hash.startsWith("kind/") && !count && view.hash !== currentView) continue;
    list.append(tab(view.hash, [icon(view.icon), view.label], { count, drop: dropOn(view) }));
    if (view.hash !== "inbox") continue;
    // Your own categories come straight after the inbox, in your order and colours.
    for (const category of categories) {
      list.append(tab(`category/${category.id}`, [h("span", { class: "swatch-dot", "aria-hidden": "true" }), h("span", { dir: "auto" }, category.name)], {
        count: category.count,
        className: `category swatch-${category.color}`,
        drop: (note) => noteActions.moveTo(note, category),
      }));
    }
    list.append(h("li", {}, h("button", {
      type: "button",
      class: "tab add-category",
      title: "Add a category",
      "aria-label": categories.length ? "Add a category" : null,
      onclick: () => openSettings("categories"),
    }, icon("plus"), categories.length ? null : "Add a category")));
  }
  // Scroll the tab strip sideways only: scrollIntoView() would also scroll the page.
  const active = list.querySelector('[aria-current="page"]');
  const strip = list.closest(".views");
  if (active && strip && strip.scrollWidth > strip.clientWidth) {
    const a = active.getBoundingClientRect();
    const s = strip.getBoundingClientRect();
    if (a.left < s.left) strip.scrollLeft -= s.left - a.left;
    else if (a.right > s.right) strip.scrollLeft += a.right - s.right;
  }
}

let renderToken = 0;
async function renderView() {
  const token = ++renderToken;
  const current = () => token === renderToken;
  const container = $("view");
  const title = $("view-title");
  renderTabs();
  if (!container.firstElementChild || container.querySelector(".empty .btn")) {
    clear(container).append(h("p", { class: "muted", role: "status" }, "Loading…"));
  }
  try {
    if (state.q) {
      title.textContent = `Search: “${state.q}”`;
      await renderNotes(container, { view: "all" }, state.q, current);
      return;
    }
    const view = currentView;
    if (view === "scheduled") {
      title.textContent = TITLES.scheduled;
      detachList();
      await renderScheduled(container, current);
      api("/api/notes?limit=1").then((d) => emit("counts", d.counts)).catch(() => {});
    } else if (view === "digests") {
      title.textContent = TITLES.digests;
      detachList();
      await renderDigests(container, current);
    } else if (view === "ask") {
      title.textContent = TITLES.ask;
      detachList();
      renderAsk(container);
    } else if (view.startsWith("kind/")) {
      const kind = view.split("/")[1];
      title.textContent = viewInfo(view).label;
      await renderNotes(container, { view: "kind", kind }, "", current);
    } else if (view.startsWith("category/")) {
      const id = Number(view.split("/")[1]);
      title.textContent = categoryOf(id)?.name || "Category";
      await renderNotes(container, { view: "category", category: id }, "", current);
      if (!current()) return;
      const category = categoryOf(id);
      if (!category) {
        toast("That category isn't there any more.");
        location.hash = "#/inbox";
        return;
      }
      title.textContent = category.name;
    } else {
      title.textContent = TITLES[view] || "Inbox";
      await renderNotes(container, { view }, "", current);
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
    const name = aiName();
    const fixed = status.brain.locked || status.brain.key_source === "env";
    box.append(h("div", { class: "banner" }, icon("alert"),
      h("p", {}, fixed
        ? `${name} isn't working with the settings on the server (check its API key in the .env file). Your notes are saved and will be filed once it's fixed.`
        : `${name} isn't connected yet. Your notes are saved and will be filed as soon as it is.`),
      fixed ? null : h("button", { type: "button", class: "btn small primary", onclick: () => openSettings("ai") }, "Choose an AI")));
  }
  const device = deviceTimeZone();
  if (state.prefs && state.prefs.timezone !== device) {
    add(dismissible(`tz-${device}`, "info", `Your device is in ${device.replaceAll("_", " ")}, but reminders use ${state.prefs.timezone}.`,
      h("button", { type: "button", class: "btn small primary", onclick: async () => {
        try {
          state.prefs = await api("/api/prefs", { method: "PUT", body: { timezone: device } });
          emit("prefs", state.prefs);
          toast(`Reminders now use ${device}.`);
        } catch (error) {
          toast(error.message, { error: true });
        }
      } }, `Use ${device.split("/").pop().replaceAll("_", " ")}`)));
  }
  const configured = status.channels.filter((c) => c.configured && c.enabled);
  if (!configured.length) {
    add(dismissible("no-channels", "info", "Reminders only show up here until you turn on pop-ups or email.",
      h("button", { type: "button", class: "btn small", onclick: () => openSettings("notify") }, "Set up reminders")));
  }
  if (updateReady) {
    box.append(h("div", { class: "banner info" }, icon("sparkles"),
      h("p", {}, `Magpie has been updated to ${status.version}. Reload to use the new version.`),
      h("button", { type: "button", class: "btn small primary", onclick: () => location.reload() }, "Reload")));
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
      n: () => focusCompose(),
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
