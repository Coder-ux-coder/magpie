// Magpie service worker: offline app shell and push notifications.

const CACHE = "magpie-shell-v1";
const SHELL = [
  "/",
  "/static/css/app.css",
  "/static/js/app.js",
  "/static/js/api.js",
  "/static/js/actions.js",
  "/static/js/capture.js",
  "/static/js/cards.js",
  "/static/js/markdown.js",
  "/static/js/note-dialog.js",
  "/static/js/phrases.js",
  "/static/js/settings.js",
  "/static/js/speech.js",
  "/static/js/state.js",
  "/static/js/ui.js",
  "/static/js/views.js",
  "/static/js/voice.js",
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
  event.respondWith(
    fetch(request)
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
  const options = {
    body: data.body || "",
    icon: "/static/icons/icon-192.png",
    badge: "/static/icons/badge-96.png",
    tag: data.tag || undefined,
    renotify: Boolean(data.tag),
    requireInteraction: Boolean(data.tag),
    data: { url: data.url || "/", links: data.links || {} },
    actions: (data.actions || []).slice(0, 2),
  };
  event.waitUntil(self.registration.showNotification(data.title || "Magpie", options));
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
          client.navigate(target).catch(() => {});
          return client.focus();
        }
      }
      return self.clients.openWindow(target);
    }),
  );
});
