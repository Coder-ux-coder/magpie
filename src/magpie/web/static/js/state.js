// Shared state, an event bus, and per-device display preferences.

export const bus = new EventTarget();

export function emit(type, detail) {
  bus.dispatchEvent(new CustomEvent(type, { detail }));
}

export function on(type, handler) {
  bus.addEventListener(type, (event) => handler(event.detail));
}

export const state = {
  status: null,
  prefs: null,
  route: { view: "inbox", kind: null },
  q: "",
};

// Preferences that belong to this device rather than the account.
const LOCAL_KEY = "magpie.local.v1";
const DEFAULTS = {
  theme: "system", // system | light | dark
  scale: 1, // text size multiplier
  contrast: "normal", // normal | high
  motion: "system", // system | reduce
  readAloud: false, // speak Claude's replies
  handsFree: false, // listen for the wake phrase
  reviewVoice: false, // put voice text in the box instead of saving straight away
  serverStt: false, // record audio and let the server transcribe
  dismissed: {}, // banners the user closed
};

function readLocal() {
  try {
    return { ...DEFAULTS, ...JSON.parse(localStorage.getItem(LOCAL_KEY) || "{}") };
  } catch {
    return { ...DEFAULTS };
  }
}

export const local = readLocal();

export function saveLocal(changes) {
  Object.assign(local, changes);
  try {
    localStorage.setItem(LOCAL_KEY, JSON.stringify(local));
  } catch {
    // private mode or storage blocked: preferences last for this visit only
  }
  applyLocal();
}

export function applyLocal() {
  const root = document.documentElement;
  if (local.theme === "light" || local.theme === "dark") root.dataset.theme = local.theme;
  else delete root.dataset.theme;
  if (local.contrast === "high") root.dataset.contrast = "high";
  else delete root.dataset.contrast;
  if (local.motion === "reduce") root.dataset.motion = "reduce";
  else delete root.dataset.motion;
  root.style.setProperty("--scale", String(local.scale || 1));
}

export function deviceTimeZone() {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

export function tz() {
  return state.prefs?.timezone || deviceTimeZone();
}

export function speechLang() {
  return state.prefs?.speech_lang || navigator.language || "en-US";
}
