/* ───────── Saved API responses (offline fallback) ─────────
   The last good copy of the data the app needs in the field (forecast hours, every hour's wind/thermal
   arrows, deer ratings, stands and other map features), kept on the phone so the app still works with no
   signal. Stored in the Cache Storage API: no library needed, room for megabytes, and it survives the app
   being closed. Keys are per region, so switching regions never shows another region's data.

   Every call swallows errors: no Cache API (an insecure origin, some private windows) or a full disk just
   means nothing is saved, and the app behaves exactly as it would online-only. */

const CACHE_NAME = "ambushiq-data-v1";

function hasCache() {
  return typeof caches !== "undefined";
}

function keyUrl(key) {
  return new URL(`/offline/${key}`, window.location.origin).toString();
}

/** Save `data` under `key` with the current time. Resolves to nothing; never throws. */
async function saveData(key, data) {
  if (!hasCache()) return;
  try {
    const cache = await caches.open(CACHE_NAME);
    await cache.put(keyUrl(key), new Response(JSON.stringify({ savedAt: Date.now(), data }),
      { headers: { "Content-Type": "application/json" } }));
  } catch { /* not saved: offline fallback just won't have this */ }
}

/** The saved `{ savedAt, data }` for `key`, or null. Never throws. */
async function loadData(key) {
  if (!hasCache()) return null;
  try {
    const cache = await caches.open(CACHE_NAME);
    const r = await cache.match(keyUrl(key));
    return r ? await r.json() : null;
  } catch {
    return null;
  }
}

/** "3:42 PM" for a save from today, "Oct 6, 3:42 PM" for an older one. */
function savedAtLabel(ms) {
  const d = new Date(ms);
  const time = d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  return d.toDateString() === new Date().toDateString()
    ? time : `${d.toLocaleDateString([], { month: "short", day: "numeric" })}, ${time}`;
}

export { saveData, loadData, savedAtLabel };
