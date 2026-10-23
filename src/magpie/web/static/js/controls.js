// Setup controls shared by Settings and the welcome screen: which AI files your
// notes, email and pop-up reminders, your categories, and what Magpie learned.

import { api } from "./api.js";
import { emit, state } from "./state.js";
import { append, clear, confirmDialog, h, icon, iconButton, toast } from "./ui.js";

const REPO = "https://github.com/Coder-ux-coder/magpie";
const CLAUDE_MODELS = [
  ["claude-haiku-5-5", "Haiku 5.5: fast and low-cost (default)"],
  ["claude-sonnet-5-5", "Sonnet 5.5: more capable"],
  ["claude-opus-5-5", "Opus 5.5: most capable"],
];
// Magpie listens for English; this only picks the accent.
export const ACCENTS = [
  ["", "Automatic"],
  ["en-GB", "English (UK)"],
  ["en-US", "English (US)"],
  ["en-AU", "English (Australia)"],
  ["en-CA", "English (Canada)"],
  ["en-IE", "English (Ireland)"],
  ["en-NZ", "English (New Zealand)"],
  ["en-ZA", "English (South Africa)"],
];
export const COLOR_NAMES = {
  red: "Red", orange: "Orange", amber: "Amber", green: "Green", teal: "Teal",
  blue: "Blue", indigo: "Indigo", purple: "Purple", pink: "Pink", grey: "Grey",
};
const SUGGESTED = [["Work", "blue"], ["Home", "green"], ["Shopping", "amber"], ["Family", "pink"], ["Ideas", "purple"], ["Health", "teal"], ["Money", "orange"]];
const APP_PASSWORDS = [
  ["Gmail", "https://myaccount.google.com/apppasswords"],
  ["Outlook", "https://account.live.com/proofs/AppPassword"],
  ["Yahoo", "https://login.yahoo.com/account/security/app-passwords"],
  ["iCloud", "https://account.apple.com/account/manage"],
];

export async function refreshStatus() {
  const status = await api("/api/status");
  if (!status || typeof status !== "object" || !status.brain || !status.prefs) {
    throw new Error("The server sent an unexpected answer. Is something else running at this address?");
  }
  state.status = status;
  state.prefs = status.prefs;
  emit("status", state.status);
  return state.status;
}

function field(id, label, control, hint) {
  return h("div", { class: "field" }, h("label", { for: id }, label), control, hint ? h("span", { class: "hint", id: `${id}-hint` }, hint) : null);
}

function busyButton(button, text) {
  const before = button.textContent;
  button.disabled = true;
  button.textContent = text;
  return () => {
    button.disabled = false;
    button.textContent = before;
  };
}

// ------------------------------------------------------------------ the AI

