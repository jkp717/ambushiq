import { CheckCircle2, Waves } from "lucide-react";
import { degToCompass } from "../utils/compass.js";

function TerrainPanel({ t }) {
  return (
    <div style={{ background: "var(--surf)", borderRadius: 10, padding: 12, display: "flex", gap: 14, alignItems: "center" }}>
      <TerrainMap t={t} />
      <div style={{ fontSize: 12.5, lineHeight: 1.6, flex: 1, minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 5, color: "var(--green)", marginBottom: 4 }}><CheckCircle2 size={13} /><span style={{ fontWeight: 500 }}>{t.source}</span></div>
        <div style={{ color: "var(--sub)" }}>Elevation <b style={{ color: "var(--txt)" }}>{t.elevation} m</b> · relief <b style={{ color: "var(--txt)" }}>{t.relief} m</b></div>
        <div style={{ color: "var(--sub)" }}>Slope <b style={{ color: "var(--txt)" }}>{t.slope_pct}%</b> · faces <b style={{ color: "var(--txt)" }}>{degToCompass(t.downhill_deg)}</b></div>
        <div style={{ color: "var(--sub)", display: "flex", alignItems: "center", gap: 4 }}><Waves size={12} color="var(--blue)" /> Drains <b style={{ color: "var(--txt)" }}>{degToCompass(t.drainage_deg)}</b> {t.channel_strength > 0.5 ? "(strong)" : t.channel_strength > 0.2 ? "(moderate)" : "(diffuse)"}</div>
      </div>
    </div>
  );
}

function TerrainMap({ t }) {
  const N = t.grid_size, px = 88, cell = px / N;
  const flat = t.dem.flat(), min = Math.min(...flat), max = Math.max(...flat), rng = max - min || 1;
  const maxAcc = Math.max(...t.acc.flat());
  const rects = [], chans = [];
  for (let r = 0; r < N; r++) for (let c = 0; c < N; c++) {
    const v = (t.dem[r][c] - min) / rng, shade = Math.round(235 - v * 150);
    rects.push(<rect key={"e"+r+c} x={c*cell} y={r*cell} width={cell+0.5} height={cell+0.5} fill={`rgb(${shade-10},${shade},${shade-20})`} />);
    if (t.acc[r][c] > maxAcc * 0.18) chans.push(<rect key={"a"+r+c} x={c*cell} y={r*cell} width={cell+0.5} height={cell+0.5} fill="var(--blue)" opacity={Math.min(0.85, 0.3 + t.acc[r][c]/maxAcc)} />);
  }
  const ctr = (N/2)*cell;
  return (
    <svg width={px} height={px} viewBox={`0 0 ${px} ${px}`} style={{ borderRadius: 6, flexShrink: 0, border: "1px solid var(--bord)" }}>
      {rects}{chans}
      <circle cx={ctr} cy={ctr} r={3.5} fill="none" stroke="var(--red)" strokeWidth={1.5} />
      <circle cx={ctr} cy={ctr} r={1.5} fill="var(--red)" />
    </svg>
  );
}

export default TerrainPanel;
