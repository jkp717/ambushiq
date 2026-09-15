import { useState } from "react";
import { Save, X } from "lucide-react";
import { api } from "../services/api.js";
import Field from "./ui/Field.jsx";

function CameraEditor({ cam, providers, stands, onSaved, onCancel }) {
  const [name, setName] = useState(cam.name || "");
  const [standId, setStandId] = useState(cam.stand_id != null ? String(cam.stand_id) : "");
  const [isActive, setIsActive] = useState(cam.is_active);
  const [updateCreds, setUpdateCreds] = useState(false);
  const [creds, setCreds] = useState({});
  const [err, setErr] = useState(null);
  const [saving, setSaving] = useState(false);
  const prov = providers.find((p) => p.brand === cam.brand);

  async function save() {
    setSaving(true); setErr(null);
    try {
      const body = {
        name: name.trim() || cam.name,
        // Explicitly send null to unassign; server uses model_fields_set to detect intent
        stand_id: standId ? +standId : null,
        is_active: isActive,
      };
      if (updateCreds && prov?.implemented) body.credentials = creds;
      await api(`/cameras/${cam.id}`, { method: "PUT", body: JSON.stringify(body) });
      onSaved();
    } catch (e) { setErr(e.message); }
    finally { setSaving(false); }
  }

  return (
    <div className="card" style={{ padding: 20, border: "2px solid var(--navy)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
        <strong>Edit camera</strong>
        <button className="icon-btn" onClick={onCancel}><X size={16} /></button>
      </div>
      <Field label="Name">
        <input value={name} onChange={(e) => setName(e.target.value)} />
      </Field>
      <div style={{ marginTop: 12 }}>
        <Field label="Assigned stand">
          <select value={standId} onChange={(e) => setStandId(e.target.value)}>
            <option value="">— unassigned —</option>
            {stands.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </Field>
      </div>
      <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, cursor: "pointer", marginTop: 12 }}>
        <input type="checkbox" checked={isActive} onChange={(e) => setIsActive(e.target.checked)} />
        Active (syncs on schedule)
      </label>
      {prov?.implemented && (
        <div style={{ marginTop: 12 }}>
          <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, cursor: "pointer", marginBottom: 8 }}>
            <input type="checkbox" checked={updateCreds} onChange={(e) => setUpdateCreds(e.target.checked)} />
            Update cloud credentials
          </label>
          {updateCreds && prov.credential_fields.map((field) => (
            <div key={field} style={{ marginBottom: 8 }}>
              <Field label={field.charAt(0).toUpperCase() + field.slice(1)}>
                <input type={field === "password" ? "password" : "text"}
                  value={creds[field] || ""} onChange={(e) => setCreds({ ...creds, [field]: e.target.value })}
                  placeholder={field} />
              </Field>
            </div>
          ))}
        </div>
      )}
      {err && <div style={{ color: "var(--red)", fontSize: 13, marginTop: 8 }}>{err}</div>}
      <div style={{ display: "flex", gap: 8, marginTop: 16 }}>
        <button className="btn btn-primary" disabled={saving} onClick={save}><Save size={15} /> {saving ? "Saving…" : "Save"}</button>
        <button className="btn" onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}

export default CameraEditor;
