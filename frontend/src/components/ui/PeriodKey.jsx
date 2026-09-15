function PeriodKey({ color, label }) {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
      <span style={{ width: 12, height: 12, borderRadius: 3, border: `2px solid ${color}`, display: "inline-block" }} />
      {label}
    </span>
  );
}

export default PeriodKey;
