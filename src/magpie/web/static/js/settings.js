// Settings: account-wide preferences (saved on the server) and device
// preferences (saved in this browser).

import { api } from "./api.js";
import { ACCENTS, aiControls, appPopupControls, categoriesEditor, emailControls, learningControls, pushControls, refreshStatus } from "./controls.js";
import "./welcome.js";
import { desktopApp, deviceTimeZone, emit, local, on, saveLocal, snoozeLabel, state } from "./state.js";
import { speechSupported, recordingSupported } from "./voice.js";
import { ttsSupported } from "./speech.js";
import { append, clear, confirmDialog, copyText, debounce, h, icon, iconButton, toast } from "./ui.js";

const dialog = () => document.getElementById("settings-dialog");
const REPO = "https://github.com/Coder-ux-coder/magpie";

const DAYS = [["", "Off"], ["mon", "Monday"], ["tue", "Tuesday"], ["wed", "Wednesday"], ["thu", "Thursday"], ["fri", "Friday"], ["sat", "Saturday"], ["sun", "Sunday"]];
const CHANNEL_HELP = {
  desktop: "Install the Magpie app for Windows: reminders then pop up in the corner of the screen.",
  telegram: "Create a bot with @BotFather, set MAGPIE_TELEGRAM_BOT_TOKEN and restart Magpie.",
  ntfy: "Install the ntfy app, pick a secret topic name and set MAGPIE_NTFY_TOPIC.",
  webhook: "Set MAGPIE_WEBHOOK_URL (Slack, Discord, or any URL that accepts JSON).",
};

export { refreshStatus };

async function savePrefs(changes, message = "Saved.") {
  try {
    state.prefs = await api("/api/prefs", { method: "PUT", body: changes });
    emit("prefs", state.prefs);
    if (message) toast(message);
  } catch (error) {
    toast(error.message, { error: true });
    // The server refused the value: show what is really saved.
    if (error.status === 422 && dialog().open) openSettings();
  }
}
const savePrefsSoon = debounce(savePrefs, 800);

function field(id, label, control, hint) {
  return h("div", { class: "field" }, h("label", { for: id }, label), control, hint ? h("span", { class: "hint", id: `${id}-hint` }, hint) : null);
}

function toggle(id, label, checked, onChange, { disabled = false, hint } = {}) {
  const input = h("input", { type: "checkbox", role: "switch", id, checked, disabled });
  input.addEventListener("change", () => onChange(input.checked));
  return h("div", {},
    h("label", { class: "switch", for: id }, input, h("span", { class: "switch-track", "aria-hidden": "true" }), h("span", {}, label)),
    hint ? h("p", { class: "muted small", style: "margin:0 0 .25rem" }, hint) : null,
  );
}

function segmented(label, options, value, onPick) {
  const group = h("div", { class: "segmented", role: "group", "aria-label": label });
  for (const [optionValue, optionLabel, aria] of options) {
    const button = h("button", { type: "button", "aria-pressed": String(optionValue === value), "aria-label": aria || null }, optionLabel);
    button.addEventListener("click", () => {
      group.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", "false"));
      button.setAttribute("aria-pressed", "true");
      onPick(optionValue);
    });
    group.append(button);
  }
  return h("div", { class: "field" }, h("span", { style: "font-weight:600" }, label), group);
}

function section(id, title, ...children) {
  return h("section", { class: "settings-section", id: `settings-${id}`, "aria-labelledby": `settings-${id}-title` },
    h("h3", { id: `settings-${id}-title` }, title),
    ...children,
  );
}

// ---------------------------------------------------------------- sections

