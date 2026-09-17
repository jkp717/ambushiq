// Minimal service worker — exists solely so Chrome/Android treats this PWA
// as installable and fires `beforeinstallprompt`. Deliberately has no fetch
// handler: it doesn't cache or intercept anything. Offline map tiles are
// already handled separately by leaflet.offline (IndexedDB, no SW involved).
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
