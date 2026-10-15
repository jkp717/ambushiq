import { PERIOD_COLORS, PERIOD_LABEL } from "../utils/periods.js";
import { degToCompass } from "../utils/compass.js";
import ProximityPill from "./ProximityPill.jsx";
import { useEffect, useRef } from "react";
import CameraIndicator from "./CameraIndicator.jsx";
import StandLinks from "./StandLinks.jsx";

// `highlight`: the user just jumped here from this stand elsewhere, so scroll to it and outline it briefly.
function DayRankCard({ row, day, standNav, highlight = false }) {
  const { stand, periods, wins, proximity } = row;
  const winColor = wins.length ? PERIOD_COLORS[wins[0]] : undefined;
  const pct = (p) => periods[p] ? Math.round(periods[p].score.total * 100) : null;
  const proxTotal = proximity ? Math.round(proximity.total * 100) : 0;
  const el = useRef(null);
  useEffect(() => {
    if (highlight && el.current) el.current.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [highlight]);
  return (
    <div ref={el} className={"card" + (highlight ? " rank-highlight" : "")} style={{ padding: "12px 14px", marginBottom: 8, border: winColor ? `2px solid ${winColor}` : undefined }}>
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
                {periods[p]?.lee_eddy && (
                  <span className="eddy-tag" title={`${periods[p].lee_eddy.text} ${periods[p].lee_eddy.why}`}>⟲ eddy</span>
                )}
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
      <StandLinks standId={stand.id} standNav={standNav} exclude="score" day={day} />
    </div>
  );
}

export default DayRankCard;
