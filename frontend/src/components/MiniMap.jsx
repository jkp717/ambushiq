import React, { useEffect, useRef } from "react";

/* global L */

const USGS_TOPO = "https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer/tile/{z}/{y}/{x}";
const COLORS = { deer: "#A35A1B", bedding: "#6B4FA0", food: "#3B6D11", stand: "#A32D2D" };

/*
  MiniMap renders one feature on a small, static topo map (the list-card thumbnails). Features are
  edited on the main map instead ("Edit on map").
  Props:
    kind: 'stand' | 'food' | 'bedding' | 'corridor'
    feature: the item ({lat,lon,radius_m} for stand/zone, {points:[[lat,lon],...]} for corridor)
    height: px
*/
export default function MiniMap({ kind, feature, height = 150 }) {
  const elRef = useRef(null);
  const mapRef = useRef(null);
  const layerRef = useRef(null);

  useEffect(() => {
    let tries = 0, timer = null, sizeTimer = null;
    function init() {
      if (mapRef.current || !elRef.current) return;
      if (typeof L === "undefined") { if (tries++ < 50) timer = setTimeout(init, 100); return; }
      const map = L.map(elRef.current, {
        zoomControl: false, attributionControl: false,
        dragging: false, scrollWheelZoom: false, doubleClickZoom: false,
        boxZoom: false, keyboard: false, tap: false,
      });
      L.tileLayer(USGS_TOPO, { maxZoom: 16 }).addTo(map);
      mapRef.current = map;
      drawFeature();
      // Ensure tiles fill after layout. Cancelled in the cleanup below: if the effect re-runs or
      // the modal closes within these 60 ms the map is already removed, and invalidateSize on a
      // removed map throws ("Cannot read properties of undefined (reading '_leaflet_pos')").
      sizeTimer = setTimeout(() => { if (mapRef.current === map) map.invalidateSize(); }, 60);
    }

    function fitTo(layer, fallbackLatLng) {
      const map = mapRef.current;
      try {
        const b = layer.getBounds ? layer.getBounds() : null;
        if (b && b.isValid()) { map.fitBounds(b, { padding: [18, 18], maxZoom: 16 }); return; }
      } catch {}
      if (fallbackLatLng) map.setView(fallbackLatLng, 15);
    }

    function drawFeature() {
      const map = mapRef.current;
      if (layerRef.current) { map.removeLayer(layerRef.current); layerRef.current = null; }
      let layer;
      if (kind === "stand") {
        layer = L.marker([feature.lat, feature.lon]).addTo(map);
        map.setView([feature.lat, feature.lon], 15);
      } else if (kind === "food" || kind === "bedding") {
        layer = L.circle([feature.lat, feature.lon], {
          radius: feature.radius_m || 80, color: COLORS[kind], fillColor: COLORS[kind], fillOpacity: 0.2, weight: 2,
        }).addTo(map);
        fitTo(layer, [feature.lat, feature.lon]);
      } else if (kind === "corridor") {
        const pts = (feature.points || []).map((p) => [p[0], p[1]]);
        layer = L.polyline(pts, { color: COLORS.deer, weight: 3, dashArray: "1 6", lineCap: "round" }).addTo(map);
        fitTo(layer);
      }
      layerRef.current = layer;
    }

    init();
    return () => { if (timer) clearTimeout(timer); if (sizeTimer) clearTimeout(sizeTimer); if (mapRef.current) { mapRef.current.remove(); mapRef.current = null; layerRef.current = null; } };
    // re-create when the feature changes
    // eslint-disable-next-line
  }, [kind, JSON.stringify(feature)]);

  return <div ref={elRef} style={{ height, width: "100%", borderRadius: 8, overflow: "hidden", border: "1px solid var(--bord)" }} />;
}
