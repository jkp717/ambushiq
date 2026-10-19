import { useState } from "react";
import { Save, X, MapPin } from "lucide-react";
import { api } from "../services/api.js";
import { fmtYd } from "../utils/units.js";
import Field from "./ui/Field.jsx";

// Edit a food or bedding zone's details. Its position and size are changed on the main map ("Edit on map").
function ZoneEditor({ zone, onSaved, onCancel, onEditOnMap }) {
  const [z, setZ] = useState({ ...zone });
  const [quality, setQuality] = useState(zone.quality ?? 5);
  const kind = zone.kind;
  const noun = kind === "food" ? "food zone" : "bedding zone";

  async function save() {
    const body = { kind, name: z.name || null, lat: zone.lat, lon: zone.lon, radius_m: +zone.radius_m,
                   is_active: z.is_active !== false };
    if (kind === "food") body.quality = quality;
    try {
      await api(`/zones/${zone.id}`, { method: "PUT", body: JSON.stringify(body) });
      onSaved();
    } catch {}
  }

  return (
    <div className="card" style={{ padding: 16, border: "2px solid var(--navy)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
        <strong>Edit {noun}</strong>
        <button className="icon-btn" onClick={onCancel}><X size={16} /></button>
      </div>
      <Field label="Name"><input value={z.name || ""} onChange={(e) => setZ({ ...z, name: e.target.value })} placeholder={noun} /></Field>
      {kind === "food" && (
        <div style={{ marginTop: 14 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
            <label style={{ fontSize: 13, fontWeight: 500 }}>Food quality</label>
            <span style={{ fontSize: 13, fontWeight: 600, color: "var(--green)" }}>{quality}/10</span>
          </div>
          <input type="range" min={1} max={10} value={quality} onChange={(e) => setQuality(+e.target.value)} style={{ width: "100%" }} />
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10.5, color: "var(--sub)", marginTop: 2 }}>
            <span>Poor</span><span>Premium</span>
          </div>
        </div>
      )}
      <div style={{ fontSize: 12, color: "var(--sub)", marginTop: 12 }}>
        {(+zone.lat).toFixed(5)}, {(+zone.lon).toFixed(5)} · {fmtYd(zone.radius_m)} radius
      </div>
      <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, cursor: "pointer", marginTop: 14 }}>
        <input type="checkbox" checked={z.is_active !== false}
          onChange={(e) => setZ({ ...z, is_active: e.target.checked })} />
        Active — contributes to stand rankings this season
      </label>
      <div style={{ display: "flex", gap: 8, marginTop: 16, flexWrap: "wrap" }}>
        <button className="btn btn-primary" onClick={save}><Save size={15} /> Save</button>
        {onEditOnMap && <button className="btn" onClick={() => onEditOnMap(kind, zone.id)}><MapPin size={14} /> Edit on map</button>}
        <button className="btn" onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}

export default ZoneEditor;