export function aiControls({ onSaved } = {}) {
  const box = h("div", { class: "ai-controls" }, h("p", { class: "muted" }, "Loading your AI settings…"));
  let ai = null;
  let chosen = null;

  const load = async () => {
    try {
      ai = await api("/api/ai");
      if (!ai.providers.some((p) => p.id === chosen)) chosen = ai.provider;
      render();
    } catch (error) {
      clear(box);
      box.append(h("p", { class: "error" }, error.message));
    }
  };

  const render = () => {
    clear(box);
    const current = ai.providers.find((p) => p.id === chosen) || ai.providers[0];
    const usingIt = current.id === ai.provider;
    const doing = ai.provider === "basic" ? "Filing notes with simple rules (no AI)" : `Filing notes with ${ai.label} · ${ai.model || "its own default model"}`;
    box.append(h("p", { class: "ai-status", role: "status" },
      h("span", { class: `status-dot${ai.available ? " on" : ""}`, "aria-hidden": "true" }),
      ai.available ? doing : `${ai.label} isn't connected yet. Your notes are saved and filed once it is.`));

    const select = h("select", { id: "ai-provider", disabled: ai.locked },
      ai.providers.map((p) => h("option", { value: p.id, selected: p.id === current.id }, p.label)));
    select.addEventListener("change", () => {
      chosen = select.value;
      render();
      box.querySelector("#ai-provider")?.focus();
    });

    const fromServer = current.key_source === "env";
    const wantsKey = (current.needs_key || current.id === "compatible") && !fromServer;
    const key = wantsKey ? h("input", {
      type: "password", id: "ai-key", autocomplete: "off", spellcheck: "false",
      placeholder: current.has_key ? "A key is saved. Paste a new one to replace it" : (current.key_hint || "Paste your key"),
    }) : null;
    const address = current.fixed_url ? null : h("input", { type: "url", id: "ai-url", value: current.base_url, placeholder: "https://…/v1", spellcheck: "false", autocomplete: "off" });
    const options = h("datalist", { id: "ai-model-list" }, (current.id === "claude" ? CLAUDE_MODELS : []).map(([value, label]) => h("option", { value }, label)));
    const model = current.id === "basic" ? null : h("input", { type: "text", id: "ai-model", value: current.model, list: "ai-model-list", spellcheck: "false", autocomplete: "off", placeholder: current.id === "codex" ? "Codex's own default" : "" });
    // Codex has no list to show: it uses its own default unless a model is named.
    const browse = model && current.id !== "codex" ? h("button", { type: "button", class: "btn small" }, "Show available models") : null;
    browse?.addEventListener("click", async () => {
      const done = busyButton(browse, "Looking…");
      try {
        const found = await api("/api/ai/models", { method: "POST", timeout: 30000, body: { provider: current.id, base_url: address?.value || null, key: key?.value.trim() || null } });
        clear(options);
        append(options, found.models.map((id) => h("option", { value: id })));
        if (found.models.length) toast(`${found.models.length} models found. Pick one in the Model box.`);
        else toast(current.id === "ollama" ? "Ollama has no models yet. Run “ollama pull llama3.2”, then try again." : "No models found.");
        model.focus();
      } catch (error) {
        toast(error.message, { error: true });
      } finally {
        done();
      }
    });

    const label = usingIt && ai.available ? "Save changes" : current.id === "basic" ? "Use simple rules" : `Use ${current.name}`;
    const save = h("button", { type: "submit", class: "btn primary" }, label);
    const form = h("form", { class: "ai-form" },
      field("ai-provider", "AI that files your notes", select, ai.locked ? `Chosen on the server (MAGPIE_BRAIN). Change it there.` : current.help),
      key ? field("ai-key", current.id === "compatible" ? "API key (if it needs one)" : `${current.name} API key`, key,
        current.key_url ? h("span", {}, "Get one at ", h("a", { href: current.key_url, target: "_blank", rel: "noopener" }, new URL(current.key_url).host), ". It's checked, then kept on your Magpie server only.") : "Kept on your Magpie server only.") : null,
      fromServer ? h("p", { class: "muted small" }, "The key is set on the server.") : null,
      address ? field("ai-url", "Address", address, current.id === "ollama" ? "Where Ollama listens. Magpie fills this in; change it only if Ollama runs on another computer." : "The service's OpenAI-compatible address, ending in /v1.") : null,
      model ? field("ai-model", "Model", h("div", { class: "copy-field" }, model, browse), current.default_model ? `Default: ${current.default_model}.` : current.id === "codex" ? "Leave it empty to use Codex's own default." : "The name of the model to use.") : null,
      current.id === "claude" && !current.has_key && !fromServer
        ? h("p", { class: "muted small" }, "No API key? Magpie also runs inside Claude, on your own Claude account: ",
            h("a", { href: `${REPO}#use-claude-without-an-api-key`, target: "_blank", rel: "noopener" }, "Magpie in Claude"), ".")
        : null,
      model ? options : null,
      h("div", { class: "row" }, save,
        current.has_key && current.key_source === "dashboard"
          ? h("button", { type: "button", class: "btn small danger", onclick: () => removeKey(current) }, "Remove saved key")
          : null),
    );
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const body = { provider: current.id };
      if (key?.value.trim()) body.key = key.value.trim();
      if (model && model.value.trim()) body.model = model.value.trim();
      if (address) body.base_url = address.value.trim();
      const done = busyButton(save, "Checking…");
      try {
        const result = await api("/api/ai", { method: "PUT", body, timeout: 90000 });
        ai = result;
        chosen = result.provider;
        render();
        toast(result.warning || (result.provider === "basic" ? "Magpie now files notes with simple rules." : `Connected. ${result.label} files your notes from now on.`));
        await refreshStatus().catch(() => {});
        onSaved?.(result);
      } catch (error) {
        done();
        toast(error.message, { error: true });
      }
    });
    box.append(form);
  };

  const removeKey = async (provider) => {
    const ok = await confirmDialog({ title: `Remove the ${provider.name} key?`, message: "Notes wait until you add a key again. Your notes stay.", confirm: "Remove", danger: true });
    if (!ok) return;
    try {
      ai = await api(`/api/ai/key?provider=${encodeURIComponent(provider.id)}`, { method: "DELETE" });
      render();
      await refreshStatus().catch(() => {});
    } catch (error) {
      toast(error.message, { error: true });
    }
  };

  load();
  return box;
}

