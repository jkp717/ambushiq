import { useState } from "react";
import { Save, X, MapPin } from "lucide-react";
import { api } from "../services/api.js";
import { ydInput, ydFromInput } from "../utils/units.js";
import Field from "./ui/Field.jsx";

// Edit a corridor's details. Its path is reshaped on the main map ("Edit on map").
function CorridorEditor({ corridor, onSaved, onCancel, onEditOnMap }) {
  const [c, setC] = useState({ ...corridor });
  const [usage, setUsage] = useState(corridor.usage ?? 5);
  const [falloff, setFalloff] = useState(ydInput(corridor.falloff_m));   // inputs are in yards
  const [width, setWidth] = useState(ydInput(corridor.width_m));

  async function save() {
    try {
      await api(`/corridors/${corridor.id}`, { method: "PUT", body: JSON.stringify({
        name: c.name || null, points: corridor.points,
        is_active: c.is_active !== false,
        usage,
        falloff_m: ydFromInput(falloff),
        width_m: ydFromInput(width),
      }) });
      onSaved();
    } catch {}
  }

  return (
    <div className="card" style={{ padding: 16, border: "2px solid var(--navy)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
        <strong>Edit corridor</strong>
        <button className="icon-btn" onClick={onCancel}><X size={16} /></button>
      </div>
      <Field label="Name"><input value={c.name || ""} onChange={(e) => setC({ ...c, name: e.target.value })} placeholder="corridor" /></Field>
      {/* Usage frequency */}
      <div style={{ marginTop: 14 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
          <label style={{ fontSize: 13, fontWeight: 500 }}>Usage frequency</label>
          <span style={{ fontSize: 13, fontWeight: 600, color: "var(--navy)" }}>{usage}/10</span>
        </div>
        <input type="range" min={1} max={10} value={usage} onChange={(e) => setUsage(+e.target.value)} style={{ width: "100%" }} />
        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10.5, color: "var(--sub)", marginTop: 2 }}>
          <span>Rarely used</span><span>Heavily used</span>
        </div>
      </div>
      {/* Per-corridor falloff */}
      <div style={{ marginTop: 14 }}>
        <Field label="Falloff distance (yd) — leave blank to use global setting">
          <input type="number" value={falloff} min={50} max={2200} step={25}
            placeholder="global default"
            onChange={(e) => setFalloff(e.target.value)} />
        </Field>
      </div>
      {/* Corridor width */}
      <div style={{ marginTop: 14 }}>
        <Field label="Corridor width (yd) — leave blank for a thin travel line">
          <input type="number" value={width} min={0} max={440} step={10}
            placeholder="e.g. 35 (creek bottom)"
            onChange={(e) => setWidth(e.target.value)} />
        </Field>
      </div>
      <div style={{ fontSize: 12, color: "var(--sub)", marginTop: 12 }}>{corridor.points.length} points</div>
      <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, cursor: "pointer", marginTop: 14 }}>
        <input type="checkbox" checked={c.is_active !== false}
          onChange={(e) => setC({ ...c, is_active: e.target.checked })} />
        Active — contributes to stand rankings this season
      </label>
      <div style={{ display: "flex", gap: 8, marginTop: 16, flexWrap: "wrap" }}>
        <button className="btn btn-primary" onClick={save}><Save size={15} /> Save</button>
        {onEditOnMap && <button className="btn" onClick={() => onEditOnMap("corridor", corridor.id)}><MapPin size={14} /> Edit on map</button>}
        <button className="btn" onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}

export default CorridorEditor;
