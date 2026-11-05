import { useEffect, useRef, useState } from "react";
import { Download, Trash2, X, HardDrive, RefreshCw } from "lucide-react";
import Modal from "./ui/Modal.jsx";
import Field from "./ui/Field.jsx";
import Banner from "./ui/Banner.jsx";
import { api } from "../services/api.js";
import {
  formatBytes, estimateTileCount, downloadArea, repairArea, getLayerStats,
  listSavedAreas, saveAreaRecord, deleteSavedArea, removeTiles, forgetAreas, clearAllOffline,
  AVG_TILE_BYTES_FALLBACK,
} from "../utils/offlineTiles.js";

const PREFETCH_IDS = new Set(["contours", "hillshade"]);   // layers the server can prepare overnight
const PREP_STATUS = {
  queued: () => "Waiting for tonight's run",
  running: (p) => `Being fetched — ${p.blocks_done.toLocaleString()} of ${p.blocks_total.toLocaleString()} blocks`,
  partial: (p) => `${p.blocks_done.toLocaleString()} of ${p.blocks_total.toLocaleString()} blocks fetched — the rest continues tonight`,
  ready: () => "Ready — open this view and download it (it will be quick)",
};

const MIN_ZOOM_FLOOR = 5; // leaflet.offline's own sanity floor — prevents saving the whole USA
const MAX_ZOOM_CEIL = 18; // each level is ~4x the tiles of the one before; deeper gets huge fast

// Records from before regions existed carry no regionId — keep them visible
// under every region rather than orphaning them.
function filterAreasForRegion(areas, activeRegion) {
  return areas.filter((a) => !a.regionId || a.regionId === activeRegion.id);
}

/** Map-page panel for downloading the current view's tiles into IndexedDB so
 * the map still renders with no/poor signal. Reads the Leaflet map + base
 * layers off the HuntMap ref rather than owning any map state itself. */