// ------------------------------------------------------------------ email

export function emailControls({ onSaved } = {}) {
  const box = h("div", { class: "email-controls" }, h("p", { class: "muted" }, "Loading…"));
  let info = null;
  let editing = false;

  const load = async () => {
    try {
      info = await api("/api/email");
      render();
    } catch (error) {
      clear(box);
      box.append(h("p", { class: "error" }, error.message));
    }
  };

  const sendTest = async (button) => {
    const done = button ? busyButton(button, "Sending…") : () => {};
    try {
      const result = await api("/api/notify/test", { method: "POST", body: { channel: "email" }, timeout: 60000 });
      if (result.results?.email === "ok") toast(`A test email is on its way to ${info.to}.`);
      else toast(`Email: ${result.results?.email || "not sent"}`, { error: true });
    } catch (error) {
      toast(error.message, { error: true });
    } finally {
      done();
    }
  };

  const render = () => {
    clear(box);
    const test = h("button", { type: "button", class: "btn small" }, "Send a test");
    test.addEventListener("click", () => sendTest(test));
    if (info.source === "env") {
      box.append(h("p", { class: "small" }, `Set up on the server: reminders go to ${info.to}.`), test);
      return;
    }
    if (info.configured && !editing) {
      box.append(
        h("p", { class: "small" }, `Reminders and digests go to ${info.to}.`),
        h("div", { class: "row" }, test,
          h("button", { type: "button", class: "btn small", onclick: () => { editing = true; render(); } }, "Change"),
          h("button", { type: "button", class: "btn small danger", onclick: turnOff }, "Turn off")),
      );
      return;
    }
    const address = h("input", { type: "email", id: "email-address", value: info.address || "", autocomplete: "email", required: true, placeholder: "you@gmail.com" });
    const password = h("input", { type: "password", id: "email-password", autocomplete: "off", spellcheck: "false", placeholder: info.configured ? "Leave empty to keep the saved one" : "16-letter app password" });
    const to = h("input", { type: "email", id: "email-to", value: info.to && info.to !== info.address ? info.to : "", placeholder: "Same address" });
    const host = h("input", { type: "text", id: "email-host", value: info.host || "", placeholder: "Filled in for Gmail, Outlook, Yahoo, iCloud…", spellcheck: "false" });
    const port = h("input", { type: "number", id: "email-port", value: info.port || "", min: 1, max: 65535, inputmode: "numeric" });
    const security = h("select", { id: "email-security" },
      [["", "Automatic"], ["starttls", "STARTTLS (port 587)"], ["ssl", "SSL/TLS (port 465)"], ["none", "None"]].map(([value, label]) => h("option", { value, selected: value === (info.security || "") }, label)));
    const save = h("button", { type: "submit", class: "btn primary" }, "Save and send a test");
    const form = h("form", { class: "email-form" },
      field("email-address", "Your email address", address),
      field("email-password", "App password", password,
        h("span", {}, "Not the password you sign in with: make an app password for Magpie (",
          APP_PASSWORDS.flatMap(([name, url], index) => [index ? ", " : "", h("a", { href: url, target: "_blank", rel: "noopener" }, name)]), ").")),
      field("email-to", "Send reminders to (optional)", to, "Another address, such as the one on your phone. Leave empty to use the address above."),
      h("details", {}, h("summary", {}, "Mail server settings"),
        field("email-host", "Server (SMTP)", host), h("div", { class: "row" }, field("email-port", "Port", port), field("email-security", "Security", security))),
      h("div", { class: "row" }, save,
        info.configured ? h("button", { type: "button", class: "btn small", onclick: () => { editing = false; render(); } }, "Cancel") : null),
    );
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const body = { address: address.value.trim(), to: to.value.trim() || null };
      if (password.value) body.password = password.value;
      if (host.value.trim()) body.host = host.value.trim();
      if (port.value) body.port = Number(port.value);
      if (security.value) body.security = security.value;
      const done = busyButton(save, "Checking…");
      try {
        info = await api("/api/email", { method: "PUT", body, timeout: 60000 });
        editing = false;
        render();
        await sendTest();
        await refreshStatus().catch(() => {});
        onSaved?.(info);
      } catch (error) {
        done();
        toast(error.message, { error: true });
      }
    });
    box.append(form);
  };

  const turnOff = async () => {
    const ok = await confirmDialog({ title: "Stop emailing reminders?", message: "Magpie forgets the address and app password.", confirm: "Turn off", danger: true });
    if (!ok) return;
    try {
      info = await api("/api/email", { method: "DELETE" });
      render();
      await refreshStatus().catch(() => {});
    } catch (error) {
      toast(error.message, { error: true });
    }
  };

  load();
  return box;
}

