import React, { useEffect, useRef, useState, useCallback, forwardRef, useImperativeHandle } from "react";
import { TILE_SOURCES } from "../utils/tileSources.js";
import { clamp } from "../utils/geo.js";
import { api } from "../services/api.js";

/* global L */

const DIRS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];
const degToCompass = (d) => DIRS[Math.round((((d % 360) + 360) % 360) / 22.5) % 16];

// USGS topo tile layers (public, no key). Imagery topo is the shaded relief + contours.
const USGS_TOPO = TILE_SOURCES.find((t) => t.id === "topo").url;
const USGS_IMAGERY = TILE_SOURCES.find((t) => t.id === "imagery").url;

const COLORS = {
  wind: "#0C447C",
  thermal: "#185FA5",
  deer: "#A35A1B",
  bedding: "#6B4FA0",
  food: "#3B6D11",
  stand: "#A32D2D",
  suggestion: "#0E8A7D",
  selected: "#F5A300",
  userLocation: "#4285F4",   // Google's location blue
};

// Public land (USGS PAD-US via /api/public-lands). Outline + tint by kind of land; closed land is a red dashed outline.
const PUBLIC_LAND_MIN_ZOOM = 11;
const LAND_COLORS = {
  wildlife: "#D81B60", forest: "#2E8B57", refuge: "#1976D2", federal: "#B8860B", state: "#5C6BC0", other: "#757575",
};
const LAND_KIND_LABEL = {
  wildlife: "State wildlife area (likely a WMA)", forest: "National forest / Forest Service land",
  refuge: "National wildlife refuge", federal: "Federal land", state: "State land", other: "Other protected land",
};
const LAND_ACCESS_LABEL = {
  open: "Open to the public", restricted: "Restricted (permit, season or other limits)",
  closed: "Closed to the public", unknown: "Unknown",
};
const escHtml = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function landStyle(p, interactive) {
  const closed = p.access === "closed";
  return {
    color: closed ? "#C62828" : LAND_COLORS[p.kind] || LAND_COLORS.other,
    weight: p.kind === "wildlife" ? 2.5 : 1.5, dashArray: closed ? "6 5" : null,
    fillColor: LAND_COLORS[p.kind] || LAND_COLORS.other, fillOpacity: 0.14, interactive,
  };
}

function landPopup(p) {
  const agfc = p.source === "AGFC";   // official Arkansas Game & Fish boundary, so no "likely"
  const kind = agfc ? "Wildlife management area" : LAND_KIND_LABEL[p.kind] || LAND_KIND_LABEL.other;
  const note = agfc
    ? "Boundary published by the Arkansas Game & Fish Commission (via the Arkansas GIS Office) for reference purposes only. WMAs usually need a hunting license and follow their own rules. Verify seasons, permits and boundaries with the AGFC."
    : `Shows who manages the land and its general public access, not hunting rules. ${p.kind === "wildlife" ? "Wildlife areas usually need a hunting license and follow their own rules. " : ""}Verify seasons, permits and boundaries with the managing agency.`;
  return `<div class="feat-popup">
    <div class="feat-popup-title">${escHtml(p.name)}</div>
    <div class="feat-popup-sub">${escHtml(kind)} · ${escHtml(p.designation)}</div>
    <div class="land-row"><b>Manager:</b> ${escHtml(p.manager)}</div>
    <div class="land-row"><b>Public access:</b> ${escHtml(LAND_ACCESS_LABEL[p.access] || LAND_ACCESS_LABEL.unknown)}</div>
    <div class="land-row"><b>Source:</b> ${escHtml(agfc ? "Arkansas Game & Fish Commission" : "USGS PAD-US")}</div>
    <div class="land-note">${note}</div>
  </div>`;
}

