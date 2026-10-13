/* The activity charts' optional second axis: one weather measure drawn as a line through each bar's average,
   with whiskers from that bar's low to its high, against its own scale on the right.
   `weather` is the API's {metric, unit, points: [{key, avg, low, high, peak?}]}, one point per bar. */

const WEATHER_OPTIONS = [
  { key: "", label: "Off" },
  { key: "temp", label: "Temperature" },
  { key: "pressure", label: "Barometric pressure" },
  { key: "wind", label: "Wind speed" },
  { key: "rain", label: "Rain" },
];
const LABEL = Object.fromEntries(WEATHER_OPTIONS.map((o) => [o.key, o.label]));
const DECIMALS = { temp: 0, pressure: 2, wind: 0, rain: 2 };
const AXIS_W = 46;   // right padding the charts reserve for this axis, in chart units

function fmtW(v, metric) {
  return v == null ? "–" : v.toFixed(DECIMALS[metric] ?? 1);
}

// A friendly step (1, 2, 2.5, 5 x 10^n) splitting `span` into about `n` parts.
function niceStep(span, n = 4) {
  const raw = span / n;
  const pow = 10 ** Math.floor(Math.log10(raw));
  for (const m of [1, 2, 2.5, 5, 10]) if (raw <= m * pow) return m * pow;
  return 10 * pow;
}

/* The right axis's range and ticks: snapped to friendly steps around the data's lows and highs. Rain starts at
   0; the rest float, so pressure's narrow range fills the chart. Null when there's no data at all. */
function weatherScale(weather) {
  const vals = (weather?.points || []).flatMap((p) => [p.low, p.high, p.avg]).filter((v) => v != null);
  if (!vals.length) return null;
  let lo = Math.min(...vals), hi = Math.max(...vals);
  if (weather.metric === "rain") lo = 0;
  if (hi - lo < 1e-9) { const pad = Math.abs(hi) * 0.05 || 1; lo -= pad; hi += pad; }
  const step = niceStep(hi - lo);
  lo = Math.floor(lo / step) * step;
  hi = Math.ceil(hi / step) * step;
  const ticks = [];
  for (let t = lo; t <= hi + step / 2; t += step) ticks.push(t);
  return { lo, hi, ticks };
}

/* The line and whiskers. `centers[i]` is the x of bar i (chart units); `y(v)` maps a value to chart y.
   `dense` (hundreds of bars) drops the caps and dots so it stays readable. Never takes pointer events,
   so the bars underneath stay tappable. */
function WeatherOverlay({ weather, centers, y, dense = false }) {
  const pts = weather.points;
  const segs = [];
  let cur = [];
  pts.forEach((p, i) => {
    if (p.avg == null) { if (cur.length) segs.push(cur); cur = []; return; }
    cur.push(`${centers[i]},${y(p.avg)}`);
  });
  if (cur.length) segs.push(cur);
  const cap = dense ? 0 : Math.min(4, Math.max(1.5, (centers[1] - centers[0] || 8) * 0.25));
  return (
    <g className={`wx wx-${weather.metric}`} pointerEvents="none">
      {pts.map((p, i) => (p.low == null || p.high == null ? null : (
        <g key={`w${i}`} className="wx-whisker">
          <line x1={centers[i]} x2={centers[i]} y1={y(p.low)} y2={y(p.high)} />
          {cap > 0 && <line x1={centers[i] - cap} x2={centers[i] + cap} y1={y(p.low)} y2={y(p.low)} />}
          {cap > 0 && <line x1={centers[i] - cap} x2={centers[i] + cap} y1={y(p.high)} y2={y(p.high)} />}
        </g>
      )))}
      {segs.map((s, k) => (s.length > 1
        ? <polyline key={`l${k}`} className="wx-line" points={s.join(" ")} />
        : null))}
      {!dense && pts.map((p, i) => (p.avg == null ? null
        : <circle key={`d${i}`} className="wx-dot" cx={centers[i]} cy={y(p.avg)} r={2.2} />))}
    </g>
  );
}

// Right-axis tick labels, left-aligned at `x` (chart units).
function WeatherAxis({ weather, scale, x, y }) {
  return (
    <g className={`wx wx-${weather.metric}`}>
      {scale.ticks.map((t) => (
        <text key={t} x={x} y={y(t) + 4} className="act-axis wx-axis">{fmtW(t, weather.metric)}</text>
      ))}
    </g>
  );
}

const weatherLabel = (weather) => LABEL[weather.metric];

// The tooltip row for one bar: "48°F avg (34–61)", or for rain by date "0.35 in (wettest hour 0.25)".
function weatherTip(weather, p) {
  if (!p || p.avg == null) return "no data";
  const m = weather.metric, u = weather.unit === "°F" ? "°F" : ` ${weather.unit}`;
  if (p.peak != null) return `${fmtW(p.avg, m)}${u} (wettest hour ${fmtW(p.peak, m)})`;
  return `${fmtW(p.avg, m)}${u} avg (${fmtW(p.low, m)}–${fmtW(p.high, m)})`;
}

function WeatherLegend({ weather }) {
  const what = weather.metric === "rain" && weather.points.some((p) => p.peak != null) ? "daily total" : "average, with low–high";
  return (
    <div className="act-legend">
      <span><i className="act-legend-bar" /> Sightings</span>
      <span className={`wx-${weather.metric}`}><i className="act-legend-line" /> {LABEL[weather.metric]} ({weather.unit}) — {what}</span>
    </div>
  );
}

export { WEATHER_OPTIONS, AXIS_W, weatherLabel, weatherScale, weatherTip, WeatherOverlay, WeatherAxis, WeatherLegend };
