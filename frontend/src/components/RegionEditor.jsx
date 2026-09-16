import { useState } from "react";
import { Save } from "lucide-react";
import { api } from "../services/api.js";
import Field from "./ui/Field.jsx";

const MONTHS = ["January", "February", "March", "April", "May", "June", "July",
  "August", "September", "October", "November", "December"];

// Keep in sync with backend app/regions/schemas.py's REGION_NAME_MAX_LEN.
const NAME_MAX_LEN = 60;

function RegionEditor({ region, onSaved, onCancel }) {
  const [name, setName] = useState(region?.name ?? "");
  const [lat, setLat] = useState(region ? String(region.lat) : "");
  const [lon, setLon] = useState(region ? String(region.lon) : "");
  const [rutMonth, setRutMonth] = useState(region?.rut_peak_month ?? 12);
  const [rutDay, setRutDay] = useState(region?.rut_peak_day ?? 5);
  const [tz, setTz] = useState(region?.property_timezone ?? "America/Chicago");
  const [err, setErr] = useState(null);

  const valid = name.trim() !== "" && lat !== "" && lon !== "" && !isNaN(+lat) && !isNaN(+lon)
    && +lat >= -90 && +lat <= 90 && +lon >= -180 && +lon <= 180;

  async function save() {
    const body = {
      name: name.trim(), lat: +lat, lon: +lon,
      rut_peak_month: +rutMonth, rut_peak_day: +rutDay, property_timezone: tz,
    };
    try {
      const r = region?.id
        ? await api(`/regions/${region.id}`, { method: "PUT", body: JSON.stringify(body) })
        : await api("/regions", { method: "POST", body: JSON.stringify(body) });
      onSaved(r);
    } catch { setErr("Couldn't save. Check the values."); }
  }

  return (
    <div className="card" style={{ padding: 16, maxWidth: 460 }}>
      <strong style={{ fontSize: 15 }}>{region?.id ? "Edit region" : "Create a hunting region"}</strong>
      <p style={{ fontSize: 13, color: "var(--sub)", margin: "8px 0 12px" }}>
        {region?.id
          ? "Update this region's center and settings."
          : "Give this hunting region a name and center point — you can add stands, zones, and cameras once it's created."}
      </p>
      <Field label="Name"><input value={name} onChange={(e) => setName(e.target.value)} placeholder="Home Farm" maxLength={NAME_MAX_LEN} /></Field>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginTop: 10 }}>
        <Field label="Latitude"><input value={lat} onChange={(e) => setLat(e.target.value)} placeholder="34.7465" inputMode="decimal" /></Field>
        <Field label="Longitude"><input value={lon} onChange={(e) => setLon(e.target.value)} placeholder="-92.2896" inputMode="decimal" /></Field>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginTop: 10 }}>
        <Field label="Peak rut month">
          <select value={rutMonth} onChange={(e) => setRutMonth(+e.target.value)}>
            {MONTHS.map((m, i) => <option key={i + 1} value={i + 1}>{m}</option>)}
          </select>
        </Field>
        <Field label="Peak rut day">
          <select value={rutDay} onChange={(e) => setRutDay(+e.target.value)}>
            {Array.from({ length: 31 }, (_, i) => i + 1).map((d) => <option key={d} value={d}>{d}</option>)}
          </select>
        </Field>
      </div>
      <div style={{ marginTop: 10 }}>
        <Field label="Timezone">
          <select value={tz} onChange={(e) => setTz(e.target.value)}>
            <option value="America/New_York">Eastern — New York, Atlanta, Miami (ET)</option>
            <option value="America/Chicago">Central — Chicago, Dallas, Kansas City (CT)</option>
            <option value="America/Denver">Mountain — Denver, Salt Lake City (MT)</option>
            <option value="America/Phoenix">Mountain no-DST — Phoenix (MST year-round)</option>
            <option value="America/Los_Angeles">Pacific — Los Angeles, Seattle (PT)</option>
            <option value="America/Anchorage">Alaska (AKT)</option>
            <option value="Pacific/Honolulu">Hawaii (no DST)</option>
          </select>
        </Field>
      </div>
      {err && <div style={{ fontSize: 12, color: "var(--red)", marginTop: 8 }}>{err}</div>}
      <div style={{ display: "flex", gap: 8, marginTop: 14 }}>
        <button className="btn btn-primary" disabled={!valid} onClick={save}>
          <Save size={15} /> {region?.id ? "Save" : "Create region"}
        </button>
        {onCancel && <button className="btn" onClick={onCancel}>Cancel</button>}
      </div>
    </div>
  );
}

export default RegionEditor;
