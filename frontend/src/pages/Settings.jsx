import { useState, useEffect } from "react";
import { Save, Footprints, Wheat, Trees, Target, Thermometer, Camera, HardDrive, CloudSun, Binoculars, Download, Share2 } from "lucide-react";
import { api } from "../services/api.js";
import { useInstallPrompt } from "../hooks/useInstallPrompt.js";
import Banner from "../components/ui/Banner.jsx";
import Empty from "../components/ui/Empty.jsx";
import Field from "../components/ui/Field.jsx";
import SliderRow from "../components/ui/SliderRow.jsx";
import InfoTip from "../components/ui/InfoTip.jsx";

function SettingsPage() {
  const [s, setS] = useState(null); const [saved, setSaved] = useState(false); const [err, setErr] = useState(null);
  const [weatherProviders, setWeatherProviders] = useState([]);
  const [weatherKeyInputs, setWeatherKeyInputs] = useState({}); // { [providerId]: typedKey }
  const { canInstall, promptInstall, installed, isIOS } = useInstallPrompt();
  const [showIOSHelp, setShowIOSHelp] = useState(false);
  useEffect(() => { api("/settings").then(setS).catch(() => setErr("Couldn't load settings.")); }, []);
  useEffect(() => { api("/weather-providers").then((r) => setWeatherProviders(r.providers || [])).catch(() => {}); }, []);

  async function save() {
    try {
      const body = { ...s };
      const keys = Object.fromEntries(Object.entries(weatherKeyInputs).filter(([, v]) => v));
      if (Object.keys(keys).length) body.weather_provider_api_keys = keys;
      const r = await api("/settings", { method: "PUT", body: JSON.stringify(body) });
      setS(r); setWeatherKeyInputs({});
      setSaved(true); setTimeout(() => setSaved(false), 1800);
    } catch { setErr("Couldn't save settings."); }
  }
  async function clearWeatherKey(providerId) {
    try { const r = await api("/settings", { method: "PUT", body: JSON.stringify({ weather_provider_api_keys: { [providerId]: "" } }) }); setS(r); }
    catch { setErr("Couldn't clear key."); }
  }
  function reset() { setS({ ...s, weight_corridor: 0.15, falloff_corridor: 150, weight_food: 0.15, falloff_food: 200, weight_bedding: 0.10, falloff_bedding: 250, weight_scrape: 0.12, falloff_scrape: 100, weight_rub: 0.10, falloff_rub: 80, scent_gate_floor: 0.4, rut_weight_strength: 1.0 }); }
  function resetRating() { setS({ ...s, rate_w_pressure: 0.32, rate_w_wind: 0.20, rate_w_rain: 0.28, rate_w_temp: 0.20 }); }
  function resetThermal() { setS({ ...s, thermal_wind_half_scale: 7.0, thermal_wind_exponent: 1.8, thermal_midday_discount: 0.3 }); }
  function resetScouting() {
    setS({ ...s,
      scout_radius_default_m: 800.0, scout_radius_min_m: 60.0, scout_radius_max_m: 2400.0,
      scout_grid_n: 60, scout_steep_slope_pct: 20.0, scout_max_pinch_width_m: 120.0,
      scout_min_candidate_score: 40.0, scout_min_separation_m: 150.0, scout_max_suggestions_per_run: 8,
      scout_suggestion_radius_m: 60.0, scout_overlap_skip_threshold: 0.5,
      scout_weight_terrain: 0.55, scout_weight_proximity: 0.20, scout_weight_camera: 0.15, scout_weight_unexplored: 0.10,
    });
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
  const scoutWSum = (s.scout_weight_terrain ?? 0.55) + (s.scout_weight_proximity ?? 0.20)
    + (s.scout_weight_camera ?? 0.15) + (s.scout_weight_unexplored ?? 0.10) || 1;

  const selectedProvider = weatherProviders.find((p) => p.id === (s.weather_provider || "open_meteo"));
  const secondaryOptions = weatherProviders.filter((p) => p.id !== selectedProvider?.id);
  const selectedSecondary = weatherProviders.find((p) => p.id === s.weather_secondary_provider);
  const primaryKeySet = !!s.weather_provider_api_keys_set?.[selectedProvider?.id];
  const secondaryKeySet = !!s.weather_provider_api_keys_set?.[selectedSecondary?.id];

  return (
    <div className="settings-page">
      {err && <Banner>{err}</Banner>}

      <div className="settings-section-title" style={{ borderTop: "1px solid var(--bord)", paddingTop: 20 }}>
        <CloudSun size={15} color="var(--navy)" style={{ verticalAlign: "text-bottom" }} /> Weather source
      </div>
      <p className="settings-desc">Which weather service supplies the hourly forecast that drives wind, thermal, and rating scoring.</p>
      <div className="settings-section">
        <Field label="Weather provider">
          <select value={s.weather_provider ?? "open_meteo"} onChange={(e) => setS({ ...s, weather_provider: e.target.value })}>
            {weatherProviders.map((p) => (
              <option key={p.id} value={p.id}>{p.label}{p.needs_key ? " (API key required)" : ""}</option>
            ))}
          </select>
        </Field>
        {selectedProvider?.needs_key && (
          <div style={{ marginTop: 10 }}>
            <Field label={primaryKeySet ? "API key (already set — leave blank to keep)" : "API key"}>
              <input type="password" value={weatherKeyInputs[selectedProvider.id] || ""}
                onChange={(e) => setWeatherKeyInputs({ ...weatherKeyInputs, [selectedProvider.id]: e.target.value })}
                placeholder={primaryKeySet ? "••••••••" : `${selectedProvider.label} API key`} />
            </Field>
            {primaryKeySet && (
              <button className="btn" style={{ marginTop: 6 }} onClick={() => clearWeatherKey(selectedProvider.id)}>Clear stored key</button>
            )}
          </div>
        )}
        <div style={{ marginTop: 14, paddingTop: 12, borderTop: "1px solid var(--bord)" }}>
          <Field label={
            <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
              Secondary provider
              <InfoTip text={`Optional. Fills in solar radiation if your primary provider doesn't report it, and extends the forecast if your primary provider returns fewer days than requested (some providers only return ~6-7 days). Leave on "None" to use only your primary provider's own range.`} />
            </span>
          }>
            <select value={s.weather_secondary_provider ?? ""} onChange={(e) => setS({ ...s, weather_secondary_provider: e.target.value })}>
              <option value="">None — use only the primary provider's own range</option>
              {secondaryOptions.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
            </select>
          </Field>
          {selectedSecondary?.needs_key && (
            <div style={{ marginTop: 10 }}>
              <Field label={secondaryKeySet ? "Secondary API key (already set — leave blank to keep)" : "Secondary API key"}>
                <input type="password" value={weatherKeyInputs[selectedSecondary.id] || ""}
                  onChange={(e) => setWeatherKeyInputs({ ...weatherKeyInputs, [selectedSecondary.id]: e.target.value })}
                  placeholder={secondaryKeySet ? "••••••••" : `${selectedSecondary.label} API key`} />
              </Field>
              {secondaryKeySet && (
                <button className="btn" style={{ marginTop: 6 }} onClick={() => clearWeatherKey(selectedSecondary.id)}>Clear stored key</button>
              )}
            </div>
          )}
        </div>
      </div>
      <div style={{ display: "flex", gap: 8, marginBottom: 24 }}>
        <button className="btn btn-primary" onClick={save}><Save size={15} /> {saved ? "Saved ✓" : "Save"}</button>
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
      <div className="settings-section">
        <div className="settings-section-hd"><strong>Scent &amp; season</strong></div>
        <SliderRow label="Bad-scent score kept" min={0} max={1} step={0.05}
          value={s.scent_gate_floor ?? 0.4}
          display={`${Math.round((s.scent_gate_floor ?? 0.4) * 100)}%`}
          onChange={(v) => setS({ ...s, scent_gate_floor: v })}
          info="Share of a stand's score it keeps when scent blows straight at the expected deer approach. 0% = a hard gate (bad scent zeroes the stand); 100% = scent direction is ignored." />
        <SliderRow label="Seasonal weighting" min={0} max={1} step={0.05}
          value={s.rut_weight_strength ?? 1.0}
          display={`${Math.round((s.rut_weight_strength ?? 1.0) * 100)}%`}
          onChange={(v) => setS({ ...s, rut_weight_strength: v })}
          info="How strongly the rut phase re-weights the bonuses above — e.g. scrapes and rubs count more pre-rut, food counts more after the rut. 0% = the same weights all season." />
      </div>
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
        <SliderRow label="Camera boost saturation" min={1} max={10} step={0.5}
          value={s.camera_boost_saturation ?? 3}
          display={`${(s.camera_boost_saturation ?? 3).toFixed(1)} pts`}
          onChange={(v) => setS({ ...s, camera_boost_saturation: v })}
          info="Each qualifying deer photo in a period contributes its detection confidence (0.1-1.0) to a running total. Once that total reaches this many points, the boost above is fully applied — so a few high-confidence photos, or more lower-confidence ones, both reach the cap. Lower this to have the boost max out with less evidence; raise it to require more." />
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

      {/* ── Scouting suggestions ── */}
      <div className="settings-section-title" style={{ borderTop: "1px solid var(--bord)", paddingTop: 20, marginTop: 4 }}>
        <Binoculars size={15} color="var(--navy)" style={{ verticalAlign: "text-bottom" }} /> Scouting suggestions
      </div>
      <p className="settings-desc">
        Controls how the "Scouting Suggestions" map tool analyzes a drawn area for
        candidate hunting locations — terrain funnels, habitat edges, and how much
        weight your own zones/corridors/sign/cameras carry versus the raw terrain model.
      </p>
      <div className="settings-section">
        <SliderRow label="Default analysis radius" min={60} max={2400} step={20}
          value={s.scout_radius_default_m ?? 800} display={`${Math.round(s.scout_radius_default_m ?? 800)} m`}
          onChange={(v) => setS({ ...s, scout_radius_default_m: v })}
          info="Starting circle size when you drop a new scouting-analysis point. You can still drag the edge to resize before running it." />
        <SliderRow label="Minimum analysis radius" min={20} max={500} step={10}
          value={s.scout_radius_min_m ?? 60} display={`${Math.round(s.scout_radius_min_m ?? 60)} m`}
          onChange={(v) => setS({ ...s, scout_radius_min_m: v })}
          info="Smallest area you're allowed to analyze — keeps you from accidentally requesting a useless few-foot circle." />
        <SliderRow label="Maximum analysis radius" min={500} max={8000} step={100}
          value={s.scout_radius_max_m ?? 2400}
          display={(s.scout_radius_max_m ?? 2400) >= 1000 ? `${((s.scout_radius_max_m ?? 2400) / 1609.34).toFixed(1)} mi` : `${Math.round(s.scout_radius_max_m ?? 2400)} m`}
          onChange={(v) => setS({ ...s, scout_radius_max_m: v })}
          info="Largest area you're allowed to analyze in one run. Raising this doesn't slow anything down by itself — see grid resolution below — but a bigger circle does mean coarser detail per cell." />
        <SliderRow label="Analysis grid resolution" min={20} max={100} step={5}
          value={s.scout_grid_n ?? 60} display={`${Math.round(s.scout_grid_n ?? 60)}×${Math.round(s.scout_grid_n ?? 60)}`}
          onChange={(v) => setS({ ...s, scout_grid_n: v })}
          info="Number of sample points across the analysis circle, in both directions. Higher = finer terrain/land-cover detail but a slower analysis (grows roughly with the square of this number) — this is what keeps runtime predictable regardless of how large a circle you draw." />
        <SliderRow label="Steep-slope threshold" min={5} max={40} step={1}
          value={s.scout_steep_slope_pct ?? 20} display={`${Math.round(s.scout_steep_slope_pct ?? 20)}%`}
          onChange={(v) => setS({ ...s, scout_steep_slope_pct: v })}
          info="Slope grade treated as a 'wall' when detecting pinch points — a narrow gap between two of these counts as a terrain funnel." />
        <SliderRow label="Max pinch-point width" min={30} max={300} step={10}
          value={s.scout_max_pinch_width_m ?? 120} display={`${Math.round(s.scout_max_pinch_width_m ?? 120)} m`}
          onChange={(v) => setS({ ...s, scout_max_pinch_width_m: v })}
          info="Widest gap between two steep-slope walls that still counts as a funnel pinch point. Narrower = only tight, obvious funnels qualify." />
        <SliderRow label="Minimum suggestion score" min={0} max={100} step={5}
          value={s.scout_min_candidate_score ?? 40} display={`${Math.round(s.scout_min_candidate_score ?? 40)}`}
          onChange={(v) => setS({ ...s, scout_min_candidate_score: v })}
          info="A location has to score at least this high (of 100) before it's even considered as a candidate — raise it to see only your strongest suggestions." />
        <SliderRow label="Minimum suggestion separation" min={50} max={500} step={25}
          value={s.scout_min_separation_m ?? 150} display={`${Math.round(s.scout_min_separation_m ?? 150)} m`}
          onChange={(v) => setS({ ...s, scout_min_separation_m: v })}
          info="How close two candidate spots can be before they're merged into a single suggestion instead of two separate ones." />
        <SliderRow label="Max suggestions per run" min={1} max={20} step={1}
          value={s.scout_max_suggestions_per_run ?? 8} display={`${Math.round(s.scout_max_suggestions_per_run ?? 8)}`}
          onChange={(v) => setS({ ...s, scout_max_suggestions_per_run: v })} />
        <SliderRow label="Suggestion marker size" min={20} max={200} step={10}
          value={s.scout_suggestion_radius_m ?? 60} display={`${Math.round(s.scout_suggestion_radius_m ?? 60)} m`}
          onChange={(v) => setS({ ...s, scout_suggestion_radius_m: v })}
          info="Radius of the flagged area drawn on the map for each suggestion." />
        <SliderRow label="'Only show new ones' overlap threshold" min={0.1} max={0.9} step={0.05}
          value={s.scout_overlap_skip_threshold ?? 0.5} display={`${Math.round((s.scout_overlap_skip_threshold ?? 0.5) * 100)}%`}
          onChange={(v) => setS({ ...s, scout_overlap_skip_threshold: v })}
          info="When re-analyzing overlapping ground with 'Only show new ones', a new candidate is skipped if it overlaps an existing suggestion by more than this fraction of its own area." />
      </div>
      <p className="settings-desc" style={{ marginTop: 4 }}>Score weights — how much each factor contributes to a suggestion's 0–100 score. Relative to each other; you don't need them to sum to 1.</p>
      <div className="settings-section">
        <SliderRow label={`Terrain (funnels + habitat edges) — ${Math.round((s.scout_weight_terrain ?? 0.55) / scoutWSum * 100)}%`}
          min={0} max={1} step={0.05} value={s.scout_weight_terrain ?? 0.55} display={(s.scout_weight_terrain ?? 0.55).toFixed(2)}
          onChange={(v) => setS({ ...s, scout_weight_terrain: v })} />
        <SliderRow label={`Proximity to your zones/corridors/sign — ${Math.round((s.scout_weight_proximity ?? 0.20) / scoutWSum * 100)}%`}
          min={0} max={1} step={0.05} value={s.scout_weight_proximity ?? 0.20} display={(s.scout_weight_proximity ?? 0.20).toFixed(2)}
          onChange={(v) => setS({ ...s, scout_weight_proximity: v })} />
        <SliderRow label={`Nearby camera confirmation — ${Math.round((s.scout_weight_camera ?? 0.15) / scoutWSum * 100)}%`}
          min={0} max={1} step={0.05} value={s.scout_weight_camera ?? 0.15} display={(s.scout_weight_camera ?? 0.15).toFixed(2)}
          onChange={(v) => setS({ ...s, scout_weight_camera: v })} />
        <SliderRow label={`Unexplored-ground bonus — ${Math.round((s.scout_weight_unexplored ?? 0.10) / scoutWSum * 100)}%`}
          min={0} max={1} step={0.05} value={s.scout_weight_unexplored ?? 0.10} display={(s.scout_weight_unexplored ?? 0.10).toFixed(2)}
          onChange={(v) => setS({ ...s, scout_weight_unexplored: v })}
          info="Biases suggestions toward ground far from your existing stands/cameras/suggestions, so the feature surfaces genuinely new spots instead of just re-confirming places you already know about." />
      </div>
      <div style={{ display: "flex", gap: 8, marginBottom: 24 }}>
        <button className="btn btn-primary" onClick={save}><Save size={15} /> {saved ? "Saved ✓" : "Save"}</button>
        <button className="btn" onClick={resetScouting}>Reset defaults</button>
      </div>

      {/* ── Install app ── */}
      {!installed && (canInstall || isIOS) && (
        <>
          <div className="settings-section-title" style={{ borderTop: "1px solid var(--bord)", paddingTop: 20, marginTop: 4 }}>
            <Download size={15} color="var(--navy)" style={{ verticalAlign: "text-bottom" }} /> Install app
          </div>
          <p className="settings-desc">
            Add AmbushIQ to your home screen for quicker, full-screen access — it opens like a
            regular app, without the browser's address bar.
          </p>
          <div className="settings-section">
            {canInstall ? (
              <button className="btn btn-primary" onClick={promptInstall}><Download size={15} /> Install app</button>
            ) : (
              <>
                <button className="btn btn-primary" onClick={() => setShowIOSHelp((v) => !v)}>
                  <Share2 size={15} /> How to install on iPhone/iPad
                </button>
                {showIOSHelp && (
                  <p className="settings-desc" style={{ marginTop: 10, marginBottom: 0 }}>
                    Tap the Share icon <Share2 size={12} style={{ verticalAlign: "middle" }} /> in
                    Safari's toolbar, then choose "Add to Home Screen".
                  </p>
                )}
              </>
            )}
          </div>
        </>
      )}
    </div>
  );
}

export default SettingsPage;
