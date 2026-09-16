import { useEffect, useRef, useState } from "react";
import { Download, Trash2, X, HardDrive } from "lucide-react";
import Modal from "./ui/Modal.jsx";
import Field from "./ui/Field.jsx";
import Banner from "./ui/Banner.jsx";
import {
  formatBytes, estimateTileCount, downloadArea, getLayerStats,
  listSavedAreas, saveAreaRecord, deleteSavedArea, AVG_TILE_BYTES_FALLBACK,
} from "../utils/offlineTiles.js";

const MIN_ZOOM_FLOOR = 5; // leaflet.offline's own sanity floor — prevents saving the whole USA

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

  const [selected, setSelected] = useState(() => new Set(layerList.map((l) => l.id)));
  const [minZoom, setMinZoom] = useState(() => Math.max(MIN_ZOOM_FLOOR, Math.round(map?.getZoom() ?? 12)));
  const [maxZoom, setMaxZoom] = useState(16);
  const [label, setLabel] = useState("");
  const [progress, setProgress] = useState(null); // { layerLabel, done, total, layerIdx, layerCount }
  const cancelledRef = useRef(false);
  const [err, setErr] = useState(null);
  const [areas, setAreas] = useState(() => filterAreasForRegion(listSavedAreas(), activeRegion));
  const [stats, setStats] = useState(null);

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

  async function startDownload() {
    setErr(null);
    cancelledRef.current = false;
    const targets = layerList.filter((l) => selected.has(l.id));
    if (!targets.length) { setErr("Pick at least one map layer."); return; }

    for (let i = 0; i < targets.length; i++) {
      if (cancelledRef.current) break;
      const target = targets[i];
      setProgress({ layerLabel: target.label, done: 0, total: 0, layerIdx: i + 1, layerCount: targets.length });
      try {
        const result = await downloadArea({
          map, layer: target.layer, bounds, zoomLevels,
          onProgress: (done, total) => setProgress({ layerLabel: target.label, done, total, layerIdx: i + 1, layerCount: targets.length }),
          isCancelled: () => cancelledRef.current,
        });
        if (cancelledRef.current) break;
        saveAreaRecord({
          id: `${Date.now()}-${target.id}`,
          label: label.trim() || `${target.label} area`,
          layerId: target.id,
          layerLabel: target.label,
          regionId: activeRegion.id,
          minZoom, maxZoom,
          tileKeys: result.tileKeys,
          tileCount: result.total,
          bytes: result.estimatedBytes,
          downloadedAt: new Date().toISOString(),
        });
      } catch {
        setErr(`Couldn't download ${target.label} tiles — check your connection and try again.`);
        break;
      }
    }
    setProgress(null);
    setAreas(filterAreasForRegion(listSavedAreas(), activeRegion));
    setLabel("");
  }

  async function removeArea(id) {
    await deleteSavedArea(id);
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

        {!progress && (
          <>
            <Field label="Layers to save">
              <div style={{ display: "flex", gap: 10 }}>
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
                    {Array.from({ length: 17 - MIN_ZOOM_FLOOR }, (_, i) => MIN_ZOOM_FLOOR + i).map((z) => (
                      <option key={z} value={z}>{z}</option>
                    ))}
                  </select>
                </Field>
              </div>
              <div style={{ flex: 1 }}>
                <Field label="Max zoom (most zoomed in)">
                  <select value={maxZoom} onChange={(e) => setMaxZoom(Math.max(+e.target.value, minZoom))}>
                    {Array.from({ length: 17 - MIN_ZOOM_FLOOR }, (_, i) => MIN_ZOOM_FLOOR + i).map((z) => (
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
            </p>

            <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
              <button className="btn btn-primary" onClick={startDownload} disabled={estimatedTiles === 0}>
                <Download size={15} /> Download this area
              </button>
              <button className="btn" onClick={onClose}>Close</button>
            </div>
          </>
        )}

        {progress && (
          <div style={{ marginTop: 10 }}>
            <div style={{ fontSize: 13, marginBottom: 6 }}>
              Downloading {progress.layerLabel} ({progress.layerIdx}/{progress.layerCount}) — {progress.done}/{progress.total || "…"} tiles
            </div>
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
                </div>
                <button className="icon-btn" title="Delete this area" onClick={() => removeArea(a.id)}><Trash2 size={14} /></button>
              </div>
            ))}
          </div>
        )}
      </div>
    </Modal>
  );
}

export default OfflineMapsPanel;