// Build an SVG divIcon for a stand showing wind (solid) + thermal (dashed) arrows.
function standIcon(vectors, rank, selected = false) {
  const size = 78, c = size / 2;
  // Arrow length tracks magnitude so stands with very different conditions don't look identical:
  // wind by speed (mph), thermal by its blended weight (0..1).
  const windLen = Math.max(16, Math.min(34, 14 + (vectors.wind_speed ?? 6) * 1.5));
  const thermalLen = 12 + 22 * Math.max(0, Math.min(1, vectors.thermal_strength ?? 0.5));
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

  const ring = (rank === 0 ? `<circle cx="${c}" cy="${c}" r="11" fill="none" stroke="${COLORS.stand}" stroke-width="2.5"/>` : "")
    + (selected ? `<circle cx="${c}" cy="${c}" r="15" fill="rgba(245,163,0,.25)" stroke="${COLORS.selected}" stroke-width="3.5"/>` : "");
  // The clickable hit-area is a small div (dotPx × dotPx) centered on the stand dot.
  // The SVG is absolutely offset so its visual center aligns with the div center, but
  // pointer-events:none on the SVG means only the tiny div registers clicks — arrows
  // don't expand the selection area.
  const dotPx = 14, dotHalf = dotPx / 2;
  const svgOff = dotHalf - c; // negative: shifts SVG up-left so (c,c) lands at (dotHalf,dotHalf)
  const html = `<div style="position:relative;width:${dotPx}px;height:${dotPx}px;overflow:visible">
    <svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}"
      style="overflow:visible;position:absolute;left:${svgOff}px;top:${svgOff}px;pointer-events:none">
      ${vectors.wind_to_deg != null ? arrow(vectors.wind_to_deg, COLORS.wind, false, windLen, 3) : ""}
      ${vectors.thermal_to_deg != null ? arrow(vectors.thermal_to_deg, COLORS.thermal, true, thermalLen, 2.5) : ""}
      ${deerArrow}
      ${ring}
      <circle cx="${c}" cy="${c}" r="5.5" fill="${COLORS.stand}" stroke="#fff" stroke-width="1.5"/>
    </svg>
  </div>`;
  return L.divIcon({ html, className: "stand-div-icon", iconSize: [dotPx, dotPx], iconAnchor: [dotHalf, dotHalf] });
}

// Build a popup with edit/delete buttons and wire them up after it opens.
function bindFeaturePopup(layer, { title, subtitle, kind, id, onEdit, onDelete, sl, onToggleStandLayer,
                                    dismissed, onDismiss }) {
  let html = `<div class="feat-popup">
    <div class="feat-popup-title">${title || "(unnamed)"}</div>
    ${subtitle ? `<div class="feat-popup-sub">${subtitle}</div>` : ""}
  `;

  if (kind === "stand") {
    const s = sl || { wind: true, thermal: true, scent: true, deer: true, flow: false };
    html += `
      <div class="feat-popup-toggles" style="display:flex; flex-direction:column; gap:6px; margin: 10px 0; border-top: 1px solid var(--bord); border-bottom: 1px solid var(--bord); padding: 8px 0;">
        <label style="font-size:12px; display:flex; gap:6px; align-items:center; cursor:pointer;"><input type="checkbox" data-layer="wind" ${s.wind ? 'checked' : ''}> Wind</label>
        <label style="font-size:12px; display:flex; gap:6px; align-items:center; cursor:pointer;"><input type="checkbox" data-layer="thermal" ${s.thermal ? 'checked' : ''}> Thermal</label>
        <label style="font-size:12px; display:flex; gap:6px; align-items:center; cursor:pointer;"><input type="checkbox" data-layer="scent" ${s.scent ? 'checked' : ''}> Scent</label>
        <label style="font-size:12px; display:flex; gap:6px; align-items:center; cursor:pointer;"><input type="checkbox" data-layer="deer" ${s.deer ? 'checked' : ''}> Deer</label>
        <label style="font-size:12px; display:flex; gap:6px; align-items:center; cursor:pointer;"><input type="checkbox" data-layer="flow" ${s.flow ? 'checked' : ''}> Drainage Flow</label>
      </div>
    `;
  }

  if (kind === "suggestion") {
    html += `<div class="feat-popup-actions">
        <button data-act="dismiss" class="feat-popup-btn">${dismissed ? "↺ Restore" : "✕ Dismiss"}</button>
        <button data-act="del" class="feat-popup-btn feat-popup-del">🗑 Delete</button>
      </div>
    </div>`;
  } else {
    html += `<div class="feat-popup-actions">
        <button data-act="edit" class="feat-popup-btn">✎ Edit</button>
        <button data-act="del" class="feat-popup-btn feat-popup-del">🗑 Delete</button>
      </div>
    </div>`;
  }

  // Check if the popup is currently open before we overwrite it
  const isOpen = layer.isPopupOpen && layer.isPopupOpen();
  
  layer.bindPopup(html, { closeButton: true, minWidth: 150 });

  // Helper function to attach listeners so we can call it dynamically
  const attachListeners = (popupElement) => {
    if (!popupElement) return;
    const editBtn = popupElement.querySelector('[data-act="edit"]');
    const delBtn = popupElement.querySelector('[data-act="del"]');
    const dismissBtn = popupElement.querySelector('[data-act="dismiss"]');
    if (editBtn) editBtn.onclick = () => { layer.closePopup(); onEdit && onEdit(kind, id); };
    if (delBtn) delBtn.onclick = () => { layer.closePopup(); onDelete && onDelete(kind, id); };
    if (dismissBtn) dismissBtn.onclick = () => { layer.closePopup(); onDismiss && onDismiss(id, dismissed); };

    if (kind === "stand" && onToggleStandLayer) {
      popupElement.querySelectorAll('input[type="checkbox"][data-layer]').forEach(cb => {
        cb.onchange = (ev) => {
          onToggleStandLayer(id, ev.target.dataset.layer);
        };
      });
    }
  };

  layer.off("popupopen"); 
  layer.on("popupopen", (e) => attachListeners(e.popup.getElement()));

  // If it was already open, bindPopup replaced the HTML instantly but the "popupopen" event 
  // won't fire again. We must manually reattach the listeners to the new live DOM elements.
  if (isOpen) {
    attachListeners(layer.getPopup().getElement());
  }
}

