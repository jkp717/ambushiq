import { useState, useEffect } from "react";
import { Mountain, Save, X, MapPin } from "lucide-react";
import { tokenStore, regionStore, api } from "../services/api.js";
import Field from "./ui/Field.jsx";
import DirPicker from "./ui/DirPicker.jsx";
import TerrainPanel from "./TerrainPanel.jsx";
import { ydInput, ydFromInput } from "../utils/units.js";
import { STAND_TYPES, STAND_COLORS, standColor, standGlyph } from "../utils/standStyle.js";

function StandEditor({ stand, onSave, onCancel, reload, onMoveOnMap }) {
  const [s, setS] = useState({ ...stand });
  const [loading, setLoading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [statusText, setStatusText] = useState("");
  const [err, setErr] = useState(null);
  const [savedId, setSavedId] = useState(stand.id);
  const [outside, setOutside] = useState(false);   // the property terrain grid can't supply this spot
  const valid = s.name && s.lat !== "" && s.lon !== "" && !isNaN(+s.lat) && !isNaN(+s.lon);
  const hasPoint = s.lat !== "" && s.lon !== "" && s.lat != null && s.lon != null && !isNaN(+s.lat) && !isNaN(+s.lon);
  const fromProperty = s.terrain?.basis === "property";

  // Terrain comes from the region's property-wide grid: show it as soon as a point is chosen, before
  // saving. Outside the grid the server analyzes the stand's own terrain automatically once it's saved.
  useEffect(() => {
    if (s.terrain || !hasPoint) return undefined;
    let cancel = false;
    const t = setTimeout(() => {
      api(`/stands/terrain-preview?lat=${+s.lat}&lon=${+s.lon}`).then((tr) => {
        if (cancel) return;
        if (tr.outside) { setOutside(true); return; }
        setOutside(false);
        setS((prev) => ({ ...prev, terrain: tr,
                          downhill_deg: prev.downhill_deg ?? (tr.flat ? null : tr.downhill_deg) }));
      }).catch(() => {});
    }, 400);
    return () => { cancel = true; clearTimeout(t); };
  }, [s.lat, s.lon, !!s.terrain]); // eslint-disable-line react-hooks/exhaustive-deps

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
        stand_type: s.stand_type || "tree",
        color: s.color || null,
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
            setS((prev) => ({ ...updated, lat: updated.lat, lon: updated.lon,
                              stand_type: prev.stand_type, color: prev.color }));
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
      <div style={{ marginTop: 10 }}>
        <div style={{ fontSize: 13, color: "var(--sub)", marginBottom: 6 }}>Stand type</div>
        <div className="grid-dir">
          {STAND_TYPES.map(({ key, label }) => (
            <button key={key} type="button" className={"chip" + ((s.stand_type || "tree") === key ? " on" : "")}
              style={{ display: "inline-flex", alignItems: "center", gap: 6 }}
              onClick={() => setS({ ...s, stand_type: key })}>
              <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true"
                dangerouslySetInnerHTML={{ __html: standGlyph(key, "currentColor") }} />
              {label}
            </button>
          ))}
        </div>
      </div>
      <div style={{ marginTop: 10 }}>
        <div style={{ fontSize: 13, color: "var(--sub)", marginBottom: 6 }}>Pin color</div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
          {STAND_COLORS.map((c, i) => (
            <button key={c} type="button" aria-label={`Pin color ${c}`}
              className={"scout-swatch" + (standColor(s) === c ? " on" : "")} style={{ background: c, width: 26, height: 26 }}
              onClick={() => setS({ ...s, color: i === 0 ? null : c })} />
          ))}
        </div>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginTop: 10 }}>
        <Field label="Latitude"><input value={s.lat} onChange={(e) => { setOutside(false); setS({ ...s, lat: e.target.value, terrain: null }); }} placeholder="34.7465" inputMode="decimal" /></Field>
        <Field label="Longitude"><input value={s.lon} onChange={(e) => { setOutside(false); setS({ ...s, lon: e.target.value, terrain: null }); }} placeholder="-92.2896" inputMode="decimal" /></Field>
      </div>
      <div style={{ marginTop: 14, paddingTop: 12, borderTop: "1px solid var(--bord)" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
          <span style={{ fontSize: 13, color: "var(--sub)" }}>Terrain & drainage</span>
          {!fromProperty && (outside || s.terrain) && (
            <button className="btn" onClick={analyze} disabled={!valid || loading}><Mountain size={14} /> {loading ? "Analyzing…" : s.terrain ? "Re-analyze" : "Analyze terrain"}</button>
          )}
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
        {!loading && (fromProperty || outside || s.terrain) && (
          <div style={{ fontSize: 11.5, color: "var(--sub)", marginTop: 6 }}>
            {fromProperty ? "From the property terrain grid."
              : s.terrain ? "Outside the property terrain grid — from this stand's own terrain analysis."
              : "Outside the property terrain grid — this stand's own terrain is analyzed automatically when saved."}
          </div>
        )}
        <div style={{ marginTop: 10 }}>
          <DirPicker label="Downhill faces" value={s.downhill_deg} onChange={(d) => setS({ ...s, downhill_deg: d })} />
          <div style={{ fontSize: 11.5, color: "var(--sub)", marginTop: 4 }}>{s.terrain ? "Set from elevation grid — adjust if needed." : "Set by hand, or let the terrain analysis fill it in."}</div>
        </div>
      </div>
      <div style={{ marginTop: 14, paddingTop: 12, borderTop: "1px solid var(--bord)" }}>
        <DirPicker label="Deer approach from (optional)" value={s.deer_approach_deg} onChange={(d) => setS({ ...s, deer_approach_deg: d })} allowNull />
      </div>
      <div style={{ marginTop: 14, paddingTop: 12, borderTop: "1px solid var(--bord)" }}>
        <Field label="Visibility / cover radius (yd) — leave blank to use the corridor's or global falloff">
          <input type="number" value={ydInput(s.visibility_m)} min={0} max={550} step={10}
            placeholder="e.g. 275 open hardwoods, 65 thick cover"
            onChange={(e) => setS({ ...s, visibility_m: ydFromInput(e.target.value) })} />
        </Field>
      </div>
      <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, cursor: "pointer", marginTop: 14, paddingTop: 12, borderTop: "1px solid var(--bord)" }}>
        <input type="checkbox" checked={s.is_active !== false} onChange={(e) => setS({ ...s, is_active: e.target.checked })} />
        Active — included in rankings and map scoring
      </label>
      <div style={{ display: "flex", gap: 8, marginTop: 16, flexWrap: "wrap" }}>
        <button className="btn btn-primary" disabled={!valid} onClick={() => onSave({ name: s.name, lat: +s.lat, lon: +s.lon, is_active: s.is_active !== false, downhill_deg: s.downhill_deg, deer_approach_deg: s.deer_approach_deg, visibility_m: s.visibility_m, stand_type: s.stand_type || "tree", color: s.color || null }, savedId)}>
          <Save size={15} /> Save stand
        </button>
        {onMoveOnMap && <button className="btn" onClick={() => { onMoveOnMap(savedId || stand.id); onCancel(); }}><MapPin size={14} /> Move on Map</button>}
        <button className="btn" onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}

export default StandEditor;
