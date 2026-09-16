import React, { useState, useEffect, useCallback, useRef } from "react";
import { Wind, Plus, ChevronLeft, ChevronRight, Play, Pause, Download } from "lucide-react";
import { api, tokenStore } from "../services/api.js";
import { localDate, morningStartIdx } from "../utils/formatters.js";
import { degToCompass } from "../utils/compass.js";
import { SCOUT_RADIUS_DEFAULT_M, SCOUT_RADIUS_MIN_M, SCOUT_RADIUS_MAX_M } from "../utils/geo.js";
import Banner from "../components/ui/Banner.jsx";
import Empty from "../components/ui/Empty.jsx";
import LayerChip from "../components/ui/LayerChip.jsx";
import HuntMap from "../components/HuntMap.jsx";
import HomeSetup from "../components/HomeSetup.jsx";
import AddMenu from "../components/AddMenu.jsx";
import OfflineMapsPanel from "../components/OfflineMapsPanel.jsx";
import { NamePrompt, FoodZonePrompt, CorridorPrompt } from "../components/Prompts.jsx";
import ScoutingOverlapPrompt from "../components/ScoutingOverlapPrompt.jsx";

function DatePickerPopup({ days, dayIdx, utcOffset, onSelect, onClose }) {
  const today = localDate(utcOffset);
  if (!days.length) return null;

  const availSet = new Set(days.map(d => d.day));

  // Calendar month is anchored to the first forecast day
  const refDate = new Date(days[0].day + "T12:00:00Z");
  const year  = refDate.getUTCFullYear();
  const month = refDate.getUTCMonth(); // 0-based

  const firstOfMonth = new Date(Date.UTC(year, month, 1));
  const startDow     = firstOfMonth.getUTCDay();          // 0=Sun
  const daysInMonth  = new Date(Date.UTC(year, month + 1, 0)).getUTCDate();
  const monthLabel   = firstOfMonth.toLocaleDateString("en-US", { month: "long", year: "numeric", timeZone: "UTC" });

  // Build cell array: leading nulls + date strings for the month
  const cells = [];
  for (let i = 0; i < startDow; i++) cells.push(null);
  for (let d = 1; d <= daysInMonth; d++) {
    cells.push(`${year}-${String(month + 1).padStart(2, "0")}-${String(d).padStart(2, "0")}`);
  }

  // If any forecast day spills into next month, append those cells too
  const lastDay   = days[days.length - 1];
  const lastDate  = new Date(lastDay.day + "T12:00:00Z");
  if (lastDate.getUTCMonth() !== month || lastDate.getUTCFullYear() !== year) {
    const nm  = month + 1, ny = nm > 11 ? year + 1 : year, nm2 = nm > 11 ? 0 : nm;
    const dnm = new Date(Date.UTC(ny, nm2 + 1, 0)).getUTCDate();
    for (let d = 1; d <= dnm; d++) {
      const ds = `${ny}-${String(nm2 + 1).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
      if (availSet.has(ds)) cells.push(ds);
    }
  }

  const DOW = ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"];

  return (
    <div className="map-date-picker">
      <div className="map-dp-hd">{monthLabel}</div>
      <div className="map-dp-dow">{DOW.map((d, i) => <span key={i}>{d}</span>)}</div>
      <div className="map-dp-grid">
        {cells.map((ds, i) => {
          if (!ds) return <span key={i} className="map-dp-empty" />;
          const avail = availSet.has(ds);
          const idx   = days.findIndex(d => d.day === ds);
          const sel   = idx === dayIdx;
          const isToday = ds === today;
          return (
            <button key={i}
              className={"map-dp-day" + (sel ? " sel" : "") + (isToday ? " today" : "") + (!avail ? " unavail" : "")}
              disabled={!avail}
              onClick={() => { onSelect(idx); onClose(); }}>
              {parseInt(ds.slice(-2), 10)}
            </button>
          );
        })}
      </div>
    </div>
  );
}

function MapPage({ stands, zones, corridors, sign, suggestions, reloadStands, reloadZones, reloadCorridors, reloadSign,
                   reloadSuggestions, onDismissSuggestion,
                   drawRequest, clearDrawRequest, relocateRequest, clearRelocateRequest,
                   openStandEditor, onEditFeature, onDeleteFeature }) {
  const [days, setDays] = useState([]);
  const [dayIdx, setDayIdx] = useState(0);
  const [hourPos, setHourPos] = useState(0);
  const [sliderHovering, setSliderHovering] = useState(false);
  const [sliderDragging, setSliderDragging] = useState(false);
  const [utcOffset, setUtcOffset] = useState(0);
  const [conditions, setConditions] = useState(null);
  const [playing, setPlaying] = useState(false);
  const [drawMode, setDrawMode] = useState(null);
  const [relocating, setRelocating] = useState(null); // { kind, id }
  const [draftPoints, setDraftPoints] = useState([]);
  
  // Updated global map layers (stand-specific elements removed)
  const [layers, setLayers] = useState({ corridors: true, zones: false, scrapes: false, rubs: false, suggestions: true });
  const [layersOpen, setLayersOpen] = useState(false);

  // New stand-specific layer state
  const [standLayers, setStandLayers] = useState({});

  const [pendingName, setPendingName] = useState(null);
  const [home, setHome] = useState(null);
  const [err, setErr] = useState(null);
  const [showDatePicker, setShowDatePicker] = useState(false);
  const datePickerRef = useRef(null);
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

  useEffect(() => {
    api("/settings").then((s) => setScoutSettings({
      scout_radius_default_m: s.scout_radius_default_m ?? SCOUT_RADIUS_DEFAULT_M,
      scout_radius_min_m: s.scout_radius_min_m ?? SCOUT_RADIUS_MIN_M,
      scout_radius_max_m: s.scout_radius_max_m ?? SCOUT_RADIUS_MAX_M,
    })).catch(() => {});
  }, []);

  // function to toggle individual stand layers
  const toggleStandLayer = useCallback((standId, layerKey) => {
    setStandLayers((prev) => {
      const current = prev[standId] || { wind: true, thermal: true, scent: true, deer: true, flow: false };
      return { ...prev, [standId]: { ...current, [layerKey]: !current[layerKey] } };
    });
  }, []);

  useEffect(() => {
    if (!showDatePicker) return;
    function h(e) { if (datePickerRef.current && !datePickerRef.current.contains(e.target)) setShowDatePicker(false); }
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, [showDatePicker]);

  useEffect(() => {
    if (!sliderDragging) return;
    const end = () => setSliderDragging(false);
    window.addEventListener("mouseup", end);
    window.addEventListener("touchend", end);
    return () => { window.removeEventListener("mouseup", end); window.removeEventListener("touchend", end); };
  }, [sliderDragging]);

  useEffect(() => { api("/home").then(setHome).catch(() => setHome({ set: false })); }, []);
  useEffect(() => { if (drawRequest) { setDrawMode(drawRequest); setDraftPoints([]); clearDrawRequest(); } }, [drawRequest, clearDrawRequest]);
  useEffect(() => { if (relocateRequest) { setRelocating(relocateRequest); setDrawMode("relocate"); setDraftPoints([]); clearRelocateRequest(); } }, [relocateRequest, clearRelocateRequest]);

  useEffect(() => {
    if (!stands.length) return;
    api("/hours").then((j) => {
      setDays(j.days || []);
      // Apply the property's UTC offset so both the date comparison and the
      // current-hour lookup use property local time rather than the browser's
      // clock timezone (which may differ) or a bare UTC date.
      const ofs = j.utc_offset_seconds ?? 0;
      setUtcOffset(ofs);
      const localNow = new Date(Date.now() + ofs * 1000);
      const todayStr  = localNow.toISOString().slice(0, 10);
      const localHour = localNow.getUTCHours();
      for (let d = 0; d < j.days.length; d++) {
        const hi = j.days[d].hours.findIndex((h) => h.hour === localHour);
        if (j.days[d].day === todayStr && hi >= 0) { setDayIdx(d); setHourPos(hi * 4); break; }
      }
    }).catch(() => setErr("Couldn't load forecast."));
  }, [stands.length]);

  const curDay     = days[dayIdx];
  const maxHour    = (curDay?.hours.length || 1) - 1;
  const maxSlot    = (curDay?.hours.length || 1) * 4 - 1;
  const curHourIdx = curDay ? Math.min(Math.floor(hourPos / 4), maxHour) : 0;
  const curMinute  = (hourPos % 4) * 15;
  const curHour    = curDay?.hours[curHourIdx];

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

  useEffect(() => {
    if (!curHour) return;
    let cancel = false;
    api("/map/conditions", { method: "POST", body: JSON.stringify({ time_index: curHour.index }) })
      .then((j) => { if (cancel) return; setConditions(j); }).catch(() => {});
    return () => { cancel = true; };
  }, [curHour?.index]);

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
    } else if (drawMode === "relocate" && relocating) {
      const { kind, id } = relocating;
      if (kind === "corridor") { setDraftPoints((p) => [...p, pt]); return; }
      try {
        if (kind === "stand") {
          const s = stands.find((x) => x.id === id);
          if (s) await api(`/stands/${id}`, { method: "PUT", body: JSON.stringify({ name: s.name, lat: pt.lat, lon: pt.lon, downhill_deg: s.downhill_deg, deer_approach_deg: s.deer_approach_deg, visibility_m: s.visibility_m }) });
          await reloadStands();
        } else if (kind === "food" || kind === "bedding") {
          const z = zones.find((x) => x.id === id);
          if (z) await api(`/zones/${id}`, { method: "PUT", body: JSON.stringify({ kind: z.kind, name: z.name, lat: pt.lat, lon: pt.lon, radius_m: z.radius_m }) });
          await reloadZones();
        }
      } catch { setErr("Couldn't move feature."); }
      setRelocating(null); setDrawMode(null);
    }
  }, [drawMode, relocating, stands, zones, openStandEditor, reloadStands, reloadZones, scoutDraft, scoutSettings]);

  function finishCorridor() {
    if (draftPoints.length >= 2)
      setPendingName({ type: "corridor", title: "Name this corridor", payload: { points: draftPoints.map((p) => [p.lat, p.lon]) } });
    setDraftPoints([]); setDrawMode(null);
  }
  async function finishRelocateCorridor() {
    if (draftPoints.length < 2 || !relocating) return;
    try {
      const c = corridors.find((x) => x.id === relocating.id);
      await api(`/corridors/${relocating.id}`, { method: "PUT", body: JSON.stringify({ name: c?.name || null, points: draftPoints.map((p) => [p.lat, p.lon]) }) });
      await reloadCorridors();
    } catch { setErr("Couldn't move corridor."); }
    setRelocating(null); setDraftPoints([]); setDrawMode(null);
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
  function cancelDraw() { setDraftPoints([]); setDrawMode(null); setRelocating(null); setScoutDraft(null); }
  const toggle = (k) => setLayers((l) => ({ ...l, [k]: !l[k] }));

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
      if (res.overlaps) setScoutOverlap({ lat, lon, radius_m, count: res.suggestions.length });
      else await runScoutAnalysis(lat, lon, radius_m, null);
    } catch { setErr("Couldn't check for existing scouting suggestions."); }
  }

  async function runScoutAnalysis(lat, lon, radius_m, mode) {
    setScoutOverlap(null);
    setScoutAnalyzing(true); setScoutError(null); setScoutProgress({ pct: 0, msg: "Starting..." });
    try {
      const tok = tokenStore.get();
      const res = await fetch(`/api/scouting/analyze`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...(tok ? { "Authorization": `Bearer ${tok}` } : {}) },
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

  if (!stands.length) {
    if (home && !home.set) return <div style={{ padding: 16 }}><HomeSetup onSaved={setHome} onCancel={() => {}} /></div>;
    return <div style={{ padding: 16 }}><Empty>Add a stand first to use the map.</Empty></div>;
  }

  return (
    <div className="map-page">
      {err && <div style={{ padding: "6px 12px" }}><Banner>{err}</Banner></div>}

      {/* ── Time controls panel ── */}
      {days.length > 0 && (
        <div className="map-ctrl-panel">
          {/* Single compact row: date nav + time + play (weather moved to a floating card on the map) */}
          <div className="map-ctrl-top">
            {/* Date nav with calendar picker popup */}
            <div className="map-day-nav-wrap" ref={datePickerRef}>
              <div className="map-day-nav">
                <button className="icon-btn map-nav-btn"
                  onClick={() => { const ni = Math.max(0, dayIdx - 1); setDayIdx(ni); setHourPos(morningStartIdx(days[ni])); }}
                  disabled={dayIdx === 0}><ChevronLeft size={18} /></button>
                <button className="map-day-label-btn" onClick={() => setShowDatePicker(s => !s)}>
                  {curDay?.label ?? "—"}
                </button>
                <button className="icon-btn map-nav-btn"
                  onClick={() => { const ni = Math.min(days.length - 1, dayIdx + 1); setDayIdx(ni); setHourPos(morningStartIdx(days[ni])); }}
                  disabled={dayIdx >= days.length - 1}><ChevronRight size={18} /></button>
              </div>
              {showDatePicker && (
                <DatePickerPopup days={days} dayIdx={dayIdx} utcOffset={utcOffset}
                  onSelect={(i) => { setDayIdx(i); setHourPos(morningStartIdx(days[i])); }}
                  onClose={() => setShowDatePicker(false)} />
              )}
            </div>
            {/* Current time */}
            {curHour && (() => {
              const h = curHour.hour, ampm = h >= 12 ? "PM" : "AM";
              return <span className="map-time-compact">{`${h % 12 || 12}:${curMinute.toString().padStart(2, "0")} ${ampm}`}</span>;
            })()}
            {/* Play / Pause */}
            <button className={"btn map-play-btn" + (playing ? " playing" : "")}
              onClick={() => setPlaying((p) => !p)} disabled={!curDay}>
              {playing ? <><Pause size={15} /> Pause</> : <><Play size={15} /> Play</>}
            </button>
          </div>

          {/* Slider (15-min steps, period colours painted on the track) + hour/15-min
              tick marks + thermal markers + tick labels */}
          {curDay && (() => {
            const srH = curDay.sunrise_h ?? 6.5;
            const ssH = curDay.sunset_h ?? 19.5;
            const numSlots = curDay.hours.length * 4;
            const hPct = (h) => `${Math.max(0, Math.min(100, (h * 4 / (numSlots - 1)) * 100)).toFixed(1)}%`;
            const pctNum = (h) => Math.max(0, Math.min(100, (h * 4 / (numSlots - 1)) * 100));
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
              <>
                <div style={{ position: "relative" }}>
                  <input type="range" className="map-hour-slider"
                    min={0} max={maxSlot}
                    value={Math.min(hourPos, maxSlot)}
                    style={{ background: trackGradient }}
                    onChange={(e) => { setPlaying(false); setHourPos(+e.target.value); }}
                    onTouchMove={(e) => {
                      const t = e.touches[0];
                      const rect = e.target.getBoundingClientRect();
                      const ratio = Math.max(0, Math.min(1, (t.clientX - rect.left) / rect.width));
                      setPlaying(false);
                      setHourPos(Math.round(ratio * maxSlot));
                    }}
                    onMouseEnter={() => setSliderHovering(true)}
                    onMouseLeave={() => setSliderHovering(false)}
                    onMouseDown={() => setSliderDragging(true)}
                    onTouchStart={() => setSliderDragging(true)} />
                  <div className="map-slider-markers">
                    <div className="map-thermal-marker" style={{ left: hPct(srH + 2), color: "#1E7FB0" }}
                      title={`Thermals switch to rising (~${Math.round(srH + 2)}:00)`}>▲</div>
                    <div className="map-thermal-marker" style={{ left: hPct(ssH - 3), color: "#7A3FA0" }}
                      title={`Thermals switch to sinking (~${Math.round(ssH - 3)}:00)`}>▽</div>
                  </div>
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
                  <span>{curDay.hours[0]?.label}</span>
                  <span>{curDay.hours[Math.floor(curDay.hours.length / 2)]?.label}</span>
                  <span>{curDay.hours[maxHour]?.label}</span>
                </div>
              </>
            );
          })()}
        </div>
      )}

      {/* draw mode banner */}
      {drawMode && (
        <div className="map-draw-bar">
          {drawMode === "stand"    && "Click the map to place the stand."}
          {(drawMode === "food" || drawMode === "bedding") && `Click the map to drop the ${drawMode} zone.`}
          {(drawMode === "scrape" || drawMode === "rub") && `Click the map to mark this ${drawMode}.`}
          {drawMode === "corridor" && `Click points along the deer path (${draftPoints.length} set).`}
          {drawMode === "corridor" && <button className="btn" style={{ marginLeft: 8 }} onClick={finishCorridor} disabled={draftPoints.length < 2}>Finish</button>}
          {drawMode === "scout" && !scoutDraft && "Click the map to drop the scouting-analysis area."}
          {drawMode === "scout" && scoutDraft && `Drag the circle's edge to resize (${Math.round(scoutDraft.radius_m)} m).`}
          {drawMode === "scout" && scoutDraft && <button className="btn" style={{ marginLeft: 8 }} onClick={confirmScoutArea} disabled={scoutAnalyzing}>Scout this area</button>}
          {drawMode === "relocate" && relocating?.kind !== "corridor" && "Tap the map to move to the new location."}
          {drawMode === "relocate" && relocating?.kind === "corridor" && `Click new path points (${draftPoints.length} set).`}
          {drawMode === "relocate" && relocating?.kind === "corridor" && <button className="btn" style={{ marginLeft: 8 }} onClick={finishRelocateCorridor} disabled={draftPoints.length < 2}>Finish</button>}
          <button className="btn" style={{ marginLeft: 8 }} onClick={cancelDraw}>Cancel</button>
        </div>
      )}

      {/* map fills all remaining vertical space */}
      <div className="map-body">
        <div className="map-fill">
          <HuntMap ref={huntMapRef} stands={stands} zones={zones} corridors={corridors} sign={sign}
            suggestions={suggestions} conditions={conditions}
            drawMode={drawMode} onMapClick={onMapClick} draftPoints={draftPoints} layers={layers}
            standLayers={standLayers} onToggleStandLayer={toggleStandLayer}
            onEditFeature={onEditFeature} onDeleteFeature={onDeleteFeature}
            onDismissSuggestion={onDismissSuggestion} center={home}
            scoutDraft={scoutDraft} onScoutRadiusChange={onScoutRadiusChange}
            scoutRadiusMin={scoutSettings.scout_radius_min_m} scoutRadiusMax={scoutSettings.scout_radius_max_m}
            height="100%" />
        </div>
        <div className="layer-overlay">
          {layersOpen && (
            <div className="layer-chips-panel">
              <LayerChip on={layers.corridors} onClick={() => toggle("corridors")} color="#A35A1B" label="Corridors" />
              <LayerChip on={layers.zones}     onClick={() => toggle("zones")}     color="#6B4FA0" label="Zones" />
              <LayerChip on={layers.scrapes}   onClick={() => toggle("scrapes")}   color="#E87800" dot label="Scrapes" />
              <LayerChip on={layers.rubs}      onClick={() => toggle("rubs")}      color="#8B3A1A" dot label="Rubs" />
              <LayerChip on={layers.suggestions} onClick={() => toggle("suggestions")} color="#0E8A7D" label="Scouting" />
            </div>
          )}
          <button className="layer-toggle-btn" onClick={() => setShowOfflinePanel(true)} title="Download map for offline use">
            <Download size={16} />
          </button>
          <button className="layer-toggle-btn" onClick={() => setLayersOpen(o => !o)} title="Map layers">
            <Plus size={16} />
          </button>
        </div>
        <div className="map-add-btn">
          <AddMenu drawMode={drawMode} setDrawMode={(m) => { setDraftPoints([]); setDrawMode(m); }} />
        </div>
        {conditions && (
          <div className="map-weather-card">
            <span className="map-wpill">☁ {conditions.time.cloud}%</span>
            <span className="map-wpill">🌡 {Math.round(conditions.time.temp * 9 / 5 + 32)}°F</span>
            {conditions.time.wind_speed != null && (
              <span className="map-wpill"><Wind size={11} /> {degToCompass(conditions.time.wind_dir)} {Math.round(conditions.time.wind_speed)} mph</span>
            )}
          </div>
        )}
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
        <OfflineMapsPanel mapApi={huntMapRef.current} onClose={() => setShowOfflinePanel(false)} />
      )}
      {scoutOverlap && (
        <ScoutingOverlapPrompt count={scoutOverlap.count}
          onOverride={() => runScoutAnalysis(scoutOverlap.lat, scoutOverlap.lon, scoutOverlap.radius_m, "override")}
          onMergeOnly={() => runScoutAnalysis(scoutOverlap.lat, scoutOverlap.lon, scoutOverlap.radius_m, "merge")}
          onCancel={() => setScoutOverlap(null)} />
      )}
    </div>
  );
}

export default MapPage;