// --------------------------------------------------------- pop-up reminders

function urlBase64ToUint8Array(base64) {
  const padding = "=".repeat((4 - (base64.length % 4)) % 4);
  const raw = atob((base64 + padding).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from([...raw].map((c) => c.charCodeAt(0)));
}

export const pushSupported = () => "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;

export async function turnOnPopups() {
  const permission = await Notification.requestPermission();
  if (permission !== "granted") {
    throw new Error("Notifications are blocked for Magpie. Allow them in your browser's site settings, then try again.");
  }
  const registration = await navigator.serviceWorker.ready;
  const subscription = await registration.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: urlBase64ToUint8Array(state.status.push.public_key),
  });
  await api("/api/push/subscribe", { method: "POST", body: subscription.toJSON() });
  await api("/api/notify/test", { method: "POST", body: { channel: "webpush" } }).catch(() => {});
  await refreshStatus().catch(() => {});
}

/** With the Magpie app, reminders pop up from the app itself: nothing to turn on. */
export function appPopupControls() {
  const test = h("button", { type: "button", class: "btn small" }, "Show a test pop-up");
  test.addEventListener("click", async () => {
    const done = busyButton(test, "Sending…");
    try {
      const result = await api("/api/notify/test", { method: "POST", body: { channel: "desktop" } });
      if (!result.ok) throw new Error(result.results?.desktop || "The Magpie app didn't show it.");
      toast("Look at the bottom-right corner of your screen.");
    } catch (error) {
      toast(error.message, { error: true });
    } finally {
      done();
    }
  });
  return h("div", { class: "push-controls" },
    h("p", { class: "muted small" }, "Reminders pop up in the corner of your screen from the Magpie app, over whatever you're doing, even when this window is closed."),
    h("div", { class: "row" }, test));
}

export function pushControls({ onSaved } = {}) {
  const box = h("div", { class: "push-controls" });
  if (!pushSupported()) {
    box.append(h("p", { class: "muted small" }, "This browser can't show pop-up notifications. On iPhone and iPad, add Magpie to your Home Screen, then open it from there."));
    return box;
  }
  const count = state.status?.push?.subscriptions || 0;
  const enable = h("button", { type: "button", class: "btn small primary" }, icon("bell"), "Turn on pop-ups here");
  enable.addEventListener("click", async () => {
    const done = busyButton(enable, "Turning on…");
    try {
      await turnOnPopups();
      toast("Done. You should see a test pop-up now.");
      onSaved?.();
    } catch (error) {
      toast(error.message || "Couldn't turn on notifications.", { error: true });
    } finally {
      done();
    }
  });
  const disable = h("button", { type: "button", class: "btn small" }, "Stop on this device");
  disable.addEventListener("click", async () => {
    try {
      const registration = await navigator.serviceWorker.ready;
      const subscription = await registration.pushManager.getSubscription();
      if (subscription) {
        await api("/api/push/unsubscribe", { method: "POST", body: { endpoint: subscription.endpoint } });
        await subscription.unsubscribe();
      }
      toast("This device won't show pop-ups.");
      await refreshStatus().catch(() => {});
    } catch (error) {
      toast(error.message, { error: true });
    }
  });
  box.append(
    h("p", { class: "muted small" }, count ? `${count} device${count > 1 ? "s" : ""} will get pop-ups.` : "No device gets pop-ups yet."),
    h("div", { class: "row" }, enable, count ? disable : null),
  );
  return box;
}

