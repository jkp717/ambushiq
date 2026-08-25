import React, { useState, useEffect, useCallback, useRef } from "react";
import { Wind, MapPin, Plus, Trash2, Edit3, AlertTriangle, RefreshCw, Save, X, Mountain, Waves, CheckCircle2, Lock, Trees, Wheat, Footprints, Eye, EyeOff, Map as MapIcon, Settings as SettingsIcon, Sun, Target, Play, Pause, ChevronLeft, ChevronRight, Camera, ImageIcon, HardDrive } from "lucide-react";
import HuntMap from "./HuntMap.jsx";
import MiniMap from "./MiniMap.jsx";

/* ───────── compass helpers ───────── */
const DIRS = ["N","NNE","NE","ENE","E","ESE","SE","SSE","S","SSW","SW","WSW","W","WNW","NW","NNW"];
const degToCompass = (d) => DIRS[Math.round((((d % 360) + 360) % 360) / 22.5) % 16];
const compassToDeg = (c) => DIRS.indexOf(c) * 22.5;

/* ───────── API client ───────── */
const tokenStore = {
  get: () => localStorage.getItem("sa_token") || "",
  set: (t) => localStorage.setItem("sa_token", t),
  clear: () => localStorage.removeItem("sa_token"),
};
async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  const tok = tokenStore.get();
  if (tok) headers["Authorization"] = `Bearer ${tok}`;
  const r = await fetch(`/api${path}`, { ...opts, headers });
  if (r.status === 401) { const e = new Error("unauthorized"); e.code = 401; throw e; }
  if (!r.ok) { const e = new Error((await r.json().catch(() => ({}))).detail || `error ${r.status}`); e.code = r.status; throw e; }
  return r.json();
}

/* ════════════════════════════════════════════════════ */
export default function App() {
  const [authState, setAuthState] = useState("checking");
  const [tokenInput, setTokenInput] = useState("");
  const [authErr, setAuthErr] = useState("");
  const [version, setVersion] = useState("");

  useEffect(() => {
    api("/health").then((h) => {
      setVersion(h.version || "");
      if (!h.auth_required) { setAuthState("ok"); return; }
      api("/verify").then(() => setAuthState("ok")).catch(() => setAuthState("need"));
    }).catch(() => setAuthState("need"));
  }, []);

  async function tryLogin() {
    tokenStore.set(tokenInput.trim());
    try { await api("/verify"); setAuthState("ok"); setAuthErr(""); }
    catch { tokenStore.clear(); setAuthErr("That access key didn't work."); }
  }

  if (authState === "checking") return <Centered><RefreshCw className="spin" size={20} /></Centered>;
  if (authState === "need") return (
    <Centered>
      <div className="login-card">
        <div className="login-brand">
          <Wind size={28} color="var(--navy)" />
          <h1>AmbushIQ</h1>
        </div>
        <div className="login-field">
          <Lock size={15} color="var(--sub)" />
          <input type="password" value={tokenInput} placeholder="Enter access key"
            onChange={(e) => setTokenInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && tryLogin()} />
        </div>
        {authErr && <div className="login-err">{authErr}</div>}
        <button className="btn btn-primary login-btn" onClick={tryLogin}>Unlock</button>
      </div>
    </Centered>
  );
  return <Shell onLogout={() => { tokenStore.clear(); setAuthState("need"); }} version={version} />;
}

