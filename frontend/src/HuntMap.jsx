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
  // The clickable hit-area is a small div (dotPx × dotPx) centered on the stand dot.
  // The SVG is absolutely offset so its visual center aligns with the div center, but
  // pointer-events:none on the SVG means only the tiny div registers clicks — arrows
  // don't expand the selection area.
  const dotPx = 14, dotHalf = dotPx / 2;
  const svgOff = dotHalf - c; // negative: shifts SVG up-left so (c,c) lands at (dotHalf,dotHalf)
  const html = `<div style="position:relative;width:${dotPx}px;height:${dotPx}px;overflow:visible">
    <svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}"
      style="overflow:visible;position:absolute;left:${svgOff}px;top:${svgOff}px;pointer-events:none">
      ${vectors.wind_to_deg != null ? arrow(vectors.wind_to_deg, COLORS.wind, false, 30, 3) : ""}
      ${vectors.thermal_to_deg != null ? arrow(vectors.thermal_to_deg, COLORS.thermal, true, 24, 2.5) : ""}
      ${deerArrow}
      ${ring}
      <circle cx="${c}" cy="${c}" r="5.5" fill="${COLORS.stand}" stroke="#fff" stroke-width="1.5"/>
    </svg>
  </div>`;
  return L.divIcon({ html, className: "stand-div-icon", iconSize: [dotPx, dotPx], iconAnchor: [dotHalf, dotHalf] });
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
  height = 420,
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
      // "scent" is added before "stands" so cones render below stand markers
      ["zones", "corridors", "scent", "stands", "draft"].forEach((k) => { layerGroups.current[k] = L.layerGroup().addTo(map); });
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
      const active = !!z.is_active;
      const baseColor = COLORS[z.kind] || "#888";
      const circle = L.circle([z.lat, z.lon], {
        radius: z.radius_m,
        color:       active ? baseColor : "#888",
        fillColor:   active ? baseColor : "#888",
        fillOpacity: active ? 0.18 : 0.04,
        opacity:     active ? 1    : 0.4,
        weight:      active ? 2    : 1.5,
        dashArray:   active ? null : "6 5",
        interactive: !drawMode,
      });
      if (!drawMode) bindFeaturePopup(circle, {
        title: z.name || `${z.kind} zone`,
        subtitle: `${z.kind} · ${z.radius_m} m${active ? "" : " · inactive"}`,
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

  // render scent cones — geographic sector from each stand in the blended scent direction
  useEffect(() => {
    if (!ready) return;
    const g = layerGroups.current.scent; g.clearLayers();
    if (!layers.scent || !conditions) return;
    const byId = {};
    (conditions.stands || []).forEach((it) => { byId[it.stand.id] = it.vectors; });

    // Move distM metres along bearingDeg from [lat, lon]; returns [lat, lon]
    function bearingPoint(lat, lon, bearingDeg, distM) {
      const R = 6371000;
      const lat1 = lat * Math.PI / 180, lon1 = lon * Math.PI / 180;
      const brng = bearingDeg * Math.PI / 180, d = distM / R;
      const lat2 = Math.asin(Math.sin(lat1) * Math.cos(d) + Math.cos(lat1) * Math.sin(d) * Math.cos(brng));
      const lon2 = lon1 + Math.atan2(Math.sin(brng) * Math.sin(d) * Math.cos(lat1), Math.cos(d) - Math.sin(lat1) * Math.sin(lat2));
      return [lat2 * 180 / Math.PI, lon2 * 180 / Math.PI];
    }

    stands.forEach((s) => {
      const v = byId[s.id];
      if (!v || v.scent_to_deg == null) return;

      const windSpeed  = v.wind_speed  ?? 0;
      const gust       = v.gust        ?? windSpeed;
      const scentDeg   = v.scent_to_deg;
      const scentScore = v.scent_score ?? 1;

      // Length: proportional to wind speed (60–300 m)
      const lengthM  = Math.max(60,  Math.min(300, 40 + windSpeed * 12));
      // Half-angle: grows with gust spread — gusty = wide fan, steady = tight beam (8–50°)
      const gustSpread = Math.max(0, gust - windSpeed);
      const halfAngle  = Math.max(8,  Math.min(50,  10 + gustSpread * 5));

      // Colour: green (away from deer) → amber → red (toward deer)
      const color = scentScore > 0.6 ? "#2D8A2D" : scentScore > 0.35 ? "#C28800" : "#C0392B";

      // Build sector: apex → arc of points along the far edge → close
      const ARC_STEPS = 10;
      const pts = [[s.lat, s.lon]];
      for (let i = 0; i <= ARC_STEPS; i++) {
        const ang = (scentDeg - halfAngle) + (2 * halfAngle * i / ARC_STEPS);
        pts.push(bearingPoint(s.lat, s.lon, (ang + 360) % 360, lengthM));
      }

      L.polygon(pts, {
        color, fillColor: color, fillOpacity: 0.22, weight: 1, opacity: 0.55,
        interactive: false, // never intercepts clicks
      }).addTo(g);
    });
  }, [stands, conditions, ready, layers.scent]);

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

  return <div ref={mapEl} style={{ height, width: "100%", overflow: "hidden" }} />;
}
