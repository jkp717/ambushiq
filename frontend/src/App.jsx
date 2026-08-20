import React, { useState, useEffect, useCallback } from "react";
import { Wind, MapPin, Plus, Trash2, Edit3, AlertTriangle, RefreshCw, Save, X, Mountain, Waves, CheckCircle2, Lock, Trees, Wheat, Footprints, Eye, EyeOff, Map as MapIcon, Menu, ChevronLeft, Settings as SettingsIcon, Camera, ChevronDown } from "lucide-react";
import HuntMap from "./HuntMap.jsx";
import MiniMap from "./MiniMap.jsx";

/* ───────── compass helpers ───────── */
const DIRS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];
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
    catch { tokenStore.clear(); setAuthErr("That access key didn’t work."); }
  }

  if (authState === "checking") return <Centered><RefreshCw className="spin" size={20} /></Centered>;
  if (authState === "need") return (
    <Centered>
      <div className="card" style={{ padding: 24, maxWidth: 360, width: "100%" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 14 }}>
          <Lock size={18} color="var(--navy)" /><h2>Enter access key</h2>
        </div>
        <input type="password" value={tokenInput} placeholder="Access key"
          onChange={(e) => setTokenInput(e.target.value)} onKeyDown={(e) => e.key === "Enter" && tryLogin()} />
        {authErr && <div style={{ color: "var(--red)", fontSize: 13, marginTop: 8 }}>{authErr}</div>}
        <button className="btn btn-primary" style={{ marginTop: 14, width: "100%", justifyContent: "center" }} onClick={tryLogin}>Unlock</button>
      </div>
    </Centered>
  );
  return <Shell onLogout={() => { tokenStore.clear(); setAuthState("need"); }} version={version} />;
}

function Centered({ children }) {
  return <div style={{ minHeight: "70vh", display: "flex", alignItems: "center", justifyContent: "center" }}>{children}</div>;
}

/* ════════════════════════════════════════════════════
   Shell: collapsible sidebar + routed pages
   ════════════════════════════════════════════════════ */
const NAV = [
  { key: "map", label: "Hunt Planner", icon: MapIcon },
  { key: "stands", label: "Stand locations", icon: MapPin },
  { key: "food", label: "Food zones", icon: Wheat },
  { key: "bedding", label: "Bedding zones", icon: Trees },
  { key: "corridors", label: "Corridors", icon: Footprints },
  { key: "settings", label: "Settings", icon: SettingsIcon },
];

function Shell({ onLogout, version }) {
  const [view, setView] = useState("map");
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);

  // shared data
  const [stands, setStands] = useState([]);
  const [zones, setZones] = useState([]);
  const [corridors, setCorridors] = useState([]);

  // a draw request handed from a list page to the Map page
  const [drawRequest, setDrawRequest] = useState(null); // 'stand' | 'food' | 'bedding' | 'corridor'
  // modal editor state
  const [editingStand, setEditingStand] = useState(null);
  const [editingZone, setEditingZone] = useState(null);
  const [editingCorridor, setEditingCorridor] = useState(null);

  const loadStands = useCallback(async () => { try { setStands(await api("/stands")); } catch {} }, []);
  const loadZones = useCallback(async () => { try { setZones(await api("/zones")); } catch {} }, []);
  const loadCorridors = useCallback(async () => { try { setCorridors(await api("/corridors")); } catch {} }, []);
  const loadAll = useCallback(async () => { await Promise.all([loadStands(), loadZones(), loadCorridors()]); }, [loadStands, loadZones, loadCorridors]);
  useEffect(() => { loadAll(); }, [loadAll]);

  // dispatch an edit request coming from a map popup to the right editor/page
  const editFeature = useCallback((kind, id) => {
    if (kind === "stand") { const s = stands.find((x) => x.id === id); if (s) setEditingStand(s); }
    else if (kind === "food" || kind === "bedding") { const z = zones.find((x) => x.id === id); if (z) { setEditingZone(z); setView(kind); } }
    else if (kind === "corridor") { const c = corridors.find((x) => x.id === id); if (c) { setEditingCorridor(c); setView("corridors"); } }
  }, [stands, zones, corridors]);

  const deleteFeature = useCallback(async (kind, id) => {
    if (kind === "stand") { await api(`/stands/${id}`, { method: "DELETE" }); await loadStands(); }
    else if (kind === "food" || kind === "bedding") { await api(`/zones/${id}`, { method: "DELETE" }); await loadZones(); }
    else if (kind === "corridor") { await api(`/corridors/${id}`, { method: "DELETE" }); await loadCorridors(); }
  }, [loadStands, loadZones, loadCorridors]);

  // navigation that can also trigger a draw mode on the map
  function goDraw(kind) { setDrawRequest(kind); setView("map"); setMobileOpen(false); }
  function navTo(key) { setView(key); setMobileOpen(false); }

  async function saveStand(body, id) {
    if (id) await api(`/stands/${id}`, { method: "PUT", body: JSON.stringify(body) });
    else await api("/stands", { method: "POST", body: JSON.stringify(body) });
    setEditingStand(null); await loadStands();
  }
  function openStandEditor(coord) {
    setEditingStand({ id: null, name: "", lat: coord ? coord.lat.toFixed(6) : "", lon: coord ? coord.lon.toFixed(6) : "",
                      downhill_deg: null, deer_approach_deg: null, terrain: null });
  }

  const sidebarW = collapsed ? 56 : 220;

  return (
    <div style={{ display: "flex", minHeight: "100vh" }}>
      {/* ───── sidebar ───── */}
      <aside style={{
        width: sidebarW, flexShrink: 0, background: "var(--surf)", borderRight: "1px solid var(--bord)",
        position: "sticky", top: 0, height: "100vh", display: "flex", flexDirection: "column",
        transition: "width .15s ease", zIndex: 20,
      }} className={"sidebar" + (mobileOpen ? " mobile-open" : "")}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "14px 14px 10px" }}>
          <Wind size={20} color="var(--navy)" style={{ flexShrink: 0 }} />
          {!collapsed && <strong style={{ fontSize: 15 }}>AmbushIQ</strong>}
          <button className="icon-btn" style={{ marginLeft: "auto" }} onClick={() => setCollapsed((c) => !c)} title={collapsed ? "Expand" : "Collapse"}>
            {collapsed ? <Menu size={16} /> : <ChevronLeft size={16} />}
          </button>
        </div>
        <nav style={{ display: "flex", flexDirection: "column", gap: 2, padding: "6px 8px", flex: 1 }}>
          {NAV.map((n) => {
            const Icon = n.icon, active = view === n.key;
            return (
              <button key={n.key} onClick={() => navTo(n.key)} title={n.label}
                style={{
                  display: "flex", alignItems: "center", gap: 10, padding: "9px 10px", borderRadius: 8,
                  border: "none", cursor: "pointer", fontSize: 14, width: "100%", textAlign: "left",
                  background: active ? "var(--bg)" : "transparent", color: active ? "var(--navy)" : "var(--txt)",
                  fontWeight: active ? 600 : 400,
                }}>
                <Icon size={17} style={{ flexShrink: 0 }} />{!collapsed && <span>{n.label}</span>}
              </button>
            );
          })}
        </nav>
        <div style={{ padding: 10, borderTop: "1px solid var(--bord)" }}>
          <button className="icon-btn" onClick={onLogout} title="Lock"><Lock size={16} />{!collapsed && <span style={{ fontSize: 13, marginLeft: 8 }}>Lock</span>}</button>
          {!collapsed && <div style={{ fontSize: 10.5, color: "var(--sub)", marginTop: 6, paddingLeft: 7 }}>v{version}</div>}
        </div>
      </aside>

      {/* mobile menu button + scrim */}
      <button className="mobile-menu-btn" onClick={() => setMobileOpen(true)} title="Menu"><Menu size={20} /></button>
      {mobileOpen && <div className="scrim" onClick={() => setMobileOpen(false)} />}

      {/* ───── main content ───── */}
      <main style={{ flex: 1, minWidth: 0, padding: "20px 22px 64px", maxWidth: 1000, margin: "0 auto", width: "100%" }}>
        {view === "map" && (
          <MapPage stands={stands} zones={zones} corridors={corridors}
            reloadStands={loadStands} reloadZones={loadZones} reloadCorridors={loadCorridors}
            drawRequest={drawRequest} clearDrawRequest={() => setDrawRequest(null)}
            openStandEditor={openStandEditor} onEditFeature={editFeature} onDeleteFeature={deleteFeature} />
        )}
        {view === "stands" && (
          <StandsPage stands={stands} onAdd={() => goDraw("stand")} onEdit={(s) => setEditingStand(s)}
            onDelete={async (id) => { await api(`/stands/${id}`, { method: "DELETE" }); loadStands(); }} />
        )}
        {view === "food" && (
          <ZonesPage kind="food" zones={zones.filter((z) => z.kind === "food")} onAdd={() => goDraw("food")}
            reload={loadZones} editing={editingZone} setEditing={setEditingZone} />
        )}
        {view === "bedding" && (
          <ZonesPage kind="bedding" zones={zones.filter((z) => z.kind === "bedding")} onAdd={() => goDraw("bedding")}
            reload={loadZones} editing={editingZone} setEditing={setEditingZone} />
        )}
        {view === "corridors" && (
          <CorridorsPage corridors={corridors} onAdd={() => goDraw("corridor")} reload={loadCorridors}
            editing={editingCorridor} setEditing={setEditingCorridor} />
        )}
        {view === "settings" && <SettingsPage />}
      </main>

      {editingStand && (
        <Modal onClose={() => setEditingStand(null)}>
          <StandEditor stand={editingStand} onSave={saveStand} onCancel={() => setEditingStand(null)} reload={loadStands} />
        </Modal>
      )}
    </div>
  );
}