function Centered({ children }) {
  return <div style={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center", background: "var(--tert)" }}>{children}</div>;
}

/* ════════════════════════════════════════════════════
   Shell — top bar + tab nav
   ════════════════════════════════════════════════════ */
const NAV = [
  { key: "today",   label: "Outlook",  icon: Sun },
  { key: "map",     label: "Map",      icon: MapIcon },
  { key: "stands",  label: "Stands",   icon: MapPin },
  { key: "zones",   label: "Zones",    icon: Wheat },
  { key: "cameras", label: "Cameras",  icon: Camera },
  { key: "settings",label: "Settings", icon: SettingsIcon },
];

/* Camera brand display metadata (shared across camera components) */
const BRAND_LABELS = {
  spypoint: "SpyPoint", reveal: "Reveal", moultrie: "Moultrie",
  stealth_cam: "StealthCam", browning: "Browning", spartan: "Spartan",
};
const BRAND_COLORS = {
  spypoint: "#E8650A", reveal: "#2563EB", moultrie: "#16A34A",
  stealth_cam: "#7C3AED", browning: "#92400E", spartan: "#DC2626",
};

function formatRelTime(iso) {
  if (!iso) return "never";
  const diffMs = Date.now() - new Date(iso).getTime();
  const h = Math.floor(diffMs / 3600000);
  if (h < 1) return "just now";
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}
function formatDateTime(iso) {
  if (!iso) return "unknown";
  try {
    const d = new Date(iso);
    return d.toLocaleDateString("en-US", { month: "short", day: "numeric" }) + " " +
           d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
  } catch { return iso; }
}

function Shell({ onLogout, version }) {
  const [view, setView] = useState("today");
  const [stands, setStands] = useState([]);
  const [zones, setZones] = useState([]);
  const [corridors, setCorridors] = useState([]);
  const [drawRequest, setDrawRequest] = useState(null);
  const [editingStand, setEditingStand] = useState(null);
  const [editingZone, setEditingZone] = useState(null);
  const [editingCorridor, setEditingCorridor] = useState(null);
  const [relocateRequest, setRelocateRequest] = useState(null);

  const loadStands    = useCallback(async () => { try { setStands(await api("/stands")); } catch {} }, []);
  const loadZones     = useCallback(async () => { try { setZones(await api("/zones")); } catch {} }, []);
  const loadCorridors = useCallback(async () => { try { setCorridors(await api("/corridors")); } catch {} }, []);
  const loadAll = useCallback(async () => { await Promise.all([loadStands(), loadZones(), loadCorridors()]); }, [loadStands, loadZones, loadCorridors]);
  useEffect(() => { loadAll(); }, [loadAll]);

  const editFeature = useCallback((kind, id) => {
    if (kind === "stand") { const s = stands.find((x) => x.id === id); if (s) setEditingStand(s); }
    else if (kind === "food" || kind === "bedding") { const z = zones.find((x) => x.id === id); if (z) { setEditingZone(z); setView("zones"); } }
    else if (kind === "corridor") { const c = corridors.find((x) => x.id === id); if (c) { setEditingCorridor(c); setView("zones"); } }
  }, [stands, zones, corridors]);

  const deleteFeature = useCallback(async (kind, id) => {
    if (kind === "stand") { await api(`/stands/${id}`, { method: "DELETE" }); await loadStands(); }
    else if (kind === "food" || kind === "bedding") { await api(`/zones/${id}`, { method: "DELETE" }); await loadZones(); }
    else if (kind === "corridor") { await api(`/corridors/${id}`, { method: "DELETE" }); await loadCorridors(); }
  }, [loadStands, loadZones, loadCorridors]);

  function goDraw(kind) { setDrawRequest(kind); setView("map"); }

  function openStandEditor(coord) {
    setEditingStand({ id: null, name: "", lat: coord ? coord.lat.toFixed(6) : "", lon: coord ? coord.lon.toFixed(6) : "",
                      downhill_deg: null, deer_approach_deg: null, terrain: null });
  }

  function requestRelocate(kind, id) {
    setRelocateRequest({ kind, id });
    setEditingStand(null);
    setEditingZone(null);
    setEditingCorridor(null);
    setView("map");
  }

  async function saveStand(body, id) {
    if (id) await api(`/stands/${id}`, { method: "PUT", body: JSON.stringify(body) });
    else await api("/stands", { method: "POST", body: JSON.stringify(body) });
    setEditingStand(null); await loadStands();
  }

  return (
    <div className="app-shell">
      {/* top bar */}
      <header className="top-bar">
        <div className="top-bar-brand">
          <Wind size={18} color="var(--navy)" />
          <strong>AmbushIQ</strong>
          {version && <span className="top-bar-ver">v{version}</span>}
        </div>
        <button className="icon-btn" onClick={onLogout} title="Lock"><Lock size={16} /></button>
      </header>

      {/* tab bar */}
      <nav className="tab-bar">
        {NAV.map(({ key, label, icon: Icon }) => (
          <button key={key} className={"tab-btn" + (view === key ? " active" : "")} onClick={() => setView(key)}>
            <Icon size={17} /><span>{label}</span>
          </button>
        ))}
      </nav>

      {/* page content */}
      <main className="main-content">
        {view === "today" && (
          <TodayPage stands={stands} zones={zones} corridors={corridors}
            onGoDraw={goDraw} openStandEditor={openStandEditor} />
        )}
        {view === "map" && (
          <MapPage stands={stands} zones={zones} corridors={corridors}
            reloadStands={loadStands} reloadZones={loadZones} reloadCorridors={loadCorridors}
            drawRequest={drawRequest} clearDrawRequest={() => setDrawRequest(null)}
            relocateRequest={relocateRequest} clearRelocateRequest={() => setRelocateRequest(null)}
            openStandEditor={openStandEditor} onEditFeature={editFeature} onDeleteFeature={deleteFeature} />
        )}
        {view === "stands" && (
          <StandsPage stands={stands} onAdd={() => goDraw("stand")} onEdit={setEditingStand}
            onDelete={async (id) => { await api(`/stands/${id}`, { method: "DELETE" }); loadStands(); }} />
        )}
        {view === "zones" && (
          <ZonesTabPage zones={zones} corridors={corridors}
            onAdd={goDraw} reloadZones={loadZones} reloadCorridors={loadCorridors}
            editingZone={editingZone} setEditingZone={setEditingZone}
            editingCorridor={editingCorridor} setEditingCorridor={setEditingCorridor}
            onMoveOnMap={requestRelocate} />
        )}
        {view === "cameras" && <CamerasPage stands={stands} />}
        {view === "settings" && <SettingsPage />}
      </main>

      {editingStand && (
        <Modal onClose={() => setEditingStand(null)}>
          <StandEditor stand={editingStand} onSave={saveStand} onCancel={() => setEditingStand(null)} reload={loadStands}
            onMoveOnMap={editingStand.id ? (id) => requestRelocate("stand", id) : null} />
        </Modal>
      )}
    </div>
  );
}

/* ════════════════════════════════════════════════════
   TODAY PAGE — weather-app style
   ════════════════════════════════════════════════════ */
function TodayPage({ stands, onGoDraw }) {
  const [days, setDays] = useState([]);
  const [deerRatings, setDeerRatings] = useState(null);
  const [selectedDay, setSelectedDay] = useState(null);
  const [dayRanked, setDayRanked] = useState(null);
  const [useProx, setUseProx] = useState({ corridor: true, food: true, bedding: true });
  const [err, setErr] = useState(null);

  useEffect(() => {
    if (!stands.length) return;
    api("/deer-ratings").then((j) => {
      setDeerRatings(j.ratings);
      const today = new Date().toISOString().slice(0, 10);
      const hit = j.ratings.find((r) => r.day === today);
      setSelectedDay(hit ? today : j.ratings[0]?.day ?? null);
    }).catch(() => setErr("Couldn't load deer ratings."));
  }, [stands.length]);

  useEffect(() => {
    if (!stands.length) return;
    api("/hours").then((j) => setDays(j.days || [])).catch(() => {});
  }, [stands.length]);

  const curDay = days.find((d) => d.day === selectedDay);

  useEffect(() => {
    if (!curDay) return;
    let cancel = false;
    api("/day/ranked", { method: "POST", body: JSON.stringify({ day: curDay.day, use_corridor: useProx.corridor, use_food: useProx.food, use_bedding: useProx.bedding }) })
      .then((j) => { if (cancel) return; setDayRanked(j); })
      .catch(() => {});
    return () => { cancel = true; };
  }, [curDay?.day, useProx.corridor, useProx.food, useProx.bedding]);

  if (!stands.length) {
    return (
      <div className="today-empty">
        <Target size={52} color="var(--bord2)" />
        <h2>No stands yet</h2>
        <p>Add your first stand to see your daily hunt outlook and rankings.</p>
        <button className="btn btn-primary" onClick={() => onGoDraw("stand")}><Plus size={15} /> Add first stand</button>
      </div>
    );
  }

  const selectedRating = deerRatings?.find((r) => r.day === selectedDay);
  const loadableDays = days.map((d) => d.day);

  return (
    <div className="today-page">
      {err && <Banner>{err}</Banner>}

      {/* Hero */}
      {selectedRating
        ? <HeroCard rating={selectedRating} />
        : <div className="hero-skeleton"><RefreshCw className="spin" size={20} /></div>}

      {/* 14-day strip */}
      {deerRatings && (
        <section>
          <div className="section-label">14-Day Outlook</div>
          <OutlookStrip ratings={deerRatings} selectedDay={selectedDay}
            loadableDays={loadableDays} onPick={setSelectedDay} />
        </section>
      )}

      {/* Expanded day detail */}
      {selectedRating && curDay && (
        <DayDetailPanel rating={selectedRating} day={curDay}
          dayRanked={dayRanked} useProx={useProx} setUseProx={setUseProx} />
      )}

      {selectedRating && !curDay && selectedRating.confidence === "low" && (
        <div className="day-detail">
          <Banner><AlertTriangle size={14} /> This day is beyond the detailed forecast window — rut phase is reliable but stand rankings aren't available.</Banner>
          <DeerRating rating={selectedRating} />
        </div>
      )}
    </div>
  );
}

/* ── Hero card ── */
function HeroCard({ rating }) {
  const r = rating.rating;
  const tone = r >= 4 ? "var(--green)" : r === 3 ? "var(--amber)" : "var(--red)";
  const label = r >= 4 ? "Great Movement" : r === 3 ? "Moderate Movement" : r >= 2 ? "Poor Movement" : "Very Poor Movement";
  const isToday = rating.day === new Date().toISOString().slice(0, 10);
  const inp = rating.inputs || {};
  return (
    <div className="hero-card" style={{ borderTopColor: tone }}>
      <div className="hero-date">{isToday ? "Today" : rating.label}</div>
      <div className="hero-rating">
        <span className="hero-deer">{"🦌".repeat(r)}{"·".repeat(5 - r)}</span>
        <span className="hero-score" style={{ color: tone }}>{rating.score != null ? (1 + rating.score * 4).toFixed(1) : r}/5</span>
      </div>
      <div className="hero-label" style={{ color: tone }}>{label}</div>
      <div className="hero-rut">{rating.rut?.phase}</div>
      <div className="hero-weather">
        {inp.wind_mph     != null && <WeatherPill icon="💨" label={`${inp.wind_mph} mph wind`} />}
        {inp.pressure_inhg!= null && <WeatherPill icon="🔵" label={`${inp.pressure_inhg}″`} />}
        {inp.day_high_f   != null && <WeatherPill icon="🌡" label={`${inp.day_high_f}°F`} />}
        {inp.rain_mm      != null && inp.rain_mm > 0 && <WeatherPill icon="🌧" label={`${inp.rain_mm} mm`} />}
      </div>
    </div>
  );
}
function WeatherPill({ icon, label }) {
  return <span className="weather-pill">{icon} {label}</span>;
}

/* ── 14-day horizontal strip ── */
function OutlookStrip({ ratings, selectedDay, loadableDays, onPick }) {
  const today = new Date().toISOString().slice(0, 10);
  const scrollRef = useRef(null);
  const drag = useRef({ active: false, startX: 0, scrollLeft: 0, moved: false });

  function onMouseDown(e) {
    drag.current = { active: true, startX: e.clientX, scrollLeft: scrollRef.current.scrollLeft, moved: false };
    scrollRef.current.style.cursor = "grabbing";
  }
  function onMouseMove(e) {
    if (!drag.current.active) return;
    const dx = e.clientX - drag.current.startX;
    if (Math.abs(dx) > 4) drag.current.moved = true;
    scrollRef.current.scrollLeft = drag.current.scrollLeft - dx;
  }
  function endDrag() {
    drag.current.active = false;
    if (scrollRef.current) scrollRef.current.style.cursor = "grab";
  }

  return (
    <div className="outlook-scroll" ref={scrollRef}
      onMouseDown={onMouseDown} onMouseMove={onMouseMove}
      onMouseUp={endDrag} onMouseLeave={endDrag}
      onClickCapture={(e) => { if (drag.current.moved) e.stopPropagation(); }}
      style={{ cursor: "grab", userSelect: "none" }}>
      <div className="outlook-strip">
        {ratings.map((r) => {
          const tone = r.rating >= 4 ? "var(--green)" : r.rating === 3 ? "var(--amber)" : "var(--red)";
          const sel = r.day === selectedDay;
          const loadable = loadableDays.includes(r.day);
          return (
            <button key={r.day}
              className={"outlook-day" + (sel ? " selected" : "") + (r.confidence === "low" ? " low-conf" : "")}
              onClick={() => onPick(r.day)} disabled={!loadable}
              title={`${r.score != null ? (1 + r.score * 4).toFixed(1) : r.rating}/5 · ${r.rut?.phase}${r.confidence === "low" ? " · est." : ""}`}>
              <div className="od-label">{r.day === today ? "Today" : r.label}</div>
              <div className="od-deer">{"🦌".repeat(r.rating)}</div>
              <div className="od-score" style={{ color: tone }}>{r.score != null ? (1 + r.score * 4).toFixed(1) : r.rating}/5</div>
              {r.confidence === "low" && <div className="od-est">est.</div>}
            </button>
          );
        })}
      </div>
    </div>
  );
}

/* ── Expanded day detail ── */
function DayDetailPanel({ rating, day, dayRanked, useProx, setUseProx }) {
  return (
    <div className="day-detail">
      <div className="day-detail-header">
        <div>
          <h2 style={{ fontSize: 16, margin: 0 }}>{day.label}</h2>
          <div className="day-detail-meta">
            <span>☀ {day.sunrise}</span><span>☾ {day.sunset}</span>
            {rating.confidence === "low" && <span className="low-badge">est.</span>}
          </div>
        </div>
      </div>

      {rating.confidence === "low" && (
        <div className="day-low-note"><AlertTriangle size={13} /> 8+ days out — wind-based rankings are directional only.</div>
      )}

      <DeerRating rating={rating} />

      {dayRanked && dayRanked.ranked.length > 0 && (
        <div className="best-stands">
          <div className="best-stands-hd">
            <strong style={{ fontSize: 13.5 }}>Best Stands</strong>
            <div className="prox-row">
              <ProxToggle on={useProx.corridor} color="#A35A1B" label="Corridors" icon={Footprints} onClick={() => setUseProx((u) => ({ ...u, corridor: !u.corridor }))} />
              <ProxToggle on={useProx.food}     color="var(--green)" label="Food"     icon={Wheat}     onClick={() => setUseProx((u) => ({ ...u, food: !u.food }))} />
              <ProxToggle on={useProx.bedding}  color="#6B4FA0" label="Bedding"  icon={Trees}     onClick={() => setUseProx((u) => ({ ...u, bedding: !u.bedding }))} />
            </div>
          </div>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 10, fontSize: 12 }}>
            <PeriodKey color={PERIOD_COLORS.morning} label="Morning" />
            <PeriodKey color={PERIOD_COLORS.midday}  label="Midday" />
            <PeriodKey color={PERIOD_COLORS.evening} label="Evening" />
          </div>
          {dayRanked.ranked.map((row) => <DayRankCard key={row.stand.id} row={row} />)}
        </div>
      )}
    </div>
  );
}

/* ── helper: index in day.hours closest to 15 min before sunrise ── */
function morningStartIdx(day) {
  if (!day) return 0;
  const targetH = Math.max(0, (day.sunrise_h ?? 6.5) - 0.25);
  let best = 0, bestDist = Infinity;
  day.hours.forEach((h, i) => { const d = Math.abs(h.hour - targetH); if (d < bestDist) { bestDist = d; best = i; } });
  return best;
}

/* ════════════════════════════════════════════════════
   MAP PAGE — full-height map with time controls + play
   ════════════════════════════════════════════════════ */
