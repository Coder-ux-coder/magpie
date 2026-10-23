// The welcome screen: the first thing a new person sees, so they can choose an
// AI, get reminders, set their command word and make categories before anything
// else. It can be skipped at any step and opened again from Settings.

import { api } from "./api.js";
import { ACCENTS, aiControls, appPopupControls, categoriesEditor, emailControls, pushControls, pushSupported, refreshStatus } from "./controls.js";
import { desktopApp, deviceTimeZone, emit, local, on, saveLocal, state } from "./state.js";
import { clear, h, icon, iconButton, toast } from "./ui.js";

let shown = false;
let returnFocus = null;

function dialog() {
  let el = document.getElementById("welcome-dialog");
  if (!el) {
    el = h("dialog", { id: "welcome-dialog", class: "sheet welcome", "aria-labelledby": "welcome-title" });
    el.addEventListener("cancel", () => finish({ quietly: true }));
    document.body.append(el);
  }
  return el;
}

async function savePrefs(changes) {
  state.prefs = await api("/api/prefs", { method: "PUT", body: changes });
  emit("prefs", state.prefs);
}

async function finish({ quietly = false } = {}) {
  const el = dialog();
  if (el.open) el.close();
  try {
    if (!state.prefs?.onboarded) await savePrefs({ onboarded: true });
  } catch {
    // Not fatal: it simply shows again next time.
  }
  if (!quietly) toast(`All set. Say “${state.prefs?.wake_phrase || "notes on"}”, press the mic, or type below.`);
  emit("welcome-done");
  (returnFocus?.isConnected ? returnFocus : document.getElementById("main"))?.focus?.({ preventScroll: true });
}

function voiceStep() {
  const prefs = state.prefs || {};
  const wake = h("input", { type: "text", id: "welcome-wake", value: prefs.wake_phrase || "notes on", maxlength: 60, dir: "auto" });
  const stop = h("input", { type: "text", id: "welcome-stop", value: prefs.stop_phrase || "notes off", maxlength: 60, dir: "auto" });
  const save = async (changes, message) => {
    try {
      await savePrefs(changes);
      toast(message);
    } catch (error) {
      toast(error.message, { error: true });
    }
  };
  wake.addEventListener("change", () => {
    const value = wake.value.trim();
    if (!value) { wake.value = state.prefs.wake_phrase; return; }
    save({ wake_phrase: value }, `Your command word is now “${value}”.`);
  });
  stop.addEventListener("change", () => {
    const value = stop.value.trim();
    if (!value) { stop.value = state.prefs.stop_phrase; return; }
    save({ stop_phrase: value }, `Say “${value}” to save a note.`);
  });
  const inApp = desktopApp();
  const listen = h("input", { type: "checkbox", role: "switch", id: "welcome-listen", checked: inApp ? state.status.desktop.listening !== false : local.autoListen !== false });
  listen.addEventListener("change", async () => {
    if (!inApp) return saveLocal({ autoListen: listen.checked });
    try {
      await api("/api/desktop/command", { method: "POST", body: { action: listen.checked ? "listen" : "pause" } });
    } catch (error) {
      listen.checked = !listen.checked;
      toast(error.message, { error: true });
    }
  });
  const accent = h("select", { id: "welcome-accent" }, ACCENTS.map(([value, label]) => h("option", { value, selected: value === (prefs.speech_lang || "") }, label)));
  accent.addEventListener("change", () => save({ speech_lang: accent.value }, "Accent saved."));
  return [
    h("p", {}, inApp
      ? "Say your command word anywhere on this computer, even with Magpie closed: a small bar shows your words as you talk. Say the stop word to save the note. You can also press the mic or just type."
      : "Say your command word to start a note, talk, then say the stop word to save it. You can also press the mic or just type."),
    h("div", { class: "row" },
      h("div", { class: "field" }, h("label", { for: "welcome-wake" }, "Command word"), wake),
      h("div", { class: "field" }, h("label", { for: "welcome-stop" }, "Stop word"), stop)),
    h("label", { class: "switch", for: "welcome-listen" }, listen, h("span", { class: "switch-track", "aria-hidden": "true" }), h("span", {}, inApp ? "Always listen for the command word (on this computer, nothing is sent anywhere)" : "Listen for the command word when Magpie opens")),
    // The app's own listening is American English whatever the accent setting says.
    inApp ? null : h("div", { class: "field" }, h("label", { for: "welcome-accent" }, "Your accent"), accent, h("span", { class: "hint" }, "Magpie listens for English. Your accent helps it hear you.")),
  ];
}

