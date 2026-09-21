/* ───────── API client ───────── */
const tokenStore = {
  get: () => localStorage.getItem("sa_token") || "",
  set: (t) => localStorage.setItem("sa_token", t),
  clear: () => localStorage.removeItem("sa_token"),
};
const regionStore = {
  get: () => localStorage.getItem("sa_region_id") || "",
  set: (id) => localStorage.setItem("sa_region_id", String(id)),
  clear: () => localStorage.removeItem("sa_region_id"),
};
async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  const tok = tokenStore.get();
  if (tok) headers["Authorization"] = `Bearer ${tok}`;
  const regionId = regionStore.get();
  if (regionId) headers["X-Region-Id"] = regionId;
  const r = await fetch(`/api${path}`, { ...opts, headers });
  if (r.status === 401) {
    // Tell AuthContext to drop back to the login screen — otherwise a token
    // invalidated mid-session (e.g. APP_TOKEN rotated) just leaves every page
    // showing a generic "couldn't load" error forever with no way back.
    window.dispatchEvent(new CustomEvent("sa:unauthorized"));
    const e = new Error("unauthorized"); e.code = 401; throw e;
  }
  if (!r.ok) { const e = new Error((await r.json().catch(() => ({}))).detail || `error ${r.status}`); e.code = r.status; throw e; }
  return r.json();
}

// GET that quietly retries once when the server or the weather service hiccups (5xx / network
// error). Auth and other 4xx errors are not retried.
async function apiRetry(path, opts = {}, retries = 1, delayMs = 1500) {
  for (let attempt = 0; ; attempt++) {
    try {
      return await api(path, opts);
    } catch (e) {
      const transient = e.code === undefined || e.code >= 500;
      if (!transient || attempt >= retries) throw e;
      await new Promise((r) => setTimeout(r, delayMs));
    }
  }
}

export { tokenStore, regionStore, api, apiRetry };
