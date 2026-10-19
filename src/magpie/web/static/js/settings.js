// Settings: account-wide preferences (saved on the server) and device
// preferences (saved in this browser).

import { api } from "./api.js";
import { deviceTimeZone, emit, local, saveLocal, snoozeLabel, state } from "./state.js";
import { speechSupported, recordingSupported } from "./voice.js";
import { ttsSupported } from "./speech.js";
import { append, clear, confirmDialog, copyText, debounce, h, icon, iconButton, toast } from "./ui.js";

const dialog = () => document.getElementById("settings-dialog");
const REPO = "https://github.com/Coder-ux-coder/magpie";

const LANGUAGES = [
  ["", "Browser default"],
  ["en-US", "English (US)"],
  ["en-GB", "English (UK)"],
  ["en-IN", "English (India)"],
  ["en-PK", "English (Pakistan)"],
  ["ur-PK", "اردو Urdu"],
  ["hi-IN", "हिन्दी Hindi"],
  ["pa-IN", "ਪੰਜਾਬੀ Punjabi"],
  ["bn-BD", "বাংলা Bengali"],
  ["ar-SA", "العربية Arabic"],
  ["fa-IR", "فارسی Persian"],
  ["tr-TR", "Türkçe Turkish"],
  ["es-ES", "Español (España)"],
  ["es-MX", "Español (México)"],
  ["fr-FR", "Français"],
  ["de-DE", "Deutsch"],
  ["it-IT", "Italiano"],
  ["pt-BR", "Português (Brasil)"],
  ["ru-RU", "Русский"],
  ["id-ID", "Bahasa Indonesia"],
  ["sw-KE", "Kiswahili"],
  ["zh-CN", "中文 (普通话)"],
  ["ja-JP", "日本語"],
  ["ko-KR", "한국어"],
];
const DAYS = [["", "Off"], ["mon", "Monday"], ["tue", "Tuesday"], ["wed", "Wednesday"], ["thu", "Thursday"], ["fri", "Friday"], ["sat", "Saturday"], ["sun", "Sunday"]];
const CHANNEL_HELP = {
  telegram: "Create a bot with @BotFather, set MAGPIE_TELEGRAM_BOT_TOKEN and restart Magpie.",
  webpush: "Turn on notifications for this browser below. On iPhone, add Magpie to your Home Screen first.",
  ntfy: "Install the ntfy app, pick a secret topic name and set MAGPIE_NTFY_TOPIC.",
  email: "Set MAGPIE_SMTP_HOST, MAGPIE_SMTP_USERNAME, MAGPIE_SMTP_PASSWORD and MAGPIE_EMAIL_TO.",
  webhook: "Set MAGPIE_WEBHOOK_URL (Slack, Discord, or any URL that accepts JSON).",
};

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

function claudeKeyControls() {
  const brain = state.status.brain;
  if (brain.name !== "claude") {
    return h("p", { class: "muted small" }, "Magpie is using its offline stand-in (MAGPIE_BRAIN=fake). Remove that setting on the server to use Claude.");
  }
  if (brain.key_source === "env") {
    return brain.available ? null : h("p", { class: "error" }, "Anthropic rejected the key in ANTHROPIC_API_KEY. Fix it on the server and restart Magpie.");
  }
  const input = h("input", { type: "password", id: "claude-key", autocomplete: "off", spellcheck: "false", placeholder: brain.key_source ? "Paste a new key to replace it" : "sk-ant-…" });
  const save = h("button", { type: "submit", class: "btn primary" }, "Save key");
  const form = h("form", { class: "copy-field" }, input, save);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const key = input.value.trim();
    if (!key) return;
    save.disabled = true;
    save.textContent = "Checking…";
    try {
      const result = await api("/api/claude/key", { method: "PUT", body: { key }, timeout: 60000 });
      toast(result.warning || "Claude is connected. Waiting notes are being filed now.");
      await refreshStatus();
      openSettings("claude");
    } catch (error) {
      toast(error.message, { error: true });
      save.disabled = false;
      save.textContent = "Save key";
    }
  });
  const remove = async () => {
    const ok = await confirmDialog({ title: "Remove the API key?", message: "Claude stops filing new notes until you add a key again. Your notes stay.", confirm: "Remove", danger: true });
    if (!ok) return;
    try {
      await api("/api/claude/key", { method: "DELETE" });
      await refreshStatus();
      openSettings("claude");
    } catch (error) {
      toast(error.message, { error: true });
    }
  };
  return h("div", { class: "field" },
    h("label", { for: "claude-key" }, brain.key_source ? "Replace your API key" : "Your Claude API key"),
    form,
    h("span", { class: "hint" }, "Get one at ", h("a", { href: "https://console.anthropic.com/settings/keys", target: "_blank", rel: "noopener" }, "console.anthropic.com"), ". It is checked with Anthropic, then kept on your Magpie server only."),
    brain.key_source === "dashboard" ? h("div", {}, h("button", { type: "button", class: "btn small danger", onclick: remove }, "Remove key")) : null,
  );
}