const STEPS = [
  {
    title: "Welcome to Magpie",
    body: () => [
      h("p", { class: "lead" }, "Say what's on your mind and Magpie files it: it tidies your words, gives the note a title and a category, and reminds you at the right time."),
      h("ul", { class: "welcome-points" },
        h("li", {}, icon("mic"), h("span", {}, `Say “${state.prefs?.wake_phrase || "notes on"}”, talk, and say “${state.prefs?.stop_phrase || "notes off"}”. Or press the mic, or type.`)),
        h("li", {}, icon("bell"), h("span", {}, "Reminders pop up on this computer and can come by email to your phone.")),
        h("li", {}, icon("list"), h("span", {}, "Your own categories, in your colours. Drag notes between them.")),
      ),
      h("p", { class: "muted small" }, `Times follow this computer's clock: ${deviceTimeZone().replaceAll("_", " ")}. A few quick choices and you're ready; you can change everything later in Settings.`),
    ],
  },
  {
    title: "Choose your AI",
    body: () => [
      h("p", {}, "Pick the AI that files your notes. Claude Haiku 5.5 is the default: fast and low-cost. You can also use ChatGPT, Gemini, a free model on this computer (Ollama), or no AI at all."),
      aiControls({ onSaved: () => {} }),
    ],
  },
  {
    title: "Get reminders",
    body: () => [
      h("p", {}, "How should Magpie tell you it's time? Use both if you like: pop-ups while you're at the computer, email for your phone."),
      h("div", { class: "welcome-card" },
        h("h3", {}, icon("bell"), "Pop-ups on this computer"),
        desktopApp()
          ? appPopupControls()
          : [pushSupported() ? h("p", { class: "muted small" }, "A simple notification, even when Magpie's window is in the background.") : null, pushControls()]),
      h("div", { class: "welcome-card" },
        h("h3", {}, icon("send"), "Email"),
        h("p", { class: "muted small" }, "Every reminder as an email, like “Call James · due today 10:00 AM”."),
        emailControls()),
    ],
  },
  { title: "Your voice", body: voiceStep },
  {
    title: "Your categories",
    body: () => [
      h("p", {}, "Add the categories you think in. The AI files notes into them, notes you write in a category's tab stay there, and you can drag notes onto a category."),
      categoriesEditor({ suggestions: true }),
    ],
  },
  {
    title: "You're all set",
    body: () => [
      h("p", { class: "lead" }, `Try it now: say “${state.prefs?.wake_phrase || "notes on"}, remind me to call James at five, ${state.prefs?.stop_phrase || "notes off"}”, or type it in the bar at the bottom.`),
      h("p", { class: "muted small" }, "Everything here is in Settings too, and this screen is under Settings → Privacy."),
    ],
  },
];

export function openWelcome(start = 0) {
  const el = dialog();
  returnFocus = document.activeElement;
  let index = Math.max(0, Math.min(start, STEPS.length - 1));

  const render = () => {
    const step = STEPS[index];
    const last = index === STEPS.length - 1;
    clear(el).append(
      h("div", { class: "sheet-inner" },
        h("div", { class: "sheet-head" },
          h("div", { class: "title-block" },
            h("p", { class: "eyebrow" }, `Step ${index + 1} of ${STEPS.length}`),
            h("h2", { id: "welcome-title", tabindex: "-1" }, step.title)),
          iconButton("close", "Close the welcome screen", () => finish({ quietly: true })),
        ),
        h("div", { class: "sheet-body welcome-body" }, step.body()),
        h("div", { class: "sheet-foot" },
          h("ol", { class: "steps", "aria-label": "Progress" },
            STEPS.map((s, i) => h("li", { class: i === index ? "current" : i < index ? "done" : "", "aria-current": i === index ? "step" : null }, h("span", { class: "sr-only" }, s.title)))),
          index > 0 ? h("button", { type: "button", class: "btn", onclick: () => { index -= 1; render(); } }, "Back") : null,
          last
            ? h("button", { type: "button", class: "btn primary", onclick: () => finish() }, icon("check"), "Start using Magpie")
            : h("button", { type: "button", class: "btn primary", onclick: () => { index += 1; render(); } }, index === 0 ? "Let's go" : "Next"),
          !last && index > 0 ? h("button", { type: "button", class: "btn linklike", onclick: () => finish({ quietly: true }) }, "Skip the rest") : null,
        ),
      ),
    );
    el.querySelector("#welcome-title")?.focus();
  };

  render();
  if (!el.open) el.showModal();
  shown = true;
}

// Open by itself for someone who hasn't seen it, once their settings arrive.
on("status", (status) => {
  if (shown || !status?.prefs || status.prefs.onboarded) return;
  if (document.querySelector("dialog[open]")) return;
  openWelcome();
});
on("welcome", () => openWelcome());
// A fresh status after the AI or email changed keeps other views in step.
on("welcome-done", () => refreshStatus().catch(() => {}));