// -------------------------------------------------------------- categories

export function colorSelect(id, value, label) {
  const select = h("select", { id, class: `color-select swatch-${value}`, "aria-label": label },
    Object.entries(COLOR_NAMES).map(([color, name]) => h("option", { value: color, selected: color === value }, name)));
  select.addEventListener("change", () => { select.className = `color-select swatch-${select.value}`; });
  return select;
}

export function categoriesEditor({ suggestions = false } = {}) {
  const box = h("div", { class: "categories-editor" }, h("p", { class: "muted" }, "Loading…"));
  let categories = [];
  let dragging = null;

  const load = async () => {
    try {
      categories = (await api("/api/categories")).categories;
      render();
    } catch (error) {
      clear(box);
      box.append(h("p", { class: "error" }, error.message));
    }
  };
  const changed = (message) => {
    emit("categories-changed");
    if (message) toast(message);
    return load();
  };
  const run = async (work, message) => {
    try {
      await work();
      await changed(message);
    } catch (error) {
      toast(error.message, { error: true });
      await load();
    }
  };
  const move = (id, offset) => {
    const ids = categories.map((c) => c.id);
    const from = ids.indexOf(id);
    const to = from + offset;
    if (from < 0 || to < 0 || to >= ids.length) return;
    ids.splice(to, 0, ids.splice(from, 1)[0]);
    run(() => api("/api/categories/order", { method: "PUT", body: { ids } })).then(() => box.querySelector(`[data-id="${id}"] .move-${offset < 0 ? "up" : "down"}`)?.focus());
  };

  const row = (category, index) => {
    const name = h("input", { type: "text", value: category.name, maxlength: 40, "aria-label": `Name of the ${category.name} category`, dir: "auto" });
    name.addEventListener("change", () => {
      const value = name.value.trim();
      if (!value) { name.value = category.name; return; }
      run(() => api(`/api/categories/${category.id}`, { method: "PATCH", body: { name: value } }), "Renamed.");
    });
    const color = colorSelect(`category-color-${category.id}`, category.color, `Colour of ${category.name}`);
    color.addEventListener("change", () => run(() => api(`/api/categories/${category.id}`, { method: "PATCH", body: { color: color.value } })));
    const remove = async () => {
      const ok = await confirmDialog({ title: `Remove ${category.name}?`, message: "Its notes stay; they just won't be in a category.", confirm: "Remove", danger: true });
      if (ok) run(() => api(`/api/categories/${category.id}`, { method: "DELETE" }), `Removed ${category.name}. Its notes are still in your inbox.`);
    };
    const li = h("li", { class: "category-row", draggable: "true", dataset: { id: category.id } },
      h("span", { class: "drag-handle", "aria-hidden": "true", title: "Drag to reorder" }, "⋮⋮"),
      color, name,
      h("span", { class: "muted small count" }, `${category.count} open`),
      h("button", { type: "button", class: "icon-btn move-up", "aria-label": `Move ${category.name} up`, disabled: index === 0, onclick: () => move(category.id, -1) }, "↑"),
      h("button", { type: "button", class: "icon-btn move-down", "aria-label": `Move ${category.name} down`, disabled: index === categories.length - 1, onclick: () => move(category.id, 1) }, "↓"),
      iconButton("trash", `Remove ${category.name}`, remove),
    );
    li.addEventListener("dragstart", (event) => {
      dragging = category.id;
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", String(category.id));
      li.classList.add("dragging");
    });
    li.addEventListener("dragend", () => li.classList.remove("dragging"));
    li.addEventListener("dragover", (event) => {
      if (dragging !== null && dragging !== category.id) event.preventDefault();
    });
    li.addEventListener("drop", (event) => {
      event.preventDefault();
      const ids = categories.map((c) => c.id).filter((id) => id !== dragging);
      ids.splice(ids.indexOf(category.id), 0, dragging);
      dragging = null;
      run(() => api("/api/categories/order", { method: "PUT", body: { ids } }));
    });
    return li;
  };

  const render = () => {
    clear(box);
    if (categories.length) {
      box.append(h("ul", { class: "category-list", "aria-label": "Your categories" }, categories.map(row)));
    } else {
      box.append(h("p", { class: "muted small" }, "No categories yet. Add your own, such as Work or Shopping: the AI files notes into them, and you can drag notes onto them."));
    }
    const name = h("input", { type: "text", id: "new-category", maxlength: 40, placeholder: "New category, e.g. Work", dir: "auto", required: true });
    const color = colorSelect("new-category-color", Object.keys(COLOR_NAMES)[categories.length % 10], "Colour of the new category");
    const form = h("form", { class: "copy-field add-category" }, name, color, h("button", { type: "submit", class: "btn small primary" }, icon("plus"), "Add"));
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const value = name.value.trim();
      if (!value) return;
      run(() => api("/api/categories", { method: "POST", body: { name: value, color: color.value } }), `Added ${value}.`)
        .then(() => box.querySelector("#new-category")?.focus());
    });
    box.append(h("div", { class: "field" }, h("label", { for: "new-category" }, "Add a category"), form));
    if (suggestions) {
      const taken = new Set(categories.map((c) => c.name.toLowerCase()));
      const offers = SUGGESTED.filter(([label]) => !taken.has(label.toLowerCase()));
      if (offers.length) {
        box.append(h("div", { class: "chips suggestions", role: "group", "aria-label": "Suggested categories" },
          offers.map(([label, tone]) => h("button", { type: "button", class: `chip chip-button swatch-${tone}`, onclick: () => run(() => api("/api/categories", { method: "POST", body: { name: label, color: tone } }), `Added ${label}.`) }, icon("plus"), label))));
      }
    }
  };

  load();
  return box;
}

