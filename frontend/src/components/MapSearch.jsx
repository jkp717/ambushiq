import React, { useEffect, useRef, useState } from "react";
import { Search, X, Loader2, MapPin, Droplets, Mountain, Building2, Trees, Crosshair, Signpost } from "lucide-react";
import { api } from "../services/api.js";
import { parseLatLon, fmtLatLon } from "../utils/coords.js";
import { fmtDist } from "../utils/units.js";
import { roadSignSvg } from "../utils/roadSignStyle.js";
import { searchLineColor } from "./HuntMap.jsx";

// Map search panel (opened from the magnifier button on the map). Typed coordinates are recognised on the spot;
// names go to /api/search (towns, streets, waterways, landforms, trails; near the map view first, see
// app.search.service). Picking a result hands it to HuntMap.showSearchResult, which pins it or draws the whole
// road/trail; it stays on the map until cleared here.

const DEBOUNCE_MS = 300;
const MIN_QUERY = 3;
const M_PER_MI = 1609.344;

const KIND_ICON = { town: Building2, street: Signpost, water: Droplets, landform: Mountain, park: Trees, place: MapPin, coords: Crosshair };
const KIND_ZOOM = { town: 13, street: 16, water: 15, landform: 15, park: 14, place: 15, trail: 15, coords: 16 };

function ResultIcon({ item }) {
  if (item.kind === "trail") {
    const k = item.trail_kind || "foot";
    return <span className="map-search-icon" dangerouslySetInnerHTML={{ __html: roadSignSvg(k, searchLineColor(k), 18) }} />;
  }
  const Icon = KIND_ICON[item.kind] || MapPin;
  return <span className={"map-search-icon kind-" + item.kind}><Icon size={16} /></span>;
}

function coordItems(q) {
  const c = parseLatLon(q);
  if (!c) return [];
  const out = [{ id: "coords", kind: "coords", name: `Go to ${fmtLatLon(c.lat, c.lon)}`, label: fmtLatLon(c.lat, c.lon), lat: c.lat, lon: c.lon }];
  // "36.1, 93.5" without N/S/E/W is most likely a US point with the minus sign left off
  if (!c.hemispheres && c.lon > 0) {
    out.push({ id: "coords-w", kind: "coords", name: `Go to ${fmtLatLon(c.lat, -c.lon)}`, sub: "West longitude (minus sign added)",
      label: fmtLatLon(c.lat, -c.lon), lat: c.lat, lon: -c.lon });
  }
  return out;
}

