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

/** Download every tile for `bounds` across `zoomLevels` into IndexedDB, with
 * limited concurrency. Already-cached tiles are skipped (cheap `hasTile`
 * check) so re-running a download only backfills gaps. Best-effort: a tile
 * that fails to fetch is skipped rather than aborting the whole run — the
 * user can re-run later to fill any holes (e.g. from a mid-download signal drop). */
async function downloadArea({ map, layer, bounds, zoomLevels, concurrency = 6, onProgress, isCancelled }) {
  const { hasTile, downloadTile, saveTile } = offline();
  const tiles = computeTiles(map, layer, bounds, zoomLevels);
  const total = tiles.length;
  let done = 0, bytes = 0, measured = 0, idx = 0;
  onProgress?.(0, total);

  async function worker() {
    while (idx < tiles.length) {
      if (isCancelled?.()) return;
      const tile = tiles[idx++];
      try {
        if (!(await hasTile(tile.key))) {
          const blob = await downloadTile(tile.url);
          await saveTile(tile, blob);
          bytes += blob.size;
          measured += 1;
        }
      } catch {
        // network blip / tile server error — skip and move on
      }
      done += 1;
      onProgress?.(done, total);
    }
  }

  const workers = Array.from({ length: Math.min(concurrency, total) || 1 }, worker);
  await Promise.all(workers);

  const avgBytes = measured ? bytes / measured : AVG_TILE_BYTES_FALLBACK;
  return {
    downloaded: done,
    total,
    tileKeys: tiles.map((t) => t.key),
    estimatedBytes: Math.round(avgBytes * total),
  };
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

/** Deletes a saved area's tiles. Note: if this area's footprint overlaps
 * another saved area (same layer/zoom/location), those shared tiles are
 * removed too — re-downloading the other area will transparently backfill
 * them. Not worth reference-counting for how this is used in practice. */
async function deleteSavedArea(id) {
  const areas = listSavedAreas();
  const area = areas.find((a) => a.id === id);
  if (area) {
    const { removeTile } = offline();
    await Promise.all((area.tileKeys || []).map((k) => removeTile(k).catch(() => {})));
  }
  const remaining = areas.filter((a) => a.id !== id);
  try { localStorage.setItem(AREAS_KEY, JSON.stringify(remaining)); } catch {}
  return remaining;
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
  getLayerStats,
  listSavedAreas,
  saveAreaRecord,
  deleteSavedArea,
  pruneSavedAreasForRegion,
  AVG_TILE_BYTES_FALLBACK,
};
