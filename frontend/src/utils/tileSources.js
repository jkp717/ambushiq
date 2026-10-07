/* ───────── Basemap tile sources ─────────
   Single source of truth for the tile URL templates, shared by HuntMap (which
   renders them) and offlineTiles.js (which downloads/inspects them by the same
   `id`/`url` — leaflet.offline keys its IndexedDB storage by urlTemplate, so
   these strings must stay byte-identical everywhere they're used; changing the
   USGS ones would orphan every area already saved for offline use).

   `nativeMaxZoom` is the deepest zoom the provider actually serves; the map
   zooms past it by stretching those tiles. A source with `parts` is a base
   layer drawn as a stack of other sources (imagery + a labels overlay).
   `needsKey` sources carry a `{key}` placeholder and are hidden until a
   MapTiler key is saved in Settings. */
const MAP_MAX_ZOOM = 20;

const ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services";
const ESRI_ATTR = "Esri, Maxar, Earthstar Geographics, USGS";
const MT_ATTR = "© MapTiler © OpenStreetMap contributors";

const TILE_SOURCES = [
  { id: "topo", label: "USGS Topo", nativeMaxZoom: 16, attribution: "USGS The National Map",
    url: "https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer/tile/{z}/{y}/{x}" },
  { id: "imagery", label: "USGS Imagery+Topo", nativeMaxZoom: 16, attribution: "USGS The National Map",
    url: "https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryTopo/MapServer/tile/{z}/{y}/{x}" },
  { id: "esri_topo", label: "Esri Topo", nativeMaxZoom: 19, attribution: ESRI_ATTR,
    url: `${ESRI}/World_Topo_Map/MapServer/tile/{z}/{y}/{x}` },
  { id: "esri_imagery", label: "Esri Imagery", nativeMaxZoom: 19, attribution: ESRI_ATTR,
    url: `${ESRI}/World_Imagery/MapServer/tile/{z}/{y}/{x}` },
  { id: "esri_reference", label: "Esri Roads & Labels", nativeMaxZoom: 19, attribution: ESRI_ATTR, overlayOnly: true,
    url: `${ESRI}/Reference/World_Transportation/MapServer/tile/{z}/{y}/{x}` },
  { id: "esri_hybrid", label: "Esri Imagery+Labels", parts: ["esri_imagery", "esri_reference"] },
  { id: "mt_outdoor", label: "MapTiler Outdoor", nativeMaxZoom: 20, attribution: MT_ATTR, needsKey: true,
    url: "https://api.maptiler.com/maps/outdoor-v2/256/{z}/{x}/{y}.png?key={key}" },
  { id: "mt_hybrid", label: "MapTiler Satellite", nativeMaxZoom: 20, attribution: MT_ATTR, needsKey: true,
    url: "https://api.maptiler.com/maps/hybrid/256/{z}/{x}/{y}.jpg?key={key}" },
];

/** Tile URL template with the API key filled in (sources without `{key}` pass through unchanged). */
function resolveUrl(source, key) {
  return source.url.replace("{key}", encodeURIComponent(key || ""));
}

export { TILE_SOURCES, MAP_MAX_ZOOM, resolveUrl };
