const CACHE_NAME = "pantryos-shell-v1";
const APP_SHELL = ["/", "/styles.css", "/app.js", "/manifest.webmanifest", "/icon.svg"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(APP_SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

function offlineProblem() {
  return new Response(
    JSON.stringify({
      type: "https://pantryos.local/problems/offline",
      title: "Offline",
      status: 503,
      code: "offline",
      detail: "PantryOS Core is offline; the request was not committed.",
      errors: [],
      request_id: "offline",
    }),
    {
      status: 503,
      headers: {
        "Content-Type": "application/problem+json; charset=utf-8",
        "Cache-Control": "no-store",
      },
    }
  );
}

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin) {
    return;
  }

  if (url.pathname.startsWith("/api/")) {
    event.respondWith(fetch(event.request).catch(() => offlineProblem()));
    return;
  }

  if (event.request.mode === "navigate") {
    event.respondWith(fetch(event.request).catch(() => caches.match("/")));
    return;
  }

  if (event.request.method !== "GET") {
    return;
  }

  event.respondWith(
    caches.match(event.request).then((cached) => {
      if (cached) {
        return cached;
      }
      return fetch(event.request).then((response) => {
        if (response.ok) {
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, response.clone()));
        }
        return response;
      });
    })
  );
});