import { useState } from "react";
import { Save, X } from "lucide-react";
import Modal from "./ui/Modal.jsx";
import Field from "./ui/Field.jsx";

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

function FoodZonePrompt({ onConfirm, onCancel }) {
  const [name, setName] = useState("");
  const [quality, setQuality] = useState(5);
  const qualityLabel = quality <= 2 ? "Poor — low-value browse" : quality <= 4 ? "Below average" : quality <= 6 ? "Average food source" : quality <= 8 ? "Good — established plot or ag field" : "Premium — high-draw destination";
  return (
    <Modal onClose={onCancel}>
      <div className="card" style={{ padding: 16, border: "2px solid var(--green)" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
          <strong>Name this food zone</strong>
          <button className="icon-btn" onClick={onCancel}><X size={16} /></button>
        </div>
        <Field label="Name (optional)">
          <input autoFocus value={name} onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") onConfirm(name.trim(), quality); }}
            placeholder="e.g. North food plot" />
        </Field>
        <div style={{ marginTop: 14 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
            <label style={{ fontSize: 13, fontWeight: 500 }}>Food quality</label>
            <span style={{ fontSize: 13, fontWeight: 600, color: "var(--green)" }}>{quality}/10</span>
          </div>
          <input type="range" min={1} max={10} value={quality} onChange={(e) => setQuality(+e.target.value)} style={{ width: "100%" }} />
          <div style={{ fontSize: 11, color: "var(--sub)", marginTop: 4 }}>{qualityLabel}</div>
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10.5, color: "var(--sub)", marginTop: 2 }}>
            <span>Poor</span><span>Premium</span>
          </div>
        </div>
        <div style={{ display: "flex", gap: 8, marginTop: 16 }}>
          <button className="btn btn-primary" onClick={() => onConfirm(name.trim(), quality)}><Save size={15} /> Save</button>
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
  const [width, setWidth] = useState("");
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
            onKeyDown={(e) => { if (e.key === "Enter") onConfirm(name.trim(), usage, falloff, width === "" ? null : +width); }}
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
        <div style={{ marginTop: 14 }}>
          <Field label="Corridor width (m) — leave blank for a thin travel line">
            <input type="number" value={width} min={0} max={400} step={10}
              placeholder="e.g. 30 (creek bottom)"
              onChange={(e) => setWidth(e.target.value)} />
          </Field>
        </div>
        <div style={{ display: "flex", gap: 8, marginTop: 16 }}>
          <button className="btn btn-primary" onClick={() => onConfirm(name.trim(), usage, falloff, width === "" ? null : +width)}><Save size={15} /> Save</button>
          <button className="btn" onClick={onCancel}>Cancel</button>
        </div>
      </div>
    </Modal>
  );
}

export { NamePrompt, FoodZonePrompt, CorridorPrompt };