/* ════════════════════════════════════════════════════ MAP PAGE ════ */
function MapPage({ stands, zones, corridors, reloadStands, reloadZones, reloadCorridors, drawRequest, clearDrawRequest, openStandEditor, onEditFeature, onDeleteFeature }) {
  const [days, setDays] = useState([]);
  const [dayIdx, setDayIdx] = useState(0);
  const [hourPos, setHourPos] = useState(0);
  const [conditions, setConditions] = useState(null);
  const [dayRanked, setDayRanked] = useState(null);
  const [err, setErr] = useState(null);
  const [drawMode, setDrawMode] = useState(null);
  const [draftPoints, setDraftPoints] = useState([]);
  const [layers, setLayers] = useState({ wind: true, thermal: true, deer: true, zones: true, corridors: true });
  const [pendingName, setPendingName] = useState(null);
  const [useProx, setUseProx] = useState({ corridor: true, food: true, bedding: true });
  const [deerRatings, setDeerRatings] = useState(null);
  const [home, setHome] = useState(null);

  useEffect(() => { api("/home").then(setHome).catch(() => setHome({ set: false })); }, []);

  useEffect(() => {
    if (!stands.length) return;
    api("/deer-ratings").then((j) => setDeerRatings(j.ratings)).catch(() => {});
  }, [stands.length]);

  useEffect(() => { if (drawRequest) { setDrawMode(drawRequest); setDraftPoints([]); clearDrawRequest(); } }, [drawRequest, clearDrawRequest]);

  useEffect(() => {
    if (!stands.length) return;
    api("/hours").then((j) => {
      setDays(j.days || []);
      const now = new Date();
      let di = 0, hp = 0;
      for (let d = 0; d < j.days.length; d++) {
        const hi = j.days[d].hours.findIndex((h) => h.hour === now.getHours());
        if (j.days[d].day === now.toISOString().slice(0, 10) && hi >= 0) { di = d; hp = hi; break; }
      }
      setDayIdx(di); setHourPos(hp);
    }).catch(() => setErr("Couldn't load forecast hours."));
  }, [stands.length]);

  const curDay = days[dayIdx];
  const curHour = curDay?.hours[Math.min(hourPos, (curDay?.hours.length || 1) - 1)];

  useEffect(() => {
    if (!curHour) return;
    let cancel = false;
    api("/map/conditions", { method: "POST", body: JSON.stringify({ time_index: curHour.index }) })
      .then((j) => { if (cancel) return; setConditions(j); })
      .catch((e) => setErr(String(e.message)));
    return () => { cancel = true; };
  }, [curHour?.index]);

  useEffect(() => {
    if (!curDay) return;
    let cancel = false;
    api("/day/ranked", { method: "POST", body: JSON.stringify({ day: curDay.day, use_corridor: useProx.corridor, use_food: useProx.food, use_bedding: useProx.bedding }) })
      .then((j) => { if (cancel) return; setDayRanked(j); })
      .catch((e) => setErr(String(e.message)));
    return () => { cancel = true; };
  }, [curDay?.day, useProx.corridor, useProx.food, useProx.bedding]);

  const onMapClick = useCallback(async (pt) => {
    if (drawMode === "stand") { setDrawMode(null); openStandEditor(pt); }
    else if (drawMode === "food" || drawMode === "bedding") {
      setDrawMode(null);
      setPendingName({ type: "zone", kind: drawMode, title: `Name this ${drawMode} zone`,
                       payload: { kind: drawMode, lat: pt.lat, lon: pt.lon, radius_m: 80 } });
    } else if (drawMode === "corridor") {
      setDraftPoints((p) => [...p, pt]);
    }
  }, [drawMode, openStandEditor]);

  function finishCorridor() {
    if (draftPoints.length >= 2) {
      setPendingName({ type: "corridor", title: "Name this corridor",
                       payload: { points: draftPoints.map((p) => [p.lat, p.lon]) } });
    }
    setDraftPoints([]); setDrawMode(null);
  }

  async function confirmName(name) {
    const pn = pendingName;
    setPendingName(null);
    if (!pn) return;
    try {
      if (pn.type === "zone") { await api("/zones", { method: "POST", body: JSON.stringify({ ...pn.payload, name: name || null }) }); await reloadZones(); }
      else { await api("/corridors", { method: "POST", body: JSON.stringify({ ...pn.payload, name: name || null }) }); await reloadCorridors(); }
    } catch { setErr(`Couldn't save ${pn.type}.`); }
  }
  function cancelDraw() { setDraftPoints([]); setDrawMode(null); }
  const toggle = (k) => setLayers((l) => ({ ...l, [k]: !l[k] }));

  if (!stands.length) {
    if (drawMode) {
      if (home && !home.set) {
        return (
          <div>
            <PageHead title="Hunt Planner" />
            <HomeSetup onSaved={(h) => setHome(h)} onCancel={cancelDraw} />
          </div>
        );
      }
      return (
        <div>
          <PageHead title="Hunt Planner" />
          {err && <Banner>{err}</Banner>}
          <div className="panel">
            <div className="panel-head">
              <div>
                <strong>Place your {drawMode === "stand" ? "stand" : drawMode === "corridor" ? "corridor" : drawMode + " zone"}</strong>
                <div style={{ fontSize: 11.5, color: "var(--sub)", marginTop: 2 }}>Click the map to drop it. The forecast and rankings appear once you have a stand.</div>
              </div>
            </div>
            <div style={{ fontSize: 13, color: "var(--amber)", marginBottom: 8, display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
              {drawMode === "stand" && <>Click the map to place the stand.</>}
              {(drawMode === "food" || drawMode === "bedding") && <>Click the map to drop the {drawMode} zone.</>}
              {drawMode === "corridor" && <>Click points along the deer path ({draftPoints.length} set).</>}
              {drawMode === "corridor" && <button className="btn" onClick={finishCorridor} disabled={draftPoints.length < 2}>Finish</button>}
              <button className="btn" onClick={cancelDraw}>Cancel</button>
            </div>
            <HuntMap stands={stands} zones={zones} corridors={corridors} conditions={null}
              drawMode={drawMode} onMapClick={onMapClick} draftPoints={draftPoints} layers={layers}
              onEditFeature={onEditFeature} onDeleteFeature={onDeleteFeature} center={home} />
          </div>
          {pendingName && (
            <NamePrompt title={pendingName.title} onCancel={() => setPendingName(null)} onConfirm={confirmName} />
          )}
        </div>
      );
    }
    return <div><PageHead title="Hunt Planner" /><Empty>Add a stand first (Stand locations → Add) to see the map and rankings.</Empty></div>;
  }

  const selectedRating = deerRatings?.find((r) => r.day === curDay?.day);

  return (
    <div>
      {err && <Banner>{err}</Banner>}

      {/* ── 14-Day Weather-Style Forecast Carousel ── */}
      {deerRatings && deerRatings.length > 0 && (
        <ForecastCarousel
          ratings={deerRatings}
          selectedDay={curDay?.day}
          loadableDays={days.map((d) => d.day)}
          onPick={(day) => { const i = days.findIndex((d) => d.day === day); if (i >= 0) { setDayIdx(i); setHourPos(0); } }}
        />
      )}

      {/* ── Selected Day Intelligence Header ── */}
      {selectedRating && curDay && (
        <DayIntelligenceHeader rating={selectedRating} curDay={curDay} />
      )}

      {/* ── Main split layout: Map + Leaderboard ── */}
      <div className="hunt-split">

        {/* Left: Map + Hourly Scrubber */}
        <div className="hunt-map-col">
          <div className="panel" style={{ padding: 0, overflow: "hidden" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "12px 14px", borderBottom: "1px solid var(--bord)" }}>
              <div>
                <strong style={{ fontSize: 14.5 }}>{curDay?.label || "Map"}</strong>
                {curDay?.confidence === "low" && (
                  <span style={{ fontSize: 11, color: "var(--amber)", marginLeft: 8, display: "inline-flex", alignItems: "center", gap: 4 }}>
                    <AlertTriangle size={11} /> est.
                  </span>
                )}
              </div>
              <AddMenu drawMode={drawMode} setDrawMode={(m) => { setDraftPoints([]); setDrawMode(m); }} />
            </div>

            {curDay && curHour && (
              <div style={{ padding: "10px 14px", borderBottom: "1px solid var(--bord)", background: "var(--tert)" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
                  <span style={{ fontWeight: 600, fontSize: 13 }}>{curHour.label}</span>
                  <span style={{ fontSize: 11.5, color: "var(--sub)" }}>
                    ☀ {curDay.sunrise} · ☾ {curDay.sunset}
                    {conditions && <> · {Math.round(conditions.time.temp)}°F · {conditions.time.cloud}% cloud</>}
                  </span>
                </div>
                <input type="range" min={0} max={curDay.hours.length - 1} value={Math.min(hourPos, curDay.hours.length - 1)}
                  onChange={(e) => setHourPos(+e.target.value)} style={{ width: "100%" }} />
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, color: "var(--sub)", marginTop: 2 }}>
                  <span>{curDay.hours[0]?.label}</span><span>{curDay.hours[curDay.hours.length - 1]?.label}</span>
                </div>
              </div>
            )}

            {drawMode && (
              <div style={{ padding: "8px 14px", background: "rgba(133,79,11,.08)", borderBottom: "1px solid var(--bord)", fontSize: 13, color: "var(--amber)", display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                {drawMode === "stand" && <>Click the map to place the stand.</>}
                {(drawMode === "food" || drawMode === "bedding") && <>Click the map to drop the {drawMode} zone.</>}
                {drawMode === "corridor" && <>Click points along the deer path ({draftPoints.length} set).</>}
                {drawMode === "corridor" && <button className="btn" onClick={finishCorridor} disabled={draftPoints.length < 2}>Finish</button>}
                <button className="btn" onClick={cancelDraw}>Cancel</button>
              </div>
            )}

            <div style={{ position: "relative" }}>
              <HuntMap stands={stands} zones={zones} corridors={corridors} conditions={conditions}
                drawMode={drawMode} onMapClick={onMapClick} draftPoints={draftPoints} layers={layers}
                onEditFeature={onEditFeature} onDeleteFeature={onDeleteFeature} center={home} />
              <div className="layer-overlay">
                <LayerChip on={layers.wind} onClick={() => toggle("wind")} color="var(--navy)" label="Wind" />
                <LayerChip on={layers.thermal} onClick={() => toggle("thermal")} color="#185FA5" dashed label="Thermal" />
                <LayerChip on={layers.deer} onClick={() => toggle("deer")} color="#A35A1B" label="Deer" />
                <LayerChip on={layers.corridors} onClick={() => toggle("corridors")} color="#A35A1B" label="Corridors" />
                <LayerChip on={layers.zones} onClick={() => toggle("zones")} color="#6B4FA0" label="Zones" />
              </div>
            </div>
          </div>
        </div>

        {/* Right: Stand Leaderboard */}
        <div className="hunt-rank-col">
          {dayRanked && dayRanked.ranked.length > 0 ? (
            <StandLeaderboard ranked={dayRanked} useProx={useProx} setUseProx={setUseProx} />
          ) : (
            <div className="panel" style={{ display: "flex", alignItems: "center", justifyContent: "center", minHeight: 200 }}>
              <Empty>Loading stand rankings…</Empty>
            </div>
          )}
        </div>
      </div>

      {pendingName && (
        <NamePrompt title={pendingName.title} onCancel={() => setPendingName(null)} onConfirm={confirmName} />
      )}
    </div>
  );
}

/* ════════════════ WEATHER-STYLE FORECAST CAROUSEL ════════════════ */
function ForecastCarousel({ ratings, selectedDay, onPick, loadableDays }) {
  const scrollRef = React.useRef(null);

  React.useEffect(() => {
    if (!scrollRef.current) return;
    const sel = scrollRef.current.querySelector(".fc-card.selected");
    if (sel) sel.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "center" });
  }, [selectedDay]);

  const weatherIcon = (r) => {
    const rain = r.inputs?.rain_mm ?? 0;
    const wind = r.inputs?.wind_mph ?? 0;
    if (rain > 5) return "🌧️";
    if (rain > 1) return "🌦️";
    if (wind > 15) return "💨";
    if (r.rating >= 4) return "☀️";
    if (r.rating === 3) return "⛅";
    return "🌥️";
  };

  const movementColor = (rating) => {
    if (rating >= 4) return { bg: "rgba(59,109,17,.12)", border: "rgba(59,109,17,.35)", text: "var(--green)" };
    if (rating === 3) return { bg: "rgba(133,79,11,.1)", border: "rgba(133,79,11,.3)", text: "var(--amber)" };
    return { bg: "rgba(163,45,45,.08)", border: "rgba(163,45,45,.25)", text: "var(--red)" };
  };

  return (
    <div style={{ marginBottom: 16 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
        <h2 style={{ fontSize: 12, fontWeight: 600, color: "var(--sub)", textTransform: "uppercase", letterSpacing: 0.8 }}>
          14-Day Movement Outlook
        </h2>
        <span style={{ fontSize: 11, color: "var(--sub)" }}>← scroll →</span>
      </div>
      <div ref={scrollRef} className="fc-strip">
        {ratings.map((r) => {
          const sel = r.day === selectedDay;
          const loadable = loadableDays.includes(r.day);
          const low = r.confidence === "low";
          const col = movementColor(r.rating);
          return (
            <button key={r.day}
              className={"fc-card" + (sel ? " selected" : "")}
              onClick={() => loadable && onPick(r.day)}
              disabled={!loadable}
              title={`${r.rating}/5 · ${r.rut?.phase || ""}${low ? " · est." : ""}`}
              style={{
                borderColor: sel ? "var(--navy)" : col.border,
                background: sel ? "var(--navy)" : col.bg,
              }}
            >
              <div className="fc-day" style={{ color: sel ? "rgba(255,255,255,0.75)" : "var(--sub)" }}>
                {r.label}
              </div>
              <div className="fc-icon">{weatherIcon(r)}</div>
              <div className="fc-rating-pill" style={{
                background: sel ? "rgba(255,255,255,0.18)" : "transparent",
                color: sel ? "#fff" : col.text,
                border: `1px solid ${sel ? "rgba(255,255,255,0.3)" : col.border}`,
              }}>
                {"🦌".repeat(r.rating)}<span style={{ fontSize: 10, marginLeft: 3 }}>{r.rating}/5</span>
              </div>
              {r.inputs?.pressure_inhg != null && (
                <div className="fc-meta" style={{ color: sel ? "rgba(255,255,255,0.65)" : "var(--sub)" }}>
                  {r.inputs.pressure_inhg}″
                  {(r.inputs.pressure_trend_inhg ?? 0) > 0.005 ? " ↑" : (r.inputs.pressure_trend_inhg ?? 0) < -0.005 ? " ↓" : ""}
                </div>
              )}
              {r.inputs?.wind_mph != null && (
                <div className="fc-meta" style={{ color: sel ? "rgba(255,255,255,0.65)" : "var(--sub)" }}>
                  💨 {r.inputs.wind_mph}mph
                </div>
              )}
              {low && <div className="fc-est" style={{ color: sel ? "rgba(255,255,255,0.45)" : "var(--sub)" }}>est.</div>}
            </button>
          );
        })}
      </div>
      <div style={{ fontSize: 11, color: "var(--sub)", marginTop: 6 }}>
        Tap any day to load its map and stand rankings. Days marked "est." are 8+ days out — weather confidence softens past ~7 days.
      </div>
    </div>
  );
}

/* ════════════════ DAY INTELLIGENCE HEADER ════════════════ */
function DayIntelligenceHeader({ rating, curDay }) {
  const [open, setOpen] = useState(false);
  const r = rating.rating;
  const fac = rating.factors || {};
  const inp = rating.inputs || {};
  const tone = r >= 4 ? "var(--green)" : r === 3 ? "var(--amber)" : "var(--red)";
  const phase = rating.rut?.phase || "";
  const rutInt = rating.rut?.intensity ?? null;

  return (
    <div className="day-intel-header" style={{ borderLeft: `4px solid ${tone}` }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: 10 }}>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
            <span style={{ fontSize: 22, letterSpacing: 1 }}>{"🦌".repeat(r)}{"·".repeat(5 - r)}</span>
            <span style={{ fontWeight: 700, fontSize: 17, color: tone }}>{r}/5 Movement</span>
            <span style={{ fontSize: 12, fontWeight: 600, background: tone + "22", color: tone, border: `1px solid ${tone}55`, padding: "2px 9px", borderRadius: 12 }}>
              {phase}
            </span>
          </div>
          <div style={{ fontSize: 12.5, color: "var(--sub)", marginTop: 4 }}>{curDay.label}</div>
        </div>
        <button className="btn" style={{ fontSize: 12, padding: "5px 10px" }} onClick={() => setOpen((o) => !o)}>
          {open ? "▲ Less" : "▼ Details"}
        </button>
      </div>

      <div className="intel-cards">
        {rutInt != null && (
          <div className="intel-card" style={{ borderLeft: `3px solid ${tone}` }}>
            <div className="intel-icon">🦌</div>
            <div>
              <div className="intel-label">Rut Intensity</div>
              <div className="intel-value" style={{ color: tone }}>{Math.round(rutInt * 100)}%</div>
              <div className="intel-sub">{phase}</div>
            </div>
          </div>
        )}
        {inp.pressure_inhg != null && (
          <div className="intel-card">
            <div className="intel-icon">📈</div>
            <div>
              <div className="intel-label">Barometric</div>
              <div className="intel-value">{inp.pressure_inhg}″</div>
              <div className="intel-sub">{(inp.pressure_trend_inhg ?? 0) > 0.005 ? "Rising ↑" : (inp.pressure_trend_inhg ?? 0) < -0.005 ? "Falling ↓" : "Steady"}</div>
            </div>
          </div>
        )}
        {inp.wind_mph != null && (
          <div className="intel-card">
            <div className="intel-icon">💨</div>
            <div>
              <div className="intel-label">Wind Speed</div>
              <div className="intel-value">{inp.wind_mph} mph</div>
              <div className="intel-sub">{inp.wind_mph < 5 ? "Calm — scent pools" : inp.wind_mph > 15 ? "Strong — movement drops" : "Good scenting"}</div>
            </div>
          </div>
        )}
        {inp.day_high_f != null && (
          <div className="intel-card">
            <div className="intel-icon">🌡️</div>
            <div>
              <div className="intel-label">High Temp</div>
              <div className="intel-value">{inp.day_high_f}°F</div>
              {inp.baseline_f != null && <div className="intel-sub">{inp.day_high_f < inp.baseline_f ? "↓" : "↑"} {Math.abs(inp.day_high_f - inp.baseline_f)}° vs avg</div>}
            </div>
          </div>
        )}
        {inp.rain_mm != null && (
          <div className="intel-card">
            <div className="intel-icon">🌧️</div>
            <div>
              <div className="intel-label">Precipitation</div>
              <div className="intel-value">{inp.rain_mm === 0 ? "Dry" : `${inp.rain_mm} mm`}</div>
              <div className="intel-sub">{inp.rain_mm > 5 ? "Movement suppressed" : inp.rain_mm > 0 ? "Light rain — OK" : "Optimal"}</div>
            </div>
          </div>
        )}
      </div>

      {open && (
        <div style={{ marginTop: 10, paddingTop: 10, borderTop: "1px solid var(--bord)" }}>
          <div style={{ fontSize: 11.5, fontWeight: 600, color: "var(--sub)", marginBottom: 8, textTransform: "uppercase", letterSpacing: 0.6 }}>Factor Breakdown</div>
          {Object.entries(fac).map(([k, v]) => {
            const pct = Math.round((v ?? 0) * 100);
            const label = { pressure: "Barometric Pressure", wind: "Wind", rain: "Rain (1=dry)", temp_shift: "Temperature Shift" }[k] || k;
            return (
              <div key={k} style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 5 }}>
                <span style={{ fontSize: 12, color: "var(--sub)", width: 150, flexShrink: 0 }}>{label}</span>
                <span style={{ flex: 1, height: 6, background: "var(--surf)", borderRadius: 3, overflow: "hidden" }}>
                  <span style={{ display: "block", height: "100%", width: `${pct}%`, background: tone, borderRadius: 3 }} />
                </span>
                <span style={{ fontSize: 11, color: "var(--sub)", width: 28, textAlign: "right" }}>{pct}</span>
              </div>
            );
          })}
          {Array.isArray(rating.breakdown) && (
            <div style={{ marginTop: 8, display: "grid", gap: 3 }}>
              {rating.breakdown.map((b, i) => (
                <div key={i} style={{ fontSize: 11, color: "var(--sub)", display: "flex", gap: 8 }}>
                  <b style={{ color: "var(--txt)", fontWeight: 500 }}>{b.factor}:</b> {b.impact}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/* ════════════════ STAND LEADERBOARD ════════════════ */
const PERIOD_COLORS = { morning: "#C28800", midday: "#1E7FB0", evening: "#7A3FA0" };
const PERIOD_LABEL = { morning: "🌅 Morning", midday: "☀️ Midday", evening: "🌇 Evening" };

function StandLeaderboard({ ranked, useProx, setUseProx }) {
  const [activePeriod, setActivePeriod] = useState("morning");
  return (
    <div className="panel" style={{ padding: 0, overflow: "hidden" }}>
      <div style={{ padding: "12px 14px", borderBottom: "1px solid var(--bord)" }}>
        <strong style={{ fontSize: 14.5 }}>Stand Rankings — {ranked.day_label}</strong>
      </div>
      <div style={{ display: "flex", borderBottom: "1px solid var(--bord)", background: "var(--tert)" }}>
        {["morning", "midday", "evening"].map((p) => (
          <button key={p} onClick={() => setActivePeriod(p)}
            style={{
              flex: 1, padding: "9px 6px", border: "none", cursor: "pointer", fontSize: 12.5,
              fontWeight: activePeriod === p ? 600 : 400,
              background: activePeriod === p ? "var(--bg)" : "transparent",
              color: activePeriod === p ? PERIOD_COLORS[p] : "var(--sub)",
              borderBottom: activePeriod === p ? `2px solid ${PERIOD_COLORS[p]}` : "2px solid transparent",
            }}>
            {PERIOD_LABEL[p]}
          </button>
        ))}
      </div>
      <div style={{ padding: "8px 12px", borderBottom: "1px solid var(--bord)", display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
        <span style={{ fontSize: 11, color: "var(--sub)" }}>Proximity:</span>
        <ProxToggle on={useProx.corridor} color="#A35A1B" label="Corridors" icon={Footprints} onClick={() => setUseProx((u) => ({ ...u, corridor: !u.corridor }))} />
        <ProxToggle on={useProx.food} color="var(--green)" label="Food" icon={Wheat} onClick={() => setUseProx((u) => ({ ...u, food: !u.food }))} />
        <ProxToggle on={useProx.bedding} color="#6B4FA0" label="Bedding" icon={Trees} onClick={() => setUseProx((u) => ({ ...u, bedding: !u.bedding }))} />
      </div>
      <div style={{ padding: "10px 12px", display: "grid", gap: 8 }}>
        {ranked.ranked.map((row, idx) => (
          <LeaderboardCard key={row.stand.id} row={row} period={activePeriod} rank={idx} isWinner={row.wins.includes(activePeriod)} />
        ))}
      </div>
    </div>
  );
}

/* ════════════════ LEADERBOARD CARD ════════════════ */
function LeaderboardCard({ row, period, rank, isWinner }) {
  const [open, setOpen] = useState(false);
  const { stand, periods, proximity } = row;
  const sc = periods[period]?.score;
  const hour = periods[period]?.hour;
  const score = sc ? Math.round(sc.total * 100) : null;
  const proxTotal = proximity ? Math.round(proximity.total * 100) : 0;
  const camBoost = sc?.camera?.boost_pct > 0 ? sc.camera.boost_pct : 0;
  return (
    <div style={{
      border: isWinner ? `2px solid ${PERIOD_COLORS[period]}` : "1px solid var(--bord)",
      borderRadius: 10, background: "var(--bg)", overflow: "hidden",
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "10px 12px", flexWrap: "wrap" }}>
        <span style={{ fontSize: 13, fontWeight: 700, color: "var(--sub)", minWidth: 22 }}>
          {isWinner ? "★" : `${rank + 1}.`}
        </span>
        <span style={{ fontWeight: 600, flex: 1 }}>{stand.name}</span>
        {score != null && (
          <span style={{
            fontSize: 12.5, fontWeight: 700,
            color: score >= 70 ? "var(--green)" : score >= 45 ? "var(--amber)" : "var(--red)",
            background: score >= 70 ? "rgba(59,109,17,.12)" : score >= 45 ? "rgba(133,79,11,.12)" : "rgba(163,45,45,.1)",
            padding: "2px 8px", borderRadius: 6,
          }}>{score}</span>
        )}
        {isWinner && (
          <span style={{ fontSize: 11, fontWeight: 600, color: "#fff", background: PERIOD_COLORS[period], padding: "2px 8px", borderRadius: 6 }}>
            Best {period}
          </span>
        )}
        <button className="icon-btn" style={{ padding: 4 }} onClick={() => setOpen((o) => !o)}>
          <ChevronDown size={14} style={{ transform: open ? "rotate(180deg)" : "none", transition: "transform .15s" }} />
        </button>
      </div>
      {sc && hour && (
        <div style={{ padding: "6px 12px 10px", display: "flex", gap: 10, flexWrap: "wrap", fontSize: 12, color: "var(--sub)", borderTop: "1px solid var(--bord)" }}>
          <span>💨 {degToCompass(hour.wind_dir)} {Math.round(hour.wind_speed)}mph</span>
          <span>🌡️ thermals {sc.thermal_phase}</span>
          {stand.deer_approach_deg != null && (
            <span style={{ color: sc.scent_score > 0.6 ? "var(--green)" : sc.scent_score > 0.35 ? "var(--amber)" : "var(--red)" }}>
              {sc.scent_score > 0.6 ? "✓ Scent away" : sc.scent_score > 0.35 ? "~ Scent crosses" : "✗ Scent toward deer"}
            </span>
          )}
          {proxTotal > 0 && <span style={{ color: "var(--green)" }}>+{proxTotal} prox</span>}
          {camBoost > 0 && <span style={{ color: "var(--navy)", display: "inline-flex", alignItems: "center", gap: 3 }}><Camera size={10} /> +{camBoost}%</span>}
        </div>
      )}
      {open && (
        <div style={{ padding: "10px 12px", background: "var(--tert)", borderTop: "1px solid var(--bord)" }}>
          {(() => {
            const bd = periods[period]?.score?.breakdown;
            if (!bd) return <div style={{ fontSize: 12, color: "var(--sub)" }}>No breakdown available.</div>;
            return (
              <div>
                <div style={{ display: "grid", gap: 4 }}>
                  {bd.map((b, i) => (
                    <div key={i} style={{ fontSize: 11.5, display: "flex", gap: 8, color: "var(--sub)" }}>
                      <b style={{ color: "var(--txt)", fontWeight: 500, minWidth: 90 }}>{b.factor}:</b>
                      <span>{b.text}</span>
                    </div>
                  ))}
                </div>
                {proximity && (
                  <div style={{ marginTop: 8, fontSize: 11, color: "var(--sub)", display: "flex", gap: 10, flexWrap: "wrap" }}>
                    <span>Corridor +{Math.round(proximity.corridor * 100)}</span>
                    <span>Food +{Math.round(proximity.food * 100)}</span>
                    <span>Bedding +{Math.round(proximity.bedding * 100)}</span>
                  </div>
                )}
              </div>
            );
          })()}
        </div>
      )}
    </div>
  );
}

/* ───────── collapsed Add menu ───────── */
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
    { m: "stand", label: "Stand", icon: MapPin, color: "var(--navy)" },
    { m: "food", label: "Food zone", icon: Wheat, color: "var(--green)" },
    { m: "bedding", label: "Bedding zone", icon: Trees, color: "#6B4FA0" },
    { m: "corridor", label: "Deer corridor", icon: Footprints, color: "#A35A1B" },
  ];
  return (
    <div style={{ position: "relative" }} ref={ref}>
      <button className="btn btn-primary" onClick={() => setOpen((o) => !o)} disabled={!!drawMode}>
        <Plus size={15} /> Add
      </button>
      {open && (
        <div style={{ position: "absolute", right: 0, top: "calc(100% + 4px)", background: "var(--bg)", border: "1px solid var(--bord2)", borderRadius: 10, padding: 6, zIndex: 3000, minWidth: 168, boxShadow: "0 6px 24px rgba(0,0,0,.18)" }}>
          {items.map(({ m, label, icon: Icon, color }) => (
            <button key={m} className="menu-item" onClick={() => pick(m)}>
              <Icon size={15} color={color} /> {label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/* ───────── name prompt modal ───────── */
function NamePrompt({ title, onConfirm, onCancel }) {
  const [name, setName] = useState("");
  return (
    <Modal onClose={onCancel}>
      <div className="card" style={{ padding: 16, border: "2px solid var(--navy)" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
          <strong>{title}</strong>
          <button className="icon-btn" onClick={onCancel}><X size={16} /></button>
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

/* ════════════════════════════════════════════════════ LIST PAGES ════ */
function StandsPage({ stands, onAdd, onEdit, onDelete }) {
  return (
    <div>
      <PageHead title="Stand locations" action={<button className="btn" onClick={onAdd}><Plus size={15} /> Add stand</button>} />
      {!stands.length && <Empty>No stands yet. Click “Add stand” to place one on the map.</Empty>}
      <div style={{ display: "grid", gap: 8 }}>
        {stands.map((s) => (
          <div key={s.id} className="card" style={{ padding: 12, display: "flex", alignItems: "center", gap: 12 }}>
            <div style={{ width: 150, flexShrink: 0 }}>
              <MiniMap kind="stand" feature={{ lat: s.lat, lon: s.lon }} height={110} />
            </div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontWeight: 500 }}>{s.name || "Unnamed stand"}</div>
              <div style={{ fontSize: 12, color: "var(--sub)" }}>
                {(+s.lat).toFixed(4)}, {(+s.lon).toFixed(4)}
                {s.terrain && <> · {s.terrain.elevation}m · drains {degToCompass(s.terrain.drainage_deg)}</>}
                {!s.terrain && s.downhill_deg != null && <> · downhill {degToCompass(s.downhill_deg)}</>}
                {s.deer_approach_deg != null && <> · deer from {degToCompass(s.deer_approach_deg)}</>}
              </div>
            </div>
            {s.terrain && <Mountain size={13} color="var(--green)" title={"terrain: " + s.terrain.source} />}
            <button className="icon-btn" onClick={() => onEdit(s)}><Edit3 size={15} /></button>
            <button className="icon-btn" onClick={() => onDelete(s.id)}><Trash2 size={15} /></button>
          </div>
        ))}
      </div>
    </div>
  );
}

function ZonesPage({ kind, zones, onAdd, reload, editing, setEditing }) {
  const Icon = kind === "food" ? Wheat : Trees;
  const title = kind === "food" ? "Food zones" : "Bedding zones";
  const [geom, setGeom] = useState(null);

  // sync local editable geometry when an item is opened
  useEffect(() => { if (editing) setGeom({ lat: editing.lat, lon: editing.lon, radius_m: editing.radius_m }); }, [editing && editing.id]);

  async function save() {
    const g = geom || { lat: editing.lat, lon: editing.lon, radius_m: editing.radius_m };
    try {
      await api(`/zones/${editing.id}`, { method: "PUT", body: JSON.stringify({ kind, name: editing.name || null, lat: g.lat, lon: g.lon, radius_m: +g.radius_m }) });
      setEditing(null); reload();
    } catch {}
  }

  return (
    <div>
      <PageHead title={title} action={<button className="btn" onClick={onAdd}><Plus size={15} /> Add {kind === "food" ? "food zone" : "bedding zone"}</button>} />
      {!zones.length && <Empty>No {title.toLowerCase()} yet. Click “Add” to draw one on the map.</Empty>}
      <div style={{ display: "grid", gap: 8 }}>
        {zones.map((z) => (
          <div key={z.id} className="card" style={{ padding: 12, display: "flex", alignItems: "center", gap: 12 }}>
            <div style={{ width: 150, flexShrink: 0 }}>
              <MiniMap kind={kind} feature={{ lat: z.lat, lon: z.lon, radius_m: z.radius_m }} height={110} />
            </div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontWeight: 500 }}>{z.name || `Unnamed ${kind} zone`}</div>
              <div style={{ fontSize: 12, color: "var(--sub)" }}>{(+z.lat).toFixed(4)}, {(+z.lon).toFixed(4)} · {z.radius_m} m radius</div>
            </div>
            <button className="icon-btn" onClick={() => setEditing({ ...z })}><Edit3 size={15} /></button>
            <button className="icon-btn" onClick={async () => { await api(`/zones/${z.id}`, { method: "DELETE" }); reload(); }}><Trash2 size={15} /></button>
          </div>
        ))}
      </div>

      {editing && (
        <Modal onClose={() => setEditing(null)}>
          <div className="card" style={{ padding: 16, border: "2px solid var(--navy)" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
              <strong>Edit {kind} zone</strong>
              <button className="icon-btn" onClick={() => setEditing(null)}><X size={16} /></button>
            </div>
            <Field label="Name"><input value={editing.name || ""} onChange={(e) => setEditing({ ...editing, name: e.target.value })} placeholder={`${kind} zone`} /></Field>
            <div style={{ marginTop: 12 }}>
              <span style={{ fontSize: 12, color: "var(--sub)", display: "block", marginBottom: 4 }}>Drag the center to move · drag the edge to resize</span>
              <MiniMap kind={kind} editable height={240}
                feature={{ lat: editing.lat, lon: editing.lon, radius_m: editing.radius_m }}
                onChange={(g) => setGeom(g)} />
            </div>
            {geom && <div style={{ fontSize: 12, color: "var(--sub)", marginTop: 6 }}>{geom.lat.toFixed(5)}, {geom.lon.toFixed(5)} · {Math.round(geom.radius_m)} m radius</div>}
            <div style={{ display: "flex", gap: 8, marginTop: 16 }}>
              <button className="btn btn-primary" onClick={save}><Save size={15} /> Save</button>
              <button className="btn" onClick={() => setEditing(null)}>Cancel</button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}

function CorridorsPage({ corridors, onAdd, reload, editing, setEditing }) {
  const [geom, setGeom] = useState(null);
  useEffect(() => { if (editing) setGeom({ points: editing.points }); }, [editing && editing.id]);

  async function save() {
    const pts = (geom && geom.points) || editing.points;
    try {
      await api(`/corridors/${editing.id}`, { method: "PUT", body: JSON.stringify({ name: editing.name || null, points: pts }) });
      setEditing(null); reload();
    } catch {}
  }
  return (
    <div>
      <PageHead title="Corridors" action={<button className="btn" onClick={onAdd}><Plus size={15} /> Add corridor</button>} />
      {!corridors.length && <Empty>No corridors yet. Click “Add corridor” to trace one on the map.</Empty>}
      <div style={{ display: "grid", gap: 8 }}>
        {corridors.map((c) => (
          <div key={c.id} className="card" style={{ padding: 12, display: "flex", alignItems: "center", gap: 12 }}>
            <div style={{ width: 150, flexShrink: 0 }}>
              <MiniMap kind="corridor" feature={{ points: c.points }} height={110} />
            </div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontWeight: 500 }}>{c.name || "Unnamed corridor"}</div>
              <div style={{ fontSize: 12, color: "var(--sub)" }}>{c.points.length} points</div>
            </div>
            <button className="icon-btn" onClick={() => setEditing({ ...c })}><Edit3 size={15} /></button>
            <button className="icon-btn" onClick={async () => { await api(`/corridors/${c.id}`, { method: "DELETE" }); reload(); }}><Trash2 size={15} /></button>
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
            <div style={{ marginTop: 12 }}>
              <span style={{ fontSize: 12, color: "var(--sub)", display: "block", marginBottom: 4 }}>Drag any point to reshape the path</span>
              <MiniMap kind="corridor" editable height={240} feature={{ points: editing.points }} onChange={(g) => setGeom(g)} />
            </div>
            <div style={{ fontSize: 12, color: "var(--sub)", marginTop: 6 }}>{((geom && geom.points) || editing.points).length} points</div>
            <div style={{ display: "flex", gap: 8, marginTop: 16 }}>
              <button className="btn btn-primary" onClick={save}><Save size={15} /> Save</button>
              <button className="btn" onClick={() => setEditing(null)}>Cancel</button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}

/* ───────── first-run hunt region setup ───────── */
function HomeSetup({ onSaved, onCancel }) {
  const [lat, setLat] = useState("");
  const [lon, setLon] = useState("");
  const [err, setErr] = useState(null);
  const valid = lat !== "" && lon !== "" && !isNaN(+lat) && !isNaN(+lon) &&
    +lat >= -90 && +lat <= 90 && +lon >= -180 && +lon <= 180;
  async function save() {
    try { const h = await api("/home", { method: "PUT", body: JSON.stringify({ lat: +lat, lon: +lon }) }); onSaved(h); }
    catch { setErr("Couldn’t save. Check the values and try again."); }
  }
  return (
    <div className="panel" style={{ maxWidth: 460 }}>
      <div className="panel-head"><strong>Set your hunt region</strong></div>
      <p style={{ fontSize: 13.5, color: "var(--sub)", marginTop: 0 }}>
        Enter the latitude and longitude of your hunting area. This centers the map so you can place your first stand. You can change it later in Settings.
      </p>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
        <Field label="Latitude"><input value={lat} onChange={(e) => setLat(e.target.value)} placeholder="34.7465" inputMode="decimal" /></Field>
        <Field label="Longitude"><input value={lon} onChange={(e) => setLon(e.target.value)} placeholder="-92.2896" inputMode="decimal" /></Field>
      </div>
      <div style={{ fontSize: 11.5, color: "var(--sub)", marginTop: 8 }}>
        Tip: right-click your spot in Google Maps and the coordinates are at the top of the menu (latitude, then longitude).
      </div>
      {err && <div style={{ fontSize: 12.5, color: "var(--red)", marginTop: 8 }}>{err}</div>}
      <div style={{ display: "flex", gap: 8, marginTop: 16 }}>
        <button className="btn btn-primary" disabled={!valid} onClick={save}><Save size={15} /> Save region</button>
        <button className="btn" onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}

/* ───────── settings page ───────── */
function SettingsPage() {
  const [s, setS] = useState(null);
  const [saved, setSaved] = useState(false);
  const [err, setErr] = useState(null);
  const [home, setHome] = useState(null);
  const [homeLat, setHomeLat] = useState("");
  const [homeLon, setHomeLon] = useState("");
  const [homeSaved, setHomeSaved] = useState(false);
  const [tab, setTab] = useState("stand");  // stand | rating | cameras
  useEffect(() => { api("/settings").then(setS).catch(() => setErr("Couldn’t load settings.")); }, []);
  useEffect(() => { api("/home").then((h) => { setHome(h); if (h.set) { setHomeLat(String(h.lat)); setHomeLon(String(h.lon)); } }).catch(() => {}); }, []);

  async function save() {
    try { const r = await api("/settings", { method: "PUT", body: JSON.stringify(s) }); setS(r); setSaved(true); setTimeout(() => setSaved(false), 1800); }
    catch { setErr("Couldn’t save settings."); }
  }
  function reset() { setS({ ...s, weight_corridor: 0.15, falloff_corridor: 150, weight_food: 0.15, falloff_food: 200, weight_bedding: 0.10, falloff_bedding: 250, max_camera_boost_pct: 15.0 }); }
  function resetRating() { setS({ ...s, rate_w_pressure: 0.32, rate_w_wind: 0.20, rate_w_rain: 0.28, rate_w_temp: 0.20, rut_peak_month: 12, rut_peak_day: 5 }); }

  const homeValid = homeLat !== "" && homeLon !== "" && !isNaN(+homeLat) && !isNaN(+homeLon) &&
    +homeLat >= -90 && +homeLat <= 90 && +homeLon >= -180 && +homeLon <= 180;
  async function saveHome() {
    try { const h = await api("/home", { method: "PUT", body: JSON.stringify({ lat: +homeLat, lon: +homeLon }) }); setHome(h); setHomeSaved(true); setTimeout(() => setHomeSaved(false), 1800); }
    catch { setErr("Couldn’t save hunt region."); }
  }

  if (!s) return <div><PageHead title="Settings" />{err ? <Banner>{err}</Banner> : <Empty>Loading…</Empty>}</div>;

  const TYPES = [
    { key: "corridor", label: "Deer corridors", icon: Footprints, color: "#A35A1B" },
    { key: "food", label: "Food zones", icon: Wheat, color: "var(--green)" },
    { key: "bedding", label: "Bedding zones", icon: Trees, color: "#6B4FA0" },
  ];
  const TABS = [
    { key: "stand", label: "Best Stand" },
    { key: "rating", label: "Daily Rating" },
    { key: "cameras", label: "Trail Cameras" },
  ];
  const MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];

  return (
    <div style={{ maxWidth: 600 }}>
      <PageHead title="Settings" action={tab !== "cameras" ? <button className="btn btn-primary" onClick={save}><Save size={15} /> {saved ? "Saved" : "Save"}</button> : null} />
      {err && <Banner>{err}</Banner>}

      {/* sub-nav tabs */}
      <div style={{ display: "flex", gap: 4, marginBottom: 16, borderBottom: "1px solid var(--bord)" }}>
        {TABS.map((t) => (
          <button key={t.key} onClick={() => setTab(t.key)}
            style={{
              padding: "8px 14px", border: "none", background: "none", cursor: "pointer", fontSize: 14,
              color: tab === t.key ? "var(--navy)" : "var(--sub)", fontWeight: tab === t.key ? 600 : 400,
              borderBottom: tab === t.key ? "2px solid var(--navy)" : "2px solid transparent", marginBottom: -1,
            }}>{t.label}</button>
        ))}
      </div>

      {/* ── TAB: Best Stand ── */}
      {tab === "stand" && (<>
        <div className="card" style={{ padding: 14, marginBottom: 16 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
            <MapPin size={17} color="var(--navy)" /><strong style={{ fontSize: 14.5 }}>Hunt region</strong>
          </div>
          <p style={{ fontSize: 12.5, color: "var(--sub)", marginTop: 0, marginBottom: 10 }}>
            Centers the map before you’ve placed any stands. Once you have stands, the map fits to them automatically.
          </p>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr auto", gap: 10, alignItems: "end" }}>
            <Field label="Latitude"><input value={homeLat} onChange={(e) => setHomeLat(e.target.value)} placeholder="34.7465" inputMode="decimal" /></Field>
            <Field label="Longitude"><input value={homeLon} onChange={(e) => setHomeLon(e.target.value)} placeholder="-92.2896" inputMode="decimal" /></Field>
            <button className="btn btn-primary" disabled={!homeValid} onClick={saveHome} style={{ height: 38 }}><Save size={15} /> {homeSaved ? "Saved" : "Save"}</button>
          </div>
          {home && !home.set && <div style={{ fontSize: 11.5, color: "var(--amber)", marginTop: 8 }}>Not set yet — you’ll be asked for this when you add your first stand.</div>}
        </div>

        <h2 style={{ fontSize: 15, marginTop: 8, marginBottom: 6 }}>Proximity weights</h2>
        <p style={{ color: "var(--sub)", fontSize: 13.5, marginTop: 0 }}>
          Stands near these features get a ranking boost. <b>Weight</b> sets how much each type can add to a stand’s score; <b>falloff</b> is the distance (meters) beyond which a feature stops helping. Bonuses from multiple nearby features of the same type stack.
        </p>
        {TYPES.map(({ key, label, icon: Icon, color }) => (
          <div key={key} className="card" style={{ padding: 14, marginBottom: 10 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
              <Icon size={17} color={color} /><strong style={{ fontSize: 14.5 }}>{label}</strong>
            </div>
            <SliderRow label="Weight (max bonus)" min={0} max={0.5} step={0.01} value={s[`weight_${key}`]}
              display={s[`weight_${key}`].toFixed(2)} onChange={(v) => setS({ ...s, [`weight_${key}`]: v })} />
            <SliderRow label="Falloff distance" min={25} max={600} step={25} value={s[`falloff_${key}`]}
              display={`${Math.round(s[`falloff_${key}`])} m`} onChange={(v) => setS({ ...s, [`falloff_${key}`]: v })} />
          </div>
        ))}

        <div className="card" style={{ padding: 14, marginBottom: 10 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
            <Camera size={17} color="var(--navy)" /><strong style={{ fontSize: 14.5 }}>Trail-camera boost cap</strong>
          </div>
          <p style={{ fontSize: 12, color: "var(--sub)", marginTop: 0, marginBottom: 10 }}>
            Max ranking boost a stand can get from recent daylight camera photos. Positive-only — cameras never lower a stand’s rank.
          </p>
          <SliderRow label="Max camera boost" min={0} max={30} step={1} value={s.max_camera_boost_pct ?? 15}
            display={`${Math.round(s.max_camera_boost_pct ?? 15)}%`} onChange={(v) => setS({ ...s, max_camera_boost_pct: v })} />
        </div>

        <div style={{ display: "flex", gap: 8, marginTop: 4 }}>
          <button className="btn btn-primary" onClick={save}><Save size={15} /> {saved ? "Saved ✓" : "Save settings"}</button>
          <button className="btn" onClick={reset}>Reset to defaults</button>
        </div>
      </>)}

      {/* ── TAB: Daily Rating ── */}
      {tab === "rating" && (<>
        <h2 style={{ fontSize: 15, marginTop: 8, marginBottom: 6 }}>Weather factor weights</h2>
        <p style={{ color: "var(--sub)", fontSize: 13.5, marginTop: 0 }}>
          These tune the 1–5 daily movement rating. They set how much each weather factor counts toward a day’s score. The values are relative — raising one lowers the others’ share automatically.
        </p>
        {(() => {
          const RW = [
            { key: "rate_w_pressure", label: "Barometric pressure" },
            { key: "rate_w_wind", label: "Wind" },
            { key: "rate_w_rain", label: "Rain" },
            { key: "rate_w_temp", label: "Temperature shift" },
          ];
          const sum = RW.reduce((a, r) => a + (s[r.key] ?? 0), 0) || 1;
          return (
            <div className="card" style={{ padding: 14, marginBottom: 10 }}>
              {RW.map((r) => (
                <SliderRow key={r.key} label={`${r.label} — ${Math.round((s[r.key] ?? 0) / sum * 100)}% of weather`}
                  min={0} max={1} step={0.01} value={s[r.key] ?? 0}
                  display={(s[r.key] ?? 0).toFixed(2)} onChange={(v) => setS({ ...s, [r.key]: v })} />
              ))}
            </div>
          );
        })()}

        <div className="card" style={{ padding: 14, marginBottom: 10 }}>
          <strong style={{ fontSize: 14.5 }}>Regional rut peak</strong>
          <p style={{ fontSize: 12, color: "var(--sub)", marginTop: 6, marginBottom: 10 }}>
            The breeding-peak date for your area. The rating’s daylight-movement peak is set automatically to the seeking/chasing window ~10 days before this. Central Arkansas ≈ Dec 5; north AR ≈ Nov 13.
          </p>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
            <Field label="Peak month">
              <select value={Math.round(s.rut_peak_month ?? 12)} onChange={(e) => setS({ ...s, rut_peak_month: +e.target.value })}>
                {MONTHS.map((m, i) => <option key={m} value={i + 1}>{m}</option>)}
              </select>
            </Field>
            <Field label="Peak day">
              <input type="number" min={1} max={31} value={Math.round(s.rut_peak_day ?? 5)}
                onChange={(e) => setS({ ...s, rut_peak_day: Math.max(1, Math.min(31, +e.target.value)) })} />
            </Field>
          </div>
        </div>

        <div style={{ display: "flex", gap: 8, marginTop: 4 }}>
          <button className="btn btn-primary" onClick={save}><Save size={15} /> {saved ? "Saved ✓" : "Save settings"}</button>
          <button className="btn" onClick={resetRating}>Reset rating weights</button>
        </div>
        <p style={{ color: "var(--sub)", fontSize: 11, marginTop: 12, fontStyle: "italic" }}>
          Moon phase is intentionally excluded — MSU research found no significant effect on buck activity.
        </p>
      </>)}

      {/* ── TAB: Trail Cameras ── */}
      {tab === "cameras" && (
        <CameraSettings settings={s} setSettings={setS} saveSettings={save} />
      )}
    </div>
  );
}

/* ───────── trail-camera settings (Settings > Cameras tab) ───────── */
function CameraSettings({ settings, setSettings, saveSettings }) {
  const [cameras, setCameras] = useState(null);
  const [stands, setStands] = useState([]);
  const [wizard, setWizard] = useState(false);
  const [busy, setBusy] = useState(null);
  const [note, setNote] = useState(null);

  const load = useCallback(async () => {
    try { setCameras(await api("/cameras")); } catch { setCameras([]); }
  }, []);
  useEffect(() => { load(); api("/stands").then(setStands).catch(() => {}); }, [load]);

  async function saveIntervals() {
    await saveSettings();
    setNote("Saved"); setTimeout(() => setNote(null), 1500);
  }
  async function verify(id) {
    setBusy(id);
    try { const r = await api(`/cameras/${id}/verify`, { method: "POST" }); setNote(r.ok ? "Credentials OK" : "Verify failed"); }
    catch (e) { setNote(e.code === 501 ? "That brand isn’t wired up yet" : "Verify failed"); }
    finally { setBusy(null); setTimeout(() => setNote(null), 2500); }
  }
  async function syncNow(id) {
    setBusy(id);
    try { const r = await api(`/cameras/${id}/sync`, { method: "POST" }); setNote(`Synced — ${r.new_sightings} new`); await load(); }
    catch { setNote("Sync failed"); }
    finally { setBusy(null); setTimeout(() => setNote(null), 2500); }
  }
  async function removeCam(id) {
    await api(`/cameras/${id}`, { method: "DELETE" }); load();
  }
  const standName = (sid) => stands.find((x) => x.id === sid)?.name || "—";
  const BRAND_LABEL = { spypoint: "SpyPoint", reveal: "Reveal", moultrie: "Moultrie", stealth_cam: "Stealth Cam", browning: "Browning", spartan: "Spartan" };

  return (
    <>
      <div className="card" style={{ padding: 14, marginBottom: 14 }}>
        <strong style={{ fontSize: 14.5 }}>Sync & storage</strong>
        <div style={{ marginTop: 10 }}>
          <Field label="Image storage directory">
            <input type="text" value={settings.camera_image_dir ?? "/app/data/camera_images"}
              placeholder="/app/data/camera_images"
              onChange={(e) => setSettings({ ...settings, camera_image_dir: e.target.value })} />
          </Field>
          <div style={{ fontSize: 11.5, color: "var(--sub)", marginTop: 4 }}>
            Base folder on the server where camera photos are saved. Images are stored in <code>[directory]/[Camera Brand]/[Camera Name]/</code>.
          </div>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginTop: 10 }}>
          <Field label="Sync interval (minutes)">
            <input type="number" min={5} max={720} value={Math.round(settings.camera_sync_interval_minutes ?? 30)}
              onChange={(e) => setSettings({ ...settings, camera_sync_interval_minutes: +e.target.value })} />
          </Field>
          <Field label="Keep photos (days)">
            <input type="number" min={1} max={365} value={Math.round(settings.image_retention_days ?? 60)}
              onChange={(e) => setSettings({ ...settings, image_retention_days: +e.target.value })} />
          </Field>
        </div>
        <div style={{ fontSize: 11.5, color: "var(--sub)", marginTop: 8 }}>
          Photos older than the retention window are deleted nightly; the sighting record is kept for model tuning.
        </div>
        <button className="btn btn-primary" style={{ marginTop: 12 }} onClick={saveIntervals}><Save size={15} /> Save</button>
      </div>

      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
        <strong style={{ fontSize: 14.5 }}>Cameras</strong>
        <button className="btn btn-primary" onClick={() => setWizard(true)}><Plus size={15} /> Set up a new Camera</button>
      </div>
      {note && <div style={{ fontSize: 12.5, color: "var(--navy)", marginBottom: 8 }}>{note}</div>}

      {cameras === null && <Empty>Loading…</Empty>}
      {cameras && cameras.length === 0 && <Empty>No cameras yet. Click “Set up a new Camera” to connect one.</Empty>}
      <div style={{ display: "grid", gap: 8 }}>
        {(cameras || []).map((c) => (
          <div key={c.id} className="card" style={{ padding: "10px 12px", display: "flex", alignItems: "center", gap: 12 }}>
            <Camera size={18} color="var(--navy)" style={{ flexShrink: 0 }} />
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontWeight: 500 }}>{c.name} <span style={{ fontSize: 12, color: "var(--sub)", fontWeight: 400 }}>· {BRAND_LABEL[c.brand] || c.brand}</span></div>
              <div style={{ fontSize: 12, color: "var(--sub)" }}>
                Stand: {standName(c.stand_id)}{c.last_sync_at ? ` · last sync ${new Date(c.last_sync_at).toLocaleString()}` : " · never synced"}
              </div>
            </div>
            <button className="icon-btn" title="Verify credentials" disabled={busy === c.id} onClick={() => verify(c.id)}><CheckCircle2 size={15} /></button>
            <button className="icon-btn" title="Sync now" disabled={busy === c.id} onClick={() => syncNow(c.id)}><RefreshCw size={15} /></button>
            <button className="icon-btn" title="Remove" onClick={() => removeCam(c.id)}><Trash2 size={15} /></button>
          </div>
        ))}
      </div>

      {wizard && <CameraWizard stands={stands} onClose={() => setWizard(false)} onDone={() => { setWizard(false); load(); }} />}
    </>
  );
}

/* ───────── 3-step camera setup wizard ───────── */
function CameraWizard({ stands, onClose, onDone }) {
  const [step, setStep] = useState(1);
  const [providers, setProviders] = useState([]);
  const [brand, setBrand] = useState(null);
  const [name, setName] = useState("");
  const [creds, setCreds] = useState({});
  const [standId, setStandId] = useState(stands[0]?.id ?? null);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => { api("/camera-providers").then((r) => setProviders(r.providers || [])).catch(() => {}); }, []);
  const BRAND_LABEL = { spypoint: "SpyPoint", reveal: "Reveal", moultrie: "Moultrie", stealth_cam: "Stealth Cam", browning: "Browning", spartan: "Spartan" };
  const meta = providers.find((p) => p.brand === brand);
  const fields = meta?.credential_fields || ["username", "password"];

  async function create() {
    setBusy(true); setErr(null);
    try {
      const cam = await api("/cameras", { method: "POST", body: JSON.stringify({ name: name || BRAND_LABEL[brand], brand, stand_id: standId, credentials: creds }) });
      // best-effort verify (won't block completion for unimplemented brands)
      try { await api(`/cameras/${cam.id}/verify`, { method: "POST" }); } catch {}
      onDone();
    } catch { setErr("Couldn’t save the camera."); setBusy(false); }
  }

  return (
    <Modal onClose={onClose}>
      <div className="card" style={{ padding: 18, border: "2px solid var(--navy)" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
          <strong>Set up a camera</strong>
          <button className="icon-btn" onClick={onClose}><X size={16} /></button>
        </div>
        <div style={{ fontSize: 12, color: "var(--sub)", marginBottom: 14 }}>Step {step} of 3</div>

        {step === 1 && (<>
          <div style={{ fontSize: 13.5, marginBottom: 10 }}>Choose your camera brand:</div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
            {providers.map((p) => (
              <button key={p.brand} onClick={() => setBrand(p.brand)}
                style={{
                  padding: "12px 10px", borderRadius: 10, cursor: "pointer", textAlign: "left",
                  border: brand === p.brand ? "2px solid var(--navy)" : "1px solid var(--bord2)",
                  background: brand === p.brand ? "var(--surf)" : "var(--bg)", color: "var(--txt)",
                }}>
                <div style={{ fontWeight: 600, fontSize: 13.5 }}>{BRAND_LABEL[p.brand]}</div>
                <div style={{ fontSize: 11, color: p.implemented ? "var(--green)" : "var(--amber)", marginTop: 3 }}>
                  {p.implemented ? "supported" : "coming soon"}
                </div>
              </button>
            ))}
          </div>
          {brand && meta && !meta.implemented && (
            <div style={{ fontSize: 12, color: "var(--amber)", marginTop: 10, display: "flex", gap: 6, alignItems: "flex-start" }}>
              <AlertTriangle size={14} style={{ flexShrink: 0, marginTop: 1 }} />
              {BRAND_LABEL[brand]} integration isn’t wired up yet. You can save it now, but photos won’t sync until it’s implemented.
            </div>
          )}
          <div style={{ display: "flex", gap: 8, marginTop: 16, justifyContent: "flex-end" }}>
            <button className="btn" onClick={onClose}>Cancel</button>
            <button className="btn btn-primary" disabled={!brand} onClick={() => setStep(2)}>Next</button>
          </div>
        </>)}

        {step === 2 && (<>
          <div style={{ fontSize: 13.5, marginBottom: 10 }}>Sign in to your {BRAND_LABEL[brand]} account:</div>
          <Field label="Camera name (label)"><input value={name} onChange={(e) => setName(e.target.value)} placeholder={`${BRAND_LABEL[brand]} — North ridge`} /></Field>
          <div style={{ marginTop: 10, display: "grid", gap: 10 }}>
            {fields.map((f) => (
              <Field key={f} label={f.charAt(0).toUpperCase() + f.slice(1)}>
                <input type={f.toLowerCase().includes("pass") || f.toLowerCase().includes("token") ? "password" : "text"}
                  value={creds[f] || ""} onChange={(e) => setCreds({ ...creds, [f]: e.target.value })} />
              </Field>
            ))}
          </div>
          <div style={{ fontSize: 11, color: "var(--sub)", marginTop: 8 }}>
            Credentials are encrypted before storage. They’re only used to fetch your photos.
          </div>
          <div style={{ display: "flex", gap: 8, marginTop: 16, justifyContent: "flex-end" }}>
            <button className="btn" onClick={() => setStep(1)}>Back</button>
            <button className="btn btn-primary" onClick={() => setStep(3)}>Next</button>
          </div>
        </>)}

        {step === 3 && (<>
          <div style={{ fontSize: 13.5, marginBottom: 10 }}>Pair this camera with a stand:</div>
          <Field label="Stand">
            <select value={standId ?? ""} onChange={(e) => setStandId(e.target.value ? +e.target.value : null)}>
              <option value="">— none —</option>
              {stands.map((st) => <option key={st.id} value={st.id}>{st.name}</option>)}
            </select>
          </Field>
          <div style={{ fontSize: 11.5, color: "var(--sub)", marginTop: 8 }}>
            Sightings from this camera boost the paired stand’s ranking when deer show up in daylight.
          </div>
          {err && <div style={{ fontSize: 12.5, color: "var(--red)", marginTop: 10 }}>{err}</div>}
          <div style={{ display: "flex", gap: 8, marginTop: 16, justifyContent: "flex-end" }}>
            <button className="btn" onClick={() => setStep(2)}>Back</button>
            <button className="btn btn-primary" disabled={busy} onClick={create}><Save size={15} /> {busy ? "Saving…" : "Finish"}</button>
          </div>
        </>)}
      </div>
    </Modal>
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

/* ───────── modal ───────── */
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

function PageHead({ title, action }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
      <h1 style={{ fontSize: 20 }}>{title}</h1>{action}
    </div>
  );
}

/* ───────── 14-day deer outlook strip ───────── */
function DeerOutlook({ ratings, selectedDay, onPick, loadableDays }) {
  const toneFor = (r) => r >= 4 ? "var(--green)" : r === 3 ? "var(--amber)" : "var(--red)";
  const hasLow = ratings.some((r) => r.confidence === "low");
  return (
    <Section title="14-day movement outlook">
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(88px, 1fr))", gap: 6 }}>
        {ratings.map((r) => {
          const tone = toneFor(r.rating);
          const low = r.confidence === "low";
          const sel = r.day === selectedDay;
          const loadable = loadableDays.includes(r.day);
          return (
            <button key={r.day} onClick={() => loadable && onPick(r.day)} disabled={!loadable}
              title={`${r.rating}/5 · ${r.rut.phase}${low ? " · lower-confidence weather" : ""}${loadable ? "" : " · planning only"}`}
              style={{
                border: sel ? "2px solid var(--navy)" : "1px solid var(--bord)", borderRadius: 10, padding: "8px 6px",
                background: "var(--bg)", cursor: loadable ? "pointer" : "default", textAlign: "center", opacity: low ? 0.62 : 1,
              }}>
              <div style={{ fontSize: 11, color: "var(--sub)", marginBottom: 3 }}>{r.label}</div>
              <div style={{ fontSize: 13, letterSpacing: 0.5 }}>{"🦌".repeat(r.rating)}</div>
              <div style={{ fontSize: 11, fontWeight: 600, color: tone, marginTop: 2 }}>{r.rating}/5</div>
              {low && <div style={{ fontSize: 9, color: "var(--sub)", marginTop: 2 }}>est.</div>}
            </button>
          );
        })}
      </div>
      {hasLow && (
        <div style={{ fontSize: 11, color: "var(--sub)", marginTop: 8, fontStyle: "italic" }}>
          Days marked “est.” are 8+ days out — the rut signal is solid but the weather forecast (and therefore wind-based stand rankings) softens past ~7 days, so treat those as directional.
        </div>
      )}
      <div style={{ fontSize: 11, color: "var(--sub)", marginTop: 4 }}>
        Tap any day to load its map and stand rankings.
      </div>
    </Section>
  );
}

/* ───────── deer movement day rating ───────── */
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
        <span style={{ fontWeight: 600, color: tone }}>{r}/5 movement</span>
        <span style={{ fontSize: 12, color: "var(--sub)" }}>· {rating.rut.phase}</span>
        <span style={{ marginLeft: "auto", fontSize: 12, color: "var(--sub)" }}>{open ? "hide ▲" : "why? ▼"}</span>
      </div>
      {open && (
        <div style={{ marginTop: 10, paddingTop: 10, borderTop: "1px solid var(--bord)" }}>
          {bar("Rut intensity", rating.rut.intensity)}
          {bar("Barometric", fac.pressure)}
          {bar("Wind", fac.wind)}
          {bar("Rain (1=dry)", fac.rain)}
          {bar("Temp shift", fac.temp_shift)}
          {Array.isArray(rating.breakdown) && (
            <div style={{ marginTop: 10, display: "grid", gap: 3 }}>
              {rating.breakdown.map((b, i) => (
                <div key={i} style={{ fontSize: 11, color: "var(--sub)", display: "flex", justifyContent: "space-between", gap: 8 }}>
                  <span><b style={{ color: "var(--txt)", fontWeight: 500 }}>{b.factor}:</b> {b.impact}</span>
                  <span style={{ flexShrink: 0 }}>{b.weight === "multiplier" ? "×" : `w ${b.weight}`}</span>
                </div>
              ))}
            </div>
          )}
          <div style={{ fontSize: 11, color: "var(--sub)", marginTop: 8, lineHeight: 1.5 }}>
            {rating.inputs.pressure_inhg != null && <>{rating.inputs.pressure_inhg}″ pressure · </>}
            {rating.inputs.wind_mph != null && <>{rating.inputs.wind_mph} mph wind · </>}
            {rating.inputs.day_high_f != null && <>{rating.inputs.day_high_f}°F high · </>}
            {rating.inputs.rain_mm != null && <>{rating.inputs.rain_mm} mm rain</>}
          </div>
          <div style={{ fontSize: 10.5, color: "var(--sub)", marginTop: 6, fontStyle: "italic" }}>
            Optimized for daytime movement. Moon phase intentionally excluded — MSU research found no significant effect on buck activity.
          </div>
        </div>
      )}
    </div>
  );
}

/* ───────── day-period ranking ───────── */
const PERIOD_COLORS = { morning: "#C28800", midday: "#1E7FB0", evening: "#7A3FA0" };
const PERIOD_LABEL = { morning: "Morning", midday: "Midday", evening: "Evening" };

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
  const [open, setOpen] = useState(false);
  // border reflects the (first) period this stand wins, if any
  const winColor = wins.length ? PERIOD_COLORS[wins[0]] : undefined;
  const pct = (p) => periods[p] ? Math.round(periods[p].score.total * 100) : null;
  const proxTotal = proximity ? Math.round(proximity.total * 100) : 0;
  // best-period camera boost (if any)
  const bestP = row.best_period;
  const cam = bestP && periods[bestP]?.score?.camera;
  const camBoost = cam && cam.boost_pct > 0 ? cam.boost_pct : 0;
  return (
    <div className="card" style={{ padding: "12px 14px", marginBottom: 8, border: winColor ? `2px solid ${winColor}` : undefined }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <span style={{ fontWeight: 600, fontSize: 15 }}>{stand.name}</span>
        {wins.map((p) => (
          <span key={p} style={{ fontSize: 11.5, fontWeight: 600, color: "#fff", background: PERIOD_COLORS[p], padding: "2px 8px", borderRadius: 6 }}>
            ★ Best {PERIOD_LABEL[p].toLowerCase()}
          </span>
        ))}
        {proxTotal > 0 && (
          <span title={`corridor +${Math.round(proximity.corridor * 100)} · food +${Math.round(proximity.food * 100)} · bedding +${Math.round(proximity.bedding * 100)}`}
            style={{ fontSize: 11.5, fontWeight: 500, color: "var(--green)", background: "rgba(59,109,17,.12)", padding: "2px 8px", borderRadius: 6 }}>
            proximity +{proxTotal}
          </span>
        )}
        {camBoost > 0 && (
          <span title={cam.text} style={{ fontSize: 11.5, fontWeight: 500, color: "var(--navy)", background: "rgba(12,68,124,.12)", padding: "2px 8px", borderRadius: 6, display: "inline-flex", alignItems: "center", gap: 4 }}>
            <Camera size={11} /> +{camBoost}%
          </span>
        )}
        <button className="icon-btn" style={{ marginLeft: "auto" }} title="Score breakdown" onClick={() => setOpen((o) => !o)}>
          <ChevronDown size={15} style={{ transform: open ? "rotate(180deg)" : "none", transition: "transform .15s" }} />
        </button>
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
      {open && (
        <div style={{ marginTop: 10, paddingTop: 10, borderTop: "1px solid var(--bord)" }}>
          {["morning", "midday", "evening"].map((p) => {
            const bd = periods[p]?.score?.breakdown;
            if (!bd) return null;
            return (
              <div key={p} style={{ marginBottom: 8 }}>
                <div style={{ fontSize: 11.5, fontWeight: 600, color: PERIOD_COLORS[p], marginBottom: 3 }}>{PERIOD_LABEL[p]}</div>
                <div style={{ display: "grid", gap: 2 }}>
                  {bd.map((b, i) => (
                    <div key={i} style={{ fontSize: 11, color: "var(--sub)", display: "flex", justifyContent: "space-between", gap: 8 }}>
                      <span><b style={{ color: "var(--txt)", fontWeight: 500 }}>{b.factor}:</b> {b.text}</span>
                    </div>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

/* ───────── result card ───────── */
function ResultCard({ rank, data }) {
  const { stand, avg, sample } = data;
  const pct = Math.round(avg * 100), sc = sample?.score;
  const grade = pct >= 70 ? { t: "Good", c: "var(--green)", bg: "rgba(59,109,17,.12)" } : pct >= 45 ? { t: "Marginal", c: "var(--amber)", bg: "rgba(133,79,11,.12)" } : { t: "Avoid", c: "var(--red)", bg: "rgba(163,45,45,.12)" };
  return (
    <div className="card" style={{ padding: "12px 14px", marginBottom: 8, display: "flex", gap: 14, alignItems: "center", border: rank === 0 ? "2px solid var(--navy)" : undefined }}>
      <Compass scentDeg={sc?.scent_to_deg} deerDeg={stand.deer_approach_deg} drainageDeg={stand.terrain?.drainage_deg} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <span style={{ fontWeight: 500, fontSize: 15 }}>{rank === 0 ? "★ " : `${rank + 1}. `}{stand.name}</span>
          <span style={{ fontSize: 12, fontWeight: 500, color: grade.c, background: grade.bg, padding: "2px 8px", borderRadius: 6 }}>{grade.t} · {pct}</span>
        </div>
        {sc && (
          <div style={{ fontSize: 12.5, color: "var(--sub)", marginTop: 4, lineHeight: 1.55 }}>
            Wind {degToCompass(sample.hour.wind_dir)} {Math.round(sample.hour.wind_speed)}mph · thermals {sc.thermal_phase}
            {stand.terrain && sc.thermal_phase === "sinking" && <> down the {degToCompass(sc.drainage_deg)} drainage</>}
            {" "}· scent drifts {degToCompass(sc.scent_to_deg)}
            {stand.deer_approach_deg != null && <> · {sc.scent_score > 0.6 ? "carries away from deer" : sc.scent_score > 0.35 ? "crosses the deer zone" : "blows toward deer"}</>}
          </div>
        )}
      </div>
    </div>
  );
}

function Compass({ scentDeg, deerDeg, drainageDeg, size = 58 }) {
  const r = size / 2, cx = r, cy = r;
  const pt = (deg, len) => { const rad = (deg - 90) * Math.PI / 180; return [cx + Math.cos(rad) * len, cy + Math.sin(rad) * len]; };
  const [sx, sy] = scentDeg != null ? pt(scentDeg, r - 7) : [cx, cy];
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} style={{ flexShrink: 0 }}>
      <circle cx={cx} cy={cy} r={r - 2} fill="none" stroke="var(--bord2)" />
      <text x={cx} y={9} fontSize="8" textAnchor="middle" fill="var(--sub)">N</text>
      {drainageDeg != null && (() => { const [ax, ay] = pt(drainageDeg, r - 5); return <line x1={cx} y1={cy} x2={ax} y2={ay} stroke="var(--blue)" strokeWidth={1.5} strokeDasharray="2 2" opacity={0.7} />; })()}
      {deerDeg != null && (() => { const [dx, dy] = pt(deerDeg, r - 6); return <circle cx={dx} cy={dy} r={4} fill="var(--red)" />; })()}
      {scentDeg != null && <line x1={cx} y1={cy} x2={sx} y2={sy} stroke="var(--navy)" strokeWidth={2.5} markerEnd="url(#ah)" />}
      <defs><marker id="ah" markerWidth="6" markerHeight="6" refX="4" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 Z" fill="var(--navy)" /></marker></defs>
    </svg>
  );
}

/* ───────── stand editor (used inside Modal) ───────── */
function StandEditor({ stand, onSave, onCancel, reload }) {
  const [s, setS] = useState({ ...stand });
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState(null);
  const [savedId, setSavedId] = useState(stand.id);
  const valid = s.name && s.lat !== "" && s.lon !== "" && !isNaN(+s.lat) && !isNaN(+s.lon);

  async function analyze() {
    if (!valid) return;
    setLoading(true); setErr(null);
    try {
      let id = savedId;
      const body = { name: s.name, lat: +s.lat, lon: +s.lon, downhill_deg: s.downhill_deg, deer_approach_deg: s.deer_approach_deg };
      if (!id) { const created = await api("/stands", { method: "POST", body: JSON.stringify(body) }); id = created.id; setSavedId(id); }
      else { await api(`/stands/${id}`, { method: "PUT", body: JSON.stringify(body) }); }
      const updated = await api(`/stands/${id}/terrain`, { method: "POST" });
      setS({ ...updated, lat: updated.lat, lon: updated.lon });
      reload && reload();
    } catch { setErr("Couldn’t reach an elevation source. Set downhill by hand below — the app still works."); }
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
          <button className="btn" onClick={analyze} disabled={!valid || loading}><Mountain size={14} /> {loading ? "Reading terrain…" : s.terrain ? "Re-analyze" : "Analyze terrain"}</button>
        </div>
        {err && <div style={{ fontSize: 12, color: "var(--amber)", marginBottom: 8 }}>{err}</div>}
        {s.terrain && <TerrainPanel t={s.terrain} />}
        <div style={{ marginTop: 10 }}>
          <DirPicker label="Downhill faces" value={s.downhill_deg} onChange={(d) => setS({ ...s, downhill_deg: d })} />
          <div style={{ fontSize: 11.5, color: "var(--sub)", marginTop: 4 }}>{s.terrain ? "Set from the elevation grid — adjust if you know better." : "Set by hand, or analyze terrain above to fill it automatically."}</div>
        </div>
      </div>
      <div style={{ marginTop: 14, paddingTop: 12, borderTop: "1px solid var(--bord)" }}>
        <DirPicker label="Deer approach from (optional)" value={s.deer_approach_deg} onChange={(d) => setS({ ...s, deer_approach_deg: d })} allowNull />
      </div>
      <div style={{ display: "flex", gap: 8, marginTop: 16 }}>
        <button className="btn btn-primary" disabled={!valid} onClick={() => onSave({ name: s.name, lat: +s.lat, lon: +s.lon, downhill_deg: s.downhill_deg, deer_approach_deg: s.deer_approach_deg }, savedId)}><Save size={15} /> Save stand</button>
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
        <div style={{ display: "flex", alignItems: "center", gap: 5, color: "var(--green)", marginBottom: 4 }}><CheckCircle2 size={13} /> <span style={{ fontWeight: 500 }}>{t.source}</span></div>
        <div style={{ color: "var(--sub)" }}>Elevation <b style={{ color: "var(--txt)" }}>{t.elevation} m</b> · relief <b style={{ color: "var(--txt)" }}>{t.relief} m</b> over the {Math.round(t.box_m)} m box</div>
        <div style={{ color: "var(--sub)" }}>Slope <b style={{ color: "var(--txt)" }}>{t.slope_pct}%</b> · faces <b style={{ color: "var(--txt)" }}>{degToCompass(t.downhill_deg)}</b></div>
        <div style={{ color: "var(--sub)", display: "flex", alignItems: "center", gap: 4 }}><Waves size={12} color="var(--blue)" /> Cold air drains <b style={{ color: "var(--txt)" }}>{degToCompass(t.drainage_deg)}</b> {t.channel_strength > 0.5 ? "(strong channel)" : t.channel_strength > 0.2 ? "(moderate)" : "(diffuse)"}</div>
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
    rects.push(<rect key={"e" + r + c} x={c * cell} y={r * cell} width={cell + 0.5} height={cell + 0.5} fill={`rgb(${shade - 10},${shade},${shade - 20})`} />);
    if (t.acc[r][c] > maxAcc * 0.18) chans.push(<rect key={"a" + r + c} x={c * cell} y={r * cell} width={cell + 0.5} height={cell + 0.5} fill="var(--blue)" opacity={Math.min(0.85, 0.3 + t.acc[r][c] / maxAcc)} />);
  }
  const ctr = (N / 2) * cell;
  return (
    <svg width={px} height={px} viewBox={`0 0 ${px} ${px}`} style={{ borderRadius: 6, flexShrink: 0, border: "1px solid var(--bord)" }}>
      {rects}{chans}
      <circle cx={ctr} cy={ctr} r={3.5} fill="none" stroke="var(--red)" strokeWidth={1.5} />
      <circle cx={ctr} cy={ctr} r={1.5} fill="var(--red)" />
    </svg>
  );
}

/* ───────── small components ───────── */
function Section({ title, right, children }) {
  return (<section style={{ marginTop: 24 }}>
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
      <h2>{title}</h2>{right}
    </div>{children}
  </section>);
}
function Field({ label, children }) { return <label style={{ display: "block" }}><span style={{ fontSize: 12, color: "var(--sub)", display: "block", marginBottom: 4 }}>{label}</span>{children}</label>; }
function Empty({ children }) { return <div style={{ fontSize: 13.5, color: "var(--sub)", padding: "10px 0" }}>{children}</div>; }
function Banner({ children }) { return <div style={{ background: "rgba(133,79,11,.12)", color: "var(--amber)", padding: "8px 12px", borderRadius: 8, fontSize: 13, marginBottom: 10, display: "flex", gap: 8, alignItems: "center" }}><AlertTriangle size={15} /> {children}</div>; }
function LayerChip({ on, onClick, color, label, dashed }) {
  return (
    <button onClick={onClick} className="chip" style={{ display: "inline-flex", alignItems: "center", gap: 6, opacity: on ? 1 : 0.45 }}>
      <span style={{ display: "inline-block", width: 16, height: 0, borderTop: `2px ${dashed ? "dashed" : "solid"} ${color}` }} />
      {label}{on ? <Eye size={12} /> : <EyeOff size={12} />}
    </button>
  );
}
function DirPicker({ label, value, onChange, allowNull }) {
  return (<div>
    <div style={{ fontSize: 13, color: "var(--sub)", marginBottom: 6 }}>{label}{value != null && <strong style={{ color: "var(--txt)" }}> · {degToCompass(value)}</strong>}</div>
    <div className="grid-dir">
      {allowNull && <button className={"chip" + (value == null ? " on" : "")} onClick={() => onChange(null)}>none</button>}
      {DIRS.map((d) => <button key={d} className={"chip" + (value === compassToDeg(d) ? " on" : "")} onClick={() => onChange(Math.round(compassToDeg(d)))}>{d}</button>)}
    </div>
  </div>);
}
