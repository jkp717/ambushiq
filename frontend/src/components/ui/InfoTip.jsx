import React, { useState, useEffect } from "react";
import { Info } from "lucide-react";

function InfoTip({ text }) {
  const [open, setOpen] = useState(false);
  const ref = React.useRef(null);
  useEffect(() => {
    if (!open) return;
    const onDoc = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);
  return (
    <span ref={ref} style={{ position: "relative", display: "inline-flex" }}>
      <button type="button" className="icon-btn" title="What does this do?" onClick={(e) => { e.stopPropagation(); setOpen(o => !o); }}
        style={{ width: 16, height: 16, padding: 0, minHeight: 0, display: "inline-flex", alignItems: "center", justifyContent: "center" }}>
        <Info size={12} color="var(--sub)" />
      </button>
      {open && (
        <div style={{ position: "absolute", top: "calc(100% + 6px)", left: 0, zIndex: 30, width: 210,
          background: "var(--bg)", border: "1px solid var(--bord2)", borderRadius: 8, padding: "8px 10px",
          fontSize: 11.5, fontWeight: 400, lineHeight: 1.4, color: "var(--sub)",
          boxShadow: "0 6px 20px rgba(0,0,0,.18)" }}>
          {text}
        </div>
      )}
    </span>
  );
}

export default InfoTip;
