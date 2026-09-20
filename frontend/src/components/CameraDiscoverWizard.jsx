import { useState } from "react";
import { Camera, AlertTriangle, RefreshCw, X } from "lucide-react";
import { api } from "../services/api.js";
import { BRAND_LABELS } from "../utils/cameraBrands.js";
import Field from "./ui/Field.jsx";

function CameraDiscoverWizard({ providers, onSaved, onCancel }) {
  const [step, setStep] = useState(1);
  const [brand, setBrand] = useState(null);
  const [creds, setCreds] = useState({});
  const [discovering, setDiscovering] = useState(false);
  const [preview, setPreview] = useState(null);
  const [selections, setSelections] = useState({});
  const [applying, setApplying] = useState(false);
  const [result, setResult] = useState(null);
  const [err, setErr] = useState(null);
  const prov = providers.find((p) => p.brand === brand);

  async function discover() {
    setDiscovering(true); setErr(null);
    try {
      const r = await api("/cameras/discover", { method: "POST",
        body: JSON.stringify({ brand, credentials: creds }) });
      setPreview(r);
      const sel = {};
      r.cameras.forEach((c) => { sel[c.provider_ref] = c.status !== "previously_removed"; });
      setSelections(sel);
      setStep(3);
    } catch (e) { setErr(e.message); }
    finally { setDiscovering(false); }
  }

  async function confirmSelections() {
    setApplying(true); setErr(null);
    try {
      const r = await api("/cameras/discover", { method: "POST",
        body: JSON.stringify({ brand, credentials: creds, selections }) });
      setResult(r);
    } catch (e) { setErr(e.message); }
    finally { setApplying(false); }
  }

  const STEP_LABELS = ["Brand", "Connect", "Done"];
  return (
    <div className="card" style={{ padding: 20, border: "2px solid var(--navy)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
        <strong>Connect trail cameras</strong>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div className="wizard-steps">
            {STEP_LABELS.map((l, i) => (
              <span key={i} className={"wizard-step" + (step === i + 1 ? " active" : "")} title={l}>{i + 1}</span>
            ))}
          </div>
          <button className="icon-btn" onClick={onCancel}><X size={16} /></button>
        </div>
      </div>

      {step === 1 && (
        <>
          <p style={{ fontSize: 13, color: "var(--sub)", marginBottom: 12 }}>Select your camera brand:</p>
          <div className="brand-grid">
            {providers.map((p) => (
              <button key={p.brand}
                className={"brand-btn" + (brand === p.brand ? " selected" : "")}
                onClick={() => setBrand(p.brand)}>
                <Camera size={20} />
                <span>{BRAND_LABELS[p.brand] || p.brand}</span>
                {!p.implemented && <span className="brand-stub">coming soon</span>}
              </button>
            ))}
          </div>
          {brand && !prov?.implemented && (
            <div className="cam-not-impl" style={{ marginTop: 12 }}>
              <AlertTriangle size={14} />
              <span><strong>{BRAND_LABELS[brand]}</strong> sync is not yet implemented.</span>
            </div>
          )}
          <div style={{ display: "flex", gap: 8, marginTop: 16 }}>
            <button className="btn btn-primary" disabled={!brand || !prov?.implemented} onClick={() => setStep(2)}>Next →</button>
            <button className="btn" onClick={onCancel}>Cancel</button>
          </div>
        </>
      )}

      {step === 2 && (
        <>
          <p style={{ fontSize: 13, color: "var(--sub)", marginBottom: 12 }}>
            Enter your <strong>{BRAND_LABELS[brand]}</strong> account credentials. All cameras on
            your account will be imported automatically — then assign stands to each one.
          </p>
          {prov.credential_fields.map((field) => (
            <div key={field} style={{ marginBottom: 10 }}>
              <Field label={field.charAt(0).toUpperCase() + field.slice(1)}>
                <input
                  type={field === "password" ? "password" : "text"}
                  value={creds[field] || ""}
                  onChange={(e) => setCreds({ ...creds, [field]: e.target.value })}
                  placeholder={field}
                  autoComplete={field === "password" ? "current-password" : "username"}
                />
              </Field>
            </div>
          ))}
          <div style={{ fontSize: 11.5, color: "var(--sub)", marginTop: 4, marginBottom: 12 }}>
            🔒 Credentials are encrypted at rest using your server secret.
          </div>
          {err && <div style={{ color: "var(--red)", fontSize: 13, marginBottom: 8 }}>{err}</div>}
          <div style={{ display: "flex", gap: 8 }}>
            <button className="btn" onClick={() => setStep(1)}>← Back</button>
            <button className="btn btn-primary" disabled={discovering ||
              prov.credential_fields.some((f) => !creds[f]?.trim())} onClick={discover}>
              {discovering ? <><RefreshCw size={14} className="spin" /> Discovering...</> : <><Camera size={14} /> Discover cameras</>}
            </button>
          </div>
        </>
      )}

      {step === 3 && preview && !result && (
        <>
          <p style={{ fontSize: 13, color: "var(--sub)", marginBottom: 10 }}>
            {preview.cameras.length === 0
              ? "No cameras found on this account."
              : "Choose which cameras to import. Previously removed cameras are unchecked by default — check one to bring it back."}
          </p>
          {preview.cameras.map((c) => (
            <label key={c.provider_ref} style={{ display: "flex", alignItems: "center", gap: 8, padding: "6px 0", cursor: "pointer" }}>
              <input type="checkbox" checked={!!selections[c.provider_ref]}
                onChange={(e) => setSelections({ ...selections, [c.provider_ref]: e.target.checked })} />
              <span style={{ fontSize: 13 }}>{c.name}</span>
              {c.status === "previously_removed" && (
                <span style={{ fontSize: 11, color: "var(--amber)" }}>previously removed</span>
              )}
              {c.status === "existing" && (
                <span style={{ fontSize: 11, color: "var(--sub)" }}>already connected</span>
              )}
            </label>
          ))}
          {err && <div style={{ color: "var(--red)", fontSize: 13, marginTop: 8 }}>{err}</div>}
          <div style={{ display: "flex", gap: 8, marginTop: 16 }}>
            <button className="btn" onClick={() => setStep(2)}>← Back</button>
            {preview.cameras.length > 0 ? (
              <button className="btn btn-primary" disabled={applying} onClick={confirmSelections}>
                {applying ? <><RefreshCw size={14} className="spin" /> Importing...</> : "Import selected"}
              </button>
            ) : (
              <button className="btn btn-primary" onClick={onSaved}>Done</button>
            )}
          </div>
        </>
      )}

      {step === 3 && result && (
        <>
          <div style={{ fontSize: 14, marginBottom: 12 }}><strong>Discovery complete!</strong></div>
          {result.created.length > 0 && (
            <div style={{ marginBottom: 8 }}>
              <div style={{ fontSize: 12, fontWeight: 600, color: "var(--green)", marginBottom: 4 }}>
                {result.created.length} new camera(s) added:
              </div>
              {result.created.map((c) => (
                <div key={c.id} style={{ fontSize: 13, paddingLeft: 12, color: "var(--txt)" }}>
                  - {c.name} <span style={{ color: "var(--sub)", fontSize: 11 }}>(assign a stand via Edit)</span>
                </div>
              ))}
            </div>
          )}
          {result.restored.length > 0 && (
            <div style={{ marginBottom: 8 }}>
              <div style={{ fontSize: 12, fontWeight: 600, color: "var(--green)", marginBottom: 4 }}>
                {result.restored.length} restored (previously removed):
              </div>
              {result.restored.map((c) => (
                <div key={c.id} style={{ fontSize: 13, paddingLeft: 12, color: "var(--txt)" }}>- {c.name}</div>
              ))}
            </div>
          )}
          {result.updated.length > 0 && (
            <div style={{ marginBottom: 8 }}>
              <div style={{ fontSize: 12, fontWeight: 600, color: "var(--sub)", marginBottom: 4 }}>
                {result.updated.length} already connected:
              </div>
              {result.updated.map((c) => (
                <div key={c.id} style={{ fontSize: 13, paddingLeft: 12, color: "var(--sub)" }}>- {c.name}</div>
              ))}
            </div>
          )}
          {result.skipped.length > 0 && (
            <div style={{ marginBottom: 8 }}>
              <div style={{ fontSize: 12, fontWeight: 600, color: "var(--amber)", marginBottom: 4 }}>
                {result.skipped.length} left removed (unchecked):
              </div>
              {result.skipped.map((c) => (
                <div key={c.provider_ref} style={{ fontSize: 13, paddingLeft: 12, color: "var(--sub)" }}>- {c.name}</div>
              ))}
            </div>
          )}
          {result.created.length === 0 && result.updated.length === 0 && result.restored.length === 0 && (
            <p style={{ fontSize: 13, color: "var(--sub)" }}>No cameras were imported.</p>
          )}
          <button className="btn btn-primary" style={{ marginTop: 12 }} onClick={onSaved}>Done</button>
        </>
      )}
    </div>
  );
}

export default CameraDiscoverWizard;
