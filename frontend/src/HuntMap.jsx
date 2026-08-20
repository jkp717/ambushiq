import React, { useEffect, useRef, useState, useCallback } from "react";

/* global L */

const DIRS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];
const degToCompass = (d) => DIRS[Math.round((((d % 360) + 360) % 360) / 22.5) % 16];

// USGS topo tile layers (public, no key). Imagery topo is the shaded relief + contours.
const USGS_TOPO = "https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer/tile/{z}/{y}/{x}";
const USGS_IMAGERY = "https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryTopo/MapServer/tile/{z}/{y}/{x}";

const COLORS = {
  wind: "#0C447C",
  thermal: "#185FA5",
  deer: "#A35A1B",
  bedding: "#6B4FA0",
  food: "#3B6D11",
  stand: "#A32D2D",
};

// Build an SVG divIcon for a stand showing wind (solid) + thermal (dashed) arrows.
function standIcon(vectors, rank) {
  const size = 78, c = size / 2;
  const arrow = (deg, color, dash, len, w) => {
    if (deg == null) return "";
    const rad = ((deg - 90) * Math.PI) / 180;
    const x2 = c + Math.cos(rad) * len, y2 = c + Math.sin(rad) * len;
    // arrowhead
    const ah = 6, a1 = rad + Math.PI - 0.4, a2 = rad + Math.PI + 0.4;
    const hx1 = x2 + Math.cos(a1) * ah, hy1 = y2 + Math.sin(a1) * ah;
    const hx2 = x2 + Math.cos(a2) * ah, hy2 = y2 + Math.sin(a2) * ah;
    return `<line x1="${c}" y1="${c}" x2="${x2}" y2="${y2}" stroke="${color}" stroke-width="${w}" ${dash ? 'stroke-dasharray="3 3"' : ""}/>
      <polyline points="${hx1},${hy1} ${x2},${y2} ${hx2},${hy2}" fill="none" stroke="${color}" stroke-width="${w}"/>`;
  };
  const deerArrow = vectors.deer_approach_deg != null
    ? (() => {
        // deer approach is the direction deer come FROM; draw arrow pointing toward stand (inward)
        const deg = (vectors.deer_approach_deg + 180) % 360;
        const rad = ((deg - 90) * Math.PI) / 180;
        const sx = c - Math.cos(rad) * 30, sy = c - Math.sin(rad) * 30;
        const ah = 5, a1 = rad + Math.PI - 0.4, a2 = rad + Math.PI + 0.4;
        const ex = c - Math.cos(rad) * 14, ey = c - Math.sin(rad) * 14;
        const hx1 = ex + Math.cos(a1) * ah, hy1 = ey + Math.sin(a1) * ah;
        const hx2 = ex + Math.cos(a2) * ah, hy2 = ey + Math.sin(a2) * ah;
        return `<line x1="${sx}" y1="${sy}" x2="${ex}" y2="${ey}" stroke="${COLORS.deer}" stroke-width="2.5"/>
          <polyline points="${hx1},${hy1} ${ex},${ey} ${hx2},${hy2}" fill="none" stroke="${COLORS.deer}" stroke-width="2.5"/>`;
      })()
    : "";

  const ring = rank === 0 ? `<circle cx="${c}" cy="${c}" r="11" fill="none" stroke="${COLORS.stand}" stroke-width="2.5"/>` : "";
  const html = `<svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" style="overflow:visible">
    ${vectors.wind_to_deg != null ? arrow(vectors.wind_to_deg, COLORS.wind, false, 30, 3) : ""}
    ${vectors.thermal_to_deg != null ? arrow(vectors.thermal_to_deg, COLORS.thermal, true, 24, 2.5) : ""}
    ${deerArrow}
    ${ring}
    <circle cx="${c}" cy="${c}" r="5.5" fill="${COLORS.stand}" stroke="#fff" stroke-width="1.5"/>
  </svg>`;
  return L.divIcon({ html, className: "stand-div-icon", iconSize: [size, size], iconAnchor: [c, c] });
}

