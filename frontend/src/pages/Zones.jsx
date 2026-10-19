import { useState } from "react";
import { Wheat, Trees, Footprints, Target, Plus, Eye, EyeOff, Edit3, Trash2 } from "lucide-react";
import { api } from "../services/api.js";
import { corridorLengthFt } from "../utils/formatters.js";
import { fmtYd } from "../utils/units.js";
import Empty from "../components/ui/Empty.jsx";
import MiniMap from "../components/MiniMap.jsx";

function ZonesTabPage({ zones, corridors, sign, reloadSign, onAdd, reloadZones, reloadCorridors,
                        onEditZone, onEditCorridor }) {
  const [tab, setTab] = useState("food");
  const TABS = [
    { key: "food",      label: "Food",      icon: Wheat },
    { key: "bedding",   label: "Bedding",   icon: Trees },
    { key: "corridors", label: "Corridors", icon: Footprints },
    { key: "sign",      label: "Sign",      icon: Target },
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
          onEdit={onEditZone} />
      )}
      {tab === "corridors" && (
        <CorridorsPage corridors={corridors} onAdd={() => onAdd("corridor")}
          reload={reloadCorridors} onEdit={onEditCorridor} />
      )}
      {tab === "sign" && (
        <SignPage sign={sign} reload={reloadSign} />
      )}
    </div>
  );
}

/* ════════════════════════════════════════════════════
   ZONES / CORRIDORS (inner pages, used inside ZonesTabPage)
   ════════════════════════════════════════════════════ */
function ZonesPage({ kind, zones, onAdd, reload, onEdit }) {
  async function toggleZone(z) {
    try {
      await api(`/zones/${z.id}`, { method: "PUT", body: JSON.stringify({
        kind: z.kind, name: z.name || null, lat: z.lat, lon: z.lon,
        radius_m: z.radius_m, is_active: !z.is_active, quality: z.quality ?? null,
      })});
      reload();
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
          <div key={z.id} className="list-card" style={!z.is_active ? { opacity: 0.55 } : undefined}>
            <div className="list-card-map"><MiniMap kind={kind} feature={{ lat: z.lat, lon: z.lon, radius_m: z.radius_m }} height={110} /></div>
            <div className="list-card-body">
              <div className="list-card-name">
                {z.name || `Unnamed ${noun}`}
                {!z.is_active && <span className="cam-stub-badge" style={{ marginLeft: 6 }}>inactive</span>}
              </div>
              <div className="list-card-sub">
                {(+z.lat).toFixed(4)}, {(+z.lon).toFixed(4)} · {fmtYd(z.radius_m)} radius
                {kind === "food" && <> · Quality {z.quality ?? 5}/10</>}
              </div>
            </div>
            <div className="list-card-actions">
              <button className="icon-btn" title={z.is_active ? "Disable zone" : "Enable zone"}
                onClick={() => toggleZone(z)}>
                {z.is_active ? <Eye size={15} /> : <EyeOff size={15} />}
              </button>
              <button className="icon-btn" onClick={() => onEdit({ ...z })}><Edit3 size={15} /></button>
              <button className="icon-btn" onClick={async () => { await api(`/zones/${z.id}`, { method: "DELETE" }); reload(); }}><Trash2 size={15} /></button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function CorridorsPage({ corridors, onAdd, reload, onEdit }) {
  async function toggleCorridor(c) {
    try {
      await api(`/corridors/${c.id}`, { method: "PUT", body: JSON.stringify({
        name: c.name || null, points: c.points,
        is_active: !c.is_active,
        usage: c.usage ?? 5,
        falloff_m: c.falloff_m ?? null,
        width_m: c.width_m ?? null,
      })});
      reload();
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
          <div key={c.id} className="list-card" style={!c.is_active ? { opacity: 0.55 } : undefined}>
            <div className="list-card-map"><MiniMap kind="corridor" feature={{ points: c.points }} height={110} /></div>
            <div className="list-card-body">
              <div className="list-card-name">
                {c.name || "Unnamed corridor"}
                {!c.is_active && <span className="cam-stub-badge" style={{ marginLeft: 6 }}>inactive</span>}
              </div>
              <div className="list-card-sub">
                {corridorLengthFt(c.points).toLocaleString()} ft · Usage {c.usage ?? 5}/10
                {c.falloff_m != null ? ` · ${fmtYd(c.falloff_m)} falloff` : " · global falloff"}
                {c.width_m ? ` · ${fmtYd(c.width_m)} wide` : ""}
              </div>
            </div>
            <div className="list-card-actions">
              <button className="icon-btn" title={c.is_active ? "Disable corridor" : "Enable corridor"}
                onClick={() => toggleCorridor(c)}>
                {c.is_active ? <Eye size={15} /> : <EyeOff size={15} />}
              </button>
              <button className="icon-btn" onClick={() => onEdit({ ...c })}><Edit3 size={15} /></button>
              <button className="icon-btn" onClick={async () => { await api(`/corridors/${c.id}`, { method: "DELETE" }); reload(); }}><Trash2 size={15} /></button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ════════════════════════════════════════════════════
   SIGN PAGE — scrapes and rubs list
   ════════════════════════════════════════════════════ */
function SignPage({ sign, reload }) {
  async function toggleSign(sg) {
    try {
      await api(`/sign/${sg.id}`, { method: "PUT", body: JSON.stringify({
        kind: sg.kind, lat: sg.lat, lon: sg.lon, is_active: !sg.is_active,
      })});
      reload();
    } catch {}
  }
  return (
    <div>
      <div className="list-header" style={{ marginBottom: 4 }}>
        <h1 style={{ fontSize: 18 }}>Deer Sign</h1>
      </div>
      <p style={{ fontSize: 12.5, color: "var(--sub)", margin: "0 0 12px" }}>
        Add scrapes and rubs via the <strong>+ Add</strong> button on the Map page. Enable the Scrapes/Rubs layer chips to show them on the map.
      </p>
      {!sign.length && <Empty>No deer sign yet — tap "+ Add" on the Map to record a scrape or rub.</Empty>}
      {sign.length > 0 && (
        <div className="list-grid">
          {sign.map((sg) => {
            const color = sg.kind === "scrape" ? "#E87800" : "#8B3A1A";
            return (
              <div key={sg.id} className="list-card" style={!sg.is_active ? { opacity: 0.55 } : undefined}>
                <div className="list-card-body">
                  <div className="list-card-name" style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <span style={{ display: "inline-block", width: 10, height: 10, borderRadius: "50%", background: color, flexShrink: 0 }} />
                    {sg.name}
                    {!sg.is_active && <span className="cam-stub-badge" style={{ marginLeft: 4 }}>inactive</span>}
                  </div>
                  <div className="list-card-sub">
                    {sg.kind} · {(+sg.lat).toFixed(4)}, {(+sg.lon).toFixed(4)}
                    {sg.created_at && <> · added {new Date(sg.created_at).toLocaleDateString("en-US", { month: "short", day: "numeric" })}</>}
                  </div>
                </div>
                <div className="list-card-actions">
                  <button className="icon-btn" title={sg.is_active ? "Disable" : "Enable"} onClick={() => toggleSign(sg)}>
                    {sg.is_active ? <Eye size={15} /> : <EyeOff size={15} />}
                  </button>
                  <button className="icon-btn" title="Delete"
                    onClick={async () => { await api(`/sign/${sg.id}`, { method: "DELETE" }); reload(); }}>
                    <Trash2 size={15} />
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

export default ZonesTabPage;
