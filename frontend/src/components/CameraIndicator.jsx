import React, { useState, useEffect } from "react";
import { Camera, AlertTriangle } from "lucide-react";

function CameraIndicator({ camera }) {
  const [open, setOpen] = useState(false);
  const ref = React.useRef(null);
  useEffect(() => {
    if (!open) return;
    const onDoc = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);
  const unhealthy = camera.status === "unhealthy";
  const positive = camera.boost_pct > 0;
  const color = unhealthy ? "var(--amber)" : positive ? "#1E7FB0" : "var(--red)";
  return (
    <span ref={ref} style={{ position: "relative", display: "inline-flex", verticalAlign: "middle" }}
      onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}>
      <span onClick={(e) => { e.stopPropagation(); setOpen((o) => !o); }}
        style={{ display: "inline-flex", alignItems: "center", gap: 2, cursor: "pointer", color, fontWeight: 600 }}>
        <Camera size={11} />{unhealthy ? <AlertTriangle size={10} /> : `${Math.round(Math.abs(camera.boost_pct))}%`}
      </span>
      {open && (
        <div style={{ position: "absolute", top: "calc(100% + 6px)", left: 0, zIndex: 30, width: 200,
          background: "var(--bg)", border: "1px solid var(--bord2)", borderRadius: 8, padding: "8px 10px",
          fontSize: 11.5, lineHeight: 1.4, color: "var(--sub)",
          boxShadow: "0 6px 20px rgba(0,0,0,.18)" }}>
          {camera.text}
        </div>
      )}
    </span>
  );
}

export default CameraIndicator;
