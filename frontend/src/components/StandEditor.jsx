import { useState } from "react";
import { Mountain, Save, X, MapPin } from "lucide-react";
import { tokenStore, regionStore, api } from "../services/api.js";
import Field from "./ui/Field.jsx";
import DirPicker from "./ui/DirPicker.jsx";
import TerrainPanel from "./TerrainPanel.jsx";

function StandEditor({ stand, onSave, onCancel, reload, onMoveOnMap }) {
  const [s, setS] = useState({ ...stand });
  const [loading, setLoading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [statusText, setStatusText] = useState("");
  const [err, setErr] = useState(null);
  const [savedId, setSavedId] = useState(stand.id);
  const valid = s.name && s.lat !== "" && s.lon !== "" && !isNaN(+s.lat) && !isNaN(+s.lon);

  async function analyze() {
    if (!valid) return; 
    setLoading(true); 
    setErr(null); 
    setProgress(5); 
    setStatusText("Saving stand...");

    try {
      let id = savedId;
      const body = {
        name: s.name,
        lat: +s.lat,
        lon: +s.lon,
        is_active: s.is_active !== false,
        downhill_deg: s.downhill_deg,
        deer_approach_deg: s.deer_approach_deg,
        visibility_m: s.visibility_m,
      };

      if (!id) { 
        const created = await api("/stands", { method: "POST", body: JSON.stringify(body) }); 
        id = created.id; 
        setSavedId(id); 
      } else { 
        await api(`/stands/${id}`, { method: "PUT", body: JSON.stringify(body) }); 
      }

      setStatusText("Connecting to elevation service...");
      
      // Use the application's native tokenStore/regionStore helpers for consistency
      const tok = tokenStore.get();
      const regionId = regionStore.get();
      const res = await fetch(`/api/stands/${id}/terrain`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(tok ? { "Authorization": `Bearer ${tok}` } : {}),
          ...(regionId ? { "X-Region-Id": regionId } : {}),
        }
      });

      if (!res.ok) {
        const errJson = await res.json().catch(() => ({}));
        throw new Error(errJson.detail || `Terrain analysis failed (status ${res.status})`);
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop(); // Keep trailing incomplete line in buffer

        for (const line of lines) {
          if (!line.trim()) continue;
          const data = JSON.parse(line);
          
          if (data.error) throw new Error(data.error);
          if (data.progress != null) setProgress(data.progress);
          if (data.message) setStatusText(data.message);
          
          if (data.complete && data.terrain) {
            const updated = data.terrain;
            setS({ ...updated, lat: updated.lat, lon: updated.lon });
          }
        }
      }

      reload && reload();
    } catch (e) { 
      setErr(e.message || "Couldn't reach elevation source."); 
    } finally { 
      setLoading(false); 
    }
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
          <button className="btn" onClick={analyze} disabled={!valid || loading}><Mountain size={14} /> {loading ? "Analyzing…" : s.terrain ? "Re-analyze" : "Analyze terrain"}</button>
        </div>
        {/* Progress Bar UI */}
        {loading && (
          <div style={{ margin: "10px 0" }}>
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, color: "var(--sub)", marginBottom: 4 }}>
              <span>{statusText}</span>
              <span>{progress}%</span>
            </div>
            <div style={{ width: "100%", height: 6, background: "var(--surf)", borderRadius: 3, overflow: "hidden" }}>
              <div style={{ width: `${progress}%`, height: "100%", background: "var(--navy)", transition: "width 0.2s ease" }} />
            </div>
          </div>
        )}
        {err && <div style={{ fontSize: 12, color: "var(--amber)", marginBottom: 8 }}>{err}</div>}
        {s.terrain && !loading && <TerrainPanel t={s.terrain} />}
        <div style={{ marginTop: 10 }}>
          <DirPicker label="Downhill faces" value={s.downhill_deg} onChange={(d) => setS({ ...s, downhill_deg: d })} />
          <div style={{ fontSize: 11.5, color: "var(--sub)", marginTop: 4 }}>{s.terrain ? "Set from elevation grid — adjust if needed." : "Set by hand, or analyze terrain above."}</div>
        </div>
      </div>
      <div style={{ marginTop: 14, paddingTop: 12, borderTop: "1px solid var(--bord)" }}>
        <DirPicker label="Deer approach from (optional)" value={s.deer_approach_deg} onChange={(d) => setS({ ...s, deer_approach_deg: d })} allowNull />
      </div>
      <div style={{ marginTop: 14, paddingTop: 12, borderTop: "1px solid var(--bord)" }}>
        <Field label="Visibility / cover radius (m) — leave blank to use the corridor's or global falloff">
          <input type="number" value={s.visibility_m ?? ""} min={0} max={500} step={10}
            placeholder="e.g. 250 open hardwoods, 60 thick cover"
            onChange={(e) => setS({ ...s, visibility_m: e.target.value === "" ? null : +e.target.value })} />
        </Field>
      </div>
      <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, cursor: "pointer", marginTop: 14, paddingTop: 12, borderTop: "1px solid var(--bord)" }}>
        <input type="checkbox" checked={s.is_active !== false} style={{ maxWidth: 20 }} onChange={(e) => setS({ ...s, is_active: e.target.checked })} />
        Active — included in rankings and map scoring
      </label>
      <div style={{ display: "flex", gap: 8, marginTop: 16, flexWrap: "wrap" }}>
        <button className="btn btn-primary" disabled={!valid} onClick={() => onSave({ name: s.name, lat: +s.lat, lon: +s.lon, is_active: s.is_active !== false, downhill_deg: s.downhill_deg, deer_approach_deg: s.deer_approach_deg, visibility_m: s.visibility_m }, savedId)}>
          <Save size={15} /> Save stand
        </button>
        {onMoveOnMap && <button className="btn" onClick={() => { onMoveOnMap(savedId || stand.id); onCancel(); }}><MapPin size={14} /> Move on Map</button>}
        <button className="btn" onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}

export default StandEditor;
