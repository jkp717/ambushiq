import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Images, X } from "lucide-react";
import { niceMax, fmt } from "./ActivityChart.jsx";

const W = 600, H = 260;
const PAD = { l: 38, r: 8, t: 12, b: 28 };
const MIN_SLOT_PX = 8;      // narrower than this and a day can't be tapped: the plot scrolls sideways instead
const LABEL_GAP_PX = 52;    // room for one "Oct 17" axis label
const TIP_HALF_PX = 90;     // keeps the tooltip inside the chart at its left/right edges

const asDate = (iso) => new Date(iso + "T12:00:00");
const shortDay = (d) => d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
const longDay = (d) => d.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric", year: "numeric" });

/* One bar per date (property-local): `dates` is [{date: "YYYY-MM-DD", total}], quiet days included.
   Same look and interaction as ActivityChart: hover or tap a bar for its numbers and a button that opens the
   Gallery on that day's photos (`onViewPhotos(date)`). A long range keeps every bar tappable by making the
   plot wider than the screen and scrolling it sideways, starting at the newest dates; the y-axis stays put. */
function DateActivityChart({ dates, onViewPhotos }) {
  const [hover, setHover] = useState(null);
  const [pinned, setPinned] = useState(null);
  const [boxW, setBoxW] = useState(W);          // rendered width of the plot, in CSS px
  const [scrollX, setScrollX] = useState(0);
  const leaveTimer = useRef(null);
  const plotRef = useRef(null);
  const scrollRef = useRef(null);
  const sel = pinned ?? hover;

  const n = dates.length;
  const s = boxW / W;                                            // CSS px per chart unit
  const vw = Math.max(W, PAD.l + PAD.r + n * (MIN_SLOT_PX / s)); // chart width in units: wider than W = scrolls
  const scrolls = vw > W;
  const plotW = vw - PAD.l - PAD.r, plotH = H - PAD.t - PAD.b;
  const slot = plotW / n, barW = slot * (slot * s > 4 ? 0.7 : 0.85);
  const max = niceMax(Math.max(0, ...dates.map((d) => d.total)));
  const y = (v) => PAD.t + plotH * (1 - v / max);
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => f * max);
  const peak = dates.reduce((best, d) => (d.total > best.total ? d : best), dates[0]);
  const step = Math.max(1, Math.ceil(LABEL_GAP_PX / (slot * s)));
  const manyYears = n > 0 && dates[0].date.slice(0, 4) !== dates[n - 1].date.slice(0, 4);

  useLayoutEffect(() => {
    const el = plotRef.current;
    if (!el) return undefined;
    const measure = () => { if (el.clientWidth) setBoxW(el.clientWidth); };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // New data or a resize: show the newest dates.
  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (el && scrolls) { el.scrollLeft = el.scrollWidth; setScrollX(el.scrollLeft); }
  }, [dates, scrolls, boxW]);

  useEffect(() => { setPinned(null); setHover(null); }, [dates]);

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

  const shown = sel != null ? dates[sel] : null;
  let tip = null;
  if (shown) {
    const centerPx = (PAD.l + sel * slot + slot / 2) * s - scrollX;
    tip = {
      left: Math.min(boxW - TIP_HALF_PX, Math.max(TIP_HALF_PX, centerPx)),
      top: y(shown.total) * s,
    };
  }

  // Axis label: "Oct 7", plus the year where it changes when the range spans more than one year.
  let lastYear = null;
  const label = (iso) => {
    const d = asDate(iso);
    const yr = iso.slice(0, 4);
    const withYear = manyYears && yr !== lastYear;
    lastYear = yr;
    return withYear ? `${shortDay(d)} '${yr.slice(2)}` : shortDay(d);
  };

  const yAxis = ticks.map((t) => (
    <text key={t} x={PAD.l - 6} y={y(t) + 4} textAnchor="end" className="act-axis">{fmt(t)}</text>
  ));

  return (
    <div className="act-chart">
      <div className="act-readout">
        {peak && peak.total > 0
          ? <>Busiest day: <strong>{asDate(peak.date).toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" })}</strong> - {peak.total} sighting{peak.total === 1 ? "" : "s"} <span>(tap a bar for details)</span></>
          : <span>Tap a bar for details</span>}
      </div>
      <div className="act-plot" ref={plotRef}>
        <div className={scrolls ? "act-scroll" : undefined} ref={scrollRef}
          onScroll={(e) => setScrollX(e.currentTarget.scrollLeft)}>
          <svg viewBox={`0 0 ${vw} ${H}`} style={scrolls ? { width: `${vw * s}px` } : undefined}
            role="img" aria-label="Animal sightings per day" onMouseLeave={leave} onClick={() => setPinned(null)}>
            {ticks.map((t) => <line key={t} x1={PAD.l} x2={vw - PAD.r} y1={y(t)} y2={y(t)} className="act-grid" />)}
            {!scrolls && yAxis}
            {dates.map((d, i) => {
              const x = PAD.l + i * slot + (slot - barW) / 2;
              const top = y(d.total);
              return (
                <g key={d.date}>
                  <rect x={x} y={top} width={barW} height={Math.max(0, PAD.t + plotH - top)} rx={Math.min(2, barW / 3)}
                    className={"act-bar" + (sel === i ? " sel" : d === peak && d.total > 0 ? " peak" : "")} />
                  {(n - 1 - i) % step === 0 && (
                    // the newest label lines up with its bar's right edge so it isn't cut off
                    <text x={i === n - 1 ? x + barW : x + barW / 2} y={H - 8} textAnchor={i === n - 1 ? "end" : "middle"}
                      className="act-axis">{label(d.date)}</text>
                  )}
                  {/* full-height hit target so thin bars are easy to hit */}
                  <rect x={PAD.l + i * slot} y={PAD.t} width={slot} height={plotH} fill="transparent" style={{ cursor: "pointer" }}
                    onMouseEnter={() => enter(i)}
                    onClick={(e) => { e.stopPropagation(); setPinned(pinned === i ? null : i); }} />
                </g>
              );
            })}
          </svg>
        </div>

        {/* scrolling plot: the y-axis labels stay pinned at the left edge */}
        {scrolls && (
          <svg className="act-yaxis" viewBox={`0 0 ${PAD.l} ${H}`} style={{ width: `${PAD.l * s}px` }} aria-hidden="true">
            {yAxis}
          </svg>
        )}

        {shown && (
          <div className="act-tip" style={{ left: `${tip.left}px`, top: `${tip.top}px` }}
            onMouseEnter={() => clearTimeout(leaveTimer.current)} onMouseLeave={leave}>
            <div className="act-tip-hd">
              <strong>{longDay(asDate(shown.date))}</strong>
              {pinned != null && <button type="button" className="act-tip-x" onClick={() => setPinned(null)} aria-label="Close"><X size={14} /></button>}
            </div>
            <div className="act-tip-row"><span>Sightings</span><b>{shown.total}</b></div>
            {onViewPhotos && (
              <button type="button" className="act-tip-btn" disabled={shown.total === 0}
                onClick={() => onViewPhotos(shown.date)}>
                <Images size={11} /> View photos
              </button>
            )}
            {onViewPhotos && shown.total > 0 && <div className="act-tip-note">The gallery lists every photo, so it can show more than the count (a visit is counted once).</div>}
          </div>
        )}
      </div>
      <div className="act-caption">Sightings per day{scrolls ? " - swipe sideways for earlier dates" : ""}</div>
    </div>
  );
}

export default DateActivityChart;
