import { useEffect, useRef, useState } from "react";
import { Navigation2 } from "lucide-react";
import { placeEdgeArrow } from "../utils/edgeIndicator.js";

// Map controls the arrow must not sit on (looked up inside the map page each time, so panels that open
// or close are respected). The bottom time-sheet tab is handled separately: a strip along the bottom edge
// is reserved for it, so an arrow for a device due south sits above the tab instead of being pushed aside.
const OBSTACLES = [
  ".layer-overlay", ".map-weather-card", ".map-scout-progress", ".map-add-btn",
  ".leaflet-control-zoom", ".leaflet-control-layers",
].join(",");

/* A small arrow on the edge of the map pointing at the device's location whenever that location is
   off-screen. Renders inside `.map-body` (its parent), tracks the Leaflet map's pan/zoom, and sits at the
   allowed point on the edge nearest the device. Tapping it pans the map to the device. */
function LocationEdgeIndicator({ getMap, location }) {
  const rootRef = useRef(null);
  const getMapRef = useRef(getMap);
  getMapRef.current = getMap;                     // callers pass a fresh arrow each render; keep effects stable
  const locRef = useRef(location);
  const scheduleRef = useRef(() => {});
  const [arrow, setArrow] = useState(null);
  const [tick, setTick] = useState(0);
  const hasLocation = !!location;
  locRef.current = location;

  // The map is created asynchronously (Leaflet loads from a CDN); keep checking until it exists.
  useEffect(() => {
    if (!hasLocation || getMapRef.current()) return undefined;
    const t = setTimeout(() => setTick((n) => n + 1), 300);
    return () => clearTimeout(t);
  }, [hasLocation, tick]);

  useEffect(() => {
    const map = getMapRef.current();
    const root = rootRef.current;
    if (!hasLocation || !map || !root) { setArrow(null); return undefined; }
    const body = root.parentElement;
    const page = body.closest(".map-page");
    let raf = 0;

    const compute = () => {
      const loc = locRef.current;
      if (!loc) { setArrow(null); return; }
      const bodyRect = body.getBoundingClientRect();
      const mapRect = map.getContainer().getBoundingClientRect();
      const pt = map.latLngToContainerPoint([loc.lat, loc.lon]);
      const px = pt.x + (mapRect.left - bodyRect.left), py = pt.y + (mapRect.top - bodyRect.top);

      // the bottom time sheet covers the lower part of the map while it is open
      let covered = 0;
      if (page) {
        const lift = parseFloat(getComputedStyle(page).getPropertyValue("--sheet-lift")) || 0;
        covered = Math.max(0, bodyRect.bottom - (page.getBoundingClientRect().bottom - lift));
      }
      const obstacles = [...(page || body).querySelectorAll(OBSTACLES)].map((el) => {
        const r = el.getBoundingClientRect();
        return { left: r.left - bodyRect.left - 6, top: r.top - bodyRect.top - 6, right: r.right - bodyRect.left + 6, bottom: r.bottom - bodyRect.top + 6 };
      }).filter((o) => o.right > o.left && o.bottom > o.top);

      const tab = page && page.querySelector(".bottom-sheet-tab");
      const reserveBottom = tab ? tab.getBoundingClientRect().height + 6 : 0;
      const next = placeEdgeArrow({ px, py, width: bodyRect.width, visHeight: bodyRect.height - covered, obstacles, reserveBottom });
      setArrow((prev) => {
        if (!next || !prev) return next;
        return Math.abs(prev.x - next.x) < 0.5 && Math.abs(prev.y - next.y) < 0.5 && Math.abs(prev.angle - next.angle) < 0.5 ? prev : next;
      });
    };
    const schedule = () => { if (!raf) raf = requestAnimationFrame(() => { raf = 0; compute(); }); };
    scheduleRef.current = schedule;

    map.on("move zoom moveend zoomend resize", schedule);
    window.addEventListener("resize", schedule);
    const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(schedule) : null;
    if (ro) ro.observe(body);
    // --sheet-lift changes as the sheet is dragged; panels open and close inside the map body
    const mo = new MutationObserver(schedule);
    if (page) mo.observe(page, { attributes: true, attributeFilter: ["style"] });
    mo.observe(body, { childList: true });
    const layerBox = body.querySelector(".layer-overlay");
    if (layerBox) mo.observe(layerBox, { childList: true });
    schedule();

    return () => {
      map.off("move zoom moveend zoomend resize", schedule);
      window.removeEventListener("resize", schedule);
      if (ro) ro.disconnect();
      mo.disconnect();
      if (raf) cancelAnimationFrame(raf);
      scheduleRef.current = () => {};
    };
  }, [hasLocation, tick]);

  // a new GPS fix moves the target even though the map itself hasn't moved
  useEffect(() => { scheduleRef.current(); }, [location]);

  function panToMe() {
    const map = getMapRef.current();
    const loc = locRef.current;
    if (map && loc) map.panTo([loc.lat, loc.lon], { animate: true });
  }

  return (
    <div ref={rootRef} className="loc-edge-layer">
      {arrow && (
        <button type="button" className="loc-edge" style={{ left: arrow.x, top: arrow.y }}
          onClick={panToMe} title="Your location is off the map — tap to go there" aria-label="Pan to my location">
          <Navigation2 size={18} fill="currentColor" style={{ transform: `rotate(${arrow.angle}deg)` }} />
        </button>
      )}
    </div>
  );
}

export default LocationEdgeIndicator;