function claudeSection() {
  const status = state.status;
  const prefs = state.prefs;
  const usage = status.brain.usage || {};
  const about = h("textarea", { id: "pref-about", rows: 3, dir: "auto", maxlength: 2000, placeholder: "e.g. I work 9 to 5 in Lahore. My manager is Ahmed. I have two kids at school." });
  about.value = prefs.about_me || "";
  about.addEventListener("input", () => savePrefsSoon({ about_me: about.value }));
  const rules = h("textarea", { id: "pref-instructions", rows: 3, dir: "auto", maxlength: 2000, placeholder: "e.g. Tag anything about the shop with #shop. Remind me 30 minutes before meetings. Don't ping me after 10 pm. Keep titles in Urdu." });
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
  return section("claude", "Claude",
    h("p", {},
      h("span", { class: `status-dot${status.brain.available ? " on" : ""}`, "aria-hidden": "true" }),
      status.brain.available ? `Connected: ${status.brain.model}` : "Not connected yet.",
    ),
    claudeKeyControls(),
    usage.requests ? h("p", { class: "muted small" }, `Since the server started: ${usage.requests} requests, ${(usage.input_tokens || 0).toLocaleString()} input and ${(usage.output_tokens || 0).toLocaleString()} output tokens.`) : null,
    h("fieldset", {}, h("legend", {}, "When should Claude ping you?"), choices),
    field("pref-about", "About you (helps Claude choose good times and context)", about),
    field("pref-instructions", "Your rules for Claude", rules, "Standing instructions Claude follows when it files notes, writes digests and answers questions."),
    toggle("pref-websearch", "Let Claude search the web for videos and articles you mention without a link", prefs.web_search, (on) => savePrefs({ web_search: on }), {
      disabled: !status.web_search,
      hint: status.web_search ? null : "Web search is turned off on the server (MAGPIE_WEB_SEARCH=false).",
    }),
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
    device !== prefs.timezone
      ? h("button", { type: "button", class: "btn small", onclick: async () => { await savePrefs({ timezone: device }, `Using ${device}.`); openSettings("time"); } }, `Use this device's zone (${device})`)
      : null,
    field("pref-morning", "Your morning", morning, "Used for “tomorrow morning” and the Tomorrow button."),
    field("pref-snooze", "Snooze for", snooze, "Used by every Snooze button: here, in Telegram, push, ntfy and email."),
  );
}

function voiceSection() {
  const prefs = state.prefs;
  const lang = h("select", { id: "pref-lang" }, LANGUAGES.map(([value, label]) => h("option", { value, selected: value === prefs.speech_lang }, label)));
  lang.addEventListener("change", () => savePrefs({ speech_lang: lang.value }, "Voice language saved."));
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
  return section("voice", "Voice",
    !speechSupported
      ? h("p", { class: "banner" }, icon("alert"), h("span", {}, stt
          ? "This browser can't recognise speech itself, so Magpie records audio and the server transcribes it."
          : "This browser can't recognise speech. Use Chrome, Edge or Safari, set up server transcription, or type."))
      : null,
    field("pref-lang", "Language you speak", lang, "Claude keeps your notes in the language you use."),
    field("pref-wake", "Wake phrase (hands-free)", wake, "Say it to start a note when hands-free is on."),
    field("pref-stop", "Stop phrase", stop, "Say it to save the note. “End note” also works, and “cancel note” throws it away."),
    field("pref-silence", "Save after this many seconds of silence (0 = never)", silence),
    toggle("local-review", "Let me check voice notes before saving", local.reviewVoice, (on) => saveLocal({ reviewVoice: on }), {
      hint: "Voice text goes into the text box so you can fix it, then press Save.",
    }),
    recordingSupported
      ? toggle("local-serverstt", "Use the server's speech-to-text instead of the browser's", local.serverStt, (on) => saveLocal({ serverStt: on }), {
          disabled: !stt,
          hint: stt ? "Often more accurate for mixed languages; the browser sends audio to your Magpie server." : "Set MAGPIE_STT_API_KEY on the server to enable this.",
        })
      : null,
    ttsSupported
      ? toggle("local-readaloud", "Read Claude's replies aloud", local.readAloud, (on) => saveLocal({ readAloud: on }))
      : null,
  );
}

