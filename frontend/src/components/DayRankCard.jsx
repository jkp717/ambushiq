import { PERIOD_COLORS, PERIOD_LABEL } from "../utils/periods.js";
import { degToCompass } from "../utils/compass.js";
import ProximityPill from "./ProximityPill.jsx";
import CameraIndicator from "./CameraIndicator.jsx";

function DayRankCard({ row }) {
  const { stand, periods, wins, proximity } = row;
  const winColor = wins.length ? PERIOD_COLORS[wins[0]] : undefined;
  const pct = (p) => periods[p] ? Math.round(periods[p].score.total * 100) : null;
  const proxTotal = proximity ? Math.round(proximity.total * 100) : 0;
  return (
    <div className="card" style={{ padding: "12px 14px", marginBottom: 8, border: winColor ? `2px solid ${winColor}` : undefined }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <span style={{ fontWeight: 600, fontSize: 15 }}>{stand.name}</span>
        {wins.map((p) => (
          <span key={p} style={{ fontSize: 11.5, fontWeight: 600, color: "#fff", background: PERIOD_COLORS[p], padding: "2px 8px", borderRadius: 6 }}>★ Best {PERIOD_LABEL[p].toLowerCase()}</span>
        ))}
        {proxTotal > 0 && <ProximityPill proximity={proximity} total={proxTotal} />}
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 8 }}>
        {["morning", "midday", "evening"].map((p) => {
          const sc = periods[p]?.score;
          const score = pct(p);
          return (
            <div key={p} style={{ fontSize: 12, color: "var(--sub)" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 5, fontWeight: 500, color: PERIOD_COLORS[p] }}>
                <span style={{ width: 8, height: 8, borderRadius: 2, background: PERIOD_COLORS[p], display: "inline-block" }} />
                {PERIOD_LABEL[p]}{score != null && <span style={{ color: "var(--sub)", fontWeight: 400 }}> · {score}</span>}
                {sc?.camera && (sc.camera.boost_pct !== 0 || sc.camera.status === "unhealthy") && <CameraIndicator camera={sc.camera} />}
              </div>
              {sc && (
                <div style={{ marginTop: 2, lineHeight: 1.45 }}>
                  Wind {degToCompass(periods[p].hour.wind_dir)} {Math.round(periods[p].hour.wind_speed)}mph · thermals {sc.thermal_phase}
                  {stand.deer_approach_deg != null && <> · {sc.scent_score > 0.6 ? "scent away from deer" : sc.scent_score > 0.35 ? "scent crosses deer" : "scent toward deer"}</>}
                </div>
              )}
              {!sc && <div style={{ marginTop: 2 }}>no forecast</div>}
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default DayRankCard;