function aiSection() {
  const status = state.status;
  const prefs = state.prefs;
  const usage = status.brain.usage || {};
  const about = h("textarea", { id: "pref-about", rows: 3, dir: "auto", maxlength: 2000, placeholder: "e.g. I work 9 to 5 in London. My manager is Sarah. I have two kids at school." });
  about.value = prefs.about_me || "";
  about.addEventListener("input", () => savePrefsSoon({ about_me: about.value }));
  const rules = h("textarea", { id: "pref-instructions", rows: 3, dir: "auto", maxlength: 2000, placeholder: "e.g. Tag anything about the shop with #shop. Remind me 30 minutes before meetings. Don't ping me after 10 pm. Use British spelling." });
  rules.value = prefs.instructions || "";
  rules.addEventListener("input", () => savePrefsSoon({ instructions: rules.value }));
  const choices = [
    ["quiet", "Quiet", "Only remind me when I ask for a time."],
    ["balanced", "Balanced", "Also remind me before clear deadlines and appointments."],
    ["proactive", "Proactive", "Suggest a reminder for anything actionable."],
  ].map(([value, label, description]) => {
    const input = h("input", { type: "radio", name: "proactivity", value, checked: prefs.proactivity === value });
    input.addEventListener("change", () => savePrefs({ proactivity: value }));
    return h("label", { class: "choice" }, input, h("span", {}, label, h("small", {}, description)));
  });
  const name = status.brain.label;
  return section("ai", "AI",
    aiControls({ onSaved: () => openSettings("ai") }),
    usage.requests ? h("p", { class: "muted small" }, `Since the server started: ${usage.requests} requests, ${(usage.input_tokens || 0).toLocaleString()} input and ${(usage.output_tokens || 0).toLocaleString()} output tokens.`) : null,
    h("fieldset", {}, h("legend", {}, "When should Magpie remind you?"), choices),
    field("pref-about", `About you (helps ${name} choose good times and context)`, about),
    field("pref-instructions", "Your instructions", rules, `Standing instructions ${name} follows when it files notes, writes digests and answers questions.`),
    h("div", { class: "field" }, h("span", { style: "font-weight:600" }, "Learning"), learningControls()),
    status.brain.web_search
      ? toggle("pref-websearch", "Let Claude search the web for videos and articles you mention without a link", prefs.web_search, (on) => savePrefs({ web_search: on }), {
          disabled: !status.web_search,
          hint: status.web_search ? null : "Web search is turned off on the server (MAGPIE_WEB_SEARCH=false).",
        })
      : null,
  );
}

function categoriesSection() {
  return section("categories", "Categories",
    h("p", { class: "muted" }, "Your own categories, in your colours. The AI files notes into them, notes you write in a category's tab stay there, and you can drag notes onto a tab."),
    categoriesEditor(),
  );
}

function timeSection() {
  const prefs = state.prefs;
  const zones = typeof Intl.supportedValuesOf === "function" ? Intl.supportedValuesOf("timeZone") : [];
  if (!zones.includes("UTC")) zones.unshift("UTC");
  let zone;
  if (zones.length > 1) {
    zone = h("select", { id: "pref-tz" }, zones.map((z) => h("option", { value: z, selected: z === prefs.timezone }, z.replaceAll("_", " "))));
    zone.addEventListener("change", () => savePrefs({ timezone: zone.value }, "Time zone saved."));
  } else {
    zone = h("input", { type: "text", id: "pref-tz", value: prefs.timezone });
    zone.addEventListener("change", () => savePrefs({ timezone: zone.value.trim() }, "Time zone saved."));
  }
  const device = deviceTimeZone();
  const morning = h("input", { type: "time", id: "pref-morning", value: prefs.morning_time });
  morning.addEventListener("change", () => morning.value && savePrefs({ morning_time: morning.value }));
  const lengths = [5, 10, 15, 30, 60, 120, 180, 1440];
  if (!lengths.includes(prefs.snooze_minutes)) lengths.push(prefs.snooze_minutes);
  const snooze = h("select", { id: "pref-snooze" },
    lengths.sort((a, b) => a - b).map((m) => h("option", { value: m, selected: m === prefs.snooze_minutes }, snoozeLabel(m))));
  snooze.addEventListener("change", () => {
    const minutes = Number(snooze.value);
    savePrefs({ snooze_minutes: minutes }, `Snooze is now ${snoozeLabel(minutes)}.`);
  });
  return section("time", "Time",
    field("pref-tz", "Your time zone", zone, "Reminders and digests use this zone."),
    toggle("local-followzone", "Follow this computer's time zone automatically", local.followDeviceZone !== false, (on) => saveLocal({ followDeviceZone: on }), {
      hint: "When this computer's zone changes (you travel, or the clocks were wrong), Magpie switches to it.",
    }),
    device !== prefs.timezone
      ? h("button", { type: "button", class: "btn small", onclick: async () => { await savePrefs({ timezone: device }, `Using ${device}.`); openSettings("time"); } }, `Use this device's zone (${device})`)
      : null,
    field("pref-morning", "Your morning", morning, "Used for “tomorrow morning” and the Tomorrow button."),
    field("pref-snooze", "Snooze for", snooze, "Used by every Snooze button: here, in Telegram, push, ntfy and email."),
  );
}