function notificationsSection() {
  const status = state.status;
  const prefs = state.prefs;
  const rows = status.channels.map((channel) => {
    const extra = [];
    if (channel.name === "telegram") extra.push(telegramControls());
    if (channel.name === "webpush") extra.push(pushControls());
    if (!channel.configured && channel.name !== "webpush" && !(channel.name === "telegram" && status.telegram.configured)) {
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

  return section("notify", "Notifications",
    h("p", { class: "muted" }, "Reminders and digests go to every channel that is set up and switched on."),
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

function urlBase64ToUint8Array(base64) {
  const padding = "=".repeat((4 - (base64.length % 4)) % 4);
  const raw = atob((base64 + padding).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from([...raw].map((c) => c.charCodeAt(0)));
}

function pushControls() {
  const box = h("div", { style: "flex-basis:100%" });
  const supported = "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
  if (!supported) {
    box.append(h("p", { class: "muted small" }, "This browser doesn't support push. On iPhone and iPad, add Magpie to your Home Screen, then open it from there."));
    return box;
  }
  const count = state.status.push.subscriptions;
  const enable = h("button", { type: "button", class: "btn small primary" }, icon("bell"), "Notify this device");
  enable.addEventListener("click", async () => {
    try {
      const permission = await Notification.requestPermission();
      if (permission !== "granted") {
        toast("Notifications are blocked for this site. Allow them in your browser settings.", { error: true });
        return;
      }
      const registration = await navigator.serviceWorker.ready;
      const subscription = await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(state.status.push.public_key),
      });
      await api("/api/push/subscribe", { method: "POST", body: subscription.toJSON() });
      toast("This device will get reminders.");
      await refreshStatus();
      openSettings("notify");
    } catch (error) {
      toast(error.message || "Couldn't turn on notifications.", { error: true });
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
      toast("This device won't get push notifications.");
      await refreshStatus();
      openSettings("notify");
    } catch (error) {
      toast(error.message, { error: true });
    }
  });
  box.append(
    h("p", { class: "muted small" }, count ? `${count} device${count > 1 ? "s" : ""} subscribed.` : "No devices subscribed yet."),
    h("div", { class: "row" }, enable, disable),
  );
  return box;
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
  return section("account", "Account",
    state.status.password_generated
      ? h("p", { class: "banner" }, icon("alert"), h("span", {}, "You're using a generated password. Set MAGPIE_PASSWORD on the server to choose your own."))
      : null,
    h("p", { class: "muted small" }, `Magpie ${state.status.version} · `, h("a", { href: REPO, target: "_blank", rel: "noopener" }, "Source code")),
    h("button", {
      type: "button", class: "btn", onclick: async () => {
        try {
          await api("/api/logout", { method: "POST" });
          location.href = "/";
        } catch (error) {
          toast(`Couldn't log out: ${error.message}`, { error: true });
        }
      },
    }, icon("logout"), "Log out"),
  );
}

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

export async function openSettings(focusSection) {
  const el = dialog();
  const wasOpen = el.open;
  const scroll = el.querySelector(".sheet-body")?.scrollTop || 0;
  clear(el);
  const sections = [
    ["claude", "Claude"], ["time", "Time"], ["voice", "Voice"], ["notify", "Notifications"],
    ["display", "Display"], ["integrations", "Integrations"], ["data", "Data"], ["account", "Account"],
  ];
  const bodyEl = h("div", { class: "sheet-body" },
    h("nav", { class: "settings-nav", "aria-label": "Settings sections" },
      sections.map(([id, label]) => h("a", { class: "tab", href: `#settings-${id}`, onclick: (event) => {
        event.preventDefault();
        el.querySelector(`#settings-${id}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
      } }, label)),
    ),
    claudeSection(),
    timeSection(),
    voiceSection(),
    notificationsSection(),
    displaySection(),
    h("div", { id: "integrations-slot" }, h("p", { class: "muted" }, "Loading integrations…")),
    dataSection(),
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
    requestAnimationFrame(() => el.querySelector(`#settings-${focusSection}`)?.scrollIntoView({ block: "start" }));
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
