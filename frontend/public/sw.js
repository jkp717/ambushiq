// Service worker: makes the PWA installable (Chrome/Android need one to fire `beforeinstallprompt`) and
// lets the app open with no signal. It caches only the app itself:
//   - pages (navigations): network first, so a new deploy shows up whenever there's signal; the last good
//     copy of index.html is used when the network fails.
//   - /assets/* (Vite's hashed bundles) and the versioned Leaflet/plugin files from the CDNs: cache first,
//     since a given URL never changes.
// API data is not handled here: the app keeps its own saved copies (src/utils/offlineData.js), and offline
// map tiles are leaflet.offline's (IndexedDB). Bump SHELL_CACHE only if this caching logic changes.
const SHELL_CACHE = "ambushiq-shell-v1";
const CDN_HOSTS = ["unpkg.com", "cdn.jsdelivr.net"];

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => {
  e.waitUntil((async () => {
    for (const name of await caches.keys()) {
      if (name.startsWith("ambushiq-shell-") && name !== SHELL_CACHE) await caches.delete(name);
    }
    await self.clients.claim();
  })());
});

async function networkFirstPage(request) {
  const cache = await caches.open(SHELL_CACHE);
  try {
    const response = await fetch(request);
    if (response.ok) cache.put("/index.html", response.clone());
    return response;
  } catch (err) {
    const saved = await cache.match("/index.html");
    if (saved) return saved;
    throw err;
  }
}

async function cacheFirst(request) {
  const cache = await caches.open(SHELL_CACHE);
  const saved = await cache.match(request);
  if (saved) return saved;
  const response = await fetch(request);
  // "opaque" = a CDN file fetched without CORS (e.g. a stylesheet with no crossorigin attribute)
  if (response.ok || response.type === "opaque") cache.put(request, response.clone());
  return response;
}

self.addEventListener("fetch", (e) => {
  const { request } = e;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (request.mode === "navigate" && url.origin === self.location.origin && !url.pathname.startsWith("/api/")) {
    e.respondWith(networkFirstPage(request));
  } else if ((url.origin === self.location.origin && url.pathname.startsWith("/assets/")) ||
             CDN_HOSTS.includes(url.hostname)) {
    e.respondWith(cacheFirst(request));
  }
});
