import React, { useEffect, useRef } from "react";

/* global L */

const USGS_TOPO = "https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer/tile/{z}/{y}/{x}";
const COLORS = { deer: "#A35A1B", bedding: "#6B4FA0", food: "#3B6D11", stand: "#A32D2D" };

/*
  MiniMap renders one feature on a small topo map.
  Props:
    kind: 'stand' | 'food' | 'bedding' | 'corridor'
    feature: the item ({lat,lon,radius_m} for stand/zone, {points:[[lat,lon],...]} for corridor)
    height: px
    editable: bool — enables Geoman drag/resize/reshape
    onChange(updatedGeometry): called on edit with the new geometry
        stand:   { lat, lon }
        zone:    { lat, lon, radius_m }
        corridor:{ points: [[lat,lon],...] }
*/
export default function MiniMap({ kind, feature, height = 150, editable = false, onChange }) {
  const elRef = useRef(null);
  const mapRef = useRef(null);
  const layerRef = useRef(null);
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;

  useEffect(() => {
    let tries = 0, timer = null;
    function init() {
      if (mapRef.current || !elRef.current) return;
      if (typeof L === "undefined") { if (tries++ < 50) timer = setTimeout(init, 100); return; }
      const map = L.map(elRef.current, {
        zoomControl: editable, attributionControl: false,
        dragging: editable, scrollWheelZoom: false, doubleClickZoom: editable,
        boxZoom: false, keyboard: false, tap: false,
      });
      L.tileLayer(USGS_TOPO, { maxZoom: 16 }).addTo(map);
      mapRef.current = map;
      drawFeature();
      setTimeout(() => map.invalidateSize(), 60);  // ensure tiles fill after layout
    }

    function fitTo(layer, fallbackLatLng) {
      const map = mapRef.current;
      try {
        const b = layer.getBounds ? layer.getBounds() : null;
        if (b && b.isValid()) { map.fitBounds(b, { padding: [18, 18], maxZoom: 16 }); return; }
      } catch {}
      if (fallbackLatLng) map.setView(fallbackLatLng, 15);
    }

    function emit() {
      const layer = layerRef.current;
      if (!layer || !onChangeRef.current) return;
      if (kind === "stand") { const ll = layer.getLatLng(); onChangeRef.current({ lat: ll.lat, lon: ll.lng }); }
      else if (kind === "food" || kind === "bedding") {
        const ll = layer.getLatLng(); onChangeRef.current({ lat: ll.lat, lon: ll.lng, radius_m: Math.round(layer.getRadius()) });
      } else if (kind === "corridor") {
        const latlngs = layer.getLatLngs(); onChangeRef.current({ points: latlngs.map((p) => [p.lat, p.lng]) });
      }
    }

    function drawFeature() {
      const map = mapRef.current;
      if (layerRef.current) { map.removeLayer(layerRef.current); layerRef.current = null; }
      let layer;
      if (kind === "stand") {
        layer = L.marker([feature.lat, feature.lon], { draggable: editable }).addTo(map);
        map.setView([feature.lat, feature.lon], 15);
        if (editable) layer.on("dragend", emit);
      } else if (kind === "food" || kind === "bedding") {
        layer = L.circle([feature.lat, feature.lon], {
          radius: feature.radius_m || 80, color: COLORS[kind], fillColor: COLORS[kind], fillOpacity: 0.2, weight: 2,
        }).addTo(map);
        fitTo(layer, [feature.lat, feature.lon]);
        if (editable) {
          layer.pm && layer.pm.enable({ allowEditing: true });
          layer.on("pm:edit", emit);
          layer.on("pm:dragend", emit);
        }
      } else if (kind === "corridor") {
        const pts = (feature.points || []).map((p) => [p[0], p[1]]);
        layer = L.polyline(pts, { color: COLORS.deer, weight: 3, dashArray: "1 6", lineCap: "round" }).addTo(map);
        fitTo(layer);
        if (editable) {
          layer.pm && layer.pm.enable({ allowEditing: true });
          layer.on("pm:edit", emit);
          layer.on("pm:markerdragend", emit);
        }
      }
      layerRef.current = layer;
    }

    init();
    return () => { if (timer) clearTimeout(timer); if (mapRef.current) { mapRef.current.remove(); mapRef.current = null; layerRef.current = null; } };
    // re-create when the feature identity or edit mode changes
    // eslint-disable-next-line
  }, [kind, editable, JSON.stringify(feature)]);

  return <div ref={elRef} style={{ height, width: "100%", borderRadius: 8, overflow: "hidden", border: "1px solid var(--bord)" }} />;
}