// ---------------------------------------------------------------- learning

function lessonText(lesson) {
  const instead = lesson.from ? ` (not ${lesson.from})` : "";
  const what = lesson.field === "category" ? `goes in ${lesson.to}` : `is a ${lesson.to}`;
  return `“${lesson.text}” ${what}${instead}`;
}

export function learningControls() {
  const box = h("div", { class: "learning" }, h("p", { class: "muted" }, "Loading…"));
  let data = null;
  const load = async () => {
    try {
      data = await api("/api/learning");
      render();
    } catch (error) {
      clear(box);
      box.append(h("p", { class: "error" }, error.message));
    }
  };
  const forget = async (index) => {
    try {
      data = await api(index === undefined ? "/api/learning" : `/api/learning?index=${index}`, { method: "DELETE" });
      render();
    } catch (error) {
      toast(error.message, { error: true });
    }
  };
  const render = () => {
    clear(box);
    const on = h("input", { type: "checkbox", role: "switch", id: "pref-learn", checked: data.enabled });
    on.addEventListener("change", async () => {
      try {
        state.prefs = await api("/api/prefs", { method: "PUT", body: { learn: on.checked } });
        emit("prefs", state.prefs);
        await load();
      } catch (error) {
        toast(error.message, { error: true });
      }
    });
    box.append(h("label", { class: "switch", for: "pref-learn" }, on, h("span", { class: "switch-track", "aria-hidden": "true" }), h("span", {}, "Learn from my corrections")));
    if (!data.lessons.length) {
      box.append(h("p", { class: "muted small" }, "Nothing learned yet. When you move a note to another category or change its kind, Magpie remembers and files similar notes your way."));
      return;
    }
    box.append(
      h("p", { class: "muted small" }, "What Magpie learned from your corrections (newest first):"),
      h("ul", { class: "lessons" }, data.lessons.map((lesson, index) => h("li", {},
        h("span", { dir: "auto" }, lessonText(lesson)),
        h("button", { type: "button", class: "btn small", "aria-label": `Forget: ${lessonText(lesson)}`, onclick: () => forget(index) }, "Forget")))),
      h("button", { type: "button", class: "btn small", onclick: () => forget() }, "Forget everything"),
    );
  };
  load();
  return box;
}
