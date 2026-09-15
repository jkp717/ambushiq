import React, { useState, useEffect, useCallback, useRef } from "react";
import { Lock, Menu, Sun, Map as MapIcon, MapPin, Wheat, Camera, Settings as SettingsIcon, RefreshCw } from "lucide-react";
import { AuthProvider } from "./context/AuthContext.jsx";
import { useAuth } from "./hooks/useAuth.js";
import { api } from "./services/api.js";
import Centered from "./components/ui/Centered.jsx";
import Modal from "./components/ui/Modal.jsx";
import StandEditor from "./components/StandEditor.jsx";
import Login from "./pages/Login.jsx";
import TodayPage from "./pages/Today.jsx";
import MapPage from "./pages/Map.jsx";
import StandsPage from "./pages/Stands.jsx";
import ZonesTabPage from "./pages/Zones.jsx";
import CamerasPage from "./pages/Cameras.jsx";
import SettingsPage from "./pages/Settings.jsx";

const NAV = [
  { key: "today",   label: "Outlook",  icon: Sun },
  { key: "map",     label: "Map",      icon: MapIcon },
  { key: "stands",  label: "Stands",   icon: MapPin },
  { key: "zones",   label: "Zones",    icon: Wheat },
  { key: "cameras", label: "Cameras",  icon: Camera },
  { key: "settings",label: "Settings", icon: SettingsIcon },
];

function Shell({ onLogout, version }) {
  const [view, setView] = useState("today");
  const [navOpen, setNavOpen] = useState(false);
  const navDropRef = useRef(null);
  useEffect(() => {
    if (!navOpen) return;
    function h(e) { if (navDropRef.current && !navDropRef.current.contains(e.target)) setNavOpen(false); }
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, [navOpen]);
  const [stands, setStands] = useState([]);
  const [zones, setZones] = useState([]);
  const [corridors, setCorridors] = useState([]);
  const [sign, setSign] = useState([]);
  const [drawRequest, setDrawRequest] = useState(null);
  const [editingStand, setEditingStand] = useState(null);
  const [editingZone, setEditingZone] = useState(null);
  const [editingCorridor, setEditingCorridor] = useState(null);
  const [relocateRequest, setRelocateRequest] = useState(null);

  const loadStands    = useCallback(async () => { try { setStands(await api("/stands")); } catch {} }, []);
  const loadZones     = useCallback(async () => { try { setZones(await api("/zones")); } catch {} }, []);
  const loadCorridors = useCallback(async () => { try { setCorridors(await api("/corridors")); } catch {} }, []);
  const loadSign      = useCallback(async () => { try { setSign(await api("/sign")); } catch {} }, []);
  const loadAll = useCallback(async () => { await Promise.all([loadStands(), loadZones(), loadCorridors(), loadSign()]); }, [loadStands, loadZones, loadCorridors, loadSign]);

  const toggleActiveStand = useCallback(async (stand) => {
    try {
      await api(`/stands/${stand.id}`, { method: "PUT", body: JSON.stringify({
        name: stand.name, lat: stand.lat, lon: stand.lon,
        is_active: !stand.is_active,
        downhill_deg: stand.downhill_deg, deer_approach_deg: stand.deer_approach_deg,
        visibility_m: stand.visibility_m,
      })});
      await loadStands();
    } catch {}
  }, [loadStands]);
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
    else if (kind === "scrape" || kind === "rub") { await api(`/sign/${id}`, { method: "DELETE" }); await loadSign(); }
  }, [loadStands, loadZones, loadCorridors, loadSign]);

  function goDraw(kind) { setDrawRequest(kind); setView("map"); }

  function openStandEditor(coord) {
    setEditingStand({ id: null, name: "", lat: coord ? coord.lat.toFixed(6) : "", lon: coord ? coord.lon.toFixed(6) : "",
                      downhill_deg: null, deer_approach_deg: null, visibility_m: null, terrain: null });
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
        <button className="top-bar-brand" onClick={() => setView("today")} title="Overview">
          <img src="/icon.png" alt="" className="top-bar-icon" />
          <strong>AmbushIQ</strong>
          {version && <span className="top-bar-ver">v{version}</span>}
        </button>
        {/* On map page: burger menu dropdown (with Lock at bottom); other pages: lock icon */}
        {view === "map" ? (
          <div className="top-bar-nav-drop" ref={navDropRef}>
            <button className="top-bar-nav-trigger" onClick={() => setNavOpen(o => !o)} title="Menu">
              <Menu size={18} />
            </button>
            {navOpen && (
              <div className="top-bar-nav-menu">
                {NAV.map(({ key, label, icon: Icon }) => (
                  <button key={key} className={"top-bar-nav-item" + (view === key ? " active" : "")}
                    onClick={() => { setView(key); setNavOpen(false); }}>
                    <Icon size={15} /><span>{label}</span>
                  </button>
                ))}
                <div className="top-bar-nav-divider" />
                <button className="top-bar-nav-item" onClick={() => { setNavOpen(false); onLogout(); }}>
                  <Lock size={15} /><span>Lock</span>
                </button>
              </div>
            )}
          </div>
        ) : (
          <button className="icon-btn" onClick={onLogout} title="Lock"><Lock size={16} /></button>
        )}
      </header>

      {/* tab bar — hidden on map page to reclaim vertical space */}
      {view !== "map" && (
        <nav className="tab-bar">
          {NAV.map(({ key, label, icon: Icon }) => (
            <button key={key} className={"tab-btn" + (view === key ? " active" : "")} onClick={() => setView(key)}>
              <Icon size={17} /><span>{label}</span>
            </button>
          ))}
        </nav>
      )}

      {/* page content */}
      <main className="main-content">
        {view === "today" && (
          <TodayPage stands={stands} zones={zones} corridors={corridors}
            onGoDraw={goDraw} openStandEditor={openStandEditor} />
        )}
        {view === "map" && (
          <MapPage stands={stands} zones={zones} corridors={corridors} sign={sign}
            reloadStands={loadStands} reloadZones={loadZones} reloadCorridors={loadCorridors} reloadSign={loadSign}
            drawRequest={drawRequest} clearDrawRequest={() => setDrawRequest(null)}
            relocateRequest={relocateRequest} clearRelocateRequest={() => setRelocateRequest(null)}
            openStandEditor={openStandEditor} onEditFeature={editFeature} onDeleteFeature={deleteFeature} />
        )}
        {view === "stands" && (
          <StandsPage stands={stands} onAdd={() => goDraw("stand")} onEdit={setEditingStand}
            onToggle={toggleActiveStand}
            onDelete={async (id) => { await api(`/stands/${id}`, { method: "DELETE" }); loadStands(); }} />
        )}
        {view === "zones" && (
          <ZonesTabPage zones={zones} corridors={corridors} sign={sign} reloadSign={loadSign}
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

function AppGate() {
  const { authState, version, logout } = useAuth();
  if (authState === "checking") return <Centered><RefreshCw className="spin" size={20} /></Centered>;
  if (authState === "need") return <Login />;
  return <Shell onLogout={logout} version={version} />;
}

export default function App() {
  return (
    <AuthProvider>
      <AppGate />
    </AuthProvider>
  );
}
