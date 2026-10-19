import React, { useState, useEffect, useCallback, useMemo, useRef } from "react";
import { Wind, List, ChevronLeft, ChevronRight, ChevronDown, ChevronUp, Play, Pause, Download, BoxSelect, Navigation2, Save, Ruler } from "lucide-react";
import { api, apiSaved, loadSaved, fromDevice, tokenStore, regionStore } from "../services/api.js";
import { savedAtLabel } from "../utils/offlineData.js";
import useForegroundRefresh from "../hooks/useForegroundRefresh.js";
import useGeolocation from "../hooks/useGeolocation.js";
import useDeviceHeading, { requestOrientationPermission } from "../hooks/useDeviceHeading.js";
import { localDate, morningStartIdx, ratingScore5, ratingTone } from "../utils/formatters.js";
import { fmtDist, fmtYd } from "../utils/units.js";
import { degToCompass } from "../utils/compass.js";
import { SCOUT_RADIUS_DEFAULT_M, SCOUT_RADIUS_MIN_M, SCOUT_RADIUS_MAX_M, distanceM } from "../utils/geo.js";
import Banner from "../components/ui/Banner.jsx";
import LayerChip from "../components/ui/LayerChip.jsx";
import BottomSheet from "../components/ui/BottomSheet.jsx";
import LocationEdgeIndicator from "../components/LocationEdgeIndicator.jsx";
import HuntMap from "../components/HuntMap.jsx";
import HourlyForecast from "../components/HourlyForecast.jsx";
import { WX_ICONS } from "../utils/wxIcons.js";
import AddMenu from "../components/AddMenu.jsx";
import OfflineMapsPanel from "../components/OfflineMapsPanel.jsx";
import { NamePrompt, FoodZonePrompt, CorridorPrompt } from "../components/Prompts.jsx";
import ScoutingOverlapPrompt from "../components/ScoutingOverlapPrompt.jsx";
import SelectionBar from "../components/SelectionBar.jsx";
import { HuntIcons, sitAt, sitParts, sitDetail } from "../components/HuntFlags.jsx";

// "06:58" (the /hours sunrise/sunset strings) -> "6:58"
const clock12 = (hhmm) => {
  const [h, m] = String(hhmm).split(":").map(Number);
  return `${h % 12 || 12}:${String(m).padStart(2, "0")}`;
};


/* The time panel's day picker: one chip per forecast day with its weekday, date, weather icon and deer
   movement score (colored like the Outlook). The bottom-sheet tab is the one place the chosen date and
   time are spelled out; this strip is just for moving between days. Swipes sideways; the chosen day is
   kept centred in view. */
function DayStrip({ days, dayIdx, ratings, utcOffset, onPick }) {
  const stripRef = useRef(null);
  useEffect(() => {
    const strip = stripRef.current;
    const chip = strip?.children[dayIdx];
    if (!chip || strip.scrollWidth <= strip.clientWidth) return;
    // scrolls the strip only (scrollIntoView could also move the page or the sheet)
    strip.scrollTo({ left: Math.max(0, chip.offsetLeft - (strip.clientWidth - chip.offsetWidth) / 2), behavior: "smooth" });
  }, [dayIdx, days.length]);
  const today = localDate(utcOffset);
  return (
    <div className="map-day-strip" role="tablist" aria-label="Forecast day" ref={stripRef}>
      {days.map((d, i) => {
        const date = new Date(d.day + "T12:00:00");
        const rating = ratings?.get(d.day);
        const Icon = WX_ICONS[d.wx?.icon];
        const score = rating ? ratingScore5(rating) : null;
        return (
          <button key={d.day} type="button" role="tab" aria-selected={i === dayIdx}
            className={"map-day-chip" + (i === dayIdx ? " on" : "")} onClick={() => onPick(i)}
            title={[d.label, d.wx?.text, score && `${score}/5 movement`].filter(Boolean).join(" · ")}>
            <span className="map-day-chip-dow">{d.day === today ? "Today" : date.toLocaleDateString("en-US", { weekday: "short" })}</span>
            <span className="map-day-chip-date">{date.getDate()}</span>
            <span className="map-day-chip-wx" role="img" aria-label={d.wx?.text || "No weather summary"}>
              {Icon && <Icon size={18} />}
            </span>
            <span className="map-day-chip-score" style={rating ? { "--tone": ratingTone(rating) } : undefined}>{score ?? "–"}</span>
            <HuntIcons hunt={d.hunt} size={11} />
          </button>
        );
      })}
    </div>
  );
}

const LAND_HINTS = {
  zoom: "Zoom in to see public land",
  loading: "Loading public land...",
  error: "Public land is unavailable right now",
  partial: "Showing saved public land data",
};
const LAND_LEGEND = [
  ["#D81B60", "State wildlife area (WMA)"], ["#2E8B57", "National forest"], ["#1976D2", "Wildlife refuge"],
  ["#B8860B", "Other federal"], ["#5C6BC0", "State land"], ["#C62828", "Closed to public", true],
];

const TRAILS_HINTS = {
  zoom: "Zoom in to see trails",
  loading: "Loading trails...",
  error: "Trails are unavailable right now",
  partial: "Showing saved trail data",
};
const TRAILS_LEGEND = [
  ["#8D6E00", "OHV/ATV/motorcycle trail"], ["#6D4C1E", "Designated forest road", true],
];

const ROADS_HINTS = {
  zoom: "Zoom in to see roads",
  loading: "Loading roads...",
  error: "Roads are unavailable right now",
  partial: "Showing saved road data",
};
const ROADS_LEGEND = [
  ["#B71C1C", "Highway"], ["#D84315", "Secondary highway"], ["#616161", "Connecting road"], ["#9E9E9E", "Local road"],
];

const RECSITES_HINTS = {
  zoom: "Zoom in to see recreation sites",
  loading: "Loading recreation sites...",
  error: "Recreation sites are unavailable right now",
  partial: "Showing saved recreation site data",
};
const RECSITES_LEGEND = [
  ["#8D6E00", "Trailhead"], ["#2E7D32", "Campground"], ["#EF6C00", "Picnic site"], ["#1565C0", "Day-use area"],
];

const THUMB_PX = 18;   // the time slider's thumb width (input[type="range"] in styles.css)

// Add-menu modes that place a single point (vs. drawing a corridor or a scouting area).
const POINT_ADDS = ["stand", "food", "bedding", "scrape", "rub"];

// A 15-minute slot's key in the saved forecast: the hour's local time ("2025-11-10T06:00") plus the
// minutes past it. Keyed by time rather than forecast index, so a copy saved on an earlier day (whose
// index 0 was an earlier hour) can never show the wrong hour's arrows.
const slotKey = (iso, minute = 0) => `${iso}|${minute}`;
const hourIso = (day, hour) => `${day.day}T${String(hour.hour).padStart(2, "0")}:00`;

// A saved lee-zone row, run-length encoded by the server: "a3b2a2" -> "0001100" (a/b/c = 0/1/2, then a count).
const RLE_VALUE = { a: "0", b: "1", c: "2" };
const unrle = (row) => row.replace(/([abc])(\d+)/g, (_m, ch, n) => RLE_VALUE[ch].repeat(+n));

// /map/conditions/all sends each stand once and refers to it by id in every hour; rebuild each hour and
// each of its :15/:30/:45 quarters in the shape /map/conditions returns, keyed by slotKey. Stands missing
// from the list are skipped. Lee-eddy zones come once per wind direction (`lee_zones`); each slot names its
// direction in `lee`, so slots with the same wind share one decoded zone.
function conditionsByHour(all) {
  const byId = new Map((all.stands || []).map((s) => [s.id, s]));
  const zones = Object.fromEntries(Object.entries(all.lee_zones || {})
    .map(([k, z]) => [k, { rows: z.rows.map(unrle), bounds: z.bounds }]));
  const build = (h) => ({
    time: h.time,
    stands: h.items.filter((it) => byId.has(it.stand_id)).map((it) => ({ stand: byId.get(it.stand_id), vectors: it.vectors })),
    ranked: h.ranked.filter((r) => byId.has(r.stand_id)).map((r) => ({ stand: byId.get(r.stand_id), avg: r.avg })),
    lee_zone: h.lee != null ? zones[String(h.lee)] ?? null : null,
  });
  const out = new Map();
  for (const h of all.hours || []) {
    out.set(slotKey(h.time.iso), build(h));
    for (const q of h.quarters || []) out.set(slotKey(h.time.iso, q.time.minute), build(q));
  }
  return out;
}

// "Edit on map": the part of a feature that edit mode changes, and the banner's instructions for it.
const EDIT_NOUN = { stand: "this stand", food: "this food zone", bedding: "this bedding zone", corridor: "this corridor",
                    scrape: "this scrape", rub: "this rub" };
