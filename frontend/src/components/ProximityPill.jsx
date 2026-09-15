import React, { useState, useEffect } from "react";

function ProximityPill({ proximity, total }) {
  const [open, setOpen] = useState(false);
  const ref = React.useRef(null);
  useEffect(() => {
    if (!open) return;
    const onDoc = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);
  const rows = [
    ["Corridor", proximity.corridor],
    ["Food", proximity.food],
    ["Bedding", proximity.bedding],
    ["Scrape", proximity.scrape],
    ["Rub", proximity.rub],
  ].filter(([, v]) => v > 0.001);
  return (
    <span ref={ref} style={{ position: "relative", display: "inline-flex" }}
      onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}>
      <span onClick={(e) => { e.stopPropagation(); setOpen((o) => !o); }}
        style={{ fontSize: 11.5, fontWeight: 500, color: "var(--green)", background: "rgba(59,109,17,.12)",
                 padding: "2px 8px", borderRadius: 6, cursor: "pointer" }}>
        proximity +{total}
      </span>
      {open && (
        <div style={{ position: "absolute", top: "calc(100% + 6px)", left: 0, zIndex: 30, minWidth: 150,
          background: "var(--bg)", border: "1px solid var(--bord2)", borderRadius: 8, padding: "8px 10px",
          fontSize: 11.5, lineHeight: 1.6, color: "var(--txt)",
          boxShadow: "0 6px 20px rgba(0,0,0,.18)" }}>
          {rows.map(([label, v]) => (
            <div key={label} style={{ display: "flex", justifyContent: "space-between", gap: 14 }}>
              <span style={{ color: "var(--sub)" }}>{label}</span>
              <strong>+{Math.round(v * 100)}</strong>
            </div>
          ))}
        </div>
      )}
    </span>
  );
}

export default ProximityPill;