function voiceSection() {
  const prefs = state.prefs;
  const accents = ACCENTS.some(([value]) => value === prefs.speech_lang) ? ACCENTS : [...ACCENTS, [prefs.speech_lang, prefs.speech_lang]];
  const lang = h("select", { id: "pref-lang" }, accents.map(([value, label]) => h("option", { value, selected: value === prefs.speech_lang }, label)));
  lang.addEventListener("change", () => savePrefs({ speech_lang: lang.value }, "Accent saved."));
  const wake = h("input", { type: "text", id: "pref-wake", value: prefs.wake_phrase, dir: "auto" });
  wake.addEventListener("change", () => {
    if (wake.value.trim()) return savePrefs({ wake_phrase: wake.value.trim() });
    wake.value = prefs.wake_phrase;
    toast("The wake phrase can't be empty.", { error: true });
  });
  const stop = h("input", { type: "text", id: "pref-stop", value: prefs.stop_phrase, dir: "auto" });
  stop.addEventListener("change", () => {
    if (stop.value.trim()) return savePrefs({ stop_phrase: stop.value.trim() });
    stop.value = prefs.stop_phrase;
    toast("The stop phrase can't be empty.", { error: true });
  });
  const silence = h("input", { type: "number", id: "pref-silence", min: 0, max: 120, value: prefs.silence_seconds, inputmode: "numeric" });
  silence.addEventListener("change", () => savePrefs({ silence_seconds: Number(silence.value) || 0 }));
  const stt = state.status.stt.enabled;
  const inApp = desktopApp();
  const listen = async (on) => {
    try {
      await api("/api/desktop/command", { method: "POST", body: { action: on ? "listen" : "pause" } });
      toast(on ? "The Magpie app is listening for the command word." : "The Magpie app stopped listening.");
      await refreshStatus().catch(() => {});
    } catch (error) {
      toast(error.message, { error: true });
    }
  };
  return section("voice", "Voice",
    inApp
      ? toggle("app-listen", "Always listen for the command word", state.status.desktop.listening !== false, listen, {
          hint: "The Magpie app listens on this computer, even when this window is closed. Nothing you say leaves the computer until a note is saved.",
        })
      : null,
    inApp ? speechControls() : null,
    !inApp && !speechSupported
      ? h("p", { class: "banner" }, icon("alert"), h("span", {}, stt
          ? "This browser can't recognise speech itself, so Magpie records audio and the server transcribes it."
          : "This browser can't recognise speech. Use Chrome, Edge or Safari, set up server transcription, or type."))
      : null,
    // The app's own listening is the same for every accent (and Whisper hears them all).
    inApp ? null : field("pref-lang", "Your accent", lang, "Magpie listens for English. Choosing your accent helps it hear you correctly."),
    field("pref-wake", "Wake phrase (hands-free)", wake, "Say it to start a note when hands-free is on."),
    field("pref-stop", "Stop phrase", stop, "Say it to save the note. “End note” also works, and “cancel note” throws it away."),
    field("pref-silence", "Save after this many seconds of silence (0 = never)", silence),
    inApp
      ? null
      : toggle("local-review", "Let me check voice notes before saving", local.reviewVoice, (on) => saveLocal({ reviewVoice: on }), {
          hint: "Voice text goes into the text box so you can fix it, then press Save.",
        }),
    recordingSupported && !inApp
      ? toggle("local-serverstt", "Use the server's speech-to-text instead of the browser's", local.serverStt, (on) => saveLocal({ serverStt: on }), {
          disabled: !stt,
          hint: stt ? "Often more accurate, especially with strong accents; the browser sends audio to your Magpie server." : "Set MAGPIE_STT_API_KEY on the server to enable this.",
        })
      : null,
    ttsSupported
      ? toggle("local-readaloud", "Read replies aloud", local.readAloud, (on) => saveLocal({ readAloud: on }))
      : null,
  );
}

