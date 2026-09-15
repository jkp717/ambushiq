import { useState } from "react";
import { Save } from "lucide-react";
import { api } from "../services/api.js";
import Field from "./ui/Field.jsx";

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

export default HomeSetup;