// `query` lives in the map page, so it's still there when the panel is reopened however it was closed.
export default function MapSearch({ getHuntMap, query: q, onQueryChange: setQ, onClose, onResultChange, hasResult }) {
  const [results, setResults] = useState([]);
  const [failed, setFailed] = useState([]);
  const [status, setStatus] = useState("idle");   // idle | loading | ok | error | offline
  const [active, setActive] = useState(0);
  const [picking, setPicking] = useState(false);
  const inputRef = useRef(null);
  const panelRef = useRef(null);

  useEffect(() => { inputRef.current?.focus(); inputRef.current?.select(); }, []);

  // click/tap outside the panel closes it (the result stays on the map)
  useEffect(() => {
    const down = (e) => { if (panelRef.current && !panelRef.current.contains(e.target) && !e.target.closest?.(".map-search-btn")) onClose(); };
    document.addEventListener("pointerdown", down);
    return () => document.removeEventListener("pointerdown", down);
  }, [onClose]);

  const coords = coordItems(q);
  const isCoords = coords.length > 0;
  const text = q.trim();

  useEffect(() => {
    setActive(0);
    if (isCoords || text.length < MIN_QUERY) { setResults([]); setFailed([]); setStatus("idle"); return undefined; }
    if (typeof navigator !== "undefined" && navigator.onLine === false) { setResults([]); setStatus("offline"); return undefined; }
    const ctrl = new AbortController();
    setStatus("loading");
    const timer = setTimeout(async () => {
      const c = getHuntMap()?.getMap?.()?.getCenter();
      if (!c) { setStatus("idle"); return; }
      try {
        const j = await api(`/search?${new URLSearchParams({ q: text, lat: c.lat.toFixed(5), lon: c.lng.toFixed(5) })}`, { signal: ctrl.signal });
        setResults(j.results || []);
        setFailed(j.failed || []);
        setStatus("ok");
      } catch (e) {
        if (e.name === "AbortError") return;
        setResults([]);
        setStatus(navigator.onLine === false ? "offline" : "error");
      }
    }, DEBOUNCE_MS);
    return () => { clearTimeout(timer); ctrl.abort(); };
  }, [text, isCoords, getHuntMap]);

  const items = isCoords ? coords : results;

  async function pick(item) {
    const hm = getHuntMap();
    if (!hm || !item) return;
    let geometry = item.geometry || null;
    if (item.source === "osm" && item.ref != null) {
      setPicking(true);
      try {
        geometry = (await api(`/search/osm-trail/${item.ref}`)).geometry;
      } catch {
        geometry = null;   // gone since the search (trail data refreshed): fall back to a pin
      } finally {
        setPicking(false);
      }
    }
    const label = item.label || item.name;
    hm.showSearchResult({
      lat: item.lat, lon: item.lon, label, geometry, trailKind: item.trail_kind || "foot",
      bbox: geometry || item.kind === "town" ? item.bbox : null,
      zoom: KIND_ZOOM[item.kind] ?? 15, maxZoom: item.kind === "town" ? 14 : 16,
    });
    onResultChange(true);
    if (item.kind !== "coords") setQ(label);
    onClose();
  }

  function clear() {
    getHuntMap()?.clearSearchResult?.();
    onResultChange(false);
    setQ("");
    setResults([]);
    inputRef.current?.focus();
  }

  function onKeyDown(e) {
    if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => Math.min(items.length - 1, a + 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(0, a - 1)); }
    else if (e.key === "Enter") { e.preventDefault(); pick(items[active]); }
    else if (e.key === "Escape") { e.preventDefault(); onClose(); }
  }

  let note = null;
  if (status === "offline") note = "Name search needs a connection. Coordinates still work.";
  else if (status === "error") note = "Search isn't available right now. Try again in a moment.";
  else if (status === "ok" && !results.length) note = failed.length ? `No matches (couldn't reach: ${failed.join(", ")}).` : "No matches.";
  else if (!isCoords && text.length > 0 && text.length < MIN_QUERY) note = `Type at least ${MIN_QUERY} letters, or a lat/long.`;
  else if (!text) note = "Town, road, trail, creek, ridge… or a lat/long like 36.12, -93.56";

  return (
    <div className="map-search" ref={panelRef} role="search">
      <div className="map-search-bar">
        <Search size={16} className="map-search-glass" />
        <input ref={inputRef} value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={onKeyDown}
          placeholder="Search places, roads, trails, lat/long" aria-label="Search the map"
          autoComplete="off" autoCorrect="off" spellCheck={false} enterKeyHint="search" />
        {(status === "loading" || picking) && <Loader2 size={16} className="map-search-spin" />}
        {(q || hasResult) && (
          <button className="map-search-clear" onClick={clear} title="Clear search" aria-label="Clear search"><X size={16} /></button>
        )}
      </div>
      {(items.length > 0 || note) && (
        <ul className="map-search-list" role="listbox">
          {items.map((it, i) => (
            <li key={it.id || i} role="option" aria-selected={i === active}
              className={"map-search-item" + (i === active ? " active" : "")}
              onMouseEnter={() => setActive(i)} onClick={() => pick(it)}>
              <ResultIcon item={it} />
              <span className="map-search-text">
                <span className="map-search-name">{it.name}</span>
                {it.sub && <span className="map-search-sub">{it.sub}</span>}
              </span>
              {it.distance_mi != null && <span className="map-search-dist">{fmtDist(it.distance_mi * M_PER_MI)}</span>}
            </li>
          ))}
          {note && <li className="map-search-note">{note}</li>}
          {status === "ok" && results.length > 0 && failed.length > 0 && (
            <li className="map-search-note">Couldn't reach: {failed.join(", ")}.</li>
          )}
        </ul>
      )}
    </div>
  );
}