// How the Magpie app writes notes down (desktop/transcribe.py): one of Whisper's models,
// downloaded once, or the quick listener's own words.
function speechControls() {
  const speech = state.status.desktop.speech;
  if (!speech) return null;
  const options = { ...(speech.models || {}), off: "Off: the listener's own words (quick, but often wrong)" };
  const chosen = state.prefs.speech_model || "large-v3-turbo";
  const select = h("select", { id: "pref-speech", "aria-describedby": "pref-speech-hint" },
    Object.entries(options).map(([value, label]) => h("option", { value, selected: value === chosen }, label)));
  select.addEventListener("change", () => savePrefs({ speech_model: select.value }, select.value === "off"
    ? "Notes now keep the words shown as you speak."
    : "Saved. Magpie downloads the model if it needs to, and uses it as soon as it's ready."));
  return field("pref-speech", "Writing down what you say", select, speechStatus(speech));
}

function speechStatus(speech) {
  if (!speech) return "";
  const done = `${Math.round((speech.progress || 0) * 100)}%`;
  if (speech.state === "ready") {
    const ready = "Ready. Whisper writes your words in the bar as you speak, then each note again when you finish it, all on this computer: no audio leaves it.";
    return speech.getting ? `${ready} Getting the model for the words shown as you speak: ${done}.` : ready;
  }
  if (speech.state === "downloading") {
    if (speech.getting && speech.getting !== speech.model) return `Downloading the faster model first: ${done}. Until it's there, notes keep the listener's rough words.`;
    if (speech.meanwhile) return `Downloading this model: ${done}. Until it's ready, Magpie uses the faster one.`;
    return `Downloading the model: ${done}. Until it's ready, notes keep the listener's rough words.`;
  }
  if (speech.state === "off") return "Notes keep the words the listener heard: quick, but often wrong.";
  return speech.message || "";
}

on("desktop", (desktop) => {
  const hint = document.getElementById("pref-speech-hint");
  if (hint) hint.textContent = speechStatus(desktop?.speech);
  const update = desktop?.update;
  const line = document.getElementById("update-status");
  if (line && update) line.textContent = updateStatus(update);
  const now = document.getElementById("update-now");
  if (now && update) now.hidden = update.state !== "ready";
});

// The update button: the app updates itself, and this checks or installs right away.
function updatesSection() {
  const update = desktopApp() ? state.status.desktop.update : null;
  if (!update) return null;
  const run = async (action, message) => {
    try {
      await api("/api/desktop/command", { method: "POST", body: { action } });
      toast(message);
      await refreshStatus().catch(() => {});
    } catch (error) {
      toast(error.message, { error: true });
    }
  };
  return section("updates", "Updates",
    h("p", { class: "small" }, `This is Magpie ${update.version}. It keeps itself up to date: a new version installs by itself when you're not taking a note.`),
    h("p", { class: "muted small", id: "update-status", role: "status" }, updateStatus(update)),
    update.supported
      ? h("div", { class: "row" },
          h("button", { type: "button", class: "btn", onclick: () => run("check_update", "Checking for updates…") }, icon("refresh"), "Check for updates"),
          h("button", { type: "button", class: "btn primary", id: "update-now", hidden: update.state !== "ready", onclick: () => run("install_update", "Updating: Magpie restarts in a moment.") }, icon("download"), "Update now"))
      : null,
  );
}

