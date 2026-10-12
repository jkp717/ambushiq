import InfoTip from "./InfoTip.jsx";

function SliderRow({ label, min, max, step, value, display, onChange, info }) {
  return (
    <div style={{ marginBottom: 10 }}>
      {/* the gap keeps the help icon's enlarged tap area clear of the slider below */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", fontSize: 13, marginBottom: 10 }}>
        <span style={{ color: "var(--sub)", display: "inline-flex", alignItems: "center", gap: 5 }}>
          {label}{info && <InfoTip text={info} />}
        </span>
        <strong>{display}</strong>
      </div>
      <input type="range" min={min} max={max} step={step} value={value} onChange={(e) => onChange(+e.target.value)} style={{ width: "100%" }} />
    </div>
  );
}

export default SliderRow;
