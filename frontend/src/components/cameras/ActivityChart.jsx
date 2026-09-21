import { useEffect, useRef, useState } from "react";
import { Images, X } from "lucide-react";
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
   Hovering a bar (or tapping/clicking it, which pins the tooltip) shows its numbers and a button that
   opens the Gallery on that hour's photos (`onViewPhotos(hour)`). */
function ActivityChart({ hours, onViewPhotos }) {
  const [hover, setHover] = useState(null);
  const [pinned, setPinned] = useState(null);
  const leaveTimer = useRef(null);
  const plotRef = useRef(null);
  const sel = pinned ?? hover;

  const max = niceMax(Math.max(0, ...hours.map((h) => h.avg)));
  const plotW = W - PAD.l - PAD.r, plotH = H - PAD.t - PAD.b;
  const slot = plotW / 24, barW = slot * 0.7;
  const y = (v) => PAD.t + plotH * (1 - v / max);
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => f * max);
  const peak = hours.reduce((best, h) => (h.avg > best.avg ? h : best), hours[0]);

  // A pinned tooltip closes on a press anywhere outside the chart.
  useEffect(() => {
    if (pinned == null) return undefined;
    const away = (e) => { if (plotRef.current && !plotRef.current.contains(e.target)) setPinned(null); };
    document.addEventListener("pointerdown", away);
    return () => document.removeEventListener("pointerdown", away);
  }, [pinned]);
  useEffect(() => () => clearTimeout(leaveTimer.current), []);

  // Leaving a bar waits a moment, so the pointer can travel from the bar to the tooltip's button.
  const enter = (i) => { clearTimeout(leaveTimer.current); setHover(i); };
  const leave = () => { clearTimeout(leaveTimer.current); leaveTimer.current = setTimeout(() => setHover(null), 250); };

  const shown = sel != null ? hours[sel] : null;
  let tip = null;
  if (shown) {
    const centerX = PAD.l + sel * slot + slot / 2;
    tip = {
      left: Math.min(84, Math.max(16, (centerX / W) * 100)),   // keep it inside the chart at the left/right edges
      top: (y(shown.avg) / H) * 100,
    };
  }

  return (
    <div className="act-chart">
      <div className="act-readout">
        {peak.avg > 0
          ? <>Busiest hour: <strong>{hourLabel(peak.hour)}</strong> - {fmt(peak.avg)} per day <span>(tap a bar for details)</span></>
          : <span>Tap a bar for details</span>}
      </div>
      <div className="act-plot" ref={plotRef}>
        <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Average animal sightings per day by hour of day"
          onMouseLeave={leave} onClick={() => setPinned(null)}>
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
                {/* full-height hit target so thin bars are easy to hit */}
                <rect x={PAD.l + i * slot} y={PAD.t} width={slot} height={plotH} fill="transparent" style={{ cursor: "pointer" }}
                  onMouseEnter={() => enter(i)}
                  onClick={(e) => { e.stopPropagation(); setPinned(pinned === i ? null : i); }} />
              </g>
            );
          })}
        </svg>

        {shown && (
          <div className="act-tip" style={{ left: `${tip.left}%`, top: `${tip.top}%` }}
            onMouseEnter={() => clearTimeout(leaveTimer.current)} onMouseLeave={leave}>
            <div className="act-tip-hd">
              <strong>{hourLabel(shown.hour)} - {hourLabel(shown.hour + 1)}</strong>
              {pinned != null && <button type="button" className="act-tip-x" onClick={() => setPinned(null)} aria-label="Close"><X size={14} /></button>}
            </div>
            <div className="act-tip-row"><span>Avg per day</span><b>{fmt(shown.avg)}</b></div>
            <div className="act-tip-row"><span>Total sightings</span><b>{shown.total}</b></div>
            {onViewPhotos && (
              <button type="button" className="act-tip-btn" disabled={shown.total === 0}
                onClick={() => onViewPhotos(shown.hour)}>
                <Images size={11} /> View photos
              </button>
            )}
            {onViewPhotos && shown.total > 0 && <div className="act-tip-note">The gallery lists every photo, so it can show more than the count (a visit is counted once).</div>}
          </div>
        )}
      </div>
      <div className="act-caption">Avg sightings per day, by hour of day</div>
    </div>
  );
}

export default ActivityChart;