const HuntMap = forwardRef(function HuntMap({
  stands, zones, corridors, sign, suggestions, conditions,
  drawMode, onMapClick, draftPoints, onFinishCorridor,
  layers, standLayers, onToggleStandLayer, onEditFeature, onDeleteFeature, onDismissSuggestion, center,
  scoutDraft, onScoutRadiusChange, scoutRadiusMin, scoutRadiusMax,
  selectMode = false, selectedKeys, onToggleSelect, boxTool = false, onBoxSelect,
  userLocation = null, onPublicLandStatus,
  height = 420,
}, ref) {
  const userRefs = useRef({ marker: null, circle: null });
  // Multi-select: every selectable feature is identified by a "kind:id" key. In select mode
  // popups are off and a click toggles the key; `interactive` covers the box tool, which needs
  // the pointer for itself.
  const isSelected = (kind, id) => selectMode && !!selectedKeys?.has(`${kind}:${id}`);
  const interactiveFor = () => (selectMode ? !boxTool : !drawMode);
  const bindToggle = (layer, kind, id) => layer.on("click", (e) => {
    L.DomEvent.stopPropagation(e);
    onToggleSelect && onToggleSelect(`${kind}:${id}`);
  });
  const selectRef = useRef({});
  selectRef.current = { selectMode, onToggleSelect };

  const mapRef = useRef(null);
  const mapEl = useRef(null);
  const layerGroups = useRef({});
  const baseLayers = useRef({});
  const offlineLayers = useRef({});
  const standsMarkers = useRef({}); // Prevents unmounting marker to keep popup open
  const scoutDraftLayer = useRef(null);
  const [ready, setReady] = useState(false);

  // Exposes the underlying Leaflet map + base tile layers for callers that need
  // to drive them directly (currently: the offline-tile download panel).
  useImperativeHandle(ref, () => ({
    getMap: () => mapRef.current,
    getBaseLayers: () => offlineLayers.current,
  }));

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
      // Public land gets its own pane below the vector overlay pane (400). Leaflet stacks shapes in the order
      // they were added, and the land arrives after your zones, corridors and suggestions have been drawn, so
      // without this it would sit on top of them and swallow their clicks.
      map.createPane("publicLandPane").style.zIndex = 380;
      // .offline (from the leaflet.offline CDN bundle) transparently serves a tile
      // from IndexedDB when it's been downloaded for offline use, network otherwise.
      const topo = L.tileLayer.offline(USGS_TOPO, { maxZoom: 16, attribution: "USGS The National Map" });
      const imagery = L.tileLayer.offline(USGS_IMAGERY, { maxZoom: 16, attribution: "USGS The National Map" });
      topo.addTo(map);
      baseLayers.current = { Topo: topo, "Imagery+Topo": imagery };
      // id-keyed (matches TILE_SOURCES) for the offline-download panel, which
      // needs each layer instance's getTileUrls() and its exact urlTemplate.
      offlineLayers.current = {
        topo: { ...TILE_SOURCES.find((t) => t.id === "topo"), layer: topo },
        imagery: { ...TILE_SOURCES.find((t) => t.id === "imagery"), layer: imagery },
      };
      L.control.layers(baseLayers.current, null, { position: "topright", collapsed: true }).addTo(map);
      // "publicLand" is drawn in its own lower pane (see above), so every other layer is above the land tint
      // "scent" is added before "stands" so cones render below stand markers
      // "location" (the device's blue dot) goes last so it draws above everything else
      ["publicLand", "zones", "corridors", "scrapes", "rubs", "scent", "stands", "draft", "flow", "suggestions", "location"].forEach((k) => { layerGroups.current[k] = L.layerGroup().addTo(map); });
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

  // when there are no features to fit, follow the active region's center
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
      const sel = isSelected("zone", z.id);
      const baseColor = sel ? COLORS.selected : (COLORS[z.kind] || "#888");
      const circle = L.circle([z.lat, z.lon], {
        radius: z.radius_m,
        color:       active || sel ? baseColor : "#888",
        fillColor:   active || sel ? baseColor : "#888",
        fillOpacity: sel ? 0.45 : active ? 0.18 : 0.04,
        opacity:     active || sel ? 1    : 0.4,
        weight:      sel ? 4 : active ? 2    : 1.5,
        dashArray:   active || sel ? null : "6 5",
        interactive: interactiveFor(),
      });
      if (selectMode) bindToggle(circle, "zone", z.id);
      else if (!drawMode) bindFeaturePopup(circle, {
        title: z.name || `${z.kind} zone`,
        subtitle: `${z.kind} · ${z.radius_m} m${active ? "" : " · inactive"}`,
        kind: z.kind === "food" ? "food" : "bedding", id: z.id, onEdit: onEditFeature, onDelete: onDeleteFeature,
      });
      circle.addTo(g);
    });
  }, [zones, ready, layers.zones, drawMode, onEditFeature, onDeleteFeature, selectMode, selectedKeys, boxTool, onToggleSelect]);

  // render corridors
  useEffect(() => {
    if (!ready) return;
    const g = layerGroups.current.corridors; g.clearLayers();
    if (!layers.corridors) return;
    corridors.forEach((c) => {
      const sel = isSelected("corridor", c.id);
      const line = L.polyline(c.points, sel
        ? { color: COLORS.selected, weight: 6, opacity: 1, lineCap: "round", interactive: interactiveFor() }
        : { color: COLORS.deer, weight: 3, opacity: 0.8, dashArray: "1 6", lineCap: "round", interactive: interactiveFor() });
      if (selectMode) bindToggle(line, "corridor", c.id);
      else if (!drawMode) bindFeaturePopup(line, {
        title: c.name || "deer corridor", subtitle: `${c.points.length} points`,
        kind: "corridor", id: c.id, onEdit: onEditFeature, onDelete: onDeleteFeature,
      });
      line.addTo(g);
    });
  }, [corridors, ready, layers.corridors, drawMode, onEditFeature, onDeleteFeature, selectMode, selectedKeys, boxTool, onToggleSelect]);

  // render deer sign (scrapes + rubs)
  useEffect(() => {
    if (!ready) return;
    const gS = layerGroups.current.scrapes; gS.clearLayers();
    const gR = layerGroups.current.rubs;    gR.clearLayers();
    (sign || []).forEach((sg) => {
      const isScrape = sg.kind === "scrape";
      if (isScrape  && !layers.scrapes) return;
      if (!isScrape && !layers.rubs)    return;
      const color = isScrape ? "#E87800" : "#8B3A1A";
      const g     = isScrape ? gS : gR;
      const sel   = isSelected("sign", sg.id);
      const m = L.circleMarker([sg.lat, sg.lon], sel
        ? { radius: 10, color: COLORS.selected, fillColor: color, fillOpacity: 0.95, weight: 4, interactive: interactiveFor() }
        : { radius: 7, color, fillColor: color, fillOpacity: 0.8, weight: 2, interactive: interactiveFor() });
      if (selectMode) {
        bindToggle(m, "sign", sg.id);
      } else if (!drawMode) {
        bindFeaturePopup(m, {
          title: sg.name,
          subtitle: `${sg.kind} · ${(+sg.lat).toFixed(4)}, ${(+sg.lon).toFixed(4)}`,
          kind: sg.kind, id: sg.id, onEdit: onEditFeature, onDelete: onDeleteFeature,
        });
      }
      m.addTo(g);
    });
  }, [sign, ready, layers.scrapes, layers.rubs, drawMode, onEditFeature, onDeleteFeature, selectMode, selectedKeys, boxTool, onToggleSelect]);

  // render scout-analysis draft circle: drag-to-resize, mirrors MiniMap.jsx's
  // Geoman pattern (L.circle + circle.pm.enable) ported onto the main map.
  useEffect(() => {
    if (!ready) return;
    const map = mapRef.current;
    if (drawMode !== "scout" || !scoutDraft) return undefined;
    const minR = scoutRadiusMin ?? 60, maxR = scoutRadiusMax ?? 2400;
    const circle = L.circle([scoutDraft.lat, scoutDraft.lon], {
      radius: scoutDraft.radius_m, color: COLORS.suggestion, fillColor: COLORS.suggestion,
      fillOpacity: 0.12, weight: 2, dashArray: "4 4",
    }).addTo(map);
    if (circle.pm) {
      circle.pm.enable({ allowEditing: true });
      const emit = () => {
        const raw = circle.getRadius();
        const clamped = clamp(raw, minR, maxR);
        if (clamped !== raw) circle.setRadius(clamped); // snap back past the bound
        onScoutRadiusChange && onScoutRadiusChange(clamped);
      };
      circle.on("pm:edit", emit);
      circle.on("pm:dragend", emit);
    }
    map.fitBounds(circle.getBounds(), { padding: [40, 40] });
    scoutDraftLayer.current = circle;
    return () => { map.removeLayer(circle); scoutDraftLayer.current = null; };
    // scoutDraft.radius_m intentionally excluded — re-running this effect on every
    // drag tick would fight the user's own resize gesture.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, drawMode, scoutDraft?.lat, scoutDraft?.lon, scoutRadiusMin, scoutRadiusMax, onScoutRadiusChange]);

  // render scouting suggestions
  useEffect(() => {
    if (!ready) return;
    const g = layerGroups.current.suggestions; g.clearLayers();
    if (!layers.suggestions) return;
    (suggestions || []).forEach((sg) => {
      const dismissed = sg.status === "dismissed";
      const selected = isSelected("suggestion", sg.id);
      const color = selected ? COLORS.selected : COLORS.suggestion;
      const circle = L.circle([sg.lat, sg.lon], {
        radius: sg.radius_m,
        color, fillColor: color,
        fillOpacity: selected ? 0.55 : dismissed ? 0.06 : 0.15 + 0.35 * (Math.max(0, Math.min(100, sg.score)) / 100),
        opacity: selected ? 1 : dismissed ? 0.35 : 0.9,
        weight: selected ? 4 : 2,
        dashArray: dismissed && !selected ? "3 5" : null,
        interactive: interactiveFor(),
      });
      if (selectMode) {
        bindToggle(circle, "suggestion", sg.id);
      } else if (!drawMode) {
        bindFeaturePopup(circle, {
          title: `Scouting suggestion (${Math.round(sg.score)}/100)`,
          subtitle: sg.reasoning?.text || "",
          kind: "suggestion", id: sg.id,
          dismissed, onDismiss: onDismissSuggestion, onDelete: onDeleteFeature,
        });
      }
      circle.addTo(g);
    });
  }, [suggestions, ready, layers.suggestions, drawMode, onDismissSuggestion, onDeleteFeature,
      selectMode, selectedKeys, boxTool, onToggleSelect]);

  // The device's location, drawn like Google Maps: blue dot with a white ring and pulsing halo, a
  // translucent accuracy circle, and a heading beam when a direction is known. Created once and
  // then updated in place; never interactive, and it never moves the map.
  useEffect(() => {
    if (!ready) return;
    const g = layerGroups.current.location;
    const r = userRefs.current;
    if (!userLocation) {
      g.clearLayers();
      r.marker = null; r.circle = null;
      return;
    }
    const ll = [userLocation.lat, userLocation.lon];
    const accuracy = Math.max(0, userLocation.accuracy || 0);
    const showCircle = accuracy >= 6;   // smaller than the dot itself: don't draw it
    const circleStyle = { opacity: showCircle ? 0.35 : 0, fillOpacity: showCircle ? 0.12 : 0 };
    if (!r.circle) {
      r.circle = L.circle(ll, {
        radius: accuracy, color: COLORS.userLocation, fillColor: COLORS.userLocation, weight: 1,
        interactive: false, ...circleStyle,
      }).addTo(g);
    } else {
      r.circle.setLatLng(ll); r.circle.setRadius(accuracy); r.circle.setStyle(circleStyle);
    }
    if (!r.marker) {
      r.marker = L.marker(ll, {
        icon: L.divIcon({
          className: "user-loc-icon", iconSize: [22, 22], iconAnchor: [11, 11],
          html: '<div class="user-loc"><div class="user-loc-beam"></div><div class="user-loc-pulse"></div><div class="user-loc-dot"></div></div>',
        }),
        interactive: false, keyboard: false, zIndexOffset: 1000,
      }).addTo(g);
    } else {
      r.marker.setLatLng(ll);
    }
    const beam = r.marker.getElement() && r.marker.getElement().querySelector(".user-loc-beam");
    if (beam) {
      if (userLocation.heading == null) beam.style.display = "none";
      else { beam.style.display = "block"; beam.style.transform = `rotate(${userLocation.heading}deg)`; }
    }
  }, [userLocation, ready]);

  // Public land: fetch the boundaries for the visible area whenever the map settles (debounced) and draw them
  // as GeoJSON. The fetched features are kept in a ref so switching draw / select mode only restyles them.
  const landData = useRef({ key: "", features: [] });
  const landStatusCb = useRef(onPublicLandStatus);
  landStatusCb.current = onPublicLandStatus;
  const drawLandRef = useRef(() => {});
  drawLandRef.current = () => {
    const g = layerGroups.current.publicLand;
    if (!g) return;
    g.clearLayers();
    const { features } = landData.current;
    if (!features.length) return;
    const interactive = !drawMode && !selectMode;   // clicks must reach the map while drawing or selecting
    L.geoJSON({ type: "FeatureCollection", features }, {
      pane: "publicLandPane",
      style: (f) => landStyle(f.properties, interactive),
      onEachFeature: (f, layer) => { if (interactive) layer.bindPopup(landPopup(f.properties), { minWidth: 200 }); },
    }).addTo(g);
  };
  useEffect(() => { if (ready) drawLandRef.current(); }, [ready, drawMode, selectMode]);

  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return undefined;
    const say = (s) => landStatusCb.current && landStatusCb.current(s);
    const reset = () => { landData.current = { key: "", features: [] }; drawLandRef.current(); };
    if (!layers.publicLand) { reset(); say("off"); return undefined; }

    let timer = null, ctrl = null, seq = 0;
    const load = async () => {
      const zoom = map.getZoom();
      if (zoom < PUBLIC_LAND_MIN_ZOOM) { if (ctrl) ctrl.abort(); reset(); say("zoom"); return; }
      const b = map.getBounds();
      if (ctrl) ctrl.abort();
      ctrl = new AbortController();
      const mine = ++seq;
      say("loading");
      try {
        const q = new URLSearchParams({ south: b.getSouth(), west: b.getWest(), north: b.getNorth(), east: b.getEast(), zoom });
        const j = await api(`/public-lands?${q}`, { signal: ctrl.signal });
        if (mine !== seq) return;
        const key = j.features.map((f) => f.id).sort().join(",");
        if (key !== landData.current.key) { landData.current = { key, features: j.features }; drawLandRef.current(); }
        say(j.partial ? "partial" : "ok");
      } catch (e) {
        if (e.name === "AbortError" || mine !== seq) return;
        say("error");
      }
    };
    const schedule = () => { clearTimeout(timer); timer = setTimeout(load, 450); };
    map.on("moveend", schedule);
    schedule();
    return () => { clearTimeout(timer); if (ctrl) ctrl.abort(); map.off("moveend", schedule); };
  }, [ready, layers.publicLand]);

  // Select mode box tool: drag a rectangle (desktop: hold Shift; touch: turn the Box tool on) and
  // report the keys of every visible feature it covers. Uses raw pointer events on the map
  // container because Leaflet's own mouse events don't fire during a touch drag.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready || !selectMode) return;
    const el = map.getContainer();

    // Points (stands, zones, sign, suggestions) count by their center; a corridor counts if any
    // vertex is inside the box or any segment crosses it. Hidden layers are not selectable.
    const boxKeys = (bounds) => {
      const inside = (lat, lon) => bounds.contains([lat, lon]);
      const planar = L.bounds(L.point(bounds.getWest(), bounds.getSouth()), L.point(bounds.getEast(), bounds.getNorth()));
      const lineHits = (pts) => pts.some(([lat, lon]) => inside(lat, lon))
        || pts.some((p, i) => i > 0 && L.LineUtil.clipSegment(L.point(pts[i - 1][1], pts[i - 1][0]), L.point(p[1], p[0]), planar));
      const keys = [];
      stands.forEach((s) => { if (inside(s.lat, s.lon)) keys.push(`stand:${s.id}`); });
      if (layers.zones) zones.forEach((z) => { if (inside(z.lat, z.lon)) keys.push(`zone:${z.id}`); });
      if (layers.corridors) corridors.forEach((c) => { if (lineHits(c.points)) keys.push(`corridor:${c.id}`); });
      (sign || []).forEach((sg) => {
        if ((sg.kind === "scrape" ? layers.scrapes : layers.rubs) && inside(sg.lat, sg.lon)) keys.push(`sign:${sg.id}`);
      });
      if (layers.suggestions) (suggestions || []).forEach((sg) => { if (inside(sg.lat, sg.lon)) keys.push(`suggestion:${sg.id}`); });
      return keys;
    };
    let shiftHeld = false, pointerId = null, start = null, rect = null;

    const setDragging = (on) => {
      if (on) { map.dragging.enable(); map.boxZoom.enable(); } else { map.dragging.disable(); map.boxZoom.disable(); }
    };
    const applyIdle = () => {
      const tool = boxTool || shiftHeld;
      setDragging(!tool);
      el.style.touchAction = boxTool ? "none" : "";   // pinch-zoom (Leaflet touch events) still works
      el.style.cursor = tool ? "crosshair" : "";
    };
    const cancelBox = () => {
      if (rect) { map.removeLayer(rect); rect = null; }
      start = null; pointerId = null;
    };
    const onKey = (e) => {
      if (e.key !== "Shift") return;
      shiftHeld = e.type === "keydown";
      if (!start) applyIdle();
    };
    const onDown = (e) => {
      if (e.target.closest && e.target.closest(".leaflet-control")) return;
      if (start && e.pointerId !== pointerId) { cancelBox(); return; }   // second finger = pinch, not a box
      if (!(boxTool || shiftHeld) || (e.pointerType === "mouse" && e.button !== 0)) return;
      pointerId = e.pointerId;
      start = map.mouseEventToLatLng(e);
      rect = L.rectangle([start, start], { color: COLORS.selected, weight: 2, dashArray: "4 4", fillOpacity: 0.12, interactive: false }).addTo(map);
      try { el.setPointerCapture(e.pointerId); } catch { /* not all browsers allow it */ }
      e.preventDefault();
    };
    const onMove = (e) => {
      if (!start || e.pointerId !== pointerId) return;
      rect.setBounds(L.latLngBounds(start, map.mouseEventToLatLng(e)));
    };
    const onUp = (e) => {
      if (!start || e.pointerId !== pointerId) return;
      const bounds = L.latLngBounds(start, map.mouseEventToLatLng(e));
      cancelBox();
      const keys = boxKeys(bounds);
      if (keys.length && onBoxSelect) onBoxSelect(keys);
    };

    applyIdle();
    el.addEventListener("pointerdown", onDown, true);
    el.addEventListener("pointermove", onMove);
    el.addEventListener("pointerup", onUp);
    el.addEventListener("pointercancel", cancelBox);
    window.addEventListener("keydown", onKey);
    window.addEventListener("keyup", onKey);
    return () => {
      cancelBox();
      el.removeEventListener("pointerdown", onDown, true);
      el.removeEventListener("pointermove", onMove);
      el.removeEventListener("pointerup", onUp);
      el.removeEventListener("pointercancel", cancelBox);
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("keyup", onKey);
      setDragging(true);
      el.style.touchAction = ""; el.style.cursor = "";
    };
  }, [ready, selectMode, boxTool, stands, zones, corridors, sign, suggestions, layers, onBoxSelect]);

  // render scent cones — geographic sector from each stand in the blended scent direction
  useEffect(() => {
    if (!ready) return;
    const g = layerGroups.current.scent; g.clearLayers();
    if (!conditions) return;
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
      const sl = standLayers?.[s.id] || { wind: true, thermal: true, scent: true, deer: true, flow: false };
      if (!sl.scent) return; // Individual stand scent check

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
  }, [stands, conditions, ready, standLayers]);

  // render stands with indicators
  useEffect(() => {
    if (!ready) return;
    const g = layerGroups.current.stands; 
    const byId = {};
    (conditions?.stands || []).forEach((it) => { byId[it.stand.id] = it.vectors; });
    const rankIndex = {};
    (conditions?.ranked || []).forEach((r, i) => { rankIndex[r.stand.id] = i; });

    // Clean up deleted stands
    const currentIds = new Set(stands.map(s => s.id));
    Object.keys(standsMarkers.current).forEach(id => {
      if (!currentIds.has(Number(id)) && !currentIds.has(id)) {
        g.removeLayer(standsMarkers.current[id]);
        delete standsMarkers.current[id];
      }
    });

    stands.forEach((s) => {
      const sl = standLayers?.[s.id] || { wind: true, thermal: true, scent: true, deer: true, flow: false };
      const v = byId[s.id] || {};
      const vectors = {
        wind_to_deg: sl.wind ? v.wind_to_deg : null,
        thermal_to_deg: sl.thermal ? v.thermal_to_deg : null,
        wind_speed: v.wind_speed,
        thermal_strength: v.thermal_strength,
        deer_approach_deg: sl.deer ? s.deer_approach_deg : null,
      };
      const rank = rankIndex[s.id] ?? 99;
      const icon = standIcon(vectors, rank, isSelected("stand", s.id));

      const windTxt = v.wind_to_deg != null ? `Wind → ${degToCompass(v.wind_to_deg)} ${v.wind_speed}mph` : "";
      const thermTxt = v.thermal_to_deg != null ? `Thermal ${v.thermal_phase} → ${degToCompass(v.thermal_to_deg)}` : "";
      const subtitle = [windTxt, thermTxt].filter(Boolean).join(" · ");

      if (standsMarkers.current[s.id]) {
        const m = standsMarkers.current[s.id];
        m.setIcon(icon);
        // removed the !m.isPopupOpen() check here so it always updates the stored HTML
        if (selectMode) {
          m.closePopup(); m.unbindPopup();   // select mode: a tap toggles selection instead
        } else if (!drawMode) {
          bindFeaturePopup(m, {
            title: s.name, subtitle, kind: "stand", id: s.id, onEdit: onEditFeature, onDelete: onDeleteFeature, sl, onToggleStandLayer
          });
        }
      } else {
        const m = L.marker([s.lat, s.lon], { icon, interactive: !drawMode });
        // Markers are reused across renders, so the toggle handler is registered once and reads
        // the live select-mode state from a ref.
        m.on("click", (e) => {
          if (!selectRef.current.selectMode) return;
          L.DomEvent.stopPropagation(e);
          selectRef.current.onToggleSelect && selectRef.current.onToggleSelect(`stand:${s.id}`);
        });
        if (selectMode) {
          // no popup while selecting
        } else if (!drawMode) {
          bindFeaturePopup(m, {
            title: s.name, subtitle, kind: "stand", id: s.id, onEdit: onEditFeature, onDelete: onDeleteFeature, sl, onToggleStandLayer
          });
        }
        m.addTo(g);
        standsMarkers.current[s.id] = m;
      }
    });
  }, [stands, conditions, ready, standLayers, drawMode, onEditFeature, onDeleteFeature, onToggleStandLayer,
      selectMode, selectedKeys]);

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

  // render terrain flow accumulation
  useEffect(() => {
    if (!ready) return;
    const g = layerGroups.current.flow; 
    g.clearLayers();

    stands.forEach((s) => {
      const sl = standLayers?.[s.id] || { wind: true, thermal: true, scent: true, deer: true, flow: false };
      if (!sl.flow) return; // Individual stand flow check
      if (!s.terrain || !s.terrain.acc) return;
      const t = s.terrain;
      const N = t.grid_size;
      const boxM = t.box_m;

      // Find max accumulation to establish a logarithmic scale
      let maxAcc = 0;
      for (let r = 0; r < N; r++) {
        for (let c = 0; c < N; c++) {
          if (t.acc[r][c] > maxAcc) maxAcc = t.acc[r][c];
        }
      }
      if (maxAcc <= 0) return;
      const logMax = Math.log1p(maxAcc);

      // Paint the matrix to an in-memory canvas
      const canvas = document.createElement("canvas");
      canvas.width = N;
      canvas.height = N;
      const ctx = canvas.getContext("2d");

      for (let r = 0; r < N; r++) {
        for (let c = 0; c < N; c++) {
          const val = t.acc[r][c];
          const norm = Math.log1p(val) / logMax;

          // Only render cells with meaningful accumulation to keep the map clean
          if (norm > 0.15) {
            // using var(--blue): rgb(24, 95, 165)
            ctx.fillStyle = `rgba(24, 95, 165, ${norm})`;
            ctx.fillRect(c, r, 1, 1);
          }
        }
      }

      // Calculate exact geographic bounds for the 800m box
      const latRad = s.lat * Math.PI / 180;
      const mPerDegLat = 111320.0;
      const mPerDegLon = mPerDegLat * Math.cos(latRad);
      const halfLat = (boxM / 2) / mPerDegLat;
      const halfLon = (boxM / 2) / mPerDegLon;

      const bounds = [
        [s.lat - halfLat, s.lon - halfLon], // SouthWest
        [s.lat + halfLat, s.lon + halfLon]  // NorthEast
      ];

      // Project the canvas onto the Leaflet map
      L.imageOverlay(canvas.toDataURL(), bounds, {
        opacity: 0.85,
        interactive: false,
        className: "pixelated-overlay"
      }).addTo(g);
    });
  }, [stands, ready, standLayers]);

  return <div ref={mapEl} style={{ height, width: "100%", overflow: "hidden" }} />;
});

export default HuntMap;
