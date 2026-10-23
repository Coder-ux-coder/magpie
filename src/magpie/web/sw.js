// Magpie service worker: offline app shell and push notifications.

const CACHE = "magpie-shell-v6";
const SHELL = [
  "/",
  "/static/css/app.css",
  "/static/js/app.js",
  "/static/js/api.js",
  "/static/js/actions.js",
  "/static/js/capture.js",
  "/static/js/cards.js",
  "/static/js/controls.js",
  "/static/js/markdown.js",
  "/static/js/note-dialog.js",
  "/static/js/phrases.js",
  "/static/js/settings.js",
  "/static/js/speech.js",
  "/static/js/state.js",
  "/static/js/ui.js",
  "/static/js/views.js",
  "/static/js/voice.js",
  "/static/js/welcome.js",
  "/static/icons/sprite.svg",
  "/static/icons/logo.svg",
  "/static/icons/icon-192.png",
  "/manifest.webmanifest",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(SHELL)).catch(() => {}).then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))))
      .then(() => self.clients.claim()),
  );
});

// Network first, so updates show up immediately; the cache only covers being offline.
self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== "GET" || url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/a/") || url.pathname.startsWith("/calendar/")) return;
  // "no-cache" revalidates with the server, so an update is never hidden by the HTTP cache.
  const fresh = request.mode === "navigate"
    ? fetch(url.href, { cache: "no-cache", credentials: "same-origin" })
    : fetch(request, { cache: "no-cache" });
  event.respondWith(
    fresh
      .then((response) => {
        if (response.ok && (url.pathname.startsWith("/static/") || url.pathname === "/")) {
          const copy = response.clone();
          caches.open(CACHE).then((cache) => cache.put(request, copy));
        }
        return response;
      })
      .catch(async () => {
        const cached = await caches.match(request, { ignoreSearch: request.mode === "navigate" });
        if (cached) return cached;
        if (request.mode === "navigate") return caches.match("/");
        return Response.error();
      }),
  );
});

self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch {
    data = { title: "Magpie", body: event.data ? event.data.text() : "" };
  }
  if (!data || typeof data !== "object") data = { body: String(data ?? "") };
  const actions = Array.isArray(data.actions)
    ? data.actions.filter((a) => a && typeof a.action === "string" && typeof a.title === "string")
    : [];
  const options = {
    body: String(data.body || ""),
    icon: "/static/icons/icon-192.png",
    badge: "/static/icons/badge-96.png",
    tag: data.tag || undefined,
    renotify: Boolean(data.tag),
    requireInteraction: Boolean(data.tag),
    data: { url: typeof data.url === "string" ? data.url : "/", links: data.links && typeof data.links === "object" ? data.links : {} },
    actions: actions.slice(0, 2),
  };
  event.waitUntil(self.registration.showNotification(String(data.title || "Magpie"), options));
});

self.addEventListener("notificationclick", (event) => {
  const { url, links } = event.notification.data || {};
  event.notification.close();
  const action = event.action;
  if (action && links && links[action]) {
    event.waitUntil(
      fetch(links[action], { method: "POST", headers: { Accept: "application/json" } })
        .then((response) => (response.ok ? response.json() : null))
        .then((result) => {
          if (!result) return undefined;
          const text = action === "done" ? "Marked as done." : `Snoozed until ${result.when}.`;
          return self.registration.showNotification("Magpie", { body: text, icon: "/static/icons/icon-192.png", tag: "magpie-ack" });
        })
        .catch(() => {}),
    );
    return;
  }
  const target = new URL(url || "/", self.location.origin).href;
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((windows) => {
      for (const client of windows) {
        if (client.url.startsWith(self.location.origin) && "focus" in client) {
          // navigate() only works for pages this worker controls; otherwise open a new one.
          return client.focus().then((focused) => (focused || client).navigate(target)).catch(() => self.clients.openWindow(target));
        }
      }
      return self.clients.openWindow(target);
    }),
  );
});