function OfflineMapsPanel({ mapApi, activeRegion, onClose }) {
  const map = mapApi.getMap();
  const offlineLayers = mapApi.getBaseLayers(); // { topo: {id,label,url,layer}, imagery: {...} }
  const layerList = Object.values(offlineLayers || {});

  // Only the layer(s) on screen start selected — with every basemap ticked, a download balloons.
  const [selected, setSelected] = useState(() => {
    const active = (mapApi.getActiveBaseIds?.() || []).filter((id) => offlineLayers?.[id]);
    return new Set(active.length ? active : layerList.slice(0, 1).map((l) => l.id));
  });
  const [minZoom, setMinZoom] = useState(() => Math.max(MIN_ZOOM_FLOOR, Math.round(map?.getZoom() ?? 12)));
  const [maxZoom, setMaxZoom] = useState(16);
  const [label, setLabel] = useState("");
  const [progress, setProgress] = useState(null); // { layerLabel, done, total, layerIdx, layerCount }
  const cancelledRef = useRef(false);
  const [err, setErr] = useState(null);
  const [areas, setAreas] = useState(() => filterAreasForRegion(listSavedAreas(), activeRegion));
  const [stats, setStats] = useState(null);
  const [note, setNote] = useState(null);          // a one-line outcome, e.g. after a cancelled download
  const [confirmClear, setConfirmClear] = useState(false);
  // Contours/hillshade not yet on the server for this view: { missing, total, estMinutes } -> offer overnight prep
  const [serverPrep, setServerPrep] = useState(null);
  const [checking, setChecking] = useState(false);
  const [prepAreas, setPrepAreas] = useState([]);   // areas the server is preparing (or has prepared) overnight

  function loadPrepAreas() {
    api(`/tiles/prefetch?region_id=${activeRegion.id}`).then(setPrepAreas).catch(() => {});
  }
  useEffect(loadPrepAreas, [activeRegion.id]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => setServerPrep(null), [selected, minZoom, maxZoom]);   // the offer was for the old choice

  useEffect(() => {
    if (!layerList.length) return;
    Promise.all(layerList.map((l) => getLayerStats(l.url)))
      .then((results) => {
        const tileCount = results.reduce((s, r) => s + r.tileCount, 0);
        const bytes = results.reduce((s, r) => s + r.bytes, 0);
        setStats({ tileCount, bytes });
      })
      .catch(() => {});
  }, [areas]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!map || !layerList.length) {
    return (
      <Modal onClose={onClose}>
        <div className="card" style={{ padding: 16, border: "2px solid var(--navy)" }}>
          <Banner>Map isn't ready yet — close this and try again in a moment.</Banner>
          <button className="btn" onClick={onClose}>Close</button>
        </div>
      </Modal>
    );
  }

  const bounds = map.getBounds();
  const zoomLevels = [];
  for (let z = minZoom; z <= maxZoom; z++) zoomLevels.push(z);

  const estimatedTiles = layerList
    .filter((l) => selected.has(l.id))
    .reduce((sum, l) => sum + estimateTileCount(map, l.layer, bounds, zoomLevels), 0);

  function toggleLayer(id) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  // Contour and hillshade tiles are slow USGS renders the server fetches overnight (app.tiles.prefetch). Before
  // downloading them, ask the server whether it already has this view; if not, offer to prepare it overnight so the
  // download is quick and complete, or to download now anyway (slower, may leave gaps for Repair).
  const prepLayers = layerList.filter((l) => selected.has(l.id) && PREFETCH_IDS.has(l.id));
  const prepBody = () => ({
    north: bounds.getNorth(), south: bounds.getSouth(), west: bounds.getWest(), east: bounds.getEast(),
    layers: prepLayers.map((l) => l.id), min_zoom: minZoom, max_zoom: maxZoom,
  });

  async function onDownloadClick() {
    setErr(null);
    setNote(null);
    if (prepLayers.length) {
      setChecking(true);
      let check = null;
      try {
        check = await api("/tiles/prefetch/check", { method: "POST", body: JSON.stringify(prepBody()) });
      } catch {
        check = null;   // server unreachable or area too big to prepare: just download
      }
      setChecking(false);
      if (check && !check.ready) {
        setServerPrep({ missing: check.missing_blocks * 16, total: check.total_blocks * 16, estMinutes: check.est_minutes });
        return;
      }
    }
    startDownload();
  }

  async function prepareOvernight() {
    setErr(null);
    try {
      await api("/tiles/prefetch", {
        method: "POST",
        body: JSON.stringify({ ...prepBody(), region_id: activeRegion.id, label: label.trim() || "Offline area" }),
      });
      setNote(`The server will fetch the ${prepLayers.map((l) => l.label.toLowerCase()).join(" and ")} tiles for this view `
        + "overnight. Come back to this view tomorrow and download it — it will be quick.");
      setServerPrep(null);
      loadPrepAreas();
    } catch (e) {
      setErr(`Couldn't queue this area on the server (${e.message}).`);
    }
  }

  // A download either finishes and is listed, or is undone: a cancelled or failed run removes every tile it added
  // (and the areas it already finished), so nothing is left in storage that the list doesn't show. Tiles that were
  // already saved before this run started are never touched; they may belong to other saved areas.
  async function startDownload() {
    setErr(null);
    setNote(null);
    setServerPrep(null);
    cancelledRef.current = false;
    const targets = layerList.filter((l) => selected.has(l.id));
    if (!targets.length) { setErr("Pick at least one map layer."); return; }

    const newKeys = [];
    const records = [];      // listed only once every layer has finished, so an undone run leaves no record behind
    let failed = null;
    for (let i = 0; i < targets.length; i++) {
      if (cancelledRef.current) break;
      const target = targets[i];
      const step = { layerLabel: target.label, layerIdx: i + 1, layerCount: targets.length };
      setProgress({ ...step, done: 0, total: 0, retrying: 0 });
      try {
        const result = await downloadArea({
          map, layer: target.layer, bounds, zoomLevels,
          onProgress: (done, total, retrying) => setProgress({ ...step, done, total, retrying }),
          isCancelled: () => cancelledRef.current,
          onTileSaved: (key) => newKeys.push(key),
        });
        if (cancelledRef.current) break;
        records.push({
          id: `${Date.now()}-${target.id}`,
          label: label.trim() || `${target.label} area`,
          layerId: target.id,
          layerLabel: target.label,
          regionId: activeRegion.id,
          minZoom, maxZoom,
          tileKeys: result.tileKeys,
          tileCount: result.total,
          missing: result.missing,
          missingTiles: result.missingTiles,   // what "Repair" fetches later
          bytes: result.estimatedBytes,
          downloadedAt: new Date().toISOString(),
        });
      } catch {
        failed = target.label;
        break;
      }
    }
    if (cancelledRef.current || failed) {
      setProgress({ removing: true });
      let left = 0;
      try { left = await removeTiles(newKeys); } catch { left = newKeys.length; }
      const leftNote = left ? ` (${left.toLocaleString()} tiles couldn't be removed — use “Clear all offline maps”)` : "";
      if (failed) setErr(`Couldn't download ${failed} tiles — check your connection and try again. Nothing was saved${leftNote}.`);
      else setNote(`Download cancelled — nothing was saved${leftNote}.`);
    } else {
      // Downloading the same view again (e.g. to fill tiles the server couldn't send) replaces that area's
      // entry instead of listing it twice: an area whose tiles the new download fully covers is superseded.
      const covers = records.map((r) => ({ r, keys: new Set(r.tileKeys) }));
      const superseded = (a) => covers.some(({ r, keys }) => a.layerId === r.layerId && a.regionId === r.regionId
        && (a.tileKeys || []).every((k) => keys.has(k)));
      forgetAreas(listSavedAreas().filter(superseded).map((a) => a.id));
      records.forEach(saveAreaRecord);
      const gaps = records.filter((r) => r.missing);
      if (gaps.length) {
        setErr(`${gaps.map((r) => `${r.missing.toLocaleString()} ${r.layerLabel}`).join(", ")} tiles couldn't be downloaded `
          + "(the map server was busy). The rest was saved — use Repair on the saved area later to fill the gaps.");
      }
    }
    setProgress(null);
    setAreas(filterAreasForRegion(listSavedAreas(), activeRegion));
    setLabel("");
  }

  // Fetches just the tiles a saved area is missing. Cancelling keeps what was fetched (it belongs to the area).
  async function repair(area) {
    setErr(null);
    setNote(null);
    cancelledRef.current = false;
    const step = { repairing: true, layerLabel: area.label, layerIdx: 1, layerCount: 1 };
    setProgress({ ...step, done: 0, total: area.missing, retrying: 0 });
    try {
      const updated = await repairArea(area.id, {
        onProgress: (done, total, retrying) => setProgress({ ...step, done, total, retrying }),
        isCancelled: () => cancelledRef.current,
      });
      const fixed = area.missing - (updated?.missing ?? area.missing);
      if (!updated) setNote("Nothing to repair for this area.");
      else if (!updated.missing) setNote(`Repaired “${area.label}” — all ${fixed.toLocaleString()} missing tiles are saved now.`);
      else setErr(`Repaired ${fixed.toLocaleString()} tiles of “${area.label}”; ${updated.missing.toLocaleString()} still `
        + `couldn't be downloaded${cancelledRef.current ? " (repair cancelled)" : " — the map server may still be busy, try again later"}.`);
    } catch {
      setErr("Couldn't repair this area — check your connection and try again.");
    }
    setProgress(null);
    setAreas(filterAreasForRegion(listSavedAreas(), activeRegion));
  }

  async function removeArea(id) {
    setNote(null);
    const left = await deleteSavedArea(id);
    if (left) setNote(`${left.toLocaleString()} tiles couldn't be removed — use “Clear all offline maps”.`);
    setAreas(filterAreasForRegion(listSavedAreas(), activeRegion));
  }

  // Empties offline storage for every region, including tiles no listed area accounts for (left by a deleted
  // region, or by a download from before cancelled runs were undone).
  async function clearAll() {
    setConfirmClear(false);
    setErr(null);
    try {
      await clearAllOffline();
      setNote("All offline maps were removed from this device.");
    } catch {
      setErr("Couldn't clear offline maps — try again.");
    }
    setAreas(filterAreasForRegion(listSavedAreas(), activeRegion));
  }

  return (
    <Modal onClose={progress ? () => {} : onClose}>
      <div className="card" style={{ padding: 16, border: "2px solid var(--navy)" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
          <strong>Download map for offline use</strong>
          {!progress && <button className="icon-btn" onClick={onClose}><X size={16} /></button>}
        </div>
        <p style={{ fontSize: 12.5, color: "var(--sub)", marginTop: 0, marginBottom: 14 }}>
          Saves map tiles for the area currently shown, so the map still works with weak or no signal.
          Pan/zoom the map to the area you want before opening this, or close and adjust the view first.
        </p>

        {err && <Banner>{err}</Banner>}
        {note && <div style={{ fontSize: 12.5, color: "var(--sub)", marginBottom: 10 }}>{note}</div>}

        {!progress && (
          <>
            <Field label="Layers to save">
              <div style={{ display: "flex", flexWrap: "wrap", gap: "6px 12px" }}>
                {layerList.map((l) => (
                  <label key={l.id} style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 13 }}>
                    <input type="checkbox" checked={selected.has(l.id)} onChange={() => toggleLayer(l.id)} />
                    {l.label}
                  </label>
                ))}
              </div>
            </Field>

            <div style={{ display: "flex", gap: 10, marginTop: 12 }}>
              <div style={{ flex: 1 }}>
                <Field label="Min zoom (more zoomed out)">
                  <select value={minZoom} onChange={(e) => setMinZoom(Math.min(+e.target.value, maxZoom))}>
                    {Array.from({ length: MAX_ZOOM_CEIL + 1 - MIN_ZOOM_FLOOR }, (_, i) => MIN_ZOOM_FLOOR + i).map((z) => (
                      <option key={z} value={z}>{z}</option>
                    ))}
                  </select>
                </Field>
              </div>
              <div style={{ flex: 1 }}>
                <Field label="Max zoom (most zoomed in)">
                  <select value={maxZoom} onChange={(e) => setMaxZoom(Math.max(+e.target.value, minZoom))}>
                    {Array.from({ length: MAX_ZOOM_CEIL + 1 - MIN_ZOOM_FLOOR }, (_, i) => MIN_ZOOM_FLOOR + i).map((z) => (
                      <option key={z} value={z}>{z}</option>
                    ))}
                  </select>
                </Field>
              </div>
            </div>

            <div style={{ marginTop: 12 }}>
              <Field label="Label (optional)">
                <input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="e.g. North property" />
              </Field>
            </div>

            <p style={{ fontSize: 12, color: "var(--sub)", marginTop: 12 }}>
              ~{estimatedTiles.toLocaleString()} tiles, roughly {formatBytes(estimatedTiles * AVG_TILE_BYTES_FALLBACK)} (rough estimate — actual size varies by imagery detail).
              A wide min/max zoom range over a large area can take a while and use meaningful storage — the current map view is what gets saved, so zoom/pan to just the area you need first.
              {[...selected].some((id) => offlineLayers?.[id]?.needsKey) && " MapTiler tiles count against your MapTiler monthly tile quota."}
            </p>

            {serverPrep ? (
              <div style={{ marginTop: 10, padding: 10, borderRadius: 6, background: "var(--surf)", fontSize: 12.5 }}>
                <div style={{ marginBottom: 8 }}>
                  About {serverPrep.missing.toLocaleString()} of the {prepLayers.map((l) => l.label.toLowerCase()).join(" and ")} tiles
                  for this view aren't on the server yet, and USGS is slow to draw them. The server can fetch them overnight
                  (about {serverPrep.estMinutes.toLocaleString()} min of work); downloading this view after that is quick and complete.
                </div>
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                  <button className="btn btn-primary" onClick={prepareOvernight}>Prepare overnight</button>
                  <button className="btn" onClick={startDownload}>
                    <Download size={15} /> Download now anyway
                  </button>
                  <button className="btn" onClick={() => setServerPrep(null)}>Back</button>
                </div>
                <div style={{ color: "var(--sub)", fontSize: 11.5, marginTop: 6 }}>
                  Downloading now is slower and may miss tiles the server can't get in time; Repair can fill them later.
                </div>
              </div>
            ) : (
              <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
                <button className="btn btn-primary" onClick={onDownloadClick} disabled={estimatedTiles === 0 || checking}>
                  <Download size={15} /> {checking ? "Checking the server…" : "Download this area"}
                </button>
                <button className="btn" onClick={onClose}>Close</button>
              </div>
            )}

            {prepAreas.length > 0 && (
              <div style={{ marginTop: 14 }}>
                <strong style={{ fontSize: 13 }}>Prepared on the server overnight</strong>
                {prepAreas.map((p) => (
                  <div key={p.id} style={{ fontSize: 12, padding: "4px 0", borderBottom: "1px solid var(--bord)" }}>
                    {p.label} <span style={{ color: "var(--sub)" }}>· {p.layers.join(", ")} · zoom {p.min_zoom}–{p.max_zoom}</span>
                    <div style={{ fontSize: 11, color: p.status === "ready" ? "var(--navy)" : "var(--sub)" }}>
                      {PREP_STATUS[p.status]?.(p) || p.status}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </>
        )}

        {progress?.removing && (
          <div style={{ marginTop: 10, fontSize: 13 }}>Removing the partial download…</div>
        )}

        {progress && !progress.removing && (
          <div style={{ marginTop: 10 }}>
            <div style={{ fontSize: 13, marginBottom: 6 }}>
              {progress.repairing
                ? <>Repairing {progress.layerLabel} — {progress.done}/{progress.total || "…"} missing tiles</>
                : <>Downloading {progress.layerLabel} ({progress.layerIdx}/{progress.layerCount}) — {progress.done}/{progress.total || "…"} tiles</>}
            </div>
            {progress.retrying > 0 && (
              <div style={{ fontSize: 12, color: "var(--sub)", marginBottom: 6 }}>
                Trying {progress.retrying.toLocaleString()} tiles again that the map server didn't send…
              </div>
            )}
            <div style={{ height: 8, borderRadius: 4, background: "var(--surf)", overflow: "hidden" }}>
              <div style={{
                height: "100%", background: "var(--navy)", borderRadius: 4,
                width: progress.total ? `${Math.round((progress.done / progress.total) * 100)}%` : "3%",
                transition: "width 0.15s",
              }} />
            </div>
            <button className="btn" style={{ marginTop: 10 }} onClick={() => { cancelledRef.current = true; }}>Cancel</button>
          </div>
        )}

        {!progress && (
          <div style={{ marginTop: 18, paddingTop: 12, borderTop: "1px solid var(--bord)" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 8 }}>
              <HardDrive size={14} color="var(--navy)" />
              <strong style={{ fontSize: 13 }}>Saved offline areas</strong>
              {stats && <span style={{ fontSize: 11.5, color: "var(--sub)", marginLeft: "auto" }}>
                {stats.tileCount.toLocaleString()} tiles · {formatBytes(stats.bytes)} total
              </span>}
            </div>
            {!areas.length && <div style={{ fontSize: 12.5, color: "var(--sub)" }}>Nothing saved yet.</div>}
            {areas.map((a) => (
              <div key={a.id} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12.5, padding: "5px 0", borderBottom: "1px solid var(--bord)" }}>
                <div style={{ flex: 1 }}>
                  <div>{a.label} <span style={{ color: "var(--sub)" }}>· {a.layerLabel}</span></div>
                  <div style={{ color: "var(--sub)", fontSize: 11 }}>
                    {a.tileCount.toLocaleString()} tiles · {formatBytes(a.bytes)} · zoom {a.minZoom}–{a.maxZoom} · {new Date(a.downloadedAt).toLocaleDateString()}
                  </div>
                  {a.missing > 0 && (
                    <div style={{ color: "var(--red)", fontSize: 11 }}>
                      {a.missing.toLocaleString()} tiles missing
                    </div>
                  )}
                </div>
                {a.missingTiles?.length > 0 && (
                  <button className="btn" style={{ padding: "3px 8px", fontSize: 12 }}
                    title="Download just the tiles this area is missing" onClick={() => repair(a)}>
                    <RefreshCw size={13} /> Repair
                  </button>
                )}
                <button className="icon-btn" title="Delete this area" onClick={() => removeArea(a.id)}><Trash2 size={14} /></button>
              </div>
            ))}
            {stats?.tileCount > 0 && (
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 10, flexWrap: "wrap" }}>
                {!confirmClear ? (
                  <button className="btn" onClick={() => setConfirmClear(true)}>
                    <Trash2 size={14} /> Clear all offline maps
                  </button>
                ) : (
                  <>
                    <span style={{ fontSize: 12.5 }}>Remove every saved map on this device, for all regions?</span>
                    <button className="btn" style={{ color: "var(--red)", borderColor: "rgba(163,45,45,.4)" }} onClick={clearAll}>Yes, clear all</button>
                    <button className="btn" onClick={() => setConfirmClear(false)}>Keep</button>
                  </>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </Modal>
  );
}

export default OfflineMapsPanel;