function editGeom(kind, f) {
  if (kind === "corridor") return { points: f.points.map((p) => [p[0], p[1]]) };
  if (kind === "food" || kind === "bedding") return { lat: +f.lat, lon: +f.lon, radius_m: +f.radius_m };
  return { lat: +f.lat, lon: +f.lon };
}
function editHint({ kind, geom }) {
  if (kind === "corridor")
    return `Drag a point to move it, drag a midpoint to add one, long-press a point to remove it, or tap the map to extend the path (${geom.points.length} points).`;
  if (kind === "food" || kind === "bedding")
    return `Drag the center to move, drag the edge to resize (${fmtYd(geom.radius_m)}), or tap the map to move it there.`;
  return "Drag the pin, or tap the map to move it there.";
}

function MapPage({ stands, standsReady = true, zones, corridors, sign, suggestions, activeRegion, reloadStands, reloadZones, reloadCorridors, reloadSign,
                   reloadSuggestions, onDismissSuggestion, onSuggestionColor, onAddSuggestionNote, onDeleteSuggestionNote,
                   drawRequest, clearDrawRequest, editRequest, clearEditRequest, editGuardRef,
                   openStandEditor, onEditFeature, onDeleteFeature, standNav, focusRequest, clearFocusRequest }) {
  const [days, setDays] = useState([]);
  const daysRef = useRef(days);
  const shownHunt = useRef(null);   // the last day's hunt chips shown, kept while that row eases closed
  daysRef.current = days;
  const [dayIdx, setDayIdx] = useState(0);
  const [hourPos, setHourPos] = useState(0);
  const [sliderHovering, setSliderHovering] = useState(false);
  const [sliderDragging, setSliderDragging] = useState(false);
  const touchDragRef = useRef(false);   // a finger is dragging the time slider (see slotFromTouch)
  const [utcOffset, setUtcOffset] = useState(0);
  const [conditions, setConditions] = useState(null);
  const [playing, setPlaying] = useState(false);
  const [drawMode, setDrawMode] = useState(null);
  // "Edit on map": { kind, id, name, record, geom, orig, rev } (see editGeom). `rev` changes only when the
  // geometry is set from outside the edit layer (entering, a map tap), so drags don't rebuild it mid-gesture.
  const [mapEdit, setMapEdit] = useState(null);
  // Ruler mode (drawMode "ruler"): { a: {lat, lon}, b: {lat, lon} | null }. Never saved; gone when the mode ends.
  const [ruler, setRuler] = useState(null);
  const [draftPoints, setDraftPoints] = useState([]);
  
  // Updated global map layers (stand-specific elements removed)
  const [layers, setLayers] = useState({ corridors: true, zones: true, scrapes: true, rubs: true, suggestions: true, publicLand: true, trails: true, roads: true, recSites: true, leeEddies: true, drainage: false });
  const [layersOpen, setLayersOpen] = useState(false);
  const [legendOpen, setLegendOpen] = useState({});   // { publicLand, trails, roads, recSites } -> bool, default collapsed
  const toggleLegend = (k) => setLegendOpen((l) => ({ ...l, [k]: !l[k] }));

  // Multi-select: tap features / box-select, then bulk activate, deactivate or delete.
  // Selected items are "kind:id" keys (stand | zone | corridor | sign | suggestion).
  const [selectMode, setSelectMode] = useState(false);
  const [selectedKeys, setSelectedKeys] = useState(() => new Set());
  const [boxTool, setBoxTool] = useState(false);
  const [bulkBusy, setBulkBusy] = useState(false);

  // Device location (the blue dot): off by default, never persisted, never moves the map
  const [locationOn, setLocationOn] = useState(false);
  const geo = useGeolocation(locationOn);
  const compassHeading = useDeviceHeading(locationOn);

  // New stand-specific layer state
  const [standLayers, setStandLayers] = useState({});

  const [pendingName, setPendingName] = useState(null);
  const [err, setErr] = useState(null);
  const [landStatus, setLandStatus] = useState("off");   // public land layer: off | zoom | loading | ok | partial | error
  const [trailsStatus, setTrailsStatus] = useState("off"); // trails layer: off | zoom | loading | ok | partial | error
  const [recSitesStatus, setRecSitesStatus] = useState("off"); // recreation sites layer: off | zoom | loading | ok | partial | error
  const [roadsStatus, setRoadsStatus] = useState("off"); // roads layer: off | zoom | loading | ok | partial | error
  const [staleAt, setStaleAt] = useState(null);   // epoch seconds of the cached forecast being shown, or null when live
  // Offline copy of the forecast. `savedHours` is every forecast hour's conditions (from
  // /map/conditions/all), keyed by hour index; `deviceAt` is when the copy being shown was saved
  // (ms), set only while the server can't be reached.
  const [savedHours, setSavedHours] = useState(null);
  const savedHoursRef = useRef({ map: null, savedAt: null });
  const savedHoursRegion = useRef(null);
  const [deviceAt, setDeviceAt] = useState(null);
  const offlineRef = useRef(false);   // the last conditions request failed: show the saved copy first
  const liveShownRef = useRef(false); // the current slot's live conditions are on screen
  // The live /hours list is in (its indexes match the server's), and the current slot's first live
  // request has finished: the large whole-forecast refresh waits for that.
  const [hoursSettled, setHoursSettled] = useState(false);
  const [firstSlotDone, setFirstSlotDone] = useState(false);
  const foregroundTick = useForegroundRefresh();
  // bottom time shelf: "open" by default; "expanded" (pulled up) adds the hourly forecast, "closed" still shows the date/time
  const [timeStop, setTimeStop] = useState("open");
  const [ratings, setRatings] = useState(null);      // day -> deer movement rating, for the day strip
  const huntMapRef = useRef(null);
  const [showOfflinePanel, setShowOfflinePanel] = useState(false);

  // Scouting Suggestions: draw-a-circle analysis flow
  const [scoutSettings, setScoutSettings] = useState({
    scout_radius_default_m: SCOUT_RADIUS_DEFAULT_M,
    scout_radius_min_m: SCOUT_RADIUS_MIN_M,
    scout_radius_max_m: SCOUT_RADIUS_MAX_M,
  });
  const [scoutDraft, setScoutDraft] = useState(null); // { lat, lon, radius_m }
  const [scoutOverlap, setScoutOverlap] = useState(null); // { lat, lon, radius_m, count }
  const [scoutAnalyzing, setScoutAnalyzing] = useState(false);
  const [scoutProgress, setScoutProgress] = useState({ pct: 0, msg: "" });
  const [scoutError, setScoutError] = useState(null);

  const [maptilerKey, setMaptilerKey] = useState("");
  // Property-wide cold-air drainage layer (doesn't change with the forecast): fetched — and saved on
  // the phone — even while its chip is off, so it still draws with no signal.
  const [drainage, setDrainage] = useState(null);

  // The page is sized to fill the screen exactly, so any document scroll — carried over from a
  // scrolled list page, or left behind when the keyboard closes — hides the top of the map under
  // the sticky top bar. Undo it on open and whenever the visible viewport resizes.
  useEffect(() => {
    const reset = () => { if (window.scrollY) window.scrollTo(0, 0); };
    reset();
    const vv = window.visualViewport;
    vv?.addEventListener("resize", reset);
    return () => vv?.removeEventListener("resize", reset);
  }, []);

  useEffect(() => {
    apiSaved("/settings", { global: true }).then((s) => {
      setScoutSettings({
        scout_radius_default_m: s.scout_radius_default_m ?? SCOUT_RADIUS_DEFAULT_M,
        scout_radius_min_m: s.scout_radius_min_m ?? SCOUT_RADIUS_MIN_M,
        scout_radius_max_m: s.scout_radius_max_m ?? SCOUT_RADIUS_MAX_M,
      });
      setMaptilerKey(s.maptiler_api_key || "");
    }).catch(() => {});
  }, []);

  // function to toggle individual stand layers
  const toggleStandLayer = useCallback((standId, layerKey) => {
    setStandLayers((prev) => {
      const current = prev[standId] || { wind: true, thermal: true, scent: true, deer: true };
      return { ...prev, [standId]: { ...current, [layerKey]: !current[layerKey] } };
    });
  }, []);


  useEffect(() => {
    if (!sliderDragging) return;
    const end = () => { touchDragRef.current = false; setSliderDragging(false); };
    const evts = ["mouseup", "touchend", "touchcancel"];
    evts.forEach((ev) => window.addEventListener(ev, end));
    return () => evts.forEach((ev) => window.removeEventListener(ev, end));
  }, [sliderDragging]);

  useEffect(() => { if (drawRequest) { setDrawMode(drawRequest); setDraftPoints([]); clearDrawRequest(); } }, [drawRequest, clearDrawRequest]);
  // Arrived from a stand's "Map" link elsewhere: open on that day (when given) and centre on the stand.
  const wantDay = useRef(focusRequest?.day ?? null);
  const [focusStandId, setFocusStandId] = useState(null);
  useEffect(() => {
    if (!focusRequest) return;
    setFocusStandId(focusRequest.standId);
    if (focusRequest.day) {
      const i = daysRef.current.findIndex((d) => d.day === focusRequest.day);
      if (i >= 0) { setDayIdx(i); setHourPos(morningStartIdx(daysRef.current[i])); }
      else wantDay.current = focusRequest.day;   // hours not loaded yet: applied when they arrive
    }
    clearFocusRequest();
  }, [focusRequest, clearFocusRequest]);
  // A stand's own links from its map popup: its daily score (on the day shown here) and its camera photos.
  const standActions = useMemo(() => standNav && ({
    can: (id) => standNav.can(id),
    score: (id) => standNav.toScore(id, days[dayIdx]?.day),
    photos: (id) => standNav.toPhotos(id),
  }), [standNav, days, dayIdx]);

  // Arrived from a feature's "Edit on map": open edit mode on it, starting from its saved geometry.
  useEffect(() => {
    if (!editRequest) return;
    const { kind, id } = editRequest;
    const list = { stand: stands, food: zones, bedding: zones, corridor: corridors, scrape: sign, rub: sign }[kind] || [];
    const f = list.find((x) => x.id === id);
    if (f) {
      const geom = editGeom(kind, f);
      setMapEdit({ kind, id, name: f.name || EDIT_NOUN[kind], record: f, geom, orig: geom, rev: 0 });
      setDrawMode("edit"); setDraftPoints([]);
    }
    clearEditRequest();
  }, [editRequest, clearEditRequest]); // eslint-disable-line react-hooks/exhaustive-deps

  // Forecast hours drive the time controls. They don't need a stand: the backend falls back to the region's
  // own location, so a brand-new region gets a working map straight away. Re-fetched when the first stand
  // appears, since the forecast location moves from the region center to that stand.
  // Every open (and return to the foreground) also refreshes the on-phone copy of the whole forecast, so
  // the map keeps working when signal drops; if the server can't be reached, the saved copies are used.
  // On open, the hours saved on the phone are shown straight away (no "no connection" banner: this is
  // just a head start) while the live copy loads; with a saved copy on screen a slow network is given
  // up on after 4 s instead of 12. Waits for the stand list so it isn't fetched twice on open.
  const hasStands = stands.length > 0;
  useEffect(() => {
    if (!standsReady) return undefined;
    let cancel = false;
    let positioned = false;
    const apply = (j) => {
      setDays(j.days || []);
      // Apply the property's UTC offset so both the date comparison and the
      // current-hour lookup use property local time rather than the browser's
      // clock timezone (which may differ) or a bare UTC date.
      const ofs = j.utc_offset_seconds ?? 0;
      setUtcOffset(ofs);
      if (positioned) return;   // the live copy doesn't move the slider the saved copy just placed
      positioned = true;
      const want = wantDay.current;
      const wi = want ? (j.days || []).findIndex((d) => d.day === want) : -1;
      if (want) wantDay.current = null;
      if (wi >= 0) { setDayIdx(wi); setHourPos(morningStartIdx(j.days[wi])); return; }
      const localNow = new Date(Date.now() + ofs * 1000);
      const todayStr  = localNow.toISOString().slice(0, 10);
      const localHour = localNow.getUTCHours();
      for (let d = 0; d < (j.days || []).length; d++) {
        const hi = j.days[d].hours.findIndex((h) => h.hour === localHour);
        if (j.days[d].day === todayStr && hi >= 0) { setDayIdx(d); setHourPos(hi * 4); break; }
      }
    };
    (async () => {
      const saved = daysRef.current.length ? null : await loadSaved("/hours");
      if (cancel) return;
      if (saved?.data?.days) apply(saved.data);
      try {
        const j = await apiSaved("/hours", { timeoutMs: saved || daysRef.current.length ? 4000 : 12000 });
        if (cancel) return;
        const savedAt = fromDevice(j);
        setDeviceAt(savedAt);
        offlineRef.current = !!savedAt;
        setStaleAt(!savedAt && j.stale ? j.fetched_at ?? null : null);
        setErr((cur) => (cur === "Couldn't load forecast." ? null : cur));
        apply(j);
      } catch {
        if (cancel) return;
        setErr("Couldn't load forecast.");
        setFirstSlotDone(true);   // nothing to wait for: let the whole-forecast refresh try anyway
      }
      if (!cancel) setHoursSettled(true);
    })();
    return () => { cancel = true; };
  }, [standsReady, hasStands, activeRegion?.id, foregroundTick]);

  // Each day's movement rating for the day strip: the same saved copy the Outlook keeps, so it shows offline.
  useEffect(() => {
    setRatings(null);
    if (!standsReady || !hasStands) return undefined;
    let cancel = false;
    apiSaved("/deer-ratings")
      .then((j) => { if (!cancel) setRatings(new Map((j.ratings || []).map((r) => [r.day, r]))); })
      .catch(() => {});
    return () => { cancel = true; };
  }, [standsReady, hasStands, activeRegion?.id, foregroundTick]);

  // The whole forecast's conditions, for offline use. The copy saved on the phone is read straight away;
  // the fresh one (a large download) waits until the current slot's arrows have loaded, so it never
  // holds them up on a slow connection.
  const applySavedHours = (j, savedAt) => {
    if (!j?.hours) return;
    const map = conditionsByHour(j);
    savedHoursRef.current = { map, savedAt };
    setSavedHours(map);
  };
  useEffect(() => {
    let cancel = false;
    if (savedHoursRegion.current !== activeRegion?.id) {   // another region's copy must never show here
      savedHoursRegion.current = activeRegion?.id;
      savedHoursRef.current = { map: null, savedAt: null };
      setSavedHours(null);
      setHoursSettled(false);
      setDays([]);
      setFirstSlotDone(false);
    }
    loadSaved("/map/conditions/all").then((s) => {
      if (!cancel && s && !savedHoursRef.current.map) applySavedHours(s.data, s.savedAt);
    });
    return () => { cancel = true; };
  }, [activeRegion?.id]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!standsReady || !firstSlotDone) return undefined;
    let cancel = false;
    apiSaved("/map/conditions/all", { method: "POST", timeoutMs: 45000 })
      .then((j) => { if (!cancel) applySavedHours(j, fromDevice(j)); })
      .catch(() => {});
    return () => { cancel = true; };
  }, [standsReady, firstSlotDone, stands.length, activeRegion?.id, foregroundTick]); // eslint-disable-line react-hooks/exhaustive-deps

  const standsKey = stands.map((s) => `${s.id}:${s.lat},${s.lon}`).join("|");
  useEffect(() => {
    if (!standsReady) return undefined;
    let cancel = false;
    apiSaved("/map/drainage").then((j) => { if (!cancel) setDrainage(j?.rows ? j : null); }).catch(() => {});
    return () => { cancel = true; };
  }, [standsReady, standsKey, activeRegion?.id, foregroundTick]);
  useEffect(() => { setDrainage(null); }, [activeRegion?.id]);

  const curDay     = days[dayIdx];
  const maxHour    = (curDay?.hours.length || 1) - 1;
  const maxSlot    = (curDay?.hours.length || 1) * 4 - 1;
  const curHourIdx = curDay ? Math.min(Math.floor(hourPos / 4), maxHour) : 0;
  const curMinute  = (hourPos % 4) * 15;
  const curHour    = curDay?.hours[curHourIdx];

  // Where the day's thermals actually switch, read from the model's own per-slot forecast (the copy
  // saved on the phone, so it works offline too): ▲ at the first slot from an hour before sunrise where
  // most stands with a thermal arrow are rising, ▽ at the first slot after that where most no longer are.
  // `pos` is the slider slot. Either is null when it doesn't happen that day (e.g. overcast: no rising).
  const thermalSwitch = useMemo(() => {
    if (!curDay || !savedHours) return null;
    const from = (curDay.sunrise_h ?? 6.5) - 1;
    let rise = null, sink = null;
    curDay.hours.forEach((hr, i) => {
      [0, 15, 30, 45].forEach((m, q) => {
        const t = hr.hour + m / 60;
        const c = savedHours.get(slotKey(hourIso(curDay, hr), m)) || (m === 0 ? null : savedHours.get(slotKey(hourIso(curDay, hr))));
        if (!c || t < from) return;
        const arrows = c.stands.filter((s) => s.vectors?.thermal_to_deg != null);
        const risingMost = arrows.length > 0
          && arrows.filter((s) => s.vectors.thermal_phase === "rising").length * 2 > arrows.length;
        const slot = { pos: i * 4 + q, t };
        if (!rise && risingMost) rise = slot;
        else if (rise && !sink && !risingMost) sink = slot;
      });
    });
    return { rise, sink };
  }, [curDay, savedHours]);

  // pause when day changes
  useEffect(() => { setPlaying(false); }, [dayIdx]);

  // play loop — advances one 15-min slot every 750 ms
  useEffect(() => {
    if (!playing || !curDay) return;
    const id = setInterval(() => {
      setHourPos((p) => {
        const max = curDay.hours.length * 4 - 1;
        if (p >= max) { setPlaying(false); return p; }
        return p + 1;
      });
    }, 750);
    return () => clearInterval(id);
  }, [playing, curDay]);

  // One 15-minute slot's conditions, live from the server. The copy of that slot saved on the phone (lee-eddy
  // zone included) is shown first whenever there is one, so arrows appear at once on open and Play/scrubbing
  // stay smooth; the live answer replaces it. With a saved slot already on screen a slow network is given up
  // on after 4 s. Once a request has failed the saved copy is flagged as such ("no connection" banner) and a
  // live answer still replaces it if signal comes back.
  const curIso = curDay && curHour ? hourIso(curDay, curHour) : null;
  useEffect(() => {
    if (!curIso) return undefined;
    let cancel = false;
    liveShownRef.current = false;
    const savedSlot = () => {
      const { map } = savedHoursRef.current;
      // a copy saved before 15-minute slots existed only has whole hours
      return map?.get(slotKey(curIso, curMinute)) || map?.get(slotKey(curIso));
    };
    const showSaved = () => {
      const hit = savedSlot();
      if (!hit || cancel) return false;
      setConditions(hit);
      setDeviceAt(savedHoursRef.current.savedAt || Date.now());
      return true;
    };
    let headStart = false;
    if (offlineRef.current) headStart = showSaved();
    else {
      const hit = savedSlot();
      if (hit) { setConditions(hit); headStart = true; }
    }
    // Until the live hours list is in, curHour.index may come from a copy saved on an earlier day,
    // which would ask the server for the wrong hour. While the slider is being dragged the saved slot is
    // enough: no request per slot passed; the slot the finger stops on is fetched once it lifts.
    if (!hoursSettled || (sliderDragging && headStart)) return () => { cancel = true; };
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), headStart ? 4000 : 12000);
    api("/map/conditions", { method: "POST", signal: ctl.signal,
                              body: JSON.stringify({ time_index: curHour.index, minute: curMinute, lee_zone: layers.leeEddies }) })
      .then((j) => {
        if (cancel) return;
        offlineRef.current = false;
        liveShownRef.current = true;
        setConditions(j);
        setDeviceAt(null);
      })
      .catch((e) => {
        if (cancel || (e.code !== undefined && e.code < 500)) return;
        offlineRef.current = true;
        showSaved();
      })
      .finally(() => {
        clearTimeout(timer);
        if (!cancel) setFirstSlotDone(true);
      });
    return () => { cancel = true; ctl.abort(); clearTimeout(timer); };
  }, [curIso, curHour?.index, curMinute, layers.leeEddies, hoursSettled, sliderDragging]); // eslint-disable-line react-hooks/exhaustive-deps

  // The saved forecast arrived after the slot was requested: show that slot from it now, flagged as the
  // phone's copy when the request failed, or as a head start while the live answer is still on its way.
  useEffect(() => {
    if (!curIso || !savedHours) return;
    const hit = savedHours.get(slotKey(curIso, curMinute)) || savedHours.get(slotKey(curIso));
    if (!hit) return;
    if (offlineRef.current) {
      setConditions(hit);
      setDeviceAt(savedHoursRef.current.savedAt || Date.now());
    } else if (!liveShownRef.current) {
      setConditions(hit);
    }
  }, [savedHours]); // eslint-disable-line react-hooks/exhaustive-deps

  const onMapClick = useCallback(async (pt) => {
    if (drawMode === "stand") { setDrawMode(null); openStandEditor(pt); }
    else if (drawMode === "food" || drawMode === "bedding") {
      setDrawMode(null);
      setPendingName({ type: "zone", kind: drawMode, title: `Name this ${drawMode} zone`,
                       payload: { kind: drawMode, lat: pt.lat, lon: pt.lon, radius_m: 80 } });
    } else if (drawMode === "corridor") {
      setDraftPoints((p) => [...p, pt]);
    } else if (drawMode === "scrape" || drawMode === "rub") {
      try {
        await api("/sign", { method: "POST", body: JSON.stringify({ kind: drawMode, lat: pt.lat, lon: pt.lon }) });
        await reloadSign();
      } catch { setErr(`Couldn't save ${drawMode}.`); }
      setDrawMode(null);
    } else if (drawMode === "scout" && !scoutDraft) {
      setScoutDraft({ lat: pt.lat, lon: pt.lon, radius_m: scoutSettings.scout_radius_default_m });
    } else if (drawMode === "ruler") {
      // the first tap (or the next tap after a finished measurement) starts one; the second finishes it
      setRuler((r) => (!r || r.b ? { a: pt, b: null } : { ...r, b: pt }));
    } else if (drawMode === "edit" && mapEdit) {
      // a tap moves a point (or a zone's center) there, or adds a point to the end of a corridor
      setMapEdit((e) => ({ ...e, rev: e.rev + 1, geom: e.kind === "corridor"
        ? { points: [...e.geom.points, [pt.lat, pt.lon]] }
        : { ...e.geom, lat: pt.lat, lon: pt.lon } }));
    }
  }, [drawMode, mapEdit, openStandEditor, scoutDraft, scoutSettings]);
  // drags in the edit layer: the new geometry, without bumping `rev`
  const onMapEditChange = useCallback((geom) => setMapEdit((e) => e && { ...e, geom }), []);

  function finishCorridor() {
    if (draftPoints.length >= 2)
      setPendingName({ type: "corridor", title: "Name this corridor", payload: { points: draftPoints.map((p) => [p.lat, p.lon]) } });
    setDraftPoints([]); setDrawMode(null);
  }
  // Save the edited geometry with the rest of the feature unchanged. Returns whether it saved.
  async function saveMapEdit() {
    const e = mapEdit;
    if (!e) return false;
    const r = e.record, g = e.geom, active = !!(r.is_active ?? true);
    const put = (path, body) => api(path, { method: "PUT", body: JSON.stringify(body) });
    try {
      if (e.kind === "stand") {
        await put(`/stands/${e.id}`, { name: r.name, lat: g.lat, lon: g.lon, is_active: active,
          downhill_deg: r.downhill_deg, deer_approach_deg: r.deer_approach_deg, visibility_m: r.visibility_m,
          stand_type: r.stand_type || "tree", color: r.color || null });
        await reloadStands();
      } else if (e.kind === "food" || e.kind === "bedding") {
        await put(`/zones/${e.id}`, { kind: r.kind, name: r.name || null, lat: g.lat, lon: g.lon,
          radius_m: Math.round(g.radius_m), is_active: active, quality: r.quality ?? null });
        await reloadZones();
      } else if (e.kind === "corridor") {
        await put(`/corridors/${e.id}`, { name: r.name || null, points: g.points, is_active: active,
          usage: r.usage ?? 5, falloff_m: r.falloff_m ?? null, width_m: r.width_m ?? null });
        await reloadCorridors();
      } else {
        await put(`/sign/${e.id}`, { kind: r.kind, lat: g.lat, lon: g.lon, is_active: active });
        await reloadSign();
      }
    } catch { setErr("Couldn't save changes."); return false; }
    setMapEdit(null); setDrawMode(null);
    return true;
  }
  async function confirmName(name, extra1, extra2, extra3) {
    const pn = pendingName; setPendingName(null); if (!pn) return;
    try {
      if (pn.type === "zone") {
        const body = { ...pn.payload, name: name || null };
        if (pn.kind === "food" && extra1 != null) body.quality = extra1;
        await api("/zones", { method: "POST", body: JSON.stringify(body) });
        await reloadZones();
      } else {
        await api("/corridors", { method: "POST", body: JSON.stringify({ ...pn.payload, name: name || null, usage: extra1 ?? 5, falloff_m: extra2 ?? null, width_m: extra3 ?? null }) });
        await reloadCorridors();
      }
    } catch { setErr(`Couldn't save ${pn.type}.`); }
  }
  function cancelDraw() { setDraftPoints([]); setDrawMode(null); setMapEdit(null); setScoutDraft(null); }

  // Unsaved map edits: App asks to save or discard them before leaving the map, and closing the tab
  // gets the browser's own "leave site?" prompt.
  // Another draw mode took over from edit mode: drop the edit. (Only on that change — on opening the page the
  // edit request above sets the edit and the mode together.)
  const prevDrawMode = useRef(drawMode);
  useEffect(() => {
    if (prevDrawMode.current === "edit" && drawMode !== "edit") setMapEdit(null);
    prevDrawMode.current = drawMode;
  }, [drawMode]);
  const mapEditDirty = !!mapEdit && JSON.stringify(mapEdit.geom) !== JSON.stringify(mapEdit.orig);
  if (editGuardRef) editGuardRef.current = { dirty: mapEditDirty, name: mapEdit?.name, save: saveMapEdit, discard: cancelDraw };
  useEffect(() => () => { if (editGuardRef) editGuardRef.current = null; }, [editGuardRef]);
  useEffect(() => {
    if (!mapEditDirty) return undefined;
    const warn = (e) => { e.preventDefault(); e.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [mapEditDirty]);
  const toggle = (k) => setLayers((l) => ({ ...l, [k]: !l[k] }));

  // ── device location ──
  const userLocation = useMemo(() => {
    const p = geo.position;
    if (!p) return null;
    // beam direction: the compass when available, else the GPS heading while actually moving
    const gpsHeading = p.gpsHeading != null && (p.speed || 0) > 0.8 ? p.gpsHeading : null;
    return { lat: p.lat, lon: p.lon, accuracy: p.accuracy, heading: compassHeading ?? gpsHeading };
  }, [geo.position, compassHeading]);

  // Single-point adds can be placed at the device's current fix ("Use my location" in the draw bar).
  // Only a live fix is offered — not a stale one left over from before GPS was lost.
  const hereFix = POINT_ADDS.includes(drawMode) && locationOn && geo.status === "active" ? userLocation : null;
  const tapOrHere = hereFix ? "Tap the map, or use your location," : "Click the map";

  function toggleLocation() {
    if (locationOn) { setLocationOn(false); return; }
    requestOrientationPermission();   // iOS requires this inside the tap; a denial just means no compass beam
    setLocationOn(true);
  }
  useEffect(() => {
    if (!locationOn) return;
    const problem = {
      denied: "Location permission was denied. Allow location for this site in your phone's settings to show your position.",
      unavailable: "This device or browser doesn't support location.",
      insecure: "Location needs a secure (HTTPS) connection.",
      nofix: "Couldn't get a location fix. Turn on location services for this device and allow them for your browser.",
    }[geo.status];
    if (problem) { setLocationOn(false); setErr(problem); }
  }, [geo.status, locationOn]);

  // ── multi-select ──
  // Everything currently visible on the map is selectable; hidden layers are not.
  const features = useMemo(() => {
    const active = (x) => !!x.is_active;
    return [
      ...stands.map((s) => ({ key: `stand:${s.id}`, kind: "stand", active: active(s) })),
      ...(layers.zones ? zones.map((z) => ({ key: `zone:${z.id}`, kind: "zone", active: active(z) })) : []),
      ...(layers.corridors ? corridors.map((c) => ({ key: `corridor:${c.id}`, kind: "corridor", active: active(c) })) : []),
      ...(sign || [])
        .filter((sg) => (sg.kind === "scrape" ? layers.scrapes : layers.rubs))
        .map((sg) => ({ key: `sign:${sg.id}`, kind: "sign", active: active(sg) })),
      ...(layers.suggestions
        ? (suggestions || []).map((sg) => ({ key: `suggestion:${sg.id}`, kind: "suggestion", active: sg.status !== "dismissed", score: sg.score }))
        : []),
    ];
  }, [stands, zones, corridors, sign, suggestions, layers]);
  const featureByKey = useMemo(() => new Map(features.map((f) => [f.key, f])), [features]);

  const selectedFeatures = [...selectedKeys].map((k) => featureByKey.get(k)).filter(Boolean);
  const selectedCounts = selectedFeatures.reduce((c, f) => ({ ...c, [f.kind]: (c[f.kind] || 0) + 1 }), {});
  const scoutingOnly = selectedFeatures.length > 0 && selectedFeatures.every((f) => f.kind === "suggestion");
  const canActivate = selectedFeatures.some((f) => !f.active);
  const canDeactivate = selectedFeatures.some((f) => f.active);

  const exitSelect = useCallback(() => { setSelectMode(false); setBoxTool(false); setSelectedKeys(new Set()); }, []);
  useEffect(() => { if (drawMode !== "ruler") setRuler(null); }, [drawMode]);
  const rulerYd = ruler?.b ? fmtYd(distanceM(ruler.a.lat, ruler.a.lon, ruler.b.lat, ruler.b.lon)) : null;
  function toggleRuler() {
    if (drawMode === "ruler") { setDrawMode(null); return; }
    if (selectMode) exitSelect();
    setDrawMode("ruler");
  }
  function toggleSelectMode() { if (selectMode) exitSelect(); else { cancelDraw(); setSelectMode(true); } }
  const toggleSelected = useCallback((key) => setSelectedKeys((prev) => {
    const next = new Set(prev);
    if (next.has(key)) next.delete(key); else next.add(key);
    return next;
  }), []);
  const addSelected = useCallback((keys) => setSelectedKeys((prev) => new Set([...prev, ...keys])), []);
  const selectWhere = (pred) => setSelectedKeys(new Set(features.filter(pred).map((f) => f.key)));

  async function bulkAction(action) {
    if (!selectedFeatures.length) return;
    setBulkBusy(true);
    try {
      const items = selectedFeatures.map((f) => ({ kind: f.kind, id: Number(f.key.split(":")[1]) }));
      await api("/features/bulk", { method: "POST", body: JSON.stringify({ items, action }) });
      const kinds = new Set(items.map((i) => i.kind));
      await Promise.all([
        kinds.has("stand") && reloadStands(), kinds.has("zone") && reloadZones(),
        kinds.has("corridor") && reloadCorridors(), kinds.has("sign") && reloadSign(),
        kinds.has("suggestion") && reloadSuggestions(),
      ]);
      setSelectedKeys(new Set());
    } catch { setErr(`Couldn't ${action} the selected items.`); }
    finally { setBulkBusy(false); }
  }

  // starting any draw/add action leaves multi-select mode
  useEffect(() => { if (drawMode) exitSelect(); }, [drawMode, exitSelect]);
  // drop selected keys that no longer exist or are no longer visible (deleted, layer hidden, region switch)
  useEffect(() => {
    setSelectedKeys((prev) => (prev.size && [...prev].some((k) => !featureByKey.has(k))
      ? new Set([...prev].filter((k) => featureByKey.has(k))) : prev));
  }, [featureByKey]);
  useEffect(() => {
    if (!selectMode) return;
    const onKey = (e) => { if (e.key === "Escape") exitSelect(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selectMode, exitSelect]);

  // Stable identity — a new function reference here on every radius tick would
  // re-trigger HuntMap's draft-circle effect mid-drag and fight the user's own gesture.
  const onScoutRadiusChange = useCallback((radius_m) => {
    setScoutDraft((d) => (d ? { ...d, radius_m } : d));
  }, []);

  async function confirmScoutArea() {
    if (!scoutDraft) return;
    const { lat, lon, radius_m } = scoutDraft;
    try {
      const res = await api(`/scouting/overlap?lat=${lat}&lon=${lon}&radius_m=${radius_m}`);
      if (res.overlaps) {
        // Override deletes the spots whose center is inside the circle; those with notes or a color lose them.
        const annotated = res.suggestions.filter((sg) =>
          (sg.color || sg.comments?.length) && distanceM(lat, lon, sg.lat, sg.lon) <= radius_m).length;
        setScoutOverlap({ lat, lon, radius_m, count: res.suggestions.length, annotated });
      }
      else await runScoutAnalysis(lat, lon, radius_m, null);
    } catch { setErr("Couldn't check for existing scouting suggestions."); }
  }

  async function runScoutAnalysis(lat, lon, radius_m, mode) {
    setScoutOverlap(null);
    setScoutAnalyzing(true); setScoutError(null); setScoutProgress({ pct: 0, msg: "Starting..." });
    try {
      const tok = tokenStore.get();
      const regionId = regionStore.get();
      const res = await fetch(`/api/scouting/analyze`, {
        method: "POST",
        headers: { "Content-Type": "application/json",
          ...(tok ? { "Authorization": `Bearer ${tok}` } : {}),
          ...(regionId ? { "X-Region-Id": regionId } : {}) },
        body: JSON.stringify({ lat, lon, radius_m, mode }),
      });
      if (!res.ok) {
        const errJson = await res.json().catch(() => ({}));
        throw new Error(errJson.detail || `Analysis failed (status ${res.status})`);
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop();
        for (const line of lines) {
          if (!line.trim()) continue;
          const data = JSON.parse(line);
          if (data.error) throw new Error(data.error);
          if (data.progress != null) setScoutProgress((p) => ({ ...p, pct: data.progress }));
          if (data.message) setScoutProgress((p) => ({ ...p, msg: data.message }));
          if (data.complete) await reloadSuggestions();
        }
      }
    } catch (e) {
      setScoutError(e.message || "Couldn't run scouting analysis.");
    } finally {
      setScoutAnalyzing(false); setScoutDraft(null); setDrawMode(null);
    }
  }

  return (
    <div className="map-page">
      {err && <div style={{ padding: "6px 12px" }}><Banner>{err}</Banner></div>}
      {deviceAt && <div style={{ padding: "6px 12px" }}><Banner>No connection — showing the forecast saved on this phone at {savedAtLabel(deviceAt)}.</Banner></div>}
      {staleAt && <div style={{ padding: "6px 12px" }}><Banner>Showing the cached forecast from {new Date(staleAt * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })} while the weather service catches up. Reload in a minute for the latest.</Banner></div>}

      {/* ── Time controls: bottom sheet. Only the centered tab (date + time + grip) shows until it is
          dragged up (or tapped); the sheet is absolutely positioned over the map, so it takes no
          layout space. ── */}
      {days.length > 0 && (
        <BottomSheet stop={timeStop} onStopChange={setTimeStop} label="time controls"
          extra={<HourlyForecast day={curDay} selectedIndex={curHour?.index}
                   onPick={(i) => { setPlaying(false); setHourPos(i * 4); }} />}
          header={(() => {
            const h = curHour?.hour ?? 0, ampm = h >= 12 ? "PM" : "AM";
            const time = curHour ? `${h % 12 || 12}:${curMinute.toString().padStart(2, "0")} ${ampm}` : "";
            // The selected day's hunt conditions, one chip per flagged sit. Keyed by the DAY, not the time,
            // so scrubbing never makes them appear or disappear (nothing above the slider moves mid-drag);
            // the sit the selected time is in is only outlined. They ease in/out (the last chips stay
            // rendered while closing, so they fade instead of blanking).
            const sits = ["morning", "evening"].filter((s) => curDay?.hunt?.[s]);
            if (sits.length) shownHunt.current = { day: curDay, sits };
            const shown = shownHunt.current;
            const activeSit = sitAt(curDay, curHour?.hour);
            // The shelf's always-visible header: date + time, the day's weather in words beside it (wraps under
            // it on a narrow phone), then the hunt chips beside that on desktop / centred below on phones.
            return (
              <span className="sheet-hd">
                <span className="sheet-hd-line">
                  {/* the time sits in a fixed-width box so the header doesn't shift while scrubbing */}
                  <span className="sheet-hd-when">
                    <span>{curDay?.label || (!time && "Time")}{curDay?.label && time && " ·"}</span>
                    {time && <span className="map-time-fixed">{time}</span>}
                  </span>
                  {(curDay?.wx?.text || (curDay?.sunrise && curDay?.sunset)) && (
                    <span className="sheet-hd-wx">
                      {curDay.wx?.text}
                      {curDay.sunrise && curDay.sunset && (
                        <span className="sheet-hd-sun" title="Sunrise · sunset">↑ {clock12(curDay.sunrise)} · ↓ {clock12(curDay.sunset)}</span>
                      )}
                    </span>
                  )}
                </span>
                {shown && (
                  <span className={"map-day-alerts" + (sits.length ? " open" : "")} aria-hidden={!sits.length}>
                    <span className="map-day-alerts-inner"><span className="map-day-alerts-row">
                      {shown.sits.map((s) => {
                        const c = shown.day.hunt[s];
                        return (
                          <span key={s} title={sitDetail(c)}
                            className={"map-hunt-sit" + (sits.length && shown.day === curDay && s === activeSit ? " active" : "")}>
                            <span className="map-hunt-sit-name">{s === "morning" ? "☀ Morning" : "☾ Evening"}</span>
                            {sitParts(c).map((p) => <span key={p.kind} className={`hunt-part ${p.kind}`}>{p.text}</span>)}
                          </span>
                        );
                      })}
                    </span></span>
                  </span>
                )}
              </span>
            );
          })()}>
        <div className="map-ctrl-panel">
          {/* Day strip: moving between days (the shelf's header above spells out the chosen date, time and weather) */}
          <div className="map-days">
            <div className="shelf-label map-days-label">{days.length}-day forecast</div>
            <DayStrip days={days} dayIdx={dayIdx} ratings={ratings} utcOffset={utcOffset}
              onPick={(i) => { setPlaying(false); setDayIdx(i); setHourPos(morningStartIdx(days[i])); }} />
          </div>

          {/* Media-control row: back/forward step buttons + play/pause flank the
              slider (15-min steps, period colours painted on the track) + hour/15-min
              tick marks + thermal markers + tick labels */}
          {curDay && (() => {
            const srH = curDay.sunrise_h ?? 6.5;
            const ssH = curDay.sunset_h ?? 19.5;
            const numSlots = curDay.hours.length * 4;
            const hPct = (h) => `${Math.max(0, Math.min(100, (h * 4 / (numSlots - 1)) * 100)).toFixed(1)}%`;
            const pctNum = (h) => Math.max(0, Math.min(100, (h * 4 / (numSlots - 1)) * 100));
            // A finger on the slider: the slot under it, measured the way the browser places the thumb (the
            // track is inset by half the thumb at each end). The only thing that moves the slider during a
            // touch drag, so it can't flip between two readings of the same finger position.
            const slotFromTouch = (e) => {
              const t = e.touches[0];
              if (!t) return;
              const rect = e.currentTarget.getBoundingClientRect();
              const ratio = Math.max(0, Math.min(1, (t.clientX - rect.left - THUMB_PX / 2) / Math.max(1, rect.width - THUMB_PX)));
              const slot = Math.round(ratio * maxSlot);
              setPlaying(false);
              setHourPos((p) => (p === slot ? p : slot));
            };
            const slotPct = (pos) => `${Math.max(0, Math.min(100, (pos / (numSlots - 1)) * 100)).toFixed(1)}%`;
            const clock = (t) => {
              const hh = Math.floor(t), mm = Math.round((t - hh) * 60);
              return `${hh % 12 || 12}:${String(mm).padStart(2, "0")} ${hh >= 12 ? "PM" : "AM"}`;
            };
            const mStart = Math.max(0, srH - 0.25), mEnd = srH + 3;
            const eStart = ssH - 3, eEnd = Math.min(maxHour, ssH + 0.25);
            const mStartP = pctNum(mStart), mEndP = pctNum(mEnd), eStartP = pctNum(eStart), eEndP = pctNum(eEnd);
            // Same three period colours as before, painted directly onto the
            // slider's own track (transparent everywhere else so var(--bord2)
            // shows through as the neutral base).
            const trackGradient =
              `linear-gradient(to right,` +
              ` transparent 0%, transparent ${mStartP}%,` +
              ` rgba(194,136,0,0.75) ${mStartP}%, rgba(194,136,0,0.75) ${mEndP}%,` +
              ` rgba(30,127,176,0.75) ${mEndP}%, rgba(30,127,176,0.75) ${eStartP}%,` +
              ` rgba(122,63,160,0.75) ${eStartP}%, rgba(122,63,160,0.75) ${eEndP}%,` +
              ` transparent ${eEndP}%, transparent 100%), var(--bord2)`;
            return (
              // Media-control row: skip-back, scrubber (slider + tick marks/labels),
              // skip-forward, play/pause — all in one row. The tick marks/labels stay
              // nested inside the same flex-sized column as the slider (rather than
              // spanning the full panel width on their own) so their hPct()-based
              // percentage positions keep lining up under the now-narrower slider.
              <>
              <div className="shelf-label map-time-label">Time of day</div>
              <div className="map-scrub-row">
                <button className="icon-btn map-nav-btn map-step-btn map-scrub-back"
                  onClick={() => { setPlaying(false); setHourPos((p) => Math.max(0, p - 1)); }}
                  disabled={hourPos <= 0} title="Back 15 minutes">
                  <ChevronLeft size={18} />
                </button>
                <div className="map-scrub-track">
                  <div style={{ position: "relative" }}>
                    <input type="range" className="map-hour-slider"
                      min={0} max={maxSlot}
                      value={Math.min(hourPos, maxSlot)}
                      style={{ background: trackGradient }}
                      onChange={(e) => {
                        if (touchDragRef.current) return;        // a finger drag is placed by slotFromTouch alone
                        setPlaying(false); setHourPos(+e.target.value);
                      }}
                      onTouchStart={(e) => { touchDragRef.current = true; setSliderDragging(true); slotFromTouch(e); }}
                      onTouchMove={slotFromTouch}
                      onMouseEnter={() => setSliderHovering(true)}
                      onMouseLeave={() => setSliderHovering(false)}
                      onMouseDown={() => setSliderDragging(true)} />
                    {(sliderHovering || sliderDragging) && curHour && (() => {
                      const h = curHour.hour, ampm = h >= 12 ? "PM" : "AM";
                      const thumbPct = maxSlot > 0 ? (Math.min(hourPos, maxSlot) / maxSlot) * 100 : 0;
                      return (
                        <div className="map-slider-tooltip" style={{ left: `${thumbPct}%` }}>
                          {`${h % 12 || 12}:${curMinute.toString().padStart(2, "0")} ${ampm}`}
                        </div>
                      );
                    })()}
                  </div>
                  {/* Hour (major) + 15-min (minor) tick marks */}
                  <div className="map-slider-tickmarks">
                    {Array.from({ length: maxHour + 1 }, (_, h) => (
                      <React.Fragment key={h}>
                        <span className="map-slider-tick map-slider-tick--major" style={{ left: hPct(h) }} />
                        {[0.25, 0.5, 0.75].map((q) => (
                          <span key={q} className="map-slider-tick map-slider-tick--minor" style={{ left: hPct(h + q) }} />
                        ))}
                      </React.Fragment>
                    ))}
                  </div>
                  <div className="map-slider-ticks">
                    {/* Thermal-switch markers, now inline with the tick labels
                        instead of floating above the slider — absolutely
                        positioned over this row so they don't disturb the
                        labels' own space-between layout. */}
                    <div className="map-slider-markers">
                      {thermalSwitch?.rise && (
                        <div className="map-thermal-marker" style={{ left: slotPct(thermalSwitch.rise.pos), color: "#1E7FB0" }}
                          title={`Most of your stands' thermals switch to rising (~${clock(thermalSwitch.rise.t)})`}>▲</div>
                      )}
                      {thermalSwitch?.sink && (
                        <div className="map-thermal-marker" style={{ left: slotPct(thermalSwitch.sink.pos), color: "#7A3FA0" }}
                          title={`Most of your stands' thermals switch to sinking (~${clock(thermalSwitch.sink.t)})`}>▽</div>
                      )}
                    </div>
                    <span>{curDay.hours[0]?.label}</span>
                    <span>{curDay.hours[Math.floor(curDay.hours.length / 2)]?.label}</span>
                    <span>{curDay.hours[maxHour]?.label}</span>
                  </div>
                </div>
                <button className="icon-btn map-nav-btn map-step-btn map-scrub-fwd"
                  onClick={() => { setPlaying(false); setHourPos((p) => Math.min(maxSlot, p + 1)); }}
                  disabled={hourPos >= maxSlot} title="Forward 15 minutes">
                  <ChevronRight size={18} />
                </button>
                <button className="icon-btn map-nav-btn map-step-btn map-scrub-play" onClick={() => setPlaying((p) => !p)}
                  disabled={!curDay} title={playing ? "Pause" : "Play"}>
                  {playing ? <Pause size={16} /> : <Play size={16} />}
                </button>
              </div>
              </>
            );
          })()}
        </div>
        </BottomSheet>
      )}

      {/* draw mode banner */}
      {drawMode && (
        <div className="map-draw-bar">
          {drawMode === "stand"    && `${tapOrHere} to place the stand.`}
          {(drawMode === "food" || drawMode === "bedding") && `${tapOrHere} to drop the ${drawMode} zone.`}
          {(drawMode === "scrape" || drawMode === "rub") && `${tapOrHere} to mark this ${drawMode}.`}
          {hereFix && (
            <button className="btn btn-primary" style={{ marginLeft: 8 }}
                    onClick={() => onMapClick({ lat: hereFix.lat, lon: hereFix.lon })}>
              <Navigation2 size={13} /> Use my location{hereFix.accuracy ? ` ±${fmtYd(hereFix.accuracy)}` : ""}
            </button>
          )}
          {drawMode === "corridor" && `Click points along the deer path (${draftPoints.length} set).`}
          {drawMode === "corridor" && <button className="btn" style={{ marginLeft: 8 }} onClick={finishCorridor} disabled={draftPoints.length < 2}>Finish</button>}
          {drawMode === "scout" && !scoutDraft && "Click the map to drop the scouting-analysis area."}
          {drawMode === "scout" && scoutDraft && `Drag the circle's edge to resize (${fmtDist(scoutDraft.radius_m)}).`}
          {drawMode === "scout" && scoutDraft && <button className="btn" style={{ marginLeft: 8 }} onClick={confirmScoutArea} disabled={scoutAnalyzing}>Scout this area</button>}
          {drawMode === "ruler" && (!ruler ? "Tap two points to measure."
            : !ruler.b ? "Tap the second point, or drag one out from the first."
            : <span><strong>{rulerYd}</strong> · drag an end to adjust, or tap to start a new measurement.</span>)}
          {drawMode === "ruler" && locationOn && geo.status === "active" && userLocation && (
            <button className="btn" style={{ marginLeft: 8 }}
                    onClick={() => setRuler({ a: { lat: userLocation.lat, lon: userLocation.lon }, b: null })}>
              <Navigation2 size={13} /> From me
            </button>
          )}
          {drawMode === "edit" && mapEdit && editHint(mapEdit)}
          {drawMode === "edit" && mapEdit && <button className="btn btn-primary" style={{ marginLeft: 8 }} onClick={saveMapEdit} disabled={!mapEditDirty}><Save size={13} /> Save</button>}
          <button className="btn" style={{ marginLeft: 8 }} onClick={cancelDraw}>Cancel</button>
        </div>
      )}

      {selectMode && (
        <SelectionBar
          counts={selectedCounts} total={features.length}
          scoutingOnly={scoutingOnly} canActivate={canActivate} canDeactivate={canDeactivate}
          boxTool={boxTool} busy={bulkBusy}
          onToggleBox={() => setBoxTool((b) => !b)}
          onSelectAll={() => selectWhere(() => true)}
          onSelectKind={(kind) => selectWhere((f) => f.kind === kind)}
          onSelectInactive={() => selectWhere((f) => !f.active)}
          onSelectBelow={(score) => selectWhere((f) => f.kind === "suggestion" && f.score < score)}
          onClear={() => setSelectedKeys(new Set())}
          onActivate={() => bulkAction("activate")}
          onDeactivate={() => bulkAction("deactivate")}
          onDelete={() => bulkAction("delete")}
          onDone={exitSelect} />
      )}

      {/* map fills all remaining vertical space */}
      <div className="map-body">
        <div className="map-fill">
          <HuntMap ref={huntMapRef} stands={stands} zones={zones} corridors={corridors} sign={sign}
            suggestions={suggestions} conditions={conditions} drainage={drainage}
            drawMode={drawMode} onMapClick={onMapClick} draftPoints={draftPoints} layers={layers}
            mapEdit={mapEdit} onMapEditChange={onMapEditChange} ruler={ruler} onRulerChange={setRuler}
            standLayers={standLayers} onToggleStandLayer={toggleStandLayer}
            onEditFeature={onEditFeature} onDeleteFeature={onDeleteFeature}
            onDismissSuggestion={onDismissSuggestion} onSuggestionColor={onSuggestionColor}
            onAddSuggestionNote={onAddSuggestionNote} onDeleteSuggestionNote={onDeleteSuggestionNote} center={{ lat: activeRegion.lat, lon: activeRegion.lon, set: true }}
            scoutDraft={scoutDraft} onScoutRadiusChange={onScoutRadiusChange}
            scoutRadiusMin={scoutSettings.scout_radius_min_m} scoutRadiusMax={scoutSettings.scout_radius_max_m}
            selectMode={selectMode} selectedKeys={selectedKeys} onToggleSelect={toggleSelected}
            boxTool={boxTool} onBoxSelect={addSelected}
            userLocation={userLocation} maptilerKey={maptilerKey} onPublicLandStatus={setLandStatus} onTrailsStatus={setTrailsStatus}
            onRecSitesStatus={setRecSitesStatus} onRoadsStatus={setRoadsStatus}
            standActions={standActions} focusStandId={focusStandId} onFocused={() => setFocusStandId(null)}
            regionId={activeRegion.id}
            height="100%" />
        </div>
        <div className="layer-overlay">
          <button className={"layer-toggle-btn ruler" + (drawMode === "ruler" ? " on" : "")} onClick={toggleRuler}
            disabled={!!drawMode && drawMode !== "ruler"} aria-pressed={drawMode === "ruler"}
            title={drawMode === "ruler" ? "Stop measuring" : "Measure distance"}>
            <Ruler size={16} />
          </button>
          <button className={"layer-toggle-btn loc" + (locationOn ? " on" : "") + (locationOn && geo.status === "locating" ? " locating" : "")}
            onClick={toggleLocation} aria-pressed={locationOn}
            title={locationOn ? "Hide my location" : "Show my location"}>
            <Navigation2 size={16} fill={locationOn ? "currentColor" : "none"} />
          </button>
          <button className={"layer-toggle-btn" + (selectMode ? " on" : "")} onClick={toggleSelectMode}
            aria-pressed={selectMode} title={selectMode ? "Exit multi-select" : "Multi-select"}>
            <BoxSelect size={16} />
          </button>
          <button className="layer-toggle-btn" onClick={() => setShowOfflinePanel(true)} title="Download map for offline use">
            <Download size={16} />
          </button>
        </div>
        <LocationEdgeIndicator getMap={() => huntMapRef.current?.getMap?.() ?? null} location={userLocation} />
        <div className="map-add-btn">
          <AddMenu drawMode={drawMode} setDrawMode={(m) => { setDraftPoints([]); setDrawMode(m); }} />
        </div>
        {/* bottom right: the map layers button (its panel opens upward from it) right above the weather card */}
        <div className="map-right-stack">
          {layersOpen && (
            <div className="layer-chips-panel">
              <LayerChip on={layers.corridors} onClick={() => toggle("corridors")} color="#A35A1B" label="Corridors" />
              <LayerChip on={layers.zones}     onClick={() => toggle("zones")}     color="#6B4FA0" label="Zones" />
              <LayerChip on={layers.scrapes}   onClick={() => toggle("scrapes")}   color="#E87800" dot label="Scrapes" />
              <LayerChip on={layers.rubs}      onClick={() => toggle("rubs")}      color="#8B3A1A" dot label="Rubs" />
              <LayerChip on={layers.suggestions} onClick={() => toggle("suggestions")} color="#0E8A7D" label="Scouting" />
              <LayerChip on={layers.leeEddies} onClick={() => toggle("leeEddies")} color="#0E7C8A" label="Lee eddies" />
              <LayerChip on={layers.drainage} onClick={() => toggle("drainage")} color="#185FA5" label="Drainage" />
              <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
                <LayerChip on={layers.publicLand} onClick={() => toggle("publicLand")} color="#D81B60" label="Public land" />
                <button className="chip-expand-btn" onClick={() => toggleLegend("publicLand")}
                  aria-label={legendOpen.publicLand ? "Collapse legend" : "Expand legend"}>
                  {legendOpen.publicLand ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
                </button>
              </div>
              {legendOpen.publicLand && (
                <div className="land-legend">
                  {LAND_HINTS[landStatus] && <div className="land-hint">{LAND_HINTS[landStatus]}</div>}
                  {LAND_LEGEND.map(([color, label, dashed]) => (
                    <div key={label} className="land-legend-row">
                      <span className="land-swatch" style={{ borderColor: color, background: dashed ? "transparent" : color + "33", borderStyle: dashed ? "dashed" : "solid" }} />{label}
                    </div>
                  ))}
                </div>
              )}
              <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
                <LayerChip on={layers.trails} onClick={() => toggle("trails")} color="#8D6E00" label="Trails" />
                <button className="chip-expand-btn" onClick={() => toggleLegend("trails")}
                  aria-label={legendOpen.trails ? "Collapse legend" : "Expand legend"}>
                  {legendOpen.trails ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
                </button>
              </div>
              {legendOpen.trails && (
                <div className="land-legend">
                  {TRAILS_HINTS[trailsStatus] && <div className="land-hint">{TRAILS_HINTS[trailsStatus]}</div>}
                  {TRAILS_LEGEND.map(([color, label, dashed]) => (
                    <div key={label} className="land-legend-row">
                      <span className="land-swatch" style={{ borderColor: color, background: dashed ? "transparent" : color + "33", borderStyle: dashed ? "dashed" : "solid" }} />{label}
                    </div>
                  ))}
                </div>
              )}
              <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
                <LayerChip on={layers.roads} onClick={() => toggle("roads")} color="#B71C1C" label="Roads" />
                <button className="chip-expand-btn" onClick={() => toggleLegend("roads")}
                  aria-label={legendOpen.roads ? "Collapse legend" : "Expand legend"}>
                  {legendOpen.roads ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
                </button>
              </div>
              {legendOpen.roads && (
                <div className="land-legend">
                  {ROADS_HINTS[roadsStatus] && <div className="land-hint">{ROADS_HINTS[roadsStatus]}</div>}
                  {ROADS_LEGEND.map(([color, label]) => (
                    <div key={label} className="land-legend-row">
                      <span className="land-swatch" style={{ borderColor: color, background: color + "33" }} />{label}
                    </div>
                  ))}
                </div>
              )}
              <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
                <LayerChip on={layers.recSites} onClick={() => toggle("recSites")} color="#1565C0" dot label="Rec sites" />
                <button className="chip-expand-btn" onClick={() => toggleLegend("recSites")}
                  aria-label={legendOpen.recSites ? "Collapse legend" : "Expand legend"}>
                  {legendOpen.recSites ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
                </button>
              </div>
              {legendOpen.recSites && (
                <div className="land-legend">
                  {RECSITES_HINTS[recSitesStatus] && <div className="land-hint">{RECSITES_HINTS[recSitesStatus]}</div>}
                  {RECSITES_LEGEND.map(([color, label]) => (
                    <div key={label} className="land-legend-row">
                      <span className="land-swatch" style={{ borderColor: color, background: color + "33" }} />{label}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
          <button className={"layer-toggle-btn" + (layersOpen ? " open" : "")} onClick={() => setLayersOpen(o => !o)}
            aria-expanded={layersOpen} title="Map layers">
            <List size={16} />
          </button>
          {conditions && (
            <div className="map-weather-card">
              <span className="map-wpill">☁ {conditions.time.cloud}%</span>
              <span className="map-wpill">🌡 {Math.round(conditions.time.temp * 9 / 5 + 32)}°F</span>
              {conditions.time.wind_speed != null && (
                <span className="map-wpill"><Wind size={11} /> {degToCompass(conditions.time.wind_dir)} {Math.round(conditions.time.wind_speed)} mph</span>
              )}
            </div>
          )}
        </div>
        {scoutAnalyzing && (
          <div className="map-scout-progress">
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, color: "var(--sub)", marginBottom: 4 }}>
              <span>{scoutProgress.msg}</span><span>{scoutProgress.pct}%</span>
            </div>
            <div style={{ width: "100%", height: 6, background: "var(--surf)", borderRadius: 3, overflow: "hidden" }}>
              <div style={{ width: `${scoutProgress.pct}%`, height: "100%", background: "var(--navy)", transition: "width 0.2s ease" }} />
            </div>
          </div>
        )}
        {scoutError && !scoutAnalyzing && (
          <div className="map-scout-progress">
            <Banner>{scoutError}</Banner>
          </div>
        )}
      </div>

      {pendingName && pendingName.type === "zone" && pendingName.kind !== "food" && (
        <NamePrompt title={pendingName.title} onCancel={() => setPendingName(null)} onConfirm={confirmName} />
      )}
      {pendingName && pendingName.type === "zone" && pendingName.kind === "food" && (
        <FoodZonePrompt onCancel={() => setPendingName(null)} onConfirm={confirmName} />
      )}
      {pendingName && pendingName.type === "corridor" && (
        <CorridorPrompt onCancel={() => setPendingName(null)} onConfirm={confirmName} />
      )}
      {showOfflinePanel && huntMapRef.current && (
        <OfflineMapsPanel mapApi={huntMapRef.current} activeRegion={activeRegion} onClose={() => setShowOfflinePanel(false)} />
      )}
      {scoutOverlap && (
        <ScoutingOverlapPrompt count={scoutOverlap.count} annotated={scoutOverlap.annotated}
          onOverride={() => runScoutAnalysis(scoutOverlap.lat, scoutOverlap.lon, scoutOverlap.radius_m, "override")}
          onMergeOnly={() => runScoutAnalysis(scoutOverlap.lat, scoutOverlap.lon, scoutOverlap.radius_m, "merge")}
          onCancel={() => setScoutOverlap(null)} />
      )}
    </div>
  );
}

export default MapPage;
