import { useState } from "react";
import { hourLabel } from "./filters.js";

const W = 600, H = 260;
const PAD = { l: 38, r: 8, t: 12, b: 28 };

// Round the tallest bar up to a friendly axis maximum (0.5, 1, 2, 5, 10, ...).
function niceMax(v) {
  if (v <= 0) return 1;
  const pow = 10 ** Math.floor(Math.log10(v));
  for (const m of [1, 2, 2.5, 5, 10]) if (v <= m * pow) return m * pow;
  return 10 * pow;
}

const fmt = (n) => {
  const s = n >= 10 ? n.toFixed(0) : n >= 1 ? n.toFixed(1) : n.toFixed(2);
  return s.includes(".") ? s.replace(/\.?0+$/, "") : s;   // 2.50 -> 2.5, 0.00 -> 0, but 10 stays 10
};

/* Single bar chart: one bar per hour of the day (property-local). `hours` is [{hour, avg, total}] x 24.
   Tapping or hovering a bar shows its exact numbers. */
function ActivityChart({ hours }) {
  const [sel, setSel] = useState(null);
  const max = niceMax(Math.max(0, ...hours.map((h) => h.avg)));
  const plotW = W - PAD.l - PAD.r, plotH = H - PAD.t - PAD.b;
  const slot = plotW / 24, barW = slot * 0.7;
  const y = (v) => PAD.t + plotH * (1 - v / max);
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => f * max);
  const peak = hours.reduce((best, h) => (h.avg > best.avg ? h : best), hours[0]);
  const shown = sel != null ? hours[sel] : null;

  return (
    <div className="act-chart">
      <div className="act-readout">
        {shown
          ? <><strong>{hourLabel(shown.hour)}</strong> - {fmt(shown.avg)} per day <span>({shown.total} sighting{shown.total === 1 ? "" : "s"} in total)</span></>
          : peak.avg > 0
            ? <>Busiest hour: <strong>{hourLabel(peak.hour)}</strong> - {fmt(peak.avg)} per day</>
            : <span>Tap a bar for details</span>}
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Average animal sightings per day by hour of day"
        onMouseLeave={() => setSel(null)}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={PAD.l} x2={W - PAD.r} y1={y(t)} y2={y(t)} className="act-grid" />
            <text x={PAD.l - 6} y={y(t) + 4} textAnchor="end" className="act-axis">{fmt(t)}</text>
          </g>
        ))}
        {hours.map((h, i) => {
          const x = PAD.l + i * slot + (slot - barW) / 2;
          const top = y(h.avg);
          return (
            <g key={h.hour}>
              <rect x={x} y={top} width={barW} height={Math.max(0, PAD.t + plotH - top)} rx={2}
                className={"act-bar" + (sel === i ? " sel" : h === peak && h.avg > 0 ? " peak" : "")} />
              {i % 3 === 0 && (
                <text x={x + barW / 2} y={H - 8} textAnchor="middle" className="act-axis">
                  {h.hour === 0 ? "12a" : h.hour === 12 ? "12p" : h.hour < 12 ? `${h.hour}a` : `${h.hour - 12}p`}
                </text>
              )}
              {/* full-height hit target so thin bars are easy to tap */}
              <rect x={PAD.l + i * slot} y={PAD.t} width={slot} height={plotH} fill="transparent"
                onMouseEnter={() => setSel(i)} onClick={() => setSel(i)} />
            </g>
          );
        })}
      </svg>
      <div className="act-caption">Avg sightings per day, by hour of day</div>
    </div>
  );
}

export default ActivityChart;
