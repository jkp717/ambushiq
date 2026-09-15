import { useState } from "react";
import InfoTip from "./ui/InfoTip.jsx";

function DeerRating({ rating }) {
  const [open, setOpen] = useState(false);
  const r = rating.rating;
  const deer = "🦌".repeat(r) + "·".repeat(5 - r);
  const tone = r >= 4 ? "var(--green)" : r === 3 ? "var(--amber)" : "var(--red)";
  const fac = rating.factors;
  const f = (v) => Math.round(v * 100);
  const bar = (label, v, info) => (
    <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
      <span style={{ fontSize: 11.5, color: "var(--sub)", width: 110, display: "inline-flex", alignItems: "center", gap: 4 }}>
        {label}{info && <InfoTip text={info} />}
      </span>
      <span style={{ flex: 1, height: 6, background: "var(--surf)", borderRadius: 3, overflow: "hidden" }}>
        <span style={{ display: "block", height: "100%", width: `${f(v)}%`, background: tone }} />
      </span>
      <span style={{ fontSize: 11, color: "var(--sub)", width: 28, textAlign: "right" }}>{f(v)}</span>
    </div>
  );
  return (
    <div className="card" style={{ padding: "10px 12px", marginBottom: 10, borderLeft: `3px solid ${tone}` }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, cursor: "pointer" }} onClick={() => setOpen((o) => !o)}>
        <span style={{ fontSize: 18, letterSpacing: 1 }} title={`${r} of 5`}>{deer}</span>
        <span style={{ fontWeight: 600, color: tone }}>{rating.score != null ? (1 + rating.score * 4).toFixed(1) : r}/5 movement</span>
        <span style={{ fontSize: 12, color: "var(--sub)" }}>· {rating.rut?.phase}</span>
        <span style={{ marginLeft: "auto", fontSize: 12, color: "var(--sub)" }}>{open ? "hide ▲" : "why? ▼"}</span>
      </div>
      {open && (
        <div style={{ marginTop: 10, paddingTop: 10, borderTop: "1px solid var(--bord)" }}>
          {bar("Rut intensity", rating.rut?.intensity,
            "How strong seasonal breeding drive is right now — higher means bucks cruise more, including in daylight. 85+ = peak pre-rut seeking, the best daylight movement of the year. 15 = off-season lull.")}
          {bar("Barometric", fac?.pressure,
            "Steady high pressure (~30.0–30.4in) or a fast-moving front pushes deer to move in daylight. 80+ = ideal pressure or a sharp swing. 30 = flat, low pressure that favors night movement.")}
          {bar("Wind", fac?.wind,
            "Moderate wind (5–15mph) helps deer scent danger and move confidently; dead calm or gusty wind suppresses it. 90+ = a ~9mph breeze, the sweet spot. 35 = wind above 25mph.")}
          {bar("Rain (1=dry)", fac?.rain,
            "Heavy rain is one of the strongest movement suppressors. 100 = dry. 55 = moderate rain (2.5–7.5mm). 25 = heavy rain (7.5mm+) — high wind blunts the effect slightly.")}
          {bar("Temp shift", fac?.temp_shift,
            "Deer don't move less in the cold, they move earlier — a colder day than recent baseline shifts activity into daylight; a warm spell shifts it to night. 100 = a sharp cool front (15°F+ below baseline). 25 = a big warm-up. High dew points lower it further.")}
          <div style={{ fontSize: 11, color: "var(--sub)", marginTop: 8, lineHeight: 1.5 }}>
            {rating.inputs?.pressure_inhg != null && <>{rating.inputs.pressure_inhg}″ · </>}
            {rating.inputs?.wind_mph      != null && <>{rating.inputs.wind_mph} mph · </>}
            {rating.inputs?.day_high_f    != null && <>{rating.inputs.day_high_f}°F · </>}
            {rating.inputs?.rain_mm       != null && <>{rating.inputs.rain_mm} mm rain</>}
          </div>
          {(() => {
            const bd = rating.breakdown;
            if (!bd || bd.length < 2) return null;
            const rutEntry = bd[0];
            const weatherFactors = bd.slice(1);
            const top = weatherFactors.reduce((best, f) => {
              const s = Math.abs((f.value || 0) - 0.5) * (typeof f.weight === "number" ? f.weight : 0);
              const b = Math.abs((best.value || 0) - 0.5) * (typeof best.weight === "number" ? best.weight : 0);
              return s > b ? f : best;
            });
            return (
              <div style={{ fontSize: 11, color: "var(--sub)", marginTop: 6, lineHeight: 1.5, borderTop: "1px solid var(--bord)", paddingTop: 6 }}>
                <span style={{ fontWeight: 600, color: "var(--txt)" }}>Why this score: </span>
                {rutEntry.impact}. Primary weather factor: {top.factor.toLowerCase()} — {top.impact}.
              </div>
            );
          })()}
          <div style={{ fontSize: 10.5, color: "var(--sub)", marginTop: 6, fontStyle: "italic" }}>Moon phase intentionally excluded — MSU research found no significant effect on buck activity.</div>
        </div>
      )}
    </div>
  );
}

export default DeerRating;