function MapPage({ stands, zones, corridors, reloadStands, reloadZones, reloadCorridors,
                   drawRequest, clearDrawRequest, relocateRequest, clearRelocateRequest,
                   openStandEditor, onEditFeature, onDeleteFeature }) {
  const [days, setDays] = useState([]);
  const [dayIdx, setDayIdx] = useState(0);
  const [hourPos, setHourPos] = useState(0);
  const [conditions, setConditions] = useState(null);
  const [playing, setPlaying] = useState(false);
  const [drawMode, setDrawMode] = useState(null);
  const [relocating, setRelocating] = useState(null); // { kind, id }
  const [draftPoints, setDraftPoints] = useState([]);
  const [layers, setLayers] = useState({ wind: true, thermal: true, deer: true, zones: true, corridors: true });
  const [pendingName, setPendingName] = useState(null);
  const [home, setHome] = useState(null);
  const [err, setErr] = useState(null);

  useEffect(() => { api("/home").then(setHome).catch(() => setHome({ set: false })); }, []);
  useEffect(() => { if (drawRequest) { setDrawMode(drawRequest); setDraftPoints([]); clearDrawRequest(); } }, [drawRequest, clearDrawRequest]);
  useEffect(() => { if (relocateRequest) { setRelocating(relocateRequest); setDrawMode("relocate"); setDraftPoints([]); clearRelocateRequest(); } }, [relocateRequest, clearRelocateRequest]);

  useEffect(() => {
    if (!stands.length) return;
    api("/hours").then((j) => {
      setDays(j.days || []);
      const now = new Date();
      for (let d = 0; d < j.days.length; d++) {
        const hi = j.days[d].hours.findIndex((h) => h.hour === now.getHours());
        if (j.days[d].day === now.toISOString().slice(0, 10) && hi >= 0) { setDayIdx(d); setHourPos(hi); break; }
      }
    }).catch(() => setErr("Couldn't load forecast."));
  }, [stands.length]);

  const curDay  = days[dayIdx];
  const curHour = curDay?.hours[Math.min(hourPos, (curDay?.hours.length || 1) - 1)];
  const maxHour = (curDay?.hours.length || 1) - 1;

  // pause when day changes
  useEffect(() => { setPlaying(false); }, [dayIdx]);

  // play loop — advances one hour every 750 ms
  useEffect(() => {
    if (!playing || !curDay) return;
    const id = setInterval(() => {
      setHourPos((p) => {
        const max = curDay.hours.length - 1;
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
    } else if (drawMode === "relocate" && relocating) {
      const { kind, id } = relocating;
      if (kind === "corridor") { setDraftPoints((p) => [...p, pt]); return; }
      try {
        if (kind === "stand") {
          const s = stands.find((x) => x.id === id);
          if (s) await api(`/stands/${id}`, { method: "PUT", body: JSON.stringify({ name: s.name, lat: pt.lat, lon: pt.lon, downhill_deg: s.downhill_deg, deer_approach_deg: s.deer_approach_deg }) });
          await reloadStands();
        } else if (kind === "food" || kind === "bedding") {
          const z = zones.find((x) => x.id === id);
          if (z) await api(`/zones/${id}`, { method: "PUT", body: JSON.stringify({ kind: z.kind, name: z.name, lat: pt.lat, lon: pt.lon, radius_m: z.radius_m }) });
          await reloadZones();
        }
      } catch { setErr("Couldn't move feature."); }
      setRelocating(null); setDrawMode(null);
    }
  }, [drawMode, relocating, stands, zones, openStandEditor, reloadStands, reloadZones]);

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
  async function confirmName(name, usage, falloffM) {
    const pn = pendingName; setPendingName(null); if (!pn) return;
    try {
      if (pn.type === "zone") { await api("/zones", { method: "POST", body: JSON.stringify({ ...pn.payload, name: name || null }) }); await reloadZones(); }
      else { await api("/corridors", { method: "POST", body: JSON.stringify({ ...pn.payload, name: name || null, usage: usage ?? 5, falloff_m: falloffM ?? null }) }); await reloadCorridors(); }
    } catch { setErr(`Couldn't save ${pn.type}.`); }
  }
  function cancelDraw() { setDraftPoints([]); setDrawMode(null); setRelocating(null); }
  const toggle = (k) => setLayers((l) => ({ ...l, [k]: !l[k] }));

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
          {/* Row 1: date nav + play */}
          <div className="map-ctrl-row">
            <div className="map-day-nav">
              <button className="icon-btn map-nav-btn"
                onClick={() => { const ni = Math.max(0, dayIdx - 1); setDayIdx(ni); setHourPos(morningStartIdx(days[ni])); }}
                disabled={dayIdx === 0}><ChevronLeft size={18} /></button>
              <span className="map-day-label">{curDay?.label ?? "—"}</span>
              <button className="icon-btn map-nav-btn"
                onClick={() => { const ni = Math.min(days.length - 1, dayIdx + 1); setDayIdx(ni); setHourPos(morningStartIdx(days[ni])); }}
                disabled={dayIdx >= days.length - 1}><ChevronRight size={18} /></button>
            </div>
            <button className={"btn map-play-btn" + (playing ? " playing" : "")}
              onClick={() => setPlaying((p) => !p)} disabled={!curDay}>
              {playing ? <><Pause size={15} /> Pause</> : <><Play size={15} /> Play</>}
            </button>
          </div>

          {/* Row 2: slider + period band + tick labels */}
          {curDay && (() => {
            const srH = curDay.sunrise_h ?? 6.5;
            const ssH = curDay.sunset_h ?? 19.5;
            const hPct = (h) => `${Math.max(0, Math.min(100, (h / maxHour) * 100)).toFixed(1)}%`;
            const hW   = (a, b) => `${Math.max(0, Math.min(100, ((b - a) / maxHour) * 100)).toFixed(1)}%`;
            const mStart = Math.max(0, srH - 0.25), mEnd = srH + 3;
            const eStart = ssH - 3, eEnd = Math.min(maxHour, ssH + 0.25);
            return (
              <>
                <input type="range" className="map-hour-slider"
                  min={0} max={maxHour}
                  value={Math.min(hourPos, maxHour)}
                  onChange={(e) => { setPlaying(false); setHourPos(+e.target.value); }}
                  onTouchMove={(e) => {
                    const t = e.touches[0];
                    const rect = e.target.getBoundingClientRect();
                    const ratio = Math.max(0, Math.min(1, (t.clientX - rect.left) / rect.width));
                    setPlaying(false);
                    setHourPos(Math.round(ratio * maxHour));
                  }} />
                {/* Period colour band */}
                <div style={{ position: "relative", height: 5, borderRadius: 3, background: "var(--bord)", marginTop: 2, marginBottom: 3 }}>
                  <div style={{ position: "absolute", left: hPct(mStart), width: hW(mStart, mEnd),  height: "100%", background: "#C28800", opacity: 0.75, borderRadius: 3 }} title="Morning" />
                  <div style={{ position: "absolute", left: hPct(mEnd),   width: hW(mEnd, eStart),   height: "100%", background: "#1E7FB0", opacity: 0.75 }} title="Midday" />
                  <div style={{ position: "absolute", left: hPct(eStart), width: hW(eStart, eEnd),   height: "100%", background: "#7A3FA0", opacity: 0.75, borderRadius: 3 }} title="Evening" />
                </div>
                <div className="map-slider-ticks">
                  <span>{curDay.hours[0]?.label}</span>
                  <span>{curDay.hours[Math.floor(curDay.hours.length / 2)]?.label}</span>
                  <span>{curDay.hours[maxHour]?.label}</span>
                </div>
              </>
            );
          })()}

          {/* Row 3: big time + weather pills */}
          {curHour && (
            <div className="map-time-row">
              <span className="map-time-big">{curHour.label}</span>
              {conditions && (
                <div className="map-weather-pills">
                  <span className="map-wpill">☁ {conditions.time.cloud}%</span>
                  <span className="map-wpill">🌡 {Math.round(conditions.time.temp * 9 / 5 + 32)}°F</span>
                  {conditions.time.wind_speed != null && (
                    <span className="map-wpill">💨 {degToCompass(conditions.time.wind_dir)} {Math.round(conditions.time.wind_speed)} mph</span>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* draw mode banner */}
      {drawMode && (
        <div className="map-draw-bar">
          {drawMode === "stand"    && "Click the map to place the stand."}
          {(drawMode === "food" || drawMode === "bedding") && `Click the map to drop the ${drawMode} zone.`}
          {drawMode === "corridor" && `Click points along the deer path (${draftPoints.length} set).`}
          {drawMode === "corridor" && <button className="btn" style={{ marginLeft: 8 }} onClick={finishCorridor} disabled={draftPoints.length < 2}>Finish</button>}
          {drawMode === "relocate" && relocating?.kind !== "corridor" && "Tap the map to move to the new location."}
          {drawMode === "relocate" && relocating?.kind === "corridor" && `Click new path points (${draftPoints.length} set).`}
          {drawMode === "relocate" && relocating?.kind === "corridor" && <button className="btn" style={{ marginLeft: 8 }} onClick={finishRelocateCorridor} disabled={draftPoints.length < 2}>Finish</button>}
          <button className="btn" style={{ marginLeft: 8 }} onClick={cancelDraw}>Cancel</button>
        </div>
      )}

      {/* map fills all remaining vertical space */}
      <div className="map-body">
        <div className="map-fill">
          <HuntMap stands={stands} zones={zones} corridors={corridors} conditions={conditions}
            drawMode={drawMode} onMapClick={onMapClick} draftPoints={draftPoints} layers={layers}
            onEditFeature={onEditFeature} onDeleteFeature={onDeleteFeature} center={home}
            height="100%" />
        </div>
        <div className="layer-overlay">
          <LayerChip on={layers.wind}      onClick={() => toggle("wind")}      color="var(--navy)" label="Wind" />
          <LayerChip on={layers.thermal}   onClick={() => toggle("thermal")}   color="#185FA5" dashed label="Thermal" />
          <LayerChip on={layers.deer}      onClick={() => toggle("deer")}      color="#A35A1B" label="Deer" />
          <LayerChip on={layers.corridors} onClick={() => toggle("corridors")} color="#A35A1B" label="Corridors" />
          <LayerChip on={layers.zones}     onClick={() => toggle("zones")}     color="#6B4FA0" label="Zones" />
        </div>
        <div className="map-add-btn">
          <AddMenu drawMode={drawMode} setDrawMode={(m) => { setDraftPoints([]); setDrawMode(m); }} />
        </div>
      </div>

      {pendingName && pendingName.type !== "corridor" && (
        <NamePrompt title={pendingName.title} onCancel={() => setPendingName(null)} onConfirm={confirmName} />
      )}
      {pendingName && pendingName.type === "corridor" && (
        <CorridorPrompt onCancel={() => setPendingName(null)} onConfirm={confirmName} />
      )}
    </div>
  );
}

/* ════════════════════════════════════════════════════
   STANDS PAGE
   ════════════════════════════════════════════════════ */
function StandsPage({ stands, onAdd, onEdit, onDelete }) {
  return (
    <div className="list-page">
      <div className="list-header">
        <h1>Stands</h1>
        <button className="btn btn-primary" onClick={onAdd}><Plus size={15} /> Add stand</button>
      </div>
      {!stands.length && <Empty>No stands yet — click "Add stand" to place one on the map.</Empty>}
      <div className="list-grid">
        {stands.map((s) => (
          <div key={s.id} className="list-card">
            <div className="list-card-map">
              <MiniMap kind="stand" feature={{ lat: s.lat, lon: s.lon }} height={110} />
            </div>
            <div className="list-card-body">
              <div className="list-card-name">{s.name || "Unnamed stand"}</div>
              <div className="list-card-sub">
                {(+s.lat).toFixed(4)}, {(+s.lon).toFixed(4)}
                {s.terrain && <> · {s.terrain.elevation}m · drains {degToCompass(s.terrain.drainage_deg)}</>}
                {!s.terrain && s.downhill_deg != null && <> · downhill {degToCompass(s.downhill_deg)}</>}
                {s.deer_approach_deg != null && <> · deer from {degToCompass(s.deer_approach_deg)}</>}
              </div>
            </div>
            <div className="list-card-actions">
              {s.terrain && <Mountain size={13} color="var(--green)" title={"terrain: " + s.terrain.source} />}
              <button className="icon-btn" onClick={() => onEdit(s)}><Edit3 size={15} /></button>
              <button className="icon-btn" onClick={() => onDelete(s.id)}><Trash2 size={15} /></button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ════════════════════════════════════════════════════
   ZONES TAB PAGE — food / bedding / corridors sub-tabs
   ════════════════════════════════════════════════════ */
function ZonesTabPage({ zones, corridors, onAdd, reloadZones, reloadCorridors,
                        editingZone, setEditingZone, editingCorridor, setEditingCorridor, onMoveOnMap }) {
  const [tab, setTab] = useState("food");
  const TABS = [
    { key: "food",      label: "Food",      icon: Wheat },
    { key: "bedding",   label: "Bedding",   icon: Trees },
    { key: "corridors", label: "Corridors", icon: Footprints },
  ];
  return (
    <div className="list-page">
      <div className="sub-tabs">
        {TABS.map(({ key, label, icon: Icon }) => (
          <button key={key} className={"sub-tab" + (tab === key ? " active" : "")} onClick={() => setTab(key)}>
            <Icon size={14} /> {label}
          </button>
        ))}
      </div>
      {(tab === "food" || tab === "bedding") && (
        <ZonesPage kind={tab} zones={zones.filter((z) => z.kind === tab)}
          onAdd={() => onAdd(tab)} reload={reloadZones}
          editing={editingZone} setEditing={setEditingZone} onMoveOnMap={onMoveOnMap} />
      )}
      {tab === "corridors" && (
        <CorridorsPage corridors={corridors} onAdd={() => onAdd("corridor")}
          reload={reloadCorridors} editing={editingCorridor} setEditing={setEditingCorridor} onMoveOnMap={onMoveOnMap} />
      )}
    </div>
  );
}

/* ════════════════════════════════════════════════════
   ZONES / CORRIDORS (inner pages, used inside ZonesTabPage)
   ════════════════════════════════════════════════════ */
function ZonesPage({ kind, zones, onAdd, reload, editing, setEditing, onMoveOnMap }) {
  const [geom, setGeom] = useState(null);
  useEffect(() => { if (editing) setGeom({ lat: editing.lat, lon: editing.lon, radius_m: editing.radius_m }); }, [editing && editing.id]);
  async function save() {
    const g = geom || { lat: editing.lat, lon: editing.lon, radius_m: editing.radius_m };
    try {
      await api(`/zones/${editing.id}`, { method: "PUT", body: JSON.stringify({ kind, name: editing.name || null, lat: g.lat, lon: g.lon, radius_m: +g.radius_m }) });
      setEditing(null); reload();
    } catch {}
  }
  const noun = kind === "food" ? "food zone" : "bedding zone";
  return (
    <div>
      <div className="list-header">
        <h1 style={{ fontSize: 18 }}>{kind === "food" ? "Food Zones" : "Bedding Zones"}</h1>
        <button className="btn btn-primary" onClick={onAdd}><Plus size={15} /> Add {noun}</button>
      </div>
      {!zones.length && <Empty>No {noun}s yet — click "Add" to draw one on the map.</Empty>}
      <div className="list-grid">
        {zones.map((z) => (
          <div key={z.id} className="list-card">
            <div className="list-card-map"><MiniMap kind={kind} feature={{ lat: z.lat, lon: z.lon, radius_m: z.radius_m }} height={110} /></div>
            <div className="list-card-body">
              <div className="list-card-name">{z.name || `Unnamed ${noun}`}</div>
              <div className="list-card-sub">{(+z.lat).toFixed(4)}, {(+z.lon).toFixed(4)} · {z.radius_m} m radius</div>
            </div>
            <div className="list-card-actions">
              <button className="icon-btn" onClick={() => setEditing({ ...z })}><Edit3 size={15} /></button>
              <button className="icon-btn" onClick={async () => { await api(`/zones/${z.id}`, { method: "DELETE" }); reload(); }}><Trash2 size={15} /></button>
            </div>
          </div>
        ))}
      </div>
      {editing && (
        <Modal onClose={() => setEditing(null)}>
          <div className="card" style={{ padding: 16, border: "2px solid var(--navy)" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
              <strong>Edit {noun}</strong>
              <button className="icon-btn" onClick={() => setEditing(null)}><X size={16} /></button>
            </div>
            <Field label="Name"><input value={editing.name || ""} onChange={(e) => setEditing({ ...editing, name: e.target.value })} placeholder={noun} /></Field>
            <div style={{ marginTop: 12 }}>
              <span style={{ fontSize: 12, color: "var(--sub)", display: "block", marginBottom: 4 }}>Drag the center to move · drag the edge to resize</span>
              <MiniMap kind={kind} editable height={240} feature={{ lat: editing.lat, lon: editing.lon, radius_m: editing.radius_m }} onChange={(g) => setGeom(g)} />
            </div>
            {geom && <div style={{ fontSize: 12, color: "var(--sub)", marginTop: 6 }}>{geom.lat.toFixed(5)}, {geom.lon.toFixed(5)} · {Math.round(geom.radius_m)} m radius</div>}
            <div style={{ display: "flex", gap: 8, marginTop: 16, flexWrap: "wrap" }}>
              <button className="btn btn-primary" onClick={save}><Save size={15} /> Save</button>
              {onMoveOnMap && editing.id && <button className="btn" onClick={() => { onMoveOnMap(editing.kind || kind, editing.id); setEditing(null); }}><MapPin size={14} /> Move on Map</button>}
              <button className="btn" onClick={() => setEditing(null)}>Cancel</button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}

function CorridorsPage({ corridors, onAdd, reload, editing, setEditing, onMoveOnMap }) {
  const [geom, setGeom] = useState(null);
  const [editUsage, setEditUsage] = useState(5);
  const [editFalloff, setEditFalloff] = useState("");
  useEffect(() => {
    if (editing) {
      setGeom({ points: editing.points });
      setEditUsage(editing.usage ?? 5);
      setEditFalloff(editing.falloff_m != null ? editing.falloff_m : "");
    }
  }, [editing && editing.id]);
  async function save() {
    const pts = (geom && geom.points) || editing.points;
    try {
      await api(`/corridors/${editing.id}`, { method: "PUT", body: JSON.stringify({
        name: editing.name || null, points: pts,
        usage: editUsage,
        falloff_m: editFalloff !== "" && editFalloff != null ? +editFalloff : null,
      }) });
      setEditing(null); reload();
    } catch {}
  }
  return (
    <div>
      <div className="list-header">
        <h1 style={{ fontSize: 18 }}>Corridors</h1>
        <button className="btn btn-primary" onClick={onAdd}><Plus size={15} /> Add corridor</button>
      </div>
      {!corridors.length && <Empty>No corridors yet — click "Add corridor" to trace one on the map.</Empty>}
      <div className="list-grid">
        {corridors.map((c) => (
          <div key={c.id} className="list-card">
            <div className="list-card-map"><MiniMap kind="corridor" feature={{ points: c.points }} height={110} /></div>
            <div className="list-card-body">
              <div className="list-card-name">{c.name || "Unnamed corridor"}</div>
              <div className="list-card-sub">
                {c.points.length} points · Usage {c.usage ?? 5}/10
                {c.falloff_m != null ? ` · ${Math.round(c.falloff_m)}m falloff` : " · global falloff"}
              </div>
            </div>
            <div className="list-card-actions">
              <button className="icon-btn" onClick={() => setEditing({ ...c })}><Edit3 size={15} /></button>
              <button className="icon-btn" onClick={async () => { await api(`/corridors/${c.id}`, { method: "DELETE" }); reload(); }}><Trash2 size={15} /></button>
            </div>
          </div>
        ))}
      </div>
      {editing && (
        <Modal onClose={() => setEditing(null)}>
          <div className="card" style={{ padding: 16, border: "2px solid var(--navy)" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
              <strong>Edit corridor</strong>
              <button className="icon-btn" onClick={() => setEditing(null)}><X size={16} /></button>
            </div>
            <Field label="Name"><input value={editing.name || ""} onChange={(e) => setEditing({ ...editing, name: e.target.value })} placeholder="corridor" /></Field>
            {/* Usage frequency */}
            <div style={{ marginTop: 14 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
                <label style={{ fontSize: 13, fontWeight: 500 }}>Usage frequency</label>
                <span style={{ fontSize: 13, fontWeight: 600, color: "var(--navy)" }}>{editUsage}/10</span>
              </div>
              <input type="range" min={1} max={10} value={editUsage} onChange={(e) => setEditUsage(+e.target.value)} style={{ width: "100%" }} />
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10.5, color: "var(--sub)", marginTop: 2 }}>
                <span>Rarely used</span><span>Heavily used</span>
              </div>
            </div>
            {/* Per-corridor falloff */}
            <div style={{ marginTop: 14 }}>
              <Field label="Falloff distance (m) — leave blank to use global setting">
                <input type="number" value={editFalloff} min={50} max={2000} step={25}
                  placeholder="global default"
                  onChange={(e) => setEditFalloff(e.target.value)} />
              </Field>
            </div>
            <div style={{ marginTop: 12 }}>
              <span style={{ fontSize: 12, color: "var(--sub)", display: "block", marginBottom: 4 }}>Drag any point to reshape the path</span>
              <MiniMap kind="corridor" editable height={240} feature={{ points: editing.points }} onChange={(g) => setGeom(g)} />
            </div>
            <div style={{ fontSize: 12, color: "var(--sub)", marginTop: 6 }}>{((geom && geom.points) || editing.points).length} points</div>
            <div style={{ display: "flex", gap: 8, marginTop: 16, flexWrap: "wrap" }}>
              <button className="btn btn-primary" onClick={save}><Save size={15} /> Save</button>
              {onMoveOnMap && editing.id && <button className="btn" onClick={() => { onMoveOnMap("corridor", editing.id); setEditing(null); }}><MapPin size={14} /> Move on Map</button>}
              <button className="btn" onClick={() => setEditing(null)}>Cancel</button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}

/* ════════════════════════════════════════════════════
   CAMERAS PAGE — list, wizard, sightings viewer
   ════════════════════════════════════════════════════ */
function CamerasPage({ stands }) {
  const [cameras, setCameras] = useState([]);
  const [providers, setProviders] = useState([]);
  const [adding, setAdding] = useState(false);
  const [editingCam, setEditingCam] = useState(null);
  const [viewingCam, setViewingCam] = useState(null);
  const [syncing, setSyncing] = useState({});
  const [err, setErr] = useState(null);

  const load = useCallback(async () => {
    try {
      const [cams, provData] = await Promise.all([api("/cameras"), api("/camera-providers")]);
      setCameras(cams);
      setProviders(provData.providers || []);
    } catch { setErr("Couldn't load cameras."); }
  }, []);
  useEffect(() => { load(); }, [load]);

  async function toggleActive(cam) {
    try { await api(`/cameras/${cam.id}`, { method: "PUT", body: JSON.stringify({ is_active: !cam.is_active }) }); load(); } catch {}
  }
  async function deleteCamera(id) {
    if (!confirm("Delete this camera and its sightings?")) return;
    try { await api(`/cameras/${id}`, { method: "DELETE" }); load(); } catch {}
  }
  async function verify(cam) {
    try {
      const r = await api(`/cameras/${cam.id}/verify`, { method: "POST" });
      if (r.implemented === false) alert("This brand isn't implemented yet — verification not available.");
      else alert(r.ok ? "✓ Credentials verified successfully!" : "✗ Verification failed. Check credentials.");
    } catch (e) { alert(`Error: ${e.message}`); }
  }
  async function syncNow(cam) {
    setSyncing((s) => ({ ...s, [cam.id]: true }));
    try {
      const r = await api(`/cameras/${cam.id}/sync`, { method: "POST" });
      alert(`Sync complete — ${r.new_sightings} new sighting(s) recorded.`);
      load();
    } catch (e) { alert(`Sync failed: ${e.message}`); }
    finally { setSyncing((s) => ({ ...s, [cam.id]: false })); }
  }

  return (
    <div className="list-page">
      {err && <Banner>{err}</Banner>}
      <div className="list-header">
        <h1>Trail Cameras</h1>
        <button className="btn btn-primary" onClick={() => setAdding(true)}><Plus size={15} /> Add camera</button>
      </div>
      {!cameras.length && (
        <div className="cameras-empty">
          <Camera size={44} color="var(--bord2)" />
          <p>No cameras yet. Add one to sync photos and boost stand rankings with real sighting data.</p>
        </div>
      )}
      <div className="list-grid">
        {cameras.map((cam) => {
          const stand = stands.find((s) => s.id === cam.stand_id);
          const prov  = providers.find((p) => p.brand === cam.brand);
          const bc    = BRAND_COLORS[cam.brand] || "#888";
          const bl    = BRAND_LABELS[cam.brand] || cam.brand;
          return (
            <div key={cam.id} className="list-card cam-card">
              <div className="cam-brand-bar" style={{ borderLeftColor: bc }}>
                <Camera size={13} color={bc} />
                <span style={{ fontWeight: 600, fontSize: 12, color: bc }}>{bl}</span>
                {!prov?.implemented && (
                  <span className="cam-stub-badge">coming soon</span>
                )}
                <button
                  className={"cam-active-chip " + (cam.is_active ? "active" : "")}
                  onClick={() => toggleActive(cam)}
                  title={cam.is_active ? "Click to deactivate" : "Click to activate"}
                >
                  {cam.is_active ? "● Active" : "○ Inactive"}
                </button>
              </div>
              <div className="list-card-body">
                <div className="list-card-name">{cam.name}</div>
                <div className="list-card-sub">
                  {stand ? <><MapPin size={11} style={{ verticalAlign: "text-bottom" }} /> {stand.name}</> : <span style={{ color: "var(--bord2)" }}>Unassigned</span>}
                  {cam.last_sync_at && <> · synced {formatRelTime(cam.last_sync_at)}</>}
                </div>
              </div>
              <div className="list-card-actions" style={{ flexWrap: "wrap" }}>
                {prov?.implemented && <>
                  <button className="icon-btn" title="Verify credentials" onClick={() => verify(cam)}><CheckCircle2 size={15} /></button>
                  <button className="icon-btn" title="Sync now" disabled={!!syncing[cam.id]} onClick={() => syncNow(cam)}>
                    <RefreshCw size={15} className={syncing[cam.id] ? "spin" : ""} />
                  </button>
                </>}
                <button className="icon-btn" title="View sightings" onClick={() => setViewingCam(cam)}><ImageIcon size={15} /></button>
                <button className="icon-btn" title="Edit" onClick={() => setEditingCam(cam)}><Edit3 size={15} /></button>
                <button className="icon-btn" title="Delete" onClick={() => deleteCamera(cam.id)}><Trash2 size={15} /></button>
              </div>
            </div>
          );
        })}
      </div>

      {adding && (
        <Modal onClose={() => setAdding(false)}>
          <CameraWizard providers={providers} stands={stands} onSaved={() => { setAdding(false); load(); }} onCancel={() => setAdding(false)} />
        </Modal>
      )}
      {editingCam && (
        <Modal onClose={() => setEditingCam(null)}>
          <CameraEditor cam={editingCam} providers={providers} stands={stands}
            onSaved={() => { setEditingCam(null); load(); }} onCancel={() => setEditingCam(null)} />
        </Modal>
      )}
      {viewingCam && (
        <Modal onClose={() => setViewingCam(null)}>
          <SightingsPanel cam={viewingCam} onClose={() => setViewingCam(null)} />
        </Modal>
      )}
    </div>
  );
}

/* ── Camera add wizard (3 steps) ── */
function CameraWizard({ providers, stands, onSaved, onCancel }) {
  const [step, setStep] = useState(1);
  const [brand, setBrand] = useState(null);
  const [name, setName] = useState("");
  const [creds, setCreds] = useState({});
  const [standId, setStandId] = useState("");
  const [err, setErr] = useState(null);
  const [saving, setSaving] = useState(false);
  const prov = providers.find((p) => p.brand === brand);

  async function save() {
    setSaving(true); setErr(null);
    try {
      await api("/cameras", { method: "POST", body: JSON.stringify({
        name: name.trim(), brand,
        stand_id: standId ? +standId : null,
        credentials: prov?.implemented ? creds : null,
      })});
      onSaved();
    } catch (e) { setErr(e.message); }
    finally { setSaving(false); }
  }

  const STEP_LABELS = ["Brand", "Setup", "Stand"];
  return (
    <div className="card" style={{ padding: 20, border: "2px solid var(--navy)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
        <strong>Add trail camera</strong>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div className="wizard-steps">
            {STEP_LABELS.map((l, i) => (
              <span key={i} className={"wizard-step" + (step === i + 1 ? " active" : "")} title={l}>{i + 1}</span>
            ))}
          </div>
          <button className="icon-btn" onClick={onCancel}><X size={16} /></button>
        </div>
      </div>

      {step === 1 && (
        <>
          <p style={{ fontSize: 13, color: "var(--sub)", marginBottom: 12 }}>Select your camera brand:</p>
          <div className="brand-grid">
            {providers.map((p) => (
              <button key={p.brand}
                className={"brand-btn" + (brand === p.brand ? " selected" : "")}
                onClick={() => setBrand(p.brand)}>
                <Camera size={20} />
                <span>{BRAND_LABELS[p.brand] || p.brand}</span>
                {!p.implemented && <span className="brand-stub">coming soon</span>}
              </button>
            ))}
          </div>
          <div style={{ display: "flex", gap: 8, marginTop: 16 }}>
            <button className="btn btn-primary" disabled={!brand} onClick={() => setStep(2)}>Next →</button>
            <button className="btn" onClick={onCancel}>Cancel</button>
          </div>
        </>
      )}

      {step === 2 && (
        <>
          <Field label="Camera name">
            <input autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Back-40 Oak Tree" />
          </Field>
          {prov?.implemented ? (
            <div style={{ marginTop: 14 }}>
              <div style={{ fontSize: 12.5, fontWeight: 500, color: "var(--txt)", marginBottom: 8 }}>
                {BRAND_LABELS[brand]} cloud account credentials
              </div>
              {prov.credential_fields.map((field) => (
                <div key={field} style={{ marginBottom: 8 }}>
                  <Field label={field.charAt(0).toUpperCase() + field.slice(1)}>
                    <input
                      type={field === "password" ? "password" : "text"}
                      value={creds[field] || ""}
                      onChange={(e) => setCreds({ ...creds, [field]: e.target.value })}
                      placeholder={field}
                      autoComplete={field === "password" ? "current-password" : "username"}
                    />
                  </Field>
                </div>
              ))}
              <div style={{ fontSize: 11.5, color: "var(--sub)", marginTop: 4 }}>
                🔒 Credentials are encrypted at rest using your server secret.
              </div>
            </div>
          ) : (
            <div className="cam-not-impl">
              <AlertTriangle size={14} />
              <span><strong>{BRAND_LABELS[brand]}</strong> sync is not yet implemented. The camera will be recorded but won't auto-sync until the integration is added.</span>
            </div>
          )}
          {err && <div style={{ color: "var(--red)", fontSize: 13, marginTop: 8 }}>{err}</div>}
          <div style={{ display: "flex", gap: 8, marginTop: 16 }}>
            <button className="btn" onClick={() => setStep(1)}>← Back</button>
            <button className="btn btn-primary" disabled={!name.trim()} onClick={() => setStep(3)}>Next →</button>
          </div>
        </>
      )}

      {step === 3 && (
        <>
          <p style={{ fontSize: 13, color: "var(--sub)", marginBottom: 12 }}>
            Assign this camera to a stand so its sightings can boost that stand's ranking score.
          </p>
          <Field label="Stand (optional)">
            <select value={standId} onChange={(e) => setStandId(e.target.value)}>
              <option value="">— none —</option>
              {stands.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </Field>
          {err && <div style={{ color: "var(--red)", fontSize: 13, marginTop: 8 }}>{err}</div>}
          <div style={{ display: "flex", gap: 8, marginTop: 16 }}>
            <button className="btn" onClick={() => setStep(2)}>← Back</button>
            <button className="btn btn-primary" disabled={saving} onClick={save}>
              <Save size={15} /> {saving ? "Saving…" : "Add camera"}
            </button>
          </div>
        </>
      )}
    </div>
  );
}

/* ── Camera edit modal ── */
function CameraEditor({ cam, providers, stands, onSaved, onCancel }) {
  const [name, setName] = useState(cam.name || "");
  const [standId, setStandId] = useState(cam.stand_id != null ? String(cam.stand_id) : "");
  const [isActive, setIsActive] = useState(cam.is_active);
  const [updateCreds, setUpdateCreds] = useState(false);
  const [creds, setCreds] = useState({});
  const [err, setErr] = useState(null);
  const [saving, setSaving] = useState(false);
  const prov = providers.find((p) => p.brand === cam.brand);

  async function save() {
    setSaving(true); setErr(null);
    try {
      const body = {
        name: name.trim() || cam.name,
        // Explicitly send null to unassign; server uses model_fields_set to detect intent
        stand_id: standId ? +standId : null,
        is_active: isActive,
      };
      if (updateCreds && prov?.implemented) body.credentials = creds;
      await api(`/cameras/${cam.id}`, { method: "PUT", body: JSON.stringify(body) });
      onSaved();
    } catch (e) { setErr(e.message); }
    finally { setSaving(false); }
  }

  return (
    <div className="card" style={{ padding: 20, border: "2px solid var(--navy)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
        <strong>Edit camera</strong>
        <button className="icon-btn" onClick={onCancel}><X size={16} /></button>
      </div>
      <Field label="Name">
        <input value={name} onChange={(e) => setName(e.target.value)} />
      </Field>
      <div style={{ marginTop: 12 }}>
        <Field label="Assigned stand">
          <select value={standId} onChange={(e) => setStandId(e.target.value)}>
            <option value="">— unassigned —</option>
            {stands.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </Field>
      </div>
      <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, cursor: "pointer", marginTop: 12 }}>
        <input type="checkbox" checked={isActive} onChange={(e) => setIsActive(e.target.checked)} />
        Active (syncs on schedule)
      </label>
      {prov?.implemented && (
        <div style={{ marginTop: 12 }}>
          <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, cursor: "pointer", marginBottom: 8 }}>
            <input type="checkbox" checked={updateCreds} onChange={(e) => setUpdateCreds(e.target.checked)} />
            Update cloud credentials
          </label>
          {updateCreds && prov.credential_fields.map((field) => (
            <div key={field} style={{ marginBottom: 8 }}>
              <Field label={field.charAt(0).toUpperCase() + field.slice(1)}>
                <input type={field === "password" ? "password" : "text"}
                  value={creds[field] || ""} onChange={(e) => setCreds({ ...creds, [field]: e.target.value })}
                  placeholder={field} />
              </Field>
            </div>
          ))}
        </div>
      )}
      {err && <div style={{ color: "var(--red)", fontSize: 13, marginTop: 8 }}>{err}</div>}
      <div style={{ display: "flex", gap: 8, marginTop: 16 }}>
        <button className="btn btn-primary" disabled={saving} onClick={save}><Save size={15} /> {saving ? "Saving…" : "Save"}</button>
        <button className="btn" onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}

/* ── Sightings viewer modal ── */
function SightingsPanel({ cam, onClose }) {
  const [sightings, setSightings] = useState([]);
  const [loading, setLoading] = useState(true);
  const [hasMore, setHasMore] = useState(false);
  const [viewImg, setViewImg] = useState(null);
  const PAGE = 48;

  const fetchPage = useCallback(async (since = null) => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ limit: PAGE });
      if (since) params.append("since", since);
      const rows = await api(`/cameras/${cam.id}/sightings?${params}`);
      setSightings((prev) => since ? [...prev, ...rows] : rows);
      setHasMore(rows.length === PAGE);
    } catch {}
    finally { setLoading(false); }
  }, [cam.id]);

  useEffect(() => { fetchPage(); }, [fetchPage]);
  function loadMore() {
    const oldest = sightings[sightings.length - 1]?.timestamp;
    if (oldest) fetchPage(oldest);
  }

  return (
    <div className="sightings-modal">
      <div className="sightings-modal-hd">
        <div>
          <strong style={{ fontSize: 15 }}>{cam.name} — Sightings</strong>
          <div style={{ fontSize: 12, color: "var(--sub)", marginTop: 2 }}>
            {loading && !sightings.length ? "Loading…"
              : sightings.length ? `${sightings.length}+ sightings${hasMore ? " (scroll for more)" : ""}`
              : "No sightings yet"}
            {cam.last_sync_at && <> · synced {formatRelTime(cam.last_sync_at)}</>}
          </div>
        </div>
        <button className="icon-btn" onClick={onClose}><X size={16} /></button>
      </div>

      {!loading && !sightings.length && (
        <div style={{ padding: "32px 16px", textAlign: "center", color: "var(--sub)" }}>
          <Camera size={40} color="var(--bord2)" style={{ marginBottom: 10 }} />
          <p style={{ margin: 0 }}>No sightings recorded yet. Sync the camera to fetch photos.</p>
        </div>
      )}

      <div className="sightings-grid">
        {sightings.map((s) => {
          const conf = s.confidence_score;
          const confCls = conf >= 0.7 ? "conf-high" : conf >= 0.4 ? "conf-med" : "conf-low";
          return (
            <div key={s.id} className="sighting-card" onClick={() => s.image_url && setViewImg(s.image_url)}>
              {s.image_url
                ? <img src={s.image_url} alt="sighting" className="sighting-thumb" loading="lazy" />
                : <div className="sighting-nophoto"><Camera size={22} color="var(--bord2)" /></div>
              }
              <div className="sighting-meta">
                <span className="sighting-ts">{formatDateTime(s.timestamp)}</span>
                <span className={"conf-badge " + confCls}>{Math.round(conf * 100)}%</span>
              </div>
            </div>
          );
        })}
        {loading && Array.from({ length: 6 }).map((_, i) => (
          <div key={"sk" + i} className="sighting-card sighting-skeleton" />
        ))}
      </div>

      {hasMore && !loading && (
        <div style={{ padding: "12px 16px", textAlign: "center" }}>
          <button className="btn" onClick={loadMore}>Load more</button>
        </div>
      )}

      {viewImg && (
        <div className="lightbox" onClick={() => setViewImg(null)}>
          <img src={viewImg} alt="full-size sighting" className="lightbox-img" onClick={(e) => e.stopPropagation()} />
          <button className="lightbox-close" onClick={() => setViewImg(null)}><X size={20} /></button>
        </div>
      )}
    </div>
  );
}

/* ════════════════════════════════════════════════════
   HOME SETUP
   ════════════════════════════════════════════════════ */
function HomeSetup({ onSaved, onCancel }) {
  const [lat, setLat] = useState(""); const [lon, setLon] = useState(""); const [err, setErr] = useState(null);
  const valid = lat !== "" && lon !== "" && !isNaN(+lat) && !isNaN(+lon) && +lat >= -90 && +lat <= 90 && +lon >= -180 && +lon <= 180;
  async function save() {
    try { const h = await api("/home", { method: "PUT", body: JSON.stringify({ lat: +lat, lon: +lon }) }); onSaved(h); }
    catch { setErr("Couldn't save. Check the values."); }
  }
  return (
    <div className="card" style={{ padding: 16, maxWidth: 460 }}>
      <strong style={{ fontSize: 15 }}>Set your hunt region</strong>
      <p style={{ fontSize: 13, color: "var(--sub)", margin: "8px 0 12px" }}>Enter the lat/lon of your hunting area so the map has a center before you've placed stands.</p>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
        <Field label="Latitude"><input value={lat} onChange={(e) => setLat(e.target.value)} placeholder="34.7465" inputMode="decimal" /></Field>
        <Field label="Longitude"><input value={lon} onChange={(e) => setLon(e.target.value)} placeholder="-92.2896" inputMode="decimal" /></Field>
      </div>
      {err && <div style={{ fontSize: 12, color: "var(--red)", marginTop: 8 }}>{err}</div>}
      <div style={{ display: "flex", gap: 8, marginTop: 14 }}>
        <button className="btn btn-primary" disabled={!valid} onClick={save}><Save size={15} /> Save region</button>
        {onCancel && <button className="btn" onClick={onCancel}>Cancel</button>}
      </div>
    </div>
  );
}

/* ════════════════════════════════════════════════════
   SETTINGS PAGE
   ════════════════════════════════════════════════════ */
function SettingsPage() {
  const [s, setS] = useState(null); const [saved, setSaved] = useState(false); const [err, setErr] = useState(null);
  const [homeLat, setHomeLat] = useState(""); const [homeLon, setHomeLon] = useState(""); const [homeSaved, setHomeSaved] = useState(false);
  useEffect(() => { api("/settings").then(setS).catch(() => setErr("Couldn't load settings.")); }, []);
  useEffect(() => { api("/home").then((h) => { if (h.set) { setHomeLat(String(h.lat)); setHomeLon(String(h.lon)); } }).catch(() => {}); }, []);

  async function save() {
    try { const r = await api("/settings", { method: "PUT", body: JSON.stringify(s) }); setS(r); setSaved(true); setTimeout(() => setSaved(false), 1800); }
    catch { setErr("Couldn't save settings."); }
  }
  function reset() { setS({ ...s, weight_corridor: 0.15, falloff_corridor: 150, weight_food: 0.15, falloff_food: 200, weight_bedding: 0.10, falloff_bedding: 250 }); }
  function resetRating() { setS({ ...s, rate_w_pressure: 0.32, rate_w_wind: 0.20, rate_w_rain: 0.28, rate_w_temp: 0.20 }); }

  const homeValid = homeLat !== "" && homeLon !== "" && !isNaN(+homeLat) && !isNaN(+homeLon) && +homeLat >= -90 && +homeLat <= 90 && +homeLon >= -180 && +homeLon <= 180;
  async function saveHome() {
    try { await api("/home", { method: "PUT", body: JSON.stringify({ lat: +homeLat, lon: +homeLon }) }); setHomeSaved(true); setTimeout(() => setHomeSaved(false), 1800); }
    catch { setErr("Couldn't save."); }
  }

  if (!s) return <div style={{ padding: 16 }}>{err ? <Banner>{err}</Banner> : <Empty>Loading…</Empty>}</div>;

  const PROX_TYPES = [
    { key: "corridor", label: "Deer corridors", icon: Footprints, color: "#A35A1B" },
    { key: "food",     label: "Food zones",     icon: Wheat,      color: "var(--green)" },
    { key: "bedding",  label: "Bedding zones",  icon: Trees,      color: "#6B4FA0" },
  ];
  const RW = [
    { key: "rate_w_pressure", label: "Barometric pressure" },
    { key: "rate_w_wind",     label: "Wind" },
    { key: "rate_w_rain",     label: "Rain" },
    { key: "rate_w_temp",     label: "Temperature shift" },
  ];
  const sum = RW.reduce((a, r) => a + (s[r.key] ?? 0), 0) || 1;

  return (
    <div className="settings-page">
      {err && <Banner>{err}</Banner>}

      <div className="settings-section">
        <div className="settings-section-hd"><MapPin size={16} color="var(--navy)" /><strong>Hunt region</strong></div>
        <p className="settings-desc">Centers the map before you've placed any stands.</p>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr auto", gap: 10, alignItems: "end" }}>
          <Field label="Latitude"><input value={homeLat} onChange={(e) => setHomeLat(e.target.value)} placeholder="34.7465" inputMode="decimal" /></Field>
          <Field label="Longitude"><input value={homeLon} onChange={(e) => setHomeLon(e.target.value)} placeholder="-92.2896" inputMode="decimal" /></Field>
          <button className="btn btn-primary" disabled={!homeValid} onClick={saveHome} style={{ height: 38 }}><Save size={15} /> {homeSaved ? "Saved" : "Save"}</button>
        </div>
      </div>

      <div className="settings-section-title">Proximity weights</div>
      <p className="settings-desc">Stands near these features get a ranking boost. <b>Weight</b> = max bonus; <b>falloff</b> = meters beyond which a feature stops helping.</p>
      {PROX_TYPES.map(({ key, label, icon: Icon, color }) => (
        <div key={key} className="settings-section">
          <div className="settings-section-hd"><Icon size={15} color={color} /><strong>{label}</strong></div>
          <SliderRow label="Weight (max bonus)" min={0} max={0.5} step={0.01} value={s[`weight_${key}`]} display={s[`weight_${key}`].toFixed(2)} onChange={(v) => setS({ ...s, [`weight_${key}`]: v })} />
          <SliderRow label="Falloff distance" min={25} max={600} step={25} value={s[`falloff_${key}`]} display={`${Math.round(s[`falloff_${key}`])} m`} onChange={(v) => setS({ ...s, [`falloff_${key}`]: v })} />
        </div>
      ))}
      <div style={{ display: "flex", gap: 8, marginBottom: 24 }}>
        <button className="btn btn-primary" onClick={save}><Save size={15} /> {saved ? "Saved ✓" : "Save"}</button>
        <button className="btn" onClick={reset}>Reset defaults</button>
      </div>

      <div className="settings-section-title" style={{ borderTop: "1px solid var(--bord)", paddingTop: 20 }}>Daily rating — weather weights</div>
      <p className="settings-desc">Tunes the 1–5 daily movement rating. Values are relative — they balance against each other automatically.</p>
      <div className="settings-section">
        {RW.map((r) => (
          <SliderRow key={r.key} label={`${r.label} — ${Math.round((s[r.key] ?? 0) / sum * 100)}%`}
            min={0} max={1} step={0.01} value={s[r.key] ?? 0}
            display={(s[r.key] ?? 0).toFixed(2)} onChange={(v) => setS({ ...s, [r.key]: v })} />
        ))}
      </div>
      <div style={{ display: "flex", gap: 8 }}>
        <button className="btn btn-primary" onClick={save}><Save size={15} /> {saved ? "Saved ✓" : "Save"}</button>
        <button className="btn" onClick={resetRating}>Reset rating weights</button>
      </div>

      {/* ── Trail cameras ── */}
      <div className="settings-section-title" style={{ borderTop: "1px solid var(--bord)", paddingTop: 20, marginTop: 24 }}>
        <Camera size={15} color="var(--navy)" style={{ verticalAlign: "text-bottom" }} /> Trail cameras
      </div>
      <p className="settings-desc">Control how frequently cameras sync and how much recent sightings can boost stand rankings.</p>
      <div className="settings-section">
        <SliderRow label="Sync interval" min={5} max={120} step={5}
          value={s.camera_sync_interval_minutes ?? 30}
          display={`${Math.round(s.camera_sync_interval_minutes ?? 30)} min`}
          onChange={(v) => setS({ ...s, camera_sync_interval_minutes: v })} />
        <SliderRow label="Max camera boost (per stand)" min={0} max={50} step={1}
          value={s.max_camera_boost_pct ?? 15}
          display={`+${Math.round(s.max_camera_boost_pct ?? 15)}%`}
          onChange={(v) => setS({ ...s, max_camera_boost_pct: v })} />
        <SliderRow label="Image retention" min={7} max={365} step={7}
          value={s.image_retention_days ?? 60}
          display={`${Math.round(s.image_retention_days ?? 60)} days`}
          onChange={(v) => setS({ ...s, image_retention_days: v })} />
      </div>

      {/* ── Image storage directory ── */}
      <div className="settings-section">
        <div className="settings-section-hd"><HardDrive size={15} color="var(--amber)" /><strong>Image storage directory</strong></div>
        <p className="settings-desc" style={{ marginBottom: 8 }}>Absolute path on the server where downloaded trail-camera photos are stored. Changing this does not move existing files.</p>
        <Field label="Server path">
          <input value={s.camera_image_dir || ""} onChange={(e) => setS({ ...s, camera_image_dir: e.target.value })}
            placeholder="/app/data/camera_images"
            style={{ fontFamily: "var(--font-mono, monospace)", fontSize: 13 }} />
        </Field>
      </div>
      <div style={{ display: "flex", gap: 8, marginBottom: 24 }}>
        <button className="btn btn-primary" onClick={save}><Save size={15} /> {saved ? "Saved ✓" : "Save"}</button>
      </div>

      {/* ── Rut calendar ── */}
      <div className="settings-section-title" style={{ borderTop: "1px solid var(--bord)", paddingTop: 20, marginTop: 4 }}>
        🦌 Rut calendar
      </div>
      <p className="settings-desc">Peak rut date for your region. Default is Dec 5 (central Arkansas). Move earlier for northern latitudes, later for deep South.</p>
      <div className="settings-section">
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
          <Field label="Peak rut month">
            <select value={s.rut_peak_month ?? 12} onChange={(e) => setS({ ...s, rut_peak_month: +e.target.value })}>
              {["January","February","March","April","May","June","July","August","September","October","November","December"].map((m, i) => (
                <option key={i + 1} value={i + 1}>{m}</option>
              ))}
            </select>
          </Field>
          <Field label="Peak rut day">
            <select value={s.rut_peak_day ?? 5} onChange={(e) => setS({ ...s, rut_peak_day: +e.target.value })}>
              {Array.from({ length: 31 }, (_, i) => i + 1).map((d) => <option key={d} value={d}>{d}</option>)}
            </select>
          </Field>
        </div>
      </div>
      <div style={{ display: "flex", gap: 8 }}>
        <button className="btn btn-primary" onClick={save}><Save size={15} /> {saved ? "Saved ✓" : "Save all settings"}</button>
      </div>
    </div>
  );
}

/* ════════════════════════════════════════════════════
   SHARED SMALL COMPONENTS (unchanged logic)
   ════════════════════════════════════════════════════ */
function AddMenu({ drawMode, setDrawMode }) {
  const [open, setOpen] = useState(false);
  const ref = React.useRef(null);
  useEffect(() => {
    if (!open) return;
    const onDoc = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);
  const pick = (m) => { setDrawMode(m); setOpen(false); };
  const items = [
    { m: "stand",    label: "Stand",        icon: MapPin,     color: "var(--navy)" },
    { m: "food",     label: "Food zone",    icon: Wheat,      color: "var(--green)" },
    { m: "bedding",  label: "Bedding zone", icon: Trees,      color: "#6B4FA0" },
    { m: "corridor", label: "Deer corridor",icon: Footprints, color: "#A35A1B" },
  ];
  return (
    <div style={{ position: "relative" }} ref={ref}>
      <button className="btn btn-primary" onClick={() => setOpen((o) => !o)} disabled={!!drawMode}><Plus size={15} /> Add</button>
      {open && (
        <div style={{ position: "absolute", left: "50%", transform: "translateX(-50%)", top: "calc(100% + 4px)", background: "var(--bg)", border: "1px solid var(--bord2)", borderRadius: 10, padding: 6, zIndex: 3000, minWidth: 168, boxShadow: "0 6px 24px rgba(0,0,0,.18)" }}>
          {items.map(({ m, label, icon: Icon, color }) => (
            <button key={m} className="menu-item" onClick={() => pick(m)}><Icon size={15} color={color} /> {label}</button>
          ))}
        </div>
      )}
    </div>
  );
}

function NamePrompt({ title, onConfirm, onCancel }) {
  const [name, setName] = useState("");
  return (
    <Modal onClose={onCancel}>
      <div className="card" style={{ padding: 16, border: "2px solid var(--navy)" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
          <strong>{title}</strong><button className="icon-btn" onClick={onCancel}><X size={16} /></button>
        </div>
        <Field label="Name (optional)">
          <input autoFocus value={name} onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") onConfirm(name.trim()); }} placeholder="e.g. North food plot" />
        </Field>
        <div style={{ display: "flex", gap: 8, marginTop: 16 }}>
          <button className="btn btn-primary" onClick={() => onConfirm(name.trim())}><Save size={15} /> Save</button>
          <button className="btn" onClick={onCancel}>Cancel</button>
        </div>
      </div>
    </Modal>
  );
}

function CorridorPrompt({ onConfirm, onCancel }) {
  const [name, setName] = useState("");
  const [usage, setUsage] = useState(5);
  const [falloff, setFalloff] = useState(150);
  const usageLabel = usage <= 2 ? "Rarely used" : usage <= 4 ? "Occasionally used" : usage <= 6 ? "Moderately used" : usage <= 8 ? "Frequently used" : "Heavily used";
  return (
    <Modal onClose={onCancel}>
      <div className="card" style={{ padding: 16, border: "2px solid var(--navy)" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
          <strong>Name this corridor</strong>
          <button className="icon-btn" onClick={onCancel}><X size={16} /></button>
        </div>
        <Field label="Name (optional)">
          <input autoFocus value={name} onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") onConfirm(name.trim(), usage, falloff); }}
            placeholder="e.g. Ridge pinch point" />
        </Field>
        <div style={{ marginTop: 14 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
            <label style={{ fontSize: 13, fontWeight: 500 }}>Usage frequency</label>
            <span style={{ fontSize: 13, fontWeight: 600, color: "var(--navy)" }}>{usage}/10 — {usageLabel}</span>
          </div>
          <input type="range" min={1} max={10} value={usage} onChange={(e) => setUsage(+e.target.value)} style={{ width: "100%" }} />
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10.5, color: "var(--sub)", marginTop: 2 }}>
            <span>Rarely used</span><span>Heavily used</span>
          </div>
        </div>
        <div style={{ marginTop: 14 }}>
          <Field label="Falloff distance (m) — how far the bonus extends from this corridor">
            <input type="number" value={falloff} min={50} max={2000} step={25}
              onChange={(e) => setFalloff(+e.target.value)} />
          </Field>
        </div>
        <div style={{ display: "flex", gap: 8, marginTop: 16 }}>
          <button className="btn btn-primary" onClick={() => onConfirm(name.trim(), usage, falloff)}><Save size={15} /> Save</button>
          <button className="btn" onClick={onCancel}>Cancel</button>
        </div>
      </div>
    </Modal>
  );
}

function Modal({ children, onClose }) {
  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,.45)", display: "flex", alignItems: "flex-start", justifyContent: "center", padding: "40px 16px", zIndex: 4000, overflowY: "auto" }}>
      <div onClick={(e) => e.stopPropagation()} style={{ width: "100%", maxWidth: 440 }}>{children}</div>
    </div>
  );
}

const PERIOD_COLORS = { morning: "#C28800", midday: "#1E7FB0", evening: "#7A3FA0" };
const PERIOD_LABEL  = { morning: "Morning", midday: "Midday", evening: "Evening" };

function PeriodKey({ color, label }) {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
      <span style={{ width: 12, height: 12, borderRadius: 3, border: `2px solid ${color}`, display: "inline-block" }} />
      {label}
    </span>
  );
}

function ProxToggle({ on, color, label, icon: Icon, onClick }) {
  return (
    <button onClick={onClick} className="chip" style={{ display: "inline-flex", alignItems: "center", gap: 5, opacity: on ? 1 : 0.45, borderColor: on ? color : "var(--bord)" }}>
      <Icon size={13} color={color} />{label}{on ? <Eye size={12} /> : <EyeOff size={12} />}
    </button>
  );
}

function DayRankCard({ row }) {
  const { stand, periods, wins, proximity } = row;
  const winColor = wins.length ? PERIOD_COLORS[wins[0]] : undefined;
  const pct = (p) => periods[p] ? Math.round(periods[p].score.total * 100) : null;
  const proxTotal = proximity ? Math.round(proximity.total * 100) : 0;
  return (
    <div className="card" style={{ padding: "12px 14px", marginBottom: 8, border: winColor ? `2px solid ${winColor}` : undefined }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <span style={{ fontWeight: 600, fontSize: 15 }}>{stand.name}</span>
        {wins.map((p) => (
          <span key={p} style={{ fontSize: 11.5, fontWeight: 600, color: "#fff", background: PERIOD_COLORS[p], padding: "2px 8px", borderRadius: 6 }}>★ Best {PERIOD_LABEL[p].toLowerCase()}</span>
        ))}
        {proxTotal > 0 && (
          <span title={`corridor +${Math.round(proximity.corridor * 100)} · food +${Math.round(proximity.food * 100)} · bedding +${Math.round(proximity.bedding * 100)}`}
            style={{ fontSize: 11.5, fontWeight: 500, color: "var(--green)", background: "rgba(59,109,17,.12)", padding: "2px 8px", borderRadius: 6 }}>
            proximity +{proxTotal}
          </span>
        )}
      </div>
      <div style={{ display: "flex", gap: 16, marginTop: 8, flexWrap: "wrap" }}>
        {["morning", "midday", "evening"].map((p) => {
          const sc = periods[p]?.score;
          const score = pct(p);
          return (
            <div key={p} style={{ fontSize: 12, color: "var(--sub)", minWidth: 150 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 5, fontWeight: 500, color: PERIOD_COLORS[p] }}>
                <span style={{ width: 8, height: 8, borderRadius: 2, background: PERIOD_COLORS[p], display: "inline-block" }} />
                {PERIOD_LABEL[p]}{score != null && <span style={{ color: "var(--sub)", fontWeight: 400 }}> · {score}</span>}
              </div>
              {sc && (
                <div style={{ marginTop: 2, lineHeight: 1.45 }}>
                  Wind {degToCompass(periods[p].hour.wind_dir)} {Math.round(periods[p].hour.wind_speed)}mph · thermals {sc.thermal_phase}
                  {stand.deer_approach_deg != null && <> · {sc.scent_score > 0.6 ? "scent away from deer" : sc.scent_score > 0.35 ? "scent crosses deer" : "scent toward deer"}</>}
                </div>
              )}
              {!sc && <div style={{ marginTop: 2 }}>no forecast</div>}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function DeerRating({ rating }) {
  const [open, setOpen] = useState(false);
  const r = rating.rating;
  const deer = "🦌".repeat(r) + "·".repeat(5 - r);
  const tone = r >= 4 ? "var(--green)" : r === 3 ? "var(--amber)" : "var(--red)";
  const fac = rating.factors;
  const f = (v) => Math.round(v * 100);
  const bar = (label, v) => (
    <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
      <span style={{ fontSize: 11.5, color: "var(--sub)", width: 110 }}>{label}</span>
      <span style={{ flex: 1, height: 6, background: "var(--surf)", borderRadius: 3, overflow: "hidden" }}>
        <span style={{ display: "block", height: "100%", width: `${f(v)}%`, background: tone }} />
      </span>
      <span style={{ fontSize: 11, color: "var(--sub)", width: 28, textAlign: "right" }}>{f(v)}</span>
    </div>
  );
  return (
    <div className="card" style={{ padding: "10px 12px", marginBottom: 10, borderLeft: `3px solid ${tone}` }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, cursor: "pointer" }} onClick={() => setOpen((o) => !o)}>
        <span style={{ fontSize: 18, letterSpacing: 1 }} title={`${r} of 5`}>{deer}</span>
        <span style={{ fontWeight: 600, color: tone }}>{rating.score != null ? (1 + rating.score * 4).toFixed(1) : r}/5 movement</span>
        <span style={{ fontSize: 12, color: "var(--sub)" }}>· {rating.rut?.phase}</span>
        <span style={{ marginLeft: "auto", fontSize: 12, color: "var(--sub)" }}>{open ? "hide ▲" : "why? ▼"}</span>
      </div>
      {open && (
        <div style={{ marginTop: 10, paddingTop: 10, borderTop: "1px solid var(--bord)" }}>
          {bar("Rut intensity", rating.rut?.intensity)}
          {bar("Barometric", fac?.pressure)}
          {bar("Wind", fac?.wind)}
          {bar("Rain (1=dry)", fac?.rain)}
          {bar("Temp shift", fac?.temp_shift)}
          <div style={{ fontSize: 11, color: "var(--sub)", marginTop: 8, lineHeight: 1.5 }}>
            {rating.inputs?.pressure_inhg != null && <>{rating.inputs.pressure_inhg}″ · </>}
            {rating.inputs?.wind_mph      != null && <>{rating.inputs.wind_mph} mph · </>}
            {rating.inputs?.day_high_f    != null && <>{rating.inputs.day_high_f}°F · </>}
            {rating.inputs?.rain_mm       != null && <>{rating.inputs.rain_mm} mm rain</>}
          </div>
          {(() => {
            const bd = rating.breakdown;
            if (!bd || bd.length < 2) return null;
            const rutEntry = bd[0];
            const weatherFactors = bd.slice(1);
            const top = weatherFactors.reduce((best, f) => {
              const s = Math.abs((f.value || 0) - 0.5) * (typeof f.weight === "number" ? f.weight : 0);
              const b = Math.abs((best.value || 0) - 0.5) * (typeof best.weight === "number" ? best.weight : 0);
              return s > b ? f : best;
            });
            return (
              <div style={{ fontSize: 11, color: "var(--sub)", marginTop: 6, lineHeight: 1.5, borderTop: "1px solid var(--bord)", paddingTop: 6 }}>
                <span style={{ fontWeight: 600, color: "var(--txt)" }}>Why this score: </span>
                {rutEntry.impact}. Primary weather factor: {top.factor.toLowerCase()} — {top.impact}.
              </div>
            );
          })()}
          <div style={{ fontSize: 10.5, color: "var(--sub)", marginTop: 6, fontStyle: "italic" }}>Moon phase intentionally excluded — MSU research found no significant effect on buck activity.</div>
        </div>
      )}
    </div>
  );
}

function StandEditor({ stand, onSave, onCancel, reload, onMoveOnMap }) {
  const [s, setS] = useState({ ...stand });
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState(null);
  const [savedId, setSavedId] = useState(stand.id);
  const valid = s.name && s.lat !== "" && s.lon !== "" && !isNaN(+s.lat) && !isNaN(+s.lon);

  async function analyze() {
    if (!valid) return; setLoading(true); setErr(null);
    try {
      let id = savedId;
      const body = { name: s.name, lat: +s.lat, lon: +s.lon, downhill_deg: s.downhill_deg, deer_approach_deg: s.deer_approach_deg };
      if (!id) { const created = await api("/stands", { method: "POST", body: JSON.stringify(body) }); id = created.id; setSavedId(id); }
      else { await api(`/stands/${id}`, { method: "PUT", body: JSON.stringify(body) }); }
      const updated = await api(`/stands/${id}/terrain`, { method: "POST" });
      setS({ ...updated, lat: updated.lat, lon: updated.lon });
      reload && reload();
    } catch { setErr("Couldn't reach elevation source. Set downhill by hand below."); }
    finally { setLoading(false); }
  }

  return (
    <div className="card" style={{ padding: 16, border: "2px solid var(--navy)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
        <strong>{stand.id ? "Edit stand" : "New stand"}</strong>
        <button className="icon-btn" onClick={onCancel}><X size={16} /></button>
      </div>
      <Field label="Stand name"><input value={s.name} onChange={(e) => setS({ ...s, name: e.target.value })} placeholder="North Ridge" /></Field>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginTop: 10 }}>
        <Field label="Latitude"><input value={s.lat} onChange={(e) => setS({ ...s, lat: e.target.value, terrain: null })} placeholder="34.7465" inputMode="decimal" /></Field>
        <Field label="Longitude"><input value={s.lon} onChange={(e) => setS({ ...s, lon: e.target.value, terrain: null })} placeholder="-92.2896" inputMode="decimal" /></Field>
      </div>
      <div style={{ marginTop: 14, paddingTop: 12, borderTop: "1px solid var(--bord)" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
          <span style={{ fontSize: 13, color: "var(--sub)" }}>Terrain & drainage</span>
          <button className="btn" onClick={analyze} disabled={!valid || loading}><Mountain size={14} /> {loading ? "Reading…" : s.terrain ? "Re-analyze" : "Analyze terrain"}</button>
        </div>
        {err && <div style={{ fontSize: 12, color: "var(--amber)", marginBottom: 8 }}>{err}</div>}
        {s.terrain && <TerrainPanel t={s.terrain} />}
        <div style={{ marginTop: 10 }}>
          <DirPicker label="Downhill faces" value={s.downhill_deg} onChange={(d) => setS({ ...s, downhill_deg: d })} />
          <div style={{ fontSize: 11.5, color: "var(--sub)", marginTop: 4 }}>{s.terrain ? "Set from elevation grid — adjust if needed." : "Set by hand, or analyze terrain above."}</div>
        </div>
      </div>
      <div style={{ marginTop: 14, paddingTop: 12, borderTop: "1px solid var(--bord)" }}>
        <DirPicker label="Deer approach from (optional)" value={s.deer_approach_deg} onChange={(d) => setS({ ...s, deer_approach_deg: d })} allowNull />
      </div>
      <div style={{ display: "flex", gap: 8, marginTop: 16, flexWrap: "wrap" }}>
        <button className="btn btn-primary" disabled={!valid}
          onClick={() => onSave({ name: s.name, lat: +s.lat, lon: +s.lon, downhill_deg: s.downhill_deg, deer_approach_deg: s.deer_approach_deg }, savedId)}>
          <Save size={15} /> Save stand
        </button>
        {onMoveOnMap && <button className="btn" onClick={() => { onMoveOnMap(savedId || stand.id); onCancel(); }}><MapPin size={14} /> Move on Map</button>}
        <button className="btn" onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}

function TerrainPanel({ t }) {
  return (
    <div style={{ background: "var(--surf)", borderRadius: 10, padding: 12, display: "flex", gap: 14, alignItems: "center" }}>
      <TerrainMap t={t} />
      <div style={{ fontSize: 12.5, lineHeight: 1.6, flex: 1, minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 5, color: "var(--green)", marginBottom: 4 }}><CheckCircle2 size={13} /><span style={{ fontWeight: 500 }}>{t.source}</span></div>
        <div style={{ color: "var(--sub)" }}>Elevation <b style={{ color: "var(--txt)" }}>{t.elevation} m</b> · relief <b style={{ color: "var(--txt)" }}>{t.relief} m</b></div>
        <div style={{ color: "var(--sub)" }}>Slope <b style={{ color: "var(--txt)" }}>{t.slope_pct}%</b> · faces <b style={{ color: "var(--txt)" }}>{degToCompass(t.downhill_deg)}</b></div>
        <div style={{ color: "var(--sub)", display: "flex", alignItems: "center", gap: 4 }}><Waves size={12} color="var(--blue)" /> Drains <b style={{ color: "var(--txt)" }}>{degToCompass(t.drainage_deg)}</b> {t.channel_strength > 0.5 ? "(strong)" : t.channel_strength > 0.2 ? "(moderate)" : "(diffuse)"}</div>
      </div>
    </div>
  );
}

function TerrainMap({ t }) {
  const N = t.grid_size, px = 88, cell = px / N;
  const flat = t.dem.flat(), min = Math.min(...flat), max = Math.max(...flat), rng = max - min || 1;
  const maxAcc = Math.max(...t.acc.flat());
  const rects = [], chans = [];
  for (let r = 0; r < N; r++) for (let c = 0; c < N; c++) {
    const v = (t.dem[r][c] - min) / rng, shade = Math.round(235 - v * 150);
    rects.push(<rect key={"e"+r+c} x={c*cell} y={r*cell} width={cell+0.5} height={cell+0.5} fill={`rgb(${shade-10},${shade},${shade-20})`} />);
    if (t.acc[r][c] > maxAcc * 0.18) chans.push(<rect key={"a"+r+c} x={c*cell} y={r*cell} width={cell+0.5} height={cell+0.5} fill="var(--blue)" opacity={Math.min(0.85, 0.3 + t.acc[r][c]/maxAcc)} />);
  }
  const ctr = (N/2)*cell;
  return (
    <svg width={px} height={px} viewBox={`0 0 ${px} ${px}`} style={{ borderRadius: 6, flexShrink: 0, border: "1px solid var(--bord)" }}>
      {rects}{chans}
      <circle cx={ctr} cy={ctr} r={3.5} fill="none" stroke="var(--red)" strokeWidth={1.5} />
      <circle cx={ctr} cy={ctr} r={1.5} fill="var(--red)" />
    </svg>
  );
}


function SliderRow({ label, min, max, step, value, display, onChange }) {
  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 13, marginBottom: 4 }}>
        <span style={{ color: "var(--sub)" }}>{label}</span><strong>{display}</strong>
      </div>
      <input type="range" min={min} max={max} step={step} value={value} onChange={(e) => onChange(+e.target.value)} style={{ width: "100%" }} />
    </div>
  );
}

function Field({ label, children }) {
  return <label style={{ display: "block" }}><span style={{ fontSize: 12, color: "var(--sub)", display: "block", marginBottom: 4 }}>{label}</span>{children}</label>;
}
function Empty({ children }) {
  return <div style={{ fontSize: 13.5, color: "var(--sub)", padding: "10px 0" }}>{children}</div>;
}
function Banner({ children }) {
  return <div style={{ background: "rgba(133,79,11,.12)", color: "var(--amber)", padding: "8px 12px", borderRadius: 8, fontSize: 13, marginBottom: 10, display: "flex", gap: 8, alignItems: "center" }}><AlertTriangle size={15} /> {children}</div>;
}
function LayerChip({ on, onClick, color, label, dashed }) {
  return (
    <button onClick={onClick} className="chip" style={{ display: "inline-flex", alignItems: "center", gap: 6, opacity: on ? 1 : 0.45 }}>
      <span style={{ display: "inline-block", width: 16, height: 0, borderTop: `2px ${dashed ? "dashed" : "solid"} ${color}` }} />
      {label}{on ? <Eye size={12} /> : <EyeOff size={12} />}
    </button>
  );
}
function DirPicker({ label, value, onChange, allowNull }) {
  return (
    <div>
      <div style={{ fontSize: 13, color: "var(--sub)", marginBottom: 6 }}>
        {label}{value != null && <strong style={{ color: "var(--txt)" }}> · {degToCompass(value)}</strong>}
      </div>
      <div className="grid-dir">
        {allowNull && <button className={"chip"+(value==null?" on":"")} onClick={() => onChange(null)}>none</button>}
        {DIRS.map((d) => <button key={d} className={"chip"+(value===compassToDeg(d)?" on":"")} onClick={() => onChange(Math.round(compassToDeg(d)))}>{d}</button>)}
      </div>
    </div>
  );
}
