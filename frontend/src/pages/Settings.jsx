import { useState, useEffect } from "react";
import { MapPin, Save, Footprints, Wheat, Trees, Target, Thermometer, Camera, HardDrive } from "lucide-react";
import { api } from "../services/api.js";
import Banner from "../components/ui/Banner.jsx";
import Empty from "../components/ui/Empty.jsx";
import Field from "../components/ui/Field.jsx";
import SliderRow from "../components/ui/SliderRow.jsx";

function SettingsPage() {
  const [s, setS] = useState(null); const [saved, setSaved] = useState(false); const [err, setErr] = useState(null);
  const [homeLat, setHomeLat] = useState(""); const [homeLon, setHomeLon] = useState(""); const [homeSaved, setHomeSaved] = useState(false);
  useEffect(() => { api("/settings").then(setS).catch(() => setErr("Couldn't load settings.")); }, []);
  useEffect(() => { api("/home").then((h) => { if (h.set) { setHomeLat(String(h.lat)); setHomeLon(String(h.lon)); } }).catch(() => {}); }, []);

  async function save() {
    try { const r = await api("/settings", { method: "PUT", body: JSON.stringify(s) }); setS(r); setSaved(true); setTimeout(() => setSaved(false), 1800); }
    catch { setErr("Couldn't save settings."); }
  }
  function reset() { setS({ ...s, weight_corridor: 0.15, falloff_corridor: 150, weight_food: 0.15, falloff_food: 200, weight_bedding: 0.10, falloff_bedding: 250, weight_scrape: 0.12, falloff_scrape: 100, weight_rub: 0.10, falloff_rub: 80 }); }
  function resetRating() { setS({ ...s, rate_w_pressure: 0.32, rate_w_wind: 0.20, rate_w_rain: 0.28, rate_w_temp: 0.20 }); }
  function resetThermal() { setS({ ...s, thermal_wind_half_scale: 7.0, thermal_wind_exponent: 1.8, thermal_midday_discount: 0.3 }); }

  const homeValid = homeLat !== "" && homeLon !== "" && !isNaN(+homeLat) && !isNaN(+homeLon) && +homeLat >= -90 && +homeLat <= 90 && +homeLon >= -180 && +homeLon <= 180;
  async function saveHome() {
    try { await api("/home", { method: "PUT", body: JSON.stringify({ lat: +homeLat, lon: +homeLon }) }); setHomeSaved(true); setTimeout(() => setHomeSaved(false), 1800); }
    catch { setErr("Couldn't save."); }
  }

  if (!s) return <div style={{ padding: 16 }}>{err ? <Banner>{err}</Banner> : <Empty>Loading…</Empty>}</div>;

  const PROX_TYPES = [
    { key: "corridor", label: "Deer corridors", icon: Footprints, color: "#A35A1B" },
    { key: "food",     label: "Food zones",     icon: Wheat,      color: "var(--green)" },
    { key: "bedding",  label: "Bedding zones",  icon: Trees,      color: "#6B4FA0" },
    { key: "scrape",   label: "Scrapes",        icon: Target,     color: "#E87800" },
    { key: "rub",      label: "Rubs",           icon: Target,     color: "#8B3A1A" },
  ];
  const RW = [
    { key: "rate_w_pressure", label: "Barometric pressure" },
    { key: "rate_w_wind",     label: "Wind" },
    { key: "rate_w_rain",     label: "Rain" },
    { key: "rate_w_temp",     label: "Temperature shift" },
  ];
  const sum = RW.reduce((a, r) => a + (s[r.key] ?? 0), 0) || 1;

  return (
    <div className="settings-page">
      {err && <Banner>{err}</Banner>}

      <div className="settings-section">
        <div className="settings-section-hd"><MapPin size={16} color="var(--navy)" /><strong>Hunt region</strong></div>
        <p className="settings-desc">Centers the map before you've placed any stands.</p>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr auto", gap: 10, alignItems: "end" }}>
          <Field label="Latitude"><input value={homeLat} onChange={(e) => setHomeLat(e.target.value)} placeholder="34.7465" inputMode="decimal" /></Field>
          <Field label="Longitude"><input value={homeLon} onChange={(e) => setHomeLon(e.target.value)} placeholder="-92.2896" inputMode="decimal" /></Field>
          <button className="btn btn-primary" disabled={!homeValid} onClick={saveHome} style={{ height: 38 }}><Save size={15} /> {homeSaved ? "Saved" : "Save"}</button>
        </div>
      </div>

      <div className="settings-section-title">Proximity weights</div>
      <p className="settings-desc">Stands near these features get a ranking boost. <b>Weight</b> = max bonus; <b>falloff</b> = meters beyond which a feature stops helping.</p>
      {PROX_TYPES.map(({ key, label, icon: Icon, color }) => (
        <div key={key} className="settings-section">
          <div className="settings-section-hd"><Icon size={15} color={color} /><strong>{label}</strong></div>
          <SliderRow label="Weight (max bonus)" min={0} max={0.5} step={0.01} value={s[`weight_${key}`] ?? 0} display={(s[`weight_${key}`] ?? 0).toFixed(2)} onChange={(v) => setS({ ...s, [`weight_${key}`]: v })} />
          <SliderRow label="Falloff distance" min={25} max={600} step={25} value={s[`falloff_${key}`] ?? 100} display={`${Math.round(s[`falloff_${key}`] ?? 100)} m`} onChange={(v) => setS({ ...s, [`falloff_${key}`]: v })} />
        </div>
      ))}
      <div style={{ display: "flex", gap: 8, marginBottom: 24 }}>
        <button className="btn btn-primary" onClick={save}><Save size={15} /> {saved ? "Saved ✓" : "Save"}</button>
        <button className="btn" onClick={reset}>Reset defaults</button>
      </div>

      <div className="settings-section-title" style={{ borderTop: "1px solid var(--bord)", paddingTop: 20 }}>Daily rating — weather weights</div>
      <p className="settings-desc">Tunes the 1–5 daily movement rating. Values are relative — they balance against each other automatically.</p>
      <div className="settings-section">
        {RW.map((r) => (
          <SliderRow key={r.key} label={`${r.label} — ${Math.round((s[r.key] ?? 0) / sum * 100)}%`}
            min={0} max={1} step={0.01} value={s[r.key] ?? 0}
            display={(s[r.key] ?? 0).toFixed(2)} onChange={(v) => setS({ ...s, [r.key]: v })} />
        ))}
      </div>
      <div style={{ display: "flex", gap: 8 }}>
        <button className="btn btn-primary" onClick={save}><Save size={15} /> {saved ? "Saved ✓" : "Save"}</button>
        <button className="btn" onClick={resetRating}>Reset rating weights</button>
      </div>

      {/* ── Thermal model ── */}
      <div className="settings-section-title" style={{ borderTop: "1px solid var(--bord)", paddingTop: 20, marginTop: 24 }}>
        <Thermometer size={15} color="var(--navy)" style={{ verticalAlign: "text-bottom" }} /> Thermal model
      </div>
      <p className="settings-desc">
        Controls how much a stand's thermal drainage/updraft actually drives the
        blended scent direction, versus wind taking over. Thermals are strongest and
        cleanest on calm mornings/evenings; wind and midday mixing crowd them out.
      </p>
      <div className="settings-section">
        <SliderRow label="Wind fade point" min={2} max={20} step={0.5}
          value={s.thermal_wind_half_scale ?? 7.0}
          display={`${(s.thermal_wind_half_scale ?? 7.0).toFixed(1)} mph`}
          onChange={(v) => setS({ ...s, thermal_wind_half_scale: v })}
          info="Wind speed at which the thermal's pull on scent direction is cut about in half. Lower = wind takes over at lighter breezes." />
        <SliderRow label="Wind fade sharpness" min={1.0} max={3.0} step={0.1}
          value={s.thermal_wind_exponent ?? 1.8}
          display={(s.thermal_wind_exponent ?? 1.8).toFixed(1)}
          onChange={(v) => setS({ ...s, thermal_wind_exponent: v })}
          info="How abruptly wind overtakes thermals past the fade point. Higher = a sharper cutover; lower = a more gradual handoff." />
        <SliderRow label="Midday mixing discount" min={0} max={0.6} step={0.05}
          value={s.thermal_midday_discount ?? 0.3}
          display={`${Math.round((s.thermal_midday_discount ?? 0.3) * 100)}%`}
          onChange={(v) => setS({ ...s, thermal_midday_discount: v })}
          info="Extra reduction applied to midday (rising) thermals even in dead calm — solar heating churns the air enough to scramble a clean directional flow." />
      </div>
      <div style={{ display: "flex", gap: 8 }}>
        <button className="btn btn-primary" onClick={save}><Save size={15} /> {saved ? "Saved ✓" : "Save"}</button>
        <button className="btn" onClick={resetThermal}>Reset thermal model</button>
      </div>

      {/* ── Trail cameras ── */}
      <div className="settings-section-title" style={{ borderTop: "1px solid var(--bord)", paddingTop: 20, marginTop: 24 }}>
        <Camera size={15} color="var(--navy)" style={{ verticalAlign: "text-bottom" }} /> Trail cameras
      </div>
      <p className="settings-desc">Control how frequently cameras sync and how much recent sightings can boost stand rankings.</p>
      <div className="settings-section">
        <SliderRow label="Sync interval" min={5} max={120} step={5}
          value={s.camera_sync_interval_minutes ?? 30}
          display={`${Math.round(s.camera_sync_interval_minutes ?? 30)} min`}
          onChange={(v) => setS({ ...s, camera_sync_interval_minutes: v })} />
        <SliderRow label="Backfill on first sync" min={1} max={90} step={1}
          value={s.camera_backfill_days ?? 7}
          display={`${Math.round(s.camera_backfill_days ?? 7)} days`}
          onChange={(v) => setS({ ...s, camera_backfill_days: v })} />
        <SliderRow label="Max camera boost (per stand)" min={0} max={50} step={1}
          value={s.max_camera_boost_pct ?? 15}
          display={`+${Math.round(s.max_camera_boost_pct ?? 15)}%`}
          onChange={(v) => setS({ ...s, max_camera_boost_pct: v })}
          info="How much a stand's period score can increase when its camera has recently caught deer during that time of day." />
        <SliderRow label="Max camera penalty (per stand)" min={0} max={50} step={1}
          value={s.max_camera_penalty_pct ?? 15}
          display={`-${Math.round(s.max_camera_penalty_pct ?? 15)}%`}
          onChange={(v) => setS({ ...s, max_camera_penalty_pct: v })}
          info="How much a stand's period score can decrease when it has a camera but that camera has caught zero deer photos during that time of day, over the lookback window. Stands with no camera are never affected. A newly paired camera gets a grace period (one full lookback window) before this can apply." />
        <SliderRow label="Camera lookback window" min={12} max={168} step={6}
          value={s.camera_lookback_hours ?? 72}
          display={`${Math.round(s.camera_lookback_hours ?? 72)}h`}
          onChange={(v) => setS({ ...s, camera_lookback_hours: v })}
          info="How far back to look for daylight deer photos when computing the camera boost/penalty for each hunt period. Longer windows smooth out day-to-day gaps; shorter windows react faster to recent activity." />
        <SliderRow label="Camera health check-in window" min={12} max={120} step={6}
          value={s.camera_health_max_age_hours ?? 48}
          display={`${Math.round(s.camera_health_max_age_hours ?? 48)}h`}
          onChange={(v) => setS({ ...s, camera_health_max_age_hours: v })}
          info="If a camera hasn't checked in (or has hit its photo quota) within this window, the penalty is skipped for that stand instead of assuming it saw no deer — the camera may simply be dead or offline. The boost is never affected by this." />
        <SliderRow label="Image retention" min={7} max={365} step={7}
          value={s.image_retention_days ?? 60}
          display={`${Math.round(s.image_retention_days ?? 60)} days`}
          onChange={(v) => setS({ ...s, image_retention_days: v })} />
      </div>

      {/* ── Image storage directory ── */}
      <div className="settings-section">
        <div className="settings-section-hd"><HardDrive size={15} color="var(--amber)" /><strong>Image storage directory</strong></div>
        <p className="settings-desc" style={{ marginBottom: 8 }}>Absolute path on the server where downloaded trail-camera photos are stored. Changing this does not move existing files.</p>
        <Field label="Server path">
          <input value={s.camera_image_dir || ""} onChange={(e) => setS({ ...s, camera_image_dir: e.target.value })}
            placeholder="/app/data/camera_images"
            style={{ fontFamily: "var(--font-mono, monospace)", fontSize: 13 }} />
        </Field>
      </div>
      <div style={{ display: "flex", gap: 8, marginBottom: 24 }}>
        <button className="btn btn-primary" onClick={save}><Save size={15} /> {saved ? "Saved ✓" : "Save"}</button>
      </div>

      {/* ── Rut calendar ── */}
      <div className="settings-section-title" style={{ borderTop: "1px solid var(--bord)", paddingTop: 20, marginTop: 4 }}>
        🦌 Rut calendar
      </div>
      <p className="settings-desc">Peak rut date for your region. Default is Dec 5 (central Arkansas). Move earlier for northern latitudes, later for deep South.</p>
      <div className="settings-section">
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
          <Field label="Peak rut month">
            <select value={s.rut_peak_month ?? 12} onChange={(e) => setS({ ...s, rut_peak_month: +e.target.value })}>
              {["January","February","March","April","May","June","July","August","September","October","November","December"].map((m, i) => (
                <option key={i + 1} value={i + 1}>{m}</option>
              ))}
            </select>
          </Field>
          <Field label="Peak rut day">
            <select value={s.rut_peak_day ?? 5} onChange={(e) => setS({ ...s, rut_peak_day: +e.target.value })}>
              {Array.from({ length: 31 }, (_, i) => i + 1).map((d) => <option key={d} value={d}>{d}</option>)}
            </select>
          </Field>
        </div>
      </div>
      <div style={{ display: "flex", gap: 8 }}>
        <button className="btn btn-primary" onClick={save}><Save size={15} /> {saved ? "Saved ✓" : "Save"}</button>
      </div>

      {/* ── Property timezone ── */}
      <div className="settings-section-title" style={{ borderTop: "1px solid var(--bord)", paddingTop: 20, marginTop: 24 }}>
        🕐 Property timezone
      </div>
      <p className="settings-desc">
        Timezone where your hunting property is located. Keeps "Today" labels,
        stand rankings, and the nightly cleanup job aligned with local time rather
        than the server's UTC clock.
      </p>
      <div className="settings-section">
        <Field label="Timezone">
          <select value={s.property_timezone ?? "America/Chicago"}
            onChange={(e) => setS({ ...s, property_timezone: e.target.value })}>
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
      <div style={{ display: "flex", gap: 8, marginBottom: 24 }}>
        <button className="btn btn-primary" onClick={save}><Save size={15} /> {saved ? "Saved ✓" : "Save"}</button>
      </div>
    </div>
  );
}

export default SettingsPage;