// Build a popup with edit/delete buttons and wire them up after it opens.
function bindFeaturePopup(layer, { title, subtitle, kind, id, onEdit, onDelete }) {
  const html = `<div class="feat-popup">
    <div class="feat-popup-title">${title || "(unnamed)"}</div>
    ${subtitle ? `<div class="feat-popup-sub">${subtitle}</div>` : ""}
    <div class="feat-popup-actions">
      <button data-act="edit" class="feat-popup-btn">✎ Edit</button>
      <button data-act="del" class="feat-popup-btn feat-popup-del">🗑 Delete</button>
    </div>
  </div>`;
  layer.bindPopup(html, { closeButton: true, minWidth: 150 });
  layer.on("popupopen", (e) => {
    const root = e.popup.getElement();
    if (!root) return;
    const editBtn = root.querySelector('[data-act="edit"]');
    const delBtn = root.querySelector('[data-act="del"]');
    if (editBtn) editBtn.onclick = () => { layer.closePopup(); onEdit && onEdit(kind, id); };
    if (delBtn) delBtn.onclick = () => { layer.closePopup(); onDelete && onDelete(kind, id); };
  });
}

export default function HuntMap({
  stands, zones, corridors, conditions,
  drawMode, onMapClick, draftPoints, onFinishCorridor,
  layers, onEditFeature, onDeleteFeature, center,
}) {
  const mapRef = useRef(null);
  const mapEl = useRef(null);
  const layerGroups = useRef({});
  const baseLayers = useRef({});
  const [ready, setReady] = useState(false);

  // init map once (waits for the Leaflet global if the CDN script is slow)
  useEffect(() => {
    let tries = 0, timer = null;
    function tryInit() {
      if (mapRef.current || !mapEl.current) return;
      if (typeof L === "undefined") {
        if (tries++ < 50) { timer = setTimeout(tryInit, 100); }  // up to ~5s
        return;
      }
      const map = L.map(mapEl.current, { zoomControl: true });
      const topo = L.tileLayer(USGS_TOPO, { maxZoom: 16, attribution: "USGS The National Map" });
      const imagery = L.tileLayer(USGS_IMAGERY, { maxZoom: 16, attribution: "USGS The National Map" });
      topo.addTo(map);
      baseLayers.current = { Topo: topo, "Imagery+Topo": imagery };
      L.control.layers(baseLayers.current, null, { position: "topright", collapsed: true }).addTo(map);
      ["zones", "corridors", "stands", "draft"].forEach((k) => { layerGroups.current[k] = L.layerGroup().addTo(map); });
      mapRef.current = map;
      setReady(true);
      map.setView(center && center.lat != null ? [center.lat, center.lon] : [34.7, -92.3], 13);
    }
    tryInit();
    return () => { if (timer) clearTimeout(timer); if (mapRef.current) { mapRef.current.remove(); mapRef.current = null; } };
  }, []);

  // map click handler (for drawing)
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const handler = (e) => { if (drawMode && onMapClick) onMapClick({ lat: e.latlng.lat, lon: e.latlng.lng }); };
    map.on("click", handler);
    // cursor feedback
    if (mapEl.current) mapEl.current.style.cursor = drawMode ? "crosshair" : "";
    return () => { map.off("click", handler); };
  }, [drawMode, onMapClick]);

  // fit bounds to all features once when stands first arrive
  const fitted = useRef(false);
  useEffect(() => {
    const map = mapRef.current;
    if (!map || fitted.current) return;
    const pts = [...stands.map((s) => [s.lat, s.lon]), ...zones.map((z) => [z.lat, z.lon])];
    corridors.forEach((c) => c.points.forEach((p) => pts.push(p)));
    if (pts.length) { map.fitBounds(pts, { padding: [50, 50], maxZoom: 15 }); fitted.current = true; }
  }, [stands, zones, corridors]);

  // when there are no features to fit, follow the configured home center
  useEffect(() => {
    const map = mapRef.current;
    if (!map || fitted.current || !center || center.lat == null) return;
    const hasFeatures = stands.length || zones.length || corridors.length;
    if (!hasFeatures) map.setView([center.lat, center.lon], 13);
  }, [center, stands.length, zones.length, corridors.length]);

  // render zones
  useEffect(() => {
    if (!ready) return;
    const g = layerGroups.current.zones; g.clearLayers();
    if (!layers.zones) return;
    zones.forEach((z) => {
      const circle = L.circle([z.lat, z.lon], {
        radius: z.radius_m, color: COLORS[z.kind] || "#888", fillColor: COLORS[z.kind] || "#888",
        fillOpacity: 0.18, weight: 2, interactive: !drawMode,
      });
      if (!drawMode) bindFeaturePopup(circle, {
        title: z.name || `${z.kind} zone`, subtitle: `${z.kind} · ${z.radius_m} m`,
        kind: z.kind === "food" ? "food" : "bedding", id: z.id, onEdit: onEditFeature, onDelete: onDeleteFeature,
      });
      circle.addTo(g);
    });
  }, [zones, ready, layers.zones, drawMode, onEditFeature, onDeleteFeature]);

  // render corridors
  useEffect(() => {
    if (!ready) return;
    const g = layerGroups.current.corridors; g.clearLayers();
    if (!layers.corridors) return;
    corridors.forEach((c) => {
      const line = L.polyline(c.points, { color: COLORS.deer, weight: 3, opacity: 0.8, dashArray: "1 6", lineCap: "round", interactive: !drawMode });
      if (!drawMode) bindFeaturePopup(line, {
        title: c.name || "deer corridor", subtitle: `${c.points.length} points`,
        kind: "corridor", id: c.id, onEdit: onEditFeature, onDelete: onDeleteFeature,
      });
      line.addTo(g);
      // arrowheads along the line showing travel direction (last segment)
      if (c.points.length >= 2) {
        const [a, b] = [c.points[c.points.length - 2], c.points[c.points.length - 1]];
        const ang = Math.atan2(b[0] - a[0], b[1] - a[1]);
        L.marker(b, {
          interactive: false,
          icon: L.divIcon({
            className: "corridor-arrow",
            html: `<div style="transform:rotate(${-ang * 180 / Math.PI}deg);color:${COLORS.deer};font-size:18px;line-height:1">▶</div>`,
            iconSize: [18, 18], iconAnchor: [9, 9],
          }),
        }).addTo(g);
      }
    });
  }, [corridors, ready, layers.corridors, drawMode, onEditFeature, onDeleteFeature]);

  // render stands with indicators
  useEffect(() => {
    if (!ready) return;
    const g = layerGroups.current.stands; g.clearLayers();
    const byId = {};
    (conditions?.stands || []).forEach((it) => { byId[it.stand.id] = it.vectors; });
    const rankIndex = {};
    (conditions?.ranked || []).forEach((r, i) => { rankIndex[r.stand.id] = i; });
    stands.forEach((s) => {
      const v = byId[s.id] || {};
      const vectors = {
        wind_to_deg: layers.wind ? v.wind_to_deg : null,
        thermal_to_deg: layers.thermal ? v.thermal_to_deg : null,
        deer_approach_deg: layers.deer ? s.deer_approach_deg : null,
      };
      const rank = rankIndex[s.id] ?? 99;
      const m = L.marker([s.lat, s.lon], { icon: standIcon(vectors, rank), interactive: !drawMode });
      if (!drawMode) {
        const windTxt = v.wind_to_deg != null ? `Wind → ${degToCompass(v.wind_to_deg)} ${v.wind_speed}mph` : "";
        const thermTxt = v.thermal_to_deg != null ? `Thermal ${v.thermal_phase} → ${degToCompass(v.thermal_to_deg)}` : "";
        bindFeaturePopup(m, {
          title: s.name, subtitle: [windTxt, thermTxt].filter(Boolean).join(" · "),
          kind: "stand", id: s.id, onEdit: onEditFeature, onDelete: onDeleteFeature,
        });
      }
      m.addTo(g);
    });
  }, [stands, conditions, ready, layers.wind, layers.thermal, layers.deer, drawMode, onEditFeature, onDeleteFeature]);

  // render draft (in-progress drawing)
  useEffect(() => {
    if (!ready) return;
    const g = layerGroups.current.draft; g.clearLayers();
    if (!draftPoints || !draftPoints.length) return;
    draftPoints.forEach((p) => L.circleMarker([p.lat, p.lon], { radius: 4, color: COLORS.deer, fillOpacity: 1 }).addTo(g));
    if (draftPoints.length >= 2) {
      L.polyline(draftPoints.map((p) => [p.lat, p.lon]), { color: COLORS.deer, weight: 2, dashArray: "4 4" }).addTo(g);
    }
  }, [draftPoints, ready]);

  return <div ref={mapEl} style={{ height: 420, width: "100%", borderRadius: 12, overflow: "hidden", border: "1px solid var(--bord)" }} />;
}
