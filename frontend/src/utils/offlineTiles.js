/* global L */
/* ───────── Offline map tiles ─────────
   Thin wrapper around the global `LeafletOffline` (from the leaflet.offline
   CDN bundle loaded in index.html) plus `idb`-backed IndexedDB tile storage.
   Nothing outside this file should touch window.LeafletOffline directly.

   Tiles themselves live in leaflet.offline's own IndexedDB store, keyed by
   the fully-templated tile URL (so topo vs imagery, and different z/x/y,
   never collide). This module additionally tracks "saved areas" — the
   human-facing (label, bounds, zoom range, tile keys) records the offline
   panel shows — in localStorage, since leaflet.offline itself has no concept
   of a named area, only a flat tile cache. */

const AREAS_KEY = "sa_offline_areas";

// Rough fallback used only when estimating size for tiles we haven't
// downloaded yet in this session (no way to know real size beforehand).
const AVG_TILE_BYTES_FALLBACK = 15000;

function offline() {
  if (typeof window === "undefined" || !window.LeafletOffline) {
    throw new Error("Offline map support isn't loaded yet.");
  }
  return window.LeafletOffline;
}

function formatBytes(bytes) {
  if (!bytes) return "0 MB";
  const mb = bytes / (1024 * 1024);
  if (mb < 1) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${mb.toFixed(1)} MB`;
}

/** Tile list for a bounds across one or more zoom levels — same math
 * leaflet.offline's own control uses internally (map.project + layer.getTileUrls). */
function computeTiles(map, layer, latLngBounds, zoomLevels) {
  let tiles = [];
  for (const z of zoomLevels) {
    const area = L.bounds(
      map.project(latLngBounds.getNorthWest(), z),
      map.project(latLngBounds.getSouthEast(), z),
    );
    tiles = tiles.concat(layer.getTileUrls(area, z));
  }
  return tiles;
}

function estimateTileCount(map, layer, latLngBounds, zoomLevels) {
  return computeTiles(map, layer, latLngBounds, zoomLevels).length;
}

// Tiles that failed in the main pass get these extra rounds, after a pause, at low concurrency. The terrain
// layers' upstream (USGS 3DEP) answers 502/504 in bursts when busy, and a block that failed a minute ago
// usually comes back fine once the burst passes.
const RETRY_ROUND_DELAYS_MS = [5000, 20000];
const RETRY_CONCURRENCY = 2;

function cancellableWait(ms, isCancelled) {
  return new Promise((resolve) => {
    const started = Date.now();
    const tick = setInterval(() => {
      if (isCancelled?.() || Date.now() - started >= ms) { clearInterval(tick); resolve(); }
    }, 250);
  });
}

/** Download every tile for `bounds` across `zoomLevels` into IndexedDB, with
 * limited concurrency. Already-cached tiles are skipped (cheap `hasTile`
 * check) so re-running a download only backfills gaps. A tile that fails to
 * fetch doesn't abort the run: failed tiles get RETRY_ROUND_DELAYS_MS more rounds
 * at the end, and any still missing after that are counted in `missing` (a
 * re-run of the same area later fills them).
 * `onProgress(done, total, retrying)` — `retrying` is the number of tiles in the
 * current retry round (0 during the main pass).
 * `onTileSaved(key)` is called for each tile this run newly stores (not ones already
 * there, which may belong to other saved areas), so a cancelled or failed run can be
 * undone with removeTiles even if this throws part-way. */
async function downloadArea({ map, layer, bounds, zoomLevels, concurrency = 6, onProgress, isCancelled, onTileSaved }) {
  const tiles = computeTiles(map, layer, bounds, zoomLevels);
  const result = await fetchTiles({ tiles, concurrency, onProgress, isCancelled, onTileSaved });
  return { ...result, tileKeys: tiles.map((t) => t.key) };
}

/** Fetches `tiles` (leaflet.offline tile infos) into storage, retrying failures as downloadArea describes.
 * Returns { downloaded, total, missing, missingTiles, estimatedBytes }; `missingTiles` are the tile infos
 * still not stored, kept on the saved area so repairArea can fetch just those later. */
async function fetchTiles({ tiles, concurrency, onProgress, isCancelled, onTileSaved }) {
  const { hasTile, downloadTile, saveTile } = offline();
  const total = tiles.length;
  let done = 0, bytes = 0, measured = 0;
  onProgress?.(0, total, 0);

  // Fetches and stores one tile; false if it couldn't (network blip, tile server error).
  async function fetchOne(tile) {
    try {
      if (!(await hasTile(tile.key))) {
        const blob = await downloadTile(tile.url);
        // a failed tile (e.g. the terrain server's 503) isn't an image
        if (!blob.type.startsWith("image/")) throw new Error(`not a tile: ${blob.type}`);
        await saveTile(tile, blob);
        onTileSaved?.(tile.key);
        bytes += blob.size;
        measured += 1;
      }
      return true;
    } catch {
      return false;
    }
  }

  // Runs `list` through `workers` parallel workers; returns the tiles that failed.
  async function runPass(list, workers, onEach) {
    const failed = [];
    let idx = 0;
    async function worker() {
      while (idx < list.length) {
        if (isCancelled?.()) return;
        const tile = list[idx++];
        if (!(await fetchOne(tile))) failed.push(tile);
        onEach?.();
      }
    }
    await Promise.all(Array.from({ length: Math.min(workers, list.length) || 1 }, worker));
    return failed;
  }

  let failed = await runPass(tiles, concurrency, () => { done += 1; onProgress?.(done, total, 0); });
  for (const wait of RETRY_ROUND_DELAYS_MS) {
    if (!failed.length || isCancelled?.()) break;
    onProgress?.(done, total, failed.length);
    await cancellableWait(wait, isCancelled);
    if (isCancelled?.()) break;
    failed = await runPass(failed, RETRY_CONCURRENCY);
  }

  const avgBytes = measured ? bytes / measured : AVG_TILE_BYTES_FALLBACK;
  return {
    downloaded: done,
    total,
    missing: failed.length,
    missingTiles: failed.map(({ key, url, urlTemplate, z, x, y }) => ({ key, url, urlTemplate, z, x, y })),
    estimatedBytes: Math.round(avgBytes * total),
  };
}

/** Retries a saved area's missing tiles and updates its record (missing count and list). Returns the updated
 * record, or null if the area is gone or has nothing to repair. Areas saved before missing tiles were
 * recorded have no `missingTiles`, so there is nothing to repair for them. */
async function repairArea(id, { onProgress, isCancelled } = {}) {
  const area = listSavedAreas().find((a) => a.id === id);
  if (!area?.missingTiles?.length) return null;
  const tiles = area.missingTiles.map((t) => ({ ...t, createdAt: Date.now() }));
  await fetchTiles({ tiles, concurrency: RETRY_CONCURRENCY, onProgress, isCancelled });
  // A cancelled repair keeps what it stored (those tiles belong to this area); the rest stay listed as missing.
  // (`bytes` is already an estimate for the whole area, missing tiles included, so it doesn't change.)
  const remaining = await missingAfterRepair(area.missingTiles);
  const updated = { ...area, missing: remaining.length, missingTiles: remaining, repairedAt: new Date().toISOString() };
  const areas = listSavedAreas().map((a) => (a.id === id ? updated : a));
  try { localStorage.setItem(AREAS_KEY, JSON.stringify(areas)); } catch {}
  return updated;
}

// The tiles of `tiles` still not in storage (works the same whether the repair finished or was cancelled).
async function missingAfterRepair(tiles) {
  const { hasTile } = offline();
  const have = await Promise.all(tiles.map((t) => hasTile(t.key).catch(() => false)));
  return tiles.filter((_, i) => !have[i]);
}

/** Storage totals for one layer's urlTemplate (across all saved areas for it). */
async function getLayerStats(urlTemplate) {
  const { getStorageInfo } = offline();
  const tiles = await getStorageInfo(urlTemplate);
  const bytes = tiles.reduce((sum, t) => sum + (t.blob?.size || 0), 0);
  return { tileCount: tiles.length, bytes };
}

function listSavedAreas() {
  try {
    return JSON.parse(localStorage.getItem(AREAS_KEY) || "[]");
  } catch {
    return [];
  }
}

function saveAreaRecord(record) {
  const areas = listSavedAreas();
  areas.push(record);
  try { localStorage.setItem(AREAS_KEY, JSON.stringify(areas)); } catch {}
  return areas;
}

/** Removes these tiles from offline storage; returns how many couldn't be removed. */
async function removeTiles(keys) {
  const { removeTile } = offline();
  const results = await Promise.allSettled((keys || []).map((k) => removeTile(k)));
  return results.filter((r) => r.status === "rejected").length;
}

/** Forgets saved-area records by id (their tiles are handled by the caller). */
function forgetAreas(ids) {
  const drop = new Set(ids);
  const remaining = listSavedAreas().filter((a) => !drop.has(a.id));
  try { localStorage.setItem(AREAS_KEY, JSON.stringify(remaining)); } catch {}
  return remaining;
}

/** Deletes a saved area's tiles. Note: if this area's footprint overlaps
 * another saved area (same layer/zoom/location), those shared tiles are
 * removed too — re-downloading the other area will transparently backfill
 * them. Not worth reference-counting for how this is used in practice.
 * Returns how many tiles couldn't be removed. */
async function deleteSavedArea(id) {
  const area = listSavedAreas().find((a) => a.id === id);
  const failed = area ? await removeTiles(area.tileKeys) : 0;
  forgetAreas([id]);
  return failed;
}

/** Empties offline storage entirely: every saved tile (listed in an area or not, e.g. left by a deleted region)
 * and every saved-area record, for all regions. */
async function clearAllOffline() {
  await offline().truncate();
  try { localStorage.setItem(AREAS_KEY, "[]"); } catch {}
}

/** Removes the saved-area *records* for a deleted region (called after a
 * region is deleted). This cannot free the underlying shared tile blobs from
 * IndexedDB — tiles aren't reference-counted per region, same as the
 * overlapping-area sharing noted on deleteSavedArea above — so any tiles this
 * region's areas shared with another region's (or an un-tagged, pre-migration)
 * saved area are simply left in place. Not worth building reference counting
 * for how this is used in practice. */
function pruneSavedAreasForRegion(regionId) {
  const remaining = listSavedAreas().filter((a) => a.regionId !== regionId);
  try { localStorage.setItem(AREAS_KEY, JSON.stringify(remaining)); } catch {}
  return remaining;
}

export {
  formatBytes,
  estimateTileCount,
  downloadArea,
  repairArea,
  getLayerStats,
  listSavedAreas,
  saveAreaRecord,
  removeTiles,
  forgetAreas,
  deleteSavedArea,
  clearAllOffline,
  pruneSavedAreasForRegion,
  AVG_TILE_BYTES_FALLBACK,
};
