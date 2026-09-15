/* ───────── USGS basemap tile sources ─────────
   Single source of truth for the tile URL templates, shared by HuntMap (which
   renders them) and offlineTiles.js (which downloads/inspects them by the same
   `id`/`url` — leaflet.offline keys its IndexedDB storage by urlTemplate, so
   these strings must stay byte-identical everywhere they're used). */
const TILE_SOURCES = [
  { id: "topo", label: "Topo", url: "https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer/tile/{z}/{y}/{x}" },
  { id: "imagery", label: "Imagery+Topo", url: "https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryTopo/MapServer/tile/{z}/{y}/{x}" },
];

export { TILE_SOURCES };
