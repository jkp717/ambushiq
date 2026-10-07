/* ───────── API client ───────── */
import { saveData, loadData } from "../utils/offlineData.js";

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

// <img> tags can't send the Authorization header, so image URLs carry the token as `t`.
function withToken(url) {
  const tok = tokenStore.get();
  return url && tok ? `${url}${url.includes("?") ? "&" : "?"}t=${encodeURIComponent(tok)}` : url;
}

// Where apiSaved keeps a response on the phone: per region unless `global`.
function savedKey(key, global) {
  return `${global ? "global" : `r${regionStore.get() || "none"}`}${key.startsWith("/") ? "" : "/"}${key}`;
}

/** Like apiRetry, but keeps the last good response on the phone and falls back to it when the server
 * can't be reached (no/weak signal, timeout, 5xx). A 401 or other 4xx never falls back. The saved copy is
 * per region unless `global` is set (for data like the region list itself). A result that came from the
 * phone carries `fromDevice(result)` = the time it was saved (ms), so pages can say how old it is.
 * Weak signal often hangs rather than failing, so the network attempt gives up after `timeoutMs`. */
async function apiSaved(path, { key = path, global = false, timeoutMs = 12000, ...opts } = {}) {
  const fullKey = savedKey(key, global);
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), timeoutMs);
  try {
    const data = await apiRetry(path, { ...opts, signal: ctl.signal });
    saveData(fullKey, data);
    return data;
  } catch (e) {
    const unreachable = e.code === undefined || e.code >= 500;
    if (!unreachable) throw e;
    const saved = await loadData(fullKey);
    if (!saved) throw e;
    const data = saved.data;
    if (data && typeof data === "object") Object.defineProperty(data, SAVED_AT, { value: saved.savedAt });
    return data;
  } finally {
    clearTimeout(timer);
  }
}

const SAVED_AT = Symbol("savedAt");
/** When `result` came from apiSaved's on-phone copy: the time it was saved (ms since epoch); else null. */
function fromDevice(result) {
  return (result && result[SAVED_AT]) || null;
}

/** The on-phone copy apiSaved keeps for `key` ({ savedAt, data }), without trying the network; or null. */
function loadSaved(key, { global = false } = {}) {
  return loadData(savedKey(key, global));
}

export { tokenStore, regionStore, api, apiRetry, apiSaved, loadSaved, fromDevice, withToken };
