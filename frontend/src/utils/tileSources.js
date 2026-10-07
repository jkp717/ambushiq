/* ───────── Basemap tile sources ─────────
   Single source of truth for the tile URL templates, shared by HuntMap (which
   renders them) and offlineTiles.js (which downloads/inspects them by the same
   `id`/`url` — leaflet.offline keys its IndexedDB storage by urlTemplate, so
   these strings must stay byte-identical everywhere they're used; changing one
   would orphan every area already saved for offline use).

   `nativeMaxZoom` is the deepest zoom the provider actually serves; the map
   zooms past it by stretching those tiles. A source with `parts` is a base
   layer drawn as a stack of other sources, bottom first; each part can be
   given `opacity` and a CSS `blend` mode. `overlayOnly` sources are never a
   base layer on their own; `toggle` ones are also offered as on/off overlays
   in the layer picker. `needsKey` sources carry a `{key}` placeholder and are
   hidden until a MapTiler key is saved in Settings.

   The /api/tiles overlays (hillshade, contours, woods & water) come from the
   backend, which renders them from USGS 3DEP elevation and NLCD land cover
   and caches them (app/tiles). Tiles nobody has viewed yet take a few seconds,
   so the map shows a "Loading <loadingLabel>…" pill while they arrive. */
const MAP_MAX_ZOOM = 20;

const ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services";
const ESRI_ATTR = "Esri, Maxar, Earthstar Geographics, USGS";
const MT_ATTR = "© MapTiler © OpenStreetMap contributors";
const USGS_3DEP_ATTR = "USGS 3DEP";

const TILE_SOURCES = [
  { id: "topo", label: "USGS Topo", nativeMaxZoom: 16, attribution: "USGS The National Map",
    url: "https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer/tile/{z}/{y}/{x}" },
  { id: "imagery", label: "USGS Imagery+Topo", nativeMaxZoom: 16, attribution: "USGS The National Map",
    url: "https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryTopo/MapServer/tile/{z}/{y}/{x}" },
  { id: "esri_topo", label: "Esri Topo", nativeMaxZoom: 19, attribution: ESRI_ATTR,
    url: `${ESRI}/World_Topo_Map/MapServer/tile/{z}/{y}/{x}` },
  { id: "esri_topo_shaded", label: "Esri Topo Shaded",
    parts: [{ id: "esri_topo" }, { id: "landcover", blend: "multiply", opacity: 0.8 }, { id: "hillshade", blend: "multiply", opacity: 0.55 }] },
  { id: "esri_imagery", label: "Esri Imagery", nativeMaxZoom: 19, attribution: ESRI_ATTR,
    url: `${ESRI}/World_Imagery/MapServer/tile/{z}/{y}/{x}` },
  { id: "esri_imagery_topo", label: "Esri Imagery+Topo",
    parts: [{ id: "esri_imagery" }, { id: "contours" }, { id: "esri_reference" }] },
  { id: "esri_reference", label: "Esri Roads & Labels", nativeMaxZoom: 19, attribution: ESRI_ATTR, overlayOnly: true,
    url: `${ESRI}/Reference/World_Transportation/MapServer/tile/{z}/{y}/{x}` },
  { id: "mt_outdoor", label: "MapTiler Outdoor", nativeMaxZoom: 20, attribution: MT_ATTR, needsKey: true,
    url: "https://api.maptiler.com/maps/outdoor-v2/256/{z}/{x}/{y}.png?key={key}" },
  { id: "mt_hybrid", label: "MapTiler Satellite", nativeMaxZoom: 20, attribution: MT_ATTR, needsKey: true,
    url: "https://api.maptiler.com/maps/hybrid/256/{z}/{x}/{y}.jpg?key={key}" },
  { id: "contours", label: "Contour lines (10 ft)", nativeMaxZoom: 18, minZoom: 13, attribution: USGS_3DEP_ATTR,
    overlayOnly: true, toggle: true, loadingLabel: "contour lines", url: "/api/tiles/contours/{z}/{x}/{y}.png" },
  { id: "hillshade", label: "Hillshade", nativeMaxZoom: 18, attribution: USGS_3DEP_ATTR,
    overlayOnly: true, toggle: { blend: "multiply", opacity: 0.55 }, loadingLabel: "hillshade",
    url: "/api/tiles/hillshade/{z}/{x}/{y}.png" },
  { id: "landcover", label: "Woods & water", nativeMaxZoom: 15, attribution: "USGS/MRLC NLCD",
    overlayOnly: true, loadingLabel: "woods & water", url: "/api/tiles/landcover/{z}/{x}/{y}.png" },
];

// Base-layer ids that were renamed or folded into another, so a remembered choice still lands somewhere.
const RENAMED_BASES = { esri_hybrid: "esri_imagery_topo" };

/** Tile URL template with the API key filled in (sources without `{key}` pass through unchanged). */
function resolveUrl(source, key) {
  return source.url.replace("{key}", encodeURIComponent(key || ""));
}

export { TILE_SOURCES, MAP_MAX_ZOOM, RENAMED_BASES, resolveUrl };