function updateStatus(update) {
  const when = update.checked ? new Date(update.checked * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : "";
  if (update.state === "downloading") return `Downloading Magpie ${update.available}: ${Math.round((update.progress || 0) * 100)}%`;
  if (update.state === "current") return `${update.message} Checked at ${when}.`;
  if (update.message) return update.message;
  return "Magpie looks for a new version every few hours.";
}

function notificationsSection() {
  const status = state.status;
  const prefs = state.prefs;
  const rows = status.channels.map((channel) => {
    const extra = [];
    if (channel.name === "telegram") extra.push(telegramControls());
    if (channel.name === "desktop" && channel.configured) extra.push(h("div", { style: "flex-basis:100%" }, appPopupControls()));
    if (channel.name === "webpush") {
      extra.push(h("div", { style: "flex-basis:100%" }, desktopApp()
        ? h("p", { class: "muted small" }, "Not needed with the Magpie app, which shows reminders itself.")
        : pushControls({ onSaved: () => openSettings("notify") })));
    }
    if (channel.name === "email") extra.push(h("div", { style: "flex-basis:100%" }, emailControls({ onSaved: () => openSettings("notify") })));
    if (!channel.configured && CHANNEL_HELP[channel.name] && !(channel.name === "telegram" && status.telegram.configured)) {
      extra.push(h("p", { class: "muted small", style: "flex-basis:100%;margin:0" }, CHANNEL_HELP[channel.name]));
    }
    const enabled = h("input", {
      type: "checkbox",
      role: "switch",
      id: `channel-${channel.name}`,
      checked: channel.enabled && channel.configured,
      disabled: !channel.configured,
    });
    enabled.addEventListener("change", () => {
      const channels = { ...(state.prefs.channels || {}), [channel.name]: enabled.checked };
      savePrefs({ channels }, enabled.checked ? `${channel.label} on.` : `${channel.label} off.`);
    });
    return h("div", { class: "channel" },
      h("span", { class: "name" }, h("span", { class: `status-dot${channel.configured ? " on" : ""}`, "aria-hidden": "true" }), channel.label,
        h("span", { class: "sr-only" }, channel.configured ? " (set up)" : " (not set up)")),
      h("label", { class: "switch", for: `channel-${channel.name}` }, enabled, h("span", { class: "switch-track", "aria-hidden": "true" }), h("span", { class: "sr-only" }, `Send to ${channel.label}`)),
      h("button", { type: "button", class: "btn small", disabled: !channel.configured, onclick: () => testChannel(channel) }, "Test"),
      ...extra,
    );
  });

  const digestTime = h("input", { type: "time", id: "pref-digest", value: prefs.digest_time });
  digestTime.addEventListener("change", () => savePrefs({ digest_time: digestTime.value || "" }));
  const weeklyDay = h("select", { id: "pref-weekly-day" }, DAYS.map(([value, label]) => h("option", { value, selected: value === prefs.weekly_review_day }, label)));
  weeklyDay.addEventListener("change", () => savePrefs({ weekly_review_day: weeklyDay.value }));
  const weeklyTime = h("input", { type: "time", id: "pref-weekly-time", value: prefs.weekly_review_time });
  weeklyTime.addEventListener("change", () => weeklyTime.value && savePrefs({ weekly_review_time: weeklyTime.value }));

  return section("notify", "Reminders",
    h("p", { class: "muted" }, "Reminders and digests go to every way that is set up and switched on: pop-ups on this computer, email for your phone, or both."),
    rows,
    field("pref-digest", "Daily digest at", digestTime, "Clear the time to turn the daily digest off."),
    h("div", { class: "row" },
      field("pref-weekly-day", "Weekly review on", weeklyDay),
      field("pref-weekly-time", "at", weeklyTime),
    ),
  );
}

async function testChannel(channel) {
  try {
    const result = await api("/api/notify/test", { method: "POST", body: { channel: channel.name } });
    const outcome = result.results[channel.name];
    if (outcome === "ok") toast(`Sent a test to ${channel.label}.`);
    else toast(`${channel.label}: ${outcome || "not set up"}`, { error: true });
  } catch (error) {
    toast(error.message, { error: true });
  }
}

function telegramControls() {
  const tg = state.status.telegram;
  const box = h("div", { style: "flex-basis:100%" });
  if (!tg.configured) return box;
  if (tg.paired) {
    box.append(
      h("p", { class: "small" }, "Connected", tg.bot ? ` to @${tg.bot}` : "", ". Send it text or voice notes any time."),
      h("button", {
        type: "button", class: "btn small", onclick: async () => {
          await api("/api/telegram/pair", { method: "DELETE" });
          await refreshStatus();
          openSettings("notify");
        },
      }, "Disconnect"),
    );
    return box;
  }
  const button = h("button", { type: "button", class: "btn small primary" }, "Connect Telegram");
  button.addEventListener("click", async () => {
    try {
      const pair = await api("/api/telegram/pair", { method: "POST" });
      clear(box);
      append(box, [
        h("p", { class: "small" }, "Open your bot in Telegram and tap Start, or send it this message:"),
        h("p", {}, h("code", { style: "font-size:1.2rem" }, `/start ${pair.code}`)),
        pair.link ? h("a", { class: "btn small primary", href: pair.link, target: "_blank", rel: "noopener" }, icon("send"), `Open @${pair.bot}`) : null,
        h("p", { class: "muted small" }, "Waiting for Telegram… the code works for 15 minutes."),
      ]);
      waitForPairing();
    } catch (error) {
      toast(error.message, { error: true });
    }
  });
  box.append(button);
  return box;
}

let pairingTimer = null;
function waitForPairing() {
  clearInterval(pairingTimer);
  const started = Date.now();
  pairingTimer = setInterval(async () => {
    if (Date.now() - started > 15 * 60 * 1000) return clearInterval(pairingTimer);
    try {
      const status = await api("/api/status");
      if (status.telegram.paired) {
        clearInterval(pairingTimer);
        state.status = status;
        emit("status", status);
        toast("Telegram connected!");
        if (dialog().open) openSettings("notify");
      }
    } catch {
      // keep waiting
    }
  }, 3000);
}

function displaySection() {
  return section("display", "Display & accessibility",
    segmented("Theme", [["system", "System"], ["light", "Light"], ["dark", "Dark"]], local.theme, (theme) => saveLocal({ theme })),
    segmented("Text size", [[1, "A", "Normal text"], [1.125, "A+", "Large text"], [1.25, "A++", "Larger text"], [1.5, "A+++", "Largest text"]], local.scale, (scale) => saveLocal({ scale })),
    toggle("local-contrast", "High contrast", local.contrast === "high", (on) => saveLocal({ contrast: on ? "high" : "normal" })),
    toggle("local-motion", "Reduce motion", local.motion === "reduce", (on) => saveLocal({ motion: on ? "reduce" : "system" })),
    h("p", { class: "muted small" }, "Everything works with a keyboard and screen readers. ",
      h("button", { type: "button", class: "linklike", onclick: () => emit("shortcuts") }, "See keyboard shortcuts"), "."),
  );
}

async function integrationsSection() {
  const origin = location.origin;
  const token = await api("/api/token");
  const calendar = await api("/api/calendar");

  const tokenInput = h("input", { type: "password", id: "api-token", readonly: true, value: token.token });
  const show = h("button", { type: "button", class: "btn small" }, "Show");
  show.addEventListener("click", () => {
    tokenInput.type = tokenInput.type === "password" ? "text" : "password";
    show.textContent = tokenInput.type === "password" ? "Show" : "Hide";
  });
  const rotate = h("button", { type: "button", class: "btn small", disabled: token.from_env }, "New token");
  rotate.addEventListener("click", async () => {
    const ok = await confirmDialog({ title: "Make a new API token?", message: "The current token stops working at once: Siri Shortcuts, scripts and the MCP server need the new one.", confirm: "Make a new token", danger: true });
    if (!ok) return;
    try {
      const next = await api("/api/token/rotate", { method: "POST" });
      tokenInput.value = next.token;
      toast("New token created. Update your Shortcuts and integrations.");
    } catch (error) {
      toast(error.message, { error: true });
    }
  });

  const calInput = h("input", { type: "text", id: "calendar-url", readonly: true, value: calendar.url });
  const calRotate = h("button", { type: "button", class: "btn small" }, "New link");
  calRotate.addEventListener("click", async () => {
    const ok = await confirmDialog({ title: "Make a new calendar link?", message: "Calendars subscribed to the current link stop updating until you subscribe again with the new one.", confirm: "Make a new link", danger: true });
    if (!ok) return;
    try {
      const next = await api("/api/calendar/rotate", { method: "POST" });
      calInput.value = next.url;
      toast("New calendar link. The old one stopped working.");
    } catch (error) {
      toast(error.message, { error: true });
    }
  });

  const bookmarklet = `javascript:(()=>{const s=String(getSelection());window.open('${origin}/share?source=bookmarklet&url='+encodeURIComponent(location.href)+'&title='+encodeURIComponent(document.title)+(s?'&text='+encodeURIComponent(s):''),'magpie','width=440,height=560')})();`;
  const mcpConfig = JSON.stringify({
    mcpServers: {
      magpie: {
        command: "uvx",
        args: ["--from", `magpie-notes[mcp] @ git+${REPO}`, "magpie", "mcp"],
        env: { MAGPIE_URL: origin, MAGPIE_API_TOKEN: "paste your API token" },
      },
    },
  }, null, 2);

  return section("integrations", "Integrations",
    field("api-token", "API token (for Siri Shortcuts, scripts and Claude Desktop)", h("div", { class: "copy-field" }, tokenInput, show,
      h("button", { type: "button", class: "btn small", onclick: () => copyText(tokenInput.value, "Token copied.") }, "Copy"), rotate),
      token.from_env ? "Set by MAGPIE_API_TOKEN on the server." : "Keep it secret: it can read and add notes."),
    h("div", { class: "field" },
      h("span", { style: "font-weight:600" }, "“Hey Siri, notes on”"),
      h("p", { class: "muted small", style: "margin:0" }, "Make an iPhone Shortcut named “Notes on”: Dictate Text → Get Contents of URL (POST ", h("code", {}, `${origin}/api/notes?wait=true`), " with your token) → Speak the reply. ",
        h("a", { href: `${REPO}/blob/main/docs/integrations.md#iphone-and-ipad-hey-siri-notes-on`, target: "_blank", rel: "noopener" }, "Step-by-step guide"), "."),
    ),
    h("div", { class: "field" },
      h("span", { style: "font-weight:600" }, "Save any page"),
      h("p", { class: "muted small", style: "margin:0" }, "Drag this button to your bookmarks bar. Click it on any page to save it (and any selected text) to Magpie."),
      h("p", {}, h("a", { class: "bookmarklet", href: bookmarklet, onclick: (event) => { event.preventDefault(); toast("Drag the button to your bookmarks bar."); } }, icon("plus"), "Save to Magpie")),
    ),
    field("calendar-url", "Calendar feed of your reminders", h("div", { class: "copy-field" }, calInput,
      h("button", { type: "button", class: "btn small", onclick: () => copyText(calInput.value, "Calendar link copied.") }, "Copy"), calRotate),
      "Subscribe in Google Calendar (Other calendars → From URL), Apple Calendar or Outlook. Anyone with the link can see your reminders."),
    h("div", { class: "field" },
      h("span", { style: "font-weight:600" }, "Use your notes from Claude Desktop or Claude Code (MCP)"),
      h("pre", { class: "snippet-block", tabindex: "0", "aria-label": "Claude Desktop configuration" }, mcpConfig),
      h("button", { type: "button", class: "btn small", onclick: () => copyText(mcpConfig, "Config copied.") }, icon("copy"), "Copy config"),
    ),
    h("p", { class: "small" }, h("a", { href: "/api/docs", target: "_blank", rel: "noopener" }, "API reference"), " · ",
      h("a", { href: `${REPO}/blob/main/docs/integrations.md`, target: "_blank", rel: "noopener" }, "All integrations")),
  );
}

function dataSection() {
  const file = h("input", { type: "file", id: "import-file", accept: "application/json,.json" });
  file.addEventListener("change", async () => {
    if (!file.files?.length) return;
    const form = new FormData();
    form.append("file", file.files[0]);
    try {
      const result = await api("/api/import", { method: "POST", form });
      toast(`Imported ${result.imported} notes${result.skipped ? `, skipped ${result.skipped}` : ""}.`);
      emit("refresh");
    } catch (error) {
      toast(error.message, { error: true });
    }
    file.value = "";
  });
  return section("data", "Your data",
    h("p", { class: "muted" }, "Your notes live on your own server. Take them anywhere."),
    h("div", { class: "row" },
      h("a", { class: "btn small", href: "/api/export?format=json", download: "" }, icon("download"), "Export JSON"),
      h("a", { class: "btn small", href: "/api/export?format=markdown", download: "" }, icon("download"), "Export Markdown"),
    ),
    field("import-file", "Import a Magpie JSON export", file),
  );
}

function accountSection() {
  const login = state.status.login !== false;
  return section("account", "Privacy & account",
    login
      ? null
      : h("p", { class: "small" }, "Magpie has no password: it opens straight away, on this computer only. Your notes stay on this computer."),
    login && state.status.password_generated
      ? h("p", { class: "banner" }, icon("alert"), h("span", {}, "This Magpie still uses the password an older version made up. Run the setup again to stop using it: Magpie then opens straight away, on this computer only."))
      : null,
    h("p", { class: "muted small" }, `Magpie ${state.status.version} · `, h("a", { href: REPO, target: "_blank", rel: "noopener" }, "Source code")),
    h("div", { class: "row" },
      h("button", { type: "button", class: "btn", onclick: () => { dialog().close(); emit("welcome"); } }, icon("sparkles"), "Show the welcome screen again"),
      login
        ? h("button", {
            type: "button", class: "btn", onclick: async () => {
              try {
                await api("/api/logout", { method: "POST" });
                location.href = "/";
              } catch (error) {
                toast(`Couldn't log out: ${error.message}`, { error: true });
              }
            },
          }, icon("logout"), "Log out")
        : null),
  );
}

export async function openSettings(focusSection) {
  const el = dialog();
  const wasOpen = el.open;
  const scroll = el.querySelector(".sheet-body")?.scrollTop || 0;
  clear(el);
  const sections = [
    ["ai", "AI"], ["notify", "Reminders"], ["categories", "Categories"], ["voice", "Voice"], ["time", "Time"],
    ["display", "Display"], ["integrations", "Integrations"], ["data", "Data"],
    ...(desktopApp() && state.status.desktop.update ? [["updates", "Updates"]] : []), ["account", "Privacy"],
  ];
  const bodyEl = h("div", { class: "sheet-body" },
    h("nav", { class: "settings-nav", "aria-label": "Settings sections" },
      sections.map(([id, label]) => h("a", { class: "tab", href: `#settings-${id}`, onclick: (event) => {
        event.preventDefault();
        el.querySelector(`#settings-${id}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
      } }, label)),
    ),
    aiSection(),
    notificationsSection(),
    categoriesSection(),
    voiceSection(),
    timeSection(),
    displaySection(),
    h("div", { id: "integrations-slot" }, h("p", { class: "muted" }, "Loading integrations…")),
    dataSection(),
    updatesSection(),
    accountSection(),
  );
  el.append(
    h("div", { class: "sheet-inner" },
      h("div", { class: "sheet-head" }, h("h2", { id: "settings-title" }, "Settings"), iconButton("close", "Close settings", () => el.close())),
      bodyEl,
    ),
  );
  if (!wasOpen) el.showModal();
  integrationsSection()
    .then((node) => el.querySelector("#integrations-slot")?.replaceWith(node))
    .catch((error) => toast(error.message, { error: true }));
  if (focusSection) {
    const target = focusSection === "claude" ? "ai" : focusSection; // older links
    requestAnimationFrame(() => el.querySelector(`#settings-${target}`)?.scrollIntoView({ block: "start" }));
  } else if (wasOpen) {
    bodyEl.scrollTop = scroll;
  } else {
    el.querySelector(".sheet-head .icon-btn").focus();
  }
}

dialog().addEventListener("click", (event) => {
  if (event.target === dialog()) dialog().close();
});
dialog().addEventListener("close", () => {
  clearInterval(pairingTimer);
  emit("settings-closed");
});
