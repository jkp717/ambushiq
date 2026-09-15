import { useState, useEffect } from "react";
import { Wheat, Trees, Footprints, Target, Plus, Eye, EyeOff, Edit3, Trash2, MapPin, Save, X } from "lucide-react";
import { api } from "../services/api.js";
import { corridorLengthFt } from "../utils/formatters.js";
import Empty from "../components/ui/Empty.jsx";
import Modal from "../components/ui/Modal.jsx";
import Field from "../components/ui/Field.jsx";
import MiniMap from "../components/MiniMap.jsx";

function ZonesTabPage({ zones, corridors, sign, reloadSign, onAdd, reloadZones, reloadCorridors,
                        editingZone, setEditingZone, editingCorridor, setEditingCorridor, onMoveOnMap }) {
  const [tab, setTab] = useState("food");
  useEffect(() => {
    if (editingCorridor) setTab("corridors");
  }, [editingCorridor]);
  useEffect(() => {
    if (editingZone) setTab(editingZone.kind === "bedding" ? "bedding" : "food");
  }, [editingZone]);
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
          editing={editingZone} setEditing={setEditingZone} onMoveOnMap={onMoveOnMap} />
      )}
      {tab === "corridors" && (
        <CorridorsPage corridors={corridors} onAdd={() => onAdd("corridor")}
          reload={reloadCorridors} editing={editingCorridor} setEditing={setEditingCorridor} onMoveOnMap={onMoveOnMap} />
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
function ZonesPage({ kind, zones, onAdd, reload, editing, setEditing, onMoveOnMap }) {
  const [geom, setGeom] = useState(null);
  const [editQuality, setEditQuality] = useState(5);
  useEffect(() => {
    if (editing) {
      setGeom({ lat: editing.lat, lon: editing.lon, radius_m: editing.radius_m });
      if (kind === "food") setEditQuality(editing.quality ?? 5);
    }
  }, [editing && editing.id]);
  async function save() {
    const g = geom || { lat: editing.lat, lon: editing.lon, radius_m: editing.radius_m };
    const body = { kind, name: editing.name || null, lat: g.lat, lon: g.lon, radius_m: +g.radius_m,
                   is_active: editing.is_active !== false };
    if (kind === "food") body.quality = editQuality;
    try {
      await api(`/zones/${editing.id}`, { method: "PUT", body: JSON.stringify(body) });
      setEditing(null); reload();
    } catch {}
  }
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
                {(+z.lat).toFixed(4)}, {(+z.lon).toFixed(4)} · {z.radius_m} m radius
                {kind === "food" && <> · Quality {z.quality ?? 5}/10</>}
              </div>
            </div>
            <div className="list-card-actions">
              <button className="icon-btn" title={z.is_active ? "Disable zone" : "Enable zone"}
                onClick={() => toggleZone(z)}>
                {z.is_active ? <Eye size={15} /> : <EyeOff size={15} />}
              </button>
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
            {kind === "food" && (
              <div style={{ marginTop: 14 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
                  <label style={{ fontSize: 13, fontWeight: 500 }}>Food quality</label>
                  <span style={{ fontSize: 13, fontWeight: 600, color: "var(--green)" }}>{editQuality}/10</span>
                </div>
                <input type="range" min={1} max={10} value={editQuality} onChange={(e) => setEditQuality(+e.target.value)} style={{ width: "100%" }} />
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10.5, color: "var(--sub)", marginTop: 2 }}>
                  <span>Poor</span><span>Premium</span>
                </div>
              </div>
            )}
            <div style={{ marginTop: 12 }}>
              <span style={{ fontSize: 12, color: "var(--sub)", display: "block", marginBottom: 4 }}>Drag the center to move · drag the edge to resize</span>
              <MiniMap kind={kind} editable height={240} feature={{ lat: editing.lat, lon: editing.lon, radius_m: editing.radius_m }} onChange={(g) => setGeom(g)} />
            </div>
            {geom && <div style={{ fontSize: 12, color: "var(--sub)", marginTop: 6 }}>{geom.lat.toFixed(5)}, {geom.lon.toFixed(5)} · {Math.round(geom.radius_m)} m radius</div>}
            <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, cursor: "pointer", marginTop: 14 }}>
              <input type="checkbox" checked={editing.is_active !== false}
                onChange={(e) => setEditing({ ...editing, is_active: e.target.checked })} />
              Active — contributes to stand rankings this season
            </label>
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
  const [editWidth, setEditWidth] = useState("");
  useEffect(() => {
    if (editing) {
      setGeom({ points: editing.points });
      setEditUsage(editing.usage ?? 5);
      setEditFalloff(editing.falloff_m != null ? editing.falloff_m : "");
      setEditWidth(editing.width_m != null ? editing.width_m : "");
    }
  }, [editing && editing.id]);
  async function save() {
    const pts = (geom && geom.points) || editing.points;
    try {
      await api(`/corridors/${editing.id}`, { method: "PUT", body: JSON.stringify({
        name: editing.name || null, points: pts,
        is_active: editing.is_active !== false,
        usage: editUsage,
        falloff_m: editFalloff !== "" && editFalloff != null ? +editFalloff : null,
        width_m: editWidth !== "" && editWidth != null ? +editWidth : null,
      }) });
      setEditing(null); reload();
    } catch {}
  }
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
                {c.falloff_m != null ? ` · ${Math.round(c.falloff_m)}m falloff` : " · global falloff"}
                {c.width_m ? ` · ${Math.round(c.width_m)}m wide` : ""}
              </div>
            </div>
            <div className="list-card-actions">
              <button className="icon-btn" title={c.is_active ? "Disable corridor" : "Enable corridor"}
                onClick={() => toggleCorridor(c)}>
                {c.is_active ? <Eye size={15} /> : <EyeOff size={15} />}
              </button>
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
            {/* Corridor width */}
            <div style={{ marginTop: 14 }}>
              <Field label="Corridor width (m) — leave blank for a thin travel line">
                <input type="number" value={editWidth} min={0} max={400} step={10}
                  placeholder="e.g. 30 (creek bottom)"
                  onChange={(e) => setEditWidth(e.target.value)} />
              </Field>
            </div>
            <div style={{ marginTop: 12 }}>
              <span style={{ fontSize: 12, color: "var(--sub)", display: "block", marginBottom: 4 }}>Drag any point to reshape the path</span>
              <MiniMap kind="corridor" editable height={240} feature={{ points: editing.points }} onChange={(g) => setGeom(g)} />
            </div>
            <div style={{ fontSize: 12, color: "var(--sub)", marginTop: 6 }}>{((geom && geom.points) || editing.points).length} points</div>
            <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, cursor: "pointer", marginTop: 14 }}>
              <input type="checkbox" checked={editing.is_active !== false}
                onChange={(e) => setEditing({ ...editing, is_active: e.target.checked })} />
              Active — contributes to stand rankings this season
            </label>
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
