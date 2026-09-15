import React, { useState, useEffect } from "react";
import { Plus, MapPin, Wheat, Trees, Footprints, Target } from "lucide-react";

function AddMenu({ drawMode, setDrawMode }) {
  const [open, setOpen] = useState(false);
  const ref = React.useRef(null);
  useEffect(() => {
    if (!open) return;
    const onDoc = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);
  const pick = (m) => { setDrawMode(m); setOpen(false); };
  const items = [
    { m: "stand",    label: "Stand",        icon: MapPin,     color: "var(--navy)" },
    { m: "food",     label: "Food zone",    icon: Wheat,      color: "var(--green)" },
    { m: "bedding",  label: "Bedding zone", icon: Trees,      color: "#6B4FA0" },
    { m: "corridor", label: "Deer corridor",icon: Footprints, color: "#A35A1B" },
    { m: "scrape",   label: "Scrape",       icon: Target,     color: "#E87800" },
    { m: "rub",      label: "Rub",          icon: Target,     color: "#8B3A1A" },
  ];
  return (
    <div style={{ position: "relative" }} ref={ref}>
      <button className="btn btn-primary" onClick={() => setOpen((o) => !o)} disabled={!!drawMode}><Plus size={15} /> Add</button>
      {open && (
        <div style={{ position: "absolute", left: "50%", transform: "translateX(-50%)", top: "calc(100% + 4px)", background: "var(--bg)", border: "1px solid var(--bord2)", borderRadius: 10, padding: 6, zIndex: 3000, minWidth: 168, boxShadow: "0 6px 24px rgba(0,0,0,.18)" }}>
          {items.map(({ m, label, icon: Icon, color }) => (
            <button key={m} className="menu-item" onClick={() => pick(m)}><Icon size={15} color={color} /> {label}</button>
          ))}
        </div>
      )}
    </div>
  );
}

export default AddMenu;
